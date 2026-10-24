"""SDK 遥测：telemetry_configured 与 llm_span 的行为。

平台镜像检查要求 /health 返回 telemetry_configured=true，评测还会核验实际任务 Trace。
因此这些路径必须有测试：有 SDK 时字段由 export_enabled 决定、
每次模型调用都开 llm_call span 且 usage 在 span 结束前写入、
业务异常穿过 span 不被吞掉、遥测自身失败不影响答题。
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest
from fastapi.testclient import TestClient

from research_assistant.api import create_app
from research_assistant.runtime import telemetry
from research_assistant.runtime.gateway import HttpGateway, Message
from research_assistant.runtime.logging import JsonLogger
from research_assistant.runtime.settings import GatewaySettings, Settings

TEAM = "测试队伍 A+1"


class FakeSpan:
    def __init__(self, calls: dict[str, Any]) -> None:
        self.calls = calls
        self.attrs: dict[str, Any] = {}
        self.exited = False

    def __enter__(self) -> FakeSpan:
        self.calls["enter"] = self.calls.get("enter", 0) + 1
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.exited = True
        self.calls["exited_exception"] = exc_type
        return False

    def set(self, **attrs: Any) -> None:
        self.attrs.update(attrs)
        self.calls["attrs"] = self.attrs


class FakeTelemetry:
    def __init__(self, export_enabled: bool) -> None:
        self.export_enabled = export_enabled

    def flush(self) -> None:
        pass

    def shutdown(self) -> None:
        pass


def install_sdk(
    monkeypatch: pytest.MonkeyPatch,
    *,
    export_enabled: bool,
    span: FakeSpan | None = None,
    calls: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if calls is None:
        calls = {"spans": []}
    module = types.ModuleType("agenthack.telemetry")

    def telemetry_factory() -> FakeTelemetry:
        return FakeTelemetry(export_enabled)

    module.Telemetry = telemetry_factory  # type: ignore[attr-defined]

    if span is not None:

        def llm_call(model: str, provider: str, endpoint=None, streaming=False):
            calls["spans"].append(
                {"model": model, "provider": provider, "endpoint": endpoint, "streaming": streaming}
            )
            return span

        module.llm_call = llm_call  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agenthack.telemetry", module)
    telemetry.reset_cache()
    return calls


def without_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "agenthack.telemetry", None)
    telemetry.reset_cache()


def test_configured_is_false_without_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    without_sdk(monkeypatch)
    assert telemetry.telemetry_configured() is False


def test_configured_reflects_export_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    install_sdk(monkeypatch, export_enabled=True)
    assert telemetry.telemetry_configured() is True
    telemetry.reset_cache()
    install_sdk(monkeypatch, export_enabled=False)
    assert telemetry.telemetry_configured() is False


def test_configured_is_false_when_construction_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """AGENTHACK_TELEMETRY_REQUIRED=true 而无端点时构造会抛；/health 不能因此挂掉。"""
    module = types.ModuleType("agenthack.telemetry")

    class Broken:
        def __init__(self) -> None:
            raise RuntimeError("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT is required")

    module.Telemetry = Broken  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agenthack.telemetry", module)
    telemetry.reset_cache()
    assert telemetry.telemetry_configured() is False


def test_llm_span_is_noop_without_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    without_sdk(monkeypatch)
    with telemetry.llm_span("test-model", endpoint="http://gw/v1") as span:
        assert span is None


def test_llm_span_yields_operation_and_supports_set(monkeypatch: pytest.MonkeyPatch) -> None:
    shared: dict[str, Any] = {"spans": []}
    span = FakeSpan(shared)
    install_sdk(monkeypatch, export_enabled=True, span=span, calls=shared)
    calls = shared
    with telemetry.llm_span("deepseek_v4", endpoint="http://gw/v1/chat/completions") as op:
        op.set(input_tokens=100, output_tokens=50, finish_reason="stop")
    assert op is span
    assert span.exited is True
    assert span.attrs == {"input_tokens": 100, "output_tokens": 50, "finish_reason": "stop"}
    assert calls["enter"] == 1
    assert calls["spans"][0]["provider"] == "openai-compatible"
    assert calls["spans"][0]["model"] == "deepseek_v4"
    assert calls["spans"][0]["streaming"] is False


def test_business_exception_propagates_through_the_span(monkeypatch: pytest.MonkeyPatch) -> None:
    span = FakeSpan({})
    install_sdk(monkeypatch, export_enabled=True, span=span)
    with pytest.raises(ValueError, match="model boom"), telemetry.llm_span("test-model"):
        raise ValueError("model boom")
    assert span.exited is True
    assert span.calls["exited_exception"] is ValueError


def test_span_exit_failure_does_not_mask_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    class ExplodingExit(FakeSpan):
        def __exit__(self, exc_type, exc, tb) -> bool:
            self.calls["exit_attempts"] = self.calls.get("exit_attempts", 0) + 1
            raise RuntimeError("otel exporter down")

    span = ExplodingExit({})
    install_sdk(monkeypatch, export_enabled=True, span=span)
    with telemetry.llm_span("test-model") as op:
        op.set(finish_reason="stop")
    # __exit__ 被调用过，只是它的失败被吞掉，没有打断答题
    assert span.calls["exit_attempts"] == 1


def test_health_exposes_telemetry_configured(
    base_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    install_sdk(monkeypatch, export_enabled=True)
    with TestClient(create_app(Settings.from_env(base_env))) as client:
        body = client.get("/health").json()
    assert body["telemetry_configured"] is True
    assert body["status"] == "ok"


def test_health_telemetry_configured_is_false_without_sdk(
    base_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    without_sdk(monkeypatch)
    with TestClient(create_app(Settings.from_env(base_env))) as client:
        body = client.get("/health").json()
    assert body["telemetry_configured"] is False
    assert body["status"] == "ok"


def _completion_payload() -> dict[str, Any]:
    return {
        "id": "x",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "OK", "reasoning": None},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "prompt_tokens_details": {"cached_tokens": 3},
        },
    }


def test_http_gateway_records_usage_on_the_llm_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """平台核验的是实际任务 Trace：每次网关调用都要开 span 且 usage 在结束前写入。"""
    span = FakeSpan({})
    install_sdk(monkeypatch, export_enabled=True, span=span)
    import httpx

    settings = GatewaySettings(
        family="challenge",
        base_url="http://gw.invalid/v1",
        api_key="sk-test-not-a-real-key",
        model="deepseek_v4",
        session_id=None,
    )

    class StubContext:
        case_id = "A-01"

        class deadline:
            @staticmethod
            def take(stage: str, minimum: float, *, case_id: str | None = None) -> float:
                return 180.0

        log = JsonLogger("test")

    gateway = HttpGateway(
        settings,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=_completion_payload())
        ),
    )
    response = gateway.complete(
        [Message(role="user", content="hi")], context=StubContext(), stage="test"
    )
    assert response.content == "OK"
    assert span.exited is True
    assert span.attrs["input_tokens"] == 100
    assert span.attrs["output_tokens"] == 50
    assert span.attrs["finish_reason"] == "stop"
    assert span.attrs["cached_tokens"] == 3
    assert span.attrs["context_length"] == 100
