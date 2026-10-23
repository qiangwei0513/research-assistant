from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from research_assistant.contracts.dispatch import (
    QUOTA_V3,
    TOTAL_CASES_V3,
    DispatchRecord,
)
from research_assistant.errors import DispatchValidationError, PathAuthorizationError
from research_assistant.runtime.paths import WorkspacePaths
from tests.fixtures.dispatch_v3 import build_v3_dispatch, write_dispatch, write_questions

TEAM = "测试队伍 A+1"


@pytest.fixture
def workspace(tmp_workspace: Path, papers_root: Path) -> WorkspacePaths:
    return WorkspacePaths(root=tmp_workspace, team=TEAM, papers_root=papers_root)


@pytest.fixture
def record_path(tmp_workspace: Path) -> Path:
    record = build_v3_dispatch(TEAM)
    write_questions(tmp_workspace, TEAM, record)
    return write_dispatch(tmp_workspace, TEAM, record)


def mutate(**changes: Any):
    def _apply(record: dict[str, Any]) -> None:
        record.update(changes)

    return _apply


def test_a_valid_v3_record_loads(record_path: Path) -> None:
    record = DispatchRecord.load(record_path)
    assert record.schema_version == 3
    assert record.rules_version == "research-assistant-objective-v3"
    assert record.team == TEAM
    assert len(record.case_ids) == TOTAL_CASES_V3 == 11
    assert record.cases_per_category == QUOTA_V3
    assert record.is_legacy is False


def test_every_case_entry_is_parsed(record_path: Path) -> None:
    record = DispatchRecord.load(record_path)
    assert len(record.cases) == 11
    by_id = {case.case_id: case for case in record.cases}
    assert by_id["D-01"].difficulty == "easy"
    assert by_id["D-05"].difficulty == "hard"
    assert by_id["A-100"].difficulty is None
    assert by_id["B-02"].function == "Literature_Retrieval"
    assert by_id["C-01"].question_file == f"data/{TEAM}/assigned/C-01.json"


def test_three_digit_case_suffix_is_supported(record_path: Path) -> None:
    assert "A-100" in DispatchRecord.load(record_path).case_ids


def test_verify_questions_passes_for_matching_hashes(
    record_path: Path, workspace: WorkspacePaths
) -> None:
    DispatchRecord.load(record_path).verify_questions(workspace)


def test_verify_questions_detects_a_tampered_question(
    record_path: Path, workspace: WorkspacePaths
) -> None:
    target = workspace.question_file("B-01")
    target.write_text('{"case_id": "B-01", "tampered": true}', encoding="utf-8")
    with pytest.raises(DispatchValidationError, match="sha256"):
        DispatchRecord.load(record_path).verify_questions(workspace)


def test_verify_questions_detects_a_missing_question(
    record_path: Path, workspace: WorkspacePaths
) -> None:
    workspace.question_file("E-02").unlink()
    with pytest.raises(DispatchValidationError, match="missing"):
        DispatchRecord.load(record_path).verify_questions(workspace)


def test_verify_questions_rejects_files_outside_assigned(
    record_path: Path, workspace: WorkspacePaths
) -> None:
    record = DispatchRecord.load(record_path)
    cases = list(record.cases)
    object.__setattr__(cases[0], "question_file", f"data/{TEAM}/dispatch.json")
    object.__setattr__(record, "cases", tuple(cases))
    with pytest.raises((DispatchValidationError, PathAuthorizationError)):
        record.verify_questions(workspace)


def test_verify_questions_rejects_another_teams_directory(
    record_path: Path, workspace: WorkspacePaths
) -> None:
    record = DispatchRecord.load(record_path)
    cases = list(record.cases)
    object.__setattr__(cases[0], "question_file", "data/别的队/assigned/A-100.json")
    object.__setattr__(record, "cases", tuple(cases))
    with pytest.raises((DispatchValidationError, PathAuthorizationError)):
        record.verify_questions(workspace)


@pytest.mark.parametrize(
    "field",
    [
        "dispatch_id",
        "team",
        "schema_version",
        "selection_mode",
        "dispatch_policy_version",
        "dispatch_policy_sha256",
        "rules_version",
        "cases_per_category",
        "case_ids",
        "cases",
    ],
)
def test_missing_field_is_rejected(tmp_workspace: Path, field: str) -> None:
    def drop(record: dict[str, Any]) -> None:
        del record[field]

    path = write_dispatch(tmp_workspace, TEAM, build_v3_dispatch(TEAM, mutate=drop))
    with pytest.raises(DispatchValidationError, match=field):
        DispatchRecord.load(path)


def test_wrong_schema_version_is_flagged_legacy(tmp_workspace: Path) -> None:
    path = write_dispatch(
        tmp_workspace,
        TEAM,
        build_v3_dispatch(
            TEAM,
            mutate=mutate(
                schema_version=2,
                cases_per_category=5,
                rules_version="research-assistant-objective-v2",
            ),
        ),
    )
    record = DispatchRecord.load(path)
    assert record.is_legacy is True


def test_v3_rules_version_is_required_for_non_legacy(tmp_workspace: Path) -> None:
    path = write_dispatch(
        tmp_workspace, TEAM, build_v3_dispatch(TEAM, mutate=mutate(rules_version="something-else"))
    )
    with pytest.raises(DispatchValidationError, match="rules_version"):
        DispatchRecord.load(path)


