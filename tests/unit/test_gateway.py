from __future__ import annotations

import io
import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from research_assistant.errors import BudgetExhausted, GatewayError
from research_assistant.runtime.deadline import Deadline
from research_assistant.runtime.gateway import (
    FakeGateway,
    GatewayResponse,
    GatewayUsage,
    HttpGateway,
    Message,
)
from research_assistant.runtime.logging import JsonLogger
from research_assistant.runtime.settings import GatewaySettings


@pytest.fixture
def log_stream() -> io.StringIO:
    return io.StringIO()


@dataclass
class StubContext:
    """只提供 gateway 真正用到的三个属性，避免与 SolveContext 循环依赖。"""

    case_id: str = "A-01"
    deadline: Deadline = field(
        default_factory=lambda: Deadline(started_at=0.0, budget_seconds=180.0)
    )
    log: Any = None

    def __post_init__(self) -> None:
        if self.log is None:
            self.log = JsonLogger("test")
        if self.deadline.started_at == 0.0:
            self.deadline = Deadline.start(180.0)


SETTINGS = GatewaySettings(
    family="challenge",
    base_url="http://gateway.invalid/v1",
    api_key="sk-test-not-a-real-key",
    model="test-model",
    session_id=None,
)


def completion_response(
    content: str = "OK",
    finish_reason: str = "stop",
    *,
    usage: dict[str, Any] | None = None,
    reasoning: Any = None,
) -> dict[str, Any]:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content, "reasoning": reasoning},
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage
        if usage is not None
        else {
            "prompt_tokens": 9,
            "completion_tokens": 2,
            "total_tokens": 11,
            "prompt_tokens_details": {"cached_tokens": 0},
            "completion_tokens_details": None,
        },
    }


def gateway_returning(payload: dict[str, Any], status_code: int = 200) -> HttpGateway:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    return HttpGateway(SETTINGS, transport=httpx.MockTransport(handler))


def test_successful_completion_is_parsed() -> None:
    gw = gateway_returning(completion_response(content="光照影响生长。"))
    response = gw.complete([Message(role="user", content="hi")], context=StubContext(), stage="t")
    assert response.content == "光照影响生长。"
    assert response.finish_reason == "stop"
    assert response.usage.prompt_tokens == 9
    assert response.usage.completion_tokens == 2
    assert response.usage.total_tokens == 11
    assert response.usage.cached_tokens == 0


def test_raw_response_is_preserved_for_source_extraction() -> None:
    payload = completion_response()
    gw = gateway_returning(payload)
    response = gw.complete([Message(role="user", content="hi")], context=StubContext(), stage="t")
    assert json.loads(json.dumps(response.raw)) == payload


def test_request_body_matches_openai_chat_completions() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json=completion_response())

    gw = HttpGateway(SETTINGS, transport=httpx.MockTransport(handler))
    gw.complete(
        [Message(role="system", content="s"), Message(role="user", content="u")],
        context=StubContext(),
        stage="t",
        max_tokens=256,
    )
    assert captured["url"] == "http://gateway.invalid/v1/chat/completions"
    assert captured["body"]["model"] == "test-model"
    assert captured["body"]["stream"] is False
    assert captured["body"]["max_tokens"] == 256
    assert captured["body"]["messages"] == [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]
    assert captured["headers"]["content-type"] == "application/json"


def test_max_tokens_is_omitted_when_not_requested() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=completion_response())

    HttpGateway(SETTINGS, transport=httpx.MockTransport(handler)).complete(
        [Message(role="user", content="u")], context=StubContext(), stage="t"
    )
    assert "max_tokens" not in captured["body"]


def test_session_header_is_sent_only_when_configured() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json=completion_response())

    with_session = GatewaySettings(
        family="challenge",
        base_url="http://gateway.invalid/v1",
        api_key="sk-x",
        model="m",
        session_id="sess-42",
    )
    HttpGateway(with_session, transport=httpx.MockTransport(handler)).complete(
        [Message(role="user", content="u")], context=StubContext(), stage="t"
    )
    assert captured["headers"]["x-session-id"] == "sess-42"

    HttpGateway(SETTINGS, transport=httpx.MockTransport(handler)).complete(
        [Message(role="user", content="u")], context=StubContext(), stage="t"
    )
    assert "x-session-id" not in captured["headers"]


