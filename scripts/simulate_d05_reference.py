"""D-05 调度参考模拟器：逐事件比对模型生成源码的输出。

为什么需要：`reproduction/invariants.py` 目前对 D-05 只做**结构性**检查（任务守恒、
start>=deadline 不得派发、end>horizon、served_ms 覆盖零服务流）。但总纲 §6.5 里最容易被
写错、也最影响「实验效果 10 分」的是**时序语义**：

- 同一时刻的固定次序：释放已完成服务端 → 纳入到达任务 → 丢弃 deadline<=now 的等待任务 → 派发
- 公平策略取 (累计已分配服务毫秒/flow_weight, arrival_ms, id) 最小者
- **成功派发立即预留服务量**，不能等服务完成才累计
- 同服务端换流付 setup，首次无 setup；空闲不清除上一流
- 选中后不因 setup 期间新到达而换任务
- 先判 start>=deadline，再判 end>horizon；end==horizon 可完成
- 拒绝任务不回队、不改变上一流，当前空闲服务端继续尝试；成功后转下一服务端
- 无可派任务时推进到未来事件，不能用旧 free_at 原地循环

这些都无法从字段一致性推出来，只能靠一个独立实现逐事件比对。

**这是校验器，不是提交物。** 它位于 scripts/，已被 .dockerignore 排除、不进镜像，
也绝不会被当作或喂给 D 类的提交源码——正式源码必须由本题会话内的 Gateway 生成
（d-model-source-v1）。函数名刻意不叫 run_experiment，以免与提交合同混淆。

用法：
    uv run python scripts/simulate_d05_reference.py
    uv run python scripts/simulate_d05_reference.py --source work/generated/D-05.py
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
MATERIAL_ROOT = REPO_ROOT.parent / "赛题信息" / "research-experiments-20261020-source-v1"
PUBLIC_REQUESTS = MATERIAL_ROOT / "公开样例请求"
DEFAULT_SOURCE = REPO_ROOT / "work" / "generated" / "D-05.py"

FIFO = "fifo"
FAIR = "fair"


@dataclass(frozen=True)
class Job:
    id: str
    flow: str
    arrival_ms: float
    service_ms: float
    deadline_ms: float


@dataclass(frozen=True)
class SimulationResult:
    trace: tuple[dict[str, Any], ...]
    dropped: tuple[dict[str, Any], ...]
    served_ms: dict[str, float]


def _jobs(data: Mapping[str, Any]) -> list[Job]:
    return [
        Job(
            id=str(j["id"]),
            flow=str(j["flow"]),
            arrival_ms=float(j["arrival_ms"]),
            service_ms=float(j["service_ms"]),
            deadline_ms=float(j["deadline_ms"]),
        )
        for j in data["jobs"]
    ]


def simulate(data: Mapping[str, Any], policy: str) -> SimulationResult:
    """按总纲 §6.5 独立实现 FIFO 与加权公平调度。"""
    jobs = _jobs(data)
    weights = {str(k): float(v) for k, v in (data.get("flow_weights") or {}).items()}
    servers_count = int(data["servers"])
    setup_ms = float(data.get("setup_ms") or 0)
    horizon = float(data["horizon_ms"])

    # 到达顺序 (arrival_ms, id)
    pending = sorted(jobs, key=lambda j: (j.arrival_ms, j.id))
    waiting: list[Job] = []
    free_at = [0.0] * servers_count
    last_flow: list[str | None] = [None] * servers_count
    allocated: dict[str, float] = {flow: 0.0 for flow in weights}
    served: dict[str, float] = {flow: 0.0 for flow in weights}
    trace: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []

    now = min([j.arrival_ms for j in pending] + [0.0])

    def order(pool: Sequence[Job]) -> list[Job]:
        if policy == FIFO:
            return sorted(pool, key=lambda j: (j.arrival_ms, j.id))

        def key(job: Job) -> tuple[float, float, str]:
            weight = weights.get(job.flow, 0.0)
            # 权重为 0 或缺失时不能除零；用极大值把它排到最后而不是崩溃
            share = allocated.get(job.flow, 0.0) / weight if weight > 0 else float("inf")
            return (share, job.arrival_ms, job.id)

        return sorted(pool, key=key)

    guard = 0
    while (pending or waiting) and guard < 100000:
        guard += 1

        # 1) 释放已完成的服务端：由 free_at <= now 隐式表达
        # 2) 纳入已到达的任务
        while pending and pending[0].arrival_ms <= now:
            waiting.append(pending.pop(0))

        # 3) 丢弃 deadline <= now 的等待任务
        kept: list[Job] = []
        for job in waiting:
            if job.deadline_ms <= now:
                dropped.append({"id": job.id, "reason": "deadline_expired"})
            else:
                kept.append(job)
        waiting = kept

        # 4) 派发：空闲服务端按编号升序
        for index in range(servers_count):
            if free_at[index] > now:
                continue
            # 选中后不因 setup 期间新到达而换任务：本轮候选在此刻固定
            for job in order(waiting):
                previous = last_flow[index]
                setup = 0.0 if previous is None or previous == job.flow else setup_ms
                start = now + setup
                end = start + job.service_ms
                waiting = [w for w in waiting if w.id != job.id]
                # 先判 start>=deadline，再判 end>horizon
                if start >= job.deadline_ms:
                    # 拒绝任务不回队、不改变上一流；当前空闲服务端继续尝试下一个
                    dropped.append({"id": job.id, "reason": "start_not_before_deadline"})
                    continue
                if end > horizon:
                    dropped.append({"id": job.id, "reason": "beyond_horizon"})
                    continue
                trace.append(
                    {
                        "id": job.id,
                        "flow": job.flow,
                        "arrival_ms": job.arrival_ms,
                        "service_ms": job.service_ms,
                        "deadline_ms": job.deadline_ms,
                        "server": index,
                        "dispatch_ms": now,
                        "setup_ms": setup,
                        "start_ms": start,
                        "end_ms": end,
                        "wait_ms": start - job.arrival_ms,
                    }
                )
                last_flow[index] = job.flow
                free_at[index] = end
                # 成功派发**立即预留**服务量，不等完成
                allocated[job.flow] = allocated.get(job.flow, 0.0) + job.service_ms
                served[job.flow] = served.get(job.flow, 0.0) + job.service_ms
                break  # 成功后转下一服务端

        # 推进到严格大于 now 的下一个事件；不能用旧 free_at 原地循环
        futures = [j.arrival_ms for j in pending if j.arrival_ms > now]
        futures += [t for t in free_at if t > now]
        if futures:
            now = min(futures)
            continue
        if waiting:
            for job in waiting:
                dropped.append({"id": job.id, "reason": "no_feasible_slot"})
            waiting = []
        if not pending:
            break

    trace.sort(key=lambda t: (t["dispatch_ms"], t["server"]))
    dropped.sort(key=lambda d: d["id"])
    return SimulationResult(trace=tuple(trace), dropped=tuple(dropped), served_ms=served)


def _index(items: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {str(item["id"]): item for item in items if isinstance(item, Mapping) and "id" in item}


def compare(label: str, expected: SimulationResult, actual: Any) -> list[str]:
    problems: list[str] = []
    if actual is None:
        # candidate 允许诚实为 null；baseline 不允许
        if label == "baseline":
            problems.append("baseline 为 null，但 FIFO 基线必须给出真实轨迹")
        return problems
    if not isinstance(actual, Mapping):
        return [f"{label} 不是对象"]

    exp_trace = _index(expected.trace)
    act_trace = _index(actual.get("trace") or [])
    exp_drop = {str(d["id"]) for d in expected.dropped}
    act_drop = {str(d["id"]) for d in (actual.get("dropped") or [])}

    if set(exp_trace) != set(act_trace):
        problems.append(
            f"{label} 完成的 task集合不符：只在参考中 {sorted(set(exp_trace) - set(act_trace))}，"
            f"只在输出中 {sorted(set(act_trace) - set(exp_trace))}"
        )
    if exp_drop != act_drop:
        problems.append(
            f"{label} 被丢弃的 task集合不符：只在参考中 {sorted(exp_drop - act_drop)}，"
            f"只在输出中 {sorted(act_drop - exp_drop)}"
        )

    for task_id, want in exp_trace.items():
        got = act_trace.get(task_id)
        if got is None:
            continue
        for field in ("server", "dispatch_ms", "setup_ms", "start_ms", "end_ms", "wait_ms"):
            if field not in got:
                problems.append(f"{label}/{task_id} 缺字段 {field}")
                continue
            if abs(float(got[field]) - float(want[field])) > 1e-9:
                problems.append(f"{label}/{task_id}.{field} 期望 {want[field]}，实际 {got[field]}")

    exp_served = expected.served_ms
    act_served = actual.get("served_ms")
    if isinstance(act_served, Mapping):
        for flow, value in exp_served.items():
            got_value = act_served.get(flow)
            if got_value is None:
                problems.append(f"{label}.served_ms 缺流 {flow}")
            elif abs(float(got_value) - float(value)) > 1e-9:
                problems.append(f"{label}.served_ms[{flow}] 期望 {value}，实际 {got_value}")
    else:
        problems.append(f"{label}.served_ms 不是对象")

    expected_order = [(t["dispatch_ms"], t["server"]) for t in expected.trace]
    actual_order = [
        (t.get("dispatch_ms"), t.get("server"))
        for t in (actual.get("trace") or [])
        if isinstance(t, Mapping)
    ]
    if actual_order != sorted(actual_order, key=lambda x: (float(x[0]), int(x[1]))):
        problems.append(f"{label}.trace 未按 (dispatch_ms, server) 排序")
    del expected_order
    return problems


def load_module(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("d05_impl", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample_requests() -> list[Path]:
    found = [PUBLIC_REQUESTS / "D-05.json"]
    extra = PUBLIC_REQUESTS / "D-05"
    if extra.is_dir():
        found.extend(sorted(extra.glob("*.json")))
    return [p for p in found if p.is_file()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    args = parser.parse_args()

    if not args.source.is_file():
        raise SystemExit(
            f"找不到生成源码 {args.source}；先跑 scripts/validate_d_offline.py --case D-05"
        )
    module = load_module(args.source)

    total = 0
    for path in sample_requests():
        message = json.loads(path.read_text(encoding="utf-8"))
        payload = message["payload"]
        if payload.get("phase") != "run":
            print(f"[SKIP] {path.name}（phase={payload.get('phase')}，非 run 阶段）")
            continue
        data = payload["data"]
        result = module.run_experiment(payload)

        problems: list[str] = []
        problems += compare("baseline", simulate(data, FIFO), result.get("baseline"))
        problems += compare("candidate", simulate(data, FAIR), result.get("candidate"))

        if problems:
            total += len(problems)
            print(f"[FAIL] {path.name}: {len(problems)} 处与参考模拟不一致")
            for line in problems[:12]:
                print(f"        ✗ {line}")
            if len(problems) > 12:
                print(f"        …另有 {len(problems) - 12} 处")
        else:
            base_n = len((result.get("baseline") or {}).get("trace") or [])
            cand = result.get("candidate")
            cand_n = len((cand or {}).get("trace") or []) if isinstance(cand, Mapping) else "null"
            print(
                f"[OK  ] {path.name}: baseline {base_n} 条轨迹、"
                f"candidate {cand_n} 条，与参考模拟逐事件一致"
            )

    print(f"\n合计 {total} 处不一致。")
    print("注意：参考模拟器是开发期校验器，不是提交源码，也不进镜像。")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
