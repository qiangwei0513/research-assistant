"""引文质量自检测试，对应 ce-quality-v1 的引文 10 分五个子项。"""

from __future__ import annotations

import pytest

from research_assistant.validation.citations import (
    MIN_SUBSTANTIVE_CONTEXT,
    PACKING_THRESHOLD,
    analyze_citations,
    build_repair_hint,
    find_instances,
    find_references,
    issue_summary,
    split_paragraphs,
    split_references,
)

A = "城市绿地遥感_2024.pdf"
B = "街区植被与热环境_2022.pdf"
C = "电池回收生命周期_2021.pdf"
D = "卫星图像分割_2023.pdf"
E = "第五篇授权材料_2020.pdf"
ALLOWED = frozenset({A, B, C, D, E})


def good_survey() -> str:
    """一篇各项都合格的综述：5 篇均被正文引用、每篇跨两段、无打包、上下文充分。"""
    return f"""# 城市绿地与热环境研究综述

## 研究问题与方法比较

城市绿地对地表温度的调节作用是本文比较的核心问题。第一段用多光谱遥感反演植被覆盖，
并以热红外波段反演地表温度，二者在区级尺度上做相关分析 [1]。该方法的优势是覆盖范围大，
局限是受云层与混合像元影响明显。第二段改以街区尺度植被指数回归实测气温 [2]，
分辨率更高但样本量受限 [1]。

## 数据与指标

数据来源上，第一项研究使用 Landsat 时序影像与土地利用分类 [2]，第二项使用高分辨率航拍
配合地面温度记录 [1]。指标方面二者都报告了植被覆盖比例与温度的回归系数 [3]，
但归一化方式不同，导致跨研究可比性有限 [4]，这一点在后续工作中被再次确认 [5]。

## 结论、局限与适用边界

结论层面，多项研究一致发现植被比例越高、地表温度越低，但降温效应存在饱和阈值 [5]。
局限性包括缺乏对夜间热环境的观测 [1]，以及对城市形态变量的控制不足 [2]。
适用边界上，这些结论主要来自温带城市 [3]，向高密度热带城市外推需要谨慎 [4]，
后续工作应补充冠层结构与蒸散量的直接观测 [5]。

## 参考文献

[1] 张三, 李四. 城市绿地遥感反演研究. `{A}`
[2] 王五. 街区植被与城市热岛. `{B}`
[3] 赵六. 电池回收与生命周期评价. `{C}`
[4] 孙七. 卫星图像语义分割. `{D}`
[5] 周八. 第五篇授权材料研究. `{E}`
"""


# --- 切分 ---


def test_split_references_separates_body_from_reference_list() -> None:
    body, refs = split_references(good_survey())
    assert "## 参考文献" not in body
    assert refs.strip().startswith("[1]")
    assert "城市绿地对地表温度的调节作用" in body


def test_split_references_without_a_heading_returns_empty_section() -> None:
    body, refs = split_references("# 只有正文\n\n没有参考文献小节。")
    assert refs == ""
    assert "只有正文" in body


def test_split_paragraphs_drops_blank_segments() -> None:
    assert split_paragraphs("a\n\n\n  \nb\n") == ("a", "b")


# --- 引用实例解析 ---


def test_finds_plain_numeric_citations() -> None:
    instances = find_instances("植被比例越高温度越低 [1]，但存在饱和阈值 [2]。")
    assert [i.numbers for i in instances] == [(1,), (2,)]
    assert all(i.page is None for i in instances)


def test_finds_page_qualified_citations() -> None:
    instances = find_instances("如文献所述 [1, p.3]，另有 [2, p.12] 支持。")
    assert instances[0].numbers == (1,)
    assert instances[0].page == "3"
    assert instances[1].page == "12"


def test_accepts_chinese_comma_and_optional_p_dot() -> None:
    instances = find_instances("混合写法 [1，p.3] 与 [2, p7] 都应识别。")
    assert instances[0].page == "3"
    assert instances[1].page == "7"


def test_multi_number_group_is_detected_and_counted() -> None:
    instances = find_instances("多项研究一致 [1,2]，另有三项打包 [3, 4, 5]。")
    assert instances[0].numbers == (1, 2)
    assert instances[0].group_size == 2
    assert instances[0].is_packed is False
    assert instances[1].numbers == (3, 4, 5)
    assert instances[1].group_size == PACKING_THRESHOLD
    assert instances[1].is_packed is True


def test_citation_context_strips_the_marker_itself() -> None:
    instances = find_instances("植被比例越高地表温度越低，但存在饱和阈值 [1]。")
    assert "[1]" not in instances[0].context
    assert len(instances[0].context) >= MIN_SUBSTANTIVE_CONTEXT
    assert instances[0].has_substantive_context is True


def test_thin_context_is_flagged() -> None:
    instances = find_instances("[1]")
    assert instances[0].has_substantive_context is False
    assert instances[0].context.strip() == ""


