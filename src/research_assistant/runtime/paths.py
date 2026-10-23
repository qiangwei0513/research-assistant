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
_CASE_ID_PATTERN = r"[A-E]-\d{1,3}"
_CASE_ID_SEGMENT = re.compile(rf"^{_CASE_ID_PATTERN}$")


def _reject_unsafe(raw: str, *, what: str) -> None:
    if not raw or not raw.strip():
        raise PathAuthorizationError(f"{what} must not be empty")
    if "\x00" in raw:
        # pathlib 会抛 ValueError，在 HTTP 层退化成 500；这里转成 403
        raise PathAuthorizationError(f"{what} must not contain NUL bytes: {raw!r}")
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
        # 本队报告文件名的完整形状，两端锚定；队名与 infix 都可能含正则元字符
        # （真实队名含空格与 '+'），必须 re.escape，否则会漏判或误判。
        # 逐实例编译、不放模块级可变状态。
        self._own_expert_name = re.compile(
            rf"^{_CASE_ID_PATTERN}"
            rf"-(?:{re.escape(SURVEY_INFIX)}|{re.escape(IDEA_INFIX)})"
            rf"-{re.escape(team)}\.md$"
        )

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
        try:
            base_resolved = base.resolve()
            candidate = (base / raw).resolve()
        except (OSError, ValueError) as exc:
            # 不可信字符串在解析阶段就可能让 pathlib 抛 ValueError/OSError；
            # 一律转成 403，并保留原因链，不静默吞掉。
            raise PathAuthorizationError(
                f"{what} cannot be resolved inside its authorized root: {raw!r}"
            ) from exc
        # 已知限制（TOCTOU）：resolve() 之后到调用方真正 open() 之间，符号链接可能被换掉，
        # 从而绕过下面这次包含性检查。真实部署里 papers/ 与题面挂载均为只读，
        # 攻击者无法改写链接，故该窗口不可利用；P1 不引入加锁或 O_NOFOLLOW。
        # base 与 candidate 都必须 resolve：macOS 上 /tmp 本身是 /private/tmp 的符号链接。
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
        """共享的 data/expert 内只允许操作本队规范命名的文件。

        expert/ 对全部队伍可写，文件名本身是不可信输入：必须整体匹配
        `<case_id>-<infix>-<team>.md` 且两端锚定。仅比对后缀会让别队投放的诱饵
        （如 `ZZZZ-综述-<team>.md`）冒充本队文件。
        """
        try:
            resolved = path.resolve()
        except (OSError, ValueError) as exc:
            raise PathAuthorizationError(f"expert file path cannot be resolved: {path!r}") from exc
        expert_resolved = self.expert_dir.resolve()
        # 同上：resolve 与实际 open 之间存在 TOCTOU 窗口，依赖只读挂载收敛风险。
        if not _contains(expert_resolved, resolved):
            raise PathAuthorizationError(f"path is not inside data/expert: {path!r}")
        if not self._own_expert_name.fullmatch(resolved.name):
            raise PathAuthorizationError(
                "expert file does not follow this team's naming rule "
                f"(<case_id>-{SURVEY_INFIX}|{IDEA_INFIX}-<team>.md); rejected {resolved.name!r}"
            )
        return resolved
