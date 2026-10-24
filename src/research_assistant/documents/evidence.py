"""证据层：把解析出的文档整理成可引用、可核验的证据包。

事实分类标记（总纲 §5.1）必须严格区分：
- `paper_claim`      论文原文声称的内容
- `task_observation` 题目给出的模拟观察
- `proposed_hypothesis` 模型本轮提出的假设
- `measured_result`  本轮真实实测结果

C/E 报告里这四类不得混淆；把未验证的假设写成已完成实验会同时丢内容分与诚信分。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from research_assistant.documents.model import Document, ParseStatus

EVIDENCE_KINDS = (
    "paper_claim",
    "task_observation",
    "proposed_hypothesis",
    "measured_result",
)

DEFAULT_MAX_CHARS_PER_DOCUMENT = 6000
_WHITESPACE = re.compile(r"[ \t]+")


@dataclass(frozen=True)
class EvidenceSnippet:
    """一段带页码定位的原文片段。页码只在能核验时给出。"""

    page_index: int
    page_label: str | None
    text: str

    @property
    def citable_page(self) -> str:
        return self.page_label if self.page_label else str(self.page_index + 1)


@dataclass(frozen=True)
class EvidenceEntry:
    filename: str
    title: str | None
    year: int | None
    authors: tuple[str, ...]
    keywords: tuple[str, ...]
    abstract: str | None
    snippets: tuple[EvidenceSnippet, ...]
    status_reason: str | None = None

    @property
    def short_label(self) -> str:
        return self.title or self.filename


@dataclass(frozen=True)
class UnusableMaterial:
    filename: str
    status: str
    reason: str | None


@dataclass(frozen=True)
class EvidencePack:
    entries: tuple[EvidenceEntry, ...]
    unusable: tuple[UnusableMaterial, ...]

    @property
    def filenames(self) -> tuple[str, ...]:
        return tuple(entry.filename for entry in self.entries)

    def entry_for(self, filename: str) -> EvidenceEntry | None:
        for entry in self.entries:
            if entry.filename == filename:
                return entry
        return None


def _compress(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def build_evidence_pack(
    documents: Sequence[Document],
    *,
    max_chars_per_document: int = DEFAULT_MAX_CHARS_PER_DOCUMENT,
) -> EvidencePack:
    """把文档整理成证据包。不可用材料单独记录，不混进可引用集合。"""
    entries: list[EvidenceEntry] = []
    unusable: list[UnusableMaterial] = []

    for document in documents:
        if not document.usable:
            unusable.append(
                UnusableMaterial(
                    filename=document.filename,
                    status=document.status.value,
                    reason=document.status_reason,
                )
            )
            continue

        budget = max_chars_per_document
        if document.metadata.abstract:
            budget -= len(document.metadata.abstract)
        snippets: list[EvidenceSnippet] = []
        for page in document.pages:
            if budget <= 0:
                break
            text = _compress(page.text)
            if not text:
                continue
            clipped = text[:budget]
            snippets.append(
                EvidenceSnippet(page_index=page.index, page_label=page.label, text=clipped)
            )
            budget -= len(clipped)

        entries.append(
            EvidenceEntry(
                filename=document.filename,
                title=document.metadata.title,
                year=document.metadata.year,
                authors=document.metadata.authors,
                keywords=document.metadata.keywords,
                abstract=document.metadata.abstract,
                snippets=tuple(snippets),
            )
        )

    return EvidencePack(entries=tuple(entries), unusable=tuple(unusable))


def render_evidence_pack(pack: EvidencePack) -> str:
    """渲染成给模型看的材料清单：编号、元数据、带页码的原文片段。"""
    blocks: list[str] = []
    for number, entry in enumerate(pack.entries, start=1):
        lines = [f"## [{number}] {entry.filename}"]
        if entry.title:
            lines.append(f"- 标题：{entry.title}")
        if entry.authors:
            lines.append(f"- 作者：{', '.join(entry.authors)}")
        if entry.year is not None:
            lines.append(f"- 年份：{entry.year}")
        if entry.keywords:
            lines.append(f"- 关键词：{', '.join(entry.keywords)}")
        if entry.abstract:
            lines.append(f"- 摘要：{entry.abstract}")
        for snippet in entry.snippets:
            lines.append(f"- 原文（第 {snippet.citable_page} 页）：{snippet.text}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def render_unusable(pack: EvidencePack) -> str:
    if not pack.unusable:
        return ""
    lines = ["# 无法解析因而不可作为证据的材料（不得引用，不得编造其内容）"]
    for item in pack.unusable:
        lines.append(f"- {item.filename}（{item.status}：{item.reason or '无进一步信息'}）")
    return "\n".join(lines)


def page_claim_map(
    pack: EvidencePack, quoted_phrases: Sequence[tuple[str, str]]
) -> dict[str, str | None]:
    """把 (filename, 引文片段) 映射到可核验的页码；找不到证据则映射为 None。

    页码可用但必须有证据——找不到就不得写页码，宁可只写 [编号]。
    """
    result: dict[str, str | None] = {}
    for filename, phrase in quoted_phrases:
        entry = pack.entry_for(filename)
        if entry is None or not phrase.strip():
            result[phrase] = None
            continue
        needle = _compress(phrase).lower()
        found: str | None = None
        for snippet in entry.snippets:
            if needle and needle in _compress(snippet.text).lower():
                found = snippet.citable_page
                break
        result[phrase] = found
    return result


def status_of(documents: Sequence[Document]) -> dict[str, str]:
    return {d.filename: d.status.value for d in documents}


def any_unusable_reason(documents: Sequence[Document]) -> str | None:
    """把不可用材料的原因汇成一句可见说明，用于「材料不足」时如实记录。"""
    reasons = [
        f"{d.filename}（{d.status.value}：{d.status_reason or '无进一步信息'}）"
        for d in documents
        if d.status is not ParseStatus.OK
    ]
    return "；".join(reasons) if reasons else None
