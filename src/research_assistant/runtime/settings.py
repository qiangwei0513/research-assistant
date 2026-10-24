"""运行时配置适配器。

两组互斥的 Gateway 变量写法按「整组」探测，禁止逐变量混合 fallback。
from_env 不抛网关相关异常，以便 /health 在配置缺失时仍能返回 2xx。
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from research_assistant.errors import ConfigError

# 耦合约定：下面两组元组的第一个元素会被当作关键字参数解包进 GatewaySettings
# （GatewaySettings(family=..., session_id=..., **challenge)），因此必须与 dataclass 字段名逐字相同；
# 同时冲突分支用 challenge 组的键去索引 platform[k]，所以两组元组的键名也必须完全一致。
# 改名时请同步修改两处，否则会退化成运行期 TypeError/KeyError。
CHALLENGE_GROUP: tuple[tuple[str, str], ...] = (
    ("base_url", "CHALLENGE_LLM_BASE_URL"),
    ("api_key", "CHALLENGE_LLM_API_KEY"),
    ("model", "CHALLENGE_LLM_MODEL"),
)
PLATFORM_GROUP: tuple[tuple[str, str], ...] = (
    ("base_url", "PLATFORM_LLM_URL"),
    ("api_key", "PLATFORM_API_KEY"),
    ("model", "PLATFORM_LLM_MODEL"),
)
CHALLENGE_SESSION_VAR = "CHALLENGE_SESSION_ID"

DEFAULT_WORKSPACE_ROOT = Path("/output")
DEFAULT_PAPERS_ROOT = Path("/data/papers")
DEFAULT_CASE_BUDGET_SECONDS = 180.0


@dataclass(frozen=True)
class GatewaySettings:
    family: Literal["challenge", "platform"]
    base_url: str
    api_key: str
    model: str
    session_id: str | None = None

    def __repr__(self) -> str:
        return (
            f"GatewaySettings(family={self.family!r}, base_url={self.base_url!r}, "
            f"model={self.model!r}, api_key='***', "
            f"session_id={('set' if self.session_id else None)!r})"
        )

    __str__ = __repr__


def _clean(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _read_group(
    env: Mapping[str, str], group: tuple[tuple[str, str], ...]
) -> dict[str, str] | None:
    values: dict[str, str] = {}
    for key, var in group:
        cleaned = _clean(env, var)
        if cleaned is None:
            return None
        values[key] = cleaned
    return values


def _comparable(key: str, value: str) -> str:
    """比较两组的等价性时，只把 base_url 末尾的 `/` 视为无意义差异。

    否则同一个网关写成 http://x/v1 与 http://x/v1/ 会被判成「两组冲突」而整个
    禁用网关——这会让所有需要模型的题直接失败。
    """
    return value.rstrip("/") if key == "base_url" else value


@dataclass(frozen=True)
class Settings:
    gateway: GatewaySettings | None
    gateway_config_error: str | None
    team: str
    workspace_root: Path
    case_budget_seconds: float
    gateway_config_warning: str | None = None
    papers_root: Path = DEFAULT_PAPERS_ROOT
    log_level: str = "INFO"

    def require_gateway(self) -> GatewaySettings:
        if self.gateway is None:
            raise ConfigError(
                self.gateway_config_error
                or "model gateway is not configured; expected a complete CHALLENGE_LLM_* "
                "or PLATFORM_LLM_* group"
            )
        return self.gateway

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source: Mapping[str, str] = os.environ if env is None else env

        challenge = _read_group(source, CHALLENGE_GROUP)
        platform = _read_group(source, PLATFORM_GROUP)
        gateway: GatewaySettings | None = None
        config_error: str | None = None
        warning: str | None = None

        if challenge is not None and platform is not None:
            if all(_comparable(k, challenge[k]) == _comparable(k, platform[k]) for k in challenge):
                gateway = GatewaySettings(
                    family="challenge",
                    session_id=_clean(source, CHALLENGE_SESSION_VAR),
                    **challenge,
                )
            else:
                differing = sorted(
                    k
                    for k in challenge
                    if _comparable(k, challenge[k]) != _comparable(k, platform[k])
                )
                # 平台会同时注入推荐组与兼容组。两组不一致时优先用推荐组继续答题；
                # 直接禁用网关会让所有模型题归零，代价远大于用其中一组。
                warning = (
                    "CHALLENGE_LLM_* and PLATFORM_LLM_* disagree on: "
                    + ", ".join(differing)
                    + "; using the recommended CHALLENGE_LLM_* group"
                )
                gateway = GatewaySettings(
                    family="challenge",
                    session_id=_clean(source, CHALLENGE_SESSION_VAR),
                    **challenge,
                )
        elif challenge is not None:
            gateway = GatewaySettings(
                family="challenge",
                session_id=_clean(source, CHALLENGE_SESSION_VAR),
                **challenge,
            )
        elif platform is not None:
            gateway = GatewaySettings(family="platform", **platform)
        else:
            config_error = (
                "no complete gateway group found; expected all of "
                "CHALLENGE_LLM_BASE_URL/CHALLENGE_LLM_API_KEY/CHALLENGE_LLM_MODEL "
                "or all of PLATFORM_LLM_URL/PLATFORM_API_KEY/PLATFORM_LLM_MODEL"
            )

        budget_raw = _clean(source, "RESEARCH_CASE_BUDGET_SECONDS")
        if budget_raw is None:
            budget = DEFAULT_CASE_BUDGET_SECONDS
        else:
            try:
                budget = float(budget_raw)
            except ValueError:
                raise ConfigError(
                    f"RESEARCH_CASE_BUDGET_SECONDS must be a number, got {budget_raw!r}"
                ) from None
            if not (math.isfinite(budget) and budget > 0):
                raise ConfigError(
                    f"RESEARCH_CASE_BUDGET_SECONDS must be a finite positive number, "
                    f"got {budget_raw!r}"
                )

        return cls(
            gateway=gateway,
            gateway_config_error=config_error,
            gateway_config_warning=warning,
            # 组委会口径：结果目录用 RESEARCH_WORKSPACE_TEAM，其次本地 TEAM_NAME；
            # TEAM_ID 只是平台遥测身份，绝不能拿来当目录名（会写到平台不读取的路径）
            team=(_clean(source, "RESEARCH_WORKSPACE_TEAM") or _clean(source, "TEAM_NAME") or ""),
            workspace_root=Path(
                _clean(source, "RESEARCH_WORKSPACE_ROOT") or DEFAULT_WORKSPACE_ROOT
            ),
            papers_root=Path(_clean(source, "RESEARCH_PAPERS_ROOT") or DEFAULT_PAPERS_ROOT),
            case_budget_seconds=budget,
            log_level=_clean(source, "AGENTHACK_LOG_LEVEL") or "INFO",
        )
