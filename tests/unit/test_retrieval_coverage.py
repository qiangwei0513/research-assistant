"""Coverage tests use invented documents and labels, never official hidden answers."""

from pathlib import Path

import pytest

from research_assistant.contracts.task import Task
from research_assistant.documents.model import Document, DocumentMetadata, PageText, ParseStatus
from research_assistant.documents.retrieval import LexicalIndex
from research_assistant.handlers.retrieval import select_candidates


def task(query="检索城市绿地降温研究，排除纯图像分割论文"):
    return Task.parse(
        {
            "case_id": "B-01",
            "function": "Literature_Retrieval",
            "title": "文献检索",
            "input": query,
            "materials": [],
            "constraints": [],
            "expected_output": "相关论文及理由",
            "output_schema_ref": "Literature_Retrieval",
        }
    )


def documents(count=40):
    return tuple(
        Document(
            filename=f"paper_{i:03}.pdf",
            path=Path(f"/unused/paper_{i:03}.pdf"),
            content_sha256=str(i),
            status=ParseStatus.OK,
            status_reason=None,
            extractor="test",
            metadata=DocumentMetadata(title=f"Study {i}", abstract="urban heat vegetation " * 150),
            pages=(PageText(index=0, text="urban heat vegetation " * 150),),
        )
        for i in range(count)
    )


@pytest.mark.parametrize("query", ["城市绿地降温效应", "urban heat vegetation"])
def test_every_usable_document_reaches_semantic_screening(query):
    docs = documents()
    candidates, _ = select_candidates(task(query), LexicalIndex(docs), docs)
    assert {c.filename for c in candidates} == {d.filename for d in docs}


def test_zero_lexical_match_still_reaches_semantic_screening():
    from dataclasses import replace

    docs = list(documents(30))
    docs[-1] = replace(
        docs[-1],
        metadata=DocumentMetadata(title="Cooling effect of trees"),
        pages=(PageText(0, "Trees reduce daytime land surface temperature."),),
    )
    candidates, _ = select_candidates(task("urban heat vegetation"), LexicalIndex(docs), docs)
    assert docs[-1].filename in {c.filename for c in candidates}


def test_actual_serialized_batch_size_is_bounded_without_lost_documents():
    import json

    from research_assistant.documents.retrieval_batches import batch_summaries

    docs = documents()
    candidates, _ = select_candidates(task(), LexicalIndex(docs), docs)
    batches = batch_summaries(candidates)
    assert {row["filename"] for batch in batches for row in batch} == {d.filename for d in docs}
    assert all(len(json.dumps(batch, ensure_ascii=False, indent=1)) <= 24000 for batch in batches)
    assert all(len(batch) <= 12 for batch in batches)


def batch_rows(messages):
    import json

    content = messages[-1].content
    text = content.split("# 候选材料", 1)[1].split("\n", 1)[1].split("\n\n# 输出要求", 1)[0]
    return json.loads(text)


def run_screen(monkeypatch, base_env, tmp_workspace, papers_root, docs, decide):
    from research_assistant.documents.model import ExtractionReport
    from research_assistant.handlers import retrieval
    from research_assistant.runtime.context import load_task_context
    from research_assistant.runtime.gateway import FakeGateway
    from research_assistant.runtime.settings import Settings

    class CallbackGateway(FakeGateway):
        budgets = None

        def complete(self, messages, *, context, stage, max_tokens=None):
            if self.budgets is None:
                self.budgets = []
            self.budgets.append(context.deadline.remaining())
            self._responses.append(decide(messages, context, stage))
            return super().complete(messages, context=context, stage=stage, max_tokens=max_tokens)

    gateway = CallbackGateway([])
    env = {
        **base_env,
        "RESEARCH_WORKSPACE_ROOT": str(tmp_workspace),
        "RESEARCH_PAPERS_ROOT": str(papers_root),
    }
    context = load_task_context(task(), Settings.from_env(env), gateway=gateway)
    monkeypatch.setattr(
        retrieval, "extract_documents", lambda *a, **kw: ExtractionReport(tuple(docs))
    )
    return retrieval.solve_retrieval(task(), context), gateway


