from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_assistant.contracts.dispatch import DispatchRecord
from research_assistant.contracts.task import Task
from research_assistant.errors import (
    ConfigError,
    PathAuthorizationError,
)
from research_assistant.runtime.context import SolveContext, load_task_context
from research_assistant.runtime.gateway import FakeGateway, UnavailableGateway
from research_assistant.runtime.settings import Settings
from tests.fixtures.dispatch_v3 import (
    build_v3_dispatch,
    question_payload,
    write_dispatch,
    write_questions,
)

TEAM = "测试队伍 A+1"


def task(**overrides: object) -> Task:
    payload: dict[str, object] = {
        "case_id": "A-01",
        "function": "Chat",
        "title": "光照与生长",
        "input": "解释关系并提一个限定问题。",
        "materials": [],
        "constraints": [],
        "expected_output": "解释关系并提问。",
        "output_schema_ref": "Chat",
    }
    payload.update(overrides)
    return Task.parse(payload)


def settings_for(base_env: dict[str, str], **overrides: str) -> Settings:
    return Settings.from_env({**base_env, **overrides})


def test_context_carries_team_paths_budget_and_log(base_env: dict[str, str]) -> None:
    context = load_task_context(task(), settings_for(base_env), gateway=FakeGateway(["x"]))
    assert isinstance(context, SolveContext)
    assert context.case_id == "A-01"
    assert context.team == TEAM
    assert context.output_dir.name == TEAM
    assert context.work_dir == context.workspace.work_dir("A-01")
    assert context.deadline.budget_seconds == 180.0
    assert context.settings.gateway is not None
    assert context.dispatch is None


def test_allowed_materials_are_frozen(base_env: dict[str, str], papers_root: Path) -> None:
    (papers_root / "光照 研究+v1.pdf").write_text("body", encoding="utf-8")
    context = load_task_context(
        task(materials=["光照 研究+v1.pdf"]), settings_for(base_env), gateway=FakeGateway(["x"])
    )
    assert context.allowed_materials == frozenset({"光照 研究+v1.pdf"})
    with pytest.raises(AttributeError):
        context.allowed_materials.add("x")  # type: ignore[attr-defined]


def test_missing_material_file_is_skipped(base_env: dict[str, str], papers_root: Path) -> None:
    """平台可能没挂论文库；缺失材料只告警，不能拦下整道题。"""
    context = load_task_context(
        task(materials=["不存在.pdf"]), settings_for(base_env), gateway=FakeGateway(["x"])
    )
    assert context.materials == ()
    assert context.allowed_materials == frozenset()


def test_material_escaping_papers_root_raises_path_authorization(base_env: dict[str, str]) -> None:
    with pytest.raises(PathAuthorizationError):
        load_task_context(
            task(materials=["../secret.pdf"]), settings_for(base_env), gateway=FakeGateway(["x"])
        )


def test_empty_team_raises_config_error(base_env: dict[str, str]) -> None:
    env = {k: v for k, v in base_env.items() if k != "RESEARCH_WORKSPACE_TEAM"}
    with pytest.raises(ConfigError, match="RESEARCH_WORKSPACE_TEAM"):
        load_task_context(task(), Settings.from_env(env), gateway=FakeGateway(["x"]))


def test_dispatch_is_loaded_and_verified_when_mounted(
    base_env: dict[str, str], tmp_workspace: Path
) -> None:
    record = build_v3_dispatch(TEAM)
    write_questions(tmp_workspace, TEAM, record)
    write_dispatch(tmp_workspace, TEAM, record)
    context = load_task_context(
        task(case_id="A-100"), settings_for(base_env), gateway=FakeGateway(["x"])
    )
    assert isinstance(context.dispatch, DispatchRecord)
    assert context.dispatch.dispatch_id == "d-selftest-001"
    assert context.dispatch.is_legacy is False


def test_dispatch_team_mismatch_is_logged_not_fatal(
    base_env: dict[str, str], tmp_workspace: Path
) -> None:
    """派题记录只作提示；平台已校验一致性，不能因它拒绝一道合法题。"""
    record = build_v3_dispatch("别的队")
    write_dispatch(tmp_workspace, TEAM, record)
    context = load_task_context(task(), settings_for(base_env), gateway=FakeGateway(["x"]))
    assert context.dispatch is None


