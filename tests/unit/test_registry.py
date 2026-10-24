from __future__ import annotations

import pytest

from research_assistant.contracts.task import Task, TaskFunction
from research_assistant.errors import HandlerNotImplemented
from research_assistant.handlers.registry import HANDLER_BY_FUNCTION


def test_every_function_has_a_handler() -> None:
    assert set(HANDLER_BY_FUNCTION) == set(TaskFunction)


def test_unimplemented_categories_raise_with_their_phase() -> None:
    for case_id, function, phase in [
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


def test_retrieval_is_wired_to_the_real_handler() -> None:
    from research_assistant.handlers.retrieval import solve_retrieval

    assert HANDLER_BY_FUNCTION[TaskFunction.LITERATURE_RETRIEVAL] is solve_retrieval


def test_survey_and_idea_are_wired_to_real_handlers() -> None:
    from research_assistant.handlers.idea import solve_idea
    from research_assistant.handlers.survey import solve_survey

    assert HANDLER_BY_FUNCTION[TaskFunction.SURVEY] is solve_survey
    assert HANDLER_BY_FUNCTION[TaskFunction.IDEA] is solve_idea