def selected(names):
    import json

    return json.dumps(
        {
            "papers": [
                {"filename": name, "reason": "原文支持城市绿地对地表温度的影响。"} for name in names
            ]
        }
    )


def test_handler_screens_tail_and_allocates_time_to_later_batches(
    monkeypatch, base_env, tmp_workspace, papers_root
):
    docs = documents()
    seen = []

    def decide(messages, context, stage):
        names = [r["filename"] for r in batch_rows(messages)]
        seen.extend(names)
        return selected([docs[-1].filename] if docs[-1].filename in names else [])

    result, gateway = run_screen(monkeypatch, base_env, tmp_workspace, papers_root, docs, decide)
    assert seen == [d.filename for d in docs]
    assert result["papers"][0]["filename"] == docs[-1].filename
    assert gateway.budgets[0] < 60  # not the whole 180-second task budget


def test_a_batch_cannot_select_an_unseen_authorized_paper(
    monkeypatch, base_env, tmp_workspace, papers_root
):
    docs = documents(10)

    def decide(messages, context, stage):
        names = [r["filename"] for r in batch_rows(messages)]
        # Hallucinate a later authorized filename only when its evidence is absent.
        return selected([docs[-1].filename] if docs[-1].filename not in names else [])

    result, _ = run_screen(monkeypatch, base_env, tmp_workspace, papers_root, docs, decide)
    assert result["papers"] == []


def test_context_overflow_splits_without_losing_candidates(
    monkeypatch, base_env, tmp_workspace, papers_root
):
    from research_assistant.errors import GatewayError

    docs = documents(10)
    seen = []

    def decide(messages, context, stage):
        rows = batch_rows(messages)
        if len(rows) > 2:
            return GatewayError("context length exceeded")
        seen.extend(r["filename"] for r in rows)
        return selected([])

    result, _ = run_screen(monkeypatch, base_env, tmp_workspace, papers_root, docs, decide)
    assert sorted(seen) == sorted(d.filename for d in docs)
    assert result["papers"] == []


def test_failed_batch_preserves_successes_and_screens_later_batches(
    monkeypatch, base_env, tmp_workspace, papers_root
):
    from research_assistant.errors import GatewayError

    docs = documents(20)
    seen = []

    def decide(messages, context, stage):
        rows = batch_rows(messages)
        names = [r["filename"] for r in rows]
        seen.extend(names)
        if docs[8].filename in names:
            return GatewayError("temporary upstream error")
        return selected([docs[0].filename] if docs[0].filename in names else [])

    result, _ = run_screen(monkeypatch, base_env, tmp_workspace, papers_root, docs, decide)
    assert result["papers"][0]["filename"] == docs[0].filename
    assert docs[-1].filename in seen


def test_all_failed_batches_raise_instead_of_claiming_no_relevant_papers(
    monkeypatch, base_env, tmp_workspace, papers_root
):
    from research_assistant.errors import GatewayError

    with pytest.raises(GatewayError):
        run_screen(
            monkeypatch,
            base_env,
            tmp_workspace,
            papers_root,
            documents(10),
            lambda *args: GatewayError("upstream error"),
        )


def test_invalid_global_order_preserves_all_confirmed_papers(
    monkeypatch, base_env, tmp_workspace, papers_root
):
    docs = documents(10)
    confirmed = []

    def decide(messages, context, stage):
        if stage == "retrieval.order":
            return '["invented.pdf"]'
        name = batch_rows(messages)[0]["filename"]
        confirmed.append(name)
        return selected([name])

    result, gateway = run_screen(monkeypatch, base_env, tmp_workspace, papers_root, docs, decide)
    assert [p["filename"] for p in result["papers"]] == confirmed
    assert gateway.calls[-1][0] == "retrieval.order"


