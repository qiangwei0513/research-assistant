"""交付镜像入口：以 agenthack.AgentRuntime 启动，并适配赛题一协议。

组委会要求镜像必须由官方运行时启动（`AgentRuntime(MyAgent()).run()`）。官方
`/solve` 使用通用 `TaskRequest/TaskResponse`（`input` 是对象、`constraints` 是对象），
与赛题一八字段报文和「直接返回题型对象」都冲突；而组委会清单明确要求参赛者在
自有入口内适配协议。因此这里保留官方 app、`/health`、lifespan 与 Telemetry，
只替换 `/solve` 路由；session → task → llm 三层埋点仍由 SDK 负责。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import Response

from research_assistant import __version__
from research_assistant.api import build_settings
from research_assistant.contracts.dispatch import RULES_VERSION_V3
from research_assistant.runtime.gateway import Gateway
from research_assistant.runtime.http_solve import handle_solve
from research_assistant.runtime.logging import JsonLogger
from research_assistant.runtime.settings import Settings
from research_assistant.runtime.telemetry import use_instance


def make_agent() -> Any:
    from agenthack import BaseAgent

    class ResearchAgent(BaseAgent):
        async def solve(self, task_input: dict[str, Any]) -> dict[str, Any]:
            # 官方通用路由已被替换；这个实现只为满足 BaseAgent 的抽象方法
            raise RuntimeError("research assistant must use the adapted /solve route")

    return ResearchAgent()


def _replace_solve_route(app: FastAPI, endpoint: Any) -> None:
    """删掉 SDK 的通用 /solve 再注册赛题一协议路由。

    FastAPI 按注册顺序匹配，不能只追加：SDK 的路由在前，新路由永远不会命中。
    """
    app.router.routes = [r for r in app.router.routes if getattr(r, "path", None) != "/solve"]
    app.add_api_route("/solve", endpoint, methods=["POST"], response_class=Response)


def configure_runtime(
    runtime: Any, settings: Settings | None = None, *, gateway: Gateway | None = None
) -> Settings:
    """在官方 runtime 上接入赛题一 /solve，并复用它的 Telemetry。"""
    resolved = settings if settings is not None else build_settings()
    log = JsonLogger("api", resolved.log_level)
    # 复用官方实例：再建一个 Telemetry 会导致重复 provider 与不同 session 上下文
    use_instance(runtime.telemetry)

    async def solve_endpoint(request: Request) -> Response:
        return await handle_solve(request, settings=resolved, gateway=gateway, log=log)

    _replace_solve_route(runtime.app, solve_endpoint)
    log.info(
        "service.starting",
        mode="agent_runtime",
        version=__version__,
        rules_version=RULES_VERSION_V3,
        gateway_configured=resolved.gateway is not None,
        gateway_family=resolved.gateway.family if resolved.gateway else None,
        gateway_warning=resolved.gateway_config_warning,
        telemetry_configured=getattr(runtime.telemetry, "export_enabled", False),
    )
    return resolved


def build_official_app(
    settings: Settings | None = None, *, gateway: Gateway | None = None
) -> tuple[Any, Settings]:
    from agenthack import AgentRuntime

    runtime = AgentRuntime(make_agent())
    resolved = configure_runtime(runtime, settings, gateway=gateway)
    return runtime, resolved


def run_official(host: str, port: int) -> None:
    runtime, _ = build_official_app()
    runtime.run(host=host, port=port)
