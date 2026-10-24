"""对交付镜像跑一整轮 11 题（官方 AgentRuntime 入口 + 真实模型）。

与 simulate_full_round.py 的区别：那个脚本用开发入口（create_app），本脚本启动
真实的 `research-assistant:p1` 容器，把工作区与论文库挂进去，逐题 POST /solve。
D 类不挂 /data/experiments，走 materials_missing 快速路径，不消耗 D 的生成预算。

需要 .env.local、Docker 与已构建的镜像。用法：
    uv run python scripts/simulate_full_round_image.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

import scripts.simulate_full_round as round_fixture  # noqa: E402
from research_assistant.contracts.results import ALLOWED_TOP_LEVEL_FIELDS  # noqa: E402
from research_assistant.contracts.task import TaskFunction  # noqa: E402
from tests.conftest import PAPER_SPECS, SCANNED_FILENAME  # noqa: E402
from tests.fixtures.dispatch_v3 import V3_CASE_IDS, build_v3_dispatch, write_dispatch  # noqa: E402
from tests.fixtures.papers import write_paper, write_scanned_pdf  # noqa: E402

IMAGE = "research-assistant:p1"
CONTAINER = "ra-round-image"
HOST_PORT = 18082


def docker() -> str:
    return shutil.which("docker") or "/Applications/Docker.app/Contents/Resources/bin/docker"


def prepare(base: Path, team: str) -> tuple[Path, Path]:
    workspace = base / "output"
    papers = base / "papers"
    (workspace / "data" / team / "assigned").mkdir(parents=True)
    (workspace / "data" / "expert").mkdir(parents=True)
    papers.mkdir(parents=True)
    for spec in PAPER_SPECS:
        write_paper(
            papers,
            str(spec["filename"]),
            title=str(spec["title"]),
            author=str(spec["author"]),
            keywords=str(spec["keywords"]),
            creation_date=str(spec["creation_date"]),
            abstract=str(spec["abstract"]),
            body_paragraphs=[str(b) for b in spec["body"]],
            pages=2,
        )
    write_scanned_pdf(papers, SCANNED_FILENAME)

    functions = {
        "A": "Chat",
        "B": "Literature_Retrieval",
        "C": "Survey",
        "D": "Reproduction",
        "E": "Idea",
    }
    record = build_v3_dispatch(team)
    for case in record["cases"]:
        case_id = case["case_id"]
        payload = round_fixture.build_task(case_id, functions[case_id[0]])
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        (workspace / "data" / team / "assigned" / f"{case_id}.json").write_bytes(body)
        case["question_sha256"] = hashlib.sha256(body).hexdigest()
        case["title"] = payload["title"]
    write_dispatch(workspace, team, record)
    # 容器内是非 root 的 agent 用户，开发机上让挂载目录可写
    subprocess.run(["chmod", "-R", "0777", str(base)], check=True)
    return workspace, papers


def start_container(base: Path, workspace: Path, papers: Path, team: str) -> None:
    env_file = REPO_ROOT / ".env.local"
    if not env_file.is_file():
        raise SystemExit("缺少 .env.local；本脚本需要真实网关凭据")
    subprocess.run([docker(), "rm", "-f", CONTAINER], check=False, capture_output=True)
    command = [
        docker(),
        "run",
        "-d",
        "--name",
        CONTAINER,
        "--platform",
        "linux/amd64",
        "-p",
        f"{HOST_PORT}:8080",
        "-v",
        f"{workspace}:/output",
        "-v",
        f"{papers}:/data/papers:ro",
        "--env-file",
        str(env_file),
        "-e",
        f"RESEARCH_WORKSPACE_TEAM={team}",
        "-e",
        "RESEARCH_WORKSPACE_ROOT=/output",
        "-e",
        "RESEARCH_PAPERS_ROOT=/data/papers",
        "-e",
        f"RESEARCH_EXPERIMENT_ROOT={base / 'no-experiments'}",
        IMAGE,
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)


def wait_ready(timeout: float = 60.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{HOST_PORT}/health", timeout=2) as resp:
                return json.load(resp)
        except Exception:
            time.sleep(0.4)
    raise SystemExit("镜像未在 60 秒内就绪")


def post(task: dict[str, Any], timeout: float = 240.0) -> tuple[int, bytes, float]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{HOST_PORT}/solve",
        data=json.dumps(task, ensure_ascii=False).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Research-Task-ID": f"image-{task['case_id']}",
        },
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, resp.read(), time.monotonic() - started
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), time.monotonic() - started


def check_case(
    task: dict[str, Any],
    body: dict[str, Any],
    workspace: Path,
    team: str,
) -> list[str]:
    problems: list[str] = []
    function = TaskFunction(task["function"])
    allowed = set(ALLOWED_TOP_LEVEL_FIELDS[function])
    if set(body) != allowed:
        problems.append(f"顶层字段 {sorted(body)} != {sorted(allowed)}")
    if body.get("case_id") != task["case_id"]:
        problems.append("case_id 未原样回显")
    path = workspace / "data" / team / f"{task['case_id']}.json"
    if not path.is_file():
        problems.append("结果文件未落盘")
    else:
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        if on_disk != body:
            problems.append("结果文件与响应不一致")
    if function in (TaskFunction.SURVEY, TaskFunction.IDEA):
        markdown = body.get("markdown", "")
        if len(markdown.strip()) < 800:
            problems.append("Markdown 少于 800 字符")
        target = workspace / str(body.get("path", ""))
        if not target.is_file():
            problems.append("path 指向的 Markdown 不存在")
        elif target.read_text(encoding="utf-8") != markdown:
            problems.append("Markdown 文件与响应不一致")
    if function is TaskFunction.REPRODUCTION:
        if body.get("status") != "materials_missing":
            problems.append(f"D 状态 {body.get('status')!r}，期望 materials_missing")
        if len(body.get("metrics", [])) != 3:
            problems.append("D 指标不是三项")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--team", default="镜像整轮验证队伍")
    parser.add_argument("--keep", action="store_true", help="保留容器与临时目录")
    args = parser.parse_args()

    base = Path(tempfile.mkdtemp(prefix="ra-image-round-"))
    workspace, papers = prepare(base, args.team)
    start_container(base, workspace, papers, args.team)
    try:
        health = wait_ready()
        print(f"/health → {json.dumps(health, ensure_ascii=False)}")
        failures: list[str] = []
        total_started = time.monotonic()
        for case_id in V3_CASE_IDS:
            function = {
                "A": "Chat",
                "B": "Literature_Retrieval",
                "C": "Survey",
                "D": "Reproduction",
                "E": "Idea",
            }[case_id[0]]
            task = round_fixture.build_task(case_id, function)
            status, raw, elapsed = post(task)
            if status != 200:
                failures.append(f"{case_id}: HTTP {status} {raw[:200]!r}")
                print(f"{case_id}: HTTP {status} ({elapsed:.1f}s) FAIL")
                continue
            body = json.loads(raw)
            problems = check_case(task, body, workspace, args.team)
            marker = "ok" if not problems else "FAIL: " + "; ".join(problems)
            print(f"{case_id}: {elapsed:.1f}s {marker}")
            if problems:
                failures.append(f"{case_id}: {'; '.join(problems)}")
        total = time.monotonic() - total_started
        print(f"总耗时 {total:.1f}s；失败 {len(failures)} 项")
        for failure in failures:
            print(f"  - {failure}")
        return 0 if not failures else 1
    finally:
        if not args.keep:
            subprocess.run([docker(), "rm", "-f", CONTAINER], check=False, capture_output=True)
        else:
            print(f"容器保留：{CONTAINER}；工作区：{base}")


if __name__ == "__main__":
    sys.exit(main())
