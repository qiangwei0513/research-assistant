"""D 类（Reproduction）handler：生成 → 来源保存 → 正式 helper 执行 → 如实返回。

流程严格遵守 d-model-source-v1 与 layered-d-v3：
1. 读取平台挂载的**当前题**材料（method/protocol/sample），缺失即 `materials_missing`
2. 在**本轮本题会话内**用赛事 Gateway 生成自包含源码；提取失败只允许让同一会话模型
   重新返回完整版本，**不得本地补码、不得拼多个回复、不得应用 diff**
3. 归一化后原样落盘到本队 work 目录，记录 SHA-256
4. 调用平台挂载的 `run_experiment.py --case <id> --implementation <绝对路径>`
5. helper 自己写结果文件与图片；外层只回读、核验、原样返回，**绝不覆盖**
6. 任何环节失败都返回合法的失败答卷（三项完整指标、observed 全 null），不伪造指标或凭证

镜像内不预置任何完整算法；本模块只有编排逻辑。
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from research_assistant.contracts.results import (
    Metric,
    placeholder_metrics,
    reproduction_result,
)
from research_assistant.contracts.task import Task
from research_assistant.errors import BudgetExhausted, GatewayError, TaskContractError
from research_assistant.reproduction.client import (
    DEFAULT_HELPER_PATH,
    ExperimentClient,
    ExperimentOutcome,
    SubprocessExperimentClient,
)
from research_assistant.reproduction.materials import (
    GENERATION_SYSTEM_PROMPT,
    CaseMaterials,
    build_generation_prompt,
    load_case_materials,
)
from research_assistant.reproduction.selftest import (
    DEFAULT_SELF_TEST_TIMEOUT,
    build_self_test_cases,
    run_self_test,
)
from research_assistant.reproduction.source import (
    SourceExtractionError,
    save_source,
)
from research_assistant.runtime.gateway import Message
from research_assistant.runtime.persistence import (
    PersistedResult,
    write_text_verified,
)

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查，避免与 context 循环导入
    from research_assistant.runtime.context import SolveContext

STAGE_GENERATE = "reproduction.generate_source"
STAGE_REGENERATE = "reproduction.regenerate_source"
MIN_GENERATION_SECONDS = 15.0
# helper 的 RPC 上限是 600 秒，但整题只有 180 秒：正式运行必须拿到足够预算，
# 因此生成阶段要为它预留时间，而不是把 180 秒全花在模型上。
RESERVE_RUN_SECONDS = 55.0
RESERVE_SELF_TEST_SECONDS = 12.0
RESERVE_PERSIST_SECONDS = 5.0
MIN_RUN_SECONDS = 20.0
MAX_GENERATION_ATTEMPTS = 3
IMPLEMENTATION_FILENAME = "implementation.py"
EXPERIMENT_ROOT_ENV = "RESEARCH_EXPERIMENT_ROOT"
HELPER_PATH_ENV = "RESEARCH_EXPERIMENT_HELPER"


def _failure_answer(
    task: Task,
    context: SolveContext,
    status: str,
    reason: str | None,
    *,
    figures: list[Mapping[str, Any]] | None = None,
) -> Mapping[str, Any]:
    """合法的失败答卷：三项完整指标、observed 全 null、只列实际存在的图。"""
    context.log.warning(
        "reproduction.failed",
        case_id=task.case_id,
        status=status,
        reason=(reason or "")[:300],
    )
    metrics: tuple[Metric, ...] = placeholder_metrics(task.input)
    if len(metrics) != 3:
        context.log.error(
            "reproduction.metric_spec_unparsable",
            case_id=task.case_id,
            parsed=len(metrics),
        )
    return reproduction_result(task.case_id, status=status, metrics=metrics, figures=figures or [])


def _generate_source(
    task: Task,
    context: SolveContext,
    materials: CaseMaterials,
    destination: Path,
    experiment_root: Path,
) -> tuple[str, str, bool] | None:
    """在本题会话内生成源码、自测、必要时让模型完整重生成。

    返回 (源码, sha256, 自测是否通过)；预算不足或始终拿不到可用源码时返回 None。

    自测不可省：官方 AST 检查只验语法与入口函数名，`re.findall` 而没 `import re`
    这类错误能顺利通过来源准入，却在正式执行时 NameError；而 helper 一执行就锁源，
    锁源后不得更换源码，整题 40 分会直接归零。
    """
    prompt = build_generation_prompt(task.input, materials)
    messages = [
        Message(role="system", content=GENERATION_SYSTEM_PROMPT),
        Message(role="user", content=prompt),
    ]
    cases = build_self_test_cases(materials, experiment_root)
    best: tuple[str, str] | None = None
    last_error = ""

    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        stage = STAGE_GENERATE if attempt == 1 else STAGE_REGENERATE
        reserve = RESERVE_RUN_SECONDS + RESERVE_PERSIST_SECONDS + RESERVE_SELF_TEST_SECONDS
        if context.deadline.remaining() - reserve < MIN_GENERATION_SECONDS:
            context.log.warning(
                "reproduction.generation_skipped_budget",
                case_id=task.case_id,
                attempt=attempt,
                remaining=round(context.deadline.remaining(), 2),
            )
            break
        generation_context = replace(
            context,
            deadline=replace(
                context.deadline, budget_seconds=context.deadline.budget_seconds - reserve
            ),
        )
        try:
            response = context.gateway.complete(messages, context=generation_context, stage=stage)
        except (GatewayError, BudgetExhausted) as exc:
            last_error = f"generation failed: {type(exc).__name__}"
            context.log.warning(
                "reproduction.generation_failed", attempt=attempt, reason=last_error
            )
            # 保留已完整取得的源码；网络失败不能清除它，也不再耗预算盲目重试。
            break
        try:
            # 提取工具接收的是**完整响应对象**，不是单独的 message.content
            source, digest = save_source(response.raw, destination)
        except SourceExtractionError as exc:
            last_error = f"来源格式检查未通过：{exc}"
            context.log.warning(
                "reproduction.source_rejected",
                case_id=task.case_id,
                attempt=attempt,
                finish_reason=response.finish_reason,
                reason=last_error[:200],
            )
            messages.append(Message(role="assistant", content=response.content))
            messages.append(Message(role="user", content=_regeneration_request(last_error)))
            continue

        best = (source, digest)
        result = run_self_test(
            destination,
            cases,
            work_dir=context.work_dir,
            timeout=min(
                DEFAULT_SELF_TEST_TIMEOUT,
                RESERVE_SELF_TEST_SECONDS,
                max(
                    0.0,
                    context.deadline.remaining() - RESERVE_RUN_SECONDS - RESERVE_PERSIST_SECONDS,
                ),
            ),
            log=context.log,
        )
        if result.ok:
            context.log.info(
                "reproduction.source_saved",
                case_id=task.case_id,
                attempt=attempt,
                source_sha256=digest,
                source_bytes=len(source.encode("utf-8")),
                self_test_passed=True,
                self_test_cases=result.case_count,
            )
            return source, digest, True

        last_error = f"隔离自测失败：{result.error or '未知原因'}"
        context.log.warning(
            "reproduction.self_test_failed",
            case_id=task.case_id,
            attempt=attempt,
            source_sha256=digest,
            passed=result.passed,
            cases=result.case_count,
            reason=(result.error or "")[:300],
        )
        # 只能让同一会话模型重新返回完整版本，绝不本地补码或打补丁
        messages.append(Message(role="assistant", content=response.content))
        messages.append(Message(role="user", content=_regeneration_request(last_error)))

    if best is None:
        context.log.error(
            "reproduction.source_unavailable",
            case_id=task.case_id,
            attempts=MAX_GENERATION_ATTEMPTS,
            last_reason=last_error[:200],
        )
        return None
    source, digest = best
    context.log.warning(
        "reproduction.source_used_despite_failed_self_test",
        case_id=task.case_id,
        source_sha256=digest,
        reason=last_error[:300],
    )
    return source, digest, False


def _regeneration_request(reason: str) -> str:
    return (
        f"上一次回复未通过检查：{reason}\n"
        "请重新返回**一份完整的** Python 实现，放在单个 ```python 围栏内。"
        "必须自包含：所有用到的标准库都要在文件顶部自己 import（例如用到 re 就要 import re）；"
        "不得依赖任何第三方库；不要输出 diff、不要输出多份实现、不要输出解释文字。"
    )


def _save_diagnostics(context: SolveContext, outcome: ExperimentOutcome) -> None:
    """先把 stdout/stderr 落盘：外层解析出错时必须保留真实产物与原始输出。"""
    work = context.work_dir
    work.mkdir(parents=True, exist_ok=True)
    write_text_verified(work / "run_stdout.txt", outcome.stdout)
    write_text_verified(work / "run_stderr.txt", outcome.stderr)
    write_text_verified(
        work / "run_meta.json",
        json.dumps(
            {
                "returncode": outcome.returncode,
                "timed_out": outcome.timed_out,
                "result_file_present": outcome.produced_result_file,
                "submission_parsed": outcome.submission is not None,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )


def _coerce_metrics(raw: Any, case_id: str) -> tuple[Metric, ...]:
    if not isinstance(raw, list) or len(raw) != 3:
        raise TaskContractError(
            f"helper submission must carry exactly three metrics, got "
            f"{len(raw) if isinstance(raw, list) else type(raw).__name__}",
            case_id=case_id,
        )
    metrics: list[Metric] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise TaskContractError(f"metrics[{index}] must be an object", case_id=case_id)
        metrics.append(
            Metric(
                name=str(item.get("name", "")),
                unit=str(item.get("unit", "") or ""),
                reference=_number(item.get("reference")),
                observed=_number(item.get("observed")),
                relative_error=_number(item.get("relative_error")),
            )
        )
    return tuple(metrics)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None if value is None else float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _coerce_figures(raw: Any, case_id: str) -> list[Mapping[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TaskContractError("figures must be an array", case_id=case_id)
    return [dict(item) for item in raw if isinstance(item, Mapping)]


def _submission_to_answer(
    task: Task, context: SolveContext, submission: Mapping[str, Any]
) -> Mapping[str, Any]:
    """把 helper 的真实答卷转成我们的答案对象；指标名与单位原样保留。"""
    status = submission.get("status")
    if status not in ("completed", "failed", "materials_missing"):
        raise TaskContractError(
            f"helper submission status {status!r} is not a legal answer status",
            case_id=task.case_id,
        )
    metrics = _coerce_metrics(submission.get("metrics"), task.case_id)
    figures = _coerce_figures(submission.get("figures"), task.case_id)
    return reproduction_result(task.case_id, status=str(status), metrics=metrics, figures=figures)


def build_experiment_client(context: SolveContext) -> ExperimentClient:
    """构造正式执行客户端。

    单独成函数是为了让测试能注入替身；生产路径永远走平台挂载的 helper，
    绝不自制凭证、不绕过客户端、不直接连 Unix socket。
    """
    return SubprocessExperimentClient(
        Path(os.environ.get(HELPER_PATH_ENV, str(DEFAULT_HELPER_PATH))),
        result_root=context.workspace.root,
        team=context.team,
        log=context.log,
    )


def solve_reproduction(task: Task, context: SolveContext) -> Mapping[str, Any] | PersistedResult:
    experiment_root = Path(os.environ.get(EXPERIMENT_ROOT_ENV, "/data/experiments"))
    materials = load_case_materials(task.case_id, experiment_root)
    context.log.info(
        "reproduction.materials",
        case_id=task.case_id,
        available=materials.available,
        files=list(materials.present_files),
        reason=(materials.missing_reason or "")[:200],
    )
    if not materials.available:
        return _failure_answer(task, context, "materials_missing", materials.missing_reason)

    context.work_dir.mkdir(parents=True, exist_ok=True)
    implementation = context.work_dir / IMPLEMENTATION_FILENAME
    generated = _generate_source(task, context, materials, implementation, experiment_root)
    if generated is None:
        return _failure_answer(task, context, "failed", "no admissible model source this session")
    _source, _digest, self_test_passed = generated
    if not self_test_passed:
        # 自测没过仍然尝试正式执行：不调用必然 0 分，调用还可能拿到流程与图表的部分分
        context.log.warning("reproduction.proceeding_without_self_test", case_id=task.case_id)

    client = build_experiment_client(context)
    run_timeout = max(MIN_RUN_SECONDS, context.deadline.remaining() - RESERVE_PERSIST_SECONDS)
    context.deadline.take("reproduction.official_run", MIN_RUN_SECONDS, case_id=task.case_id)
    try:
        outcome = client.run(task.case_id, implementation, timeout=run_timeout)
    except FileNotFoundError as exc:
        return _failure_answer(task, context, "failed", str(exc))
    except OSError as exc:
        return _failure_answer(task, context, "failed", f"{exc.__class__.__name__}: {exc}")

    _save_diagnostics(context, outcome)

    submission = outcome.authoritative_submission
    if submission is None:
        return _failure_answer(
            task,
            context,
            "failed",
            "helper produced no parsable submission "
            f"(returncode={outcome.returncode}, timed_out={outcome.timed_out})",
        )

    try:
        answer = _submission_to_answer(task, context, submission)
    except TaskContractError as exc:
        context.log.error(
            "reproduction.submission_invalid", case_id=task.case_id, reason=exc.message[:200]
        )
        return _failure_answer(task, context, "failed", exc.message)

    # helper 自己写下的结果文件是权威产物：只回读校验，不重写、不重新序列化
    if outcome.produced_result_file and outcome.result_file_path is not None:
        expected = context.workspace.result_json(task.case_id)
        if outcome.result_file_path.resolve() == expected.resolve():
            context.log.info(
                "reproduction.completed",
                case_id=task.case_id,
                status=answer["status"],
                figures=len(answer["figures"]),
                preserved_helper_file=True,
            )
            return PersistedResult(
                payload=answer,
                data=outcome.result_file_bytes or b"",
                path=outcome.result_file_path,
            )
        # helper 写到了别处（例如 RESEARCH_RESULT_ROOT 与我们不同）：以我们的路径为准落盘
        context.log.warning(
            "reproduction.result_path_mismatch",
            case_id=task.case_id,
            helper_path=str(outcome.result_file_path),
            expected=context.workspace.relative_to_output(expected),
        )
    context.log.info(
        "reproduction.completed",
        case_id=task.case_id,
        status=answer["status"],
        figures=len(answer["figures"]),
        preserved_helper_file=False,
    )
    return answer
