"""E 类（Idea）handler。

评分为 15 × 八要点平均档位 × 专属性系数，再减扣分（总纲 §5.5）：
八要点＝研究动机、技术路线、实验设计、基准数据集、量化指标、复现瓶颈反思、
多文献互补、场景可行性；系数 high=1 / medium=0.85 / generic_template=0.55；
缺明确基线与改进比较、缺具体量化指标、技术内容不足 200 字符各扣 1，最多扣 3。
参考文献、代码和重复句不计入技术篇幅。

档位判断属于官方裁判；本模块只做可诊断自检，并在预算允许时做一轮定向修复。
证据边界必须写清：模拟观察尚未验证、所提改进尚未执行。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from research_assistant.contracts.results import idea_result
from research_assistant.contracts.task import Task
from research_assistant.documents.evidence import (
    EvidencePack,
    build_evidence_pack,
    render_evidence_pack,
    render_unusable,
)
from research_assistant.documents.extractor import extract_documents
from research_assistant.errors import MaterialUnavailableError
from research_assistant.runtime.gateway import Message
from research_assistant.runtime.persistence import write_text_verified
from research_assistant.validation.citations import analyze_citations, issue_summary

if TYPE_CHECKING:  # pragma: no cover
    from research_assistant.runtime.context import SolveContext

STAGE_GENERATE = "idea.generate"
STAGE_REPAIR = "idea.repair"
MAX_TOKENS = 8192
REPAIR_BUDGET_SECONDS = 20.0
TARGET_BODY_CHARACTERS = 1800
MIN_TECHNICAL_CHARACTERS = 200

# 规则要求的分节；缺节直接压低档位，因此必须逐节自检
REQUIRED_SECTIONS: tuple[str, ...] = (
    "研究问题与边界",
    "动机与已有局限",
    "核心机制",
    "技术路线",
    "基线与改进组",
    "数据集",
    "实验与消融",
    "量化指标",
    "多文献互补",
    "可行性与成本",
    "失败边界与可证伪条件",
)
# 证据边界必须出现的两处声明
REQUIRED_BOUNDARY_STATEMENTS: tuple[str, ...] = (
    "模拟观察尚未验证",
    "所提改进尚未执行",
)
# 会被判为 generic_template 的模板化句式（专属性系数会从 1 掉到 0.55）
GENERIC_TEMPLATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"随着(?:人工智能|深度学习|大模型)的(?:快速)?发展"),
    re.compile(r"在(?:当今|如今|当前)(?:社会|时代|背景下)"),
    re.compile(r"具有重要的(?:理论)?(?:意义|价值)(?:和|与)?(?:广阔)?(?:应用)?(?:前景)?[。.]?$"),
    re.compile(r"本文(?:将|会)?(?:从|拟)?(?:以下|如下)?(?:几|若干)?(?:个)?(?:方面|维度)"),
    re.compile(r"综上所述[，,]?本(?:文|研究|方案)"),
)
_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_HEADING_LINE = re.compile(r"^#{1,6}\s*(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class IdeaCheck:
    missing_sections: tuple[str, ...]
    missing_boundary_statements: tuple[str, ...]
    generic_template_hits: tuple[str, ...]
    technical_characters: int
    has_baseline_comparison: bool
    has_quantitative_metrics: bool
    repeated_sentence_count: int

    @property
    def problems(self) -> tuple[str, ...]:
        out: list[str] = []
        for name in self.missing_sections:
            out.append(f"缺少小节：{name}")
        for statement in self.missing_boundary_statements:
            out.append(f"缺少证据边界声明：{statement}")
        if not self.has_baseline_comparison:
            out.append("缺少明确的基线与改进组比较（扣 1 分）")
        if not self.has_quantitative_metrics:
            out.append("缺少具体量化指标（扣 1 分）")
        if self.technical_characters < MIN_TECHNICAL_CHARACTERS:
            out.append(
                f"技术内容仅 {self.technical_characters} 字符，不足 "
                f"{MIN_TECHNICAL_CHARACTERS}（扣 1 分）"
            )
        if self.generic_template_hits:
            out.append(
                "出现模板化句式，专属性系数可能被判为 generic_template（0.55）："
                + "；".join(self.generic_template_hits)
            )
        if self.repeated_sentence_count:
            out.append(f"有 {self.repeated_sentence_count} 处重复句；重复句不计入技术篇幅")
        return tuple(out)


def _strip_code_and_references(markdown: str) -> str:
    body = _CODE_FENCE.sub(" ", markdown)
    match = re.search(r"^#{1,6}\s*参考文献\s*$", body, re.MULTILINE)
    if match:
        body = body[: match.start()]
    return body


def _sentences(text: str) -> list[str]:
    parts = re.split(r"[。！？!?\n]", text)
    return [p.strip() for p in parts if len(p.strip()) >= 8]


_BASELINE_SECTION = "基线与改进组"
_BASELINE_WORDS = ("基线", "对照组", "baseline", "对照")
_COMPARISON_WORDS = ("改进", "对比", "比较", "相比", "vs", "提升", "差异")


def _section_body(markdown: str, heading: str) -> str:
    """取出某个小节的正文（到下一个同级或更高级标题为止）。"""
    match = re.search(rf"^#{{1,6}}\s*{re.escape(heading)}\s*$", markdown, re.MULTILINE)
    if match is None:
        return ""
    level = len(match.group(0).strip()) - len(match.group(0).strip().lstrip("#"))
    rest = markdown[match.end() :]
    next_heading = re.search(rf"^#{{1,{level}}}\s", rest, re.MULTILINE)
    return rest[: next_heading.start()] if next_heading else rest


def _has_baseline_comparison(markdown: str) -> bool:
    """必须在「基线与改进组」小节里同时出现基线对象与比较方式。

    只看全文关键词会被小节标题本身骗过——标题里就有「基线」二字。
    """
    body = _section_body(markdown, _BASELINE_SECTION)
    if not body.strip():
        return False
    return any(w in body for w in _BASELINE_WORDS) and any(w in body for w in _COMPARISON_WORDS)


def check_idea_report(markdown: str) -> IdeaCheck:
    headings = [h.strip() for h in _HEADING_LINE.findall(markdown)]
    joined_headings = " ".join(headings)
    missing_sections = tuple(
        section for section in REQUIRED_SECTIONS if section not in joined_headings
    )
    missing_boundary = tuple(
        statement
        for statement in REQUIRED_BOUNDARY_STATEMENTS
        if statement not in markdown.replace(" ", "")
    )

    technical = _strip_code_and_references(markdown)
    sentences = _sentences(technical)
    seen: set[str] = set()
    repeats = 0
    unique: list[str] = []
    for sentence in sentences:
        if sentence in seen:
            repeats += 1
        else:
            seen.add(sentence)
            unique.append(sentence)
    # 参考文献、代码与重复句都不计入技术篇幅
    technical_characters = len(re.sub(r"\s+", "", "".join(unique)))

    generic_hits = tuple(
        sorted(
            {pattern.pattern for pattern in GENERIC_TEMPLATE_PATTERNS if pattern.search(markdown)}
        )
    )
    lowered = markdown
    has_baseline = _has_baseline_comparison(markdown)
    has_metrics = bool(
        re.search(r"\d+(?:\.\d+)?\s*(?:%|百分点|ms|秒|F1|BLEU|AUC|MRR|Recall)", lowered)
    ) or bool(re.search(r"(?:提升|下降|降低|减少)\s*\d+(?:\.\d+)?\s*(?:%|个百分点)", lowered))

    return IdeaCheck(
        missing_sections=missing_sections,
        missing_boundary_statements=missing_boundary,
        generic_template_hits=generic_hits,
        technical_characters=technical_characters,
        has_baseline_comparison=has_baseline,
        has_quantitative_metrics=has_metrics,
        repeated_sentence_count=repeats,
    )


SYSTEM_PROMPT = (
    "你是一名研究方案设计者。依据给定材料的真实内容提出一个**具体、可证伪**的研究方案。"
    "严格遵守：\n"
    "1. 必须包含以下小节，标题原文照抄，各节都要有实质内容：\n"
    + "\n".join(f"   - {name}" for name in REQUIRED_SECTIONS)
    + "\n2. 「基线与改进组」必须给出明确的基线方法与改进方法及其比较方式；"
    "「量化指标」必须给出可测量的指标名与预期数值变化。\n"
    "3. 「多文献互补」要说明各指定论文分别提供了什么、缺口在哪里，"
    "并标注引用编号 [n]。\n"
    "4. 证据边界必须明确写出："
    + "；".join(REQUIRED_BOUNDARY_STATEMENTS)
    + "。不得把未验证的改进效果写成已完成实验。\n"
    "5. 避免模板化套话（如「随着深度学习的发展」「具有重要意义」），"
    "写具体到本题材料的技术内容；重复句不计入篇幅。\n"
    "6. 不得编造事实、数据、页码或参考文献；结尾用 `## 参考文献` 列出实际引用的指定论文"
    "及完整 PDF 文件名。\n"
    f"7. 正文（不含参考文献与代码）至少 {TARGET_BODY_CHARACTERS} 字符，中文写作。\n"
    "8. 只输出 Markdown 正文本身，不要代码围栏、不要任何前后缀说明。"
)


def build_user_prompt(task: Task, pack: EvidencePack) -> str:
    parts = [f"# 研究方案任务\n{task.input}", f"# 案例标题\n{task.title}"]
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
    parts.append("请按系统要求的全部小节写出完整 Markdown 研究方案。")
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
    return "\n".join(lines).strip() + "\n"


def solve_idea(task: Task, context: SolveContext) -> Mapping[str, Any]:
    report = extract_documents(task.materials, context.workspace, context.log)
    pack = build_evidence_pack(report.documents)
    if not pack.entries:
        raise MaterialUnavailableError(
            "cannot design a research plan without usable materials",
            case_id=task.case_id,
        )

    allowed = frozenset(pack.filenames)
    response = context.gateway.complete(
        (
            Message(role="system", content=SYSTEM_PROMPT),
            Message(role="user", content=build_user_prompt(task, pack)),
        ),
        context=context,
        stage=STAGE_GENERATE,
        max_tokens=MAX_TOKENS,
    )
    markdown = _strip_code_fence(response.content)
    check = check_idea_report(markdown)
    citations = analyze_citations(markdown, allowed)
    _log_check(context, check, citations, "generate")

    problems = list(check.problems)
    if not citations.has_body_citations and pack.entries:
        problems.append("正文没有任何 [编号] 引用；多文献互补无法核验。")
    if problems and context.deadline.remaining() > REPAIR_BUDGET_SECONDS:
        repaired = context.gateway.complete(
            (
                Message(role="system", content=SYSTEM_PROMPT),
                Message(role="user", content=build_user_prompt(task, pack)),
                Message(role="assistant", content=markdown),
                Message(
                    role="user",
                    content=(
                        "自检发现以下会被扣分的具体问题。请在不编造事实、不新增未授权材料、"
                        "不改变已有引用编号含义的前提下重写整篇方案修掉它们：\n"
                        + "\n".join(f"- {p}" for p in problems)
                        + "\n\n只输出修改后的完整 Markdown 正文，不要代码围栏、不要解释。"
                    ),
                ),
            ),
            context=context,
            stage=STAGE_REPAIR,
            max_tokens=MAX_TOKENS,
        )
        candidate = _strip_code_fence(repaired.content)
        candidate_check = check_idea_report(candidate)
        # 只有问题确实变少才采用，避免修复反而破坏原稿
        if candidate.strip() and len(candidate_check.problems) < len(check.problems):
            markdown, check = candidate, candidate_check
            citations = analyze_citations(markdown, allowed)
            context.log.info("idea.repair_applied", case_id=task.case_id)
        else:
            context.log.info(
                "idea.repair_rejected",
                case_id=task.case_id,
                before=len(check.problems),
                after=len(candidate_check.problems),
            )
        _log_check(context, check, citations, "repair")

    path = context.workspace.idea_md(task.case_id)
    # 生成唯一正文字符串：先落盘再回读比对，不得写文件后又让模型改 JSON 正文
    write_text_verified(path, markdown)
    relative = context.workspace.relative_to_output(path)
    context.log.info(
        "idea.completed",
        case_id=task.case_id,
        path=relative,
        characters=len(markdown),
        technical_characters=check.technical_characters,
        remaining_problems=len(check.problems),
    )
    return idea_result(task.case_id, path=relative, markdown=markdown)


def _log_check(context: SolveContext, check: IdeaCheck, citations: Any, stage: str) -> None:
    context.log.info(
        "idea.self_check",
        case_id=context.case_id,
        stage=stage,
        technical_characters=check.technical_characters,
        missing_sections=list(check.missing_sections),
        missing_boundary=list(check.missing_boundary_statements),
        generic_template_hits=len(check.generic_template_hits),
        has_baseline_comparison=check.has_baseline_comparison,
        has_quantitative_metrics=check.has_quantitative_metrics,
        repeated_sentences=check.repeated_sentence_count,
        citation_issues=issue_summary(citations),
    )


def missing_sections_of(markdown: str) -> Sequence[str]:
    return check_idea_report(markdown).missing_sections
