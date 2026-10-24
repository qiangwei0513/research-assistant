"""单题上下文装配。

一题一实例，无模块级可变状态；材料范围校验在这里发生，因为它需要工作区路径。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from research_assistant.contracts.dispatch import DispatchRecord
from research_assistant.contracts.task import Task, TaskFunction
from research_assistant.errors import ConfigError, PathAuthorizationError
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
    materials: tuple[str, ...]
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


def _resolve_material(
    material: str, workspace: WorkspacePaths, listing: list[str] | None
) -> str | None:
    """把题目里的材料名解析成论文库里真实存在的文件名。

    平台可能给带目录前缀的路径，也可能大小写与挂载文件不同；B 类评分本身对
    大小写等价。找不到就返回 None，由调用方告警，绝不因此拒绝整道题。
    """
    candidates = [material]
    basename = Path(material).name
    if basename and basename != material:
        candidates.append(basename)
    for candidate in candidates:
        try:
            path = workspace.paper(candidate)
        except PathAuthorizationError:
            # 路径越界是攻击面，不是「缺材料」；必须原样上抛为 403
            raise
        except Exception:
            continue
        try:
            if path.is_file():
                return candidate
        except OSError:
            continue
    if listing is None:
        return None
    target = basename.casefold()
    for name in listing:
        if name.casefold() == target:
            return name
    return None


# 官方 B 类输入示例明确用这个范围描述，而不是某个 PDF 的文件名。
_LIBRARY_SCOPE_DESCRIPTIONS = frozenset({"本地论文文件夹内的 PDF"})


def _retrieval_scope(
    material: str, workspace: WorkspacePaths, log: BoundLogger
) -> list[str] | None:
    """仅展开明确授权的 B 类目录范围；缺失的具体文件绝不扩大为整库。"""
    relative = "." if material in _LIBRARY_SCOPE_DESCRIPTIONS else material
    directory = workspace.paper(relative)
    if not directory.is_dir():
        return None
    found: list[str] = []
    # rglob 不递归跟随目录符号链接；文件链接仍逐个走 workspace.paper 授权。
    for entry in sorted(directory.rglob("*")):
        if entry.suffix.lower() != ".pdf":
            continue
        name = entry.relative_to(workspace.papers_root.resolve()).as_posix()
        try:
            if workspace.paper(name).is_file():
                found.append(name)
        except (PathAuthorizationError, OSError):
            log.warning("materials.scope_entry_rejected", filename=name[:200])
    log.info("materials.library_scope", scope=material, files=len(found))
    return found


def _resolve_materials(task: Task, workspace: WorkspacePaths, log: BoundLogger) -> tuple[str, ...]:
    """只保留真实存在的材料，并保持题目给出的顺序。

    材料缺失不是协议错误：平台可能没有挂载论文库，或 Single 模式只挂当前题所需
    文件。拒绝整题会让 B/C/E 直接归零；交给各 handler 如实处理（B 返回空数组，
    C/E 返回诚实报告）才是规范要求的行为。
    """
    listing: list[str] | None = None
    try:
        listing = sorted(entry.name for entry in workspace.papers_root.iterdir() if entry.is_file())
    except OSError:
        listing = None
    resolved: list[str] = []
    seen: set[str] = set()
    for material in task.materials:
        if task.function is TaskFunction.LITERATURE_RETRIEVAL:
            scoped = _retrieval_scope(material, workspace, log)
            if scoped is not None:
                for name in scoped:
                    if name not in seen:
                        seen.add(name)
                        resolved.append(name)
                continue
        found = _resolve_material(material, workspace, listing)
        if found is None:
            log.warning("materials.missing", filename=material[:200])
            continue
        if found not in seen:
            seen.add(found)
            resolved.append(found)
    return tuple(resolved)


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

    materials = _resolve_materials(task, workspace, log)
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
        allowed_materials=frozenset(materials),
        materials=materials,
        output_dir=workspace.team_dir,
        work_dir=workspace.work_dir(task.case_id),
        deadline=Deadline.start(settings.case_budget_seconds),
        settings=settings,
        dispatch=dispatch,
        log=log,
        gateway=gateway,
    )
