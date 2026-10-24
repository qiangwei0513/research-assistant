"""真实网关 A 类端到端测试。

需要仓库根下被 gitignore 的 .env.local（含真实凭据）与网络。
默认被 pyproject 的 addopts `-m 'not live'` 排除；运行方式：uv run pytest -m live -v

证据级别为「本地测试 + 真实模型调用」。这既不等于镜像验证，也不等于平台验证：
线上注入的变量族、会话头要求与模型名仍可能与开发网关不同（见总纲 Q01）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from research_assistant.api import create_app
from research_assistant.runtime.settings import Settings

pytestmark = pytest.mark.live

ENV_FILE = Path(__file__).resolve().parents[2] / ".env.local"
TEAM = "live-smoke"

CHAT_TASK = {
    "case_id": "A-99",
    "function": "Chat",
    "title": "室内植物光照与生长：科研对话",
    "input": (
        "我想研究不同光照条件对室内植物生长速度的影响。请解释光照强度、每日照射时长和"
        "生长速度之间可能有什么关系，并问我一个最关键的研究范围限定问题。"
    ),
    "materials": [],
    "constraints": [],
    "expected_output": "解释光照条件与植物生长速度的可能关系，并提出一个研究范围限定问题。",
    "output_schema_ref": "Chat",
}


def load_local_env() -> dict[str, str]:
    if not ENV_FILE.is_file():
        pytest.skip(f"{ENV_FILE.name} 不存在；live 测试需要真实凭据")
    env: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        env[key.strip()] = value.strip()
    return env


@pytest.fixture
def live_settings(tmp_path: Path) -> Settings:
    """把真实凭据接到一个临时工作区上；凭据本身不进日志、不进断言。"""
    settings = Settings.from_env({**load_local_env(), "RESEARCH_WORKSPACE_TEAM": TEAM})
    if settings.gateway is None:
        pytest.skip(f"没有完整的网关变量组：{settings.gateway_config_error}")
    workspace = tmp_path / "output"
    (workspace / "data" / TEAM).mkdir(parents=True)
    (tmp_path / "papers").mkdir()
    return Settings(
        gateway=settings.gateway,
        gateway_config_error=settings.gateway_config_error,
        team=TEAM,
        workspace_root=workspace,
        case_budget_seconds=settings.case_budget_seconds,
        papers_root=tmp_path / "papers",
        log_level=settings.log_level,
    )


def test_live_chat_returns_a_schema_valid_answer(live_settings: Settings) -> None:
    with TestClient(create_app(live_settings)) as client:
        response = client.post("/solve", json=CHAT_TASK)

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"case_id", "answer", "clarifying_question"}
    assert body["case_id"] == "A-99"
    assert isinstance(body["answer"], str) and len(body["answer"].strip()) >= 20
    assert isinstance(body["clarifying_question"], str) and body["clarifying_question"].strip()

    written = (live_settings.workspace_root / "data" / TEAM / "A-99.json").read_bytes()
    assert response.content == written


def test_live_answer_addresses_the_light_variables(live_settings: Settings) -> None:
    """内容相关性只做弱断言：官方档位由裁判判定，本地不得声称算出了分数。"""
    with TestClient(create_app(live_settings)) as client:
        body = client.post("/solve", json=CHAT_TASK).json()
    text = body["answer"] + body["clarifying_question"]
    assert any(word in text for word in ("光照", "光", "light")), text[:200]


def test_live_answer_is_valid_utf8_json_without_escapes(live_settings: Settings) -> None:
    with TestClient(create_app(live_settings)) as client:
        response = client.post("/solve", json=CHAT_TASK)
    # ensure_ascii=False：中文必须是原字节
    assert b"\\u" not in response.content
    assert json.loads(response.content.decode("utf-8"))["case_id"] == "A-99"
