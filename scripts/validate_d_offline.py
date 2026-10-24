"""开发期离线验证 D 类：真实网关生成源码 → 隔离容器跑官方公开样例请求。

这不是平台来源准入，也不是可信执行：只证明「我们生成的源码在官方样例上能跑通、
输出结构符合 protocol」，即验收 D03/D04 的本地部分。

用法：
    uv run python scripts/validate_d_offline.py --case D-01

前置：.env.local 里有可用网关凭据；Docker 可用且已 pull --platform linux/amd64 python:3.9-slim；
官方材料在同级 ../赛题信息/research-experiments-20261020-source-v1/。

安全：生成源码只在隔离容器内执行（--network none --read-only --cap-drop ALL、非 root、
1 CPU / 1024 MiB / 20 秒 CPU 上限），绝不在开发宿主直接 exec。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from research_assistant.handlers.reproduction import (  # noqa: E402
    _generate_source,  # 开发脚本复用生产路径，避免另写一套生成逻辑
)
from research_assistant.reproduction.materials import load_case_materials  # noqa: E402
from research_assistant.runtime.deadline import Deadline  # noqa: E402
from research_assistant.runtime.gateway import HttpGateway  # noqa: E402
from research_assistant.runtime.logging import JsonLogger  # noqa: E402
from research_assistant.runtime.settings import Settings  # noqa: E402

MATERIAL_ROOT = REPO_ROOT.parent / "赛题信息" / "research-experiments-20261020-source-v1"
IMAGE = "python:3.9-slim"
CPU_SECONDS = 20

# 与官方一致的三项指标写法；正式评测时这些由本题 input 下发
TASK_INPUT_BY_CASE = {
    "D-01": (
        "复现 TF-IDF 与 softmax 分类实验。核心指标恰好三项："
        "name=Macro-F1，unit=分数；name=Accuracy，unit=%；name=运行时间，unit=秒。"
    ),
    "D-02": (
        "复现标题/正文 BM25 与稳健选参实验。核心指标恰好三项："
        "name=Recall@5，unit=%；name=MRR@10，unit=分数；name=查询吞吐，unit=queries/s。"
    ),
    "D-05": (
        "复现 FIFO 与加权公平调度实验。核心指标恰好三项："
        "name=公平性 Jain 指数，unit=分数；name=归一化吞吐，unit=%；"
        "name=平均等待时间，unit=ms。"
    ),
}


@dataclass
class DevContext:
    """只实现 _generate_source 真正用到的三个属性，避免在脚本里搭一整套工作区。"""

    case_id: str
    deadline: Deadline
    log: JsonLogger
    gateway: HttpGateway
    work_dir: Path


def load_env() -> dict[str, str]:
    env_file = REPO_ROOT / ".env.local"
    if not env_file.is_file():
        raise SystemExit("缺少 .env.local；本脚本需要真实网关凭据")
    env: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        env[key.strip()] = value.strip()
    return env


def sample_requests(case_id: str) -> list[Path]:
    root = MATERIAL_ROOT / "公开样例请求"
    found = [root / f"{case_id}.json"]
    extra = root / case_id
    if extra.is_dir():
        found.extend(sorted(extra.glob("*.json")))
    return [p for p in found if p.is_file()]


def run_in_container(case_id: str, implementation: Path, request: Path) -> tuple[int, str, str]:
    material_root = MATERIAL_ROOT
    command = [
        "docker",
        "run",
        "--rm",
        "-i",
        "--pull=never",
        "--platform",
        "linux/amd64",
        "--network",
        "none",
        "--read-only",
        "--user",
        "65534:65534",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--cpus",
        "1",
        "--memory",
        "1024m",
        "--pids-limit",
        "64",
        "--ulimit",
        f"cpu={CPU_SECONDS}:{CPU_SECONDS}",
        "--env",
        "PYTHONHASHSEED=42",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--mount",
        f"type=bind,source={material_root / case_id},target=/experiment,readonly",
        "--mount",
        f"type=bind,source={implementation},target=/experiment/implementation.py,readonly",
        "--workdir",
        "/experiment",
        "--entrypoint",
        "python",
        IMAGE,
        "-B",
        "-c",
        "import signal,runpy; signal.alarm(20); runpy.run_path('worker.py',run_name='__main__')",
    ]
    with open(request, "rb") as stdin:
        completed = subprocess.run(
            command, stdin=stdin, capture_output=True, text=True, timeout=180, check=False
        )
    return completed.returncode, completed.stdout, completed.stderr


def summarise(stdout: str) -> dict[str, object]:
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if isinstance(payload, dict):
                return {
                    "keys": sorted(payload),
                    "has_baseline": "baseline" in payload,
                    "has_candidate": "candidate" in payload,
                    "has_details": "details" in payload,
                }
    return {"keys": [], "unparsable": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default="D-01")
    parser.add_argument("--budget", type=float, default=170.0)
    parser.add_argument("--source", type=Path, help="复用已生成的源码，跳过模型调用")
    args = parser.parse_args()

    case_id: str = args.case
    if not MATERIAL_ROOT.is_dir():
        raise SystemExit(f"找不到官方材料目录：{MATERIAL_ROOT}")
    if shutil.which("docker") is None:
        raise SystemExit("需要 Docker 才能在隔离容器里执行生成源码")

    work = Path(tempfile.mkdtemp(prefix=f"ra-{case_id}-"))
    implementation = work / "implementation.py"

    if args.source is not None:
        shutil.copyfile(args.source, implementation)
        print(f"[reuse] 复用已生成源码 {args.source}")
    else:
        settings = Settings.from_env(load_env())
        gateway_settings = settings.require_gateway()
        log = JsonLogger("validate-d", "INFO")
        context = DevContext(
            case_id=case_id,
            deadline=Deadline.start(args.budget),
            log=log,
            gateway=HttpGateway(gateway_settings, log=log),
            work_dir=work,
        )
        materials = load_case_materials(case_id, MATERIAL_ROOT)
        if not materials.available:
            raise SystemExit(f"材料不可用：{materials.missing_reason}")

        task_input = TASK_INPUT_BY_CASE.get(case_id, "")
        started = time.monotonic()
        # 直接复用生产路径的生成与保存逻辑，验证的就是真实代码
        generated = _generate_source_with_input(context, materials, task_input, implementation)
        elapsed = time.monotonic() - started
        if generated is None:
            print("[FAIL] 始终拿不到通过来源格式检查的源码；详见日志")
            return 2
        source, digest, self_test_passed = generated
        print(
            f"[gen] {elapsed:.1f}s  {len(source.encode('utf-8'))} bytes  "
            f"sha256={digest[:16]}  self_test={'PASS' if self_test_passed else 'FAIL'}"
        )

    requests = sample_requests(case_id)
    if not requests:
        raise SystemExit(f"找不到 {case_id} 的公开样例请求")

    failures = 0
    for request in requests:
        started = time.monotonic()
        try:
            code, stdout, stderr = run_in_container(case_id, implementation, request)
        except subprocess.TimeoutExpired:
            print(f"[TIMEOUT] {request.name}")
            failures += 1
            continue
        elapsed = time.monotonic() - started
        info = summarise(stdout)
        ok = code == 0 and not info.get("unparsable") and info.get("keys")
        print(
            f"[{'OK ' if ok else 'FAIL'}] {request.name}  {elapsed:.1f}s  rc={code}  "
            f"keys={info.get('keys')}"
        )
        if not ok:
            failures += 1
            tail = (stderr or stdout).strip().splitlines()[-6:]
            for line in tail:
                print(f"        | {line}")

    print(f"\n{len(requests) - failures}/{len(requests)} 个公开样例通过；工作目录 {work}")
    print("注意：这只是离线格式与算法自检，不是平台来源准入，也不是可信执行结果。")
    return 1 if failures else 0


def _generate_source_with_input(context, materials, task_input: str, destination: Path):
    """用生产路径的 _generate_source，但任务 input 由脚本给出。"""
    from research_assistant.contracts.task import Task

    task = Task.parse(
        {
            "case_id": context.case_id,
            "function": "Reproduction",
            "title": f"{context.case_id} 离线验证",
            "input": task_input,
            "materials": [],
            "constraints": [],
            "expected_output": "返回 case_id、status、metrics、figures。",
            "output_schema_ref": "Reproduction",
        }
    )
    return _generate_source(task, context, materials, destination, MATERIAL_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
