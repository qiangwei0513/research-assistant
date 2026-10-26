"""Thirty invented English PDFs, Chinese request, target papers late in library order."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from research_assistant.api import create_app
from research_assistant.runtime.settings import Settings
from tests.fixtures.papers import write_paper
from tests.live.test_live_reports import load_local_env

pytestmark = pytest.mark.live


def write_coverage_library(papers: Path) -> set[str]:
    papers.mkdir(parents=True, exist_ok=True)
    relevant = {"Study_002.pdf", "Study_027.pdf", "Study_029.pdf"}
    for i in range(30):
        name = f"Study_{i:03}.pdf"
        if name in relevant:
            title = f"Satellite observations of vegetation cover and urban surface cooling: district {i}"
            abstract = (
                "We investigate how urban green space coverage relates to land surface temperature. "
                "Thermal infrared satellite observations and vegetation indices quantify cooling across "
                "different urban districts. We compare densely built neighbourhoods with tree-covered "
                "districts while controlling for season and local weather. The study analyses temperature "
                "patterns and green coverage, rather than only classifying vegetation types."
            )
            body = [
                abstract,
                "Our analysis links NDVI and urban green fraction to remotely sensed land surface temperature. "
                * 5,
            ]
            keywords = "urban green space; remote sensing; land surface temperature"
        elif i == 28:
            title = "Satellite vegetation segmentation with convolutional neural networks"
            abstract = (
                "We benchmark semantic segmentation of vegetation in satellite images using neural networks. "
                "The evaluation uses pixel classification accuracy and intersection over union. "
                "No temperature, thermal infrared, surface cooling or urban heat relationship is analysed."
            )
            body = [abstract * 3]
            keywords = "remote sensing; vegetation; segmentation"
        else:
            title = f"Hydrometallurgical recovery from discarded lithium batteries: process {i}"
            abstract = (
                "This work investigates cobalt and lithium recovery from discarded batteries. "
                "We compare acid leaching and solvent extraction with respect to chemical recovery "
                "and recycling process cost. The experiments concern industrial battery materials "
                "and have no connection to vegetation, urban surface temperature or satellite observations."
            )
            body = [abstract * 3]
            keywords = "battery recycling; hydrometallurgy; cobalt"
        write_paper(
            papers,
            name,
            title=title,
            author="Example Researcher",
            keywords=keywords,
            creation_date="D:20240101000000Z",
            abstract=abstract,
            body_paragraphs=body,
            pages=2,
        )
    return relevant


def test_chinese_query_finds_late_targets_and_excludes_decoys(tmp_path):
    papers = tmp_path / "papers"
    relevant = write_coverage_library(papers)
    output = tmp_path / "output"
    env = {
        **load_local_env(),
        "RESEARCH_WORKSPACE_ROOT": str(output),
        "RESEARCH_PAPERS_ROOT": str(papers),
        "RESEARCH_WORKSPACE_TEAM": "live-coverage",
    }
    settings = Settings.from_env(env)
    if settings.gateway is None:
        pytest.skip("live Gateway is not configured")
    payload = {
        "case_id": "B-01",
        "function": "Literature_Retrieval",
        "title": "城市绿地降温研究",
        "input": "检索利用遥感数据研究城市绿地覆盖与地表温度关系、比较不同城区降温效应的论文。排除纯植被分类、图像分割且不分析温度的工作，以及电池回收论文。",
        "materials": ["本地论文文件夹内的 PDF"],
        "constraints": ["只选择有正文证据的相关论文，完整返回文件名和具体理由"],
        "expected_output": "按相关性排序的相关论文列表",
        "output_schema_ref": "Literature_Retrieval",
    }
    with TestClient(create_app(settings)) as client:
        response = client.post("/solve", json=payload)
    evidence = (
        Path(__file__).resolve().parents[2] / "work/verification/2026-10-25-retrieval-coverage"
    )
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "live-response.json").write_text(response.text, encoding="utf-8")
    (evidence / "live-request.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    assert response.status_code == 200, response.text[:200]
    result = response.json()
    assert {row["filename"] for row in result["papers"]} == relevant
    disk = output / "data/live-coverage/B-01.json"
    assert json.loads(disk.read_text()) == result
