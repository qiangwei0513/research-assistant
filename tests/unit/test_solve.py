from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from research_assistant.contracts.results import serialize_result
from research_assistant.contracts.task import Task
from research_assistant.errors import HandlerNotImplemented, TaskContractError
from research_assistant.runtime.context import load_task_context
from research_assistant.runtime.gateway import FakeGateway
from research_assistant.runtime.settings import Settings
from research_assistant.solve import persist_and_verify, solve

TEAM = "测试队伍 A+1"


def chat_payload() -> dict[str, Any]:
    return {
        "case_id": "A-01",
        "function": "Chat",
        "title": "光照与生长",
        "input": "解释关系并提一个限定问题。",
        "materials": [],
        "constraints": [],
        "expected_output": "解释关系并提问。",
        "output_schema_ref": "Chat",
    }


def model_json() -> str:
    return json.dumps(
        {"answer": "光照强度影响光合速率。", "clarifying_question": "研究对象是哪种室内植物？"},
        ensure_ascii=False,
    )


def context_for(base_env: dict[str, str], payload: dict[str, Any], responses: list[Any]):
    task = Task.parse(payload)
    return task, load_task_context(
        task, Settings.from_env(base_env), gateway=FakeGateway(responses)
    )


def test_solve_returns_the_chat_result(base_env: dict[str, str]) -> None:
    task, context = context_for(base_env, chat_payload(), [model_json()])
    result = solve(task, context)
    assert result == {
        "case_id": "A-01",
        "answer": "光照强度影响光合速率。",
        "clarifying_question": "研究对象是哪种室内植物？",
    }


def test_solve_routes_by_function_not_by_schema_ref(base_env: dict[str, str]) -> None:
    payload = {
        **chat_payload(),
        "case_id": "B-01",
        "function": "Literature_Retrieval",
        "output_schema_ref": "Literature_Retrieval",
    }
    task, context = context_for(base_env, payload, [])
    with pytest.raises(HandlerNotImplemented, match="P2"):
        solve(task, context)


def test_solve_rejects_a_result_with_extra_fields(
    base_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """HANDLER_BY_FUNCTION 是 MappingProxyType，不能 setitem；改打 solve 模块内的名字。"""
    from research_assistant import solve as solve_module

    task, context = context_for(base_env, chat_payload(), [model_json()])

    def bad_handler(t: Task, c: Any) -> dict[str, Any]:
        return {"case_id": t.case_id, "answer": "a", "clarifying_question": "q", "score": 5}

    monkeypatch.setattr(solve_module, "HANDLER_BY_FUNCTION", {task.function: bad_handler})
    with pytest.raises(TaskContractError, match="score"):
        solve(task, context)


def test_persist_writes_the_team_result_json(base_env: dict[str, str], tmp_workspace: Path) -> None:
    task, context = context_for(base_env, chat_payload(), [model_json()])
    data = persist_and_verify(solve(task, context), context)
    written = tmp_workspace / "data" / TEAM / "A-01.json"
    assert written.read_bytes() == data


def test_persisted_bytes_match_the_serialized_result(
    base_env: dict[str, str], tmp_workspace: Path
) -> None:
    task, context = context_for(base_env, chat_payload(), [model_json()])
    result = solve(task, context)
    data = persist_and_verify(result, context)
    assert data == serialize_result(result)


def test_persisted_file_is_parseable_and_carries_the_same_case_id(
    base_env: dict[str, str], tmp_workspace: Path
) -> None:
    task, context = context_for(base_env, chat_payload(), [model_json()])
    persist_and_verify(solve(task, context), context)
    loaded = json.loads((tmp_workspace / "data" / TEAM / "A-01.json").read_text(encoding="utf-8"))
    assert loaded["case_id"] == "A-01"


def test_persist_creates_the_work_directory_only_when_needed(
    base_env: dict[str, str], tmp_workspace: Path
) -> None:
    task, context = context_for(base_env, chat_payload(), [model_json()])
    persist_and_verify(solve(task, context), context)
    assert not (tmp_workspace / "data" / TEAM / "work" / "A-01").exists()
