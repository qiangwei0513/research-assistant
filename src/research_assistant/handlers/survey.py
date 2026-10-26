"""C 类（Survey）handler。

流程：解析授权材料 → 建证据包 → 一次生成完整 Markdown → 引文自检 →
预算允许时做**一轮**定向修复 → 落盘 → 返回与文件逐字一致的 markdown。

评分依据 ce-quality-v1：事实 15 分（冻结事实逐项 0/0.6/1 档）+ 引文 10 分独立计分。
引文的五个子项（合法性一致性、指定材料覆盖、反打包、跨段落均衡、上下文相关）
都由 validation.citations 做可诊断自检，但**档位判断属于官方裁判**，本模块不声称算出分数。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from research_assistant.contracts.report_text import report_body_characters
from research_assistant.contracts.results import MIN_BODY_CHARACTERS, survey_result
from research_assistant.contracts.task import Task
from research_assistant.documents.evidence import (
    DEFAULT_MAX_CHARS_PER_DOCUMENT,
    EvidencePack,
    build_evidence_pack,
    is_context_length_error,
    render_evidence_pack,
    render_unusable,
    shrink_budget,
)
from research_assistant.documents.extractor import extract_documents
from research_assistant.errors import BudgetExhausted, GatewayError
from research_assistant.runtime.gateway import Message
from research_assistant.runtime.modelcall import complete_parsed
from research_assistant.validation.citations import (
    CitationAnalysis,
    build_repair_hint,
    issue_summary,
)
from research_assistant.validation.grounding import (
    analyze_report_evidence,
)
from research_assistant.validation.report_repair import complete_missing_references, decide_repair
from research_assistant.validation.source_audit import REPAIR_RESERVE, audit_report

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
# 保留生成时间余量；字数下限不是内容质量或得分保证。
MAX_BODY_CHARACTERS = 2400

SYSTEM_PROMPT = (
    "你是一名科研综述写作者，只能依据给定材料的原文与元数据写作。严格遵守：\n"
    "1. 比较各论文的**研究问题、方法、数据、指标、结论、局限与适用边界**，"
    "梳理出技术脉络，而不是逐篇罗列摘要。\n"
    "2. 正文事实与数据必须用数字引用标注：[编号] 或 [编号, p.页码]。"
    "编号对应材料清单里的 [n]。**页码只有在给定原文片段里确实能找到该陈述时才写**，"
    "否则只写 [编号]。\n"
    "3. 论文数值必须在对应原文有依据，不能借用另一篇论文的数字；原文只报告方向时不得补写幅度。"
    "不得编造事实、数据、页码或参考文献。材料不足时如实写明不足，"
    "不要为了凑满 5 篇而虚构第 5 篇。\n"
    "保留原文中的关键结果、定量阈值、反例与适用条件，不能只写泛化趋势。"
    "地区、气候、样本量和局限未在证据中给出时写明材料未说明；"
    "你自行提出的局限或外推风险必须标为分析判断，不能归作论文已证实结论。"
    "原文节选没覆盖某信息不等于整篇论文没有该信息。\n"
    "4. 引用要服务于具体陈述：不要在同一个方括号里堆 3 个及以上编号（这会被判为打包引用），"
    "也不要用无意义的重复内容去凑分布。\n"
    "5. 每篇被引用的材料应在**至少两个不同的实质段落**中就其真实贡献被引用；"
    "不要让单篇占绝大多数引用。\n"
    "6. 结尾必须有一行 `## 参考文献`，其下逐条列出**正文实际引用过**的指定论文，"
    "每条以 [编号] 开头，只对应一篇论文，完整 PDF 文件名必须用成对反引号包裹。\n"
    f"7. 正文（不含参考文献）控制在 {TARGET_BODY_CHARACTERS} 至 {MAX_BODY_CHARACTERS} 字符之间，"
    "用中文写作，使用 Markdown 小节标题组织，**每节写要点即可、不要展开成长段落**："
    "优先用篇幅交代有原文依据的具体发现及差异，删去空泛铺垫。字数下限不能代替事实覆盖。\n"
    "数值范围请用闭区间0至1等文字形式，不要写成与引文冲突的[0,1]。"
    "8. 只输出完整 Markdown 报告（含末尾参考文献表），不要代码围栏、不要任何前后缀说明。"
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
        "结尾用 `## 参考文献` 列出实际引用过的论文，每条只列一个用反引号包裹的完整 PDF 文件名。"
    )
    return "\n\n".join(parts)


def _parse_markdown(content: str) -> str:
    """剥掉模型可能自行添加的代码围栏，并守住正文长度下限。"""
    stripped = _strip_code_fence(content)
    body_characters = report_body_characters(stripped)
    if body_characters < MIN_BODY_CHARACTERS:
        raise GatewayError(
            f"survey body was {body_characters} characters, below the required "
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


def _unavailable_survey(task: Task, context: SolveContext, report: Any) -> Mapping[str, Any]:
    """零可用材料时的诚实报告：保持 C 类 Schema，不编造任何论文事实。"""
    context.log.warning(
        "survey.materials_unavailable",
        case_id=task.case_id,
        requested=len(task.materials or ()),
        statuses={d.filename: d.status.value for d in report.documents},
    )
    requested = "\n".join(f"- {name}" for name in task.materials or ()) or "- （题目未列出材料）"
    markdown = (
        f"# {task.title}\n\n"
        "## 摘要\n\n"
        "本题指定的授权论文在当前运行环境中不可读取（未挂载、无法解析或文件名不匹配），"
        "因此本报告无法依据原文给出可核验的综述结论。为避免把推测写成事实，正文不包含"
        "任何来自指定论文的数据、页码或结论性陈述。\n\n"
        f"## 题目要求\n\n{task.input}\n\n"
        "## 材料可用性说明\n\n"
        "授权材料是本题事实与引用的唯一来源。当前可用材料为零，任何具体的实验数值、"
        "样本规模、区域差异或饱和效应描述都无法溯源，因此不予给出。待材料恢复后，应按"
        "原文逐条提取事实，标注 [编号] 或 [编号, p.页码]，并保证编号与参考文献一一对应。\n\n"
        "## 建议的核验流程\n\n"
        "第一步，确认每篇指定论文均存在于授权论文库并可解析出文本层；第二步，按主题"
        "整理研究区域、遥感指标与温度指标，记录可核验页码；第三步，比较不同研究的设计"
        "与结论差异，明确哪些结论可相互印证、哪些存在条件限制；第四步，撰写综述并逐条"
        "回查引用，删除无法溯源的表述，最后生成与正文一致的参考文献表。\n\n"
        "## 证据边界\n\n"
        "本报告只记录材料可用性与核验流程，不包含未经阅读原文而写出的研究结论，也不"
        "把模拟观察或常识推断表述为论文结果。材料恢复后应重新生成完整综述，并以原文"
        "为准。\n\n"
        "## 质量核对清单\n\n"
        "材料恢复后应逐项核对：每一处事实与数据是否能在指定论文中找到对应页码；正文"
        "引用编号是否与参考文献条目一一对应；参考文献是否使用论文库中的完整真实文件名；"
        "是否只引用了本题允许的论文；不同研究的区域、时间与观测尺度差异是否被如实说明；"
        "结论是否避免了超出原文证据范围的推断。只有以上各项全部通过，才可以把本报告"
        "视为可提交的综述；任何一项无法核验，都应保留相应说明而不是补写推测内容。"
        "复核时还应记录每篇论文的解析状态（可用、无文本层或缺失），并在正文中区分"
        "“原文明确陈述”与“基于多篇研究的综合判断”，避免把后者写成前者。\n\n"
        f"## 指定材料清单\n\n{requested}\n\n"
        "## 参考文献\n\n"
        "无可引用的授权材料。\n"
    )
    path = context.workspace.survey_md(task.case_id)
    from research_assistant.runtime.persistence import write_text_verified

    write_text_verified(path, markdown)
    relative = context.workspace.relative_to_output(path)
    context.log.info("survey.completed_unavailable", case_id=task.case_id, path=relative)
    return survey_result(task.case_id, path=relative, markdown=markdown)


def solve_survey(task: Task, context: SolveContext) -> Mapping[str, Any]:
    report = extract_documents(
        context.materials,
        context.workspace,
        context.log,
        time_budget_seconds=max(5.0, min(60.0, context.deadline.remaining() - 120)),
    )
    # 首次也要显式传预算：否则与缩减后的调用不一致（一个用隐式默认、一个用变量），
    # 既不可观测也容易被误当成「缩减没生效」。
    pack = build_evidence_pack(
        report.documents, max_chars_per_document=DEFAULT_MAX_CHARS_PER_DOCUMENT
    )
    if not pack.entries:
        # 规范要求非 D 类在材料缺失时仍保持原 Schema；返回 422 会让整题判无效。
        return _unavailable_survey(task, context, report)

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
    reference_repair = complete_missing_references(markdown, pack)
    markdown = reference_repair.markdown
    if reference_repair.added_numbers:
        context.log.info(
            "survey.references_completed",
            case_id=task.case_id,
            added_numbers=reference_repair.added_numbers,
        )
    analysis = analyze_report_evidence(markdown, report.documents, allowed)
    _log_analysis(context, analysis, "generate")
    source_audit = audit_report(markdown, pack, context, stage="survey.source_audit")

    hint = build_repair_hint(analysis, min_distinct_papers=min_papers)
    hint = replace(
        hint,
        lines=hint.lines
        + tuple(finding.hint() for finding in source_audit.findings)
        + tuple(
            issue.detail
            for issue in analysis.issues
            if issue.kind == "unverified_numeric_attribution"
        ),
    )
    if hint.lines and context.deadline.remaining() > REPAIR_BUDGET_SECONDS:
        # 只做一轮有针对性的修复；多轮自评不等于真实实验，也不该吃掉整题预算
        repair_messages = (
            Message(role="system", content=SYSTEM_PROMPT),
            Message(role="user", content=build_user_prompt(task, pack)),
            Message(role="assistant", content=markdown),
            Message(
                role="user",
                content=(
                    "下面的自检发现了会被扣分的具体问题。请保留有依据的事实、纠正无据归因，且不改变引用编号含义，"
                    "不新增未授权材料、不编造内容的前提下，重写整篇综述修掉这些问题：\n"
                    + hint.as_prompt_block()
                    + "\n\n只输出修改后的完整 Markdown 报告，必须保留末尾 ## 参考文献表、全部实际引用的编号及用反引号包裹的完整PDF文件名；不要代码围栏、不要解释。"
                ),
            ),
        )
        try:
            repaired = context.gateway.complete(
                repair_messages,
                context=replace(
                    context,
                    deadline=replace(
                        context.deadline,
                        budget_seconds=context.deadline.budget_seconds
                        - (REPAIR_RESERVE if source_audit.complete else 5.0),
                    ),
                ),
                stage=STAGE_REPAIR,
                max_tokens=REPAIR_MAX_TOKENS,
            )
            candidate = _parse_markdown(repaired.content)
            candidate_references = complete_missing_references(candidate, pack)
            candidate = candidate_references.markdown
            if candidate_references.added_numbers:
                context.log.info(
                    "survey.references_completed",
                    case_id=task.case_id,
                    stage="repair_candidate",
                    added_numbers=candidate_references.added_numbers,
                )
            candidate_analysis = analyze_report_evidence(candidate, report.documents, allowed)
            _log_analysis(context, candidate_analysis, "repair_candidate")
            candidate_audit = (
                audit_report(
                    candidate, pack, context, stage="survey.source_audit_candidate", candidate=True
                )
                if source_audit.complete
                else None
            )
            decision = decide_repair(
                analysis,
                candidate_analysis,
                min_sources=min_papers,
                semantic_before=len(source_audit.findings) if source_audit.complete else 0,
                semantic_after=len(candidate_audit.findings)
                if candidate_audit and candidate_audit.complete
                else 0,
            )
            if candidate_audit is not None and not candidate_audit.complete:
                decision = replace(decision, accepted=False, reason="source_audit_unavailable")
            context.log.info(
                "survey.repair_decision",
                case_id=task.case_id,
                accepted=decision.accepted,
                reason=decision.reason,
                before_priority=decision.before_priority,
                after_priority=decision.after_priority,
            )
            if decision.accepted:
                markdown, analysis = candidate, candidate_analysis
                context.log.info("survey.repair_applied", case_id=task.case_id)
            else:
                context.log.info(
                    "survey.repair_rejected",
                    case_id=task.case_id,
                    before=len(analysis.issues),
                    after=len(candidate_analysis.issues),
                )
        except (GatewayError, BudgetExhausted) as exc:
            context.log.warning(
                "survey.repair_failed_preserving_draft",
                case_id=task.case_id,
                error_type=type(exc).__name__,
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
