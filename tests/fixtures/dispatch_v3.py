"""自建的 v3 派题记录测试材料。

这些不是官方题目，不含任何正式答案；只用于验证解析与校验流程。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

# 覆盖三位后缀（A-100）以验证解析器不假定两位编号
V3_CASE_IDS: tuple[str, ...] = (
    "A-100",
    "B-01",
    "B-02",
    "B-03",
    "C-01",
    "C-03",
    "D-01",
    "D-02",
    "D-05",
    "E-01",
    "E-02",
)
FUNCTION_BY_PREFIX: dict[str, str] = {
    "A": "Chat",
    "B": "Literature_Retrieval",
    "C": "Survey",
    "D": "Reproduction",
    "E": "Idea",
}
DIFFICULTY_BY_CASE: dict[str, str] = {"D-01": "easy", "D-02": "medium", "D-05": "hard"}


def question_payload(case_id: str) -> dict[str, Any]:
    function = FUNCTION_BY_PREFIX[case_id[0]]
    return {
        "case_id": case_id,
        "function": function,
        "title": f"自建测试题目 {case_id}",
        "input": f"这是 {case_id} 的自建测试任务说明，不是官方题目。",
        "materials": [],
        "constraints": [],
        "expected_output": "自建的预期输出说明。",
        "output_schema_ref": function,
    }


def build_v3_dispatch(
    team: str, *, mutate: Callable[[dict[str, Any]], None] | None = None
) -> dict[str, Any]:
    cases = []
    for case_id in V3_CASE_IDS:
        body = json.dumps(question_payload(case_id), ensure_ascii=False, indent=2).encode("utf-8")
        entry: dict[str, Any] = {
            "case_id": case_id,
            "function": FUNCTION_BY_PREFIX[case_id[0]],
            "title": f"自建测试题目 {case_id}",
            "question_file": f"data/{team}/assigned/{case_id}.json",
            "question_sha256": hashlib.sha256(body).hexdigest(),
        }
        if case_id in DIFFICULTY_BY_CASE:
            entry["difficulty"] = DIFFICULTY_BY_CASE[case_id]
        cases.append(entry)

    record: dict[str, Any] = {
        "dispatch_id": "d-selftest-001",
        "team": team,
        "schema_version": 3,
        "selection_mode": "random_a_fixed_bcde",
        "dispatch_policy_version": "research-assistant-dispatch-v3",
        "dispatch_policy_sha256": "0" * 64,
        "difficulty_by_case": dict(DIFFICULTY_BY_CASE),
        "cases_per_category": {"A": 1, "B": 3, "C": 2, "D": 3, "E": 2},
        "rules_version": "research-assistant-objective-v3",
        "case_ids": list(V3_CASE_IDS),
        "cases": cases,
    }
    if mutate is not None:
        mutate(record)
    return record


def write_dispatch(workspace_root: Path, team: str, payload: Mapping[str, Any]) -> Path:
    path = workspace_root / "data" / team / "dispatch.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_questions(workspace_root: Path, team: str, record: Mapping[str, Any]) -> None:
    for case in record["cases"]:
        payload = question_payload(case["case_id"])
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        path = workspace_root / "data" / team / "assigned" / f"{case['case_id']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
