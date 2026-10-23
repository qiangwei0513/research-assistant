from __future__ import annotations

import pytest

from research_assistant.contracts.task import Task, TaskFunction
from research_assistant.errors import HandlerNotImplemented
from research_assistant.handlers.registry import HANDLER_BY_FUNCTION


def test_every_function_has_a_handler() -> None:
    assert set(HANDLER_BY_FUNCTION) == set(TaskFunction)


def test_unimplemented_categories_raise_with_their_phase() -> None:
    for case_id, function, phase in [
        ("B-01", TaskFunction.LITERATURE_RETRIEVAL, "P2"),
        ("C-01", TaskFunction.SURVEY, "P3"),
        ("E-01", TaskFunction.IDEA, "P3"),
        ("D-01", TaskFunction.REPRODUCTION, "P4"),
    ]:
        handler = HANDLER_BY_FUNCTION[function]
        task = Task.parse(
            {
                "case_id": case_id,
                "function": function.value,
                "title": "t",
                "input": "i",
                "materials": [],
                "constraints": [],
                "expected_output": "e",
                "output_schema_ref": function.value,
            }
        )
        with pytest.raises(HandlerNotImplemented, match=phase) as excinfo:
            handler(task, None)  # type: ignore[arg-type]
        assert excinfo.value.http_status == 501
        assert excinfo.value.case_id == case_id


def test_chat_is_wired_to_the_real_handler() -> None:
    from research_assistant.handlers.chat import solve_chat

    assert HANDLER_BY_FUNCTION[TaskFunction.CHAT] is solve_chat
