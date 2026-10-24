from __future__ import annotations

from pathlib import Path

import pytest

TEAM = "测试队伍 A+1"


@pytest.fixture(autouse=True)
def _reset_telemetry_cache():
    """telemetry 有进程级缓存（_INTERCEPTORS_INSTALLED/_TELEMETRY_TRIED/_TELEMETRY）。

    个别测试会用 monkeypatch 装假 SDK，若不复位就会跨文件泄漏状态，
    让依赖 telemetry 行为的测试变成顺序相关。每个测试前后都清一次。
    """
    from research_assistant.runtime import telemetry

    telemetry.reset_cache()
    yield
    telemetry.reset_cache()


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


# --- P2：自建小论文库，用于文档解析与 B 类检索测试 ---
#
# 这些不是官方论文，不含任何正式答案。设计成可区分的四档：
#   强相关（遥感 + 城市绿地 + 地表温度）、次相关（植被 + 城市热环境）、
#   邻近但不匹配主题（深度学习 + 卫星图像分割，不涉及温度）、完全无关（电池回收），
#   外加一份无文本层的「扫描件」用于验证 EMPTY 状态如实上报。
PAPER_SPECS: list[dict[str, object]] = [
    {
        "filename": "Example_2024_Remote_Sensing_of_Urban_Green_Space_and_Surface_Temperature.pdf",
        "title": "Remote Sensing of Urban Green Space and Land Surface Temperature",
        "author": "Zhang, Wei; Li, Na",
        "keywords": "remote sensing, urban green space, land surface temperature, NDVI",
        "creation_date": "D:20240315120000+08'00'",
        "abstract": (
            "We use multispectral satellite remote sensing to quantify how urban green space "
            "coverage correlates with land surface temperature across city districts."
        ),
        "body": [
            "Green space coverage was derived from NDVI and compared against thermal infrared bands.",
            "Districts with higher vegetation fraction showed consistently lower mean surface temperature.",
            "The cooling effect saturated beyond a vegetation fraction of about forty percent.",
        ],
    },
    {
        "filename": "Example_2022_Neighborhood_Vegetation_and_Urban_Heat.pdf",
        "title": "Neighborhood Vegetation Patterns and Urban Heat Island Intensity",
        "author": "Chen, Bo",
        "keywords": "urban heat island, vegetation, neighborhood comparison",
        "creation_date": "D:20220701090000Z",
        "abstract": (
            "This study compares vegetation conditions and thermal environment differences between "
            "neighbourhoods of varying density."
        ),
        "body": [
            "Neighbourhood scale vegetation indices were regressed against measured air temperature.",
            "Dense downtown blocks exhibited the strongest heat island intensity.",
        ],
    },
    {
        "filename": "Example_2023_Deep_Learning_for_Satellite_Image_Segmentation.pdf",
        "title": "Deep Learning Architectures for Satellite Image Semantic Segmentation",
        "author": "Wang, Fang; Liu, Yang; Zhao, Ming",
        "keywords": "deep learning, semantic segmentation, satellite imagery",
        "creation_date": "D:20231120101010Z",
        "abstract": (
            "We benchmark convolutional and transformer architectures for semantic segmentation of "
            "satellite imagery, focusing on boundary accuracy rather than thermal retrieval."
        ),
        "body": [
            "Segmentation accuracy was evaluated on labelled building and road footprints.",
            "Transformer backbones improved boundary recall over convolutional baselines.",
        ],
    },
    {
        "filename": "Example_2021_Battery_Recycling_Hydrometallurgy_Life_Cycle.pdf",
        "title": "Hydrometallurgical Recycling of Lithium Ion Batteries and Life Cycle Assessment",
        "author": "Kim, Soo Jin",
        "keywords": "battery recycling, hydrometallurgy, life cycle assessment",
        "creation_date": "D:20210405080000Z",
        "abstract": (
            "We assess hydrometallurgical recovery routes for spent lithium ion cells using life "
            "cycle inventory data and compare their environmental impacts."
        ),
        "body": [
            "Recovery yields for lithium and cobalt exceeded ninety percent in the pilot line.",
            "Life cycle results favoured direct recycling over hydrometallurgy for cathode material.",
        ],
    },
]

SCANNED_FILENAME = "扫描版 无文本层 2020.pdf"


@pytest.fixture
def paper_library(papers_root: Path) -> list[str]:
    """在授权论文库中生成自建材料，返回全部文件名（含扫描件）。"""
    from tests.fixtures.papers import write_paper, write_scanned_pdf

    names: list[str] = []
    for spec in PAPER_SPECS:
        write_paper(
            papers_root,
            str(spec["filename"]),
            title=str(spec["title"]),
            author=str(spec["author"]),
            keywords=str(spec["keywords"]),
            creation_date=str(spec["creation_date"]),
            abstract=str(spec["abstract"]),
            body_paragraphs=[str(b) for b in spec["body"]],
            pages=2,
        )
        names.append(str(spec["filename"]))
    write_scanned_pdf(papers_root, SCANNED_FILENAME)
    names.append(SCANNED_FILENAME)
    return names


@pytest.fixture
def relevant_paper_names() -> list[str]:
    """对「城市绿地与地表温度的遥感研究」这一主题真正相关的两篇。"""
    return [
        "Example_2024_Remote_Sensing_of_Urban_Green_Space_and_Surface_Temperature.pdf",
        "Example_2022_Neighborhood_Vegetation_and_Urban_Heat.pdf",
    ]