def test_single_oversized_candidate_is_compacted_without_removal(
    monkeypatch, base_env, tmp_workspace, papers_root
):
    from research_assistant.errors import GatewayError

    sizes = []

    def decide(messages, context, stage):
        rows = batch_rows(messages)
        sizes.append(len(messages[-1].content))
        return (
            GatewayError("context length exceeded")
            if len(sizes) == 1
            else selected([rows[0]["filename"]])
        )

    result, _ = run_screen(monkeypatch, base_env, tmp_workspace, papers_root, documents(1), decide)
    assert len(sizes) == 2 and sizes[1] < sizes[0]
    assert result["papers"][0]["filename"] == "paper_000.pdf"


def test_budget_exhaustion_after_success_preserves_verified_result(
    monkeypatch, base_env, tmp_workspace, papers_root
):
    from research_assistant.runtime import deadline

    tick = [1000.0]
    monkeypatch.setattr(deadline.time, "monotonic", lambda: tick[0])
    docs = documents(20)

    def decide(messages, context, stage):
        name = batch_rows(messages)[0]["filename"]
        tick[0] += 179.0
        return selected([name])

    result, gateway = run_screen(monkeypatch, base_env, tmp_workspace, papers_root, docs, decide)
    assert len(gateway.calls) == 1
    assert result["papers"][0]["filename"] == docs[0].filename


def test_one_child_budget_failure_does_not_abandon_later_batches(
    monkeypatch, base_env, tmp_workspace, papers_root
):
    from research_assistant.errors import BudgetExhausted

    docs = documents(20)
    attempts = []

    def decide(messages, context, stage):
        names = [r["filename"] for r in batch_rows(messages)]
        attempts.extend(names)
        if docs[0].filename in names:
            return BudgetExhausted("this allocated batch deadline expired")
        return selected([docs[-1].filename] if docs[-1].filename in names else [])

    result, _ = run_screen(monkeypatch, base_env, tmp_workspace, papers_root, docs, decide)
    assert docs[-1].filename in attempts
    assert result["papers"][0]["filename"] == docs[-1].filename


def test_irreducible_long_filename_does_not_discard_previous_results(
    monkeypatch, base_env, tmp_workspace, papers_root
):
    from dataclasses import replace

    from research_assistant.errors import GatewayError

    docs = list(documents(6))
    long_name = "/".join(["a" * 100] * 11) + "/paper.pdf"
    docs[-1] = replace(docs[-1], filename=long_name)

    def decide(messages, context, stage):
        names = [r["filename"] for r in batch_rows(messages)]
        if long_name in names:
            return GatewayError("context length exceeded")
        return selected([docs[0].filename])

    result, _ = run_screen(monkeypatch, base_env, tmp_workspace, papers_root, docs, decide)
    assert result["papers"][0]["filename"] == docs[0].filename


@pytest.mark.parametrize("mode", ["middle_failure", "global_budget", "all_failed"])
def test_coverage_log_identifies_exact_unreviewed_range(
    monkeypatch, base_env, tmp_workspace, papers_root, capfd, mode
):
    import json

    from research_assistant.errors import GatewayError
    from research_assistant.runtime import deadline

    tick = [1000.0]
    monkeypatch.setattr(deadline.time, "monotonic", lambda: tick[0])
    docs = documents(20)
    reviewed = []

    def decide(messages, context, stage):
        names = [r["filename"] for r in batch_rows(messages)]
        if mode == "all_failed" or (mode == "middle_failure" and docs[8].filename in names):
            return GatewayError("upstream failure")
        reviewed.extend(names)
        if mode == "global_budget":
            tick[0] += 179
        return selected([])

    if mode == "all_failed":
        with pytest.raises(GatewayError):
            run_screen(monkeypatch, base_env, tmp_workspace, papers_root, docs, decide)
    else:
        run_screen(monkeypatch, base_env, tmp_workspace, papers_root, docs, decide)
    events = [
        json.loads(line) for line in capfd.readouterr().err.splitlines() if line.startswith("{")
    ]
    coverage = next(e for e in events if e["event"] == "retrieval.coverage")
    assert coverage["screened"] == len(reviewed)
    assert coverage["unreviewed_filenames"] == [
        d.filename for d in docs if d.filename not in reviewed
    ]