def test_base_url_trailing_slash_does_not_double_up() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=completion_response())

    settings = GatewaySettings(
        family="challenge", base_url="http://gateway.invalid/v1/", api_key="k", model="m"
    )
    HttpGateway(settings, transport=httpx.MockTransport(handler)).complete(
        [Message(role="user", content="u")], context=StubContext(), stage="t"
    )
    assert captured["url"] == "http://gateway.invalid/v1/chat/completions"


def test_api_key_is_never_logged(log_stream: io.StringIO) -> None:
    gw = HttpGateway(
        SETTINGS,
        log=JsonLogger("test", stream=log_stream),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=completion_response())
        ),
    )
    gw.complete([Message(role="user", content="u")], context=StubContext(), stage="t")
    assert "sk-test-not-a-real-key" not in log_stream.getvalue()


def test_truncated_response_is_rejected() -> None:
    gw = gateway_returning(completion_response(content="partial", finish_reason="length"))
    with pytest.raises(GatewayError, match="finish_reason"):
        gw.complete([Message(role="user", content="u")], context=StubContext(), stage="t")


def test_non_200_is_rejected_without_leaking_the_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream unavailable")

    gw = HttpGateway(SETTINGS, transport=httpx.MockTransport(handler))
    with pytest.raises(GatewayError) as excinfo:
        gw.complete([Message(role="user", content="u")], context=StubContext(), stage="t")
    assert "503" in str(excinfo.value)
    assert "sk-test-not-a-real-key" not in str(excinfo.value)


def test_transport_error_is_wrapped_as_gateway_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    gw = HttpGateway(SETTINGS, transport=httpx.MockTransport(handler))
    with pytest.raises(GatewayError, match="boom"):
        gw.complete([Message(role="user", content="u")], context=StubContext(), stage="t")


def test_unparseable_body_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    gw = HttpGateway(SETTINGS, transport=httpx.MockTransport(handler))
    with pytest.raises(GatewayError):
        gw.complete([Message(role="user", content="u")], context=StubContext(), stage="t")


def test_missing_choices_is_rejected() -> None:
    gw = gateway_returning({"id": "x", "choices": []})
    with pytest.raises(GatewayError, match="choices"):
        gw.complete([Message(role="user", content="u")], context=StubContext(), stage="t")


def test_null_usage_is_tolerated() -> None:
    payload = completion_response()
    payload["usage"] = None
    gw = gateway_returning(payload)
    response = gw.complete([Message(role="user", content="u")], context=StubContext(), stage="t")
    assert response.usage == GatewayUsage(None, None, None, None)


def test_null_completion_token_details_is_tolerated() -> None:
    """实测开发网关会返回 completion_tokens_details: null。"""
    gw = gateway_returning(completion_response())
    response = gw.complete([Message(role="user", content="u")], context=StubContext(), stage="t")
    assert response.usage.cached_tokens == 0


def test_reasoning_field_is_surfaced_when_present() -> None:
    gw = gateway_returning(completion_response(reasoning="思考过程"))
    response = gw.complete([Message(role="user", content="u")], context=StubContext(), stage="t")
    assert response.reasoning == "思考过程"


def test_empty_message_list_is_rejected() -> None:
    gw = gateway_returning(completion_response())
    with pytest.raises(GatewayError, match="messages"):
        gw.complete([], context=StubContext(), stage="t")


def test_insufficient_budget_raises_before_any_request() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=completion_response())

    gw = HttpGateway(SETTINGS, transport=httpx.MockTransport(handler))
    context = StubContext(
        deadline=Deadline(started_at=time.monotonic() - 179.0, budget_seconds=180.0)
    )
    with pytest.raises(BudgetExhausted):
        gw.complete([Message(role="user", content="u")], context=context, stage="t")
    assert calls == []


