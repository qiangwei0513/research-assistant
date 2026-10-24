"""D 类算法不变量检查（生产自测用）。

为什么需要：`reproduction/selftest.py` 原本只检查「能跑通、返回 JSON 对象」。
实测发现这远远不够——真实网关生成的源码在两个边界用例上算法是错的，而输出结构完全合法：

- D-01 `均衡-空文本-未知词`：类别并列时基线取了 `class_b`，而官方规则要求
  「多数类基线和预测并列均取字典序最小类」，应为 `class_a`。
- D-05 `并发预留`：`task_3` 在 trace 与 dropped 中重复出现，违反
  「每题每条件从空状态开始，每任务恰好进入 trace 或 dropped 一次」。

这两个错误都能通过官方来源准入、也能通过结构性自测，只有比对官方 method 的
不变量才能发现。把它们放进自测后，重生成回路才会真正带着错误信息去修，
而不是把一份跑得出错误数字的源码送去正式执行并锁源。

只检查**可从输出与输入独立复核**的不变量；不复制完整参考实现，
也不计算任何官方指标（指标由可信框架计算）。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence

_TOKEN = re.compile(r"[a-z0-9]+")


def check_invariants(
    case_id: str, payload: Mapping[str, object], result: Mapping[str, object]
) -> list[str]:
    """返回被违反的不变量描述；空列表表示通过。描述会回传给模型用于重生成。"""
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return []
    if case_id == "D-01":
        return _check_d01(data, result)
    if case_id == "D-02":
        return _check_d02(payload, result)
    if case_id == "D-05":
        return _check_d05(data, result)
    return []


def _check_d01(data: Mapping[str, object], result: Mapping[str, object]) -> list[str]:
    problems: list[str] = []
    train = data.get("train")
    texts = data.get("texts")
    baseline = result.get("baseline")
    candidate = result.get("candidate")
    details = result.get("details")

    if isinstance(train, list) and isinstance(baseline, list) and isinstance(texts, list):
        labels = [str(item.get("label")) for item in train if isinstance(item, Mapping)]
        if labels:
            counts = Counter(labels)
            top = max(counts.values())
            majority = sorted(label for label, count in counts.items() if count == top)[0]
            wrong = [label for label in baseline if label != majority]
            if wrong:
                problems.append(
                    f"基线必须是训练集多数类，且并列时取字典序最小类：期望全部为 {majority!r}，"
                    f"实际出现 {sorted(set(wrong))[:3]}"
                )
        if len(baseline) != len(texts):
            problems.append(f"baseline 长度必须与 data.texts 一致：{len(baseline)} vs {len(texts)}")
    if isinstance(candidate, list) and isinstance(texts, list) and len(candidate) != len(texts):
        problems.append(f"candidate 长度必须与 data.texts 一致：{len(candidate)} vs {len(texts)}")

    if candidate is None:
        if details not in ({}, None):
            problems.append("candidate 为 null（诚实基线路径）时 details 必须是空对象")
        return problems
    if not isinstance(details, Mapping):
        return problems

    if "model" in details:
        problems.append("details 必须直接含 classes/vocabulary/idf/weights，不能多嵌一层 model")
    classes = details.get("classes")
    vocabulary = details.get("vocabulary")
    weights = details.get("weights")
    if not isinstance(classes, list) or not isinstance(vocabulary, list):
        return problems
    if classes != sorted(classes):
        problems.append("classes 必须按字典序排列")
    if vocabulary != sorted(vocabulary):
        problems.append("vocabulary 必须按字典序排列")
    if isinstance(weights, list) and weights:
        if len(weights) != len(classes) or any(
            not isinstance(row, list) or len(row) != len(vocabulary) + 1 for row in weights
        ):
            problems.append(
                f"weights 形状必须是 类别数×(词表大小+1)="
                f"{len(classes)}×{len(vocabulary) + 1}，最后一列为 bias"
            )
            return problems
        rebuilt = _rebuild_d01(texts, vocabulary, details.get("idf"), weights, classes)
        if rebuilt is not None:
            diffs = [
                (index, candidate[index], rebuilt[index])
                for index in range(min(len(candidate), len(rebuilt)))
                if candidate[index] != rebuilt[index]
            ]
            if diffs or len(rebuilt) != len(candidate):
                problems.append(
                    "用 details 里的词表/IDF/权重必须能重建 candidate 预测"
                    "（raw TF×IDF 后 L2 归一、追加 bias=1、并列取字典序最小类）；"
                    f"{len(diffs)} 处不一致，例如 {diffs[:2]}"
                )
    return problems


def _rebuild_d01(
    texts: Sequence[object],
    vocabulary: Sequence[str],
    idf: object,
    weights: Sequence[Sequence[float]],
    classes: Sequence[str],
) -> list[str] | None:
    if isinstance(idf, Mapping):
        idf_map = {str(k): float(v) for k, v in idf.items()}
    elif isinstance(idf, Sequence):
        idf_map = dict(zip(vocabulary, (float(v) for v in idf), strict=False))
    else:
        return None
    out: list[str] = []
    for text in texts:
        counts = Counter(_TOKEN.findall(str(text).lower()))
        vector = [float(counts.get(term, 0)) * idf_map.get(term, 0.0) for term in vocabulary]
        norm = math.sqrt(sum(value * value for value in vector))
        if norm > 0:
            vector = [value / norm for value in vector]
        vector.append(1.0)
        scores = [
            sum(weight * value for weight, value in zip(row, vector, strict=False))
            for row in weights
        ]
        best = max(scores)
        out.append(next(classes[i] for i, score in enumerate(scores) if score == best))
    return out


def _check_d02(payload: Mapping[str, object], result: Mapping[str, object]) -> list[str]:
    problems: list[str] = []
    protocol = payload.get("protocol")
    details = result.get("details")
    if payload.get("phase") == "rank":
        if set(result) != {"candidate"}:
            problems.append(
                f"rank 阶段只应返回 {{candidate: rankings}}，不得重新选参；实际键 {sorted(result)}"
            )
        return problems
    if not isinstance(details, Mapping):
        return problems
    search = details.get("search")
    if isinstance(protocol, Mapping) and isinstance(search, list):
        grid = protocol.get("grid")
        if isinstance(grid, list) and len(search) != len(grid):
            problems.append(
                f"details.search 必须包含全部 {len(grid)} 组候选，实际 {len(search)} 行"
            )
    for row in search if isinstance(search, list) else []:
        if not isinstance(row, Mapping) or not isinstance(row.get("group_metrics"), Mapping):
            problems.append(
                "details.search 每行必须含 group_metrics（groups、worst_mrr_at_10、"
                "macro_mrr_at_10、macro_recall_at_5_percent）"
            )
            break
    return problems


def _check_d05(data: Mapping[str, object], result: Mapping[str, object]) -> list[str]:
    problems: list[str] = []
    jobs = data.get("jobs")
    if not isinstance(jobs, list):
        return problems
    expected_ids = [str(job.get("id")) for job in jobs if isinstance(job, Mapping)]
    by_id = {str(job.get("id")): job for job in jobs if isinstance(job, Mapping)}
    horizon = data.get("horizon_ms")

    for name in ("baseline", "candidate"):
        run = result.get(name)
        if not isinstance(run, Mapping):
            if name == "baseline":
                problems.append("baseline 必须是含 trace/served_ms/dropped 的对象")
            continue
        trace = run.get("trace") if isinstance(run.get("trace"), list) else []
        dropped = run.get("dropped") if isinstance(run.get("dropped"), list) else []
        seen = [str(item.get("id")) for item in trace if isinstance(item, Mapping)]
        seen += [str(item.get("id")) for item in dropped if isinstance(item, Mapping)]
        duplicates = sorted({i for i, count in Counter(seen).items() if count > 1})
        missing = sorted(set(expected_ids) - set(seen))
        extra = sorted(set(seen) - set(expected_ids))
        if duplicates or missing or extra:
            problems.append(
                f"{name} 违反任务守恒：每个任务必须恰好进入 trace 或 dropped 一次"
                f"（重复 {duplicates[:3]}，缺失 {missing[:3]}，多余 {extra[:3]}）；"
                "被拒绝的任务不得回队，也不得重复派发"
            )
        for item in trace:
            if not isinstance(item, Mapping):
                continue
            job = by_id.get(str(item.get("id")))
            if job is None:
                continue
            try:
                start = float(item.get("start_ms", 0))
                end = float(item.get("end_ms", 0))
            except (TypeError, ValueError):
                continue
            if start >= float(job.get("deadline_ms", start + 1)):
                problems.append(
                    f"{name} 中 {item.get('id')} 的 start>=deadline 仍被派发；"
                    "必须先判 start>=deadline 再判 end>horizon"
                )
            if horizon is not None and end > float(horizon):
                problems.append(
                    f"{name} 中 {item.get('id')} 的 end>horizon；end==horizon 可完成，超出则不得派发"
                )
        served = run.get("served_ms")
        weights = data.get("flow_weights")
        if isinstance(served, Mapping) and isinstance(weights, Mapping):
            absent = sorted(set(weights) - set(served))
            if absent:
                problems.append(f"{name} 的 served_ms 必须包含零服务流：缺 {absent}")
    return problems
