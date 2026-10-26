"""D 类算法不变量校验（验收 D04）。

`validate_d_offline.py` 只证明「生成源码能跑通、输出键名对」；本脚本进一步证明
**公开输入输出可独立复核的不变量**（不是完整算法正确性证明）。这直接决定 D 类 40 分里的「指标计算 6 +
实验效果 10」：管线跑通但算法错，那 16 分照样是 0。

覆盖的不变量（总纲 §6.3–§6.5）：
- D-01：词表/类别字典序；IDF=1+ln((1+N)/(1+df)) 且**只用当前条件训练集**（训练泄漏检查）；
  权重形状为 类别数×(词表大小+1)、最后一列 bias；**用权重必须能重建 candidate 预测**；
  基线为多数类、并列取字典序最小；输出长度与 data.texts 一致；details 直接含
  classes/vocabulary/idf/weights，不得多嵌 model。
- D-02：details.search 恰好 27 行且与 protocol.grid 一一对应；每行含 group_metrics 的
  groups/worst_mrr_at_10/macro_mrr_at_10/macro_recall_at_5_percent；默认基线不在网格内；
  **按「最差组 MRR → 组等权宏 MRR → 组等权宏 Recall@5，各先四舍五入到 12 位，
  仍并列按 title_weight,k1,b 升序」重算选参，必须等于 details.selected**；
  排名按 (-score,id) 稳定排序且包含全部文档。
- D-05：每个任务在 trace 与 dropped 中**恰好出现一次**（任务守恒）；trace 含
  server/dispatch_ms/setup_ms/start_ms/end_ms/wait_ms 与原字段，按派发时刻/服务端排序；
  dropped 只含 id/reason 且按 id 排序；served_ms 覆盖零服务流；start>=deadline 的任务
  不得进 trace；end>horizon 不得出现而 end==horizon 允许；wait_ms==start-arrival；
  服务时长守恒（setup 不计入服务）。
  注：与参考模拟器逐事件比对尚未实现，「成功派发立即预留服务量」这条目前只由
  trace 的字段一致性间接约束，属已知缺口。

本脚本只在开发期运行，**不进镜像**（.dockerignore 已排除 scripts/）；
它是校验器而非提交物，因此不构成「镜像预置完整算法」。
生成源码请放在 work/generated/<case>.py（work/ 已被 gitignore）。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from research_assistant.reproduction.d02_validation import check_d02 as validate_d02

REPO_ROOT = Path(__file__).resolve().parents[1]
MATERIAL_ROOT = REPO_ROOT.parent / "赛题信息" / "research-experiments-20261020-source-v1"
PUBLIC_REQUESTS = MATERIAL_ROOT / "公开样例请求"
TOKEN = re.compile(r"[a-z0-9]+")
ROUND_DIGITS = 12


@dataclass
class Report:
    case_id: str
    request: str
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def check(self, ok: bool, label: str, detail: str = "") -> None:
        if ok:
            self.passed.append(label)
        else:
            self.failed.append(f"{label}" + (f" —— {detail}" if detail else ""))

    @property
    def ok(self) -> bool:
        return not self.failed


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample_requests(case_id: str) -> list[Path]:
    found = [PUBLIC_REQUESTS / f"{case_id}.json"]
    extra = PUBLIC_REQUESTS / case_id
    if extra.is_dir():
        found.extend(sorted(extra.glob("*.json")))
    return [p for p in found if p.is_file()]


def run_case(module: Any, request_path: Path) -> dict[str, Any]:
    message = json.loads(request_path.read_text(encoding="utf-8"))
    return module.run_experiment(message["payload"])


# ---------------------------------------------------------------- D-01


def tokenize(text: str) -> list[str]:
    return TOKEN.findall(text.lower())


def reference_idf(train_texts: Sequence[str]) -> tuple[list[str], dict[str, float]]:
    """按官方公式独立重算词表与 IDF：词表字典序，IDF 只用训练集。"""
    df: Counter[str] = Counter()
    vocab: set[str] = set()
    for text in train_texts:
        tokens = set(tokenize(text))
        vocab |= tokens
        df.update(tokens)
    ordered = sorted(vocab)
    n = len(train_texts)
    idf = {t: 1.0 + math.log((1.0 + n) / (1.0 + df[t])) for t in ordered}
    return ordered, idf


def features(text: str, vocab: Sequence[str], idf: Mapping[str, float]) -> list[float]:
    counts = Counter(tokenize(text))
    vector = [float(counts.get(term, 0)) * idf.get(term, 0.0) for term in vocab]
    norm = math.sqrt(sum(v * v for v in vector))
    if norm > 0:
        vector = [v / norm for v in vector]
    return [*vector, 1.0]  # 追加 bias=1


def argmax_lex(scores: Sequence[float], classes: Sequence[str]) -> str:
    """并列取字典序最小类；classes 已按字典序排列，故取第一个最大值。"""
    best = max(scores)
    for index, score in enumerate(scores):
        if score == best:
            return classes[index]
    return classes[0]


def check_d01(result: Mapping[str, Any], data: Mapping[str, Any], report: Report) -> None:
    train = data["train"]
    texts = data["texts"]
    train_texts = [str(item["text"]) for item in train]
    train_labels = [str(item["label"]) for item in train]

    report.check(
        set(result) >= {"baseline", "candidate", "details"},
        "D01 顶层键含 baseline/candidate/details",
        f"实际 {sorted(result)}",
    )
    baseline = result.get("baseline")
    candidate = result.get("candidate")
    details = result.get("details")

    if not isinstance(baseline, list) or not isinstance(candidate, list):
        report.check(False, "D01 baseline/candidate 为标签列表", f"类型 {type(candidate).__name__}")
        return
    report.check(
        len(baseline) == len(texts) and len(candidate) == len(texts),
        "D01 输出长度与 data.texts 一致",
        f"baseline={len(baseline)} candidate={len(candidate)} texts={len(texts)}",
    )

    counts = Counter(train_labels)
    top = max(counts.values())
    majority = sorted(label for label, c in counts.items() if c == top)[0]
    report.check(
        all(label == majority for label in baseline),
        "D01 基线为多数类且并列取字典序最小",
        f"期望全为 {majority!r}，实际出现 {sorted(set(baseline))}",
    )

    if candidate is None:
        report.check(True, "D01 candidate=null（诚实基线路径）")
        report.check(
            details == {}, "D01 candidate 为 null 时 details 为空对象", f"实际 {details!r}"
        )
        return

    if not isinstance(details, Mapping):
        report.check(False, "D01 details 为对象", f"类型 {type(details).__name__}")
        return
    report.check(
        "model" not in details,
        "D01 details 直接含字段，未多嵌 model",
        f"details 键 {sorted(details)}",
    )
    for key in ("classes", "vocabulary", "idf", "weights"):
        report.check(key in details, f"D01 details 含 {key}", f"实际键 {sorted(details)}")
    if not all(k in details for k in ("classes", "vocabulary", "idf", "weights")):
        return

    classes = list(details["classes"])
    vocabulary = list(details["vocabulary"])
    weights = details["weights"]
    report.check(classes == sorted(classes), "D01 classes 字典序", f"{classes}")
    report.check(vocabulary == sorted(vocabulary), "D01 vocabulary 字典序")

    ref_vocab, ref_idf = reference_idf(train_texts)
    report.check(
        vocabulary == ref_vocab,
        "D01 词表只由当前条件训练集拟合并按字典序",
        f"模型 {len(vocabulary)} 词 vs 参考 {len(ref_vocab)} 词",
    )

    idf = details["idf"]
    if isinstance(idf, Mapping):
        idf_items = {str(k): float(v) for k, v in idf.items()}
    else:
        idf_items = dict(zip(vocabulary, (float(v) for v in idf), strict=False))
    mismatches = [
        term
        for term in vocabulary
        if term in idf_items and not math.isclose(idf_items[term], ref_idf[term], rel_tol=1e-9)
    ]
    report.check(
        not mismatches,
        "D01 IDF 等于 1+ln((1+N)/(1+df)) 且未泄漏测试文本",
        f"{len(mismatches)} 个词不符，例如 {mismatches[:3]}",
    )

    report.check(
        isinstance(weights, list)
        and len(weights) == len(classes)
        and all(len(row) == len(vocabulary) + 1 for row in weights),
        "D01 权重形状为 类别数×(词表大小+1)，最后一列 bias",
        f"classes={len(classes)} vocab={len(vocabulary)} "
        f"rows={len(weights) if isinstance(weights, list) else 'n/a'}",
    )
    if not isinstance(weights, list) or not weights:
        return

    rebuilt: list[str] = []
    for text in texts:
        vector = features(str(text), vocabulary, idf_items)
        scores = [sum(w * x for w, x in zip(row, vector, strict=False)) for row in weights]
        rebuilt.append(argmax_lex(scores, classes))
    diffs = [
        (i, candidate[i], rebuilt[i])
        for i in range(min(len(candidate), len(rebuilt)))
        if candidate[i] != rebuilt[i]
    ]
    report.check(
        not diffs and len(rebuilt) == len(candidate),
        "D01 用权重可重建 candidate 预测（含并列取字典序最小）",
        f"{len(diffs)} 处不一致，例如 {diffs[:3]}",
    )
    report.check(
        set(candidate) <= set(classes),
        "D01 candidate 标签都在 classes 内",
        f"越界标签 {sorted(set(candidate) - set(classes))[:3]}",
    )


# ---------------------------------------------------------------- D-02


def check_d02(
    result: Mapping[str, Any],
    data: Mapping[str, Any],
    protocol: Mapping[str, Any],
    report: Report,
) -> None:
    problems = validate_d02({"phase": "run", "data": data, "protocol": protocol}, result)
    report.check(not problems, "D02 查询覆盖、开发指标独立重算与完整选参规则", "; ".join(problems))


# ---------------------------------------------------------------- D-05


def check_d05(result: Mapping[str, Any], data: Mapping[str, Any], report: Report) -> None:
    jobs = list(data["jobs"])
    weights = data.get("flow_weights") or {}
    horizon = float(data["horizon_ms"])
    job_ids = {str(j["id"]) for j in jobs}
    by_id = {str(j["id"]): j for j in jobs}

    for name in ("baseline", "candidate"):
        run = result.get(name)
        if run is None:
            report.check(name == "candidate", f"D05 {name} 存在", "为 null（诚实基线路径）")
            continue
        if not isinstance(run, Mapping):
            report.check(False, f"D05 {name} 为对象", f"类型 {type(run).__name__}")
            continue

        trace = run.get("trace") or []
        dropped = run.get("dropped") or []
        served_ms = run.get("served_ms")

        seen = [str(t["id"]) for t in trace if isinstance(t, Mapping) and "id" in t]
        dropped_ids = [str(d["id"]) for d in dropped if isinstance(d, Mapping) and "id" in d]
        all_seen = seen + dropped_ids
        report.check(
            len(all_seen) == len(set(all_seen)) and set(all_seen) == job_ids,
            f"D05 {name} 任务守恒：每个任务恰好进入 trace 或 dropped 一次",
            f"重复 {sorted(Counter(all_seen).items() and [k for k, v in Counter(all_seen).items() if v > 1])[:3]}，"
            f"缺失 {sorted(job_ids - set(all_seen))[:3]}，多余 {sorted(set(all_seen) - job_ids)[:3]}",
        )

        required = {"server", "dispatch_ms", "setup_ms", "start_ms", "end_ms", "wait_ms"}
        missing = [
            str(t.get("id")) for t in trace if isinstance(t, Mapping) and not required <= set(t)
        ]
        report.check(
            not missing,
            f"D05 {name} trace 含 server/dispatch_ms/setup_ms/start_ms/end_ms/wait_ms",
            f"{len(missing)} 条缺字段，例如 {missing[:3]}",
        )
        original = {"id", "flow", "arrival_ms", "service_ms", "deadline_ms"}
        missing_original = [
            str(t.get("id")) for t in trace if isinstance(t, Mapping) and not original <= set(t)
        ]
        report.check(
            not missing_original,
            f"D05 {name} trace 保留任务原字段",
            f"{len(missing_original)} 条缺原字段",
        )

        order = [(t.get("dispatch_ms"), t.get("server")) for t in trace if isinstance(t, Mapping)]
        report.check(
            order == sorted(order),
            f"D05 {name} trace 按派发时刻/服务端排序",
        )
        report.check(
            dropped_ids == sorted(dropped_ids),
            f"D05 {name} dropped 按 id 排序",
        )
        extra_keys = [
            sorted(set(d) - {"id", "reason"})
            for d in dropped
            if isinstance(d, Mapping) and set(d) - {"id", "reason"}
        ]
        report.check(
            not extra_keys,
            f"D05 {name} dropped 只含 id/reason",
            f"多余键 {extra_keys[:2]}",
        )

        if isinstance(served_ms, Mapping):
            missing_flows = sorted(set(weights) - set(served_ms))
            report.check(
                not missing_flows,
                f"D05 {name} served_ms 覆盖全部流（含零服务流）",
                f"缺失 {missing_flows}",
            )
        else:
            report.check(False, f"D05 {name} served_ms 为对象", f"类型 {type(served_ms).__name__}")

        late = [
            str(t["id"])
            for t in trace
            if isinstance(t, Mapping)
            and float(t["start_ms"]) >= float(by_id[str(t["id"])]["deadline_ms"])
        ]
        report.check(not late, f"D05 {name} 无 start>=deadline 的任务被派发", f"{late[:3]}")
        over = [
            str(t["id"]) for t in trace if isinstance(t, Mapping) and float(t["end_ms"]) > horizon
        ]
        report.check(not over, f"D05 {name} 无 end>horizon", f"{over[:3]}")
        at_horizon = [
            str(t["id"]) for t in trace if isinstance(t, Mapping) and float(t["end_ms"]) == horizon
        ]
        report.check(
            True,
            f"D05 {name} end==horizon 视为可完成（本例 {len(at_horizon)} 条）",
        )

        bad_wait = [
            str(t["id"])
            for t in trace
            if isinstance(t, Mapping)
            and not math.isclose(
                float(t["wait_ms"]),
                float(t["start_ms"]) - float(by_id[str(t["id"])]["arrival_ms"]),
                rel_tol=1e-9,
                abs_tol=1e-9,
            )
        ]
        report.check(
            not bad_wait, f"D05 {name} wait_ms == start_ms - arrival_ms", f"{bad_wait[:3]}"
        )
        bad_service = [
            str(t["id"])
            for t in trace
            if isinstance(t, Mapping)
            and not math.isclose(
                float(t["end_ms"]) - float(t["start_ms"]) - float(t.get("setup_ms", 0.0)) * 0,
                float(by_id[str(t["id"])]["service_ms"]),
                rel_tol=1e-9,
                abs_tol=1e-9,
            )
            and not math.isclose(
                float(t["end_ms"]) - float(t["start_ms"]),
                float(by_id[str(t["id"])]["service_ms"]),
                rel_tol=1e-9,
                abs_tol=1e-9,
            )
        ]
        report.check(
            not bad_service,
            f"D05 {name} end-start 等于服务时长（setup 不计入服务）",
            f"{bad_service[:3]}",
        )


# ---------------------------------------------------------------- 驱动


CHECKS = {
    "D-01": lambda result, data, protocol, report: check_d01(result, data, report),
    "D-02": lambda result, data, protocol, report: check_d02(result, data, protocol, report),
    "D-05": lambda result, data, protocol, report: check_d05(result, data, report),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=REPO_ROOT / "work" / "generated",
        help="存放已生成源码的目录（默认 work/generated，已被 gitignore）",
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="源码缺失时用真实网关现场生成（需要 .env.local 与网络）",
    )
    args = parser.parse_args()
    cases = args.cases or ["D-01", "D-02", "D-05"]

    total_failed = 0
    for case_id in cases:
        source = args.source_dir / f"{case_id}.py"
        if not source.is_file():
            if not args.generate:
                print(
                    f"[SKIP] {case_id}：找不到 {source}（先跑 validate_d_offline.py 或加 --generate）"
                )
                continue
            raise SystemExit("--generate 需要走 validate_d_offline.py 的生产路径，本脚本只做校验")

        module = load_module(source, f"impl_{case_id.replace('-', '_')}")
        requests = sample_requests(case_id)
        print(f"\n===== {case_id}：{len(requests)} 个公开样例请求 =====")
        for request_path in requests:
            report = Report(case_id=case_id, request=request_path.name)
            message = json.loads(request_path.read_text(encoding="utf-8"))
            payload = message["payload"]
            try:
                result = module.run_experiment(payload)
            except Exception as exc:
                report.check(False, "run_experiment 正常返回", f"{exc.__class__.__name__}: {exc}")
                _print(report)
                total_failed += 1
                continue
            if not isinstance(result, Mapping):
                report.check(False, "返回值为对象", f"类型 {type(result).__name__}")
            elif case_id == "D-02":
                problems = validate_d02(payload, result)
                report.check(
                    not problems,
                    "D02 查询覆盖、开发指标独立重算与完整选参规则",
                    "; ".join(problems),
                )
            else:
                CHECKS[case_id](
                    result, payload.get("data") or {}, payload.get("protocol") or {}, report
                )
            _print(report)
            total_failed += len(report.failed)

    print(f"\n合计 {total_failed} 项不变量未通过。")
    print("注意：这是开发期算法自检，不是平台来源准入，也不是可信执行结果。")
    return 1 if total_failed else 0


def _print(report: Report) -> None:
    mark = "OK  " if report.ok else "FAIL"
    print(f"[{mark}] {report.request}: {len(report.passed)} 项通过, {len(report.failed)} 项未通过")
    for line in report.failed:
        print(f"        ✗ {line}")


if __name__ == "__main__":
    raise SystemExit(main())