def test_paragraph_index_is_tracked() -> None:
    instances = find_instances("第一段的充分上下文陈述 [1]。\n\n第二段的充分上下文陈述 [2]。")
    assert [i.paragraph_index for i in instances] == [0, 1]


# --- 参考文献条目 ---


def test_finds_reference_entries_with_filenames() -> None:
    _, refs = split_references(good_survey())
    entries = find_references(refs)
    assert [e.number for e in entries] == [1, 2, 3, 4, 5]
    assert entries[0].filename == A
    assert entries[4].filename == E


def test_reference_without_a_pdf_filename_is_detected() -> None:
    entries = find_references("[1] 张三. 一篇没有文件名的文献.\n")
    assert entries[0].filename is None


def test_reference_with_bullet_prefix_is_parsed() -> None:
    entries = find_references(f"- [1] 张三. 标题. `{A}`\n")
    assert entries[0].number == 1
    assert entries[0].filename == A


# --- 合格样本 ---


def test_a_good_survey_has_no_issues() -> None:
    analysis = analyze_citations(good_survey(), ALLOWED)
    assert analysis.has_body_citations is True
    assert analysis.issues == ()
    assert issue_summary(analysis) == {}


def test_a_good_survey_covers_all_allowed_materials() -> None:
    analysis = analyze_citations(good_survey(), ALLOWED)
    assert analysis.cited_filenames == ALLOWED
    assert analysis.coverage_ratio() == 1.0
    assert analysis.dangling_numbers == frozenset()
    assert analysis.zombie_numbers == frozenset()


def test_each_paper_appears_in_at_least_two_substantive_paragraphs() -> None:
    analysis = analyze_citations(good_survey(), ALLOWED)
    assert analysis.unbalanced_numbers == frozenset()
    for number in (1, 2, 3, 4, 5):
        assert len(analysis.paragraphs_for(number)) >= 2


def test_no_single_paper_dominates() -> None:
    assert analyze_citations(good_survey(), ALLOWED).dominant_number is None


# --- 各类扣分情形 ---


def test_no_body_citations_is_detected() -> None:
    markdown = f"# 综述\n\n正文完全没有引用。\n\n## 参考文献\n\n[1] 张三. `{A}`\n"
    analysis = analyze_citations(markdown, ALLOWED)
    assert analysis.has_body_citations is False
    kinds = {i.kind for i in analysis.issues}
    assert "no_body_citations" in kinds
    assert "zombie_number" in kinds


def test_listing_in_references_alone_is_not_a_body_citation() -> None:
    """仅列在参考文献不等于正文引用。"""
    markdown = f"# 综述\n\n正文只引用了一篇 [1]。\n\n## 参考文献\n\n[1] a. `{A}`\n[2] b. `{B}`\n"
    analysis = analyze_citations(markdown, ALLOWED)
    assert analysis.cited_numbers == frozenset({1})
    assert analysis.zombie_numbers == frozenset({2})


def test_dangling_number_is_detected() -> None:
    markdown = f"# 综述\n\n正文引用了不存在的编号 [7]，上下文足够长以便通过实质检查。\n\n## 参考文献\n\n[1] a. `{A}`\n"
    analysis = analyze_citations(markdown, ALLOWED)
    assert analysis.dangling_numbers == frozenset({7})
    assert any(i.kind == "dangling_number" for i in analysis.issues)


def test_duplicate_reference_numbers_are_detected() -> None:
    markdown = (
        f"# 综述\n\n正文引用 [1] 且上下文足够长。\n\n## 参考文献\n\n[1] a. `{A}`\n[1] b. `{B}`\n"
    )
    analysis = analyze_citations(markdown, ALLOWED)
    assert analysis.duplicate_reference_numbers == frozenset({1})


def test_unknown_material_is_detected() -> None:
    markdown = "# 综述\n\n正文引用 [1] 且上下文足够长以通过检查。\n\n## 参考文献\n\n[1] a. `编造的不存在论文.pdf`\n"
    analysis = analyze_citations(markdown, ALLOWED)
    assert analysis.unknown_filenames == frozenset({"编造的不存在论文.pdf"})
    assert analysis.cited_filenames == frozenset()


def test_cited_filenames_only_counts_authorized_materials() -> None:
    markdown = (
        f"# 综述\n\n第一段引用授权材料 [1] 与未授权材料 [2]，上下文都足够长。\n\n"
        f"第二段再次引用 [1] 以满足跨段落要求。\n\n"
        f"## 参考文献\n\n[1] a. `{A}`\n[2] b. `未授权.pdf`\n"
    )
    analysis = analyze_citations(markdown, ALLOWED)
    assert analysis.cited_filenames == frozenset({A})
    assert analysis.coverage_ratio() == pytest.approx(1 / len(ALLOWED))


