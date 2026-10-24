"""单题上下文装配。

一题一实例，无模块级可变状态；材料范围校验在这里发生，因为它需要工作区路径。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from research_assistant.contracts.dispatch import DispatchRecord
from research_assistant.contracts.task import Task
from research_assistant.errors import ConfigError, MaterialUnavailableError
from research_assistant.runtime.deadline import Deadline
from research_assistant.runtime.gateway import Gateway, HttpGateway, UnavailableGateway
from research_assistant.runtime.logging import BoundLogger, JsonLogger
from research_assistant.runtime.paths import WorkspacePaths
from research_assistant.runtime.settings import Settings


@dataclass(frozen=True)
class SolveContext:
    case_id: str
    team: str
    workspace: WorkspacePaths
    allowed_materials: frozenset[str]
    output_dir: Path
    work_dir: Path
    deadline: Deadline
    settings: Settings
    dispatch: DispatchRecord | None
    log: BoundLogger
    gateway: Gateway


def _check_current_question(
    record: DispatchRecord, workspace: WorkspacePaths, case_id: str, log: BoundLogger
) -> None:
    """只核对当前题的 assigned 文件；缺失或哈希不符只告警。

    组委会清单说明平台自己会校验 dispatch/assigned 与请求的一致性，且 Single
    模式只保证当前题文件可读。把「11 个文件都在且哈希都对」当作解题前提，会在
    平台只挂当前题时让整轮 422——这比少一层自检的代价大得多。
    """
    case = next((item for item in record.cases if item.case_id == case_id), None)
    if case is None:
        log.warning("dispatch.case_not_listed", case_id=case_id)
        return
    relative = case.question_file
    if relative.startswith("/") or "\\" in relative or ".." in relative.split("/"):
        log.warning("dispatch.unsafe_question_path", case_id=case_id)
        return
    path = (workspace.root / relative).resolve()
    if workspace.assigned_dir.resolve() not in path.parents:
        log.warning("dispatch.question_outside_assigned", case_id=case_id)
        return
    if not path.is_file():
        log.warning("dispatch.question_absent", case_id=case_id)
        return
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        log.warning("dispatch.question_unreadable", reason=exc.__class__.__name__)
        return
    if digest != case.question_sha256:
        log.warning(
            "dispatch.question_hash_mismatch",
            case_id=case_id,
            declared=case.question_sha256[:12],
            actual=digest[:12],
        )


def _load_dispatch(
    workspace: WorkspacePaths, team: str, log: BoundLogger, case_id: str
) -> DispatchRecord | None:
    """派题记录只用于一致性与安全边界提示，不因它拒绝一道平台派发的题。"""
    path = workspace.dispatch_file
    if not path.is_file():
        log.info("dispatch.absent", path=str(path))
        return None
    try:
        record = DispatchRecord.load(path)
        record.assert_team(team)
    except Exception as exc:
        log.warning("dispatch.invalid", reason=exc.__class__.__name__, detail=str(exc)[:200])
        return None
    _check_current_question(record, workspace, case_id, log)
    log.info("dispatch.loaded", **record.summary())
    if record.is_legacy:
        log.warning(
            "dispatch.legacy_version",
            schema_version=record.schema_version,
            rules_version=record.rules_version,
        )
    return record


def _verify_materials(task: Task, workspace: WorkspacePaths) -> frozenset[str]:
    allowed: set[str] = set()
    for material in task.materials:
        path = workspace.paper(material)
        # is_file() 只对 ENOENT/ENOTDIR/EBADF/ELOOP 静默；ENAMETOOLONG 等会抛 OSError。
        # 那是材料问题，必须落在 422，不能让裸 OSError 逃成笼统 500。
        try:
            present = path.is_file()
        except OSError:
            present = False
        if not present:
            raise MaterialUnavailableError(
                f"authorized material is not available: {material!r:.64}",
                case_id=task.case_id,
            )
        allowed.add(material)
    return frozenset(allowed)


def load_task_context(
    task: Task, settings: Settings, *, gateway: Gateway | None = None
) -> SolveContext:
    if not settings.team:
        raise ConfigError(
            "RESEARCH_WORKSPACE_TEAM is not set; refusing to guess a team directory",
            case_id=task.case_id,
        )

    workspace = WorkspacePaths(
        root=settings.workspace_root, team=settings.team, papers_root=settings.papers_root
    )
    log = JsonLogger("solve", settings.log_level).bind(
        case_id=task.case_id, function=task.function.value, team=settings.team
    )
    if task.unknown_fields:
        log.warning(
            "task.unknown_fields", fields=sorted(task.unknown_fields), reason="protocol drift"
        )

    allowed_materials = _verify_materials(task, workspace)
    dispatch = _load_dispatch(workspace, settings.team, log, task.case_id)

    if gateway is None:
        if settings.gateway is None:
            gateway = UnavailableGateway(
                settings.gateway_config_error or "model gateway is not configured"
            )
        else:
            gateway = HttpGateway(settings.gateway, log=log)

    return SolveContext(
        case_id=task.case_id,
        team=settings.team,
        workspace=workspace,
        allowed_materials=allowed_materials,
        output_dir=workspace.team_dir,
        work_dir=workspace.work_dir(task.case_id),
        deadline=Deadline.start(settings.case_budget_seconds),
        settings=settings,
        dispatch=dispatch,
        log=log,
        gateway=gateway,
    )
