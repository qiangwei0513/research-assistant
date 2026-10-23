"""赛事模型网关客户端。

同步 httpx.Client：由 SDK interceptor 自动抓取 token 与 finish_reason。
不做无界重试；超时来自 Deadline，不写死常量；凭据不出现在日志或异常消息中。
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

import httpx

from research_assistant.errors import ConfigError, GatewayError
from research_assistant.runtime.logging import BoundLogger, JsonLogger
from research_assistant.runtime.settings import GatewaySettings

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查，避免与 context 循环导入
    from research_assistant.runtime.context import SolveContext

MIN_MODEL_SECONDS = 5.0
COMPLETIONS_PATH = "/chat/completions"


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant"]
    content: str

    def to_json(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class GatewayUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None

    @classmethod
    def from_response(cls, usage: Mapping[str, Any] | None) -> GatewayUsage:
        def number(key: str, source: Mapping[str, Any] | None) -> int | None:
            if not source:
                return None
            value = source.get(key)
            return value if isinstance(value, int) and not isinstance(value, bool) else None

        details = usage.get("prompt_tokens_details") if usage else None
        if not isinstance(details, Mapping):
            details = None
        return cls(
            prompt_tokens=number("prompt_tokens", usage),
            completion_tokens=number("completion_tokens", usage),
            total_tokens=number("total_tokens", usage),
            cached_tokens=number("cached_tokens", details),
        )


@dataclass(frozen=True)
class GatewayResponse:
    content: str
    finish_reason: str | None
    usage: GatewayUsage
    reasoning: str | None
    raw: Mapping[str, Any]


class Gateway(Protocol):
    def complete(
        self,
        messages: Sequence[Message],
        *,
        context: SolveContext,
        stage: str,
        max_tokens: int | None = None,
    ) -> GatewayResponse: ...


class HttpGateway:
    """真实网关适配器。`transport` 仅用于测试注入。"""

    def __init__(
        self,
        gateway: GatewaySettings,
        *,
        log: BoundLogger | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = gateway
        self._log = log or JsonLogger("gateway")
        self._transport = transport
        self.last_timeout_seconds: float | None = None

    @property
    def endpoint(self) -> str:
        return self._settings.base_url.rstrip("/") + COMPLETIONS_PATH

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._settings.api_key}",
            "Content-Type": "application/json",
        }
        if self._settings.session_id:
            headers["X-Session-ID"] = self._settings.session_id
        return headers

    def complete(
        self,
        messages: Sequence[Message],
        *,
        context: SolveContext,
        stage: str,
        max_tokens: int | None = None,
    ) -> GatewayResponse:
        if not messages:
            raise GatewayError("messages must not be empty", case_id=context.case_id)

        timeout = context.deadline.take(stage, MIN_MODEL_SECONDS, case_id=context.case_id)
        self.last_timeout_seconds = timeout

        body: dict[str, Any] = {
            "model": self._settings.model,
            "stream": False,
            "messages": [message.to_json() for message in messages],
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens

        started = time.monotonic()
        context.log.info(
            "gateway.request",
            stage=stage,
            model=self._settings.model,
            family=self._settings.family,
            message_count=len(messages),
        )
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout), transport=self._transport) as client:
                response = client.post(self.endpoint, json=body, headers=self._headers())
        except httpx.HTTPError as exc:
            raise GatewayError(
                f"gateway request failed during {stage}: {exc.__class__.__name__}: {exc}",
                case_id=context.case_id,
            ) from exc

        duration_ms = (time.monotonic() - started) * 1000
        if response.status_code != 200:
            raise GatewayError(
                f"gateway returned HTTP {response.status_code} during {stage}",
                case_id=context.case_id,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise GatewayError(
                f"gateway response is not valid JSON during {stage}", case_id=context.case_id
            ) from exc
        if not isinstance(payload, Mapping):
            raise GatewayError(
                f"gateway response must be a JSON object during {stage}",
                case_id=context.case_id,
            )

        choices = payload.get("choices")
        if not isinstance(choices, Sequence) or not choices:
            raise GatewayError(
                f"gateway response has no choices during {stage}", case_id=context.case_id
            )
        choice = choices[0]
        if not isinstance(choice, Mapping):
            raise GatewayError(
                f"gateway choice must be an object during {stage}", case_id=context.case_id
            )

        finish_reason = choice.get("finish_reason")
        if finish_reason != "stop":
            raise GatewayError(
                f"gateway finish_reason was {finish_reason!r}, expected 'stop' "
                f"(truncated or refused completion) during {stage}",
                case_id=context.case_id,
            )

        message = choice.get("message")
        if not isinstance(message, Mapping):
            raise GatewayError(
                f"gateway choice has no message object during {stage}", case_id=context.case_id
            )
        content = message.get("content")
        reasoning = message.get("reasoning")
        if reasoning is None:
            reasoning = message.get("reasoning_content")

        usage = GatewayUsage.from_response(
            payload.get("usage") if isinstance(payload.get("usage"), Mapping) else None
        )
        context.log.info(
            "gateway.response",
            stage=stage,
            duration_ms=round(duration_ms, 1),
            finish_reason=finish_reason,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            content_characters=len(content) if isinstance(content, str) else 0,
        )
        return GatewayResponse(
            content=content if isinstance(content, str) else "",
            finish_reason=finish_reason if isinstance(finish_reason, str) else None,
            usage=usage,
            reasoning=reasoning if isinstance(reasoning, str) else None,
            raw=payload,
        )


class FakeGateway:
    """测试替身：按脚本返回，可注入延迟与异常。绝不发起网络请求。"""

    def __init__(
        self,
        responses: Sequence[GatewayResponse | str | Exception],
        *,
        delay_seconds: float = 0.0,
    ) -> None:
        self._responses = list(responses)
        self._delay = delay_seconds
        self.calls: list[tuple[str, tuple[Message, ...]]] = []

    def complete(
        self,
        messages: Sequence[Message],
        *,
        context: SolveContext,
        stage: str,
        max_tokens: int | None = None,
    ) -> GatewayResponse:
        self.calls.append((stage, tuple(messages)))
        if self._delay:
            time.sleep(self._delay)
        if not self._responses:
            raise GatewayError(
                f"no scripted response left for stage {stage}", case_id=context.case_id
            )
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, GatewayResponse):
            return item
        return GatewayResponse(
            content=item,
            finish_reason="stop",
            usage=GatewayUsage(0, 0, 0, 0),
            reasoning=None,
            raw={"fake": True, "content": item},
        )


class UnavailableGateway:
    """网关未配置时的占位实现。

    让路由错误（501）先于配置错误（500）暴露，而不是在装配上下文时就失败。
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def complete(
        self,
        messages: Sequence[Message],
        *,
        context: SolveContext,
        stage: str,
        max_tokens: int | None = None,
    ) -> GatewayResponse:
        raise ConfigError(self.reason, case_id=context.case_id)
