from __future__ import annotations

from pathlib import Path

import pytest

from research_assistant.errors import ConfigError
from research_assistant.runtime.settings import Settings

CHALLENGE = {
    "CHALLENGE_LLM_BASE_URL": "http://gw.invalid/v1",
    "CHALLENGE_LLM_API_KEY": "sk-challenge",
    "CHALLENGE_LLM_MODEL": "model-c",
}
PLATFORM = {
    "PLATFORM_LLM_URL": "http://gw.invalid/v1",
    "PLATFORM_API_KEY": "sk-platform",
    "PLATFORM_LLM_MODEL": "model-p",
}


def test_challenge_group_is_used_when_complete() -> None:
    settings = Settings.from_env(CHALLENGE)
    gw = settings.require_gateway()
    assert gw.family == "challenge"
    assert gw.base_url == "http://gw.invalid/v1"
    assert gw.model == "model-c"
    assert gw.session_id is None
    assert settings.gateway_config_error is None


def test_platform_group_is_used_when_challenge_absent() -> None:
    gw = Settings.from_env(PLATFORM).require_gateway()
    assert gw.family == "platform"
    assert gw.base_url == "http://gw.invalid/v1"
    assert gw.model == "model-p"


def test_session_id_is_picked_up_from_challenge_group() -> None:
    env = {**CHALLENGE, "CHALLENGE_SESSION_ID": "sess-42"}
    assert Settings.from_env(env).require_gateway().session_id == "sess-42"


def test_challenge_wins_when_both_groups_are_identical() -> None:
    env = {
        "CHALLENGE_LLM_BASE_URL": "http://same/v1",
        "CHALLENGE_LLM_API_KEY": "sk-same",
        "CHALLENGE_LLM_MODEL": "m",
        "PLATFORM_LLM_URL": "http://same/v1",
        "PLATFORM_API_KEY": "sk-same",
        "PLATFORM_LLM_MODEL": "m",
    }
    settings = Settings.from_env(env)
    assert settings.require_gateway().family == "challenge"
    assert settings.gateway_config_error is None


def test_conflicting_groups_raise_config_error_without_leaking_values() -> None:
    env = {**CHALLENGE, **PLATFORM}
    settings = Settings.from_env(env)
    with pytest.raises(ConfigError) as excinfo:
        settings.require_gateway()
    message = str(excinfo.value)
    assert "sk-challenge" not in message
    assert "sk-platform" not in message
    assert settings.gateway is None


def test_incomplete_challenge_group_falls_through_to_platform() -> None:
    env = {"CHALLENGE_LLM_BASE_URL": "http://gw.invalid/v1", **PLATFORM}
    assert Settings.from_env(env).require_gateway().family == "platform"


def test_empty_string_treated_as_missing() -> None:
    env = {**CHALLENGE, "CHALLENGE_LLM_API_KEY": "   "}
    settings = Settings.from_env(env)
    assert settings.gateway is None
    assert settings.gateway_config_error is not None


def test_no_group_configured_leaves_gateway_none_and_reports_reason() -> None:
    settings = Settings.from_env({})
    assert settings.gateway is None
    assert "CHALLENGE" in (settings.gateway_config_error or "")
    with pytest.raises(ConfigError):
        settings.require_gateway()


def test_per_variable_mixing_is_forbidden() -> None:
    """只有 URL 来自 CHALLENGE、key 来自 PLATFORM 时不得拼出一个可用配置。"""
    env = {
        "CHALLENGE_LLM_BASE_URL": "http://gw.invalid/v1",
        "PLATFORM_API_KEY": "sk-platform",
        "PLATFORM_LLM_MODEL": "model-p",
    }
    settings = Settings.from_env(env)
    # PLATFORM 组缺 URL，CHALLENGE 组缺 key 与 model → 两组都不齐备
    assert settings.gateway is None
    with pytest.raises(ConfigError):
        settings.require_gateway()


def test_api_key_never_appears_in_repr_or_str() -> None:
    gw = Settings.from_env(CHALLENGE).require_gateway()
    assert "sk-challenge" not in repr(gw)
    assert "sk-challenge" not in str(gw)
    assert "sk-challenge" not in repr(Settings.from_env(CHALLENGE))


def test_workspace_and_team_defaults() -> None:
    settings = Settings.from_env(CHALLENGE)
    assert settings.team == ""
    assert settings.workspace_root == Path("/output")
    assert settings.papers_root == Path("/data/papers")
    assert settings.case_budget_seconds == 180.0
    assert settings.log_level == "INFO"


def test_workspace_overrides_are_honoured() -> None:
    env = {
        **CHALLENGE,
        "RESEARCH_WORKSPACE_TEAM": "测试队伍 A+1",
        "RESEARCH_WORKSPACE_ROOT": "/tmp/ws",
        "RESEARCH_PAPERS_ROOT": "/tmp/papers",
        "RESEARCH_CASE_BUDGET_SECONDS": "90",
        "AGENTHACK_LOG_LEVEL": "DEBUG",
    }
    settings = Settings.from_env(env)
    assert settings.team == "测试队伍 A+1"
    assert settings.workspace_root == Path("/tmp/ws")
    assert settings.papers_root == Path("/tmp/papers")
    assert settings.case_budget_seconds == 90.0
    assert settings.log_level == "DEBUG"


def test_non_positive_budget_is_rejected() -> None:
    env = {**CHALLENGE, "RESEARCH_CASE_BUDGET_SECONDS": "0"}
    with pytest.raises(ConfigError, match="RESEARCH_CASE_BUDGET_SECONDS"):
        Settings.from_env(env)


def test_non_numeric_budget_is_rejected() -> None:
    env = {**CHALLENGE, "RESEARCH_CASE_BUDGET_SECONDS": "abc"}
    with pytest.raises(ConfigError, match="RESEARCH_CASE_BUDGET_SECONDS"):
        Settings.from_env(env)


def test_base_url_path_is_preserved_verbatim() -> None:
    """不得裁剪或自猜补 /v1。"""
    env = {**CHALLENGE, "CHALLENGE_LLM_BASE_URL": "http://gw.invalid:3000/custom/prefix/v1"}
    assert Settings.from_env(env).require_gateway().base_url == (
        "http://gw.invalid:3000/custom/prefix/v1"
    )
