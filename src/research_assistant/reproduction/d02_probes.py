"""Tiny hand-derived D-02 inputs; no BM25 implementation or final test metrics."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from research_assistant.reproduction.d02_validation import RankCheck


@dataclass(frozen=True)
class RankProbe:
    name: str
    payload: Mapping[str, Any]
    checks: Mapping[str, RankCheck]


def build_rank_probes(protocol: Mapping[str, Any]) -> tuple[RankProbe, ...]:
    if protocol.get("version") != "layered-d-v3":
        return ()

    def probe(name, docs, queries, config, checks):
        return RankProbe(
            name,
            {
                "case_id": "D-02",
                "phase": "rank",
                "protocol": dict(protocol),
                "data": {
                    "documents": docs,
                    "dev_queries": [],
                    "test_queries": queries,
                    "selected": config,
                },
            },
            checks,
        )

    def query(qid, text):
        return {"id": qid, "text": text, "group": "property"}

    def config(b=0.0, title_weight=0.0):
        return {"k1": 1.2, "b": b, "title_weight": title_weight}

    equal = [{"id": "a", "title": "", "text": "y"}, {"id": "b", "title": "", "text": "x"}]
    fields = [{"id": "a", "title": "x", "text": ""}, {"id": "b", "title": "", "text": "x"}]
    lengths = [
        {"id": "a", "title": "", "text": "x"},
        {"id": "b", "title": "", "text": "x x x y y y y y y"},
    ]
    # Equal field statistics: repeat ratio 2; title_weight=2 gives title/body ratio 2.
    # For length probe N=2, avg_len=5 and k1=6/5. b=0 => score_b/score_a=11/7;
    # b=1 => score_a/score_b=(55/31)/(55/43)=43/31. No absolute score/IDF needed.
    return (
        probe(
            "d02-repeated-unknown-empty",
            equal,
            [query("repeat", "X x, y"), query("unknown", "unseen123"), query("empty", "")],
            config(),
            {
                "repeat": RankCheck(order=("b", "a"), ratio=("b", "a", 2)),
                "unknown": RankCheck(order=("a", "b"), zero=True),
                "empty": RankCheck(order=("a", "b"), zero=True),
            },
        ),
        probe(
            "d02-title-zero",
            fields,
            [query("title", "x")],
            config(),
            {"title": RankCheck(order=("b", "a"), positive=("b",))},
        ),
        probe(
            "d02-title-two",
            fields,
            [query("title", "x")],
            config(title_weight=2),
            {"title": RankCheck(order=("a", "b"), ratio=("a", "b", 2))},
        ),
        probe(
            "d02-length-zero",
            lengths,
            [query("length", "x")],
            config(),
            {"length": RankCheck(order=("b", "a"), ratio=("b", "a", 11 / 7))},
        ),
        probe(
            "d02-length-one",
            lengths,
            [query("length", "x")],
            config(b=1),
            {"length": RankCheck(order=("a", "b"), ratio=("a", "b", 43 / 31))},
        ),
    )
