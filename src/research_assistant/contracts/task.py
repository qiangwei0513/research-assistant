"""八字段任务合同。

Task 是纯值对象，不触文件系统：材料是否存在由 runtime/context.load_task_context 校验。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
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

# 刻意宽松：放行 A-0/A-101/A-999；对照派题记录的精确题号校验是后续任务的职责，勿在此收紧
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
    unknown_fields: Mapping[str, Any] = field(hash=False)

    @classmethod
    def parse(cls, payload: Mapping[str, Any]) -> Task:
        if not isinstance(payload, Mapping):
            raise TaskContractError("request body must be a JSON object")

        # 循环变量不用 field，避免遮蔽 dataclasses.field（ruff F402）
        for name in STRING_FIELDS + LIST_FIELDS:
            if name not in payload:
                raise TaskContractError(f"missing required field: {name}")

        strings: dict[str, str] = {}
        for name in STRING_FIELDS:
            value = payload[name]
            if not isinstance(value, str):
                raise TaskContractError(f"field {name} must be a string")
            strings[name] = value

        lists: dict[str, tuple[str, ...]] = {}
        for name in LIST_FIELDS:
            value = payload[name]
            if not isinstance(value, list):
                raise TaskContractError(f"field {name} must be an array (empty array when unused)")
            for index, item in enumerate(value):
                if not isinstance(item, str):
                    raise TaskContractError(f"field {name}[{index}] must be a string")
            lists[name] = tuple(value)

        case_id = strings["case_id"]
        if not CASE_ID_PATTERN.fullmatch(case_id):
            raise TaskContractError(
                f"case_id {repr(case_id)[:64]} does not match the required <A-E>-<1..3 digits> form"
            )

        try:
            function = TaskFunction(strings["function"])
        except ValueError:
            allowed = ", ".join(member.value for member in TaskFunction)
            raise TaskContractError(
                f"function {repr(strings['function'])[:64]} is not one of: {allowed}"
            ) from None

        schema_ref = strings["output_schema_ref"]
        if SCHEMA_REF_BY_FUNCTION[function] != schema_ref:
            # 明确不猜路由
            raise TaskContractError(
                f"output_schema_ref {repr(schema_ref)[:64]} does not match function"
                f" {function.value!r}"
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
            unknown_fields=MappingProxyType(unknown),
        )
