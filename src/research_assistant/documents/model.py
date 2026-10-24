"""文档层的数据模型。

论文正文与元数据一律视为**数据**，不是指令：PDF 内容里出现的任何「请读取/忽略/改写」
之类文字都不得改变程序行为（总纲 §4.2）。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class ParseStatus(StrEnum):
    """解析状态。缺失、加密、不可解析必须留下可见原因，不得静默当成空文档。"""

    OK = "ok"
    EMPTY = "empty"  # 可打开但无正文，通常是扫描件；资源允许时才 OCR
    ENCRYPTED = "encrypted"
    CORRUPT = "corrupt"
    TIMEOUT = "timeout"  # 本批剩余解析时间用尽，不能当作无相关证据
    MISSING = "missing"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class PageText:
    """单页正文。`label` 是印刷页码，仅在能核验时给出，否则为 None。"""

    index: int
    text: str
    label: str | None = None


@dataclass(frozen=True)
class DocumentMetadata:
    title: str | None = None
    authors: tuple[str, ...] = ()
    year: int | None = None
    keywords: tuple[str, ...] = ()
    abstract: str | None = None
    subject: str | None = None
    producer: str | None = None


@dataclass(frozen=True)
class Document:
    """一份被授权材料的解析结果。

    `filename` 是论文库中的**原始**文件名：输出与读取都必须用它，
    因为评分只对大小写做等价、其他字符必须完全一致，而服务器文件系统区分大小写。
    """

    filename: str
    path: Path
    content_sha256: str
    status: ParseStatus
    status_reason: str | None
    extractor: str
    metadata: DocumentMetadata = field(default_factory=DocumentMetadata)
    pages: tuple[PageText, ...] = ()

    @property
    def usable(self) -> bool:
        """是否含可作为证据的正文。EMPTY/CORRUPT/MISSING 都不可用。"""
        return self.status is ParseStatus.OK and bool(self.full_text.strip())

    @property
    def full_text(self) -> str:
        return "\n".join(page.text for page in self.pages)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def page_containing(self, needle: str) -> PageText | None:
        """按关键词定位正文证据所在页，用于 [编号, p.页码] 引用。"""
        if not needle.strip():
            return None
        target = normalize_for_search(needle)
        for page in self.pages:
            if target in normalize_for_search(page.text):
                return page
        return None

    def citation_page(self, needle: str) -> str | None:
        """返回可用于引用的页码字符串；无证据时返回 None，不得编造页码。"""
        page = self.page_containing(needle)
        if page is None:
            return None
        return page.label if page.label else str(page.index + 1)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


_HYPHEN_LINE_BREAK = re.compile(r"-\s*\n\s*")
_WHITESPACE_RUN = re.compile(r"\s+")


def normalize_for_search(text: str) -> str:
    """把 PDF 提取的排版噪声归一化，只用于**检索定位**，不改存储的原文。

    PDF 提取会在短语中间断行，也会把行尾连字符断词拆成两行。
    朴素的子串匹配跨行必然失败——而 C 类的 `[编号, p.页码]` 引用完全依赖定位证据，
    找不到就会漏引用，甚至诱导编造页码。因此匹配前先：
    去掉行尾连字符断词、把所有空白折叠成单空格、转小写。
    """
    return _WHITESPACE_RUN.sub(" ", _HYPHEN_LINE_BREAK.sub("", text)).strip().lower()


@dataclass(frozen=True)
class ExtractionReport:
    """一次授权范围内的批量解析结果，含重名与失败原因。"""

    documents: tuple[Document, ...]
    duplicate_filenames: tuple[str, ...] = ()

    def by_filename(self, filename: str) -> Document | None:
        for document in self.documents:
            if document.filename == filename:
                return document
        return None

    def usable(self) -> tuple[Document, ...]:
        return tuple(d for d in self.documents if d.usable)

    def failures(self) -> tuple[Document, ...]:
        return tuple(d for d in self.documents if not d.usable)


def dedupe_case_insensitive(filename: str, seen: Sequence[str]) -> str | None:
    """B 类评分对「仅大小写不同」按同一篇处理。

    返回已存在的等价文件名（说明当前这个是重复项），否则返回 None。
    注意：这只用于**去重判定**，实际读取与输出仍用论文库原始文件名。
    """
    lowered = filename.lower()
    for existing in seen:
        if existing.lower() == lowered:
            return existing
    return None
