from __future__ import annotations

from pathlib import Path

import pytest

TEAM = "测试队伍 A+1"


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    """建一个符合平台布局的临时工作区根目录（相当于容器内的 /output）。"""
    root = tmp_path / "output"
    (root / "data" / TEAM / "assigned").mkdir(parents=True)
    (root / "data" / TEAM / "work").mkdir(parents=True)
    (root / "data" / "expert").mkdir(parents=True)
    (root / "data" / "figure" / TEAM).mkdir(parents=True)
    (tmp_path / "papers").mkdir()
    return root


@pytest.fixture
def papers_root(tmp_workspace: Path) -> Path:
    return tmp_workspace.parent / "papers"


@pytest.fixture
def base_env(tmp_workspace: Path, papers_root: Path) -> dict[str, str]:
    """一份完整的、使用 CHALLENGE 变量组的开发环境。

    队伍名故意含中文、空格与 '+'，用于持续验证文件名不被归一化或转义。
    api_key 是明显的假值，不是真实凭据。
    """
    return {
        "CHALLENGE_LLM_BASE_URL": "http://gateway.invalid/v1",
        "CHALLENGE_LLM_API_KEY": "sk-test-not-a-real-key",
        "CHALLENGE_LLM_MODEL": "test-model",
        "RESEARCH_WORKSPACE_TEAM": TEAM,
        "RESEARCH_WORKSPACE_ROOT": str(tmp_workspace),
        "RESEARCH_PAPERS_ROOT": str(papers_root),
    }
