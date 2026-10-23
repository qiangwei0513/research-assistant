from __future__ import annotations

import time
from dataclasses import FrozenInstanceError

import pytest

from research_assistant.errors import BudgetExhausted
from research_assistant.runtime.deadline import Deadline


def test_start_uses_monotonic_and_reports_full_budget() -> None:
    before = time.monotonic()
    deadline = Deadline.start(180.0)
    after = time.monotonic()
    assert before <= deadline.started_at <= after
    assert deadline.budget_seconds == 180.0
    assert 179.0 < deadline.remaining() <= 180.0


def test_remaining_decreases_as_time_passes() -> None:
    deadline = Deadline(started_at=time.monotonic() - 30.0, budget_seconds=180.0)
    assert deadline.remaining() == pytest.approx(150.0, abs=0.5)
    assert deadline.elapsed() == pytest.approx(30.0, abs=0.5)
    assert deadline.expired is False


def test_expired_when_budget_consumed() -> None:
    deadline = Deadline(started_at=time.monotonic() - 200.0, budget_seconds=180.0)
    assert deadline.expired is True
    assert deadline.remaining() < 0


def test_take_returns_remaining_when_sufficient() -> None:
    deadline = Deadline(started_at=time.monotonic(), budget_seconds=180.0)
    assert deadline.take("model.complete", minimum=10.0) == pytest.approx(180.0, abs=1.0)


def test_take_raises_when_remaining_below_minimum() -> None:
    deadline = Deadline(started_at=time.monotonic() - 175.0, budget_seconds=180.0)
    with pytest.raises(BudgetExhausted, match=r"model\.complete"):
        deadline.take("model.complete", minimum=10.0)


def test_take_raises_when_already_expired() -> None:
    deadline = Deadline(started_at=time.monotonic() - 181.0, budget_seconds=180.0)
    with pytest.raises(BudgetExhausted):
        deadline.take("persist", minimum=0.1)


def test_take_error_message_contains_stage_and_numbers_not_secrets() -> None:
    deadline = Deadline(started_at=time.monotonic() - 179.0, budget_seconds=180.0)
    with pytest.raises(BudgetExhausted) as excinfo:
        deadline.take("d.generate_source", minimum=20.0)
    message = str(excinfo.value)
    assert "d.generate_source" in message
    assert excinfo.value.case_id is None


def test_take_propagates_case_id() -> None:
    deadline = Deadline(started_at=time.monotonic() - 179.0, budget_seconds=180.0)
    with pytest.raises(BudgetExhausted) as excinfo:
        deadline.take("stage", minimum=20.0, case_id="D-05")
    assert excinfo.value.case_id == "D-05"


def test_guard_passes_when_budget_remains() -> None:
    deadline = Deadline.start(180.0)
    with deadline.guard("fast.stage"):
        pass


def test_guard_raises_when_stage_overruns() -> None:
    deadline = Deadline(started_at=time.monotonic() - 179.9, budget_seconds=180.0)
    # 合并 with：guard 先退出并抛出，pytest.raises 后退出并捕获，与嵌套写法语义一致
    with pytest.raises(BudgetExhausted, match=r"slow\.stage"), deadline.guard("slow.stage"):
        time.sleep(0.2)


def test_deadline_is_frozen() -> None:
    deadline = Deadline.start(10.0)
    with pytest.raises(FrozenInstanceError):
        deadline.budget_seconds = 5.0  # type: ignore[misc]
