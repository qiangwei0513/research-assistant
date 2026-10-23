"""原子落盘与回读校验。

写入失败、回读不一致或权限不足一律抛异常，绝不报告成功。
"""

from __future__ import annotations

import os
from pathlib import Path

from research_assistant.errors import PersistenceError


def _write_atomic(path: Path, data: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        raise PersistenceError(
            f"failed to write {path.name}: {exc.strerror or exc.__class__.__name__}"
        ) from exc


def _readback(path: Path, expected: bytes) -> None:
    try:
        actual = path.read_bytes()
    except OSError as exc:
        raise PersistenceError(
            f"readback failed for {path.name}: {exc.strerror or exc.__class__.__name__}"
        ) from exc
    if actual != expected:
        raise PersistenceError(
            f"readback mismatch for {path.name}: wrote {len(expected)} bytes, "
            f"read {len(actual)} bytes"
        )


def write_bytes_verified(path: Path, data: bytes) -> bytes:
    """临时文件 + os.replace 原子替换；回读断言逐字节一致；返回落盘字节。"""
    if not isinstance(data, bytes):
        raise PersistenceError(f"{path.name} requires bytes, got {type(data).__name__}")
    _write_atomic(path, data)
    _readback(path, data)
    return data


def write_text_verified(path: Path, text: str) -> bytes:
    """UTF-8 原样落盘后回读，断言与入参逐字节一致。用于 C/E 的 markdown。"""
    if not isinstance(text, str):
        raise PersistenceError(f"{path.name} requires str, got {type(text).__name__}")
    return write_bytes_verified(path, text.encode("utf-8"))
