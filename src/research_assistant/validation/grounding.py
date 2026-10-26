"""Advisory source-specific numeric gaps; numeric presence is not semantic proof."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal, DecimalException

from research_assistant.documents.model import Document
from research_assistant.validation.citations import (
    CITATION_GROUP,
    CitationAnalysis,
    CitationIssue,
    analyze_citations,
)

KIND = "unverified_numeric_attribution"
_NUMBER = re.compile(
    r"(?<![A-Za-z0-9_.])([+-]?(?:\d+(?:\.\d+)?|\.\d+))(\s*(?:%|％|percent\b|per\s+cent\b))?", re.I
)
_FACT = re.compile(
    r"报告|实测|测得|发现|观测结果|实验结果|结果(?:表明|显示|为|达到)|基准(?:为|是)|优于|reported|achieved|observed",
    re.I,
)
_DESIGN = re.compile(r"目标|预设|预期|阈值|计划|拟|假设|设计|待验证|target|propos|planned", re.I)
_BREAK = re.compile(r"[。；;！？!?\n，,]|(?<!\d)\.(?!\d)")
_ONES = {
    word: i
    for i, word in enumerate(
        [
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
        ]
    )
}
_TENS = {
    word: value
    for word, value in zip(
        ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"],
        range(20, 100, 10),
        strict=True,
    )
}
_PERCENT_WORD = re.compile(
    r"\b("
    + "|".join([*_ONES, *_TENS, "one hundred"])
    + r")(?:(?:-|\s+)(one|two|three|four|five|six|seven|eight|nine))?\s+(?:percent|per\s+cent)\b",
    re.I,
)
_CHINESE = {char: i for i, char in enumerate("零一二三四五六七八九")}


def _normalized(text: str) -> str:
    def english(match):
        first = match[1].lower()
        value = 100 if first == "one hundred" else _ONES.get(first, _TENS.get(first, 0))
        return str(value + _ONES.get((match[2] or "").lower(), 0)) + "%"

    text = _PERCENT_WORD.sub(english, text)

    def chinese(match):
        token = match[1]
        if re.fullmatch(r"\d+(?:\.\d+)?", token):
            return token + "%"
        if token == "一百":
            return "100%"
        if "十" in token:
            left, right = token.split("十", 1)
            return str((_CHINESE.get(left, 1) * 10) + _CHINESE.get(right, 0)) + "%"
        return str(_CHINESE.get(token, 0)) + "%"

    return re.sub(
        r"百分之(\d+(?:\.\d+)?|一百|[一二三四五六七八九]?十[一二三四五六七八九]?|[零一二三四五六七八九])",
        chinese,
        text,
    )


def _quantities(text: str):
    for match in _NUMBER.finditer(text):
        try:
            value = Decimal(match[1])
            if match[2]:
                value /= 100
        except DecimalException:
            continue
        yield value, match


def _clause(analysis: CitationAnalysis, instance) -> str:
    paragraph = analysis.paragraphs[instance.paragraph_index]
    masked = CITATION_GROUP.sub(lambda m: " " * len(m[0]), paragraph)
    boundaries = list(_BREAK.finditer(masked))
    left = max((m.end() for m in boundaries if m.end() <= instance.char_offset), default=0)
    right = min(
        (m.start() for m in boundaries if m.start() > instance.char_offset), default=len(masked)
    )
    groups = list(CITATION_GROUP.finditer(paragraph[left:right]))
    by_number = {ref.number: ref.filename for ref in analysis.references}
    filenames = {by_number.get(int(n)) for group in groups for n in re.split(r"[,，]", group[1])}
    if len(groups) > 1 and len(filenames - {None}) > 1:
        # Multiple source-specific claims without clause punctuation are ambiguous.
        # Avoid treating every number as attributable to every separately cited paper.
        return ""
    return masked[left:right].strip()


def citation_issue_count(analysis: CitationAnalysis) -> int:
    return sum(issue.kind != KIND for issue in analysis.issues)


def numeric_issue_count(analysis: CitationAnalysis) -> int:
    return sum(issue.kind == KIND for issue in analysis.issues)


def _factual_scope(context: str, start: int, end: int) -> str:
    """Keep a paper-result parenthesis separate from surrounding proposal parameters."""
    stack: list[tuple[str, int]] = []
    ranges: list[tuple[int, int]] = []
    for index, char in enumerate(context):
        if char in "(（":
            stack.append((char, index))
        elif char in ")）" and stack:
            opening, left = stack[-1]
            if (opening, char) in (("(", ")"), ("（", "）")):
                stack.pop()
                ranges.append((left, index + 1))
    containing = [(left, right) for left, right in ranges if left <= start and end <= right]
    if containing:
        left, right = min(containing, key=lambda pair: pair[1] - pair[0])
        return context[left:right]
    masked = list(context)
    for left, right in ranges:
        masked[left:right] = " " * (right - left)
    return "".join(masked)


def analyze_report_evidence(
    markdown: str, documents: Sequence[Document], allowed: frozenset[str]
) -> CitationAnalysis:
    analysis = analyze_citations(markdown, allowed)
    documents_by_name = {d.filename: d for d in documents if d.usable and d.filename in allowed}
    references = {ref.number: ref.filename for ref in analysis.references}
    quantities_by_name = {}
    for name, document in documents_by_name.items():
        text = (
            document.full_text
            + " "
            + (document.metadata.abstract or "")
            + " "
            + str(document.metadata.year or "")
        )
        quantities_by_name[name] = {value for value, _ in _quantities(_normalized(text))}
    issues = list(analysis.issues)
    seen = set()
    for instance in analysis.instances:
        context = _normalized(_clause(analysis, instance))
        if not _FACT.search(context):
            continue
        if re.search(r"题目|题面|模拟观察", context) and not re.search(r"论文|文献|原文", context):
            continue
        names = {references.get(number) for number in instance.numbers} & documents_by_name.keys()
        if not names:
            continue  # Existing citation checks own dangling/unknown sources.
        supported = set().union(*(quantities_by_name[name] for name in names))
        missing = []
        for value, match in _quantities(context):
            if not _FACT.search(_factual_scope(context, match.start(), match.end())):
                continue
            surrounding = context[max(0, match.start() - 20) : match.end() + 12]
            if not _DESIGN.search(surrounding) and value not in supported:
                missing.append(match[0].strip())
        signature = (tuple(sorted(names)), tuple(missing), context)
        if not missing or signature in seen:
            continue
        seen.add(signature)
        issues.append(
            CitationIssue(
                KIND,
                "所引论文 "
                + ", ".join(sorted(names))
                + " 未检出数值 "
                + ", ".join(missing)
                + "；请核对、删除无据数值或明确为待验证目标。上下文："
                + context[:160],
            )
        )
    return replace(analysis, issues=tuple(issues))
