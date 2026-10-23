"""单题编排：路由 → handler → 形状校验 → 落盘回读。

不遍历整轮，不生成官方评分，不实现自己的裁判。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from research_assistant.contracts.results import serialize_result, validate_result_shape
from research_assistant.contracts.task import Task
from research_assistant.handlers.registry import HANDLER_BY_FUNCTION
from research_assistant.runtime.context import SolveContext
from research_assistant.runtime.persistence import write_bytes_verified


def solve(task: Task, context: SolveContext) -> Mapping[str, Any]:
    handler = HANDLER_BY_FUNCTION[task.function]
    result = handler(task, context)
    validate_result_shape(task.function, result)
    return result


def persist_and_verify(result: Mapping[str, Any], context: SolveContext) -> bytes:
    """落盘当前题结果并回读校验；返回的字节必须与 HTTP 响应体完全一致。"""
    data = serialize_result(result)
    path = context.workspace.result_json(context.case_id)
    write_bytes_verified(path, data)
    context.log.info(
        "result.persisted", path=context.workspace.relative_to_output(path), bytes=len(data)
    )
    return data
