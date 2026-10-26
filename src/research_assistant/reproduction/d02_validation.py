"""D-02 的输出与公开开发标注校验；不实现检索算法、不生成正式指标。"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_CONFIG_KEYS = ("k1", "b", "title_weight")


class _Invalid(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise _Invalid(message)


def _number(value: Any) -> float:
    _require(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value),
        "分数、指标与配置必须为有限数值",
    )
    return float(value)


def _config(value: Any) -> tuple[float, float, float]:
    _require(isinstance(value, Mapping), "config/selected 必须是对象")
    return tuple(_number(value.get(key)) for key in _CONFIG_KEYS)


def _ranking_map(value: Any, queries: list[dict]) -> Mapping[str, Any]:
    """接受 query_id 字典、按查询顺序的完整数组、带 query_id/rankings 的数组。"""
    if isinstance(value, Mapping):
        return value
    _require(isinstance(value, list), "rankings 必须是 query_id 字典或完整数组")
    if value and isinstance(value[0], Mapping):
        out: dict[str, Any] = {}
        for row in value:
            _require(isinstance(row, Mapping) and "query_id" in row, "查询排名行缺少 query_id")
            qid = str(row["query_id"])
            _require(qid not in out, f"查询 {qid} 重复")
            out[qid] = row.get("rankings")
        return out
    _require(len(value) == len(queries), "顺序数组须包含全部必需查询")
    return {str(query["id"]): rows for query, rows in zip(queries, value, strict=True)}


def _ranking_entries(
    value: Any, queries: list[dict], doc_ids: set[str], label: str
) -> dict[str, list[tuple[str, float]]]:
    mapped = _ranking_map(value, queries)
    checked: dict[str, list[tuple[str, float]]] = {}
    for query in queries:
        qid = str(query["id"])
        _require(
            qid in mapped,
            f"{label} 缺少必需查询 {qid}；应直接为 {{query_id: [{{id,score}}, ...]}}，"
            f"不能包在 dev/test 子对象；实际键 {list(mapped)[:8]}",
        )
        rows = mapped[qid]
        _require(isinstance(rows, list), f"{label}/{qid} 排名必须是数组")
        entries: list[tuple[str, float]] = []
        for row in rows:
            if isinstance(row, Mapping):
                _require("id" in row and "score" in row, "排名行必须包含 id/score")
                doc_id, score = row["id"], row["score"]
            else:
                _require(
                    isinstance(row, (list, tuple)) and len(row) == 2,
                    "排名行必须为 {id,score} 或 [id,score]",
                )
                doc_id, score = row
            entries.append((str(doc_id), _number(score)))
        ids = [doc_id for doc_id, _ in entries]
        _require(
            len(ids) == len(doc_ids) and set(ids) == doc_ids,
            f"{label}/{qid} 必须包含全部文档且每个恰好一次",
        )
        _require(all(score >= 0 for _, score in entries), f"{label}/{qid} BM25 分数不得为负")
        _require(
            entries == sorted(entries, key=lambda item: (-item[1], item[0])),
            f"{label}/{qid} 必须按 (-score,id) 排序",
        )
        checked[qid] = entries
    return checked


def ranking_orders(
    value: Any, queries: list[dict], doc_ids: set[str], label: str
) -> dict[str, list[str]]:
    return {
        qid: [doc_id for doc_id, _ in entries]
        for qid, entries in _ranking_entries(value, queries, doc_ids, label).items()
    }


@dataclass(frozen=True)
class RankCheck:
    order: tuple[str, ...] | None = None
    ratio: tuple[str, str, float] | None = None
    zero: bool = False
    positive: tuple[str, ...] = ()


def check_rank_properties(
    payload: Mapping[str, Any], result: Mapping[str, Any], checks: Mapping[str, RankCheck]
) -> list[str]:
    """Verify a few hand-derived properties, allowing arbitrary positive score scaling."""
    try:
        data = payload["data"]
        rankings = _ranking_entries(
            result.get("candidate"),
            data["test_queries"],
            {str(d["id"]) for d in data["documents"]},
            "candidate",
        )
        for qid, check in checks.items():
            _require(qid in rankings, f"性质校验缺少查询 {qid}")
            entries = rankings[qid]
            ids = tuple(doc_id for doc_id, _ in entries)
            scores = dict(entries)
            if check.order is not None:
                _require(
                    ids == check.order, f"{qid} 排名违反公开规则：期望 {check.order}，实际 {ids}"
                )
            if check.zero:
                _require(
                    all(score == 0 for _, score in entries), f"{qid} 空/未知查询分数必须全部为0"
                )
            for doc_id in check.positive:
                _require(scores.get(doc_id, 0) > 0, f"{qid}/{doc_id} 命中词应产生正分")
            if check.ratio is not None:
                left, right, expected = check.ratio
                _require(
                    scores.get(left, 0) > 0 and scores.get(right, 0) > 0,
                    f"{qid} 比例校验的两文档必须为正分，不能用全零排名代替算法",
                )
                actual = scores[left] / scores[right]
                _require(
                    math.isclose(actual, expected, rel_tol=1e-7, abs_tol=0),
                    f"{qid} 分数比应为 {expected:.12g}，实际 {actual:.12g}；检查查询词频/字段权重/长度归一化",
                )
    except _Invalid as exc:
        return [str(exc)]
    except (KeyError, TypeError, ValueError, IndexError, OverflowError) as exc:
        return [f"D-02 性质输出不可校验：{type(exc).__name__}"]
    return []


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _dev_metrics(rankings: dict[str, list[str]], queries: list[dict]) -> dict[str, Any]:
    """仅重算公开 dev relevant 的指标，不能读取隐藏 test 标注。"""
    recalls: list[float] = []
    mrrs: list[float] = []
    grouped: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for query in queries:
        ids = rankings[str(query["id"])]
        relevant = {str(doc_id) for doc_id in query.get("relevant", [])}
        recall = 100.0 * len(set(ids[:5]) & relevant) / len(relevant) if relevant else 0.0
        mrr = next(
            (1.0 / rank for rank, doc_id in enumerate(ids[:10], 1) if doc_id in relevant), 0.0
        )
        recalls.append(recall)
        mrrs.append(mrr)
        grouped[str(query.get("group", "default"))].append((recall, mrr))
    groups = {
        name: {
            "recall_at_5_percent": _mean([r for r, _ in values]),
            "mrr_at_10": _mean([m for _, m in values]),
        }
        for name, values in grouped.items()
    }
    return {
        "recall_at_5_percent": _mean(recalls),
        "mrr_at_10": _mean(mrrs),
        "group_metrics": {
            "groups": groups,
            "worst_mrr_at_10": min((group["mrr_at_10"] for group in groups.values()), default=0.0),
            "macro_mrr_at_10": _mean([group["mrr_at_10"] for group in groups.values()]),
            "macro_recall_at_5_percent": _mean(
                [group["recall_at_5_percent"] for group in groups.values()]
            ),
        },
    }


def _compare_metrics(actual: Any, expected: Mapping[str, Any], label: str) -> None:
    _require(isinstance(actual, Mapping), f"{label} 必须是指标对象")
    for key, value in expected.items():
        where = f"{label}.{key}"
        if isinstance(value, Mapping):
            if key == "groups":
                _require(
                    isinstance(actual.get(key), Mapping) and set(actual[key]) == set(value),
                    f"{where} 必须覆盖全部开发查询组",
                )
            _compare_metrics(actual.get(key), value, where)
        else:
            observed = _number(actual.get(key))
            _require(
                math.isclose(observed, value, rel_tol=1e-7, abs_tol=1e-8),
                f"{where} 与公开开发排名重算不符：期望 {value:.12g}，实际 {observed:.12g}",
            )


def _check(payload: Mapping[str, Any], result: Mapping[str, Any]) -> None:
    data = payload["data"]
    doc_ids = {str(doc["id"]) for doc in data.get("documents", [])}
    test_queries = data.get("test_queries", [])
    if payload.get("phase") == "rank":
        _require(
            set(result) == {"candidate"}, "rank 阶段只应返回 {candidate: rankings}，不得重新选参"
        )
        ranking_orders(result.get("candidate"), test_queries, doc_ids, "candidate")
        return
    _require(
        {"baseline", "candidate", "details"} <= set(result),
        "run 必须显式返回 baseline、candidate、details；基线模式也须 candidate=null",
    )
    ranking_orders(result.get("baseline"), test_queries, doc_ids, "baseline")
    details = result.get("details")
    if result.get("candidate") is None:
        _require(details == {}, "诚实基线路径 candidate=null 时 details 必须为空对象")
        return
    ranking_orders(result.get("candidate"), test_queries, doc_ids, "candidate")
    _require(isinstance(details, Mapping), "details 必须是对象")
    search = details.get("search")
    grid = payload.get("protocol", {}).get("grid", [])
    _require(
        isinstance(search, list) and len(search) == len(grid),
        f"details.search 必须包含全部 {len(grid)} 组候选",
    )
    _require(
        all(
            isinstance(row, Mapping) and isinstance(row.get("group_metrics"), Mapping)
            for row in search
        ),
        "details.search 每行必须含 group_metrics 对象",
    )
    configs = [_config(row.get("config")) for row in search]
    expected_configs = [_config(config) for config in grid]
    _require(
        Counter(configs) == Counter(expected_configs) and len(set(configs)) == len(configs),
        "details.search 必须唯一覆盖 protocol.grid，不能重复或遗漏配置",
    )
    dev_queries = data.get("dev_queries", [])
    candidates: list[tuple[tuple[float, ...], tuple[float, float, float]]] = []
    for index, (row, config) in enumerate(zip(search, configs, strict=True)):
        rankings = ranking_orders(
            row.get("rankings"), dev_queries, doc_ids, f"search[{index}].rankings"
        )
        metrics = _dev_metrics(rankings, dev_queries)
        _compare_metrics(row, metrics, f"search[{index}]")
        gm = metrics["group_metrics"]
        key = (
            -round(gm["worst_mrr_at_10"], 12),
            -round(gm["macro_mrr_at_10"], 12),
            -round(gm["macro_recall_at_5_percent"], 12),
            config[2],
            config[0],
            config[1],
        )
        candidates.append((key, config))
    _require(bool(candidates), "候选网格不能为空")
    _require(
        _config(details.get("selected")) == min(candidates)[1],
        "selected 不符合重算开发指标及 title_weight,k1,b 完整并列规则",
    )


def check_d02(payload: Mapping[str, Any], result: Mapping[str, Any]) -> list[str]:
    try:
        _check(payload, result)
    except _Invalid as exc:
        return [str(exc)]
    except (KeyError, TypeError, ValueError, IndexError, OverflowError) as exc:
        return [f"D-02 输出结构不可校验：{type(exc).__name__}"]
    return []
