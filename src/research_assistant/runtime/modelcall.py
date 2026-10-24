"""带一次有界重试的模型调用。

为什么需要：实测真实网关下 A 类连续 5 次调用中出现过 1 次模型不按 JSON 契约输出
（返回自由文本或空内容），直接导致该题 502、整题 0 分。这个失败模式对 B/C/E 同样致命，
而它们的分值远高于 A。

与总纲禁止的「无界 SDK 重试」不同：这里最多**一次**重问，且只在预算允许时进行，
并把上一次的违规输出与具体原因回传给同一会话，要求它重新给出合规输出。
传输层错误也重试一次（网关偶发 5xx），但会在日志里区分原因。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, TypeVar

from research_assistant.errors import GatewayError
from research_assistant.runtime.gateway import Message

if TYPE_CHECKING:  # pragma: no cover
    from research_assistant.runtime.context import SolveContext
    from research_assistant.runtime.gateway import Gateway, GatewayResponse

T = TypeVar("T")

MAX_ATTEMPTS = 2
RETRY_STAGE_SUFFIX = ".retry"
# 重问至少要留出这么多秒，否则不如直接把失败如实报出去
MIN_RETRY_SECONDS = 12.0

CORRECTION_PROMPT = (
    "你上一次的输出不符合要求，原因：{reason}\n"
    "请重新输出一次，严格遵守原始要求：{requirement}\n"
    "只输出要求的内容本身，不要代码围栏、不要解释、不要任何前后缀。"
)


def complete_parsed(
    gateway: Gateway,
    messages: Sequence[Message],
    *,
    context: SolveContext,
    stage: str,
    requirement: str,
    parse: Callable[[str], T],
    max_tokens: int | None = None,
    attempts: int = MAX_ATTEMPTS,
) -> tuple[T, GatewayResponse]:
    """调用模型并解析；解析失败时在预算允许下重问一次。

    `parse` 必须在输出不合规时抛 `GatewayError`（或 `ValueError`），
    其消息会原样回传给模型作为纠错依据。
    返回 (解析结果, 最后一次响应)，调用方可从响应里取 usage 记日志。
    """
    conversation: list[Message] = list(messages)
    last_error: Exception | None = None
    response: GatewayResponse | None = None

    for attempt in range(1, max(1, attempts) + 1):
        current_stage = stage if attempt == 1 else stage + RETRY_STAGE_SUFFIX
        response = gateway.complete(
            conversation, context=context, stage=current_stage, max_tokens=max_tokens
        )
        try:
            parsed = parse(response.content)
        except (GatewayError, ValueError) as exc:
            last_error = exc
            reason = exc.message if isinstance(exc, GatewayError) else str(exc)
            context.log.warning(
                "model.output_rejected",
                case_id=context.case_id,
                stage=current_stage,
                attempt=attempt,
                finish_reason=response.finish_reason,
                content_characters=len(response.content),
                reason=reason[:200],
            )
            if attempt >= attempts:
                break
            if context.deadline.remaining() < MIN_RETRY_SECONDS:
                context.log.warning(
                    "model.retry_skipped_budget",
                    case_id=context.case_id,
                    stage=stage,
                    remaining=round(context.deadline.remaining(), 2),
                )
                break
            conversation.append(Message(role="assistant", content=response.content))
            conversation.append(
                Message(
                    role="user",
                    content=CORRECTION_PROMPT.format(reason=reason[:300], requirement=requirement),
                )
            )
            continue
        if attempt > 1:
            context.log.info(
                "model.retry_succeeded", case_id=context.case_id, stage=stage, attempt=attempt
            )
        return parsed, response

    assert response is not None
    if last_error is None:  # pragma: no cover - 循环至少执行一次
        raise GatewayError("model call produced no result", case_id=context.case_id)
    if isinstance(last_error, GatewayError):
        raise last_error
    raise GatewayError(str(last_error), case_id=context.case_id)
