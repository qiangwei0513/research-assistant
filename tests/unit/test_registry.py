from __future__ import annotations

from research_assistant.contracts.task import Task, TaskFunction
from research_assistant.errors import HandlerNotImplemented
from research_assistant.handlers.registry import HANDLER_BY_FUNCTION


def test_every_function_has_a_handler() -> None:
    assert set(HANDLER_BY_FUNCTION) == set(TaskFunction)


def test_every_category_routes_to_a_real_handler() -> None:
    """P4 之后五类都有真实实现，路由表里不应再有任何 501 桩。"""
    from research_assistant.handlers.chat import solve_chat
    from research_assistant.handlers.idea import solve_idea
    from research_assistant.handlers.reproduction import solve_reproduction
    from research_assistant.handlers.retrieval import solve_retrieval
    from research_assistant.handlers.survey import solve_survey

    assert {
        TaskFunction.CHAT: solve_chat,
        TaskFunction.LITERATURE_RETRIEVAL: solve_retrieval,
        TaskFunction.SURVEY: solve_survey,
        TaskFunction.IDEA: solve_idea,
        TaskFunction.REPRODUCTION: solve_reproduction,
    } == HANDLER_BY_FUNCTION


def test_no_handler_stub_remains_in_the_registry() -> None:
    for function, handler in HANDLER_BY_FUNCTION.items():
        task = Task.parse(
            {
                "case_id": f"{function.value and {'Chat': 'A', 'Literature_Retrieval': 'B', 'Survey': 'C', 'Reproduction': 'D', 'Idea': 'E'}[function.value]}-01",
                "function": function.value,
                "title": "t",
                "input": "i",
                "materials": [],
                "constraints": [],
                "expected_output": "e",
                "output_schema_ref": function.value,
            }
        )
        # 桩会立刻抛 HandlerNotImplemented；真实 handler 会因缺少上下文/材料抛别的错
        try:
            handler(task, None)  # type: ignore[arg-type]
        except HandlerNotImplemented as exc:
            raise AssertionError(f"{function.value} still routes to a stub") from exc
        except Exception:
            pass
