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


def test_conflicting_groups_prefer_the_recommended_group_without_leaking_values() -> None:
    """平台可能同时注入两组；不一致时用推荐的 CHALLENGE_*，绝不能让网关失效。"""
    env = {**CHALLENGE, **PLATFORM}
    settings = Settings.from_env(env)
    gw = settings.require_gateway()
    assert gw.family == "challenge"
    assert gw.api_key == "sk-challenge"
    assert gw.model == "model-c"
    warning = settings.gateway_config_warning or ""
    assert "sk-challenge" not in warning
    assert "sk-platform" not in warning
    # 警告必须真的点名不一致的字段
    assert "api_key" in warning
    assert "model" in warning
    # 两组 base_url 取值相同（都是 http://gw.invalid/v1），不应被列为 differing
    assert "base_url" not in warning


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


def test_session_id_never_appears_in_repr_or_str() -> None:
    """session id 也是凭据侧标识，掩码必须覆盖 repr/str 与外层 Settings。"""
    env = {**CHALLENGE, "CHALLENGE_SESSION_ID": "sess-secret-42"}
    settings = Settings.from_env(env)
    gw = settings.require_gateway()
    # 值确实被读到了（否则下面的「不出现」断言是恒真的）
    assert gw.session_id == "sess-secret-42"
    assert "sess-secret-42" not in repr(gw)
    assert "sess-secret-42" not in str(gw)
    assert "sess-secret-42" not in repr(settings)
    assert "sess-secret-42" not in str(settings)
    # 掩码写成带引号的字符串 'set'，不能裸写成内建类型名 set
    assert "session_id='set'" in repr(gw)
    assert "session_id='set'" in str(gw)


def test_session_id_is_rendered_as_none_when_absent() -> None:
    gw = Settings.from_env(CHALLENGE).require_gateway()
    assert gw.session_id is None
    assert "session_id=None" in repr(gw)
    assert "session_id='set'" not in repr(gw)


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


def test_negative_budget_is_rejected() -> None:
    env = {**CHALLENGE, "RESEARCH_CASE_BUDGET_SECONDS": "-5"}
    with pytest.raises(ConfigError, match="RESEARCH_CASE_BUDGET_SECONDS"):
        Settings.from_env(env)


def test_non_numeric_budget_is_rejected() -> None:
    env = {**CHALLENGE, "RESEARCH_CASE_BUDGET_SECONDS": "abc"}
    with pytest.raises(ConfigError, match="RESEARCH_CASE_BUDGET_SECONDS"):
        Settings.from_env(env)


@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-inf", "infinity", "1e400"])
def test_non_finite_budget_is_rejected(raw: str) -> None:
    """nan/inf 能通过 float()，且 nan 与任何数比较都为 False。

    若只做 `budget <= 0` 校验，nan 会漏过去：Deadline.remaining() 返回 nan，
    take() 的 `remaining < minimum` 恒为 False，整个每题限时守卫静默失效。
    """
    env = {**CHALLENGE, "RESEARCH_CASE_BUDGET_SECONDS": raw}
    with pytest.raises(
        ConfigError, match="RESEARCH_CASE_BUDGET_SECONDS must be a finite positive number"
    ):
        Settings.from_env(env)


def test_finite_budget_is_accepted() -> None:
    """守卫不得误伤合法值。"""
    env = {**CHALLENGE, "RESEARCH_CASE_BUDGET_SECONDS": "0.5"}
    assert Settings.from_env(env).case_budget_seconds == 0.5
    env = {**CHALLENGE, "RESEARCH_CASE_BUDGET_SECONDS": "1e6"}
    assert Settings.from_env(env).case_budget_seconds == 1e6


@pytest.mark.parametrize(
    "base_url",
    [
        "http://gw.invalid:3000/v1",
        "http://gw.invalid:3000/v1/",
        "http://gw.invalid:3000",
        "http://gw.invalid/custom/prefix/v1",
        "http://gw.invalid/custom/prefix/v1/",
        "  http://gw.invalid:3000/v1  ",
    ],
)
def test_base_url_path_is_preserved_verbatim(base_url: str) -> None:
    """不得裁剪或自猜补 /v1；唯一的加工是 strip() 去掉首尾空白。"""
    env = {**CHALLENGE, "CHALLENGE_LLM_BASE_URL": base_url}
    assert Settings.from_env(env).require_gateway().base_url == base_url.strip()


