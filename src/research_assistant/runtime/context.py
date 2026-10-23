"""单题上下文装配。

一题一实例，无模块级可变状态；材料范围校验在这里发生，因为它需要工作区路径。
"""

from __future__ import annotations

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


def _load_dispatch(workspace: WorkspacePaths, team: str, log: BoundLogger) -> DispatchRecord | None:
    path = workspace.dispatch_file
    if not path.is_file():
        log.info("dispatch.absent", path=str(path))
        return None
    record = DispatchRecord.load(path)
    record.assert_team(team)
    record.verify_questions(workspace)
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
        if not path.is_file():
            raise MaterialUnavailableError(
                f"authorized material is not available: {material}", case_id=task.case_id
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
    dispatch = _load_dispatch(workspace, settings.team, log)

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
