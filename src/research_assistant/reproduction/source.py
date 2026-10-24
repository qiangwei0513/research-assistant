"""D 类生成源码的提取与保存，与官方 model_source.py 的语义逐字对齐。

官方规则（总纲 §6.2、[D1][D4]）：
- 每道 D 题必须在**本轮本题会话内**用赛事 Gateway 生成自包含源码
- `stream=False`、完整回复 `finish_reason=stop`、UTF-8、1～65536 字节
- 顶层必须有 `run_experiment(request)`，Python 3.9 标准库兼容
- 只允许 CRLF/LF 与末尾换行归一，不补 import、不加注释、不格式化、不应用 diff、不拼多个回复
- 截断或错误时让同一会话模型重新返回完整版本

`extract_source` 接收**完整的 chat/completions 响应对象**，不是单独的 message.content。
它只做 AST 层面的格式检查：可被 Python 3.9 解析、存在入口函数名。
提取成功、语法通过、平台来源准入、正式算法得分是四个不同的事实，不得混为一谈。
"""

from __future__ import annotations

import ast
import hashlib
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

MAX_SOURCE_BYTES = 65536
ENTRY_FUNCTION = "run_experiment"
PYTHON_FEATURE_VERSION = (3, 9)

_FENCED_BLOCK = re.compile(
    r"^```(?:python|py)?[ \t]*\r?\n(.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL
)


class SourceExtractionError(ValueError):
    """生成源码不满足来源要求。消息面向诊断，不含源码正文。"""


def normalize_source(source: Any) -> str:
    """与官方 `_normalize` 完全一致：字节数、换行归一、3.9 可解析、入口函数存在。"""
    if not isinstance(source, str) or not 0 < len(source.encode("utf-8")) <= MAX_SOURCE_BYTES:
        raise SourceExtractionError("Source must be 1..65536 UTF-8 bytes")
    source = source.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n"
    try:
        tree = ast.parse(source, feature_version=PYTHON_FEATURE_VERSION)
    except SyntaxError as exc:
        raise SourceExtractionError(f"source is not valid Python 3.9: {exc.msg}") from exc
    if not any(
        isinstance(node, ast.FunctionDef) and node.name == ENTRY_FUNCTION for node in tree.body
    ):
        raise SourceExtractionError(f"Missing {ENTRY_FUNCTION}(request)")
    return source


def extract_source(response: Any) -> str:
    """从完整响应对象中提取**唯一**一份完整实现。

    候选包括整段 content 与其中每个 ```python 围栏块；归一化后必须恰好得到一份不同的源码。
    多于或少于一份都视为失败——此时应让同一会话模型重新返回完整版本，
    而不是挑一份、拼多份或本地补码。
    """
    if not isinstance(response, Mapping):
        raise SourceExtractionError("Expected a complete chat/completions JSON response")
    found: set[str] = set()
    for choice in response.get("choices", []) or []:
        if not isinstance(choice, Mapping) or choice.get("finish_reason") != "stop":
            continue
        message = choice.get("message", {})
        if not isinstance(message, Mapping):
            continue
        if message.get("role") != "assistant" or not isinstance(message.get("content"), str):
            continue
        content = message["content"]
        candidates = [content]
        candidates.extend(_FENCED_BLOCK.findall(content))
        for candidate in candidates:
            try:
                found.add(normalize_source(candidate))
            except (SourceExtractionError, SyntaxError, ValueError, RecursionError):
                continue
    if len(found) != 1:
        raise SourceExtractionError(
            "Expected exactly one complete Python implementation with finish_reason=stop; "
            "request a full new response"
        )
    return found.pop()


def source_sha256(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def save_source(response: Mapping[str, Any], destination: Path) -> tuple[str, str]:
    """提取并原样落盘，返回 (源码, sha256)。

    落盘的就是归一化后的字节，不做任何格式化；Ruff 等工具必须排除该目录。
    """
    source = extract_source(response)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = source.encode("utf-8")
    destination.write_bytes(data)
    return source, hashlib.sha256(data).hexdigest()


def read_saved_source(path: Path) -> tuple[str, str] | None:
    """读回已保存的生成源码；用于锁源后只能以同源码恢复既有结果的情形。"""
    if not path.is_file():
        return None
    data = path.read_bytes()
    if not 0 < len(data) <= MAX_SOURCE_BYTES:
        return None
    source = data.decode("utf-8")
    return source, hashlib.sha256(data).hexdigest()
