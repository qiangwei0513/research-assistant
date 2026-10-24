"""限时解析子进程入口；只接收父进程已经授权的单篇路径，不加载服务或网关。"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

from research_assistant.documents.extractor import extract_one


def main() -> None:
    document = extract_one(Path(sys.argv[1]), filename=sys.argv[2])
    # 这里只序列化 Document；绝不读取/序列化 Settings 或 GatewaySettings。
    payload = asdict(document)
    payload["path"] = str(document.path)
    print(json.dumps(payload, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
