"""function → handler 路由表。

B/C/D/E 在 P1 尚未实现，路由必须存在且报出所属阶段，而不是静默返回空壳。
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol

from research_assistant.contracts.task import Task, TaskFunction
from research_assistant.errors import HandlerNotImplemented
from research_assistant.handlers.chat import solve_chat

if TYPE_CHECKING:  # pragma: no cover
    from research_assistant.runtime.context import SolveContext


class Handler(Protocol):
    def __call__(self, task: Task, context: SolveContext) -> Mapping[str, Any]: ...


def _pending(function: TaskFunction, phase: str) -> Handler:
    def handler(task: Task, context: SolveContext) -> Mapping[str, Any]:
        raise HandlerNotImplemented(
            f"{function.value} is scheduled for {phase} and is not implemented in P1",
            case_id=task.case_id,
        )

    return handler


HANDLER_BY_FUNCTION: Mapping[TaskFunction, Handler] = MappingProxyType(
    {
        TaskFunction.CHAT: solve_chat,
        TaskFunction.LITERATURE_RETRIEVAL: _pending(TaskFunction.LITERATURE_RETRIEVAL, "P2"),
        TaskFunction.SURVEY: _pending(TaskFunction.SURVEY, "P3"),
        TaskFunction.IDEA: _pending(TaskFunction.IDEA, "P3"),
        TaskFunction.REPRODUCTION: _pending(TaskFunction.REPRODUCTION, "P4"),
    }
)
