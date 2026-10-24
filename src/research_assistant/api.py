"""FastAPI 应用：GET /health 与 POST /solve。

不复用 agenthack.AgentRuntime：它自带的 /solve 是通用赛题合同（TaskRequest/TaskResponse 包装，
constraints 是对象），与赛题一的八字段输入（constraints 是字符串数组）和纯结果 JSON 冲突——
请求会被判 422，响应会多出 task_id/status/output/metadata 顶层字段。
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Mapping
from contextlib import asynccontextmanager
from functools import partial
from typing import Any
from uuid import uuid4

import anyio.to_thread
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from research_assistant import __version__
from research_assistant.contracts.dispatch import RULES_VERSION_V3
from research_assistant.contracts.task import Task
from research_assistant.errors import ResearchAssistantError, TaskContractError, error_body
from research_assistant.runtime.context import SolveContext, load_task_context
from research_assistant.runtime.gateway import Gateway
from research_assistant.runtime.logging import JsonLogger
from research_assistant.runtime.settings import Settings
from research_assistant.runtime.telemetry import (
    flush_telemetry,
    install_interceptors,
    shutdown_telemetry,
    telemetry_configured,
    trace_session,
)
from research_assistant.solve import persist_and_verify, solve

JSON_MEDIA_TYPE = "application/json; charset=utf-8"
HEALTH_PROBE_NAME = ".health-probe"
SESSION_HEADER = "x-session-id"


def build_settings() -> Settings:
    return Settings.from_env(os.environ)


def _error_response(exc: ResearchAssistantError, log: JsonLogger) -> JSONResponse:
    log.error(
        "solve.failed",
        error_type=type(exc).__name__,
        case_id=exc.case_id,
        http_status=exc.http_status,
        detail=exc.message,
    )
    return JSONResponse(status_code=exc.http_status, content=error_body(exc))


def _workspace_writable(settings: Settings) -> bool:
    """只检查输出根，不检查队伍目录。

    队伍标识未注入时仍报健康：否则平台一旦用了我们没预料到的注入方式，
    基础镜像的 healthcheck 会连续失败、整轮评测根本起不来，代价远大于单题报配置错误。

    探针文件名必须唯一：Docker 自身的 HEALTHCHECK 是串行的，但平台的就绪探针、
    负载均衡器或运维 curl 可能并发，固定文件名会让后一个探测在 unlink 时抛
    FileNotFoundError，被当成「不可写」而误报 503。
    """
    probe = settings.workspace_root / f"{HEALTH_PROBE_NAME}-{os.getpid()}-{uuid4().hex}"
    try:
        settings.workspace_root.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"")
    except OSError:
        return False
    finally:
        # 清不掉探针不影响可写性结论；它在 data/<team>/ 之外，不会污染结果
        with contextlib.suppress(OSError):
            probe.unlink(missing_ok=True)
    return True


def _dispatch_mounted(settings: Settings) -> bool:
    if not settings.team:
        return False
    return (settings.workspace_root / "data" / settings.team / "dispatch.json").is_file()


def _solve_in_thread(task: Task, context: SolveContext) -> bytes:
    return persist_and_verify(solve(task, context), context)


def _unexpected_response(exc: Exception, case_id: str, log: JsonLogger) -> JSONResponse:
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


def create_app(settings: Settings | None = None, *, gateway: Gateway | None = None) -> FastAPI:
    resolved = settings if settings is not None else build_settings()
    log = JsonLogger("api", resolved.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        installed = install_interceptors()
        log.info(
            "service.starting",
            version=__version__,
            rules_version=RULES_VERSION_V3,
            sdk_telemetry=installed,
            gateway_configured=resolved.gateway is not None,
            gateway_family=resolved.gateway.family if resolved.gateway else None,
        )
        yield
        flush_telemetry()
        shutdown_telemetry()

    app = FastAPI(title="Research Assistant Agent", version=__version__, lifespan=lifespan)

    @app.get("/health")
    async def health() -> Response:
        writable = await anyio.to_thread.run_sync(_workspace_writable, resolved)
        mounted = await anyio.to_thread.run_sync(_dispatch_mounted, resolved)
        body = {
            "status": "ok" if writable else "unavailable",
            "version": __version__,
            "rules_version": RULES_VERSION_V3,
            "workspace_writable": writable,
            "workspace_root": str(resolved.workspace_root),
            "team_configured": bool(resolved.team),
            "gateway_configured": resolved.gateway is not None,
            # 平台镜像检查要求该字段：Telemetry.export_enabled，即平台注入的 OTLP 配置是否生效
            "telemetry_configured": telemetry_configured(),
            "dispatch_mounted": mounted,
        }
        return Response(
            content=json.dumps(body, ensure_ascii=False),
            status_code=200 if writable else 503,
            media_type=JSON_MEDIA_TYPE,
        )

    @app.post("/solve")
    async def solve_endpoint(request: Request) -> Response:
        """只解当前题：绝不遍历整轮 11 题。"""
        try:
            raw = await request.body()
        except Exception as exc:
            # 读体失败也必须回合同规定的错误形状，而不是让框架吐默认 500
            return _error_response(
                TaskContractError(f"cannot read request body: {exc.__class__.__name__}"), log
            )
        try:
            payload: Mapping[str, Any] = json.loads(raw)
        except ValueError as exc:
            return _error_response(TaskContractError(f"request body is not valid JSON: {exc}"), log)
        if not isinstance(payload, Mapping):
            return _error_response(TaskContractError("request body must be a JSON object"), log)

        try:
            task = Task.parse(payload)
        except ResearchAssistantError as exc:
            return _error_response(exc, log)

        # 只透传会话头给遥测，避免把 Authorization 等敏感头带进 trace
        headers = {k: v for k, v in request.headers.items() if k.lower() == SESSION_HEADER}
        try:
            with trace_session(task.case_id, task.function.value, headers):
                context = await anyio.to_thread.run_sync(
                    partial(load_task_context, task, resolved, gateway=gateway)
                )
                data = await anyio.to_thread.run_sync(_solve_in_thread, task, context)
                flush_telemetry()
        except ResearchAssistantError as exc:
            return _error_response(exc, log)
        except Exception as exc:
            # 未预期异常不得泄漏内部细节：只回类名，不回消息
            return _unexpected_response(exc, task.case_id, log)
        # 响应体直接复用落盘字节，保证与文件逐字节一致
        return Response(content=data, status_code=200, media_type=JSON_MEDIA_TYPE)

    return app
