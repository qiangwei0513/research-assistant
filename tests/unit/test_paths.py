from __future__ import annotations

from pathlib import Path

import pytest

from research_assistant.errors import ConfigError, PathAuthorizationError
from research_assistant.runtime.paths import WorkspacePaths

TEAM = "测试队伍 A+1"


@pytest.fixture
def ws(tmp_workspace: Path, papers_root: Path) -> WorkspacePaths:
    return WorkspacePaths(root=tmp_workspace, team=TEAM, papers_root=papers_root)


def test_directories_follow_the_platform_layout(ws: WorkspacePaths, tmp_workspace: Path) -> None:
    assert ws.team_dir == tmp_workspace / "data" / TEAM
    assert ws.assigned_dir == tmp_workspace / "data" / TEAM / "assigned"
    assert ws.expert_dir == tmp_workspace / "data" / "expert"
    assert ws.figure_dir == tmp_workspace / "data" / "figure" / TEAM
    assert ws.dispatch_file == tmp_workspace / "data" / TEAM / "dispatch.json"


def test_result_json_uses_case_id_verbatim(ws: WorkspacePaths) -> None:
    assert ws.result_json("A-100").name == "A-100.json"
    assert ws.result_json("D-05").parent == ws.team_dir


def test_survey_and_idea_filenames_keep_the_fixed_infix(ws: WorkspacePaths) -> None:
    assert ws.survey_md("C-01").name == f"C-01-综述-{TEAM}.md"
    assert ws.idea_md("E-02").name == f"E-02-灵感Idea报告-{TEAM}.md"
    assert ws.survey_md("C-01").parent == ws.expert_dir


def test_work_dir_is_inside_the_team_directory(ws: WorkspacePaths) -> None:
    assert ws.work_dir("D-05") == ws.team_dir / "work" / "D-05"


def test_chinese_space_and_plus_are_preserved_in_paper_names(
    ws: WorkspacePaths, papers_root: Path
) -> None:
    name = "深度学习 综述+v2.pdf"
    (papers_root / name).write_text("body", encoding="utf-8")
    resolved = ws.paper(name)
    assert resolved.name == name
    assert resolved == (papers_root / name).resolve()


@pytest.mark.parametrize("material", ["/etc/passwd", "/data/papers/x.pdf", "C:\\papers\\x.pdf"])
def test_absolute_and_backslash_materials_are_rejected(ws: WorkspacePaths, material: str) -> None:
    with pytest.raises(PathAuthorizationError):
        ws.paper(material)


@pytest.mark.parametrize("material", ["../secret.pdf", "sub/../../secret.pdf", ".."])
def test_parent_traversal_is_rejected(ws: WorkspacePaths, material: str) -> None:
    with pytest.raises(PathAuthorizationError):
        ws.paper(material)


def test_empty_material_is_rejected(ws: WorkspacePaths) -> None:
    with pytest.raises(PathAuthorizationError):
        ws.paper("")


def test_symlink_escape_is_rejected(ws: WorkspacePaths, papers_root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.pdf"
    secret.write_text("secret", encoding="utf-8")
    link = papers_root / "link.pdf"
    link.symlink_to(secret)
    with pytest.raises(PathAuthorizationError):
        ws.paper("link.pdf")


def test_other_team_result_path_is_unreachable(ws: WorkspacePaths) -> None:
    """没有 API 能构造出指向他队目录的路径。"""
    assert "别的队" not in str(ws.result_json("A-01"))
    with pytest.raises(PathAuthorizationError):
        ws.paper("../别的队/A-01.json")


def test_relative_to_output_produces_posix_relative_path(ws: WorkspacePaths) -> None:
    rel = ws.relative_to_output(ws.survey_md("C-01"))
    assert rel == f"data/expert/C-01-综述-{TEAM}.md"
    assert not rel.startswith("/")


def test_relative_to_output_rejects_paths_outside_root(ws: WorkspacePaths, tmp_path: Path) -> None:
    with pytest.raises(PathAuthorizationError):
        ws.relative_to_output(tmp_path / "elsewhere.md")


def test_assert_own_expert_file_accepts_own_report(ws: WorkspacePaths) -> None:
    own = ws.survey_md("C-01")
    assert ws.assert_own_expert_file(own) == own


def test_assert_own_expert_file_rejects_other_team_report(ws: WorkspacePaths) -> None:
    foreign = ws.expert_dir / "C-01-综述-别的队.md"
    with pytest.raises(PathAuthorizationError):
        ws.assert_own_expert_file(foreign)


@pytest.mark.parametrize("team", ["", "  ", "a/b", "../evil", "a\\b"])
def test_invalid_team_is_rejected(tmp_workspace: Path, papers_root: Path, team: str) -> None:
    with pytest.raises(ConfigError):
        WorkspacePaths(root=tmp_workspace, team=team, papers_root=papers_root)


@pytest.mark.parametrize("case_id", ["../evil", "a/b", "", "A-01.json"])
def test_invalid_case_id_is_rejected(ws: WorkspacePaths, case_id: str) -> None:
    with pytest.raises(PathAuthorizationError):
        ws.result_json(case_id)


def test_question_file_stays_inside_assigned(ws: WorkspacePaths) -> None:
    assert ws.question_file("A-01") == ws.assigned_dir / "A-01.json"
