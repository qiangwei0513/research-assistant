"""引文质量自检，对应生效规则 ce-quality-v1 的「引文 10 分」。

本模块只做**可诊断的自动检查**，不实现也不声称官方裁判：档位判断由平台完成。
它的用途是在提交前发现会被扣分的具体问题——悬空编号、僵尸条目、未知材料、
打包引用、跨段落分布失衡、无实质上下文——好让生成端有机会修一轮。

规则依据（总纲 §5.4）：
- 合法性及正文/参考条目一致性 3 分：未知材料每篇扣 1.5，僵尸/悬空/重复编号每处扣 1，最低 0
- 指定材料覆盖 3 分：3 × 正文实际引用的不同允许材料数占比
- 反打包 1 分：每组 ≥3 个引用视为打包；打包占比 0 得 1，≤30% 得 0.5，否则 0
- 跨段落均衡 1 分：每篇被引材料至少出现在两个实质段落，且单篇实例占比 ≤80%
- 上下文与材料 Metadata 主题相关 2 分；实质上下文最低 12 有效字符
- 无有效正文引用时，引用分为 0；仅列在参考文献不等于正文引用
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

REFERENCES_HEADING = re.compile(r"^#{1,6}\s*参考文献\s*$", re.MULTILINE)
# [1]、[1, p.3]、[1,2]、[1, 2, p.5]、[12]
CITATION_GROUP = re.compile(r"\[\s*(\d+(?:\s*[,，]\s*\d+)*)(?:\s*[,，]\s*[pP]\.?\s*(\S+?))?\s*\]")
REFERENCE_LINE_NUMBER = re.compile(r"^\s*\[?(\d+)\]?[.、)）]?\s*(.+)$")
MIN_SUBSTANTIVE_CONTEXT = 12
PACKING_THRESHOLD = 3
MAX_SINGLE_PAPER_SHARE = 0.80
MIN_DISTINCT_PARAGRAPHS = 2


@dataclass(frozen=True)
class CitationInstance:
    """正文中的一次引用。`group_size` 是同一方括号内的编号数，用于判断打包。"""

    numbers: tuple[int, ...]
    page: str | None
    paragraph_index: int
    char_offset: int
    group_size: int
    context: str

    @property
    def is_packed(self) -> bool:
        return self.group_size >= PACKING_THRESHOLD

    @property
    def has_substantive_context(self) -> bool:
        return len(self.context.strip()) >= MIN_SUBSTANTIVE_CONTEXT


@dataclass(frozen=True)
class ReferenceEntry:
    number: int
    raw: str
    filename: str | None


@dataclass(frozen=True)
class CitationIssue:
    kind: str
    detail: str


@dataclass(frozen=True)
class CitationAnalysis:
    body: str
    references_section: str
    paragraphs: tuple[str, ...]
    instances: tuple[CitationInstance, ...]
    references: tuple[ReferenceEntry, ...]
    allowed_filenames: frozenset[str]
    issues: tuple[CitationIssue, ...] = ()

    @property
    def cited_numbers(self) -> frozenset[int]:
        return frozenset(n for instance in self.instances for n in instance.numbers)

    @property
    def reference_numbers(self) -> frozenset[int]:
        return frozenset(entry.number for entry in self.references)

    @property
    def dangling_numbers(self) -> frozenset[int]:
        """正文引用了但参考文献没有。"""
        return self.cited_numbers - self.reference_numbers

    @property
    def zombie_numbers(self) -> frozenset[int]:
        """参考文献列了但正文从未引用。仅列在参考文献不等于正文引用。"""
        return self.reference_numbers - self.cited_numbers

    @property
    def duplicate_reference_numbers(self) -> frozenset[int]:
        counts = Counter(entry.number for entry in self.references)
        return frozenset(n for n, c in counts.items() if c > 1)

    @property
    def unknown_filenames(self) -> frozenset[str]:
        return frozenset(
            entry.filename
            for entry in self.references
            if entry.filename is not None and entry.filename not in self.allowed_filenames
        )

    @property
    def cited_filenames(self) -> frozenset[str]:
        """正文实际引用到的授权材料文件名。"""
        by_number = {entry.number: entry.filename for entry in self.references}
        return frozenset(
            by_number[n] for n in self.cited_numbers if by_number.get(n) in self.allowed_filenames
        )

    @property
    def has_body_citations(self) -> bool:
        return bool(self.instances)

    @property
    def packed_instance_count(self) -> int:
        return sum(1 for instance in self.instances if instance.is_packed)

    @property
    def packing_ratio(self) -> float:
        if not self.instances:
            return 0.0
        return self.packed_instance_count / len(self.instances)

    def paragraphs_for(self, number: int) -> frozenset[int]:
        return frozenset(
            instance.paragraph_index
            for instance in self.instances
            if number in instance.numbers and instance.has_substantive_context
        )

    @property
    def unbalanced_numbers(self) -> frozenset[int]:
        """未出现在至少两个实质段落里的被引编号。"""
        return frozenset(
            n for n in self.cited_numbers if len(self.paragraphs_for(n)) < MIN_DISTINCT_PARAGRAPHS
        )

    @property
    def dominant_number(self) -> int | None:
        """单篇实例占比超过 80% 时返回该编号。"""
        counts = Counter(n for instance in self.instances for n in instance.numbers)
        total = sum(counts.values())
        if not total:
            return None
        for number, count in counts.most_common(1):
            if count / total > MAX_SINGLE_PAPER_SHARE:
                return number
        return None

    @property
    def context_free_instances(self) -> tuple[CitationInstance, ...]:
        return tuple(i for i in self.instances if not i.has_substantive_context)

    def coverage_ratio(self) -> float:
        """覆盖项 = 正文实际引用的不同允许材料数 / 允许材料总数。"""
        if not self.allowed_filenames:
            return 0.0
        return len(self.cited_filenames) / len(self.allowed_filenames)


def split_references(markdown: str) -> tuple[str, str]:
    """按 `## 参考文献` 切出正文与参考文献段。找不到时参考文献段为空。"""
    match = REFERENCES_HEADING.search(markdown)
    if match is None:
        return markdown, ""
    return markdown[: match.start()], markdown[match.end() :]


def split_paragraphs(body: str) -> tuple[str, ...]:
    """按空行分段，丢弃空白段。与规则里的「实质段落」对齐。"""
    return tuple(p.strip() for p in re.split(r"\n\s*\n", body) if p.strip())


def _context_around(paragraph: str, start: int, end: int) -> str:
    """取引用所在的那一句，去掉引用标记本身，作为实质上下文。"""
    left = paragraph.rfind("。", 0, start)
    right = paragraph.find("。", end)
    sentence = paragraph[left + 1 : right if right >= 0 else len(paragraph)]
    return CITATION_GROUP.sub(" ", sentence).strip()


def find_instances(body: str) -> tuple[CitationInstance, ...]:
    paragraphs = split_paragraphs(body)
    instances: list[CitationInstance] = []
    for paragraph_index, paragraph in enumerate(paragraphs):
        for match in CITATION_GROUP.finditer(paragraph):
            if _is_numeric_interval(paragraph, match):
                continue
            numbers = tuple(
                int(part) for part in re.split(r"[,，]", match.group(1)) if part.strip()
            )
            if not numbers:
                continue
            instances.append(
                CitationInstance(
                    numbers=numbers,
                    page=match.group(2),
                    paragraph_index=paragraph_index,
                    char_offset=match.start(),
                    group_size=len(numbers),
                    context=_context_around(paragraph, match.start(), match.end()),
                )
            )
    return tuple(instances)


def _is_numeric_interval(text: str, match: re.Match[str]) -> bool:
    numbers = [int(n) for n in re.split(r"[,，]", match[1])]
    if len(numbers) != 2 or match[2] is not None:
        return False
    before, after = text[max(0, match.start() - 16) : match.start()], text[match.end() :]
    if re.search(r"∈\s*$", before):
        return True
    return 0 in numbers and (
        re.search(r"(?:归一化(?:到|至)?|统一|区间|范围)\s*$", before) is not None
        or re.match(r"\s*(?:区间|空间|分箱|范围)", after) is not None
    )


_QUOTED_LOCATOR = re.compile(r"""(?<![^\s(:：（])(?:`[^`\n]*`|"[^"\n]*"|'[^'\n]*')""")
_LOCATOR_END = re.compile(r"(?:$|[\s,，。;；)）\]]|\.(?:\s|$))")
_PDF_FRAGMENT = re.compile(r"\.pdf\b", re.IGNORECASE)


def _extract_filename(raw: str, allowed: frozenset[str] = frozenset()) -> str | None:
    """Resolve one complete locator; never authorize an arbitrary filename substring.

    Free author/title prose cannot prove the start of a space-containing filename.
    Require delimiters or a label there; the repair hint explains this constraint.
    Unknown explicit locators are returned intact so membership stays exact.
    """
    text = raw.strip()
    bare = text.rstrip(" .。")
    if bare in allowed:
        return bare
    candidates: list[str] = []
    outside = list(text)
    # Scan all quote styles together, left to right. An outer quoted span owns
    # its contents; an inner backtick cannot authorize a suffix of that name.
    for match in _QUOTED_LOCATOR.finditer(text):
        candidate = match[0][1:-1]
        outside[match.start() : match.end()] = " " * len(match[0])
        if not _PDF_FRAGMENT.search(candidate):
            continue
        if not candidate.lower().endswith(".pdf") or not _LOCATOR_END.match(text[match.end() :]):
            return None
        candidates.append(candidate)
    if candidates:
        if len(candidates) == 1 and not _PDF_FRAGMENT.search("".join(outside)):
            return candidates[0]
        return None
    labelled = re.search(
        r"(?:^|[\s（(,，;；])(?:PDF\s*文件名|PDF|文件名)\s*[:：]\s*(.+)$", bare, re.IGNORECASE
    )
    if labelled:
        candidate = labelled[1]
        if _PDF_FRAGMENT.search(bare[: labelled.start(1)]):
            return None
        if candidate in allowed:
            return candidate
        if (
            candidate.lower().endswith(".pdf")
            and len(_PDF_FRAGMENT.findall(candidate)) == 1
            and not any(q in candidate for q in "`\"'")
        ):
            return candidate
    # Single bare unknown names can be diagnosed without guessing metadata boundaries.
    if re.fullmatch(r"[^\s`\"'<>\[\]()（）]+\.pdf", bare, re.IGNORECASE):
        return bare
    return None


