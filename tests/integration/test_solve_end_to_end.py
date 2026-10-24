"""端到端集成测试：临时工作区 + FakeGateway，验证落盘与响应的一致性。

自建测试材料，非官方题目，不含正式答案。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from research_assistant.api import create_app
from research_assistant.errors import GatewayError
from research_assistant.runtime.gateway import FakeGateway
from research_assistant.runtime.settings import Settings
from tests.fixtures.dispatch_v3 import build_v3_dispatch, write_dispatch, write_questions

TEAM = "测试队伍 A+1"


def answer_json(answer: str = "光照强度影响光合速率。") -> str:
    return json.dumps(
        {"answer": answer, "clarifying_question": "研究对象是哪种室内植物？"}, ensure_ascii=False
    )


def chat_payload(case_id: str = "A-100") -> dict[str, Any]:
    return {
        "case_id": case_id,
        "function": "Chat",
        "title": "室内植物光照与生长",
        "input": "解释光照强度与生长速度的关系，并问一个最关键的限定问题。",
        "materials": [],
        "constraints": [],
        "expected_output": "解释关系并提问。",
        "output_schema_ref": "Chat",
    }


@pytest.fixture
def mounted_dispatch(tmp_workspace: Path) -> None:
    record = build_v3_dispatch(TEAM)
    write_questions(tmp_workspace, TEAM, record)
    write_dispatch(tmp_workspace, TEAM, record)


def build_client(base_env: dict[str, str], responses: list[Any]) -> TestClient:
    return TestClient(create_app(Settings.from_env(base_env), gateway=FakeGateway(responses)))


def test_response_body_is_byte_identical_to_the_persisted_file(
    base_env: dict[str, str], tmp_workspace: Path
) -> None:
    with build_client(base_env, [answer_json()]) as client:
        response = client.post("/solve", json=chat_payload())
    assert response.status_code == 200
    written = (tmp_workspace / "data" / TEAM / "A-100.json").read_bytes()
    assert response.content == written


def test_persisted_file_is_valid_utf8_json_with_the_same_case_id(
    base_env: dict[str, str], tmp_workspace: Path
) -> None:
    with build_client(base_env, [answer_json()]) as client:
        client.post("/solve", json=chat_payload())
    path = tmp_workspace / "data" / TEAM / "A-100.json"
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["case_id"] == "A-100"
    assert set(loaded) == {"case_id", "answer", "clarifying_question"}


def test_chinese_content_survives_the_round_trip(
    base_env: dict[str, str], tmp_workspace: Path
) -> None:
    answer = "光照强度与光合速率正相关。"
    with build_client(base_env, [answer_json(answer)]) as client:
        response = client.post("/solve", json=chat_payload())
    assert answer in response.text
    # ensure_ascii=False：中文必须是原字节，不能是 \uXXXX 转义
    assert b"\\u" not in response.content
    assert answer.encode("utf-8") in (tmp_workspace / "data" / TEAM / "A-100.json").read_bytes()


def test_a_mounted_dispatch_is_accepted_and_does_not_block_solving(
    base_env: dict[str, str], mounted_dispatch: None
) -> None:
    with build_client(base_env, [answer_json()]) as client:
        assert client.get("/health").json()["dispatch_mounted"] is True
        assert client.post("/solve", json=chat_payload()).status_code == 200


def test_a_tampered_question_file_blocks_solving(
    base_env: dict[str, str], mounted_dispatch: None, tmp_workspace: Path
) -> None:
    (tmp_workspace / "data" / TEAM / "assigned" / "B-01.json").write_text(
        '{"case_id": "B-01", "tampered": true}', encoding="utf-8"
    )
    with build_client(base_env, [answer_json()]) as client:
        response = client.post("/solve", json=chat_payload())
    assert response.status_code == 422
    assert response.json()["error"]["type"] == "DispatchValidationError"


def test_a_missing_question_file_blocks_solving(
    base_env: dict[str, str], mounted_dispatch: None, tmp_workspace: Path
) -> None:
    (tmp_workspace / "data" / TEAM / "assigned" / "E-02.json").unlink()
    with build_client(base_env, [answer_json()]) as client:
        response = client.post("/solve", json=chat_payload())
    assert response.status_code == 422
    assert response.json()["error"]["type"] == "DispatchValidationError"


def test_a_dispatch_for_another_team_blocks_solving(
    base_env: dict[str, str], tmp_workspace: Path
) -> None:
    record = build_v3_dispatch("别的队")
    write_dispatch(tmp_workspace, TEAM, record)
    with build_client(base_env, [answer_json()]) as client:
        response = client.post("/solve", json=chat_payload())
    assert response.status_code == 422
    assert response.json()["error"]["type"] == "DispatchValidationError"


def test_no_result_file_is_written_when_the_handler_fails(
    base_env: dict[str, str], tmp_workspace: Path
) -> None:
    with build_client(base_env, [GatewayError("boom")]) as client:
        assert client.post("/solve", json=chat_payload()).status_code == 502
    assert not (tmp_workspace / "data" / TEAM / "A-100.json").exists()


def test_unwritable_team_dir_reports_failure_not_success(
    base_env: dict[str, str], tmp_workspace: Path
) -> None:
    team_dir = tmp_workspace / "data" / TEAM
    team_dir.chmod(0o555)
    try:
        with build_client(base_env, [answer_json()]) as client:
            response = client.post("/solve", json=chat_payload())
        assert response.status_code == 500
        assert response.json()["error"]["type"] == "PersistenceError"
    finally:
        team_dir.chmod(0o755)


def test_sequential_requests_each_write_their_own_result(
    base_env: dict[str, str], tmp_workspace: Path
) -> None:
    responses = [answer_json("第一个答案。"), answer_json("第二个答案。")]
    with build_client(base_env, responses) as client:
        client.post("/solve", json=chat_payload("A-01"))
        client.post("/solve", json=chat_payload("A-02"))
    first = json.loads((tmp_workspace / "data" / TEAM / "A-01.json").read_text(encoding="utf-8"))
    second = json.loads((tmp_workspace / "data" / TEAM / "A-02.json").read_text(encoding="utf-8"))
    assert first["answer"] == "第一个答案。"
    assert second["answer"] == "第二个答案。"
    assert first["case_id"] == "A-01"
    assert second["case_id"] == "A-02"


def test_work_directory_is_not_polluted_by_chat(
    base_env: dict[str, str], tmp_workspace: Path
) -> None:
    with build_client(base_env, [answer_json()]) as client:
        client.post("/solve", json=chat_payload())
    assert list((tmp_workspace / "data" / TEAM / "work").iterdir()) == []


def test_assigned_questions_and_dispatch_are_never_overwritten(
    base_env: dict[str, str], tmp_workspace: Path, mounted_dispatch: None
) -> None:
    question = tmp_workspace / "data" / TEAM / "assigned" / "A-100.json"
    dispatch = tmp_workspace / "data" / TEAM / "dispatch.json"
    question_before = question.read_bytes()
    dispatch_before = dispatch.read_bytes()
    with build_client(base_env, [answer_json()]) as client:
        client.post("/solve", json=chat_payload())
    assert question.read_bytes() == question_before
    assert dispatch.read_bytes() == dispatch_before


def test_no_temp_file_is_left_behind(base_env: dict[str, str], tmp_workspace: Path) -> None:
    """连写两次同一题：原子替换不得留下 .tmp，也不得多出别的文件。"""
    with build_client(base_env, [answer_json(), answer_json()]) as client:
        client.post("/solve", json=chat_payload())
        client.post("/solve", json=chat_payload())
    names = sorted(p.name for p in (tmp_workspace / "data" / TEAM).iterdir())
    assert names == ["A-100.json", "assigned", "work"]


def test_other_team_directories_are_untouched(
    base_env: dict[str, str], tmp_workspace: Path
) -> None:
    other = tmp_workspace / "data" / "别的队"
    other.mkdir(parents=True)
    (other / "A-100.json").write_text('{"case_id": "A-100"}', encoding="utf-8")
    before = (other / "A-100.json").read_bytes()
    with build_client(base_env, [answer_json()]) as client:
        client.post("/solve", json=chat_payload())
    assert (other / "A-100.json").read_bytes() == before
    assert list(other.iterdir()) == [other / "A-100.json"]