def test_packing_ratio_counts_groups_of_three_or_more() -> None:
    markdown = (
        "# 综述\n\n这里打包引用 [1, 2, 3]，另有正常的单篇引用 [1] 在同样的段落里。\n\n"
        f"## 参考文献\n\n[1] a. `{A}`\n[2] b. `{B}`\n[3] c. `{C}`\n"
    )
    analysis = analyze_citations(markdown, ALLOWED)
    assert analysis.packed_instance_count == 1
    assert analysis.packing_ratio == pytest.approx(0.5)
    assert any(i.kind == "packed_citations" for i in analysis.issues)


def test_unbalanced_distribution_is_detected() -> None:
    """只在一段出现过的被引编号不满足跨段落均衡。"""
    markdown = (
        f"# 综述\n\n第一段同时引用 [1] 与 [2]，上下文足够长以通过实质检查。\n\n"
        f"第二段只再次引用 [1]，因此另一篇的跨段落分布不均衡。\n\n"
        f"## 参考文献\n\n[1] a. `{A}`\n[2] b. `{B}`\n"
    )
    analysis = analyze_citations(markdown, ALLOWED)
    assert analysis.unbalanced_numbers == frozenset({2})


def test_dominant_source_is_detected() -> None:
    markdown = (
        f"# 综述\n\n第一段大量引用 [1]，上下文足够长 [1]，再来一次 [1]，"
        f"继续引用 [1]，最后一次 [1]。\n\n"
        f"第二段只引用一次 [2]，上下文也足够长以通过实质检查。\n\n"
        f"## 参考文献\n\n[1] a. `{A}`\n[2] b. `{B}`\n"
    )
    analysis = analyze_citations(markdown, ALLOWED)
    assert analysis.dominant_number == 1


def test_thin_context_instances_are_listed() -> None:
    markdown = f"# 综述\n\n[1]\n\n## 参考文献\n\n[1] a. `{A}`\n"
    analysis = analyze_citations(markdown, ALLOWED)
    assert len(analysis.context_free_instances) == 1


def test_coverage_ratio_is_zero_without_allowed_materials() -> None:
    analysis = analyze_citations("# 综述\n\n无引用。\n", frozenset())
    assert analysis.coverage_ratio() == 0.0


def test_page_qualifier_does_not_affect_number_parsing() -> None:
    markdown = f"# 综述\n\n带页码的引用 [1, p.3] 上下文足够长，另一次 [1, p.9] 也够长。\n\n第二段再引 [1, p.11] 以满足均衡。\n\n## 参考文献\n\n[1] a. `{A}`\n"
    analysis = analyze_citations(markdown, ALLOWED)
    assert analysis.cited_numbers == frozenset({1})
    assert [i.page for i in analysis.instances] == ["3", "9", "11"]


# --- 修复提示 ---


def test_repair_hint_is_empty_for_a_clean_survey() -> None:
    analysis = analyze_citations(good_survey(), ALLOWED)
    assert build_repair_hint(analysis, min_distinct_papers=5).lines == ()


def test_repair_hint_names_each_problem() -> None:
    markdown = (
        "# 综述\n\n正文引用了 [9]，上下文足够长以通过实质检查。\n\n"
        f"## 参考文献\n\n[1] a. `{A}`\n[2] b. `编造.pdf`\n"
    )
    analysis = analyze_citations(markdown, ALLOWED)
    hint = build_repair_hint(analysis, min_distinct_papers=5).as_prompt_block()
    assert "[9]" in hint
    assert "编造.pdf" in hint
    assert "5" in hint
    assert "不得编造" in hint


def test_repair_hint_never_demands_fabricating_a_fifth_paper() -> None:
    """材料只有 2 篇时，min_distinct_papers 必须降到 2，不得逼模型编第 5 篇。"""
    markdown = (
        f"# 综述\n\n第一段引用编号 [1]，这一段的上下文足够长以通过实质检查。\n\n"
        f"第二段引用编号 [2]，这一段的上下文同样足够长以通过检查。\n\n"
        f"第三段再次引用 [1]，用来说明它在另一处的真实贡献也足够具体。\n\n"
        f"第四段再次引用 [2]，用来说明它在另一处的真实贡献同样具体。\n\n"
        f"## 参考文献\n\n[1] a. `{A}`\n[2] b. `{B}`\n"
    )
    analysis = analyze_citations(markdown, frozenset({A, B}))
    hint = build_repair_hint(analysis, min_distinct_papers=2).as_prompt_block()
    assert "至少需要 2 篇" not in hint
    assert hint == ""


def test_issue_summary_counts_by_kind() -> None:
    markdown = "# 综述\n\n引用 [9] 与 [8]，上下文都足够长。\n\n## 参考文献\n\n[1] a. `x.pdf`\n"
    summary = issue_summary(analyze_citations(markdown, ALLOWED))
    assert summary["dangling_number"] == 2
    assert summary["zombie_number"] == 1
    assert summary["unknown_material"] == 1
