"""只使用公开开发标注；独立手算结果，不复制检索算法。"""

from __future__ import annotations

from copy import deepcopy

import pytest

from research_assistant.reproduction.invariants import check_invariants


def example():
    configs = [
        {"k1": 1.2, "b": 1.0, "title_weight": 0.5},
        {"k1": 1.2, "b": 0.0, "title_weight": 0.5},
    ]
    queries = [
        {"id": "qa1", "text": "x", "group": "A", "relevant": ["d0"]},
        {"id": "qa2", "text": "x", "group": "A", "relevant": ["d0"]},
        {"id": "qb", "text": "x", "group": "B", "relevant": ["d5"]},
    ]
    payload = {
        "case_id": "D-02",
        "phase": "run",
        "data": {
            "documents": [{"id": f"d{i}", "title": "", "text": "x"} for i in range(6)],
            "dev_queries": queries,
            "test_queries": [{"id": "test", "text": "x"}],
        },
        "protocol": {"grid": configs},
    }
    rows = [{"id": f"d{i}", "score": 0.0} for i in range(6)]
    gm = {
        "groups": {
            "A": {"recall_at_5_percent": 100, "mrr_at_10": 1},
            "B": {"recall_at_5_percent": 0, "mrr_at_10": 1 / 6},
        },
        "worst_mrr_at_10": 1 / 6,
        "macro_mrr_at_10": 7 / 12,
        "macro_recall_at_5_percent": 50,
    }
    search = [
        {
            "config": c,
            "rankings": {q["id"]: deepcopy(rows) for q in queries},
            "recall_at_5_percent": 200 / 3,
            "mrr_at_10": 13 / 18,
            "group_metrics": deepcopy(gm),
        }
        for c in configs
    ]
    result = {
        "baseline": {"test": deepcopy(rows)},
        "candidate": {"test": deepcopy(rows)},
        "details": {"selected": configs[1], "search": search},
    }
    return payload, result


def test_valid_development_statistics_are_accepted():
    payload, result = example()
    assert check_invariants("D-02", payload, result) == []


@pytest.mark.parametrize("target", ["baseline", "candidate", "dev"])
def test_missing_required_query_is_rejected(target):
    payload, result = example()
    if target == "dev":
        result["details"]["search"][0]["rankings"] = {}
    else:
        result[target] = {}
    assert check_invariants("D-02", payload, result)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "nan", "unsorted"])
def test_invalid_document_ranking_is_rejected(mutation):
    payload, result = example()
    rows = result["candidate"]["test"]
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows.append(deepcopy(rows[0]))
    elif mutation == "nan":
        rows[0]["score"] = float("nan")
    else:
        rows.reverse()
    assert check_invariants("D-02", payload, result)


@pytest.mark.parametrize("field", ["mrr_at_10", "recall_at_5_percent", "macro_mrr_at_10", "groups"])
def test_self_reported_development_metrics_must_match_rankings(field):
    payload, result = example()
    row = result["details"]["search"][0]
    if field == "groups":
        row["group_metrics"][field] = {}
    elif field == "macro_mrr_at_10":
        row["group_metrics"][field] = 13 / 18  # micro mean is not group macro mean
    else:
        row[field] = 1234
    assert check_invariants("D-02", payload, result)


def test_complete_tie_uses_b_as_final_tiebreaker():
    payload, result = example()
    result["details"]["selected"] = payload["protocol"]["grid"][0]
    assert check_invariants("D-02", payload, result)


def test_duplicate_config_does_not_count_as_full_grid():
    payload, result = example()
    result["details"]["search"][1]["config"] = result["details"]["search"][0]["config"]
    assert check_invariants("D-02", payload, result)


@pytest.mark.parametrize("representation", ["ordered", "keyed"])
def test_equivalent_development_ranking_representations_are_accepted(representation):
    payload, result = example()
    for search in result["details"]["search"]:
        ranks = search["rankings"]
        values = [[[row["id"], row["score"]] for row in rows] for rows in ranks.values()]
        search["rankings"] = (
            values
            if representation == "ordered"
            else [
                {"query_id": qid, "rankings": rows} for qid, rows in zip(ranks, values, strict=True)
            ]
        )
    assert check_invariants("D-02", payload, result) == []


def test_rank_phase_cannot_omit_all_query_predictions():
    payload, _ = example()
    payload["phase"] = "rank"
    assert check_invariants("D-02", payload, {"candidate": {}})


@pytest.mark.parametrize("value", [None, [], "bad", True])
def test_malformed_search_row_is_diagnosed_without_crashing(value):
    payload, result = example()
    result["details"]["search"][0] = value
    assert check_invariants("D-02", payload, result)


def test_baseline_only_is_legal_when_complete_and_honest():
    payload, result = example()
    result["candidate"] = None
    result["details"] = {}
    assert check_invariants("D-02", payload, result) == []


def test_missing_candidate_is_not_an_explicit_baseline_only_result():
    payload, result = example()
    result.pop("candidate")
    result["details"] = {}
    assert check_invariants("D-02", payload, result)


def test_nested_test_wrapper_reports_actual_keys_and_required_shape():
    payload, result = example()
    result["baseline"] = {"test": result["baseline"]}
    # Use a different query ID: "test" itself could be a valid supplied ID.
    payload["data"]["test_queries"][0]["id"] = "q02"
    errors = check_invariants("D-02", payload, result)
    assert errors and "query_id" in errors[0] and "实际键" in errors[0]
