"""PDF 与文本提取。

pypdf 是纯 Python、零强制依赖，基础镜像不含任何 PDF 库，因此它是唯一的运行时新增依赖。
扫描件在资源允许时才 OCR；P2 不实现 OCR，只把状态记为 EMPTY 并给出可见原因。

安全：PDF 正文与元数据一律当作数据处理。提取到的文字不会作为指令解释，
也不会用它去决定读取哪些其他文件。
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable, Sequence
from pathlib import Path

import pypdf

from research_assistant.documents.model import (
    Document,
    DocumentMetadata,
    ExtractionReport,
    PageText,
    ParseStatus,
    dedupe_case_insensitive,
    sha256_of,
)
from research_assistant.errors import PathAuthorizationError
from research_assistant.runtime.logging import BoundLogger
from research_assistant.runtime.paths import WorkspacePaths

EXTRACTOR_NAME = f"pypdf-{pypdf.__version__}"

TEXT_SUFFIXES = frozenset({".txt", ".md", ".markdown", ".json", ".csv"})
PDF_SUFFIX = ".pdf"

_YEAR_IN_FILENAME = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
# 分号、& 与中文顿号/「和」总是作者分隔符
_STRONG_AUTHOR_SEPARATORS = re.compile(r"\s*(?:;|&|、|和)\s*")
_ABSTRACT_HEAD = re.compile(r"^\s*(?:abstract|摘\s*要)\s*[:：]?\s*$", re.IGNORECASE)
_ABSTRACT_INLINE = re.compile(r"^\s*(?:abstract|摘\s*要)\s*[:：]\s*(.+)$", re.IGNORECASE)
_ABSTRACT_END = re.compile(
    r"^\s*(?:1\.?\s*introduction|keywords?|index terms|categories and subject descriptors|ccs concepts|摘\s*要|关键词)\b",
    re.IGNORECASE,
)
_MAX_ABSTRACT_CHARS = 4000


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _split_keywords(value: object) -> tuple[str, ...]:
    """关键词按逗号/分号切分。"""
    text = _clean(value)
    if text is None:
        return ()
    parts = re.split(r"[,;、]", text)
    return tuple(part.strip() for part in parts if part.strip())


def _split_authors(value: object) -> tuple[str, ...]:
    """切分作者，但不能把「Zhang, Wei」这种「姓, 名」拆成两个人。

    规则：分号/&/顿号/「和」总是分隔符；只剩逗号时按形状判断——
    三段及以上视为并列作者（"Wei Zhang, Na Li, Bo Chen"）；
    恰好两段且每段都是多词名（各含空格）也视为并列；
    否则保留整体，视为单个「姓, 名」形式的作者。
    """
    text = _clean(value)
    if text is None:
        return ()
    strong = [part.strip() for part in _STRONG_AUTHOR_SEPARATORS.split(text) if part.strip()]
    if len(strong) > 1:
        return tuple(strong)
    single = strong[0] if strong else text
    if "," not in single:
        return (single,)
    comma_parts = [part.strip() for part in single.split(",") if part.strip()]
    if len(comma_parts) >= 3:
        return tuple(comma_parts)
    if len(comma_parts) == 2 and all(len(p.split()) >= 2 for p in comma_parts):
        return tuple(comma_parts)
    return (single,)


def _year_from_metadata(raw: object) -> int | None:
    """PDF 日期形如 D:20240315120000+08'00'。"""
    text = _clean(raw)
    if text is None:
        return None
    match = re.match(r"D?:?((?:19|20)\d{2})", text)
    return int(match.group(1)) if match else None


def _year_from_filename(filename: str) -> int | None:
    matches = _YEAR_IN_FILENAME.findall(filename)
    return int(matches[0]) if matches else None


def _first_line_title(text: str) -> str | None:
    for line in text.splitlines():
        candidate = line.strip()
        if len(candidate) >= 4:
            return candidate[:300]
    return None


def _extract_abstract(pages: Sequence[PageText]) -> str | None:
    """从正文里保守地截取摘要；找不到就返回 None，不猜测、不编造。"""
    collected: list[str] = []
    started = False
    for page in pages[:3]:
        for line in page.text.splitlines():
            stripped = line.strip()
            if not started:
                inline = _ABSTRACT_INLINE.match(stripped)
                if inline:
                    started = True
                    collected.append(inline.group(1).strip())
                    continue
                if _ABSTRACT_HEAD.match(stripped):
                    started = True
                continue
            if not stripped:
                if collected:
                    # 摘要通常是一段连续文字，遇到空行即结束
                    break
                continue
            if _ABSTRACT_END.match(stripped):
                return _finish_abstract(collected)
            collected.append(stripped)
        if collected:
            break
    return _finish_abstract(collected)


def _finish_abstract(collected: Sequence[str]) -> str | None:
    if not collected:
        return None
    text = " ".join(collected).strip()
    if len(text) < 40:
        return None
    return text[:_MAX_ABSTRACT_CHARS]


def _page_labels(reader: pypdf.PdfReader, count: int) -> list[str | None]:
    """印刷页码只在能核验时给出。取不到就整篇留 None，不按索引假造。"""
    try:
        raw = reader.page_labels
    except Exception:
        return [None] * count
    if not isinstance(raw, Sequence) or len(raw) < count:
        return [None] * count
    labels: list[str | None] = []
    for index in range(count):
        value = _clean(raw[index])
        # 与 0 基索引完全相同的标签没有信息量，视为未提供印刷页码
        labels.append(None if value == str(index) else value)
    return labels


def _extract_pdf(path: Path, filename: str, digest: str) -> Document:
    try:
        reader = pypdf.PdfReader(str(path))
    except Exception as exc:
        return Document(
            filename=filename,
            path=path,
            content_sha256=digest,
            status=ParseStatus.CORRUPT,
            status_reason=f"{exc.__class__.__name__}: {exc}"[:300],
            extractor=EXTRACTOR_NAME,
        )

    if reader.is_encrypted:
        # 不尝试任何口令：未授权解密不属于本题能力，如实报告
        return Document(
            filename=filename,
            path=path,
            content_sha256=digest,
            status=ParseStatus.ENCRYPTED,
            status_reason="PDF is encrypted; no credential available",
            extractor=EXTRACTOR_NAME,
        )

    pages: list[PageText] = []
    errors: list[str] = []
    try:
        count = len(reader.pages)
    except Exception as exc:
        count = 0
        errors.append(f"page count unavailable: {exc.__class__.__name__}")
    labels = _page_labels(reader, count)
    for index in range(count):
        try:
            text = reader.pages[index].extract_text() or ""
        except Exception as exc:
            text = ""
            errors.append(f"page {index + 1}: {exc.__class__.__name__}")
        pages.append(PageText(index=index, text=text, label=labels[index]))

    raw_meta = reader.metadata
    meta_map = dict(raw_meta) if raw_meta else {}
    body = "\n".join(page.text for page in pages)
    metadata = DocumentMetadata(
        title=_clean(meta_map.get("/Title")) or _first_line_title(body),
        authors=_split_authors(meta_map.get("/Author")),
        year=_year_from_metadata(meta_map.get("/CreationDate")) or _year_from_filename(filename),
        keywords=_split_keywords(meta_map.get("/Keywords")),
        abstract=_extract_abstract(pages),
        subject=_clean(meta_map.get("/Subject")),
        producer=_clean(meta_map.get("/Producer")),
    )

    if not body.strip():
        reason = "no extractable text; likely a scanned image (OCR not implemented in P2)"
        if errors:
            reason += "; " + "; ".join(errors[:3])
        return Document(
            filename=filename,
            path=path,
            content_sha256=digest,
            status=ParseStatus.EMPTY,
            status_reason=reason[:500],
            extractor=EXTRACTOR_NAME,
            metadata=metadata,
            pages=tuple(pages),
        )

    status_reason = "; ".join(errors[:5])[:500] if errors else None
    return Document(
        filename=filename,
        path=path,
        content_sha256=digest,
        status=ParseStatus.OK,
        status_reason=status_reason,
        extractor=EXTRACTOR_NAME,
        metadata=metadata,
        pages=tuple(pages),
    )


def _extract_text(path: Path, filename: str, digest: str) -> Document:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return Document(
            filename=filename,
            path=path,
            content_sha256=digest,
            status=ParseStatus.CORRUPT,
            status_reason=f"{exc.__class__.__name__}: {exc.strerror}"[:300],
            extractor="utf-8-text",
        )
    page = PageText(index=0, text=text, label=None)
    metadata = DocumentMetadata(
        title=_first_line_title(text),
        year=_year_from_filename(filename),
        abstract=_extract_abstract([page]),
    )
    status = ParseStatus.OK if text.strip() else ParseStatus.EMPTY
    return Document(
        filename=filename,
        path=path,
        content_sha256=digest,
        status=status,
        status_reason=None if text.strip() else "file is empty",
        extractor="utf-8-text",
        metadata=metadata,
        pages=(page,),
    )


def extract_one(path: Path, *, filename: str) -> Document:
    """解析单个已授权文件。路径必须已由 WorkspacePaths 校验过。"""
    digest = sha256_of(path)
    suffix = path.suffix.lower()
    if suffix == PDF_SUFFIX:
        return _extract_pdf(path, filename, digest)
    if suffix in TEXT_SUFFIXES:
        return _extract_text(path, filename, digest)
    return Document(
        filename=filename,
        path=path,
        content_sha256=digest,
        status=ParseStatus.UNSUPPORTED,
        status_reason=f"no extractor for suffix {suffix or '(none)'}",
        extractor="none",
    )


def extract_documents(
    materials: Iterable[str],
    workspace: WorkspacePaths,
    log: BoundLogger | None = None,
    *,
    time_budget_seconds: float | None = None,
) -> ExtractionReport:
    """按 materials 批量解析。

    重名（仅大小写不同视为同一篇）记录到 duplicate_filenames，不重复解析；
    路径越界由 WorkspacePaths 抛 PathAuthorizationError；文件不存在记为 MISSING 并保留可见原因。
    time_budget_seconds 是整批解析的上限：材料库很大时保证把时间留给模型与落盘。
    """
    documents: list[Document] = []
    seen: list[str] = []
    duplicates: list[str] = []
    ordered = list(materials)
    started = time.monotonic()

    for material in ordered:
        if time_budget_seconds is not None and time.monotonic() - started >= time_budget_seconds:
            if log is not None:
                log.warning(
                    "documents.time_budget_exhausted",
                    parsed=len(documents),
                    total=len(ordered),
                    budget_seconds=time_budget_seconds,
                )
            break
        existing = dedupe_case_insensitive(material, seen)
        if existing is not None:
            duplicates.append(material)
            if log is not None:
                log.warning(
                    "documents.duplicate",
                    filename=material,
                    equivalent_to=existing,
                    reason="case-insensitive duplicate; scoring treats them as one paper",
                )
            continue
        seen.append(material)

        try:
            path = workspace.paper(material)
        except PathAuthorizationError:
            raise
        except Exception as exc:
            documents.append(
                Document(
                    filename=material,
                    path=Path(material),
                    content_sha256="",
                    status=ParseStatus.MISSING,
                    status_reason=f"{exc.__class__.__name__}: {exc}"[:300],
                    extractor="none",
                )
            )
            continue

        if not path.is_file():
            documents.append(
                Document(
                    filename=material,
                    path=path,
                    content_sha256="",
                    status=ParseStatus.MISSING,
                    status_reason="authorized material is not present on disk",
                    extractor="none",
                )
            )
            if log is not None:
                log.warning("documents.missing", filename=material)
            continue

        document = extract_one(path, filename=material)
        documents.append(document)
        if log is not None:
            log.info(
                "documents.extracted",
                filename=material,
                status=document.status.value,
                pages=document.page_count,
                characters=len(document.full_text),
                content_sha256=document.content_sha256[:16],
                extractor=document.extractor,
                title=document.metadata.title,
                year=document.metadata.year,
            )

    return ExtractionReport(documents=tuple(documents), duplicate_filenames=tuple(duplicates))
