"""离线词项检索：BM25 召回。

先离线召回、再由赛事 Gateway 基于原文做语义重排与理由生成（总纲 §5.1）。
不假设可以调用外部 embedding 服务。分词沿用 lowercase + [a-z0-9]+，与 D 类算法约定一致。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from research_assistant.documents.model import Document

TOKEN = re.compile(r"[a-z0-9]+")
DEFAULT_K1 = 1.2
DEFAULT_B = 0.75
# 标题与摘要的命中比正文更有判别力
TITLE_WEIGHT = 3.0
ABSTRACT_WEIGHT = 2.0


def tokenize(text: str) -> list[str]:
    return TOKEN.findall(text.lower())


@dataclass(frozen=True)
class RetrievalCandidate:
    document: Document
    score: float
    matched_terms: tuple[str, ...]
    matched_in_title: bool
    matched_in_abstract: bool

    @property
    def filename(self) -> str:
        return self.document.filename


class LexicalIndex:
    """一个授权范围内的倒排索引。每题一实例，无模块级可变状态。"""

    def __init__(
        self,
        documents: Sequence[Document],
        *,
        k1: float = DEFAULT_K1,
        b: float = DEFAULT_B,
    ) -> None:
        self._k1 = k1
        self._b = b
        self._docs = tuple(d for d in documents if d.usable)
        self._titles = [_weight_tokens(d, "title") for d in self._docs]
        self._abstracts = [_weight_tokens(d, "abstract") for d in self._docs]
        self._bodies = [Counter(tokenize(d.full_text)) for d in self._docs]
        lengths = [sum(c.values()) for c in self._bodies]
        self._avg_length = (sum(lengths) / len(lengths)) if lengths else 0.0
        self._n = len(self._docs)
        self._df: dict[str, int] = {}
        for counter in self._bodies:
            for term in counter:
                self._df[term] = self._df.get(term, 0) + 1

    @property
    def document_count(self) -> int:
        return self._n

    def _idf(self, term: str) -> float:
        df = self._df.get(term, 0)
        return math.log(1.0 + (self._n - df + 0.5) / (df + 0.5))

    def search(self, query: str, *, limit: int = 20) -> tuple[RetrievalCandidate, ...]:
        terms = list(dict.fromkeys(tokenize(query)))
        if not terms or self._n == 0:
            return ()
        scored: list[RetrievalCandidate] = []
        for index, document in enumerate(self._docs):
            body = self._bodies[index]
            length = sum(body.values())
            total = 0.0
            matched: list[str] = []
            for term in terms:
                freq = body.get(term, 0)
                in_title = term in self._titles[index]
                in_abstract = term in self._abstracts[index]
                if freq == 0 and not in_title and not in_abstract:
                    continue
                matched.append(term)
                idf = self._idf(term)
                if freq and self._avg_length > 0:
                    norm = self._k1 * (1.0 - self._b + self._b * length / self._avg_length)
                    total += idf * freq * (self._k1 + 1.0) / (freq + norm)
                if in_title:
                    total += idf * TITLE_WEIGHT
                if in_abstract:
                    total += idf * ABSTRACT_WEIGHT
            if not matched:
                continue
            scored.append(
                RetrievalCandidate(
                    document=document,
                    score=total,
                    matched_terms=tuple(matched),
                    matched_in_title=any(t in self._titles[index] for t in matched),
                    matched_in_abstract=any(t in self._abstracts[index] for t in matched),
                )
            )
        # 稳定排序：分数降序，同分按文件名字典序，保证可复现
        scored.sort(key=lambda c: (-c.score, c.filename))
        return tuple(scored[: max(0, limit)])


def _weight_tokens(document: Document, part: str) -> set[str]:
    if part == "title":
        return set(tokenize(document.metadata.title or ""))
    if part == "abstract":
        parts = [document.metadata.abstract or "", document.metadata.subject or ""]
        parts.extend(document.metadata.keywords)
        return set(tokenize(" ".join(parts)))
    raise ValueError(f"unknown part: {part}")


def summarize_for_prompt(
    candidate: RetrievalCandidate, *, snippet_chars: int = 700
) -> Mapping[str, object]:
    """给 Gateway 重排用的紧凑材料摘要。

    只放正文片段，不放全文：既控 Token，也让模型必须基于原文片段判断。
    """
    document = candidate.document
    body = document.full_text
    snippet = _best_snippet(body, candidate.matched_terms, snippet_chars)
    return {
        "filename": document.filename,
        "title": document.metadata.title,
        "year": document.metadata.year,
        "keywords": list(document.metadata.keywords),
        "abstract": document.metadata.abstract,
        "matched_terms": list(candidate.matched_terms),
        "snippet": snippet,
    }


def _best_snippet(body: str, terms: Iterable[str], limit: int) -> str:
    """围绕命中词截取正文片段；无命中时取开头。"""
    lowered = body.lower()
    best_pos = -1
    for term in terms:
        pos = lowered.find(term)
        if pos >= 0 and (best_pos < 0 or pos < best_pos):
            best_pos = pos
    if best_pos < 0:
        return body[:limit].strip()
    start = max(0, best_pos - limit // 4)
    return body[start : start + limit].strip()