def test_timeout_is_derived_from_the_deadline() -> None:
    gw = HttpGateway(
        SETTINGS,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=completion_response())
        ),
    )
    context = StubContext(deadline=Deadline(started_at=time.monotonic(), budget_seconds=42.0))
    gw.complete([Message(role="user", content="u")], context=context, stage="t")
    assert gw.last_timeout_seconds == pytest.approx(42.0, abs=1.0)


def test_no_automatic_retry_on_failure() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(500, text="nope")

    gw = HttpGateway(SETTINGS, transport=httpx.MockTransport(handler))
    with pytest.raises(GatewayError):
        gw.complete([Message(role="user", content="u")], context=StubContext(), stage="t")
    assert len(attempts) == 1


def test_fake_gateway_returns_scripted_responses_in_order() -> None:
    fake = FakeGateway(["first", "second"])
    ctx = StubContext()
    assert (
        fake.complete([Message(role="user", content="a")], context=ctx, stage="s1").content
        == "first"
    )
    assert (
        fake.complete([Message(role="user", content="b")], context=ctx, stage="s2").content
        == "second"
    )
    assert [stage for stage, _ in fake.calls] == ["s1", "s2"]


def test_fake_gateway_records_messages() -> None:
    fake = FakeGateway(["x"])
    messages = (Message(role="system", content="s"), Message(role="user", content="u"))
    fake.complete(messages, context=StubContext(), stage="s")
    assert fake.calls[0][1] == messages


def test_fake_gateway_accepts_full_responses() -> None:
    response = GatewayResponse(
        content="c",
        finish_reason="stop",
        usage=GatewayUsage(1, 2, 3, 0),
        reasoning=None,
        raw={"k": "v"},
    )
    assert (
        FakeGateway([response]).complete(
            [Message(role="user", content="u")], context=StubContext(), stage="s"
        )
        is response
    )


def test_fake_gateway_can_raise_scripted_exceptions() -> None:
    fake = FakeGateway([GatewayError("boom")])
    with pytest.raises(GatewayError, match="boom"):
        fake.complete([Message(role="user", content="u")], context=StubContext(), stage="s")


def test_fake_gateway_exhaustion_is_an_error() -> None:
    fake = FakeGateway(["only-one"])
    ctx = StubContext()
    fake.complete([Message(role="user", content="u")], context=ctx, stage="s")
    with pytest.raises(GatewayError, match="no scripted response"):
        fake.complete([Message(role="user", content="u")], context=ctx, stage="s")


def test_fake_gateway_can_simulate_slowness() -> None:
    fake = FakeGateway(["slow"], delay_seconds=0.05)
    started = time.monotonic()
    fake.complete([Message(role="user", content="u")], context=StubContext(), stage="s")
    assert time.monotonic() - started >= 0.05


def test_usage_from_response_handles_missing_and_partial_usage() -> None:
    assert GatewayUsage.from_response(None) == GatewayUsage(None, None, None, None)
    assert GatewayUsage.from_response({}) == GatewayUsage(None, None, None, None)
    parsed = GatewayUsage.from_response({"prompt_tokens": 5, "completion_tokens": None})
    assert parsed.prompt_tokens == 5
    assert parsed.completion_tokens is None


def test_usage_from_response_ignores_non_numeric_values() -> None:
    parsed = GatewayUsage.from_response({"prompt_tokens": "many", "total_tokens": 7})
    assert parsed.prompt_tokens is None
    assert parsed.total_tokens == 7


def test_message_to_json() -> None:
    assert Message(role="system", content="c").to_json() == {"role": "system", "content": "c"}


def test_slow_trickle_response_cannot_overrun_wall_clock_budget():
    import contextlib
    import threading
    from dataclasses import replace
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    payload = json.dumps(completion_response()).encode()

    class Trickle(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                for index in range(0, len(payload), 30):
                    self.wfile.write(payload[index : index + 30])
                    self.wfile.flush()
                    time.sleep(0.05)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Trickle)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    gateway = HttpGateway(replace(SETTINGS, base_url=f"http://127.0.0.1:{server.server_port}"))
    try:
        started = time.monotonic()
        with pytest.raises(GatewayError, match="Timeout"):
            gateway._request_and_parse({}, 0.15, "trickle", StubContext())
        assert time.monotonic() - started < 0.5
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
