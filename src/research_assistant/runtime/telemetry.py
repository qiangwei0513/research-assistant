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


def use_instance(instance: Any | None) -> None:
    """复用外部已经建好的 Telemetry 实例。

    交付镜像由 agenthack.AgentRuntime 启动，它自己持有一个 Telemetry。若这里再建一个，
    同一进程会有两个 TracerProvider：span 被重复导出，session 上下文也不是同一个。
    因此官方入口必须把 runtime.telemetry 注入进来，之后的 session/task/llm 都走它。
    """
    global _TELEMETRY, _TELEMETRY_TRIED
    _TELEMETRY = instance
    _TELEMETRY_TRIED = True


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
    task_id: str,
    function: str,
    headers: Mapping[str, str] | None = None,
    *,
    session_id: str | None = None,
) -> Iterator[None]:
    """有 SDK 时进入 Telemetry.session；无 SDK 时为 no-op。

    task_id 是 SDK 语义里的「运行时任务」：优先用平台传输头 X-Research-Task-ID
    （清单明确它是独立 UUID，不等于 case_id），缺失时才退到 case_id；
    case_id 另作为 task span 属性写入。session_id 用平台会话头（若有），否则由 SDK 生成；
    headers 只应含 W3C trace context（traceparent/tracestate），SDK 靠它把本任务的
    span 接到平台这次请求的同一条 trace 上。
    """
    instance = _telemetry_instance()
    if instance is None:
        with nullcontext():
            yield
        return
    try:
        manager = instance.session(task_id, function, session_id, None, dict(headers or {}))
    except Exception as exc:
        _LOG.warning("telemetry.session_failed", reason=exc.__class__.__name__, task_id=task_id)
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
            "telemetry.session_enter_failed", reason=exc.__class__.__name__, task_id=task_id
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
                task_id=task_id,
            )
            raise exc from None
        if not suppressed:
            raise
    else:
        try:
            manager.__exit__(None, None, None)
        except Exception as exc:
            _LOG.warning(
                "telemetry.session_exit_failed", reason=exc.__class__.__name__, task_id=task_id
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


def telemetry_configured() -> bool:
    """平台镜像检查要求 /health 返回 telemetry_configured。

    语义与 SDK 的 AgentRuntime 一致：`Telemetry.export_enabled`，即存在 provider 或
    平台注入的 OTLP 端点（OTEL_EXPORTER_OTLP_TRACES_ENDPOINT / OTEL_EXPORTER_OTLP_ENDPOINT）。
    本地没有这些变量时为 False；平台上由注入的配置决定。
    构造 Telemetry 失败（例如 AGENTHACK_TELEMETRY_REQUIRED=true 却无端点）记为未配置并告警，
    绝不让 /health 因此抛异常。
    """
    try:
        instance = _telemetry_instance()
    except Exception as exc:  # /health 必须永远能回答
        _LOG.warning("telemetry.config_probe_failed", reason=exc.__class__.__name__)
        return False
    if instance is None:
        return False
    return bool(getattr(instance, "export_enabled", False))


@contextmanager
def llm_span(model: str, *, endpoint: str | None = None, streaming: bool = False):
    """为一次模型调用产出真实的 LLM trace span。

    平台评测会核验实际任务 Trace。SDK 的 openai 拦截器只认 openai 官方客户端，
    而我们用 httpx，所以走 SDK 明写的「Manual adapter for SDKs other than
    OpenAI Chat Completions」——`llm_call(model, provider, endpoint, streaming)`。
    业务异常照常穿过 span（SDK 会把它记进 span 再抛出）；
    只有遥测自身构造/退出的失败被吞掉并记日志。
    无 SDK 时 yield None，调用方据此跳过属性设置。
    """
    module = _import_module("agenthack.telemetry")
    factory = getattr(module, "llm_call", None) if module is not None else None
    if factory is None:
        yield None
        return
    try:
        manager = factory(model, "openai-compatible", endpoint=endpoint, streaming=streaming)
    except Exception as exc:
        _LOG.warning("telemetry.llm_span_unavailable", reason=exc.__class__.__name__)
        yield None
        return
    try:
        op = manager.__enter__()
    except Exception as exc:
        _LOG.warning("telemetry.llm_span_unavailable", reason=exc.__class__.__name__)
        yield None
        return
    try:
        yield op
    except BaseException as exc:
        manager.__exit__(type(exc), exc, exc.__traceback__)
        raise
    else:
        try:
            manager.__exit__(None, None, None)
        except Exception as exc:
            _LOG.warning("telemetry.llm_span_exit_failed", reason=exc.__class__.__name__)


@contextmanager
def task_span():
    """为一次 /solve 产出业务 task span，补齐 session → task → llm 的拓扑。

    平台报错 AGENT_RUNTIME_TRACE_MISSING 的直接原因：之前只有 session 与 llm 两层，
    没有 `task()` 这个「solve 调用」span。SDK 的 AgentRuntime 是
    `with telemetry.session(...): with task():`，task() 里再用 llm_call 埋点。
    这里保持同一拓扑，但不用它的路由。

    注意：SDK 的 task() 要求 current_session()，所以本函数必须嵌在 trace_session 内使用；
    无 SDK 或不在 session 内时退化为 no-op，绝不影响答题。
    """
    module = _import_module("agenthack.telemetry")
    factory = getattr(module, "task", None) if module is not None else None
    if factory is None:
        yield None
        return
    manager = None
    try:
        manager = factory()
        op = manager.__enter__()
    except Exception as exc:
        _LOG.warning("telemetry.task_span_unavailable", reason=exc.__class__.__name__)
        yield None
        return
    try:
        yield op
    except BaseException as exc:
        # 同 trace_session：业务异常必须原样上抛，不能被导出失败顶替
        try:
            suppressed = manager.__exit__(type(exc), exc, exc.__traceback__)
        except Exception as exit_exc:
            _LOG.warning("telemetry.task_span_exit_failed", reason=exit_exc.__class__.__name__)
            raise exc from None
        if not suppressed:
            raise
    else:
        try:
            manager.__exit__(None, None, None)
        except Exception as exc:
            _LOG.warning("telemetry.task_span_exit_failed", reason=exc.__class__.__name__)
