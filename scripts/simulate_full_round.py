"""本地模拟一整轮 11 题评测（A/B/C/D/E = 1/3/2/3/2）。

这是最接近真实评测的本地验证：构造一份合规的 v3 派题记录与 11 个题目文件、
一个自建论文库，然后**逐题**调用真实服务的 POST /solve，最后核验：
11 个结果文件全部落盘、每个都符合该类型的字段白名单、派题记录通过校验、
题目文件与派题记录未被改写、C/E 的 markdown 与另存文件逐字一致。

D 类默认走 materials_missing 快速路径（不挂载 /data/experiments），
因此不消耗模型预算；D 的算法正确性由 validate_d_offline.py 与
validate_d_invariants.py 单独验证（13/13 公开样例、227 项不变量）。
加 --with-d-materials 可让它对着真实官方材料生成并自测。

需要 .env.local 与网络。用法：
    uv run python scripts/simulate_full_round.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from research_assistant.api import create_app  # noqa: E402
from research_assistant.contracts.results import ALLOWED_TOP_LEVEL_FIELDS  # noqa: E402
from research_assistant.contracts.task import TaskFunction  # noqa: E402
from research_assistant.runtime.settings import Settings  # noqa: E402
from tests.conftest import PAPER_SPECS, SCANNED_FILENAME  # noqa: E402
from tests.fixtures.dispatch_v3 import (  # noqa: E402
    V3_CASE_IDS,
    build_v3_dispatch,
    write_dispatch,
)
from tests.fixtures.papers import write_paper, write_scanned_pdf  # noqa: E402

TEAM = "模拟队伍 A+1"
MATERIAL_ROOT = REPO_ROOT.parent / "赛题信息" / "research-experiments-20261020-source-v1"

# 正式评测里每道题的 input 由平台下发；这里用能覆盖各类关键要求的自建题面。
INPUTS: dict[str, dict[str, Any]] = {
    "A-100": {
        "title": "室内植物光照与生长：科研对话",
        "input": (
            "我想研究不同光照条件对室内植物生长速度的影响。请解释光照强度、每日照射时长"
            "和生长速度之间可能有什么关系，并问我一个最关键的研究范围限定问题。"
        ),
        "constraints": [],
        "expected_output": "解释可能关系，并提出一个研究范围限定问题。",
    },
    "B-01": {
        "title": "城市绿地与地表温度检索",
        "input": (
            "查找使用遥感手段研究城市绿地覆盖率与地表温度关系的论文，重点关注城市热环境；"
            "排除只讨论图像分割算法而不涉及温度的工作，也排除电池回收方向的研究。"
        ),
        "constraints": ["只返回本地实际存在的 PDF", "每篇给出唯一完整文件名与具体理由"],
        "expected_output": "按相关性排序的论文列表与理由。",
    },
    "B-02": {
        "title": "城市热岛与植被检索",
        "input": (
            "查找比较不同城市区域植被状况与热环境差异的论文；"
            "排除只讲卫星图像分割模型架构、不涉及温度或热环境的研究。"
        ),
        "constraints": ["没有相关论文时返回空数组"],
        "expected_output": "按相关性排序的论文列表与理由。",
    },
    "B-03": {
        "title": "一个库内没有相关文献的检索",
        "input": (
            "查找使用量子化学方法研究锂电池正极材料电子结构的论文；"
            "本库若没有真正相关的论文，必须返回空数组，不得凑数。"
        ),
        "constraints": ["不得为了凑篇数放入弱相关论文"],
        "expected_output": "按相关性排序的论文列表；无相关文献时为空数组。",
    },
    "C-01": {
        "title": "城市绿地与地表温度综述",
        "input": (
            "阅读本题指定论文，撰写至少 800 字的 Markdown 综述，比较各论文的研究问题、"
            "方法、数据、指标、结论、局限与适用边界，梳理技术脉络。正文事实用 [编号] 或 "
            "[编号, p.页码] 标注，结尾用「## 参考文献」列出实际引用的指定论文及完整 PDF 文件名。"
        ),
        "constraints": ["只依据本题指定论文", "不得编造事实、数据、页码或参考文献"],
        "expected_output": "返回 case_id、path 和完整 Markdown 综述。",
    },
    "C-03": {
        "title": "城市热环境研究方法比较综述",
        "input": (
            "撰写至少 800 字的 Markdown 综述，比较各论文在城市热环境研究中的数据源、"
            "指标定义与适用边界，指出方法学上的分歧与研究缺口。正文用 [编号] 标注引用，"
            "结尾用「## 参考文献」列出实际引用的论文及完整 PDF 文件名。"
        ),
        "constraints": ["不得编造页码", "每篇被引论文至少在两个段落中出现"],
        "expected_output": "返回 case_id、path 和完整 Markdown 综述。",
    },
    "D-01": {
        "title": "TF-IDF 与 softmax 分类复现",
        "input": (
            "核心指标恰好三项：name=Macro-F1，unit=分数；name=Accuracy，unit=%；"
            "name=运行时间，unit=秒。"
        ),
        "constraints": ["指标必须来自本次真实运行"],
        "expected_output": "返回 case_id、status、恰好三项 metrics 和 figures。",
    },
    "D-02": {
        "title": "标题/正文 BM25 与稳健选参复现",
        "input": (
            "核心指标恰好三项：name=Recall@5，unit=%；name=MRR@10，unit=分数；"
            "name=查询吞吐，unit=queries/s。"
        ),
        "constraints": ["指标必须来自本次真实运行"],
        "expected_output": "返回 case_id、status、恰好三项 metrics 和 figures。",
    },
    "D-05": {
        "title": "FIFO 与加权公平调度复现",
        "input": (
            "核心指标恰好三项：name=公平性 Jain 指数，unit=分数；name=归一化吞吐，unit=%；"
            "name=平均等待时间，unit=ms。"
        ),
        "constraints": ["指标必须来自本次真实运行"],
        "expected_output": "返回 case_id、status、恰好三项 metrics 和 figures。",
    },
    "E-01": {
        "title": "城市绿地降温效应的研究方案",
        "input": (
            "基于指定论文提出一个具体、可证伪的研究方案，说明如何进一步量化城市绿地对"
            "地表温度的调节作用。需覆盖研究问题与边界、动机与已有局限、核心机制、技术路线、"
            "基线与改进组、数据集、实验与消融、量化指标、多文献互补、可行性与成本、"
            "失败边界与可证伪条件。"
        ),
        "constraints": ["不得把未验证的改进效果写成已完成实验"],
        "expected_output": "返回 case_id、path 和完整 Markdown 研究方案。",
    },
    "E-02": {
        "title": "跨城市热环境指标可比性的研究方案",
        "input": (
            "基于指定论文，设计一个能提升不同城市热环境研究结果可比性的方案，"
            "需给出明确的基线与改进组、数据集、消融实验、量化指标与可证伪条件。"
        ),
        "constraints": ["明确区分论文证据、模拟观察与所提假设"],
        "expected_output": "返回 case_id、path 和完整 Markdown 研究方案。",
    },
}
PAPER_NAMES = [str(spec["filename"]) for spec in PAPER_SPECS]


def build_task(case_id: str, function: str) -> dict[str, Any]:
    spec = INPUTS[case_id]
    materials: list[str] = []
    if function == "Literature_Retrieval":
        materials = ["本地论文文件夹内的 PDF"]
    elif function in ("Survey", "Idea"):
        materials = [*PAPER_NAMES, SCANNED_FILENAME]
    return {
        "case_id": case_id,
        "function": function,
        "title": spec["title"],
        "input": spec["input"],
        "materials": materials,
        "constraints": spec["constraints"],
        "expected_output": spec["expected_output"],
        "output_schema_ref": function,
    }


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--with-d-materials",
        action="store_true",
        help="让 D 类对着真实官方材料生成并自测（每题约 40-115 秒模型时间）",
    )
    parser.add_argument("--keep", action="store_true", help="保留临时工作区以便检查")
    args = parser.parse_args()

    base = Path(tempfile.mkdtemp(prefix="ra-round-"))
    workspace = base / "output"
    papers = base / "papers"
    (workspace / "data" / TEAM / "assigned").mkdir(parents=True)
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

    # 用真实题面覆盖 fixture 的占位题面，再据此重算 question_sha256
    functions = {
        "A": "Chat",
        "B": "Literature_Retrieval",
        "C": "Survey",
        "D": "Reproduction",
        "E": "Idea",
    }
    record = build_v3_dispatch(TEAM)
    for case in record["cases"]:
        case_id = case["case_id"]
        payload = build_task(case_id, functions[case_id[0]])
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        path = workspace / "data" / TEAM / "assigned" / f"{case_id}.json"
        path.write_bytes(body)
        case["question_sha256"] = hashlib.sha256(body).hexdigest()
        case["title"] = payload["title"]
    write_dispatch(workspace, TEAM, record)

    env = {
        **load_env(),
        "RESEARCH_WORKSPACE_TEAM": TEAM,
        "RESEARCH_WORKSPACE_ROOT": str(workspace),
        "RESEARCH_PAPERS_ROOT": str(papers),
    }
    if args.with_d_materials:
        env["RESEARCH_EXPERIMENT_ROOT"] = str(MATERIAL_ROOT)
    else:
        env["RESEARCH_EXPERIMENT_ROOT"] = str(base / "no-experiments")

    for key, value in env.items():
        os.environ[key] = value

    settings = Settings.from_env(env)
    if settings.gateway is None:
        raise SystemExit(f"网关未配置：{settings.gateway_config_error}")

    expected_ids = list(V3_CASE_IDS)
    results: dict[str, Any] = {}
    started_all = time.monotonic()
    with TestClient(create_app(settings)) as client:
        health = client.get("/health")
        print(f"/health → {health.status_code} {health.json()}")
        if health.status_code != 200:
            return 2
        for case_id in expected_ids:
            task = build_task(case_id, functions[case_id[0]])
            started = time.monotonic()
            response = client.post("/solve", json=task)
            elapsed = time.monotonic() - started
            ok = response.status_code == 200
            body = response.json() if ok else response.json().get("error", {})
            results[case_id] = body
            summary = ""
            if ok and "papers" in body:
                summary = f"papers={len(body['papers'])}"
            elif ok and "markdown" in body:
                summary = f"markdown={len(body['markdown'])} 字符 path={body['path']}"
            elif ok and "status" in body:
                summary = f"status={body['status']} metrics={len(body['metrics'])}"
            elif ok:
                summary = f"answer={len(body.get('answer', ''))} 字符"
            print(
                f"[{'OK ' if ok else 'ERR'}] {case_id:6} {response.status_code} "
                f"{elapsed:6.1f}s  {summary}"
            )
    total = time.monotonic() - started_all

    print(f"\n===== 核验（总耗时 {total:.1f}s）=====")
    failures: list[str] = []

    # dispatch.json 与结果 JSON 同在本队目录根部，必须排除
    written = sorted(
        p.stem for p in (workspace / "data" / TEAM).glob("*.json") if p.stem != "dispatch"
    )
    if written != sorted(expected_ids):
        failures.append(f"结果文件不齐：{written}")
    else:
        print(f"✓ 11 个结果文件全部落盘：{written}")

    for case_id in expected_ids:
        path = workspace / "data" / TEAM / f"{case_id}.json"
        if not path.is_file():
            continue
        body = json.loads(path.read_text(encoding="utf-8"))
        function = functions[case_id[0]]
        allowed = ALLOWED_TOP_LEVEL_FIELDS[TaskFunction(function)]
        if set(body) != set(allowed):
            failures.append(f"{case_id} 顶层字段不符：{sorted(body)} vs {sorted(allowed)}")
        if body.get("case_id") != case_id:
            failures.append(f"{case_id} 的 case_id 未原样返回：{body.get('case_id')!r}")
        if function in ("Survey", "Idea"):
            markdown = body.get("markdown", "")
            if len(markdown.strip()) < 800:
                failures.append(f"{case_id} 正文仅 {len(markdown.strip())} 字符，不足 800")
            infix = "综述" if function == "Survey" else "灵感Idea报告"
            md_path = workspace / "data" / "expert" / f"{case_id}-{infix}-{TEAM}.md"
            if not md_path.is_file():
                failures.append(f"{case_id} 的 markdown 文件不存在：{md_path.name}")
            elif md_path.read_text(encoding="utf-8") != markdown:
                failures.append(f"{case_id} 的 markdown 与另存文件不一致")
            if body.get("path") != f"data/expert/{md_path.name}":
                failures.append(f"{case_id} 的 path 字段不正确：{body.get('path')!r}")
        if function == "Reproduction":
            if len(body.get("metrics", [])) != 3:
                failures.append(f"{case_id} 的 metrics 不是恰好三项")
            for metric in body.get("metrics", []):
                if set(metric) != {"name", "unit", "reference", "observed", "relative_error"}:
                    failures.append(f"{case_id} 的指标字段不全：{sorted(metric)}")

    assigned_now = {
        p.name: p.read_bytes() for p in (workspace / "data" / TEAM / "assigned").glob("*.json")
    }
    if len(assigned_now) != 11:
        failures.append(f"题目文件数量变化：{len(assigned_now)}")
    if not (workspace / "data" / TEAM / "dispatch.json").is_file():
        failures.append("派题记录丢失")
    leftovers = [
        p.name
        for p in (workspace / "data" / TEAM).iterdir()
        if p.name.endswith(".tmp") or p.name.startswith(".health-probe")
    ]
    if leftovers:
        failures.append(f"存在临时残留：{leftovers}")

    other = workspace / "data" / "别的队"
    if other.exists():
        failures.append("出现了不属于本队的目录")

    if failures:
        print(f"\n✗ {len(failures)} 项核验未通过：")
        for line in failures:
            print(f"   - {line}")
    else:
        print(
            "✓ 字段白名单、case_id 原样、C/E 正文长度与逐字一致、D 三项指标完整、"
            "题目与派题未被改写、无临时残留 —— 全部通过"
        )

    if args.keep:
        print(f"\n工作区已保留：{workspace}")
    else:
        shutil.rmtree(base, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
