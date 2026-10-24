"""官方 agenthack SDK 遥测的可选复用。

本地开发机没有 agenthack 包，全部函数降级为 no-op。
遥测失败绝不影响答案返回：整个模块的每个入口都吞掉异常，只记日志。
"""

from __future__ import annotations

import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, nullcontext
from typing import Any

from research_assistant.runtime.logging import JsonLogger

_LOG = JsonLogger("telemetry")
_INTERCEPTORS_INSTALLED = False
_TELEMETRY: Any | None = None
_TELEMETRY_TRIED = False


def reset_cache() -> None:
    """仅测试使用：清掉进程级缓存，让 monkeypatch 后的模块状态生效。"""
    global _INTERCEPTORS_INSTALLED, _TELEMETRY, _TELEMETRY_TRIED
    _INTERCEPTORS_INSTALLED = False
    _TELEMETRY = None
    _TELEMETRY_TRIED = False


def _import_module(name: str) -> Any | None:
    module = sys.modules.get(name, "missing")
    if module is None:
        return None
    if module != "missing":
        return module
    try:
        import importlib

        return importlib.import_module(name)
    except Exception as exc:  # 遥测缺失不得影响主流程
        _LOG.warning("telemetry.sdk_unavailable", module=name, reason=exc.__class__.__name__)
        return None


def sdk_available() -> bool:
    return _import_module("agenthack.interceptor") is not None


def install_interceptors() -> bool:
    """进程级、幂等。返回是否真的装上了 SDK interceptor。"""
    global _INTERCEPTORS_INSTALLED
    if _INTERCEPTORS_INSTALLED:
        return True
    module = _import_module("agenthack.interceptor")
    if module is None:
        return False
    try:
        module.install_all_interceptors()
    except Exception as exc:
        _LOG.warning("telemetry.interceptor_failed", reason=exc.__class__.__name__)
        return False
    _INTERCEPTORS_INSTALLED = True
    _LOG.info("telemetry.interceptors_installed")
    return True


def _telemetry_instance() -> Any | None:
    global _TELEMETRY, _TELEMETRY_TRIED
    if _TELEMETRY_TRIED:
        return _TELEMETRY
    _TELEMETRY_TRIED = True
    module = _import_module("agenthack.telemetry")
    if module is None:
        return None
    try:
        _TELEMETRY = module.Telemetry()
    except Exception as exc:
        _LOG.warning("telemetry.init_failed", reason=exc.__class__.__name__)
        _TELEMETRY = None
    return _TELEMETRY


@contextmanager
def trace_session(
    case_id: str, function: str, headers: Mapping[str, str] | None = None
) -> Iterator[None]:
    """有 SDK 时进入 Telemetry.session；无 SDK 时为 no-op。"""
    instance = _telemetry_instance()
    if instance is None:
        with nullcontext():
            yield
        return
    try:
        manager = instance.session(case_id, function, None, None, dict(headers or {}))
    except Exception as exc:
        _LOG.warning("telemetry.session_failed", reason=exc.__class__.__name__, case_id=case_id)
        with nullcontext():
            yield
        return
    # 手动进出上下文管理器，而不是用 `with manager: yield`。
    # 原因：SDK 的 session().__exit__ 自身可能抛异常（OTEL collector 不可达、导出超时、
    # 关闭竞态）。用 with 包住 yield 时，那个异常会从 trace_session 逃出、越过 api 的
    # ResearchAssistantError 分支、变成 HTTP 500——而此时答案早已算出并落盘校验通过，
    # 等于静默丢弃一个正确结果。spec §5.6 要求遥测失败绝不影响答案返回。
    try:
        manager.__enter__()
    except Exception as exc:
        _LOG.warning(
            "telemetry.session_enter_failed", reason=exc.__class__.__name__, case_id=case_id
        )
        yield
        return

    try:
        yield
    except BaseException as exc:
        # 业务异常照常向上抛；只有 __exit__ 自身的失败被吞掉，且改抛原业务异常
        try:
            suppressed = manager.__exit__(type(exc), exc, exc.__traceback__)
        except Exception as exit_exc:
            _LOG.warning(
                "telemetry.session_exit_failed",
                reason=exit_exc.__class__.__name__,
                case_id=case_id,
            )
            raise exc from None
        if not suppressed:
            raise
    else:
        try:
            manager.__exit__(None, None, None)
        except Exception as exc:
            _LOG.warning(
                "telemetry.session_exit_failed", reason=exc.__class__.__name__, case_id=case_id
            )


def flush_telemetry() -> None:
    instance = _telemetry_instance()
    if instance is None:
        return
    try:
        instance.flush()
    except Exception as exc:
        _LOG.warning("telemetry.flush_failed", reason=exc.__class__.__name__)


def shutdown_telemetry() -> None:
    instance = _telemetry_instance()
    if instance is None:
        return
    try:
        instance.shutdown()
    except Exception as exc:
        _LOG.warning("telemetry.shutdown_failed", reason=exc.__class__.__name__)
