"""Bounded contiguous source excerpts with structural and spatial coverage."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from research_assistant.documents.model import PageText

_BOUNDARY = re.compile(r"[。！？]|[.!?](?=\s|$)|\n\s*\n")
MAX_EXCERPTS = 16
# Generic source cues only: these are recall heuristics, not labels of truth.
_ROLES = (
    (
        r"results?|conclusions?|findings?|结果|结论",
        r"\b(?:found|showed|achieved|outperformed|saturated)\b|发现|表明|饱和",
    ),
    (
        r"limitations?|discussion|局限|讨论",
        r"\b(?:however|limited|restricted|limitations?)\b|局限|然而|未能|不适用",
    ),
    (
        r"methods?|data(?:sets?)?|experiments?|方法|数据|实验",
        r"\b(?:method|dataset|metrics?|measured)\b|方法|数据集|指标|观测",
    ),
)
_ROLE_PATTERNS = tuple(
    (
        re.compile(r"^\s*(?:\d+(?:\.\d+)*[.)]?\s+)?(?:" + heading + r")", re.I | re.M),
        re.compile(r"(?:" + heading + "|" + cue + ")", re.I),
    )
    for heading, cue in _ROLES
)


def compress_source(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text).strip()


@dataclass(frozen=True)
class _Candidate:
    order: int
    page: PageText


def _chunks(text: str, limit: int):
    start = 0
    while start < len(text):
        end = min(len(text), start + limit)
        if end < len(text):
            boundaries = [m.end() for m in _BOUNDARY.finditer(text, start, end)]
            natural = next(
                (point for point in reversed(boundaries) if point - start >= limit // 2), None
            )
            if natural is not None:
                end = natural
        piece = text[start:end].strip()
        if piece:
            yield piece
        start = end


def select_page_excerpts(pages: Sequence[PageText], budget: int) -> tuple[PageText, ...]:
    """Return original page identities and contiguous normalized text, within budget."""
    if budget <= 0:
        return ()
    normalized = tuple(
        PageText(page.index, compress_source(page.text), page.label)
        for page in pages
        if page.text.strip()
    )
    if sum(len(page.text) for page in normalized) <= budget:
        return normalized
    # Leave at least a quarter for spatial coverage after the three role picks.
    limit = max(160, min(800, budget // 4))
    candidates: list[_Candidate] = []
    seen: set[str] = set()
    for page in normalized:
        for text in _chunks(page.text, limit):
            if text in seen:
                continue
            seen.add(text)
            candidates.append(_Candidate(len(candidates), PageText(page.index, text, page.label)))
    selected: dict[int, PageText] = {}
    nearest = [len(candidates)] * len(candidates)
    remaining = budget

    def take(candidate: _Candidate) -> None:
        nonlocal remaining
        text = candidate.page.text[:remaining].rstrip()
        if text:
            selected[candidate.order] = PageText(candidate.page.index, text, candidate.page.label)
            remaining -= len(text)
            for index in range(len(candidates)):
                nearest[index] = min(nearest[index], abs(index - candidate.order))

    for role, (heading, cue) in enumerate(_ROLE_PATTERNS):
        available = [c for c in candidates if c.order not in selected and cue.search(c.page.text)]
        if available and remaining:
            # Results/limitations later in a paper should not lose every tie to
            # a structured abstract or front-matter summary using the same heading.
            chosen = max(
                available,
                key=lambda c: (
                    bool(heading.search(c.page.text)),
                    c.page.index > normalized[0].index,
                    c.order if role < 2 else -c.order,
                ),
            )
            take(chosen)
    while remaining and len(selected) < min(len(candidates), MAX_EXCERPTS):
        available = [c for c in candidates if c.order not in selected]
        if not selected:
            chosen = available[0]
        else:
            chosen = max(available, key=lambda c: (nearest[c.order], -c.order))
        take(chosen)
    return tuple(selected[index] for index in sorted(selected))
