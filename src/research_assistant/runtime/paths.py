"""授权路径解析。

规则：拒绝绝对提交路径、`..`、反斜杠绕过与符号链接越界；解析真实路径后确认归属。
中文、空格、`+` 等合法字符原样保留。任何文件名都不得拼进 shell 命令。
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from research_assistant.errors import ConfigError, PathAuthorizationError

SURVEY_INFIX = "综述"
IDEA_INFIX = "灵感Idea报告"

# case_id 已由 Task.parse 校验；这里再挡一次，避免路径拼接被污染
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._+\-\u4e00-\u9fff ]+$")
_CASE_ID_SEGMENT = re.compile(r"^[A-E]-\d{1,3}$")


def _reject_unsafe(raw: str, *, what: str) -> None:
    if not raw or not raw.strip():
        raise PathAuthorizationError(f"{what} must not be empty")
    if "\\" in raw:
        raise PathAuthorizationError(f"{what} must not contain backslashes: {raw!r}")
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise PathAuthorizationError(f"{what} must be relative to its authorized root: {raw!r}")
    if any(part == ".." for part in PurePosixPath(raw).parts):
        raise PathAuthorizationError(f"{what} must not traverse parents: {raw!r}")


def _check_segment(value: str, *, what: str) -> str:
    if not value or not value.strip():
        raise PathAuthorizationError(f"{what} must not be empty")
    if not _SAFE_SEGMENT.fullmatch(value):
        raise PathAuthorizationError(f"{what} contains disallowed characters: {value!r}")
    return value


def _contains(base: Path, candidate: Path) -> bool:
    return candidate == base or base in candidate.parents


class WorkspacePaths:
    """一个队伍在本轮评测中的全部授权位置。"""

    def __init__(self, root: Path, team: str, papers_root: Path) -> None:
        if not team or not team.strip():
            raise ConfigError(
                "RESEARCH_WORKSPACE_TEAM is not set; refusing to guess a team directory"
            )
        if "/" in team or "\\" in team or ".." in team:
            raise ConfigError(f"RESEARCH_WORKSPACE_TEAM is not a single path segment: {team!r}")
        self.root = root
        self.team = team
        self.papers_root = papers_root
        self._root_resolved = root.resolve()
        self._papers_resolved = papers_root.resolve()

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def team_dir(self) -> Path:
        return self.data_dir / self.team

    @property
    def assigned_dir(self) -> Path:
        return self.team_dir / "assigned"

    @property
    def dispatch_file(self) -> Path:
        return self.team_dir / "dispatch.json"

    @property
    def expert_dir(self) -> Path:
        return self.data_dir / "expert"

    @property
    def figure_dir(self) -> Path:
        return self.data_dir / "figure" / self.team

    def _resolve_within(self, base: Path, raw: str, *, what: str) -> Path:
        _reject_unsafe(raw, what=what)
        base_resolved = base.resolve()
        candidate = (base / raw).resolve()
        if not _contains(base_resolved, candidate):
            raise PathAuthorizationError(f"{what} escapes its authorized root: {raw!r}")
        return candidate

    def paper(self, material: str) -> Path:
        """把 materials 条目解析为论文库内的真实路径。"""
        path = self._resolve_within(self.papers_root, material, what="material")
        for part in path.relative_to(self.papers_root.resolve()).parts[:-1]:
            _check_segment(part, what="material path segment")
        return path

    def _case_path(self, case_id: str, directory: Path, filename: str) -> Path:
        if not _CASE_ID_SEGMENT.fullmatch(case_id):
            raise PathAuthorizationError(f"case_id is not usable as a filename: {case_id!r}")
        return directory / filename

    def result_json(self, case_id: str) -> Path:
        return self._case_path(case_id, self.team_dir, f"{case_id}.json")

    def question_file(self, case_id: str) -> Path:
        return self._case_path(case_id, self.assigned_dir, f"{case_id}.json")

    def survey_md(self, case_id: str) -> Path:
        return self._case_path(case_id, self.expert_dir, f"{case_id}-{SURVEY_INFIX}-{self.team}.md")

    def idea_md(self, case_id: str) -> Path:
        return self._case_path(case_id, self.expert_dir, f"{case_id}-{IDEA_INFIX}-{self.team}.md")

    def work_dir(self, case_id: str) -> Path:
        return self._case_path(case_id, self.team_dir / "work", case_id)

    def relative_to_output(self, path: Path) -> str:
        """产出 JSON 内使用的 path：相对工作区根，正斜杠，不含前导 /。"""
        resolved = path.resolve()
        if not _contains(self._root_resolved, resolved):
            raise PathAuthorizationError(f"path is outside the workspace root: {path!r}")
        return resolved.relative_to(self._root_resolved).as_posix()

    def assert_own_expert_file(self, path: Path) -> Path:
        """共享的 data/expert 内只允许操作本队规范命名的文件。"""
        resolved = path.resolve()
        expert_resolved = self.expert_dir.resolve()
        if not _contains(expert_resolved, resolved):
            raise PathAuthorizationError(f"path is not inside data/expert: {path!r}")
        suffixes = (
            f"-{SURVEY_INFIX}-{self.team}.md",
            f"-{IDEA_INFIX}-{self.team}.md",
        )
        if not resolved.name.endswith(suffixes):
            raise PathAuthorizationError(
                f"expert file does not follow this team's naming rule: {resolved.name!r}"
            )
        return resolved
