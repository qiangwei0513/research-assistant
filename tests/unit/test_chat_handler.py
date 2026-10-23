from __future__ import annotations

import json
from typing import Any

import pytest

from research_assistant.contracts.task import Task
from research_assistant.errors import BudgetExhausted, GatewayError
from research_assistant.handlers.chat import (
    CHAT_MAX_TOKENS,
    build_chat_user_prompt,
    parse_chat_payload,
    solve_chat,
)
from research_assistant.runtime.context import load_task_context
from research_assistant.runtime.gateway import FakeGateway, GatewayResponse, GatewayUsage
from research_assistant.runtime.settings import Settings

TEAM = "测试队伍 A+1"


def chat_task(**overrides: Any) -> Task:
    payload: dict[str, Any] = {
        "case_id": "A-01",
        "function": "Chat",
        "title": "室内植物光照与生长：科研对话",
        "input": "解释光照强度与生长速度的关系，并问一个最关键的限定问题。",
        "materials": [],
        "constraints": [],
        "expected_output": "解释关系并提问。",
        "output_schema_ref": "Chat",
    }
    payload.update(overrides)
    return Task.parse(payload)


def context_with(base_env: dict[str, str], responses: list[Any], **kwargs: Any):
    fake = FakeGateway(responses, **kwargs)
    return load_task_context(chat_task(), Settings.from_env(base_env), gateway=fake), fake


def model_json(
    answer: str = "光照强度提高会加快光合作用。", question: str = "你研究的是哪种室内植物？"
) -> str:
    return json.dumps({"answer": answer, "clarifying_question": question}, ensure_ascii=False)


def test_successful_chat_returns_exactly_three_fields(base_env: dict[str, str]) -> None:
    context, fake = context_with(base_env, [model_json()])
    result = solve_chat(chat_task(), context)
    assert list(result) == ["case_id", "answer", "clarifying_question"]
    assert result["case_id"] == "A-01"
    assert result["answer"] == "光照强度提高会加快光合作用。"
    assert len(fake.calls) == 1


def test_case_id_comes_from_the_task_not_the_model(base_env: dict[str, str]) -> None:
    """模型返回的 case_id 与未知字段一律丢弃，不透传。"""
    payload = json.dumps(
        {
            "case_id": "HACKED",
            "answer": "a",
            "clarifying_question": "q",
            "score": 100,
        },
        ensure_ascii=False,
    )
    context, _ = context_with(base_env, [payload])
    result = solve_chat(chat_task(case_id="A-100"), context)
    assert result["case_id"] == "A-100"
    assert "score" not in result


def test_prompt_includes_the_task_input_and_expected_output(base_env: dict[str, str]) -> None:
    prompt = build_chat_user_prompt(chat_task())
    assert "解释光照强度与生长速度的关系" in prompt
    assert "解释关系并提问。" in prompt


def test_constraints_are_forwarded_into_the_prompt(base_env: dict[str, str]) -> None:
    prompt = build_chat_user_prompt(chat_task(constraints=["不超过 300 字", "只讨论光照"]))
    assert "不超过 300 字" in prompt
    assert "只讨论光照" in prompt


def test_request_is_a_single_non_streaming_call(base_env: dict[str, str]) -> None:
    context, fake = context_with(base_env, [model_json()])
    solve_chat(chat_task(), context)
    stage, messages = fake.calls[0]
    assert stage == "chat.completion"
    assert [m.role for m in messages] == ["system", "user"]


def test_max_tokens_is_passed_to_the_gateway(base_env: dict[str, str]) -> None:
    recorded: dict[str, int | None] = {}

    class RecordingGateway:
        def complete(
            self, messages: Any, *, context: Any, stage: str, max_tokens: int | None = None
        ) -> GatewayResponse:
            recorded["max_tokens"] = max_tokens
            recorded["stage"] = stage
            return GatewayResponse(
                content=model_json(),
                finish_reason="stop",
                usage=GatewayUsage(1, 1, 2, None),
                reasoning=None,
                raw={},
            )

    context = load_task_context(
        chat_task(), Settings.from_env(base_env), gateway=RecordingGateway()
    )
    solve_chat(chat_task(), context)
    assert recorded["max_tokens"] == CHAT_MAX_TOKENS == 2048
    assert recorded["stage"] == "chat.completion"


def test_code_fenced_model_output_is_still_parsed(base_env: dict[str, str]) -> None:
    """我们要求无围栏，但模型有时会加；解析要能挡住这种情况。"""
    fenced = f"```json\n{model_json()}\n```"
    context, _ = context_with(base_env, [fenced])
    assert solve_chat(chat_task(), context)["answer"] == "光照强度提高会加快光合作用。"


def test_non_json_model_output_is_a_gateway_error(base_env: dict[str, str]) -> None:
    context, _ = context_with(base_env, ["光照确实很重要，我认为……"])
    with pytest.raises(GatewayError, match="JSON"):
        solve_chat(chat_task(), context)


def test_json_array_model_output_is_rejected(base_env: dict[str, str]) -> None:
    context, _ = context_with(base_env, ['[{"answer": "a"}]'])
    with pytest.raises(GatewayError, match="object"):
        solve_chat(chat_task(), context)


def test_missing_clarifying_question_is_rejected(base_env: dict[str, str]) -> None:
    context, _ = context_with(base_env, [json.dumps({"answer": "a"}, ensure_ascii=False)])
    with pytest.raises(GatewayError, match="clarifying_question"):
        solve_chat(chat_task(), context)


def test_blank_answer_is_rejected(base_env: dict[str, str]) -> None:
    context, _ = context_with(base_env, [model_json(answer="   ")])
    with pytest.raises(GatewayError, match="answer"):
        solve_chat(chat_task(), context)


def test_non_string_answer_is_rejected(base_env: dict[str, str]) -> None:
    context, _ = context_with(base_env, [json.dumps({"answer": 1, "clarifying_question": "q"})])
    with pytest.raises(GatewayError, match="answer"):
        solve_chat(chat_task(), context)


def test_parse_chat_payload_returns_a_tuple() -> None:
    answer, question = parse_chat_payload(model_json(), case_id="A-01")
    assert answer == "光照强度提高会加快光合作用。"
    assert question == "你研究的是哪种室内植物？"


def test_gateway_failure_propagates(base_env: dict[str, str]) -> None:
    context, _ = context_with(base_env, [GatewayError("upstream 502")])
    with pytest.raises(GatewayError, match="upstream 502"):
        solve_chat(chat_task(), context)


def test_exhausted_budget_is_reported_before_calling_the_model(
    base_env: dict[str, str],
) -> None:
    context, fake = context_with(base_env, [model_json()])
    object.__setattr__(context.deadline, "started_at", context.deadline.started_at - 179.5)
    with pytest.raises(BudgetExhausted):
        solve_chat(chat_task(), context)
    assert fake.calls == []


def test_model_content_is_not_written_to_logs(base_env: dict[str, str], capsys) -> None:
    """日志只记长度与计数，不记模型响应全文。"""
    marker = "标记性内容不应进日志"
    context, _ = context_with(base_env, [model_json(answer=marker)])
    solve_chat(chat_task(), context)
    captured = capsys.readouterr()
    assert marker not in captured.err
    assert marker not in captured.out
