"""脱敏结构化日志。

允许：错误类型、阶段耗时、finish_reason、usage 计数、SHA-256 摘要、版本号、case_id、dispatch_id。
禁止：请求头原文、API key、全量环境变量、论文正文全文、模型响应全文。
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Protocol, TextIO

REDACTED_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "password",
        "secret",
        "token",
        "headers",
        "challenge_llm_api_key",
        "platform_api_key",
        "x-session-id",
        "challenge_session_id",
        # dataclasses.asdict(GatewaySettings) 产出的键名就是 session_id，
        # 归一化后为 sessionid，不被上面两个候选项覆盖，故必须单列。
        "session_id",
    }
)

# 敏感的是「令牌值」（凭据），不是「令牌计数」；usage 计数属允许记录项。
# 精确匹配（非子串），因此 my_prompt_tokens 这类变体仍会被脱敏——失效关闭方向正确。
USAGE_COUNT_KEYS: frozenset[str] = frozenset(
    {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cached_tokens",
        "reasoning_tokens",
        "max_tokens",
        "token_count",
        "total_input_tokens",
        "total_output_tokens",
    }
)
REDACTED = "***"

# 全局约束「禁止模块级可变状态」：用 MappingProxyType 冻结（仍支持 .get / []）。
_LEVELS: Mapping[str, int] = MappingProxyType({"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40})


class BoundLogger(Protocol):
    def debug(self, event: str, **fields: Any) -> None: ...
    def info(self, event: str, **fields: Any) -> None: ...
    def warning(self, event: str, **fields: Any) -> None: ...
    def error(self, event: str, **fields: Any) -> None: ...
    def bind(self, **fields: Any) -> BoundLogger: ...


def _normalise(key: str) -> str:
    """归一化字段名：去首尾空白、转小写、去掉 `_` 与 `-`。

    使 CHALLENGE_LLM_API_KEY、x-api-key、Prompt-Tokens 这类变体可与常量表逐字比对。
    """
    return key.strip().lower().replace("_", "").replace("-", "")


_NORMALISED_USAGE_COUNT_KEYS = frozenset(_normalise(key) for key in USAGE_COUNT_KEYS)


def _is_sensitive(key: str) -> bool:
    """按键名判定是否需要脱敏。

    默认走去分隔符的**子串**匹配（失效关闭：宁可多脱敏也不漏密钥）。唯一例外是 usage
    计数白名单——它承载「令牌计数」而非「令牌值」，按归一化后的**精确**匹配放行。
    """
    normalised = _normalise(key)
    if normalised in _NORMALISED_USAGE_COUNT_KEYS:
        return False
    return any(
        candidate.replace("_", "").replace("-", "") in normalised for candidate in REDACTED_KEYS
    )


def _redact_value(value: Any) -> Any:
    """递归脱敏任意值。

    mapping 逐键处理；list/tuple 逐元素递归（容器统一降级为 list，与既有行为一致），
    因此 list 套 list、tuple 套 tuple 里的密钥同样挡得住。
    """
    if isinstance(value, Mapping):
        return redact(value)
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    return value


def redact(fields: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in fields.items():
        out[key] = REDACTED if _is_sensitive(key) else _redact_value(value)
    return out


class JsonLogger:
    """每行一个 JSON 对象；bind 返回新实例，不修改父级。"""

    def __init__(
        self,
        name: str,
        level: str = "INFO",
        stream: TextIO | None = None,
        bound: Mapping[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.level = level.upper()
        self._stream = stream if stream is not None else sys.stderr
        self._bound: dict[str, Any] = dict(bound or {})

    def bind(self, **fields: Any) -> JsonLogger:
        return JsonLogger(self.name, self.level, self._stream, {**self._bound, **fields})

    def _enabled(self, level: str) -> bool:
        return _LEVELS[level] >= _LEVELS.get(self.level, 20)

    def _emit(self, level: str, event: str, fields: Mapping[str, Any]) -> None:
        if not self._enabled(level):
            return
        # 日志是旁路，绝不能把异常抛回答题路径（与遥测模块同一原则）。因此**脱敏也在守卫区内**：
        # 循环引用会让 redact 抛 RecursionError，而它不是 TypeError/ValueError，兜底分支接不住。
        # 这里刻意用宽 except——病态载荷一律降级为最小记录，而不是让整行日志连带请求一起崩掉；
        # 降级记录仍会落盘（unserialisable=True），故失败可见，不会被静默吞掉。
        try:
            record: dict[str, Any] = {
                "ts": round(time.time(), 3),
                "level": level.lower(),
                "logger": self.name,
                "event": event,
            }
            record.update(redact(self._bound))
            record.update(redact(fields))
            line = json.dumps(record, ensure_ascii=False, default=str, sort_keys=False)
        except Exception:
            line = json.dumps({"level": level.lower(), "event": event, "unserialisable": True})
        # 写出同样在旁路守卫内：stream 损坏（已关闭/断管）时回落到 sys.__stderr__ 而非静默吞掉，
        # 回落也失败才放弃该行——任何情况下都不把 I/O 异常抛回答题路径。
        try:
            print(line, file=self._stream, flush=True)
        except Exception:
            with contextlib.suppress(Exception):
                print(line, file=sys.__stderr__, flush=True)

    def debug(self, event: str, **fields: Any) -> None:
        self._emit("DEBUG", event, fields)

    def info(self, event: str, **fields: Any) -> None:
        self._emit("INFO", event, fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._emit("WARNING", event, fields)

    def error(self, event: str, **fields: Any) -> None:
        self._emit("ERROR", event, fields)
