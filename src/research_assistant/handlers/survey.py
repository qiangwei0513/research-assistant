"""C 类（Survey）handler。

流程：解析授权材料 → 建证据包 → 一次生成完整 Markdown → 引文自检 →
预算允许时做**一轮**定向修复 → 落盘 → 返回与文件逐字一致的 markdown。

评分依据 ce-quality-v1：事实 15 分（冻结事实逐项 0/0.6/1 档）+ 引文 10 分独立计分。
引文的五个子项（合法性一致性、指定材料覆盖、反打包、跨段落均衡、上下文相关）
都由 validation.citations 做可诊断自检，但**档位判断属于官方裁判**，本模块不声称算出分数。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from research_assistant.contracts.results import MIN_BODY_CHARACTERS, survey_result
from research_assistant.contracts.task import Task
from research_assistant.documents.evidence import (
    DEFAULT_MAX_CHARS_PER_DOCUMENT,
    EvidencePack,
    any_unusable_reason,
    build_evidence_pack,
    is_context_length_error,
    render_evidence_pack,
    render_unusable,
    shrink_budget,
)
from research_assistant.documents.extractor import extract_documents
from research_assistant.errors import GatewayError, MaterialUnavailableError
from research_assistant.runtime.gateway import Message
from research_assistant.runtime.modelcall import complete_parsed
from research_assistant.validation.citations import (
    CitationAnalysis,
    analyze_citations,
    build_repair_hint,
    issue_summary,
)

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查，避免与 context 循环导入
    from research_assistant.runtime.context import SolveContext

STAGE_GENERATE = "survey.generate"
STAGE_REPAIR = "survey.repair"
MAX_TOKENS = 8192
REPAIR_MAX_TOKENS = 8192
REPAIR_BUDGET_SECONDS = 20.0
MIN_DISTINCT_PAPERS = 5
# 规则要求正文至少 800 字符；这里要求模型写得更长，留出被 strip 与自检后的余量
TARGET_BODY_CHARACTERS = 1600
# 上限同样重要：实测 E 类写到 4460 字符耗时 170.9s，而单题上限只有 180s。
# 正文超过下限即可得分，过长只会把整题推向超时（超时即 0 分）。
MAX_BODY_CHARACTERS = 2400

SYSTEM_PROMPT = (
    "你是一名科研综述写作者，只能依据给定材料的原文与元数据写作。严格遵守：\n"
    "1. 比较各论文的**研究问题、方法、数据、指标、结论、局限与适用边界**，"
    "梳理出技术脉络，而不是逐篇罗列摘要。\n"
    "2. 正文事实与数据必须用数字引用标注：[编号] 或 [编号, p.页码]。"
    "编号对应材料清单里的 [n]。**页码只有在给定原文片段里确实能找到该陈述时才写**，"
    "否则只写 [编号]。\n"
    "3. 不得编造事实、数据、页码或参考文献。材料不足时如实写明不足，"
    "不要为了凑满 5 篇而虚构第 5 篇。\n"
    "4. 引用要服务于具体陈述：不要在同一个方括号里堆 3 个及以上编号（这会被判为打包引用），"
    "也不要用无意义的重复内容去凑分布。\n"
    "5. 每篇被引用的材料应在**至少两个不同的实质段落**中就其真实贡献被引用；"
    "不要让单篇占绝大多数引用。\n"
    "6. 结尾必须有一行 `## 参考文献`，其下逐条列出**正文实际引用过**的指定论文，"
    "每条以 [编号] 开头并包含完整 PDF 文件名。\n"
    f"7. 正文（不含参考文献）控制在 {TARGET_BODY_CHARACTERS} 至 {MAX_BODY_CHARACTERS} 字符之间，"
    "用中文写作，使用 Markdown 小节标题组织，**每节写要点即可、不要展开成长段落**："
    "本题有严格的单题时限，超时的代价是整题 0 分，而正文只要超过 800 字符、"
    "比较维度齐全、引用合规就能得分。\n"
    "8. 只输出 Markdown 正文本身，不要代码围栏、不要任何前后缀说明。"
)


def build_user_prompt(task: Task, pack: EvidencePack) -> str:
    parts = [
        f"# 综述任务\n{task.input}",
        f"# 案例标题\n{task.title}",
    ]
    if task.expected_output.strip():
        parts.append(f"# 结果要求\n{task.expected_output}")
    if task.constraints:
        parts.append("# 必须遵守的约束\n" + "\n".join(f"- {c}" for c in task.constraints))
    unusable_block = render_unusable(pack)
    if unusable_block:
        parts.append(unusable_block)
    parts.append(
        f"# 指定材料清单（共 {len(pack.entries)} 篇，编号即引用编号）\n"
        + render_evidence_pack(pack)
    )
    parts.append(
        "请写出完整 Markdown 综述：正文比较上述维度并用 [编号] 标注引用，"
        "结尾用 `## 参考文献` 列出实际引用过的论文及完整 PDF 文件名。"
    )
    return "\n\n".join(parts)


def _parse_markdown(content: str) -> str:
    """剥掉模型可能自行添加的代码围栏，并守住正文长度下限。"""
    stripped = _strip_code_fence(content)
    if len(stripped.strip()) < MIN_BODY_CHARACTERS:
        raise GatewayError(
            f"survey body was {len(stripped.strip())} characters, below the required "
            f"{MIN_BODY_CHARACTERS}"
        )
    return stripped


def _strip_code_fence(content: str) -> str:
    text = content.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip() + "\n"


def _log_analysis(context: SolveContext, analysis: CitationAnalysis, stage: str) -> None:
    context.log.info(
        "survey.citation_check",
        case_id=context.case_id,
        stage=stage,
        body_citations=len(analysis.instances),
        distinct_cited_papers=len(analysis.cited_filenames),
        reference_entries=len(analysis.references),
        coverage_ratio=round(analysis.coverage_ratio(), 3),
        packing_ratio=round(analysis.packing_ratio, 3),
        issues=issue_summary(analysis),
    )


def solve_survey(task: Task, context: SolveContext) -> Mapping[str, Any]:
    report = extract_documents(task.materials, context.workspace, context.log)
    pack = build_evidence_pack(report.documents)
    if not pack.entries:
        # C 类没有 failed 状态：写不出综述就必须报错，不能返回伪装成功的空壳
        reason = any_unusable_reason(report.documents) or "本题没有可用的授权材料"
        raise MaterialUnavailableError(
            f"cannot write a survey without usable materials: {reason!r:.200}",
            case_id=task.case_id,
        )

    allowed = frozenset(pack.filenames)
    min_papers = min(MIN_DISTINCT_PAPERS, len(pack.entries))
    evidence_budget = DEFAULT_MAX_CHARS_PER_DOCUMENT
    if min_papers < MIN_DISTINCT_PAPERS:
        # 材料不足时如实记录，不捏造第 5 篇
        context.log.warning(
            "survey.insufficient_materials",
            case_id=task.case_id,
            usable=len(pack.entries),
            required=MIN_DISTINCT_PAPERS,
            unusable=[(u.filename, u.status) for u in pack.unusable],
        )

    def _generate(current_pack: EvidencePack, stage: str):
        return complete_parsed(
            context.gateway,
            (
                Message(role="system", content=SYSTEM_PROMPT),
                Message(role="user", content=build_user_prompt(task, current_pack)),
            ),
            context=context,
            stage=stage,
            requirement=(
                f"完整的 Markdown 综述正文，去掉首尾空白后至少 {TARGET_BODY_CHARACTERS} 字符，"
                "含数字引用与结尾的 ## 参考文献"
            ),
            parse=_parse_markdown,
            max_tokens=MAX_TOKENS,
        )

    # 正文至少 800 字符是硬合同，过短会让整题非法；一次有界重问远比 0 分便宜
    try:
        markdown, response = _generate(pack, STAGE_GENERATE)
    except GatewayError as exc:
        # 正式模型上下文上限未知：超限时把每篇证据预算减半重建，再试一次且仅一次
        if not is_context_length_error(exc.message):
            raise
        evidence_budget = shrink_budget(evidence_budget)
        pack = build_evidence_pack(report.documents, max_chars_per_document=evidence_budget)
        allowed = frozenset(pack.filenames)
        context.log.warning(
            "survey.context_overflow_shrinking",
            case_id=task.case_id,
            new_budget_per_document=evidence_budget,
            reason=exc.message[:200],
        )
        markdown, response = _generate(pack, STAGE_GENERATE + ".shrunk")
    analysis = analyze_citations(markdown, allowed)
    _log_analysis(context, analysis, "generate")

    hint = build_repair_hint(analysis, min_distinct_papers=min_papers)
    if hint.lines and context.deadline.remaining() > REPAIR_BUDGET_SECONDS:
        # 只做一轮有针对性的修复；多轮自评不等于真实实验，也不该吃掉整题预算
        repair_messages = (
            Message(role="system", content=SYSTEM_PROMPT),
            Message(role="user", content=build_user_prompt(task, pack)),
            Message(role="assistant", content=markdown),
            Message(
                role="user",
                content=(
                    "下面的自检发现了会被扣分的具体问题。请在**不改变已有事实与引用编号含义**、"
                    "不新增未授权材料、不编造内容的前提下，重写整篇综述修掉这些问题：\n"
                    + hint.as_prompt_block()
                    + "\n\n只输出修改后的完整 Markdown 正文，不要代码围栏、不要解释。"
                ),
            ),
        )
        repaired = context.gateway.complete(
            repair_messages,
            context=context,
            stage=STAGE_REPAIR,
            max_tokens=REPAIR_MAX_TOKENS,
        )
        candidate = _strip_code_fence(repaired.content)
        candidate_analysis = analyze_citations(candidate, allowed)
        # 只有确实变好才采用；否则保留原稿，避免修复反而破坏正文
        if len(candidate_analysis.issues) < len(analysis.issues) and candidate.strip():
            markdown, analysis = candidate, candidate_analysis
            context.log.info("survey.repair_applied", case_id=task.case_id)
        else:
            context.log.info(
                "survey.repair_rejected",
                case_id=task.case_id,
                before=len(analysis.issues),
                after=len(candidate_analysis.issues),
            )
        _log_analysis(context, analysis, "repair")
    elif hint.lines:
        context.log.warning(
            "survey.repair_skipped_budget",
            case_id=task.case_id,
            remaining=round(context.deadline.remaining(), 2),
            issues=issue_summary(analysis),
        )

    # 生成唯一正文字符串：先落盘再回读比对，不得写文件后又让模型改 JSON 正文
    path = context.workspace.survey_md(task.case_id)
    from research_assistant.runtime.persistence import write_text_verified

    write_text_verified(path, markdown)
    relative = context.workspace.relative_to_output(path)
    context.log.info(
        "survey.completed",
        case_id=task.case_id,
        path=relative,
        characters=len(markdown),
        prompt_tokens=response.usage.prompt_tokens,
        completion_tokens=response.usage.completion_tokens,
        finish_reason=response.finish_reason,
        distinct_cited_papers=len(analysis.cited_filenames),
        remaining_issues=issue_summary(analysis),
    )
    return survey_result(task.case_id, path=relative, markdown=markdown)
