from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from research_assistant.contracts.task import SCHEMA_REF_BY_FUNCTION, Task, TaskFunction
from research_assistant.errors import TaskContractError


def payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "case_id": "A-01",
        "function": "Chat",
        "title": "光照与生长",
        "input": "解释关系并提一个限定问题。",
        "materials": [],
        "constraints": [],
        "expected_output": "解释关系并提问。",
        "output_schema_ref": "Chat",
    }
    base.update(overrides)
    return base


def test_parse_accepts_a_valid_chat_task() -> None:
    task = Task.parse(payload())
    assert task.case_id == "A-01"
    assert task.function is TaskFunction.CHAT
    assert task.materials == ()
    assert task.constraints == ()
    assert task.unknown_fields == {}


def test_parse_preserves_materials_and_constraints_as_ordered_tuples() -> None:
    task = Task.parse(
        payload(materials=["a b.pdf", "中文+报告.pdf"], constraints=["不超过 3 篇", "只用正文证据"])
    )
    assert task.materials == ("a b.pdf", "中文+报告.pdf")
    assert task.constraints == ("不超过 3 篇", "只用正文证据")


@pytest.mark.parametrize(
    "field",
    [
        "case_id",
        "function",
        "title",
        "input",
        "materials",
        "constraints",
        "expected_output",
        "output_schema_ref",
    ],
)
def test_missing_field_is_rejected(field: str) -> None:
    data = payload()
    del data[field]
    with pytest.raises(TaskContractError, match=field):
        Task.parse(data)


@pytest.mark.parametrize(
    "field", ["case_id", "title", "input", "expected_output", "output_schema_ref", "function"]
)
def test_non_string_scalar_field_is_rejected(field: str) -> None:
    with pytest.raises(TaskContractError, match=field):
        Task.parse(payload(**{field: 123}))


@pytest.mark.parametrize("field", ["materials", "constraints"])
def test_non_list_material_field_is_rejected(field: str) -> None:
    with pytest.raises(TaskContractError, match=field):
        Task.parse(payload(**{field: "a.pdf"}))


@pytest.mark.parametrize("field", ["materials", "constraints"])
def test_null_material_field_is_rejected(field: str) -> None:
    """无材料时必须是空数组，不是 null。"""
    with pytest.raises(TaskContractError, match=field):
        Task.parse(payload(**{field: None}))


def test_materials_must_contain_only_strings() -> None:
    with pytest.raises(TaskContractError, match="materials"):
        Task.parse(payload(materials=["ok.pdf", 7]))


@pytest.mark.parametrize("case_id", ["A-01", "A-1", "A-100", "B-03", "C-01", "D-05", "E-02"])
def test_valid_case_ids_are_accepted(case_id: str) -> None:
    function = {
        "A": "Chat",
        "B": "Literature_Retrieval",
        "C": "Survey",
        "D": "Reproduction",
        "E": "Idea",
    }[case_id[0]]
    task = Task.parse(payload(case_id=case_id, function=function, output_schema_ref=function))
    assert task.case_id == case_id


@pytest.mark.parametrize("case_id", ["", "A-", "A-1000", "a-01", "F-01", "A_01", "A-01 ", "01"])
def test_invalid_case_ids_are_rejected(case_id: str) -> None:
    with pytest.raises(TaskContractError, match="case_id"):
        Task.parse(payload(case_id=case_id))


def test_unknown_function_is_rejected() -> None:
    with pytest.raises(TaskContractError, match="function"):
        Task.parse(payload(function="Summarize"))


def test_function_and_schema_ref_mismatch_is_rejected_without_guessing() -> None:
    """不得猜测路由：function=Survey 而 output_schema_ref=Chat 必须直接报错。"""
    with pytest.raises(TaskContractError, match="output_schema_ref"):
        Task.parse(payload(function="Survey", output_schema_ref="Chat"))


@pytest.mark.parametrize("function", list(SCHEMA_REF_BY_FUNCTION.values()))
def test_every_function_pairs_with_its_own_schema_ref(function: str) -> None:
    case_id = {
        "Chat": "A-01",
        "Literature_Retrieval": "B-01",
        "Survey": "C-01",
        "Reproduction": "D-01",
        "Idea": "E-01",
    }[function]
    task = Task.parse(payload(case_id=case_id, function=function, output_schema_ref=function))
    assert task.function.value == function


def test_schema_ref_by_function_covers_all_five_types() -> None:
    assert set(SCHEMA_REF_BY_FUNCTION) == set(TaskFunction)
    assert SCHEMA_REF_BY_FUNCTION[TaskFunction.LITERATURE_RETRIEVAL] == "Literature_Retrieval"


def test_unrecognized_extension_fields_are_recorded_not_dropped_or_forwarded() -> None:
    data = payload()
    data["future_field"] = {"nested": 1}
    data["another"] = "x"
    task = Task.parse(data)
    assert task.unknown_fields == {"future_field": {"nested": 1}, "another": "x"}


def test_unknown_fields_mapping_is_immutable() -> None:
    task = Task.parse(payload(extra=1))
    with pytest.raises(TypeError):
        task.unknown_fields["extra"] = 2  # type: ignore[index]


def test_parse_does_not_mutate_the_incoming_payload() -> None:
    data = payload(materials=["a.pdf"])
    snapshot = copy.deepcopy(data)
    Task.parse(data)
    assert data == snapshot


def test_task_is_frozen() -> None:
    task = Task.parse(payload())
    with pytest.raises(FrozenInstanceError):
        task.case_id = "B-01"  # type: ignore[misc]


def test_task_is_hashable_even_with_nested_unknown_fields() -> None:
    """unknown_fields 常含 dict/list 值；哈希不得因此抛 TypeError。"""
    data = payload()
    data["future_field"] = {"nested": 1}
    data["tags"] = ["a", "b"]
    task = Task.parse(data)
    assert isinstance(hash(task), int)
    assert len({task, Task.parse(data), Task.parse(payload())}) == 2
