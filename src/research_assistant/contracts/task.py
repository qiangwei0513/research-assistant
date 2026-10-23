"""八字段任务合同。

Task 是纯值对象，不触文件系统：材料是否存在由 runtime/context.load_task_context 校验。
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from research_assistant.errors import TaskContractError


class TaskFunction(StrEnum):
    CHAT = "Chat"
    LITERATURE_RETRIEVAL = "Literature_Retrieval"
    SURVEY = "Survey"
    REPRODUCTION = "Reproduction"
    IDEA = "Idea"


SCHEMA_REF_BY_FUNCTION: Mapping[TaskFunction, str] = MappingProxyType(
    {member: member.value for member in TaskFunction}
)

# 规范允许 A-01 至 A-100，不得假定只有两位后缀
CASE_ID_PATTERN = re.compile(r"^[A-E]-\d{1,3}$")

STRING_FIELDS: tuple[str, ...] = (
    "case_id",
    "function",
    "title",
    "input",
    "expected_output",
    "output_schema_ref",
)
LIST_FIELDS: tuple[str, ...] = ("materials", "constraints")
KNOWN_FIELDS: frozenset[str] = frozenset(STRING_FIELDS + LIST_FIELDS)


class FrozenMapping(Mapping[str, Any]):
    """只读且可哈希的 Mapping，用于 Task.unknown_fields。

    不用 MappingProxyType：它只读但不可哈希，会让 frozen dataclass Task 无法 hash()。
    collections.abc.Mapping 默认 __hash__ = None，故此处显式实现。
    """

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, Any]) -> None:
        self._data = dict(data)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __hash__(self) -> int:
        return hash(frozenset(self._data.items()))

    def __repr__(self) -> str:
        return f"FrozenMapping({self._data!r})"


@dataclass(frozen=True)
class Task:
    case_id: str
    function: TaskFunction
    title: str
    input: str
    materials: tuple[str, ...]
    constraints: tuple[str, ...]
    expected_output: str
    output_schema_ref: str
    unknown_fields: Mapping[str, Any]

    @classmethod
    def parse(cls, payload: Mapping[str, Any]) -> Task:
        if not isinstance(payload, Mapping):
            raise TaskContractError("request body must be a JSON object")

        for field in STRING_FIELDS + LIST_FIELDS:
            if field not in payload:
                raise TaskContractError(f"missing required field: {field}")

        strings: dict[str, str] = {}
        for field in STRING_FIELDS:
            value = payload[field]
            if not isinstance(value, str):
                raise TaskContractError(f"field {field} must be a string")
            strings[field] = value

        lists: dict[str, tuple[str, ...]] = {}
        for field in LIST_FIELDS:
            value = payload[field]
            if not isinstance(value, list):
                raise TaskContractError(f"field {field} must be an array (empty array when unused)")
            for index, item in enumerate(value):
                if not isinstance(item, str):
                    raise TaskContractError(f"field {field}[{index}] must be a string")
            lists[field] = tuple(value)

        case_id = strings["case_id"]
        if not CASE_ID_PATTERN.fullmatch(case_id):
            raise TaskContractError(
                f"case_id {case_id!r} does not match the required <A-E>-<1..3 digits> form"
            )

        try:
            function = TaskFunction(strings["function"])
        except ValueError:
            allowed = ", ".join(member.value for member in TaskFunction)
            raise TaskContractError(
                f"function {strings['function']!r} is not one of: {allowed}"
            ) from None

        schema_ref = strings["output_schema_ref"]
        if SCHEMA_REF_BY_FUNCTION[function] != schema_ref:
            # 明确不猜路由
            raise TaskContractError(
                f"output_schema_ref {schema_ref!r} does not match function {function.value!r}"
            )

        unknown = {key: value for key, value in payload.items() if key not in KNOWN_FIELDS}

        return cls(
            case_id=case_id,
            function=function,
            title=strings["title"],
            input=strings["input"],
            materials=lists["materials"],
            constraints=lists["constraints"],
            expected_output=strings["expected_output"],
            output_schema_ref=schema_ref,
            unknown_fields=FrozenMapping(unknown),
        )
