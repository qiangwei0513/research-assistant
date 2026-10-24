"""python -m research_assistant：优先用官方 AgentRuntime 启动，监听 0.0.0.0:8080。

基础镜像的默认 CMD 故意打印错误并 exit 1，交付镜像必须覆盖它。
本地没有 agenthack 时退到开发用 FastAPI（协议实现相同），镜像里始终走官方运行时。
"""

from __future__ import annotations

import os

import uvicorn

from research_assistant.api import create_app
from research_assistant.runtime.logging import JsonLogger

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080


def main() -> None:
    host = os.environ.get("RESEARCH_HOST", DEFAULT_HOST)
    port = int(os.environ.get("RESEARCH_PORT", DEFAULT_PORT))
    try:
        from research_assistant.runtime.official_runtime import run_official
    except ImportError as exc:
        JsonLogger("api").warning(
            "service.official_runtime_unavailable", reason=exc.__class__.__name__
        )
        uvicorn.run(create_app(), host=host, port=port, log_level="warning")
        return
    run_official(host=host, port=port)


if __name__ == "__main__":
    main()
