"""Conservative bibliography completion and explainable report-repair decisions."""

from __future__ import annotations

import re
from dataclasses import dataclass

from research_assistant.documents.evidence import EvidencePack
from research_assistant.validation.citations import (
    REFERENCE_LINE_NUMBER,
    CitationAnalysis,
    analyze_citations,
)
from research_assistant.validation.grounding import numeric_issue_count

_LEGALITY_KINDS = frozenset(
    {
        "no_body_citations",
        "dangling_number",
        "zombie_number",
        "unknown_material",
        "missing_filename",
        "duplicate_number",
        "ambiguous_numeric_brackets",
    }
)


@dataclass(frozen=True)
class ReferenceRepair:
    markdown: str
    added_numbers: tuple[int, ...] = ()


def _unique_anchor(raw: str, filename: str) -> bool:
    match = REFERENCE_LINE_NUMBER.match(raw)
    if match is None:
        return False
    text = match.group(2).strip()
    if len(re.findall(r"\.pdf\b", text, re.IGNORECASE)) != 1:
        return False
    if text.rstrip(" .。") == filename:
        return True
    # Free author/title text is not a provable filename boundary. Explicit paired
    # delimiters or a PDF/filename label distinguish the locator from that metadata.
    if any(f"{quote}{filename}{quote}" in text for quote in ("`", '"', "'")):
        return True
    return (
        re.fullmatch(
            r".*?(?:PDF|文件名)\s*[:：]\s*" + re.escape(filename) + r"[.。]?", text, re.IGNORECASE
        )
        is not None
    )


def complete_missing_references(markdown: str, pack: EvidencePack) -> ReferenceRepair:
    if re.search(r"(?m)^\s*(?:`{3,}|~{3,})", markdown):
        return ReferenceRepair(markdown)
    canonical = {index: entry.filename for index, entry in enumerate(pack.entries, 1)}
    analysis = analyze_citations(markdown, frozenset(pack.filenames))
    unchanged = ReferenceRepair(markdown)
    # Existing anchors must all prove the numbering used in the evidence prompt.
    if (
        not analysis.references
        or analysis.duplicate_reference_numbers
        or not analysis.cited_numbers <= canonical.keys()
        or any(
            canonical.get(ref.number) != ref.filename
            or not _unique_anchor(ref.raw, canonical.get(ref.number, ""))
            for ref in analysis.references
        )
    ):
        return unchanged
    missing = tuple(sorted(analysis.dangling_numbers))
    if not missing or any("\n" in canonical[n] or "\r" in canonical[n] for n in missing):
        return unchanged
    suffix = "" if markdown.endswith("\n") else "\n"
    suffix += "".join(f"[{n}] {canonical[n]}\n" for n in missing)
    return ReferenceRepair(markdown + suffix, missing)


def evidence_priority(analysis: CitationAnalysis) -> tuple[int, int, int]:
    hard = sum(issue.kind in _LEGALITY_KINDS for issue in analysis.issues)
    soft = sum(
        issue.kind not in _LEGALITY_KINDS and issue.kind != "unverified_numeric_attribution"
        for issue in analysis.issues
    )
    return hard, -len(analysis.cited_filenames), soft


@dataclass(frozen=True)
class RepairDecision:
    accepted: bool
    reason: str
    before_priority: tuple[int, int, int]
    after_priority: tuple[int, int, int]


def decide_repair(
    before: CitationAnalysis,
    after: CitationAnalysis,
    *,
    content_before: int = 0,
    content_after: int = 0,
    min_sources: int = 0,
    semantic_before: int = 0,
    semantic_after: int = 0,
) -> RepairDecision:
    old = evidence_priority(before)
    new = evidence_priority(after)
    reason = "improved"
    if numeric_issue_count(after) > numeric_issue_count(before):
        reason = "numeric_grounding_regressed"
    elif semantic_after > semantic_before:
        reason = "source_grounding_regressed"
    elif content_after > content_before:
        reason = "content_regressed"
    elif max(0, min_sources - len(after.cited_filenames)) > max(
        0, min_sources - len(before.cited_filenames)
    ):
        reason = "required_coverage_regressed"
    elif new[0] > old[0] or (new[0] == old[0] and (new[1] > old[1] or new[2] > old[2])):
        reason = "citation_quality_regressed"
    elif (
        new == old
        and content_after == content_before
        and numeric_issue_count(after) == numeric_issue_count(before)
        and semantic_after == semantic_before
    ):
        reason = "not_better"
    return RepairDecision(reason == "improved", reason, old, new)
