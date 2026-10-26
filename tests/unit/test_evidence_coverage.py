"""Evidence recall and provenance with invented multi-page source documents."""

from pathlib import Path

import pytest

from research_assistant.documents.evidence import build_evidence_pack, render_evidence_pack
from research_assistant.documents.model import Document, DocumentMetadata, PageText, ParseStatus


def document(pages, abstract="Short abstract."):
    return Document(
        "study.pdf",
        Path("/unused/study.pdf"),
        "synthetic",
        ParseStatus.OK,
        None,
        "test",
        metadata=DocumentMetadata(title="Study", abstract=abstract),
        pages=tuple(PageText(i, text, str(10 + i)) for i, text in enumerate(pages)),
    )


def content_size(entry):
    return len(entry.abstract or "") + sum(len(s.text) for s in entry.snippets)


def test_long_abstract_is_bounded_and_cannot_suppress_source_body():
    source = document(
        [
            "Methods used satellite thermal observations. " * 100,
            "Results showed cooling saturation beyond forty percent coverage.",
        ],
        abstract="Background knowledge. " * 250,
    )
    entry = build_evidence_pack([source], max_chars_per_document=2200).entries[0]
    assert content_size(entry) <= 2200
    assert entry.snippets
    assert any("saturation" in s.text for s in entry.snippets)
    assert len(source.metadata.abstract) > 2200  # original source remains intact


@pytest.mark.parametrize("language", ["english", "chinese"])
def test_late_results_and_counterevidence_survive_long_introduction(language):
    if language == "english":
        result = "Results\nCooling saturated beyond forty percent coverage."
        limitation = "Limitations\nThe effect did not persist under water stress."
        method = "Methods\nThermal observations were matched by acquisition time."
    else:
        result = "结果\n覆盖率达到百分之四十后降温效应饱和。"
        limitation = "局限\n水分胁迫条件下该效应未能持续。"
        method = "方法\n按照采集时刻匹配热红外观测。"
    source = document(
        ["Introduction and background. " * 300, method, "Related work. " * 300, result, limitation]
    )
    entry = build_evidence_pack([source], max_chars_per_document=2200).entries[0]
    text = "\n".join(s.text for s in entry.snippets)
    assert result in text
    assert limitation in text
    assert method in text
    assert content_size(entry) <= 2200
    for snippet in entry.snippets:
        page = source.pages[snippet.page_index]
        assert snippet.text in page.text
        assert snippet.page_label == page.label


def test_unmarked_pages_do_not_reduce_to_the_front_page():
    source = document([f"Page {i} discusses a distinct observation. " * 100 for i in range(9)])
    entry = build_evidence_pack([source], max_chars_per_document=2200).entries[0]
    indexes = {s.page_index for s in entry.snippets}
    assert 0 in indexes and 8 in indexes
    assert any(2 <= i <= 6 for i in indexes)


def test_short_paper_keeps_all_original_pages():
    source = document(["Background information.", "Methods and results.", "Limitations."])
    entry = build_evidence_pack([source], max_chars_per_document=2200).entries[0]
    assert [s.text for s in entry.snippets] == [p.text for p in source.pages]
    assert entry.abstract == source.metadata.abstract


def test_truncated_evidence_is_explicitly_marked_without_claiming_absence():
    pack = build_evidence_pack(
        [document(["Unmarked evidence. " * 500])], max_chars_per_document=800
    )
    assert "节选" in render_evidence_pack(pack)
    assert "不等于原文没有" in render_evidence_pack(pack)


@pytest.mark.parametrize("budget", [0, 1, 10, 800, 1100, 2200, 6000])
def test_requested_content_budget_is_a_real_bound(budget):
    source = document(["Methods and findings. " * 500], abstract="Long abstract. " * 600)
    entry = build_evidence_pack([source], max_chars_per_document=budget).entries[0]
    assert content_size(entry) <= budget


def test_negative_content_budget_is_rejected():
    with pytest.raises(ValueError):
        build_evidence_pack([document(["Source text."])], max_chars_per_document=-1)


def test_complete_content_that_fits_is_not_unnecessarily_excerpted():
    source = document(["A short complete source body."], abstract="Abstract evidence. " * 80)
    assert len(source.metadata.abstract) + len(source.full_text) < 2200
    entry = build_evidence_pack([source], max_chars_per_document=2200).entries[0]
    assert entry.abstract == source.metadata.abstract.strip()
    assert not entry.excerpted


def test_front_matter_headings_do_not_hide_later_actual_results():
    source = document(
        [
            "Results\n" + "Earlier studies describe background variation across regions. " * 35,
            "Discussion\n"
            + "Earlier work provides general context for subsequent evaluation. " * 35,
            "Methods\n" + "The overview introduces how the paper is organized and read. " * 35,
            "Results\nCooling saturated beyond forty percent coverage.",
            "Limitations\nThe effect did not persist under water stress.",
        ]
    )
    entry = build_evidence_pack([source], max_chars_per_document=2200).entries[0]
    text = "\n".join(s.text for s in entry.snippets)
    assert "saturated beyond forty percent" in text
    assert "did not persist under water stress" in text
    assert any(s.page_index < 3 for s in entry.snippets)


def test_many_tiny_pages_have_bounded_rendering_overhead_and_diverse_positions():
    source = document([str(i) for i in range(1000)])
    entry = build_evidence_pack([source], max_chars_per_document=2200).entries[0]
    assert len(entry.snippets) <= 16
    indexes = {s.page_index for s in entry.snippets}
    assert 0 in indexes and 999 in indexes
    assert any(300 <= i <= 700 for i in indexes)


@pytest.mark.parametrize("pages", [1, 2, 4])
def test_pdf_fixture_never_silently_drops_supplied_paragraphs(tmp_path, pages):
    from research_assistant.documents.extractor import extract_one
    from tests.fixtures.papers import write_paper

    paragraphs = ["Background evidence.", "Observed cooling.", "Saturation beyond forty percent."]
    path = write_paper(tmp_path, "complete.pdf", body_paragraphs=paragraphs, pages=pages)
    source = extract_one(path, filename=path.name)
    assert len(source.pages) == pages
    for text in paragraphs:
        assert text in source.full_text
