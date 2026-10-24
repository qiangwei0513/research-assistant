"""POST /solve 的协议适配与错误映射，供开发应用与官方运行时共用。

平台发送的是八字段题目 JSON，响应必须是该类结果的直接对象；这套协议与 SDK 通用
`TaskRequest/TaskResponse` 不兼容，因此路由层必须适配。开发环境用自建 FastAPI，
交付镜像由 `agenthack.AgentRuntime` 启动并在其 app 上替换 `/solve` 路由，
两条路径都走本模块，避免协议实现分叉。
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from functools import partial
from typing import Any

import anyio.to_thread
from fastapi import Request
from fastapi.responses import JSONResponse, Response

from research_assistant.contracts.task import Task
from research_assistant.errors import ResearchAssistantError, TaskContractError, error_body
from research_assistant.runtime.context import SolveContext, load_task_context
from research_assistant.runtime.gateway import Gateway
from research_assistant.runtime.logging import JsonLogger
from research_assistant.runtime.settings import Settings
from research_assistant.runtime.telemetry import flush_telemetry, task_span, trace_session
from research_assistant.solve import persist_and_verify, solve

JSON_MEDIA_TYPE = "application/json; charset=utf-8"
SESSION_HEADER = "x-session-id"
TRANSPORT_TASK_HEADER = "x-research-task-id"
TRACE_CONTEXT_HEADERS = frozenset({"traceparent", "tracestate"})


def error_response(exc: ResearchAssistantError, log: JsonLogger) -> JSONResponse:
    log.error(
        "solve.failed",
        error_type=type(exc).__name__,
        case_id=exc.case_id,
        http_status=exc.http_status,
        detail=exc.message,
    )
    return JSONResponse(status_code=exc.http_status, content=error_body(exc))


def unexpected_response(exc: Exception, case_id: str, log: JsonLogger) -> JSONResponse:
    # 只回异常类名，不回消息：未预期异常的消息可能含路径、负载片段等内部细节
    log.error("solve.unexpected", error_type=exc.__class__.__name__, case_id=case_id)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "type": "UnexpectedError",
                "message": exc.__class__.__name__,
                "case_id": case_id,
            }
        },
    )


def _solve_in_thread(task: Task, context: SolveContext) -> bytes:
    return persist_and_verify(solve(task, context), context)


@contextmanager
def trace_current_task(
    task: Task, headers: Mapping[str, str], session_id: str | None
) -> Iterator[Any]:
    with (
        trace_session(task.case_id, task.function.value, headers, session_id=session_id),
        task_span() as operation,
    ):
        yield operation


def _trace_headers(request: Request) -> dict[str, str]:
    return {k: v for k, v in request.headers.items() if k.lower() in TRACE_CONTEXT_HEADERS}


def _transport_session_id(request: Request) -> str | None:
    """传输任务 ID 是平台为本次派发生成的稳定 UUID，用它关联 trace 最可靠。

    `X-Session-ID` 是模型调用的会话标识，二者不能混为一谈；只有在没有传输 ID
    时才退到它，避免 trace 完全没有会话标识。
    """
    return request.headers.get(TRANSPORT_TASK_HEADER) or request.headers.get(SESSION_HEADER)


async def handle_solve(
    request: Request,
    *,
    settings: Settings,
    gateway: Gateway | None,
    log: JsonLogger,
) -> Response:
    """只解当前题：绝不遍历整轮 11 题。"""
    try:
        raw = await request.body()
    except Exception as exc:
        # 读体失败也必须回合同规定的错误形状，而不是让框架吐默认 500
        return error_response(
            TaskContractError(f"cannot read request body: {exc.__class__.__name__}"), log
        )
    try:
        payload: Mapping[str, Any] = json.loads(raw)
    except ValueError as exc:
        return error_response(TaskContractError(f"request body is not valid JSON: {exc}"), log)
    if not isinstance(payload, Mapping):
        return error_response(TaskContractError("request body must be a JSON object"), log)

    try:
        task = Task.parse(payload)
    except ResearchAssistantError as exc:
        return error_response(exc, log)

    # 只透传 W3C trace context，避免把 Authorization 等敏感头带进 trace
    try:
        with trace_current_task(
            task, _trace_headers(request), _transport_session_id(request)
        ) as operation:
            context = await anyio.to_thread.run_sync(
                partial(load_task_context, task, settings, gateway=gateway)
            )
            data = await anyio.to_thread.run_sync(_solve_in_thread, task, context)
            if operation is not None:
                # 与官方 AgentRuntime 一致：在 task span 结束前记录产物大小
                operation.result(data)
    except ResearchAssistantError as exc:
        return error_response(exc, log)
    except Exception as exc:
        return unexpected_response(exc, task.case_id, log)
    finally:
        # 必须等 session span 也结束再 flush，否则导出的 trace 缺顶层 session
        flush_telemetry()
    # 响应体直接复用落盘字节，保证与文件逐字节一致
    return Response(content=data, status_code=200, media_type=JSON_MEDIA_TYPE)
