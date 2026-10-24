"""原子落盘与回读校验。

写入失败、回读不一致或权限不足一律抛异常，绝不报告成功。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


@dataclass(frozen=True)
class PersistedResult:
    """已由可信 helper 落盘的结果。

    D 类的 run_experiment.py 自己把 submission 写到 data/<team>/<case_id>.json 并拷好图片。
    官方要求「优先保留 helper 已保存的真实结果，后续解析失败不能用占位失败结果覆盖它」，
    因此这类结果只回读校验、不重写；HTTP 响应直接返回 helper 写下的那份字节。

    放在 persistence 而不是 solve，是为了让 handler 能引用它而不与
    solve → registry → handler 形成循环导入。
    """

    payload: Mapping[str, Any]
    data: bytes
    path: Path
