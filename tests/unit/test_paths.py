from __future__ import annotations

from pathlib import Path

import pytest

from research_assistant.errors import ConfigError, PathAuthorizationError
from research_assistant.runtime.paths import IDEA_INFIX, SURVEY_INFIX, WorkspacePaths

TEAM = "测试队伍 A+1"
# 队名含正则元字符，用于持续验证 assert_own_expert_file 做了 re.escape
META_TEAM = "队+A.B*C"


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


def test_assert_own_expert_file_accepts_own_survey_and_idea(ws: WorkspacePaths) -> None:
    """两种规范命名都必须被接受：锚定不能过紧。"""
    survey = ws.survey_md("C-01")
    idea = ws.idea_md("E-02")
    survey.write_text("survey", encoding="utf-8")
    idea.write_text("idea", encoding="utf-8")
    assert ws.assert_own_expert_file(survey) == survey.resolve()
    assert ws.assert_own_expert_file(idea) == idea.resolve()


@pytest.mark.parametrize("infix", [SURVEY_INFIX, IDEA_INFIX])
@pytest.mark.parametrize("prefix", ["ZZZZ", "X", "ZZ-99", "A-01-2", "A-01-综述-别的队"])
def test_assert_own_expert_file_rejects_decoy_prefixes_in_shared_expert_dir(
    ws: WorkspacePaths, infix: str, prefix: str
) -> None:
    """共享 expert/ 对他队可写；仅后缀相同不得被当作本队文件。"""
    decoy = ws.expert_dir / f"{prefix}-{infix}-{TEAM}.md"
    decoy.write_text("decoy planted by another team", encoding="utf-8")
    with pytest.raises(PathAuthorizationError):
        ws.assert_own_expert_file(decoy)


@pytest.mark.parametrize("extra", [".evil", "x", "-2", ".md"])
def test_assert_own_expert_file_rejects_extra_trailing_characters(
    ws: WorkspacePaths, extra: str
) -> None:
    """文件名只是「包含」规范后缀、后面还挂了字符时同样拒绝（钉住 $ 锚点）。"""
    forged = ws.expert_dir / f"C-01-{SURVEY_INFIX}-{TEAM}.md{extra}"
    forged.write_text("forged", encoding="utf-8")
    with pytest.raises(PathAuthorizationError):
        ws.assert_own_expert_file(forged)


def test_assert_own_expert_file_escapes_regex_metacharacters_in_team_name(
    tmp_workspace: Path, papers_root: Path
) -> None:
    """队名里的 + . * 是正则元字符；未 re.escape 时本条会双向失败。"""
    ws_meta = WorkspacePaths(root=tmp_workspace, team=META_TEAM, papers_root=papers_root)
    own = ws_meta.survey_md("B-07")
    own.write_text("own report", encoding="utf-8")
    # 未转义时 `队+A.B*C` 无法匹配字面量 `队+A.B*C`，本队自己的报告反而被拒
    assert ws_meta.assert_own_expert_file(own) == own.resolve()

    idea = ws_meta.idea_md("B-07")
    idea.write_text("own idea", encoding="utf-8")
    assert ws_meta.assert_own_expert_file(idea) == idea.resolve()


@pytest.mark.parametrize("lookalike_team", ["队AXC", "队队XBC", "队AABC"])
def test_assert_own_expert_file_rejects_regex_lookalikes_of_the_team_name(
    tmp_workspace: Path, papers_root: Path, lookalike_team: str
) -> None:
    """未转义的 `.`/`*`/`+` 会匹配这些形近队名，转义后必须全部拒绝。"""
    ws_meta = WorkspacePaths(root=tmp_workspace, team=META_TEAM, papers_root=papers_root)
    lookalike = ws_meta.expert_dir / f"B-07-{SURVEY_INFIX}-{lookalike_team}.md"
    lookalike.write_text("lookalike", encoding="utf-8")
    with pytest.raises(PathAuthorizationError):
        ws_meta.assert_own_expert_file(lookalike)


def test_material_with_nul_byte_is_rejected_as_authorization_error(ws: WorkspacePaths) -> None:
    """NUL 字节必须映射成 403，而不是 pathlib 的 ValueError 冒到 HTTP 层变成 500。"""
    with pytest.raises(PathAuthorizationError):
        ws.paper("a\x00b.pdf")


def test_symlink_inside_papers_root_to_other_team_report_is_rejected(
    ws: WorkspacePaths, papers_root: Path, tmp_workspace: Path
) -> None:
    """papers_root 内部的符号链接指向他队报告时，paper() 仍必须拒绝。"""
    other_dir = tmp_workspace / "data" / "别的队"
    other_dir.mkdir(parents=True)
    secret = other_dir / f"C-01-{SURVEY_INFIX}-别的队.md"
    secret.write_text("other team survey", encoding="utf-8")
    link = papers_root / "看起来像论文.pdf"
    link.symlink_to(secret)
    with pytest.raises(PathAuthorizationError):
        ws.paper("看起来像论文.pdf")


def test_nested_subdirectory_material_is_accepted(ws: WorkspacePaths, papers_root: Path) -> None:
    """论文库内的合法多级子目录不得被误拒。"""
    sub = papers_root / "子目录"
    sub.mkdir()
    nested = sub / "深度 学习+v2.pdf"
    nested.write_text("body", encoding="utf-8")
    resolved = ws.paper("子目录/深度 学习+v2.pdf")
    assert resolved == nested.resolve()
    assert resolved.name == "深度 学习+v2.pdf"
    assert resolved.parent == sub.resolve()


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
