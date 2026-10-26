"""自建 PDF 测试材料生成器。

这些不是官方论文，不含任何正式答案，只用于验证解析、检索与 handler 流程。
PDF 标准字体（Helvetica/WinAnsiEncoding）不含中文字形，因此**正文**只用 ASCII；
文件名仍可用中文、空格与 '+'，那属于文件系统层面，与字体无关。
"""

from __future__ import annotations

from pathlib import Path


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _content_stream(lines: list[str]) -> bytes:
    parts = ["BT /F1 11 Tf 54 730 Td 14 TL"]
    for line in lines:
        parts.append(f"({_escape(line)}) Tj T*")
    parts.append("ET")
    return ("\n".join(parts) + "\n").encode("latin-1", "replace")


def build_pdf(
    *,
    pages: list[list[str]],
    title: str | None = None,
    author: str | None = None,
    keywords: str | None = None,
    subject: str | None = None,
    creation_date: str | None = None,
) -> bytes:
    """构造一个带正确 xref 偏移的最小合法 PDF。"""
    objects: list[bytes] = []
    page_ids = [3 + i * 2 for i in range(len(pages))]
    font_id = 3 + len(pages) * 2
    info_id = font_id + 1

    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode())
    for index, lines in enumerate(pages):
        content_id = 3 + index * 2 + 1
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Contents {content_id} 0 R "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>"
            ).encode()
        )
        stream = _content_stream(lines)
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"endstream"
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    info_entries: list[str] = []
    if title is not None:
        info_entries.append(f"/Title ({_escape(title)})")
    if author is not None:
        info_entries.append(f"/Author ({_escape(author)})")
    if keywords is not None:
        info_entries.append(f"/Keywords ({_escape(keywords)})")
    if subject is not None:
        info_entries.append(f"/Subject ({_escape(subject)})")
    if creation_date is not None:
        info_entries.append(f"/CreationDate ({_escape(creation_date)})")
    objects.append(("<< " + " ".join(info_entries) + " >>").encode())

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info {info_id} 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


def write_paper(
    directory: Path,
    filename: str,
    *,
    body_paragraphs: list[str],
    abstract: str | None = None,
    title: str | None = None,
    author: str | None = None,
    keywords: str | None = None,
    creation_date: str | None = None,
    pages: int = 1,
) -> Path:
    """写一篇「论文」：首页含标题与摘要，其余页放正文段落。"""
    if pages < 1:
        raise ValueError("pages must be positive")
    directory.mkdir(parents=True, exist_ok=True)
    first_page = [title or filename.rsplit(".", 1)[0]]
    if abstract is not None:
        first_page.append("Abstract")
        first_page.extend(_wrap(abstract))
    first_page.append("1. Introduction")
    first_page.extend(_wrap(body_paragraphs[0] if body_paragraphs else ""))
    if pages == 1:
        for paragraph in body_paragraphs[1:]:
            first_page.extend(_wrap(paragraph))
    all_pages = [first_page]
    for index in range(1, pages):
        paragraphs = (
            body_paragraphs[index:] if index == pages - 1 else body_paragraphs[index : index + 1]
        )
        lines = [line for paragraph in paragraphs for line in _wrap(paragraph)]
        all_pages.append([f"Page {index + 1}", *lines])
    data = build_pdf(
        pages=all_pages,
        title=title,
        author=author,
        keywords=keywords,
        creation_date=creation_date,
    )
    path = directory / filename
    path.write_bytes(data)
    return path


def write_scanned_pdf(directory: Path, filename: str) -> Path:
    """一份「扫描件」：页面存在但没有任何文本算子，提取结果为空。"""
    directory.mkdir(parents=True, exist_ok=True)
    data = build_pdf(pages=[[]])
    path = directory / filename
    path.write_bytes(data)
    return path


def write_corrupt_pdf(directory: Path, filename: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_bytes(b"%PDF-1.4\nthis is not a valid pdf body at all\n")
    return path


def write_encrypted_marker_pdf(directory: Path, filename: str) -> Path:
    """带 /Encrypt（Standard handler）条目的 PDF：pypdf 会报 is_encrypted。

    O/U 需要 32 字节（64 个十六进制字符）才是合法的 Standard 加密字典。
    没有口令，因此本 Agent 无法也不应解密——只验证能如实报告状态。
    """
    directory.mkdir(parents=True, exist_ok=True)
    body = build_pdf(pages=[["Secret content"]])
    padding = "00" * 32
    encrypt = (
        f"/Encrypt << /Filter /Standard /V 1 /R 2 /O <{padding}> /U <{padding}> /P -4 >>"
    ).encode()
    patched = body.replace(b"/Root 1 0 R", b"/Root 1 0 R " + encrypt)
    path = directory / filename
    path.write_bytes(patched)
    return path


def _wrap(text: str, width: int = 88) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines or [""]
