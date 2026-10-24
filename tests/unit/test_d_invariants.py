"""D 类算法不变量检查的单元测试。

这些不变量是保护 40 分的最后一道闸：生成源码若违反它们，自测会把具体错误回传给模型
要求完整重生成。因此不变量本身必须有测试，否则它静默失效时无人发现。
每条测试都用实测抓到过的真实缺陷作为反例。
"""

from __future__ import annotations

from typing import Any

from research_assistant.reproduction.invariants import check_invariants

CLASSES = ["class_a", "class_b"]
VOCAB = ["alpha", "beta"]


def d01_payload(train: list[dict[str, str]], texts: list[str]) -> dict[str, Any]:
    return {"case_id": "D-01", "phase": "run", "data": {"train": train, "texts": texts}}


def d01_result(
    baseline: list[str],
    candidate: list[str] | None,
    *,
    weights: list[list[float]] | None = None,
    idf: dict[str, float] | None = None,
    classes: list[str] | None = None,
    vocabulary: list[str] | None = None,
    details: Any = "auto",
) -> dict[str, Any]:
    if details != "auto":
        return {"baseline": baseline, "candidate": candidate, "details": details}
    if candidate is None:
        return {"baseline": baseline, "candidate": None, "details": {}}
    return {
        "baseline": baseline,
        "candidate": candidate,
        "details": {
            "classes": classes if classes is not None else CLASSES,
            "vocabulary": vocabulary if vocabulary is not None else VOCAB,
            "idf": idf if idf is not None else {"alpha": 1.0, "beta": 1.0},
            "weights": weights if weights is not None else [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
        },
    }


# --- D-01 ---


def test_d01_tied_majority_must_pick_the_lexicographically_smallest_class() -> None:
    """实测缺陷：并列时取了 class_b，规则要求字典序最小的 class_a。"""
    train = [{"text": "alpha", "label": "class_a"}, {"text": "beta", "label": "class_b"}]
    problems = check_invariants(
        "D-01",
        d01_payload(train, ["alpha"]),
        d01_result(["class_b"], None),
    )
    assert any("字典序最小" in p for p in problems)


def test_d01_clear_majority_is_accepted() -> None:
    train = [
        {"text": "alpha", "label": "class_b"},
        {"text": "beta", "label": "class_b"},
        {"text": "alpha", "label": "class_a"},
    ]
    assert (
        check_invariants("D-01", d01_payload(train, ["alpha"]), d01_result(["class_b"], None)) == []
    )


def test_d01_output_length_must_match_texts() -> None:
    train = [{"text": "alpha", "label": "class_a"}]
    problems = check_invariants(
        "D-01", d01_payload(train, ["alpha", "beta"]), d01_result(["class_a"], None)
    )
    assert any("data.texts 一致" in p for p in problems)


def test_d01_unsorted_classes_and_vocabulary_are_flagged() -> None:
    train = [{"text": "alpha", "label": "class_a"}]
    problems = check_invariants(
        "D-01",
        d01_payload(train, ["alpha"]),
        d01_result(
            ["class_a"],
            ["class_b"],
            classes=["class_b", "class_a"],
            vocabulary=["beta", "alpha"],
        ),
    )
    assert any("classes 必须按字典序" in p for p in problems)
    assert any("vocabulary 必须按字典序" in p for p in problems)


def test_d01_weight_shape_must_be_classes_by_vocab_plus_one() -> None:
    train = [{"text": "alpha", "label": "class_a"}]
    problems = check_invariants(
        "D-01",
        d01_payload(train, ["alpha"]),
        d01_result(["class_a"], ["class_a"], weights=[[0.0, 0.0], [0.0, 0.0]]),
    )
    assert any("weights 形状" in p for p in problems)


def test_d01_weights_must_reconstruct_the_candidate_predictions() -> None:
    """官方明写「必须能重建预测」：全零权重只会预测 class_a，声称 class_b 就是不一致。"""
    train = [{"text": "alpha beta", "label": "class_a"}]
    problems = check_invariants(
        "D-01",
        d01_payload(train, ["alpha beta"]),
        d01_result(
            ["class_a"],
            ["class_b"],
            weights=[[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
            idf={"alpha": 1.0, "beta": 1.0},
        ),
    )
    assert any("重建 candidate" in p for p in problems)


def test_d01_consistent_weights_pass_reconstruction() -> None:
    train = [{"text": "alpha", "label": "class_a"}]
    # 给 class_b 一个正的 bias，则任何输入都应预测 class_b
    problems = check_invariants(
        "D-01",
        d01_payload(train, ["alpha", "beta"]),
        d01_result(
            ["class_a", "class_a"],
            ["class_b", "class_b"],
            weights=[[0.0, 0.0, 0.0], [0.0, 0.0, 1.0]],
            idf={"alpha": 1.0, "beta": 1.0},
        ),
    )
    assert problems == []


def test_d01_nested_model_key_is_flagged() -> None:
    """candidate 存在时 details 必须直接含四个字段，不能多嵌一层 model。"""
    train = [{"text": "alpha", "label": "class_a"}]
    problems = check_invariants(
        "D-01",
        d01_payload(train, ["alpha"]),
        d01_result(
            ["class_a"],
            ["class_b"],
            details={"model": {"classes": CLASSES, "vocabulary": VOCAB}},
        ),
    )
    assert any("不能多嵌一层 model" in p for p in problems)


def test_d01_honest_baseline_path_requires_empty_details() -> None:
    train = [{"text": "alpha", "label": "class_a"}]
    problems = check_invariants(
        "D-01",
        d01_payload(train, ["alpha"]),
        {"baseline": ["class_a"], "candidate": None, "details": {"classes": CLASSES}},
    )
    assert any("诚实基线路径" in p for p in problems)
    assert (
        check_invariants("D-01", d01_payload(train, ["alpha"]), d01_result(["class_a"], None)) == []
    )


# --- D-02 ---


def grid(n: int) -> list[dict[str, float]]:
    return [{"k1": 1.2, "b": 0.5, "title_weight": float(i)} for i in range(n)]


def d02_row() -> dict[str, Any]:
    return {
        "config": {"k1": 1.2, "b": 0.5, "title_weight": 0.0},
        "group_metrics": {
            "groups": ["topic"],
            "worst_mrr_at_10": 0.5,
            "macro_mrr_at_10": 0.5,
            "macro_recall_at_5_percent": 50.0,
        },
    }


def test_d02_run_requires_the_full_grid() -> None:
    payload = {"case_id": "D-02", "phase": "run", "data": {}, "protocol": {"grid": grid(27)}}
    result = {"baseline": {}, "candidate": {}, "details": {"search": [d02_row()] * 26}}
    problems = check_invariants("D-02", payload, result)
    assert any("全部 27 组候选" in p for p in problems)


def test_d02_run_accepts_a_complete_grid() -> None:
    payload = {"case_id": "D-02", "phase": "run", "data": {}, "protocol": {"grid": grid(27)}}
    result = {"baseline": {}, "candidate": {}, "details": {"search": [d02_row()] * 27}}
    assert check_invariants("D-02", payload, result) == []


def test_d02_missing_group_metrics_is_flagged() -> None:
    payload = {"case_id": "D-02", "phase": "run", "data": {}, "protocol": {"grid": grid(1)}}
    result = {"baseline": {}, "candidate": {}, "details": {"search": [{"config": {}}]}}
    problems = check_invariants("D-02", payload, result)
    assert any("group_metrics" in p for p in problems)


def test_d02_rank_phase_must_not_retune_or_return_details() -> None:
    payload = {"case_id": "D-02", "phase": "rank", "data": {}, "protocol": {"grid": grid(27)}}
    problems = check_invariants("D-02", payload, {"candidate": {}, "details": {"selected": {}}})
    assert any("rank 阶段只应返回" in p for p in problems)
    assert check_invariants("D-02", payload, {"candidate": {}}) == []


# --- D-05 ---


def d05_data() -> dict[str, Any]:
    return {
        "jobs": [
            {"id": "a", "flow": "A", "arrival_ms": 0, "service_ms": 2, "deadline_ms": 10},
            {"id": "b", "flow": "B", "arrival_ms": 0, "service_ms": 2, "deadline_ms": 10},
        ],
        "flow_weights": {"A": 1.0, "B": 1.0, "Z": 1.0},
        "servers": 1,
        "setup_ms": 0,
        "horizon_ms": 10,
    }


def trace_entry(job_id: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": job_id,
        "flow": job_id.upper(),
        "arrival_ms": 0,
        "service_ms": 2,
        "deadline_ms": 10,
        "server": 0,
        "dispatch_ms": 0,
        "setup_ms": 0,
        "start_ms": 0,
        "end_ms": 2,
        "wait_ms": 0,
    }
    base.update(overrides)
    return base


def d05_run(
    trace: list[dict[str, Any]],
    dropped: list[dict[str, Any]] | None = None,
    served: dict[str, float] | None = None,
) -> dict[str, Any]:
    return {
        "trace": trace,
        "dropped": dropped if dropped is not None else [],
        "served_ms": served if served is not None else {"A": 2.0, "B": 2.0, "Z": 0.0},
    }


def test_d05_task_conservation_flags_a_duplicated_task() -> None:
    """实测缺陷：task_3 同时出现在 trace 与 dropped，被拒绝的任务不得回队。"""
    payload = {"case_id": "D-05", "phase": "run", "data": d05_data()}
    run = d05_run([trace_entry("a"), trace_entry("b")], dropped=[{"id": "b", "reason": "x"}])
    problems = check_invariants("D-05", payload, {"baseline": run, "candidate": None})
    assert any("任务守恒" in p and "重复" in p for p in problems)


def test_d05_task_conservation_flags_a_missing_task() -> None:
    """实测缺陷：candidate 里任务 a、b 完全缺失。"""
    payload = {"case_id": "D-05", "phase": "run", "data": d05_data()}
    run = d05_run([])
    problems = check_invariants("D-05", payload, {"baseline": None, "candidate": run})
    assert any("任务守恒" in p and "缺失" in p for p in problems)


def test_d05_a_valid_run_passes() -> None:
    payload = {"case_id": "D-05", "phase": "run", "data": d05_data()}
    run = d05_run([trace_entry("a"), trace_entry("b", dispatch_ms=2, start_ms=2, end_ms=4)])
    assert check_invariants("D-05", payload, {"baseline": run, "candidate": run}) == []


def test_d05_start_at_or_after_deadline_must_not_be_dispatched() -> None:
    payload = {"case_id": "D-05", "phase": "run", "data": d05_data()}
    run = d05_run(
        [
            trace_entry("a", start_ms=10, end_ms=12),
            trace_entry("b", dispatch_ms=2, start_ms=2, end_ms=4),
        ]
    )
    problems = check_invariants("D-05", payload, {"baseline": run, "candidate": None})
    assert any("start>=deadline" in p for p in problems)


def test_d05_end_beyond_horizon_is_flagged_but_end_equal_horizon_is_allowed() -> None:
    payload = {"case_id": "D-05", "phase": "run", "data": d05_data()}
    over = d05_run(
        [
            trace_entry("a", start_ms=9, end_ms=11),
            trace_entry("b", dispatch_ms=2, start_ms=2, end_ms=4),
        ]
    )
    assert any(
        "end>horizon" in p
        for p in check_invariants("D-05", payload, {"baseline": over, "candidate": None})
    )
    at = d05_run(
        [
            trace_entry("a", start_ms=8, end_ms=10),
            trace_entry("b", dispatch_ms=2, start_ms=2, end_ms=4),
        ]
    )
    assert not any(
        "end>horizon" in p
        for p in check_invariants("D-05", payload, {"baseline": at, "candidate": None})
    )


def test_d05_served_ms_must_include_zero_service_flows() -> None:
    payload = {"case_id": "D-05", "phase": "run", "data": d05_data()}
    run = d05_run(
        [trace_entry("a"), trace_entry("b", dispatch_ms=2, start_ms=2, end_ms=4)],
        served={"A": 2.0, "B": 2.0},
    )
    problems = check_invariants("D-05", payload, {"baseline": run, "candidate": None})
    assert any("零服务流" in p and "Z" in p for p in problems)


def test_unknown_case_id_is_a_no_op() -> None:
    assert check_invariants("D-99", {"data": {}}, {}) == []
    assert check_invariants("D-01", {}, {}) == []


# --- D-05 换流 setup（参考模拟器发现的真实缺陷） ---


def d05_setup_data(setup_ms: float = 2.0) -> dict[str, Any]:
    """对应公开样例「空闲间隔与流切换」：单服务端、A 流后接 B 流、中间有空闲。"""
    return {
        "jobs": [
            {"id": "task_1", "flow": "A", "arrival_ms": 0, "service_ms": 1, "deadline_ms": 12},
            {"id": "task_2", "flow": "B", "arrival_ms": 5, "service_ms": 1, "deadline_ms": 12},
        ],
        "flow_weights": {"A": 1.0, "B": 2.0, "ZERO": 1.0},
        "servers": 1,
        "setup_ms": setup_ms,
        "horizon_ms": 12,
    }


def _run_with(trace: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "baseline": {
            "trace": trace,
            "dropped": [],
            "served_ms": {"A": 1.0, "B": 1.0, "ZERO": 0.0},
        },
        "candidate": None,
    }


def test_d05_flow_switch_after_idle_must_pay_setup() -> None:
    """实测缺陷：模型漏算换流 setup，空闲之后尤其容易漏。

    规则明写「同服务端换流付 setup，首次无 setup，空闲不清除上一流」，
    所以 task_2（B 流）在 task_1（A 流）之后必须付 setup=2，start=7 而非 5。
    """
    payload = {"case_id": "D-05", "phase": "run", "data": d05_setup_data()}
    wrong = _run_with(
        [
            trace_entry("task_1", flow="A", dispatch_ms=0, setup_ms=0, start_ms=0, end_ms=1),
            trace_entry("task_2", flow="B", dispatch_ms=5, setup_ms=0, start_ms=5, end_ms=6),
        ]
    )
    problems = check_invariants("D-05", payload, wrong)
    assert any("setup_ms 应为 2.0" in p and "空闲不清除上一流" in p for p in problems)


def test_d05_correct_flow_switch_setup_passes() -> None:
    payload = {"case_id": "D-05", "phase": "run", "data": d05_setup_data()}
    right = _run_with(
        [
            trace_entry(
                "task_1",
                flow="A",
                arrival_ms=0,
                service_ms=1,
                deadline_ms=12,
                server=0,
                dispatch_ms=0,
                setup_ms=0,
                start_ms=0,
                end_ms=1,
                wait_ms=0,
            ),
            trace_entry(
                "task_2",
                flow="B",
                arrival_ms=5,
                service_ms=1,
                deadline_ms=12,
                server=0,
                dispatch_ms=5,
                setup_ms=2,
                start_ms=7,
                end_ms=8,
                wait_ms=2,
            ),
        ]
    )
    assert check_invariants("D-05", payload, right) == []


def test_d05_same_flow_consecutively_pays_no_setup() -> None:
    payload = {"case_id": "D-05", "phase": "run", "data": d05_setup_data()}
    run = _run_with(
        [
            trace_entry("task_1", flow="A", dispatch_ms=0, setup_ms=0, start_ms=0, end_ms=1),
            trace_entry(
                "task_2",
                flow="A",
                arrival_ms=5,
                dispatch_ms=5,
                setup_ms=0,
                start_ms=5,
                end_ms=6,
                wait_ms=0,
            ),
        ]
    )
    assert check_invariants("D-05", payload, run) == []


def test_d05_spurious_setup_on_the_first_dispatch_is_flagged() -> None:
    payload = {"case_id": "D-05", "phase": "run", "data": d05_setup_data()}
    run = _run_with(
        [
            trace_entry("task_1", flow="A", dispatch_ms=0, setup_ms=2, start_ms=2, end_ms=3),
            trace_entry(
                "task_2",
                flow="B",
                arrival_ms=5,
                dispatch_ms=5,
                setup_ms=2,
                start_ms=7,
                end_ms=8,
                wait_ms=2,
            ),
        ]
    )
    problems = check_invariants("D-05", payload, run)
    assert any("首次派发" in p for p in problems)


def test_d05_setup_check_is_per_server() -> None:
    """不同服务端各自独立记「上一流」，不能跨服务端串味。"""
    data = {**d05_setup_data(), "servers": 2}
    payload = {"case_id": "D-05", "phase": "run", "data": data}
    run = {
        "baseline": {
            "trace": [
                trace_entry(
                    "task_1", flow="A", server=0, dispatch_ms=0, setup_ms=0, start_ms=0, end_ms=1
                ),
                trace_entry(
                    "task_2",
                    flow="B",
                    arrival_ms=5,
                    server=1,
                    dispatch_ms=5,
                    setup_ms=0,
                    start_ms=5,
                    end_ms=6,
                    wait_ms=0,
                ),
            ],
            "dropped": [],
            "served_ms": {"A": 1.0, "B": 1.0, "ZERO": 0.0},
        },
        "candidate": None,
    }
    # server 1 上 task_2 是首次派发，setup 必须为 0，即使 server 0 上一股是 A 流
    assert check_invariants("D-05", payload, run) == []


def test_d05_setup_check_is_skipped_when_setup_ms_is_absent() -> None:
    data = {k: v for k, v in d05_setup_data().items() if k != "setup_ms"}
    payload = {"case_id": "D-05", "phase": "run", "data": data}
    run = _run_with(
        [
            trace_entry("task_1", flow="A", dispatch_ms=0, setup_ms=0, start_ms=0, end_ms=1),
            trace_entry(
                "task_2",
                flow="B",
                arrival_ms=5,
                dispatch_ms=5,
                setup_ms=0,
                start_ms=5,
                end_ms=6,
                wait_ms=0,
            ),
        ]
    )
    assert check_invariants("D-05", payload, run) == []