def find_references(
    section: str, allowed: frozenset[str] = frozenset()
) -> tuple[ReferenceEntry, ...]:
    entries: list[ReferenceEntry] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        cleaned = re.sub(r"^[-*+]\s*", "", stripped)
        match = REFERENCE_LINE_NUMBER.match(cleaned)
        if match is None:
            continue
        entries.append(
            ReferenceEntry(
                number=int(match.group(1)),
                raw=cleaned,
                filename=_extract_filename(match.group(2), allowed),
            )
        )
    return tuple(entries)


def analyze_citations(markdown: str, allowed_filenames: Iterable[str]) -> CitationAnalysis:
    body, references_section = split_references(markdown)
    paragraphs = split_paragraphs(body)
    instances = find_instances(body)
    allowed = frozenset(allowed_filenames)
    references = find_references(references_section, allowed)
    analysis = CitationAnalysis(
        body=body,
        references_section=references_section,
        paragraphs=paragraphs,
        instances=instances,
        references=references,
        allowed_filenames=allowed,
    )
    return _with_issues(analysis)


def _with_issues(analysis: CitationAnalysis) -> CitationAnalysis:
    issues: list[CitationIssue] = []
    for match in CITATION_GROUP.finditer(analysis.body):
        if _is_numeric_interval(analysis.body, match):
            issues.append(
                CitationIssue(
                    "ambiguous_numeric_brackets",
                    f"数学区间{match[0]}易与文献引用混淆；请改写为文字区间（如闭区间0至1），不要增添引用编号0。",
                )
            )
    if not analysis.instances:
        issues.append(CitationIssue("no_body_citations", "正文没有任何数字引用；引用分将为 0"))
    for number in sorted(analysis.dangling_numbers):
        issues.append(CitationIssue("dangling_number", f"正文引用 [{number}] 但参考文献没有该条"))
    for number in sorted(analysis.zombie_numbers):
        issues.append(CitationIssue("zombie_number", f"参考文献 [{number}] 正文从未引用"))
    for number in sorted(analysis.duplicate_reference_numbers):
        issues.append(CitationIssue("duplicate_number", f"参考文献中 [{number}] 出现多次"))
    for filename in sorted(analysis.unknown_filenames):
        issues.append(CitationIssue("unknown_material", f"参考文献列了未授权材料 {filename}"))
    for entry in analysis.references:
        if entry.filename is None:
            issues.append(
                CitationIssue(
                    "missing_filename",
                    f"参考文献 [{entry.number}] 无法唯一识别完整 PDF 文件名；"
                    "请只列一个文件名并用反引号包裹，不得混入第二个 PDF",
                )
            )
    if analysis.packing_ratio > 0:
        issues.append(
            CitationIssue(
                "packed_citations",
                f"{analysis.packed_instance_count}/{len(analysis.instances)} 组引用一次列了 "
                f"{PACKING_THRESHOLD} 个以上编号（占比 {analysis.packing_ratio:.0%}）",
            )
        )
    for number in sorted(analysis.unbalanced_numbers):
        issues.append(
            CitationIssue(
                "unbalanced_distribution",
                f"[{number}] 未出现在至少 {MIN_DISTINCT_PARAGRAPHS} 个实质段落中",
            )
        )
    dominant = analysis.dominant_number
    if dominant is not None:
        issues.append(
            CitationIssue(
                "dominant_source",
                f"[{dominant}] 占了全部引用实例的 {MAX_SINGLE_PAPER_SHARE:.0%} 以上",
            )
        )
    for instance in analysis.context_free_instances:
        issues.append(
            CitationIssue(
                "thin_context",
                f"第 {instance.paragraph_index + 1} 段的引用 "
                f"[{','.join(str(n) for n in instance.numbers)}] 实质上下文不足 "
                f"{MIN_SUBSTANTIVE_CONTEXT} 字符",
            )
        )
    return CitationAnalysis(
        body=analysis.body,
        references_section=analysis.references_section,
        paragraphs=analysis.paragraphs,
        instances=analysis.instances,
        references=analysis.references,
        allowed_filenames=analysis.allowed_filenames,
        issues=tuple(issues),
    )


