"""Report contract and source-specific numeric diagnostics, using invented papers."""

from pathlib import Path

import pytest

from research_assistant.contracts.results import idea_result, survey_result
from research_assistant.documents.model import Document, DocumentMetadata, PageText, ParseStatus
from research_assistant.errors import GatewayError, TaskContractError
from tests.unit.test_report_handlers import five_papers as five_papers


def doc(name, text):
    return Document(
        name,
        Path("/unused") / name,
        name,
        ParseStatus.OK,
        None,
        "test",
        metadata=DocumentMetadata(title=name, year=2023),
        pages=(PageText(0, text),),
    )


def diagnose(sentence, sources):
    from research_assistant.validation.grounding import analyze_report_evidence

    markdown = (
        sentence
        + "\n\n## 参考文献\n"
        + "\n".join(f"[{i}] {d.filename}" for i, d in enumerate(sources, 1))
    )
    result = analyze_report_evidence(markdown, sources, frozenset(d.filename for d in sources))
    return [issue for issue in result.issues if issue.kind == "unverified_numeric_attribution"]


@pytest.mark.parametrize("builder", [survey_result, idea_result])
def test_reference_list_cannot_fill_the_800_character_body_requirement(builder):
    markdown = "# 短正文\n内容不足。\n\n## 参考文献\n" + "[1] Long_reference.pdf\n" * 80
    with pytest.raises(TaskContractError, match="800"):
        builder("C-01", path="data/expert/report.md", markdown=markdown)


@pytest.mark.parametrize("kind", ["survey", "idea"])
def test_model_parse_rejects_short_body_with_long_references(kind):
    from research_assistant.handlers import idea, survey

    parser = idea._parse_markdown if kind == "idea" else survey._parse_markdown
    with pytest.raises(GatewayError, match="800"):
        parser("# 正文\n很短。\n\n## 参考文献\n" + "参考条目\n" * 250)


def test_reference_heading_inside_a_code_fence_does_not_end_the_body():
    from research_assistant.contracts.report_text import report_body_characters

    text = (
        "# 正文\n\n```text\n## 参考文献\n```\n\n"
        + "实质正文。" * 180
        + "\n\n## 参考文献\n[1] a.pdf"
    )
    assert report_body_characters(text) >= 800


def test_exactly_800_body_characters_with_references_are_legal():
    markdown = "文" * 800 + "\n\n## 参考文献\n[1] a.pdf"
    assert idea_result("E-01", path="data/expert/a.md", markdown=markdown)["markdown"] == markdown


def test_numbers_from_a_different_paper_cannot_support_a_claim():
    sources = [
        doc("cooling.pdf", "The cooling effect saturated beyond forty percent vegetation."),
        doc(
            "segmentation.pdf", "Transformer backbones improved boundary recall over CNN baselines."
        ),
    ]
    issues = diagnose("分割论文报告Transformer边界精度优于CNN基线40% [2]。", sources)
    assert issues and "segmentation.pdf" in issues[0].detail and "40" in issues[0].detail


@pytest.mark.parametrize("value", ["0.70", "70%"])
def test_unsupported_reported_baseline_value_is_diagnosed(value):
    assert diagnose(
        f"论文报告准确率基准为{value} [1]。",
        [doc("a.pdf", "Accuracy improved over the baseline, but no score was reported.")],
    )


@pytest.mark.parametrize(
    "source,value",
    [
        ("The cooling effect saturated beyond forty percent coverage.", "40%"),
        ("Accuracy was 0.70 on the test set.", "70%"),
        ("准确率为百分之七十。", "0.70"),
        ("The accuracy reached 70 percent.", "0.70"),
    ],
)
def test_common_equivalent_numeric_forms_do_not_trigger_false_diagnostics(source, value):
    assert not diagnose(f"论文报告准确率达到{value} [1]。", [doc("a.pdf", source)])


@pytest.mark.parametrize(
    "sentence",
    [
        "本方案计划在5个城市验证，并预设RMSE下降15%为成功阈值 [1]。",
        "题目模拟观察显示40%路段无遮荫，尚未测试替代策略 [1]。",
        "我们提出待验证目标：相对文献基线提高15%，不是已完成结果 [1]。",
    ],
)
def test_proposed_targets_and_task_observations_are_not_paper_result_claims(sentence):
    assert not diagnose(sentence, [doc("a.pdf", "This paper studies urban heat exposure.")])


@pytest.mark.parametrize("kind", ["survey", "idea"])
def test_repair_cannot_trade_better_citations_for_unsupported_paper_numbers(
    kind, base_env, five_papers, papers_root, tmp_workspace
):
    import re

    from research_assistant.handlers.idea import solve_idea
    from research_assistant.handlers.survey import solve_survey
    from tests.unit.test_report_handlers import (
        context_for,
        good_idea_markdown,
        good_survey_markdown,
        idea_task,
        survey_task,
    )

    good = good_idea_markdown() if kind == "idea" else good_survey_markdown()
    head, refs = good.split("## 参考文献", 1)
    draft = re.sub(r"\[\d+\]", "", head) + "## 参考文献" + refs
    candidate = head + "\n\n论文报告准确率达到99.37% [1]。\n\n## 参考文献" + refs
    task = idea_task(five_papers) if kind == "idea" else survey_task(five_papers)
    context = context_for(base_env, task, [draft, candidate], papers_root, tmp_workspace)
    result = (solve_idea if kind == "idea" else solve_survey)(task, context)
    assert result["markdown"] == draft.strip()


def test_proposal_list_index_and_threshold_are_not_attributed_to_parenthetical_paper_result():
    sentence = "5. 使用[1]的方法（论文报告改善40%）辅助提取裸土（NDVI<0.1），其余均为计划设计。"
    assert not diagnose(sentence, [doc("a.pdf", "The reported improvement was forty percent.")])


@pytest.mark.parametrize(
    "source,value", [("准确率为百分之70.5。", "70.5%"), ("Accuracy was 70.5%.", "百分之70.5")]
)
def test_arabic_decimal_chinese_percentages_are_preserved(source, value):
    assert not diagnose(f"论文报告准确率达到{value} [1]。", [doc("a.pdf", source)])


def test_distinct_source_comparisons_do_not_cross_contaminate_numeric_attributions():
    sources = [doc("a.pdf", "The accuracy was 70%."), doc("b.pdf", "The accuracy was 80%.")]
    assert not diagnose("论文A报告准确率70%[1]而论文B报告准确率80%[2]。", sources)


@pytest.mark.parametrize("kind", ["survey", "idea"])
def test_fixing_numeric_gaps_cannot_introduce_a_new_citation_defect(
    kind, base_env, five_papers, papers_root, tmp_workspace
):
    from research_assistant.handlers.idea import solve_idea
    from research_assistant.handlers.survey import solve_survey
    from tests.unit.test_report_handlers import (
        context_for,
        good_idea_markdown,
        good_survey_markdown,
        idea_task,
        survey_task,
    )

    good = good_idea_markdown() if kind == "idea" else good_survey_markdown()
    head, refs = good.split("## 参考文献", 1)
    draft = (
        head
        + "\n\n论文报告准确率为99.37% [1]。\n\n论文报告召回率为88.26% [1]。\n\n## 参考文献"
        + refs
    )
    candidate = good + "\n[6] 额外参考. " + five_papers[0]
    task = idea_task(five_papers) if kind == "idea" else survey_task(five_papers)
    context = context_for(base_env, task, [draft, candidate], papers_root, tmp_workspace)
    result = (solve_idea if kind == "idea" else solve_survey)(task, context)
    assert result["markdown"] == draft.strip()
