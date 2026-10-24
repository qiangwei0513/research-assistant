"""带一次有界重试的模型调用测试。"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from research_assistant.contracts.task import Task
from research_assistant.errors import GatewayError
from research_assistant.runtime.gateway import FakeGateway
from research_assistant.runtime.modelcall import (
    MAX_ATTEMPTS,
    MIN_RETRY_SECONDS,
    RETRY_STAGE_SUFFIX,
    complete_parsed,
)
from research_assistant.runtime.settings import Settings


def parse_int(content: str) -> int:
    try:
        return int(json.loads(content)["value"])
    except (ValueError, KeyError, TypeError) as exc:
        raise GatewayError(f"not a valid payload: {exc.__class__.__name__}") from exc


def good(value: int = 7) -> str:
    return json.dumps({"value": value})


@pytest.fixture
def context(base_env: dict[str, str], tmp_workspace: Any, papers_root: Any):
    from research_assistant.runtime.context import load_task_context

    task = Task.parse(
        {
            "case_id": "A-01",
            "function": "Chat",
            "title": "t",
            "input": "i",
            "materials": [],
            "constraints": [],
            "expected_output": "e",
            "output_schema_ref": "Chat",
        }
    )
    return task, load_task_context(task, Settings.from_env(base_env), gateway=FakeGateway([]))


def test_first_attempt_success_makes_exactly_one_call(context) -> None:
    _, ctx = context
    fake = FakeGateway([good()])
    value, response = complete_parsed(
        fake, (), context=ctx, stage="s", requirement="r", parse=parse_int
    )
    assert value == 7
    assert response.finish_reason == "stop"
    assert [stage for stage, _ in fake.calls] == ["s"]


def test_one_retry_then_success(context) -> None:
    _, ctx = context
    fake = FakeGateway(["garbage", good(9)])
    value, _ = complete_parsed(fake, (), context=ctx, stage="s", requirement="r", parse=parse_int)
    assert value == 9
    assert [stage for stage, _ in fake.calls] == ["s", "s" + RETRY_STAGE_SUFFIX]


def test_retry_carries_the_previous_output_and_the_reason(context) -> None:
    _, ctx = context
    fake = FakeGateway(["garbage", good()])
    complete_parsed(
        fake, (), context=ctx, stage="s", requirement="必须是一个 JSON 对象", parse=parse_int
    )
    messages = fake.calls[1][1]
    assert messages[-2].role == "assistant"
    assert messages[-2].content == "garbage"
    assert messages[-1].role == "user"
    assert "必须是一个 JSON 对象" in messages[-1].content
    assert "not a valid payload" in messages[-1].content


def test_attempts_are_bounded(context) -> None:
    assert MAX_ATTEMPTS == 2
    _, ctx = context
    fake = FakeGateway(["a", "b", good()])
    with pytest.raises(GatewayError):
        complete_parsed(fake, (), context=ctx, stage="s", requirement="r", parse=parse_int)
    assert len(fake.calls) == MAX_ATTEMPTS


def test_retry_is_skipped_when_the_budget_is_short(
    base_env: dict[str, str], tmp_workspace, papers_root
) -> None:
    """预算不足时不重问：宁可如实报错，也不要挤掉后续阶段的时间。"""
    from research_assistant.runtime.context import load_task_context

    assert MIN_RETRY_SECONDS > 0
    env = {**base_env, "RESEARCH_CASE_BUDGET_SECONDS": str(MIN_RETRY_SECONDS + 1)}
    task = Task.parse(
        {
            "case_id": "A-01",
            "function": "Chat",
            "title": "t",
            "input": "i",
            "materials": [],
            "constraints": [],
            "expected_output": "e",
            "output_schema_ref": "Chat",
        }
    )
    fake = FakeGateway(["garbage", good()])
    ctx = load_task_context(task, Settings.from_env(env), gateway=fake)
    # 先耗掉预算，使剩余低于 MIN_RETRY_SECONDS
    object.__setattr__(ctx.deadline, "started_at", time.monotonic() - 5.0)
    with pytest.raises(GatewayError):
        complete_parsed(fake, (), context=ctx, stage="s", requirement="r", parse=parse_int)
    assert len(fake.calls) == 1


def test_transport_errors_are_not_retried_by_the_parser_path(context) -> None:
    """传输层错误由 gateway.complete 直接抛出，不进入解析重试路径。"""
    _, ctx = context
    fake = FakeGateway([GatewayError("upstream 502")])
    with pytest.raises(GatewayError, match="upstream 502"):
        complete_parsed(fake, (), context=ctx, stage="s", requirement="r", parse=parse_int)
    assert len(fake.calls) == 1