def test_case_ids_must_be_unique(tmp_workspace: Path) -> None:
    def duplicate(record: dict[str, Any]) -> None:
        record["case_ids"] = [record["case_ids"][0]] * 11

    path = write_dispatch(tmp_workspace, TEAM, build_v3_dispatch(TEAM, mutate=duplicate))
    with pytest.raises(DispatchValidationError, match="duplicate"):
        DispatchRecord.load(path)


def test_case_ids_must_total_eleven(tmp_workspace: Path) -> None:
    """保持声明配额不变、只少给一题，触发总数校验。"""

    def shrink(record: dict[str, Any]) -> None:
        record["case_ids"] = record["case_ids"][:10]
        record["cases"] = record["cases"][:10]

    path = write_dispatch(tmp_workspace, TEAM, build_v3_dispatch(TEAM, mutate=shrink))
    with pytest.raises(DispatchValidationError, match="11"):
        DispatchRecord.load(path)


@pytest.mark.parametrize(
    "quota",
    [
        {"A": 2, "B": 3, "C": 2, "D": 3, "E": 2},
        {"A": 1, "B": 2, "C": 2, "D": 3, "E": 2},
        {"A": 1, "B": 3, "C": 2, "D": 2, "E": 2},
        {"A": 1, "B": 3, "C": 2, "D": 3, "E": 1},
        {"A": 1, "B": 3, "C": 1, "D": 3, "E": 2},
    ],
)
def test_quota_must_be_one_three_two_three_two(tmp_workspace: Path, quota: dict[str, int]) -> None:
    def set_quota(record: dict[str, Any]) -> None:
        record["cases_per_category"] = quota

    path = write_dispatch(tmp_workspace, TEAM, build_v3_dispatch(TEAM, mutate=set_quota))
    with pytest.raises(DispatchValidationError, match="cases_per_category"):
        DispatchRecord.load(path)


def test_declared_quota_must_match_actual_case_ids(tmp_workspace: Path) -> None:
    """把一道 B 题换成 A 题：总数仍是 11，但各类实际计数与声明配额不符 → 拒绝。"""

    def inconsistent(record: dict[str, Any]) -> None:
        record["case_ids"][1] = "A-02"
        record["cases"][1] = {**record["cases"][1], "case_id": "A-02", "function": "Chat"}

    path = write_dispatch(tmp_workspace, TEAM, build_v3_dispatch(TEAM, mutate=inconsistent))
    with pytest.raises(DispatchValidationError, match="do not match cases_per_category"):
        DispatchRecord.load(path)


def test_cases_must_correspond_one_to_one_with_case_ids(tmp_workspace: Path) -> None:
    def mismatch(record: dict[str, Any]) -> None:
        record["cases"] = record["cases"][:9]

    path = write_dispatch(tmp_workspace, TEAM, build_v3_dispatch(TEAM, mutate=mismatch))
    with pytest.raises(DispatchValidationError, match="one-to-one"):
        DispatchRecord.load(path)


def test_missing_case_entry_field_is_rejected(tmp_workspace: Path) -> None:
    def drop_field(record: dict[str, Any]) -> None:
        del record["cases"][0]["question_sha256"]

    path = write_dispatch(tmp_workspace, TEAM, build_v3_dispatch(TEAM, mutate=drop_field))
    with pytest.raises(DispatchValidationError, match="question_sha256"):
        DispatchRecord.load(path)


def test_team_mismatch_with_runtime_is_detected(
    workspace: WorkspacePaths, tmp_workspace: Path
) -> None:
    path = write_dispatch(tmp_workspace, TEAM, build_v3_dispatch("别的队"))
    record = DispatchRecord.load(path)
    with pytest.raises(DispatchValidationError, match="team"):
        record.assert_team(TEAM)


def test_assert_team_accepts_a_matching_record(record_path: Path) -> None:
    DispatchRecord.load(record_path).assert_team(TEAM)


def test_unparseable_json_is_rejected(tmp_workspace: Path) -> None:
    path = tmp_workspace / "data" / TEAM / "dispatch.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(DispatchValidationError, match="JSON"):
        DispatchRecord.load(path)


def test_missing_dispatch_file_raises_file_not_found(tmp_workspace: Path) -> None:
    """派题文件缺失不是致命错误，由调用方决定降级；load 只报 FileNotFoundError。"""
    with pytest.raises(FileNotFoundError):
        DispatchRecord.load(tmp_workspace / "data" / TEAM / "dispatch.json")


def test_integer_cases_per_category_is_legacy_not_v3(tmp_workspace: Path) -> None:
    path = write_dispatch(
        tmp_workspace,
        TEAM,
        build_v3_dispatch(TEAM, mutate=mutate(schema_version=2, cases_per_category=5)),
    )
    record = DispatchRecord.load(path)
    assert record.is_legacy is True
    assert record.cases_per_category == {}


def test_loaded_record_is_serialisable_for_logging(record_path: Path) -> None:
    record = DispatchRecord.load(record_path)
    assert json.loads(json.dumps(record.summary()))["dispatch_id"] == "d-selftest-001"
    assert "team" in record.summary()
