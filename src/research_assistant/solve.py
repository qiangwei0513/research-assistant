"""单题编排：路由 → handler → 形状校验 → 落盘回读。

不遍历整轮，不生成官方评分，不实现自己的裁判。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from research_assistant.contracts.results import serialize_result, validate_result_shape
from research_assistant.contracts.task import Task
from research_assistant.errors import PersistenceError
from research_assistant.handlers.registry import HANDLER_BY_FUNCTION
from research_assistant.runtime.context import SolveContext
from research_assistant.runtime.persistence import PersistedResult, write_bytes_verified


def solve(task: Task, context: SolveContext) -> Mapping[str, Any] | PersistedResult:
    handler = HANDLER_BY_FUNCTION[task.function]
    result = handler(task, context)
    payload = result.payload if isinstance(result, PersistedResult) else result
    validate_result_shape(task.function, payload)
    return result


def persist_and_verify(result: Mapping[str, Any] | PersistedResult, context: SolveContext) -> bytes:
    """落盘当前题结果并回读校验；返回的字节必须与 HTTP 响应体完全一致。"""
    if isinstance(result, PersistedResult):
        actual = result.path.read_bytes()
        if actual != result.data:
            raise PersistenceError(
                f"trusted result file changed after the experiment wrote it: {result.path.name}"
            )
        context.log.info(
            "result.preserved",
            path=context.workspace.relative_to_output(result.path),
            bytes=len(actual),
        )
        return actual
    data = serialize_result(result)
    path = context.workspace.result_json(context.case_id)
    write_bytes_verified(path, data)
    context.log.info(
        "result.persisted", path=context.workspace.relative_to_output(path), bytes=len(data)
    )
    return data
