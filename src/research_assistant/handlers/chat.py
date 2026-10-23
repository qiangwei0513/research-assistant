"""A 类（Chat）handler。

模型只产内容：case_id 由程序注入，模型返回的 case_id 与任何未知字段一律丢弃。
自动项是 answer 3 分、clarifying_question 2 分，但内容必须与题意相关。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from research_assistant.contracts.results import chat_result
from research_assistant.contracts.task import Task
from research_assistant.errors import GatewayError
from research_assistant.runtime.gateway import MIN_MODEL_SECONDS, Message

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查，避免与 context 循环导入
    from research_assistant.runtime.context import SolveContext

CHAT_MAX_TOKENS = 2048
STAGE = "chat.completion"

CHAT_SYSTEM_PROMPT = (
    "你是一名科研助手，正在回答研究者提出的科研对话问题。"
    "严格遵守以下要求：\n"
    "1. 直接回答当前研究问题，明确涉及的变量、变量之间可能的关系，以及现有证据的限制。\n"
    "2. 不得编造数据、文献或实验结果；无法确定时明确说明不确定性。\n"
    "3. 提出一个最有价值的研究范围限定问题，用于收窄研究边界。\n"
    "4. 只输出一个 JSON 对象，键恰好为 answer 与 clarifying_question，两项都是非空字符串。\n"
    "5. 不要输出 Markdown 代码围栏、自然语言前缀或任何额外字段。"
)


def build_chat_user_prompt(task: Task) -> str:
    parts = [f"# 案例标题\n{task.title}", f"# 研究者问题\n{task.input}"]
    if task.expected_output.strip():
        parts.append(f"# 回答应覆盖的内容\n{task.expected_output}")
    if task.constraints:
        parts.append("# 必须遵守的额外要求\n" + "\n".join(f"- {c}" for c in task.constraints))
    if task.materials:
        parts.append("# 本题允许的材料\n" + "\n".join(f"- {m}" for m in task.materials))
    parts.append('请按系统要求输出 JSON：{"answer": "...", "clarifying_question": "..."}')
    return "\n\n".join(parts)


def _strip_code_fence(content: str) -> str:
    text = content.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_chat_payload(content: str, *, case_id: str) -> tuple[str, str]:
    text = _strip_code_fence(content)
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise GatewayError(
            f"model did not return valid JSON for {case_id}: {exc.__class__.__name__}",
            case_id=case_id,
        ) from exc
    if not isinstance(payload, Mapping):
        raise GatewayError(
            f"model returned a JSON {type(payload).__name__}, expected an object",
            case_id=case_id,
        )

    values: list[str] = []
    for field in ("answer", "clarifying_question"):
        value = payload.get(field)
        if not isinstance(value, str):
            raise GatewayError(f"model output field {field} must be a string", case_id=case_id)
        if not value.strip():
            raise GatewayError(f"model output field {field} must not be blank", case_id=case_id)
        values.append(value)
    return values[0], values[1]


def solve_chat(task: Task, context: SolveContext) -> Mapping[str, Any]:
    # 预算前置校验：剩余时间不足以完成一次模型调用时立即失败，不在耗尽后仍去打模型。
    # HttpGateway.complete 内部会再 take 一次同样的额度；take 不修改状态，重复领取无副作用。
    context.deadline.take(STAGE, MIN_MODEL_SECONDS, case_id=task.case_id)
    messages = (
        Message(role="system", content=CHAT_SYSTEM_PROMPT),
        Message(role="user", content=build_chat_user_prompt(task)),
    )
    response = context.gateway.complete(
        messages, context=context, stage=STAGE, max_tokens=CHAT_MAX_TOKENS
    )
    answer, question = parse_chat_payload(response.content, case_id=task.case_id)
    context.log.info(
        "chat.completed",
        answer_characters=len(answer),
        question_characters=len(question),
        finish_reason=response.finish_reason,
        prompt_tokens=response.usage.prompt_tokens,
        completion_tokens=response.usage.completion_tokens,
    )
    return chat_result(task.case_id, answer=answer, clarifying_question=question)