def test_corrupt_dispatch_is_logged_not_fatal(
    base_env: dict[str, str], tmp_workspace: Path
) -> None:
    (tmp_workspace / "data" / TEAM / "dispatch.json").write_text("{bad", encoding="utf-8")
    context = load_task_context(task(), settings_for(base_env), gateway=FakeGateway(["x"]))
    assert context.dispatch is None


def test_single_mode_works_when_only_the_current_question_is_mounted(
    base_env: dict[str, str], tmp_workspace: Path
) -> None:
    """组委会清单：Single 只读取当前题。其余 assigned 文件不存在也必须能解题。"""
    record = build_v3_dispatch(TEAM)
    write_dispatch(tmp_workspace, TEAM, record)
    assigned = tmp_workspace / "data" / TEAM / "assigned"
    assigned.mkdir(parents=True, exist_ok=True)
    (assigned / "A-100.json").write_text(
        json.dumps(question_payload("A-100"), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    context = load_task_context(
        task(case_id="A-100"), settings_for(base_env), gateway=FakeGateway(["x"])
    )
    assert isinstance(context.dispatch, DispatchRecord)


def test_tampered_current_question_is_logged_not_fatal(
    base_env: dict[str, str], tmp_workspace: Path
) -> None:
    record = build_v3_dispatch(TEAM)
    write_dispatch(tmp_workspace, TEAM, record)
    assigned = tmp_workspace / "data" / TEAM / "assigned"
    assigned.mkdir(parents=True, exist_ok=True)
    (assigned / "A-100.json").write_text('{"tampered": true}', encoding="utf-8")
    context = load_task_context(
        task(case_id="A-100"), settings_for(base_env), gateway=FakeGateway(["x"])
    )
    assert isinstance(context.dispatch, DispatchRecord)


def test_absent_dispatch_is_not_fatal(base_env: dict[str, str]) -> None:
    """外部平台可能逐题直传 JSON 而不挂载派题文件；不得伪造记录。"""
    context = load_task_context(task(), settings_for(base_env), gateway=FakeGateway(["x"]))
    assert context.dispatch is None


def test_gateway_defaults_to_unavailable_when_not_configured(
    tmp_workspace: Path, papers_root: Path
) -> None:
    env = {
        "RESEARCH_WORKSPACE_TEAM": TEAM,
        "RESEARCH_WORKSPACE_ROOT": str(tmp_workspace),
        "RESEARCH_PAPERS_ROOT": str(papers_root),
    }
    context = load_task_context(task(), Settings.from_env(env))
    assert isinstance(context.gateway, UnavailableGateway)


def test_unavailable_gateway_raises_config_error_on_use(
    tmp_workspace: Path, papers_root: Path
) -> None:
    env = {
        "RESEARCH_WORKSPACE_TEAM": TEAM,
        "RESEARCH_WORKSPACE_ROOT": str(tmp_workspace),
        "RESEARCH_PAPERS_ROOT": str(papers_root),
    }
    context = load_task_context(task(), Settings.from_env(env))
    from research_assistant.runtime.gateway import Message

    with pytest.raises(ConfigError):
        context.gateway.complete([Message(role="user", content="u")], context=context, stage="s")


def test_injected_gateway_is_used_verbatim(base_env: dict[str, str]) -> None:
    fake = FakeGateway(["hi"])
    context = load_task_context(task(), settings_for(base_env), gateway=fake)
    assert context.gateway is fake


def test_log_is_bound_to_case_and_team(base_env: dict[str, str]) -> None:
    context = load_task_context(task(), settings_for(base_env), gateway=FakeGateway(["x"]))
    assert context.log is not None


def test_budget_is_configurable(base_env: dict[str, str]) -> None:
    context = load_task_context(
        task(),
        settings_for(base_env, RESEARCH_CASE_BUDGET_SECONDS="42"),
        gateway=FakeGateway(["x"]),
    )
    assert context.deadline.budget_seconds == 42.0
