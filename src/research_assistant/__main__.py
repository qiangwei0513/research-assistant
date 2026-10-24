"""python -m research_assistant：启动 uvicorn，监听 0.0.0.0:8080。

基础镜像的默认 CMD 故意打印错误并 exit 1，交付镜像必须覆盖它。
"""

from __future__ import annotations

import os

import uvicorn

from research_assistant.api import create_app

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080


def main() -> None:
    host = os.environ.get("RESEARCH_HOST", DEFAULT_HOST)
    port = int(os.environ.get("RESEARCH_PORT", DEFAULT_PORT))
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
