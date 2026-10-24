"""HTTP 合同测试：五类路由、响应字段白名单、错误映射、/health 形状。

自建测试材料，非官方题目，不含正式答案。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from research_assistant.api import create_app
from research_assistant.errors import GatewayError
from research_assistant.runtime.gateway import FakeGateway
from research_assistant.runtime.settings import Settings

TEAM = "测试队伍 A+1"

FIVE_FUNCTIONS = {
    "A-01": ("Chat", "Chat"),
    "B-01": ("Literature_Retrieval", "Literature_Retrieval"),
    "C-01": ("Survey", "Survey"),
    "D-01": ("Reproduction", "Reproduction"),
    "E-01": ("Idea", "Idea"),
}


def answer_json() -> str:
    return json.dumps(
        {"answer": "光照强度影响光合速率。", "clarifying_question": "研究对象是哪种室内植物？"},
        ensure_ascii=False,
    )


def payload(case_id: str = "A-01", **overrides: Any) -> dict[str, Any]:
    # 未在表中的 case_id（如非法编号 A-1000、三位后缀 A-100）按 Chat 处理，
    # 这样非法编号的用例才能走到 Task.parse 的校验分支而不是先 KeyError。
    function, schema_ref = FIVE_FUNCTIONS.get(case_id, ("Chat", "Chat"))
    base: dict[str, Any] = {
        "case_id": case_id,
        "function": function,
        "title": f"自建测试 {case_id}",
        "input": "解释关系并提一个限定问题。",
        "materials": [],
        "constraints": [],
        "expected_output": "解释关系并提问。",
        "output_schema_ref": schema_ref,
    }
    base.update(overrides)
    return base


@pytest.fixture
def client(base_env: dict[str, str]) -> Iterator[TestClient]:
    """两条脚本响应：够跑两次独立请求，用于验证不跨题串状态。"""
    app = create_app(
        Settings.from_env(base_env), gateway=FakeGateway([answer_json(), answer_json()])
    )
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def bare_client(base_env: dict[str, str]) -> Iterator[TestClient]:
    """网关未配置的应用，用于验证配置错误通道。"""
    env = {k: v for k, v in base_env.items() if not k.startswith("CHALLENGE_LLM")}
    app = create_app(Settings.from_env(env), gateway=None)
    with TestClient(app) as test_client:
        yield test_client


def build_client(base_env: dict[str, str], responses: list[Any]) -> TestClient:
    return TestClient(create_app(Settings.from_env(base_env), gateway=FakeGateway(responses)))


# --- /health ---


def test_health_returns_200_with_a_small_json_body(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["workspace_writable"] is True
    assert body["rules_version"] == "research-assistant-objective-v3"
    assert body["gateway_configured"] is True
    assert body["team_configured"] is True


def test_health_does_not_call_the_model(base_env: dict[str, str]) -> None:
    fake = FakeGateway([answer_json()])
    with TestClient(create_app(Settings.from_env(base_env), gateway=fake)) as test_client:
        test_client.get("/health")
        test_client.get("/health")
    assert fake.calls == []


def test_health_reports_unconfigured_gateway_but_still_200(bare_client: TestClient) -> None:
    response = bare_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["gateway_configured"] is False
    assert body["status"] == "ok"


def test_health_reports_missing_dispatch(base_env: dict[str, str]) -> None:
    with build_client(base_env, []) as test_client:
        assert test_client.get("/health").json()["dispatch_mounted"] is False


def test_health_reports_mounted_dispatch(base_env: dict[str, str], tmp_workspace: Path) -> None:
    from tests.fixtures.dispatch_v3 import build_v3_dispatch, write_dispatch, write_questions

    record = build_v3_dispatch(TEAM)
    write_questions(tmp_workspace, TEAM, record)
    write_dispatch(tmp_workspace, TEAM, record)
    with build_client(base_env, []) as test_client:
        assert test_client.get("/health").json()["dispatch_mounted"] is True


def test_health_503_when_workspace_root_is_not_writable(
    base_env: dict[str, str], tmp_path: Path
) -> None:
    blocked = tmp_path / "readonly-root"
    blocked.mkdir()
    blocked.chmod(0o555)
    env = {**base_env, "RESEARCH_WORKSPACE_ROOT": str(blocked)}
    try:
        with build_client(env, []) as test_client:
            assert test_client.get("/health").status_code == 503
    finally:
        blocked.chmod(0o755)


def test_health_leaves_no_probe_file_behind(base_env: dict[str, str], tmp_workspace: Path) -> None:
    with build_client(base_env, []) as test_client:
        test_client.get("/health")
    assert not (tmp_workspace / ".health-probe").exists()


# --- /solve 正常路径 ---


def test_solve_returns_the_chat_answer(client: TestClient) -> None:
    response = client.post("/solve", json=payload())
    assert response.status_code == 200
    assert response.json() == {
        "case_id": "A-01",
        "answer": "光照强度影响光合速率。",
        "clarifying_question": "研究对象是哪种室内植物？",
    }


def test_solve_response_has_exactly_the_allowed_top_level_keys(client: TestClient) -> None:
    body = client.post("/solve", json=payload()).json()
    assert set(body) == {"case_id", "answer", "clarifying_question"}


def test_solve_response_is_json_without_fences_or_prefix(client: TestClient) -> None:
    response = client.post("/solve", json=payload())
    assert response.headers["content-type"].startswith("application/json")
    text = response.text
    assert text.startswith("{")
    assert text.endswith("}")
    assert "```" not in text


def test_case_id_is_returned_verbatim(client: TestClient) -> None:
    assert client.post("/solve", json=payload("A-100")).json()["case_id"] == "A-100"


def test_extra_input_fields_are_never_forwarded(client: TestClient) -> None:
    response = client.post("/solve", json=payload(score=100, function_hint="x"))
    assert set(response.json()) == {"case_id", "answer", "clarifying_question"}


def test_solve_does_not_process_the_whole_round(client: TestClient, tmp_workspace: Path) -> None:
    """一次请求只解当前题，不得写出其他题的结果。"""
    client.post("/solve", json=payload())
    written = sorted(p.name for p in (tmp_workspace / "data" / TEAM).glob("*.json"))
    assert written == ["A-01.json"]


def test_two_requests_are_independent(client: TestClient) -> None:
    """赛题一没有 task_id 幂等语义；两次请求各自独立求解。"""
    first = client.post("/solve", json=payload())
    second = client.post("/solve", json=payload())
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()


# --- /solve 路由 ---


def test_reproduction_without_mounted_materials_returns_a_legal_answer(
    base_env: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """D 类材料缺失是 200 + materials_missing 合法答卷，不是 5xx，也不是空壳。"""
    monkeypatch.setenv("RESEARCH_EXPERIMENT_ROOT", str(tmp_path / "no-experiments"))
    data = payload("D-01")
    data["input"] = (
        "核心指标恰好三项：name=Macro-F1，unit=分数；name=Accuracy，unit=%；"
        "name=运行时间，unit=秒。"
    )
    with build_client(base_env, []) as test_client:
        response = test_client.post("/solve", json=data)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"case_id", "status", "metrics", "figures"}
    assert body["status"] == "materials_missing"
    assert body["figures"] == []
    assert [m["name"] for m in body["metrics"]] == ["Macro-F1", "Accuracy", "运行时间"]
    assert [m["unit"] for m in body["metrics"]] == ["分数", "%", "秒"]
    assert all(m["observed"] is None for m in body["metrics"])
    assert all(m["relative_error"] is None for m in body["metrics"])


def test_report_category_without_gateway_reports_config_error(
    base_env: dict[str, str], papers_root: Path
) -> None:
    """材料齐备但网关未配置时，C 类报 500 ConfigError，而不是返回伪装成功的空壳报告。"""
    from tests.fixtures.papers import write_paper

    name = "城市绿地遥感_2024.pdf"
    write_paper(
        papers_root,
        name,
        title="Remote Sensing of Urban Green Space",
        body_paragraphs=["Sufficiently long body text so the document counts as usable."],
    )
    env = {k: v for k, v in base_env.items() if not k.startswith("CHALLENGE_LLM")}
    with TestClient(create_app(Settings.from_env(env), gateway=None)) as test_client:
        response = test_client.post("/solve", json=payload("C-01", materials=[name]))
    assert response.status_code == 500, response.text
    assert response.json()["error"]["type"] == "ConfigError"


def test_report_category_without_materials_reports_422_before_touching_the_gateway(
    base_env: dict[str, str],
) -> None:
    """材料校验先于模型调用：没有可用材料时是 422，且不消耗任何模型预算。"""
    env = {k: v for k, v in base_env.items() if not k.startswith("CHALLENGE_LLM")}
    with TestClient(create_app(Settings.from_env(env), gateway=None)) as test_client:
        response = test_client.post("/solve", json=payload("C-01"))
    assert response.status_code == 422
    assert response.json()["error"]["type"] == "MaterialUnavailableError"


def test_retrieval_with_no_materials_returns_an_empty_papers_array(
    base_env: dict[str, str],
) -> None:
    """B 类没有可用材料时，空数组是合法答案，不得报 501 也不得凑篇数。"""
    with build_client(base_env, []) as test_client:
        response = test_client.post("/solve", json=payload("B-01"))
    assert response.status_code == 200
    assert response.json() == {"case_id": "B-01", "papers": []}


# --- /solve 错误映射 ---


@pytest.mark.parametrize(
    "field",
    [
        "case_id",
        "function",
        "title",
        "input",
        "materials",
        "constraints",
        "expected_output",
        "output_schema_ref",
    ],
)
def test_missing_field_returns_422(client: TestClient, field: str) -> None:
    data = payload()
    del data[field]
    response = client.post("/solve", json=data)
    assert response.status_code == 422
    assert response.json()["error"]["type"] == "TaskContractError"


def test_function_and_schema_mismatch_returns_422(client: TestClient) -> None:
    response = client.post("/solve", json=payload(output_schema_ref="Survey"))
    assert response.status_code == 422
    assert "output_schema_ref" in response.json()["error"]["message"]


def test_invalid_case_id_returns_422(client: TestClient) -> None:
    assert client.post("/solve", json=payload(case_id="A-1000")).status_code == 422


def test_malformed_json_body_returns_422(client: TestClient) -> None:
    response = client.post(
        "/solve", content=b"{not json", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["type"] == "TaskContractError"


def test_json_array_body_returns_422(client: TestClient) -> None:
    assert client.post("/solve", json=[payload()]).status_code == 422


def test_null_materials_returns_422(client: TestClient) -> None:
    assert client.post("/solve", json=payload(materials=None)).status_code == 422


def test_material_escape_returns_403(base_env: dict[str, str]) -> None:
    with build_client(base_env, [answer_json()]) as test_client:
        response = test_client.post("/solve", json=payload(materials=["../secret.pdf"]))
    assert response.status_code == 403
    assert response.json()["error"]["type"] == "PathAuthorizationError"


def test_missing_material_returns_422(base_env: dict[str, str]) -> None:
    with build_client(base_env, [answer_json()]) as test_client:
        response = test_client.post("/solve", json=payload(materials=["不存在.pdf"]))
    assert response.status_code == 422
    assert response.json()["error"]["type"] == "MaterialUnavailableError"


def test_missing_team_returns_500(base_env: dict[str, str]) -> None:
    env = {k: v for k, v in base_env.items() if k != "RESEARCH_WORKSPACE_TEAM"}
    with build_client(env, [answer_json()]) as test_client:
        response = test_client.post("/solve", json=payload())
    assert response.status_code == 500
    assert response.json()["error"]["type"] == "ConfigError"


def test_unconfigured_gateway_returns_500_on_chat(bare_client: TestClient) -> None:
    response = bare_client.post("/solve", json=payload())
    assert response.status_code == 500
    body = response.json()["error"]
    assert body["type"] == "ConfigError"
    assert body["case_id"] == "A-01"


def test_gateway_failure_returns_502(base_env: dict[str, str]) -> None:
    with build_client(base_env, [GatewayError("upstream 502")]) as test_client:
        response = test_client.post("/solve", json=payload())
    assert response.status_code == 502
    assert response.json()["error"]["type"] == "GatewayError"


def test_non_json_model_output_returns_502(base_env: dict[str, str]) -> None:
    with build_client(base_env, ["不是 JSON 的自由文本"]) as test_client:
        assert test_client.post("/solve", json=payload()).status_code == 502


def test_budget_exhaustion_returns_504(base_env: dict[str, str]) -> None:
    env = {**base_env, "RESEARCH_CASE_BUDGET_SECONDS": "0.001"}
    with build_client(env, [answer_json()]) as test_client:
        response = test_client.post("/solve", json=payload())
    assert response.status_code == 504
    assert response.json()["error"]["type"] == "BudgetExhausted"


def test_corrupt_dispatch_is_logged_and_solving_continues(
    base_env: dict[str, str], tmp_workspace: Path
) -> None:
    """派题记录损坏只告警：平台已校验过，不能因此拒答一道合法题。"""
    (tmp_workspace / "data" / TEAM / "dispatch.json").write_text("{bad", encoding="utf-8")
    with build_client(base_env, [answer_json()]) as test_client:
        response = test_client.post("/solve", json=payload())
    assert response.status_code == 200


def test_error_body_never_leaks_the_api_key(base_env: dict[str, str]) -> None:
    with build_client(base_env, [GatewayError("boom")]) as test_client:
        response = test_client.post("/solve", json=payload())
    assert base_env["CHALLENGE_LLM_API_KEY"] not in response.text


def test_error_body_shape_is_stable(client: TestClient) -> None:
    response = client.post("/solve", json=payload(case_id="A-1000"))
    assert set(response.json()) == {"error"}
    assert set(response.json()["error"]) == {"type", "message", "case_id"}


# --- 方法与环境 ---


def test_get_on_solve_is_not_allowed(client: TestClient) -> None:
    assert client.get("/solve").status_code == 405


def test_unknown_path_returns_404(client: TestClient) -> None:
    assert client.get("/nope").status_code == 404


def test_session_header_is_accepted_and_does_not_change_the_answer(
    base_env: dict[str, str],
) -> None:
    with build_client(base_env, [answer_json()]) as test_client:
        plain = test_client.post("/solve", json=payload())
    with build_client(base_env, [answer_json()]) as test_client:
        with_header = test_client.post("/solve", json=payload(), headers={"X-Session-ID": "sess-1"})
    assert plain.json() == with_header.json()
