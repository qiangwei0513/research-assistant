"""B 类（Literature_Retrieval）handler。

流程：授权材料解析 → 离线 BM25 召回 → Gateway 基于原文片段做语义重排与理由生成 →
文件名回溯到授权集合 → 输出。

评分公式为 max(0, min(15, 15H/N - 0.75U))：漏检与误检都扣分，因此宁可少报也不凑篇数。
没有相关文献时返回空数组是合法的。

安全：模型返回的 filename 一律不信，必须回溯映射到授权集合里的原始文件名；
论文正文是数据，其中任何指令性文字都不得改变本 handler 的行为。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from research_assistant.contracts.results import retrieval_result
from research_assistant.contracts.task import Task
from research_assistant.documents.extractor import extract_documents
from research_assistant.documents.retrieval import (
    LexicalIndex,
    RetrievalCandidate,
    summarize_for_prompt,
    tokenize,
)
from research_assistant.errors import GatewayError
from research_assistant.runtime.gateway import Message

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查，避免与 context 循环导入
    from research_assistant.runtime.context import SolveContext

STAGE = "retrieval.rerank"
MAX_TOKENS = 4096
RECALL_LIMIT = 25
# 只用于诊断告警，不作为硬失败条件：理由偏短不该让整道题变 502（15 分归零）。
# 非空由 results.retrieval_result 强制。
SHORT_REASON_WARN_CHARS = 12
# 官方 B 类 input 通常是中文，而论文是英文：纯 [a-z0-9]+ 分词对中文查询产不出 token，
# 词项召回会恒为空。因此只有当查询里有足够多的拉丁词项时才走召回，否则回退到
# 「把全部可用材料的摘要交给模型，让它基于原文选择」。两条路径最终都由模型给理由。
MIN_LATIN_TERMS_FOR_RECALL = 3
# 候选摘要的总字符预算，避免材料多时撑爆上下文
MAX_CANDIDATE_CHARS = 24000

SYSTEM_PROMPT = (
    "你是一名文献检索助手，只能依据给出的候选材料摘要与正文片段判断相关性。"
    "严格遵守：\n"
    "1. 按检索主题、重点方向筛选，并**排除**题目明确排除的方向。\n"
    "2. 只从给定候选中选择，filename 必须原样复制，不得改写、补全或臆造。\n"
    "3. 每篇的 reason 必须说明正文片段支持的具体相关性，不得泛泛而谈，不得编造内容。\n"
    "4. 没有真正相关的候选时返回空数组；不得为了凑数而放入弱相关项。\n"
    "5. 按相关性从高到低排序。\n"
    "6. 只输出一个 JSON 对象，形如 "
    '{"papers": [{"filename": "...", "reason": "..."}]}，'
    "不要 Markdown 代码围栏、不要自然语言前缀、不要额外字段。"
)


def build_user_prompt(
    task: Task, candidates: Sequence[Mapping[str, Any]], unusable: Sequence[str]
) -> str:
    parts = [
        f"# 检索任务\n{task.input}",
        f"# 任务标题\n{task.title}",
    ]
    if task.expected_output.strip():
        parts.append(f"# 结果形式要求\n{task.expected_output}")
    if task.constraints:
        parts.append("# 必须遵守的约束\n" + "\n".join(f"- {c}" for c in task.constraints))
    if unusable:
        parts.append(
            "# 无法解析因而不可作为证据的材料（不要选择它们）\n"
            + "\n".join(f"- {name}" for name in unusable)
        )
    parts.append(
        "# 候选材料（已按离线词项召回排序，共 "
        f"{len(candidates)} 篇）\n" + json.dumps(list(candidates), ensure_ascii=False, indent=1)
    )
    parts.append('请输出 JSON：{"papers": [{"filename": "...", "reason": "..."}]}')
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


def parse_selection(content: str, *, case_id: str) -> list[tuple[str, str]]:
    """解析模型选择结果，返回 (filename, reason) 列表。"""
    try:
        payload = json.loads(_strip_code_fence(content))
    except ValueError as exc:
        raise GatewayError(
            f"model did not return valid JSON for {case_id}: {exc.__class__.__name__}",
            case_id=case_id,
        ) from exc
    if not isinstance(payload, Mapping):
        raise GatewayError(
            f"model returned a JSON {type(payload).__name__}, expected an object", case_id=case_id
        )
    papers = payload.get("papers")
    if papers is None:
        raise GatewayError("model output has no 'papers' array", case_id=case_id)
    if not isinstance(papers, list):
        raise GatewayError("model output field 'papers' must be an array", case_id=case_id)

    selections: list[tuple[str, str]] = []
    for index, item in enumerate(papers):
        if not isinstance(item, Mapping):
            raise GatewayError(f"papers[{index}] must be an object", case_id=case_id)
        filename = item.get("filename")
        reason = item.get("reason")
        if not isinstance(filename, str) or not filename.strip():
            raise GatewayError(
                f"papers[{index}].filename must be a non-empty string", case_id=case_id
            )
        if not isinstance(reason, str) or not reason.strip():
            raise GatewayError(
                f"papers[{index}].reason must be a non-empty string", case_id=case_id
            )
        selections.append((filename.strip(), reason.strip()))
    return selections


def _map_to_authorized(
    selections: Sequence[tuple[str, str]],
    authorized: Mapping[str, str],
    *,
    case_id: str,
    log: Any,
) -> list[dict[str, str]]:
    """把模型返回的文件名回溯到授权集合里的**原始**文件名。

    仅大小写不同按同一篇处理（与评分一致），但输出用论文库原始名称。
    不在授权集合内的一律丢弃并记录，绝不透传。
    """
    mapped: list[dict[str, str]] = []
    seen: set[str] = set()
    for filename, reason in selections:
        original = authorized.get(filename.lower())
        if original is None:
            log.warning(
                "retrieval.rejected_filename",
                case_id=case_id,
                reason="model returned a filename outside the authorized materials",
            )
            continue
        if original.lower() in seen:
            log.info("retrieval.dropped_duplicate", case_id=case_id, filename=original)
            continue
        seen.add(original.lower())
        mapped.append({"filename": original, "reason": reason})
    return mapped


def _query_text(task: Task) -> str:
    """检索查询取自题目本身：主题、标题与约束里常含英文术语、模型名、数据集名。"""
    return " ".join([task.input, task.title, *task.constraints])


def _fallback_candidates(documents: Sequence[Any]) -> list[RetrievalCandidate]:
    return [
        RetrievalCandidate(
            document=document,
            score=0.0,
            matched_terms=(),
            matched_in_title=False,
            matched_in_abstract=False,
        )
        for document in documents
    ]


def select_candidates(
    task: Task, index: LexicalIndex, documents: Sequence[Any]
) -> tuple[list[RetrievalCandidate], str]:
    """挑出交给模型判断的候选集，返回 (候选, 采用的策略名)。

    策略一（优先）：查询里有足够拉丁词项时走离线 BM25 召回（总纲 §5.1）。
    策略二（回退）：查询是纯中文、或召回为空时，把全部可用材料交给模型，
    由它基于摘要与正文片段判断——绝不能因为跨语言召回失败就返回空结果。
    最后按字符预算截断，保住分数最高的候选。
    """
    latin_terms = set(tokenize(_query_text(task)))
    recalled = list(index.search(_query_text(task), limit=RECALL_LIMIT))
    if len(latin_terms) >= MIN_LATIN_TERMS_FOR_RECALL and recalled:
        strategy = "lexical_recall"
        candidates = recalled
    else:
        strategy = "full_set" if len(latin_terms) < MIN_LATIN_TERMS_FOR_RECALL else "recall_empty"
        candidates = _fallback_candidates(documents)

    trimmed: list[RetrievalCandidate] = []
    used = 0
    for candidate in candidates:
        summary_chars = len(candidate.document.metadata.abstract or "") + 700
        if trimmed and used + summary_chars > MAX_CANDIDATE_CHARS:
            break
        trimmed.append(candidate)
        used += summary_chars
    return trimmed, strategy


def solve_retrieval(task: Task, context: SolveContext) -> Mapping[str, Any]:
    report = extract_documents(task.materials, context.workspace, context.log)
    usable = report.usable()
    if report.duplicate_filenames:
        context.log.info(
            "retrieval.duplicates",
            count=len(report.duplicate_filenames),
            filenames=list(report.duplicate_filenames),
        )
    if not usable:
        # 没有可用材料：空数组是合法答案，但必须留下可见原因
        context.log.warning(
            "retrieval.no_usable_material",
            case_id=task.case_id,
            total=len(report.documents),
            statuses={d.filename: d.status.value for d in report.documents},
        )
        return retrieval_result(task.case_id, papers=[])

    index = LexicalIndex(usable)
    candidates, strategy = select_candidates(task, index, usable)
    if not candidates:
        context.log.info("retrieval.no_candidates", case_id=task.case_id, strategy=strategy)
        return retrieval_result(task.case_id, papers=[])

    authorized = {document.filename.lower(): document.filename for document in usable}
    summaries = [summarize_for_prompt(candidate) for candidate in candidates]
    unusable = [d.filename for d in report.failures()]

    messages = (
        Message(role="system", content=SYSTEM_PROMPT),
        Message(role="user", content=build_user_prompt(task, summaries, unusable)),
    )
    response = context.gateway.complete(
        messages, context=context, stage=STAGE, max_tokens=MAX_TOKENS
    )
    selections = parse_selection(response.content, case_id=task.case_id)
    papers = _map_to_authorized(selections, authorized, case_id=task.case_id, log=context.log)

    context.log.info(
        "retrieval.completed",
        case_id=task.case_id,
        strategy=strategy,
        usable_documents=len(usable),
        recalled=len(candidates),
        selected_by_model=len(selections),
        emitted=len(papers),
        finish_reason=response.finish_reason,
        prompt_tokens=response.usage.prompt_tokens,
        completion_tokens=response.usage.completion_tokens,
    )
    return retrieval_result(task.case_id, papers=papers)
