"""Source-specific claim evidence and citation-shaped numeric notation."""

import json

import pytest

from research_assistant.documents.evidence import build_evidence_pack
from research_assistant.validation.citations import (
    analyze_citations,
    build_repair_hint,
    issue_summary,
)
from research_assistant.validation.report_repair import decide_repair
from tests.unit.test_report_grounding import doc

SOURCE = "The cooling effect saturated beyond a vegetation fraction of about forty percent."
CLAIM = "直接采用论文的NDVI阈值40%作为基线 [1]。"
MARKDOWN = CLAIM + "\n\n## 参考文献\n[1] `paper.pdf`"


def pack():
    return build_evidence_pack([doc("paper.pdf", SOURCE)])


def finding(**overrides):
    item = {
        "claim": CLAIM,
        "reference": 1,
        "source_quote": SOURCE,
        "explanation": "原文阈值对应vegetation fraction，不能直接说成NDVI阈值。",
    }
    return {**item, **overrides}


@pytest.mark.parametrize(
    "context", ["NDVI∈[0,1]分箱。", "在统一[0,1]空间内比较。", "归一化到[0,1]区间。"]
)
def test_numeric_interval_is_not_a_source_citation_but_requests_unambiguous_format(context):
    analysis = analyze_citations(
        context + "论文支持该比较方法 [2]。\n\n## 参考文献\n[2] paper.pdf", {"paper.pdf"}
    )
    assert analysis.cited_numbers == {2}
    assert not analysis.dangling_numbers
    assert issue_summary(analysis)["ambiguous_numeric_brackets"] == 1
    assert "区间" in build_repair_hint(analysis, min_distinct_papers=1).as_prompt_block()


def test_standalone_zero_citation_is_still_invalid():
    assert analyze_citations("文献[0]说明这一问题。", {"paper.pdf"}).dangling_numbers == {0}


def test_normal_multiple_source_citation_is_preserved():
    assert analyze_citations("这两篇论文的结论相近[1,2]。", set()).cited_numbers == {1, 2}


def test_audit_validates_actual_claim_cited_source_and_exact_quote():
    from research_assistant.validation.source_audit import parse_audit

    result = parse_audit(json.dumps({"issues": [finding()]}), MARKDOWN, pack())
    assert result.complete
    assert len(result.findings) == 1
    assert result.findings[0].source_quote == SOURCE


@pytest.mark.parametrize(
    "change",
    [
        {"claim": "报告里没有这句话 [1]。"},
        {"reference": 2},
        {"source_quote": "The paper released pretrained weights to the public."},
        {"source_quote": "the"},
        {"claim": "仅保留正文的局部短语，不含引用"},
        {"reference": True},
    ],
)
def test_fabricated_or_unbound_audit_findings_are_not_clean_results(change):
    from research_assistant.validation.source_audit import parse_audit

    result = parse_audit(json.dumps({"issues": [finding(**change)]}), MARKDOWN, pack())
    assert not result.complete
    assert not result.findings


@pytest.mark.parametrize("raw", ['{"issues": []}', '```json\n{"issues": []}\n```'])
def test_explicit_empty_audit_is_distinct_from_unavailable(raw):
    from research_assistant.validation.source_audit import parse_audit

    result = parse_audit(raw, MARKDOWN, pack())
    assert result.complete and not result.findings


@pytest.mark.parametrize("raw", ["{}", "null", '{"issues": null}', "not json"])
def test_invalid_audit_payload_is_not_clean(raw):
    from research_assistant.validation.source_audit import parse_audit

    assert not parse_audit(raw, MARKDOWN, pack()).complete


def test_semantic_improvement_can_trigger_repair_without_format_changes():
    analysis = analyze_citations(MARKDOWN, {"paper.pdf"})
    assert decide_repair(analysis, analysis, semantic_before=1, semantic_after=0).accepted


def test_citation_improvements_cannot_trade_in_new_semantic_defects():
    before = analyze_citations(MARKDOWN + "\n[2] paper.pdf", {"paper.pdf"})
    after = analyze_citations(MARKDOWN, {"paper.pdf"})
    result = decide_repair(before, after, semantic_before=0, semantic_after=1)
    assert not result.accepted and result.reason == "source_grounding_regressed"


@pytest.mark.parametrize("kind", ["survey", "idea"])
def test_clean_format_but_wrong_source_variable_is_audited_and_repaired(
    kind, base_env, papers_root, tmp_workspace
):
    from dataclasses import replace

    from research_assistant.handlers.idea import solve_idea
    from research_assistant.handlers.survey import solve_survey
    from tests.fixtures.papers import write_paper
    from tests.fixtures.report_gateway import ReportGateway
    from tests.unit.test_report_handlers import (
        P1,
        P2,
        P3,
        P4,
        P5,
        context_for,
        good_idea_markdown,
        good_survey_markdown,
        idea_task,
        survey_task,
    )

    names = [P1, P2, P3, P4, P5]
    for name in names:
        write_paper(papers_root, name, body_paragraphs=[SOURCE])
    good = good_idea_markdown() if kind == "idea" else good_survey_markdown()
    draft = good.replace("## 参考文献", CLAIM + "\n\n## 参考文献")
    task = idea_task(names) if kind == "idea" else survey_task(names)
    gateway = ReportGateway(
        [draft, good], audit_responses=[json.dumps({"issues": [finding()]}), '{"issues":[]}']
    )
    context = replace(context_for(base_env, task, [], papers_root, tmp_workspace), gateway=gateway)
    result = (solve_idea if kind == "idea" else solve_survey)(task, context)
    assert result["markdown"] == good.strip()
    assert [stage for stage, _ in gateway.calls] == [
        f"{kind}.generate",
        f"{kind}.source_audit",
        f"{kind}.repair",
        f"{kind}.source_audit_candidate",
    ]
    assert all(0 < remaining <= 30 for remaining in gateway.audit_remaining)


@pytest.mark.parametrize("candidate_audit", ["invalid json", '{"issues": null}'])
def test_unavailable_candidate_audit_cannot_replace_the_audited_draft(
    candidate_audit, base_env, papers_root, tmp_workspace
):
    from dataclasses import replace

    from research_assistant.handlers.idea import solve_idea
    from tests.fixtures.papers import write_paper
    from tests.fixtures.report_gateway import ReportGateway
    from tests.unit.test_report_handlers import P1, P2, context_for, good_idea_markdown, idea_task

    for name in (P1, P2):
        write_paper(papers_root, name, body_paragraphs=[SOURCE])
    good = good_idea_markdown()
    draft = good.replace("## 参考文献", CLAIM + "\n\n## 参考文献")
    task = idea_task([P1, P2])
    gateway = ReportGateway(
        [draft, good], audit_responses=[json.dumps({"issues": [finding()]}), candidate_audit]
    )
    context = replace(context_for(base_env, task, [], papers_root, tmp_workspace), gateway=gateway)
    result = solve_idea(task, context)
    assert result["markdown"] == draft.strip()
    assert len(gateway.audit_calls) == 2


def test_insufficient_budget_skips_audit_without_model_call(base_env, papers_root, tmp_workspace):
    from dataclasses import replace

    from research_assistant.runtime.deadline import Deadline
    from research_assistant.validation.source_audit import audit_report
    from tests.unit.test_report_handlers import context_for, idea_task

    context = context_for(base_env, idea_task([]), [], papers_root, tmp_workspace)
    context = replace(context, deadline=Deadline.start(40))
    result = audit_report(MARKDOWN, pack(), context, stage="idea.source_audit")
    assert not result.complete and result.status == "skipped_budget"
    assert not context.gateway.calls
