"""Conservative bibliography completion and explainable repair decisions."""

from __future__ import annotations

from dataclasses import replace

import pytest

from research_assistant.documents.evidence import EvidenceEntry, EvidencePack
from research_assistant.validation.citations import CitationIssue, analyze_citations
from tests.unit.test_report_handlers import five_papers as five_papers


def pack():
    return EvidencePack(
        tuple(
            EvidenceEntry(f"paper_{i}.pdf", f"Title {i}", 2024, (), (), None, ())
            for i in range(1, 5)
        ),
        (),
    )


def complete(markdown):
    from research_assistant.validation.report_repair import complete_missing_references

    return complete_missing_references(markdown, pack())


def test_completes_only_missing_cited_reference_and_preserves_body():
    body = "论文一说明城市绿地与温度的研究 [1]。\n\n第四篇说明具体适用范围 [4]。\n\n"
    text = body + "## 参考文献\n[1] paper_1.pdf\n"
    result = complete(text)
    assert result.added_numbers == (4,)
    assert result.markdown.split("## 参考文献")[0] == body
    assert "[4] paper_4.pdf" in result.markdown
    assert "paper_2.pdf" not in result.markdown
    assert complete(result.markdown).markdown == result.markdown


@pytest.mark.parametrize(
    "text",
    [
        "正文 [1] [4]。",  # no anchored reference table
        "正文 [1] [4]。\n\n## 参考文献\n[1] paper_2.pdf",  # renumbered meaning
        "正文 [1] [4]。\n\n## 参考文献\n[1] unknown.pdf",
        "正文 [1] [4]。\n\n## 参考文献\n[1] paper_1.pdf\n[1] paper_1.pdf",
        "正文 [1] [99]。\n\n## 参考文献\n[1] paper_1.pdf",
    ],
)
def test_ambiguous_numbering_is_left_untouched(text):
    assert complete(text).markdown == text
    assert complete(text).added_numbers == ()


def analysis(kinds=(), cited=(1, 2)):
    allowed = frozenset({"paper_1.pdf", "paper_2.pdf", "paper_3.pdf"})
    markdown = "\n\n".join(f"有实质内容的研究证据对应论文 [{i}]。" for i in cited)
    markdown += "\n\n## 参考文献\n" + "\n".join(f"[{i}] paper_{i}.pdf" for i in cited)
    return replace(
        analyze_citations(markdown, allowed), issues=tuple(CitationIssue(k, k) for k in kinds)
    )


def decide(before, after, **kwargs):
    from research_assistant.validation.report_repair import decide_repair

    return decide_repair(before, after, **kwargs)


def test_same_issue_count_can_improve_citation_legality():
    assert decide(analysis(("dangling_number",)), analysis(("unbalanced_distribution",))).accepted


def test_fixing_numbers_cannot_introduce_illegal_citations():
    before = analysis(("unverified_numeric_attribution",) * 2)
    assert not decide(before, analysis(("zombie_number",))).accepted


def test_improving_style_cannot_worsen_content_or_numeric_grounding():
    before = analysis(("unbalanced_distribution",))
    assert not decide(before, analysis(), content_before=0, content_after=1).accepted
    assert not decide(before, analysis(("unverified_numeric_attribution",))).accepted


def test_required_source_deficit_cannot_increase():
    assert not decide(
        analysis(("dangling_number",), cited=(1, 2)), analysis(cited=(1,)), min_sources=2
    ).accepted


def test_unchanged_analysis_is_not_an_improvement():
    assert not decide(analysis(), analysis()).accepted


@pytest.mark.parametrize("kind", ["survey", "idea"])
def test_handler_completes_canonical_missing_reference_without_an_extra_model_call(
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
    # Explicit filename-only anchors make completion unambiguous.
    head, _refs = good.split("## 参考文献", 1)
    references = analyze_citations(good, frozenset(five_papers)).references
    good = head + "## 参考文献\n" + "\n".join(f"[{r.number}] {r.filename}" for r in references)
    missing = 2 if kind == "idea" else 5
    draft = "\n".join(line for line in good.splitlines() if not line.startswith(f"[{missing}] "))
    task = idea_task(five_papers) if kind == "idea" else survey_task(five_papers)
    context = context_for(base_env, task, [draft], papers_root, tmp_workspace)
    answer = (solve_idea if kind == "idea" else solve_survey)(task, context)
    a = analyze_citations(answer["markdown"], frozenset(five_papers))
    assert a.dangling_numbers == frozenset()
    assert len(context.gateway.content_calls) == 1


@pytest.mark.parametrize(
    "anchor",
    [
        "unknown_paper_1.pdf",
        "unknown paper_1.pdf",
        "paper_1.pdf and unrelated.pdf",
        "Author Title paper_1.pdf",
    ],
)
def test_substring_or_unstructured_reference_is_not_a_trusted_anchor(anchor):
    text = "正文 [1] [4]。\n\n## 参考文献\n[1] " + anchor
    assert complete(text).markdown == text
    assert complete(text).added_numbers == ()


@pytest.mark.parametrize("fence", ["```markdown", "~~~markdown"])
def test_code_fenced_reference_examples_cannot_authorize_mutation(fence):
    closing = fence[:3]
    text = "正文[1]和[4]。\n\n" + fence + "\n## 参考文献\n[1] paper_1.pdf\n" + closing
    assert complete(text).markdown == text


def test_explicitly_delimited_metadata_filename_is_a_unique_anchor():
    text = "正文[1]和[4]。\n\n## 参考文献\n[1] Author. Title. `paper_1.pdf`"
    assert complete(text).added_numbers == (4,)
