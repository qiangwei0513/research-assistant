"""Complete source identity, independent of allowed-set iteration order."""

import pytest

from research_assistant.validation.citations import analyze_citations, build_repair_hint
from research_assistant.validation.grounding import analyze_report_evidence
from tests.unit.test_report_grounding import doc


def report(entry):
    return "论文报告准确率达到70% [1]。\n\n## 参考文献\n[1] " + entry


@pytest.mark.parametrize(
    "entry",
    [
        "unknown_paper.pdf",
        "unknown paper.pdf",
        "`unknown paper.pdf`",
        "PDF: unknown paper.pdf",
        "paper.pdf.bak",
        "`paper.pdf.bak`",
        "paper.pdf / other.pdf",
        "`paper.pdf` and `other.pdf`",
        "Author. Title. paper.pdf",
    ],
)
def test_unknown_or_ambiguous_entries_cannot_borrow_authorized_identity(entry):
    analysis = analyze_citations(report(entry), {"paper.pdf", "other.pdf"})
    assert not analysis.cited_filenames
    assert any(i.kind in {"missing_filename", "unknown_material"} for i in analysis.issues)
    assert build_repair_hint(analysis, min_distinct_papers=1).lines


@pytest.mark.parametrize(
    "entry,expected",
    [
        ("paper.pdf", "paper.pdf"),
        ("unknown_paper.pdf", "unknown_paper.pdf"),
        ("A Study + 中文.pdf", "A Study + 中文.pdf"),
        ("Author. Title. `A Study + 中文.pdf`", "A Study + 中文.pdf"),
        ('Author. "A Study + 中文.pdf" (2024)', "A Study + 中文.pdf"),
        ("Title. PDF: A Study + 中文.pdf", "A Study + 中文.pdf"),
        ("作者. 文件名：A Study + 中文.pdf。", "A Study + 中文.pdf"),
    ],
)
def test_unique_explicit_locators_preserve_complete_authorized_names(entry, expected):
    analysis = analyze_citations(
        report(entry), {"paper.pdf", "unknown_paper.pdf", "A Study + 中文.pdf", "中文.pdf"}
    )
    assert analysis.cited_filenames == {expected}
    assert analysis.references[0].filename == expected


def test_quoted_unknown_filename_is_reported_in_full():
    analysis = analyze_citations(report("Author. `unknown paper.pdf`"), {"paper.pdf"})
    assert analysis.unknown_filenames == {"unknown paper.pdf"}


def test_unknown_reference_does_not_use_authorized_paper_for_numeric_grounding():
    source = doc("paper.pdf", "Accuracy was 50%.")
    analysis = analyze_report_evidence(report("unknown_paper.pdf"), [source], {"paper.pdf"})
    # Diagnosis belongs to source identity, not a numeric comparison against the wrong PDF.
    assert any(i.kind == "unknown_material" for i in analysis.issues)
    assert not any(i.kind == "unverified_numeric_attribution" for i in analysis.issues)


def test_allowed_filename_iterable_is_materialized_once():
    analysis = analyze_citations(report("paper.pdf"), iter(["paper.pdf"]))
    assert analysis.cited_filenames == {"paper.pdf"}
    assert not analysis.unknown_filenames


def test_pdf_filename_label_used_in_saved_idea_report_is_explicit():
    analysis = analyze_citations(report("Author. Title. 2024. PDF文件名: paper.pdf"), {"paper.pdf"})
    assert analysis.cited_filenames == {"paper.pdf"}


@pytest.mark.parametrize(
    "entry",
    [
        "`paper.pdf`.bak",
        "unknown_`paper.pdf`",
        '"unknown_`paper.pdf`"',
        "'paper.pdf'.bak",
        "`paper.pdf`后缀",
        "`paper.pdf`/other",
    ],
)
def test_quoted_locator_requires_complete_outer_boundaries(entry):
    assert not analyze_citations(report(entry), {"paper.pdf"}).cited_filenames


@pytest.mark.parametrize("name", ["A.pdf version.pdf", "paper.pdf.notes.pdf"])
@pytest.mark.parametrize("template", ["{}", "Author. `{}`", "Title. PDF: {}"])
def test_pdf_substrings_inside_one_complete_name_do_not_create_extra_locators(name, template):
    analysis = analyze_citations(report(template.format(name)), {name, "version.pdf", "notes.pdf"})
    assert analysis.cited_filenames == {name}


def test_invalid_quoted_suffix_cannot_authorize_automatic_completion():
    from research_assistant.validation.report_repair import complete_missing_references
    from tests.unit.test_reference_repair import pack

    text = "正文[1]和[4]。\n\n## 参考文献\n[1] `paper_1.pdf`.bak"
    result = complete_missing_references(text, pack())
    assert result.markdown == text
    assert not result.added_numbers


@pytest.mark.parametrize("entry", ["other.pdf PDF: paper.pdf", "other.pdf 文件名：paper.pdf"])
def test_explicit_label_cannot_hide_an_additional_filename(entry):
    assert not analyze_citations(report(entry), {"paper.pdf", "other.pdf"}).cited_filenames
