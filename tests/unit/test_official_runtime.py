"""官方运行时入口：必须由 AgentRuntime 启动，且 /solve 仍说赛题一协议。

平台要求镜像入口执行 `AgentRuntime(MyAgent()).run()`；官方通用 /solve 又收不了
八字段报文。这里用假 agenthack 钉住三件事：app/health 来自运行时、SDK 的
/solve 被替换、session → task → llm 三层埋点仍然成立。
"""

from __future__ import annotations

import json
import sys
import types
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from research_assistant.runtime import telemetry
from research_assistant.runtime.gateway import FakeGateway
from research_assistant.runtime.official_runtime import build_official_app
from research_assistant.runtime.settings import Settings

EVENTS: list[str] = []


class FakeOperation:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def set(self, **attrs: Any) -> None:
        self.events.append("operation.set")

    def result(self, value: Any) -> None:
        self.events.append(f"task.result:{len(value)}")


class FakeTelemetry:
    export_enabled = True

    def __init__(self, events: list[str]) -> None:
        self.events = events

    def session(
        self,
        task_id: str,
        task_type: str,
        session_id: str | None,
        user_id: str | None,
        headers: Any,
    ) -> Any:
        events = self.events
        events.append(f"session.args:{task_id}:{task_type}:{session_id}:{dict(headers or {})}")

        class Session:
            def __enter__(self) -> Session:
                events.append("session.enter")
                return self

            def __exit__(self, *exc: Any) -> bool:
                events.append("session.exit")
                return False

        return Session()

    def flush(self) -> None:
        self.events.append("flush")


class FakeRuntime:
    def __init__(self, agent: Any) -> None:
        self.agent = agent
        self.events = EVENTS
        self.telemetry = FakeTelemetry(EVENTS)
        self.app = FastAPI()
        runtime = self

        @self.app.get("/health")
        def health() -> dict[str, Any]:
            return {"status": "healthy", "telemetry_configured": runtime.telemetry.export_enabled}

        @self.app.post("/solve")
        def generic_solve() -> dict[str, Any]:
            # 走到的化会暴露成 task_id/output 包装，测试据此判失败
            return {"task_id": "wrong", "status": "success", "output": {}, "metadata": {}}

        self.ran: tuple[str, int] | None = None

    def run(self, host: str = "0.0.0.0", port: int = 8080) -> None:
        self.ran = (host, port)


class FakeBaseAgent:
    async def solve(self, task_input: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover
        raise AssertionError("generic route must not be used")


@pytest.fixture
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    telemetry.reset_cache()
    EVENTS.clear()
    sdk_telemetry = types.ModuleType("agenthack.telemetry")

    @contextmanager
    def task() -> Iterator[FakeOperation]:
        EVENTS.append("task.enter")
        yield FakeOperation(EVENTS)
        EVENTS.append("task.exit")

    @contextmanager
    def llm_call(model: str, provider: str, endpoint: Any = None, streaming: bool = False) -> Any:
        EVENTS.append(f"llm:{model}")
        yield FakeOperation(EVENTS)

    sdk_telemetry.task = task  # type: ignore[attr-defined]
    sdk_telemetry.llm_call = llm_call  # type: ignore[attr-defined]

    root = types.ModuleType("agenthack")
    root.AgentRuntime = FakeRuntime  # type: ignore[attr-defined]
    root.BaseAgent = FakeBaseAgent  # type: ignore[attr-defined]
    root.telemetry = sdk_telemetry  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agenthack", root)
    monkeypatch.setitem(sys.modules, "agenthack.telemetry", sdk_telemetry)
    yield
    telemetry.reset_cache()


def _chat_task() -> dict[str, Any]:
    return {
        "case_id": "A-01",
        "function": "Chat",
        "title": "官方运行时协议验证",
        "input": "解释并提问。",
        "materials": [],
        "constraints": [],
        "expected_output": "解释并提问。",
        "output_schema_ref": "Chat",
    }


def _answer() -> str:
    return json.dumps({"answer": "答", "clarifying_question": "问？"}, ensure_ascii=False)


def test_entrypoint_uses_agent_runtime_and_its_health(
    fake_sdk: None, base_env: dict[str, str]
) -> None:
    runtime, settings = build_official_app(Settings.from_env(base_env))
    assert isinstance(runtime, FakeRuntime)
    assert settings.team == base_env["RESEARCH_WORKSPACE_TEAM"]
    with TestClient(runtime.app) as client:
        body = client.get("/health").json()
    assert body == {"status": "healthy", "telemetry_configured": True}


def test_solve_accepts_the_eight_field_body_and_returns_the_direct_object(
    fake_sdk: None, base_env: dict[str, str]
) -> None:
    runtime, _ = build_official_app(Settings.from_env(base_env), gateway=FakeGateway([_answer()]))
    with TestClient(runtime.app) as client:
        response = client.post("/solve", json=_chat_task())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["case_id"] == "A-01"
    assert body["answer"] == "答"
    assert "task_id" not in body and "output" not in body and "metadata" not in body
    # session → task → llm 的顺序来自官方运行时；flush 在 session 关闭之后
    assert runtime.events[0] == "session.args:A-01:Chat:None:{}"
    assert runtime.events[1:3] == ["session.enter", "task.enter"], runtime.events
    assert runtime.events[-3:] == ["task.exit", "session.exit", "flush"]
    assert any(event.startswith("task.result:") for event in runtime.events)


def test_transport_task_id_and_trace_context_reach_the_session(
    fake_sdk: None, base_env: dict[str, str]
) -> None:
    runtime, _ = build_official_app(Settings.from_env(base_env), gateway=FakeGateway([_answer()]))
    traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    with TestClient(runtime.app) as client:
        response = client.post(
            "/solve",
            json=_chat_task(),
            headers={
                "X-Research-Task-ID": "transport-7",
                "traceparent": traceparent,
                "Authorization": "Bearer sk-never-in-trace",
            },
        )
    assert response.status_code == 200
    assert runtime.events[0] == (
        f"session.args:A-01:Chat:transport-7:{{'traceparent': '{traceparent}'}}"
    )


def test_invalid_body_is_422_and_opens_no_session(fake_sdk: None, base_env: dict[str, str]) -> None:
    runtime, _ = build_official_app(Settings.from_env(base_env), gateway=FakeGateway([]))
    with TestClient(runtime.app) as client:
        response = client.post("/solve", json={"case_id": "A-01"})
    assert response.status_code == 422
    assert response.json()["error"]["type"] == "TaskContractError"
    assert runtime.events == []
