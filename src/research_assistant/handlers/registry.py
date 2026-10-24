"""function → handler 路由表。

未实现的类别路由必须存在且报出所属阶段，而不是静默返回空壳。
A（Chat）自 P1、B（Literature_Retrieval）自 P2、C（Survey）与 E（Idea）自 P3 起为真实实现。
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol

from research_assistant.contracts.task import Task, TaskFunction
from research_assistant.errors import HandlerNotImplemented
from research_assistant.handlers.chat import solve_chat
from research_assistant.handlers.idea import solve_idea
from research_assistant.handlers.retrieval import solve_retrieval
from research_assistant.handlers.survey import solve_survey

if TYPE_CHECKING:  # pragma: no cover
    from research_assistant.runtime.context import SolveContext


class Handler(Protocol):
    def __call__(self, task: Task, context: SolveContext) -> Mapping[str, Any]: ...


def _pending(function: TaskFunction, phase: str) -> Handler:
    def handler(task: Task, context: SolveContext) -> Mapping[str, Any]:
        raise HandlerNotImplemented(
            f"{function.value} is scheduled for {phase} and is not implemented yet",
            case_id=task.case_id,
        )

    return handler


HANDLER_BY_FUNCTION: Mapping[TaskFunction, Handler] = MappingProxyType(
    {
        TaskFunction.CHAT: solve_chat,
        TaskFunction.LITERATURE_RETRIEVAL: solve_retrieval,
        TaskFunction.SURVEY: solve_survey,
        TaskFunction.IDEA: solve_idea,
        TaskFunction.REPRODUCTION: _pending(TaskFunction.REPRODUCTION, "P4"),
    }
)
