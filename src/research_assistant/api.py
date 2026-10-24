"""开发用 FastAPI 应用：GET /health 与 POST /solve。

交付镜像不以本模块为入口——它由 `agenthack.AgentRuntime(...).run()` 启动，
并在官方 app 上替换 `/solve` 以适配赛题一八字段协议（见 `official_runtime.py`）。
本模块保留给本地开发、单测与没有 agenthack 的环境；两条路径共用 `http_solve`，
协议实现只有一份。官方运行时的 /health 由 SDK 提供（含 telemetry_configured）。
"""

from __future__ import annotations

import contextlib
import json
import os
from contextlib import asynccontextmanager
from uuid import uuid4

import anyio.to_thread
from fastapi import FastAPI, Request
from fastapi.responses import Response

from research_assistant import __version__
from research_assistant.contracts.dispatch import RULES_VERSION_V3
from research_assistant.runtime.gateway import Gateway
from research_assistant.runtime.http_solve import JSON_MEDIA_TYPE, handle_solve
from research_assistant.runtime.logging import JsonLogger
from research_assistant.runtime.settings import Settings
from research_assistant.runtime.telemetry import (
    flush_telemetry,
    install_interceptors,
    shutdown_telemetry,
    telemetry_configured,
)

HEALTH_PROBE_NAME = ".health-probe"


def build_settings() -> Settings:
    return Settings.from_env(os.environ)


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
        return await handle_solve(request, settings=resolved, gateway=gateway, log=log)

    return app
