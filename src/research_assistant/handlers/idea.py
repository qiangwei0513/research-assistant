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
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from research_assistant.contracts.report_text import report_body_characters
from research_assistant.contracts.results import MIN_BODY_CHARACTERS, idea_result
from research_assistant.contracts.task import Task
from research_assistant.documents.evidence import (
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
from research_assistant.runtime.persistence import write_text_verified
from research_assistant.validation.citations import issue_summary
from research_assistant.validation.grounding import (
    analyze_report_evidence,
)
from research_assistant.validation.report_repair import complete_missing_references, decide_repair
from research_assistant.validation.source_audit import REPAIR_RESERVE, audit_report

if TYPE_CHECKING:  # pragma: no cover
    from research_assistant.runtime.context import SolveContext

STAGE_GENERATE = "idea.generate"
STAGE_REPAIR = "idea.repair"
MAX_TOKENS = 8192
REPAIR_BUDGET_SECONDS = 20.0
TARGET_BODY_CHARACTERS = 1800
# 实测 E-01 写到 4460 字符耗时 170.9s，单题上限 180s；必须给正文设上限。
MAX_BODY_CHARACTERS = 2600
PER_SECTION_CHARACTERS = 180
# C 类保留 6000 字符/篇（引用与页码核验需要详尽原文），E 类收紧到 2200
IDEA_MAX_CHARS_PER_DOCUMENT = 2200
MIN_TECHNICAL_CHARACTERS = 200

# 规则要求的分节；缺节直接压低档位，因此必须逐节自检
REQUIRED_SECTIONS: tuple[str, ...] = (
    "研究问题与边界",
    "动机与已有局限",
    "复现瓶颈与模拟观察",
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
# 证据边界必须表达的两层意思。提示词要求模型写**字面原句**（对裁判最稳妥），
# 但自检接受语义等价表达——否则模型写了「未经任何实验取得」这类同样合规的说法时，
# 会触发一轮约 60 秒的无谓修复，而单题时限只有 180 秒。
REQUIRED_BOUNDARY_STATEMENTS: tuple[str, ...] = (
    "模拟观察尚未验证",
    "所提改进尚未执行",
)
# 两层边界意思用**语义正则**判定，而不是枚举字面串。
# 教训：早先要求字面原句「模拟观察尚未验证」，实测模型写「以上数值均为设计预期，
# 未经任何实验取得」这类同样合规的表达就被判缺失，从而触发一轮约 60 秒的无谓修复；
# 而单题时限只有 180 秒。逐个扩充字面标记是打地鼠，改成「否定词 + 验证/执行类动词」
# 的小范围正则更稳健。提示词仍要求模型写字面原句（对裁判最稳妥）。
_NEGATION = r"(?:尚未|尚末|未曾|不曾|还未|没有|并未|未|待)"
_VERIFIED = r"(?:验证|检验|实验|测量|测试|实测|证实)"
_EXECUTED = r"(?:执行|实施|开展|运行|跑过|做过|完成)"
OBSERVATION_UNVERIFIED_PATTERN = re.compile(
    _NEGATION + r"[^。；;\n]{0,16}" + _VERIFIED + r"|" + r"(?:设计预期|期望目标|预期目标)"
)
IMPROVEMENT_NOT_RUN_PATTERN = re.compile(
    _NEGATION + r"[^。；;\n]{0,16}" + _EXECUTED + r"|" + r"不作(?:为|当作)已完成实验"
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


_METRIC_SECTION = "量化指标"
_METRIC_NAME_TOKENS = (
    "指标",
    "f1",
    "rmse",
    "mae",
    "auc",
    "mrr",
    "recall",
    "precision",
    "accuracy",
    "r²",
    "r2",
    "召回",
    "准确率",
    "误差",
    "吞吐",
    "时延",
    "延迟",
    "耗时",
    "百分点",
    "baseline",
    "score",
    "ndcg",
    "bleu",
    "rouge",
    "jain",
)
_METRIC_UNITS = (
    "%",
    "％",
    "ms",
    "秒",
    "k",
    "kw",
    "分",
    "个",
    "倍",
    "queries",
    "pp",
)


def _has_quantitative_metrics(markdown: str) -> bool:
    """「量化指标」小节里必须同时出现数字与可测量的指标名/单位。

    早先用固定单位后缀的正则（如 `\\d+%`、`提升\\d+%`）判定，实测把
    「R²（基线组预期0.52±0.02）」「RMSE 3.2K」「高4个百分点」这些完全合格的写法
    全部漏判为缺失，从而触发一轮无谓修复。改为「有数字 + 有指标名或单位」。
    """
    body = _section_body(markdown, _METRIC_SECTION)
    if not body.strip():
        # 小节标题缺失时退回全文，避免与 missing_sections 重复报同一个问题
        body = markdown
    lowered = body.lower()
    if not re.search(r"\d", lowered):
        return False
    return any(token in lowered for token in _METRIC_NAME_TOKENS) or any(
        unit in lowered for unit in _METRIC_UNITS
    )


def check_idea_report(markdown: str) -> IdeaCheck:
    headings = [h.strip() for h in _HEADING_LINE.findall(markdown)]
    joined_headings = " ".join(headings)
    missing_sections = tuple(
        section
        for section in REQUIRED_SECTIONS
        if section not in joined_headings
        or (section == "复现瓶颈与模拟观察" and len(_section_body(markdown, section).strip()) < 40)
    )
    missing_boundary = tuple(
        statement
        for statement, pattern in (
            (REQUIRED_BOUNDARY_STATEMENTS[0], OBSERVATION_UNVERIFIED_PATTERN),
            (REQUIRED_BOUNDARY_STATEMENTS[1], IMPROVEMENT_NOT_RUN_PATTERN),
        )
        if pattern.search(markdown) is None
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
    has_baseline = _has_baseline_comparison(markdown)
    has_metrics = _has_quantitative_metrics(markdown)

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
    "「复现瓶颈与模拟观察」须串联题面观察、暴露的机制限制、所提改动和证伪消融；"
    "题目未给出观察时明确说明未提供，提出待验证瓶颈假设，不得捏造已发生的复现。\n"
    "3. 「多文献互补」要说明各指定论文分别提供了什么、缺口在哪里，"
    "并标注引用编号 [n]。\n"
    "4. 证据边界必须明确写出："
    + "；".join(REQUIRED_BOUNDARY_STATEMENTS)
    + "。不得把未验证的改进效果写成已完成实验。\n"
    "5. 避免模板化套话（如「随着深度学习的发展」「具有重要意义」），"
    "写具体到本题材料的技术内容；重复句不计入篇幅。\n"
    "6. 论文实测数值必须在对应原文中有依据，不能从另一篇论文借用数字；原文只报告方向时不得补写幅度。"
    "方案的目标、样本量、成本和阈值须明确标为预设或待验证，不得伪装成文献实测基准。"
    "优先用原文的具体结果、反例与适用条件支撑改动；原文未给出的地区、气候、样本量或局限"
    "必须写明未说明，自己的推断标为待验证分析。原文节选未覆盖不等于整篇论文没有。"
    "不得编造事实、数据、页码或参考文献；结尾用 `## 参考文献` 列出实际引用的指定论文"
    "；每条只对应一篇论文，完整 PDF 文件名必须用成对反引号包裹。\n"
    f"7. 正文（不含参考文献与代码）控制在 {TARGET_BODY_CHARACTERS} 至 "
    f"{MAX_BODY_CHARACTERS} 字符之间，中文写作。**每节 {PER_SECTION_CHARACTERS} 字符左右即可，"
    "写要点不要展开成段落**：本题有严格的单题时限，实测写满 4000 字符要耗时 170 秒，"
    "逼近上限。优先保留有来源的瓶颈、互补机制、对照实验和证伪条件，字数下限不能代替实质内容。\n"
    "数值范围请用闭区间0至1等文字形式，不要写成与引文冲突的[0,1]。"
    "8. 只输出完整 Markdown 报告（含末尾参考文献表），不要代码围栏、不要任何前后缀说明。"
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


def _parse_markdown(content: str) -> str:
    """剥掉代码围栏并守住正文长度下限（至少 800 字符是硬合同）。"""
    stripped = _strip_code_fence(content)
    body_characters = report_body_characters(stripped)
    if body_characters < MIN_BODY_CHARACTERS:
        raise GatewayError(
            f"idea body was {body_characters} characters, below the required {MIN_BODY_CHARACTERS}"
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


def _unavailable_idea(task: Task, context: SolveContext, report: Any) -> Mapping[str, Any]:
    """零可用材料时的诚实方案：保持 E 类 Schema 与必需小节，不编造论文事实。"""
    context.log.warning(
        "idea.materials_unavailable",
        case_id=task.case_id,
        requested=len(task.materials or ()),
        statuses={d.filename: d.status.value for d in report.documents},
    )
    notes = {
        "研究问题与边界": "问题仍来自本题要求；由于授权材料不可读，研究边界暂不能依据原文细化。",
        "动机与已有局限": "无法核验原文所述局限，因此不给出具体文献判断，仅保留待核验位置。",
        "复现瓶颈与模拟观察": "仅能讨论题目明确给出的模拟观察，不能假定发生过真实复现；材料恢复后需核对原方法的机制限制，再设计针对性的改动与证伪消融。",
        "核心机制": "机制假设需由原文证据支撑；材料缺失时只登记假设，不写成已证实结论。",
        "技术路线": "恢复材料后按“读取—提取—对齐—评估”推进；当前仅保留流程定义。",
        "基线与改进组": "基线需从原文实验设置中提取；当前不指定具体数值或方法名。",
        "数据集": "数据集需按原文核对；当前不声称任何未经验证的数据来源。",
        "实验与消融": "消融设计需以原文方法为参照；当前仅登记待设计的实验维度。",
        "量化指标": "指标名称与单位须与题目要求一致；当前不报告任何未运行的数值。",
        "多文献互补": "多篇论文的互补关系需逐篇核对后建立；当前不臆测引用关系。",
        "可行性与成本": "成本评估依赖真实数据规模；当前只保留评估框架，不给出编造数字。",
        "失败边界与可证伪条件": "可证伪条件需与实验设置绑定；材料恢复后补充具体阈值与判定规则。",
    }
    requested = "\n".join(f"- {name}" for name in task.materials or ()) or "- （题目未列出材料）"
    markdown = (
        f"# {task.title}\n\n"
        "## 材料可用性声明\n\n"
        "本题指定的授权论文在当前运行环境中不可读取。为避免把推测写成论文结论，本方案"
        "只保留研究设计与待核验事项，不包含任何来自指定论文的具体事实或数据。\n\n"
        f"## 题目要求\n\n{task.input}\n\n"
        + "\n\n".join(f"## {name}\n\n{text}" for name, text in notes.items())
        + "\n\n## 证据边界与可证伪性\n\n"
        "上述内容均为待核验的设计说明，不构成对论文结果的复述。模拟观察尚未验证，"
        "任何效果判断都必须等待真实材料与实验数据；在材料恢复前，本方案不声称任何"
        "改进效果已经出现或可复现。恢复材料后应补充：明确的对照组与改进组定义、可"
        "复现的数据切分与随机种子、预先登记的量化阈值、以及否定该假设的判定条件。\n\n"
        "## 交付与复核清单\n\n"
        "提交前应逐项核对：每个小节是否给出可执行的步骤；指标名称与单位是否与题目"
        "一致；引用是否只来自本题指定论文并可在原文定位；成本与风险是否给出依据；"
        "失败条件是否可观测。任何无法核验的数值都应保持为空或明确标注未知。\n\n"
        f"## 指定材料清单\n\n{requested}\n"
    )
    path = context.workspace.idea_md(task.case_id)
    from research_assistant.runtime.persistence import write_text_verified

    write_text_verified(path, markdown)
    relative = context.workspace.relative_to_output(path)
    context.log.info("idea.completed_unavailable", case_id=task.case_id, path=relative)
    return idea_result(task.case_id, path=relative, markdown=markdown)


def solve_idea(task: Task, context: SolveContext) -> Mapping[str, Any]:
    report = extract_documents(
        context.materials,
        context.workspace,
        context.log,
        time_budget_seconds=max(5.0, min(60.0, context.deadline.remaining() - 120)),
    )
    # E 类是提方案，不像 C 类需要逐条引用的详尽证据；收紧证据包可显著缩短 prefill。
    # 实测 E 类生成耗时与输出长度强相关（60.9s/2809 字符 到 176.1s/4400 字符），
    # 而单题上限只有 180s，因此必须从输入侧压时间，而不能只靠提示词求模型写短。
    pack = build_evidence_pack(report.documents, max_chars_per_document=IDEA_MAX_CHARS_PER_DOCUMENT)
    if not pack.entries:
        return _unavailable_idea(task, context, report)

    allowed = frozenset(pack.filenames)

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
                f"完整的 Markdown 研究方案正文，去掉首尾空白后至少 {TARGET_BODY_CHARACTERS} 字符，"
                "含全部要求的小节与结尾的 ## 参考文献"
            ),
            parse=_parse_markdown,
            max_tokens=MAX_TOKENS,
        )

    try:
        markdown, response = _generate(pack, STAGE_GENERATE)
    except GatewayError as exc:
        if not is_context_length_error(exc.message):
            raise
        shrunk = shrink_budget(IDEA_MAX_CHARS_PER_DOCUMENT)
        pack = build_evidence_pack(report.documents, max_chars_per_document=shrunk)
        allowed = frozenset(pack.filenames)
        context.log.warning(
            "idea.context_overflow_shrinking",
            case_id=task.case_id,
            new_budget_per_document=shrunk,
            reason=exc.message[:200],
        )
        markdown, response = _generate(pack, STAGE_GENERATE + ".shrunk")
    reference_repair = complete_missing_references(markdown, pack)
    markdown = reference_repair.markdown
    if reference_repair.added_numbers:
        context.log.info(
            "idea.references_completed",
            case_id=task.case_id,
            added_numbers=reference_repair.added_numbers,
        )
    check = check_idea_report(markdown)
    citations = analyze_report_evidence(markdown, report.documents, allowed)
    _log_check(context, check, citations, "generate")
    source_audit = audit_report(markdown, pack, context, stage="idea.source_audit")

    problems = list(check.problems)
    problems.extend(issue.detail for issue in citations.issues)
    problems.extend(finding.hint() for finding in source_audit.findings)
    if problems and context.deadline.remaining() > REPAIR_BUDGET_SECONDS:
        try:
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
                            + "\n\n只输出修改后的完整 Markdown 报告，必须保留末尾 ## 参考文献表、全部实际引用的编号及用反引号包裹的完整PDF文件名；不要代码围栏、不要解释。"
                        ),
                    ),
                ),
                context=replace(
                    context,
                    deadline=replace(
                        context.deadline,
                        budget_seconds=context.deadline.budget_seconds
                        - (REPAIR_RESERVE if source_audit.complete else 5.0),
                    ),
                ),
                stage=STAGE_REPAIR,
                max_tokens=MAX_TOKENS,
            )
            candidate = _parse_markdown(repaired.content)
            candidate_references = complete_missing_references(candidate, pack)
            candidate = candidate_references.markdown
            if candidate_references.added_numbers:
                context.log.info(
                    "idea.references_completed",
                    case_id=task.case_id,
                    stage="repair_candidate",
                    added_numbers=candidate_references.added_numbers,
                )
            candidate_check = check_idea_report(candidate)
            candidate_citations = analyze_report_evidence(candidate, report.documents, allowed)
            _log_check(context, candidate_check, candidate_citations, "repair_candidate")
            candidate_audit = (
                audit_report(
                    candidate, pack, context, stage="idea.source_audit_candidate", candidate=True
                )
                if source_audit.complete
                else None
            )
            decision = decide_repair(
                citations,
                candidate_citations,
                content_before=len(check.problems),
                content_after=len(candidate_check.problems),
                semantic_before=len(source_audit.findings) if source_audit.complete else 0,
                semantic_after=len(candidate_audit.findings)
                if candidate_audit and candidate_audit.complete
                else 0,
            )
            if candidate_audit is not None and not candidate_audit.complete:
                decision = replace(decision, accepted=False, reason="source_audit_unavailable")
            context.log.info(
                "idea.repair_decision",
                case_id=task.case_id,
                accepted=decision.accepted,
                reason=decision.reason,
                before_priority=decision.before_priority,
                after_priority=decision.after_priority,
            )
            if decision.accepted:
                markdown, check, citations = candidate, candidate_check, candidate_citations
                context.log.info("idea.repair_applied", case_id=task.case_id)
            else:
                context.log.info(
                    "idea.repair_rejected",
                    case_id=task.case_id,
                    before=len(check.problems),
                    after=len(candidate_check.problems),
                )
        except (GatewayError, BudgetExhausted) as exc:
            context.log.warning(
                "idea.repair_failed_preserving_draft",
                case_id=task.case_id,
                error_type=type(exc).__name__,
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
        prompt_tokens=response.usage.prompt_tokens,
        completion_tokens=response.usage.completion_tokens,
        finish_reason=response.finish_reason,
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