def issue_summary(analysis: CitationAnalysis) -> dict[str, int]:
    """按类型汇总，用于日志与修复提示，不含论文正文。"""
    return dict(Counter(issue.kind for issue in analysis.issues))


@dataclass(frozen=True)
class RepairHint:
    """给模型的一轮定向修复提示。只描述问题，不代写内容。"""

    lines: tuple[str, ...]

    def as_prompt_block(self) -> str:
        return "\n".join(f"- {line}" for line in self.lines)


def build_repair_hint(analysis: CitationAnalysis, *, min_distinct_papers: int) -> RepairHint:
    lines: list[str] = []
    kinds = issue_summary(analysis)
    if kinds.get("no_body_citations"):
        lines.append("正文必须出现数字引用 [编号]，只在参考文献里列出不算引用。")
    if kinds.get("dangling_number"):
        lines.append(
            "以下编号在正文出现但参考文献没有："
            + ", ".join(str(n) for n in sorted(analysis.dangling_numbers))
            + "；请补齐条目或改正编号。"
        )
    if kinds.get("zombie_number"):
        lines.append(
            "以下参考文献条目正文从未引用："
            + ", ".join(str(n) for n in sorted(analysis.zombie_numbers))
            + "；请在正文中实际引用，或删除该条。"
        )
    if kinds.get("duplicate_number"):
        lines.append("参考文献存在重复编号，请合并为唯一编号。")
    if kinds.get("unknown_material"):
        lines.append(
            "以下材料不在本题授权范围内，必须删除：" + ", ".join(sorted(analysis.unknown_filenames))
        )
    if kinds.get("missing_filename"):
        lines.append(
            "每条参考文献必须只对应一份材料；完整 PDF 文件名用反引号包裹，"
            "例如 [1] 作者. 标题. `完整文件名.pdf`，或编号后只写完整文件名。"
            "不得省略前缀或把多个 PDF 塞进同一编号。"
        )
    if kinds.get("ambiguous_numeric_brackets"):
        lines.append(
            "数学区间不要使用类似[0,1]的引文括号；请用闭区间0至1等文字形式，保留原数值与边界含义。"
        )
    cited = len(analysis.cited_filenames)
    if cited < min_distinct_papers:
        lines.append(
            f"正文实际引用的不同授权材料只有 {cited} 篇，至少需要 {min_distinct_papers} 篇；"
            "请补充其他指定论文的真实证据，不得编造第 "
            f"{min_distinct_papers} 篇。"
        )
    if kinds.get("packed_citations"):
        lines.append(
            f"有 {analysis.packed_instance_count} 处一次引用了 {PACKING_THRESHOLD} 个以上编号，"
            "请把打包引用拆成服务于具体陈述的单篇或双篇引用。"
        )
    if kinds.get("unbalanced_distribution"):
        lines.append(
            "以下编号未出现在至少两个实质段落中："
            + ", ".join(str(n) for n in sorted(analysis.unbalanced_numbers))
            + "；请在不同段落分别就其真实贡献引用。"
        )
    if kinds.get("dominant_source"):
        lines.append(f"编号 [{analysis.dominant_number}] 占比过高，请均衡使用多篇材料。")
    if kinds.get("thin_context"):
        lines.append(
            f"有 {len(analysis.context_free_instances)} 处引用的实质上下文不足 "
            f"{MIN_SUBSTANTIVE_CONTEXT} 字符，请为引用补上具体陈述。"
        )
    return RepairHint(lines=tuple(lines))


def page_evidence(
    documents: Mapping[str, object], claims: Sequence[tuple[str, str]]
) -> tuple[CitationIssue, ...]:
    """核验 [编号, p.页码] 的页码是否真有证据支撑。

    `claims` 是 (filename, 声称的原文片段) 序列；片段找不到就报问题。
    页码本身可用，但必须有证据——总纲明确禁止编造页码。
    """
    issues: list[CitationIssue] = []
    for filename, needle in claims:
        document = documents.get(filename)
        locator = getattr(document, "page_containing", None)
        if locator is None:
            continue
        if locator(needle) is None:
            issues.append(
                CitationIssue(
                    "unverifiable_page",
                    f"{filename} 中找不到声称的原文片段，页码不得作为证据使用",
                )
            )
    return tuple(issues)


__all__ = [
    "CITATION_GROUP",
    "MIN_SUBSTANTIVE_CONTEXT",
    "PACKING_THRESHOLD",
    "CitationAnalysis",
    "CitationInstance",
    "CitationIssue",
    "ReferenceEntry",
    "RepairHint",
    "analyze_citations",
    "build_repair_hint",
    "find_instances",
    "find_references",
    "issue_summary",
    "page_evidence",
    "split_paragraphs",
    "split_references",
]