def test_from_env_without_argument_reads_os_environ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """env=None → os.environ 分支（__main__ 的便捷路径；Task 10 显式传 os.environ）。"""
    for name, value in {
        **CHALLENGE,
        "CHALLENGE_SESSION_ID": "sess-from-os-environ",
        "RESEARCH_WORKSPACE_TEAM": "os-env-team",
        "RESEARCH_WORKSPACE_ROOT": "/tmp/os-env-ws",
        "RESEARCH_PAPERS_ROOT": "/tmp/os-env-papers",
        "RESEARCH_CASE_BUDGET_SECONDS": "45",
        "AGENTHACK_LOG_LEVEL": "DEBUG",
    }.items():
        monkeypatch.setenv(name, value)
    # 避免宿主环境里恰好存在 PLATFORM_* 而触发「两组冲突」分支
    for name in ("PLATFORM_LLM_URL", "PLATFORM_API_KEY", "PLATFORM_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    gw = settings.require_gateway()
    assert gw.family == "challenge"
    assert gw.base_url == "http://gw.invalid/v1"
    assert gw.model == "model-c"
    assert gw.session_id == "sess-from-os-environ"
    assert settings.gateway_config_error is None
    assert settings.team == "os-env-team"
    assert settings.workspace_root == Path("/tmp/os-env-ws")
    assert settings.papers_root == Path("/tmp/os-env-papers")
    assert settings.case_budget_seconds == 45.0
    assert settings.log_level == "DEBUG"


def test_from_env_without_argument_reports_missing_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一分支在配置缺失时也不抛网关异常，只写 gateway_config_error。"""
    for name in (
        "CHALLENGE_LLM_BASE_URL",
        "CHALLENGE_LLM_API_KEY",
        "CHALLENGE_LLM_MODEL",
        "CHALLENGE_SESSION_ID",
        "PLATFORM_LLM_URL",
        "PLATFORM_API_KEY",
        "PLATFORM_LLM_MODEL",
        "RESEARCH_CASE_BUDGET_SECONDS",
        "RESEARCH_WORKSPACE_TEAM",
        "RESEARCH_WORKSPACE_ROOT",
        "RESEARCH_PAPERS_ROOT",
        "AGENTHACK_LOG_LEVEL",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    assert settings.gateway is None
    assert "CHALLENGE" in (settings.gateway_config_error or "")
    assert settings.case_budget_seconds == 180.0
    assert settings.workspace_root == Path("/output")
    assert settings.papers_root == Path("/data/papers")
    assert settings.log_level == "INFO"
    assert settings.team == ""


def test_trailing_slash_difference_alone_is_not_a_group_conflict() -> None:
    """同一网关写成 /v1 与 /v1/ 是等价写法；误判成冲突会让所有模型题失败。"""
    env = {
        **CHALLENGE,
        "PLATFORM_LLM_URL": "http://gw.invalid/v1/",
        "PLATFORM_API_KEY": "sk-challenge",
        "PLATFORM_LLM_MODEL": "model-c",
    }
    settings = Settings.from_env(env)
    assert settings.gateway_config_error is None
    assert settings.require_gateway().family == "challenge"


def test_different_base_urls_prefer_the_recommended_group() -> None:
    env = {
        **CHALLENGE,
        "PLATFORM_LLM_URL": "http://other.invalid/v1",
        "PLATFORM_API_KEY": "sk-challenge",
        "PLATFORM_LLM_MODEL": "model-c",
    }
    settings = Settings.from_env(env)
    assert settings.require_gateway().base_url == "http://gw.invalid/v1"
    assert "base_url" in (settings.gateway_config_warning or "")


def test_team_name_is_the_local_fallback_for_the_output_directory() -> None:
    """组委会口径：目录取 RESEARCH_WORKSPACE_TEAM，其次 TEAM_NAME。"""
    settings = Settings.from_env({**CHALLENGE, "TEAM_NAME": "local-team-name"})
    assert settings.team == "local-team-name"
    assert Settings.from_env(CHALLENGE).team == ""


def test_team_id_is_not_used_as_the_output_directory() -> None:
    """TEAM_ID 只用于遥测身份；拿它当目录会写到平台不读取的路径。"""
    assert Settings.from_env({**CHALLENGE, "TEAM_ID": "platform-team-id"}).team == ""


def test_explicit_team_variable_wins_over_team_name() -> None:
    settings = Settings.from_env(
        {**CHALLENGE, "RESEARCH_WORKSPACE_TEAM": "explicit-team", "TEAM_NAME": "local-team"}
    )
    assert settings.team == "explicit-team"
