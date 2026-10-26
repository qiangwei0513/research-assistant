"""Hand-derived small properties, not an embedded BM25 reference implementation."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from research_assistant.reproduction.materials import load_case_materials
from research_assistant.reproduction.selftest import (
    SelfTestCase,
    build_self_test_cases,
    run_self_test,
)
from tests.unit.test_d02_validation import example

ROOT = Path(__file__).resolve().parents[3] / "赛题信息/research-experiments-20261020-source-v1"


def setup_check():
    from research_assistant.reproduction.d02_validation import RankCheck

    return RankCheck


def rank_payload():
    return {
        "case_id": "D-02",
        "phase": "rank",
        "data": {
            "documents": [{"id": "a"}, {"id": "b"}],
            "test_queries": [{"id": "q", "text": "x x y"}],
        },
    }


def check(rows, expectation):
    from research_assistant.reproduction.d02_validation import check_rank_properties

    return check_rank_properties(rank_payload(), {"candidate": {"q": rows}}, {"q": expectation})


@pytest.mark.parametrize("scale", [1e-200, 1.0, 1e200])
def test_query_repetition_ratio_accepts_positive_global_scaling(scale):
    check_type = setup_check()
    assert (
        check([["b", 2 * scale], ["a", scale]], check_type(order=("b", "a"), ratio=("b", "a", 2)))
        == []
    )


@pytest.mark.parametrize("scores", [(0.0, 0.0), (1.0, 1.0), (2.0, 1.0)])
def test_wrong_query_frequency_and_constant_scores_are_rejected(scores):
    check_type = setup_check()
    # Stable order makes all three outputs structurally legal; the hand-derived ratio still fails.
    assert check([["a", scores[0]], ["b", scores[1]]], check_type(ratio=("a", "b", 3)))


def test_zero_query_requires_actual_zero_scores():
    check_type = setup_check()
    assert check([["a", 1], ["b", 1]], check_type(zero=True))
    assert check([["a", 0], ["b", 0]], check_type(order=("a", "b"), zero=True)) == []


def test_expected_order_checks_ranking_not_only_score_sorting():
    check_type = setup_check()
    assert check([["a", 2], ["b", 1]], check_type(order=("b", "a")))


def test_keyed_and_ordered_equivalent_representations_are_supported():
    from research_assistant.reproduction.d02_validation import check_rank_properties

    check_type = setup_check()
    expected = {"q": check_type(order=("b", "a"), ratio=("b", "a", 2))}
    for value in ([[["b", 2], ["a", 1]]], [{"query_id": "q", "rankings": [["b", 2], ["a", 1]]}]):
        assert check_rank_properties(rank_payload(), {"candidate": value}, expected) == []


def test_public_cases_gain_five_valid_rank_probes_without_mutating_protocol():
    materials = load_case_materials("D-02", ROOT)
    before = deepcopy(materials.protocol)
    cases = build_self_test_cases(materials, ROOT)
    probes = [case for case in cases if case.origin == "metamorphic:layered-d-v3"]
    assert len(probes) == 5
    assert all(case.payload["phase"] == "rank" and case.rank_checks for case in probes)
    assert all(case.replay_d02 for case in cases if case.payload.get("phase") == "run")
    assert materials.protocol == before


def test_other_cases_and_unknown_versions_do_not_gain_d02_probes(tmp_path):
    from dataclasses import replace

    materials = load_case_materials("D-02", ROOT)
    wrong = replace(materials, protocol={**materials.protocol, "version": "future-v4"})
    assert not any(
        case.origin.startswith("metamorphic") for case in build_self_test_cases(wrong, ROOT)
    )
    d01 = load_case_materials("D-01", ROOT)
    assert not any(
        case.origin.startswith("metamorphic") for case in build_self_test_cases(d01, ROOT)
    )


def replay_case(tmp_path, *, inconsistent=False, baseline_only=False):
    payload, result = example()
    payload["protocol"]["default_config"] = {"k1": 1.2, "b": 0.75, "title_weight": 0}
    if baseline_only:
        result["candidate"] = None
        result["details"] = {}
    rows = result["baseline"]["test"]
    if inconsistent:
        rows = [{"id": f"d{i}", "score": i + 1} for i in range(5, -1, -1)]
    replay = {"candidate": {"test": rows}}
    source = tmp_path / "implementation.py"
    source.write_text(
        "import json\ndef run_experiment(request):\n    return json.loads("
        + repr(json.dumps(replay))
        + ") if request['phase']=='rank' else json.loads("
        + repr(json.dumps(result))
        + ")\n"
    )
    return source, SelfTestCase("run", payload, "test", replay_d02=True)


def test_real_child_detects_run_rank_inconsistency(tmp_path):
    implementation, case = replay_case(tmp_path, inconsistent=True)
    result = run_self_test(implementation, [case], work_dir=tmp_path / "work")
    assert not result.ok and "排名" in (result.error or "")
    assert result.case_count == 3


@pytest.mark.parametrize("baseline_only,count", [(False, 3), (True, 2)])
def test_real_child_replays_default_and_selected_with_honest_baseline_support(
    tmp_path, baseline_only, count
):
    implementation, case = replay_case(tmp_path, baseline_only=baseline_only)
    result = run_self_test(implementation, [case], work_dir=tmp_path / "work")
    assert result.ok, result.error
    assert result.case_count == result.passed == count


def test_synthetic_all_zero_source_is_rejected_before_source_lock(tmp_path):
    materials = load_case_materials("D-02", ROOT)
    cases = [
        c for c in build_self_test_cases(materials, ROOT) if c.origin.startswith("metamorphic")
    ]
    assert cases
    implementation = tmp_path / "zero.py"
    implementation.write_text(
        "def run_experiment(r):\n    rows=[{'id':d['id'],'score':0} for d in sorted(r['data']['documents'],key=lambda d:d['id'])]\n    return {'candidate':{q['id']:rows for q in r['data']['test_queries']}}\n"
    )
    result = run_self_test(implementation, cases, work_dir=tmp_path / "work")
    assert not result.ok


def test_derived_replays_share_the_original_batch_budget(tmp_path, monkeypatch):
    import subprocess

    from research_assistant.reproduction import selftest

    implementation, case = replay_case(tmp_path)
    _payload, response = example()
    tick = [100.0]
    monkeypatch.setattr("time.monotonic", lambda: tick[0])
    calls = []

    def execute(command, *, timeout, **kwargs):
        calls.append(timeout)
        tick[0] += 0.6
        Path(command[-1]).write_text(json.dumps(response))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(selftest, "_run_process", execute)
    result = run_self_test(implementation, [case], work_dir=tmp_path / "work", timeout=1)
    assert calls == pytest.approx([1, 0.4])
    assert not result.ok and result.case_count == 3


def probe_outputs():
    return {
        "d02-repeated-unknown-empty": {
            "repeat": [["b", 2], ["a", 1]],
            "unknown": [["a", 0], ["b", 0]],
            "empty": [["a", 0], ["b", 0]],
        },
        "d02-title-zero": {"title": [["b", 1], ["a", 0]]},
        "d02-title-two": {"title": [["a", 2], ["b", 1]]},
        "d02-length-zero": {"length": [["b", 11], ["a", 7]]},
        "d02-length-one": {"length": [["a", 43], ["b", 31]]},
    }


def test_each_probe_accepts_independently_hand_derived_scaled_scores():
    from research_assistant.reproduction.d02_probes import build_rank_probes
    from research_assistant.reproduction.d02_validation import check_rank_properties

    protocol = load_case_materials("D-02", ROOT).protocol
    outputs = probe_outputs()
    for probe in build_rank_probes(protocol):
        assert (
            check_rank_properties(probe.payload, {"candidate": outputs[probe.name]}, probe.checks)
            == []
        )


@pytest.mark.parametrize(
    "fault", ["deduplicated_query", "ignored_title", "ignored_b", "unknown_nonzero"]
)
def test_probe_oracles_reject_individual_semantic_faults(fault):
    from research_assistant.reproduction.d02_probes import build_rank_probes
    from research_assistant.reproduction.d02_validation import check_rank_properties

    probes = {p.name: p for p in build_rank_probes(load_case_materials("D-02", ROOT).protocol)}
    outputs = probe_outputs()
    name = "d02-repeated-unknown-empty"
    if fault == "deduplicated_query":
        outputs[name]["repeat"] = [["a", 1], ["b", 1]]
    elif fault == "ignored_title":
        name = "d02-title-two"
        outputs[name]["title"] = [["a", 1], ["b", 1]]
    elif fault == "ignored_b":
        name = "d02-length-one"
        outputs[name] = outputs["d02-length-zero"]
    else:
        outputs[name]["unknown"] = [["a", 1], ["b", 1]]
    probe = probes[name]
    assert check_rank_properties(probe.payload, {"candidate": outputs[name]}, probe.checks)
