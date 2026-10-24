"""C 类（Survey）与 E 类（Idea）handler 测试，含 Idea 自检器的逐项覆盖。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from research_assistant.contracts.results import MIN_BODY_CHARACTERS
from research_assistant.contracts.task import Task
from research_assistant.errors import MaterialUnavailableError
from research_assistant.handlers.idea import (
    REQUIRED_BOUNDARY_STATEMENTS,
    REQUIRED_SECTIONS,
    check_idea_report,
    solve_idea,
)
from research_assistant.handlers.survey import build_user_prompt, solve_survey
from research_assistant.runtime.context import load_task_context
from research_assistant.runtime.gateway import FakeGateway
from research_assistant.runtime.settings import Settings
from tests.fixtures.papers import write_paper

TEAM = "测试队伍 A+1"
P1 = "城市绿地遥感_2024.pdf"
P2 = "街区植被与热环境_2022.pdf"
P3 = "电池回收生命周期_2021.pdf"
P4 = "卫星图像分割_2023.pdf"
P5 = "第五篇授权材料_2020.pdf"


def long_sentence(topic: str) -> str:
    return f"关于{topic}的具体陈述，这一段足够长以通过实质上下文的最小字符要求"


def cited(topic: str, number: int) -> str:
    """一句带引用的完整陈述。引用必须落在句号之前，才符合真实正文写法。"""
    return long_sentence(topic) + f" [{number}]。"


def good_survey_markdown() -> str:
    """一篇能通过全部引文自检的综述：5 篇均被正文引用、每篇跨两个实质段落、无打包。"""
    return (
        "# 城市绿地与热环境研究综述\n\n"
        "## 研究问题与方法比较\n\n"
        + cited("遥感反演植被覆盖与地表温度", 1)
        + cited("街区尺度植被指数回归实测气温", 2)
        + cited("两种方法在分辨率与样本量上的取舍", 3)
        + "\n\n## 数据与指标\n\n"
        + cited("时序影像与土地利用分类数据", 2)
        + cited("回归系数与归一化方式的差异", 3)
        + cited("跨研究可比性受指标定义影响", 4)
        + cited("饱和阈值在不同数据源上的一致性", 5)
        + "\n\n## 结论、局限与适用边界\n\n"
        + cited("植被比例与地表温度的负相关", 5)
        + cited("缺乏夜间热环境观测", 1)
        + cited("城市形态变量控制不足", 4)
        + "\n\n## 技术脉络与研究缺口\n\n"
        + cited("从区级相关分析走向街区尺度因果识别", 1)
        + cited("指标定义不统一造成的比较障碍", 3)
        + cited("分割精度提升为绿地提取带来的新可能", 4)
        + cited("缺少夜间与三维冠层观测这一共同缺口", 5)
        + "\n\n## 参考文献\n\n"
        f"[1] 张三, 李四. 城市绿地遥感反演研究. {P1}\n"
        f"[2] 王五. 街区植被与城市热岛. {P2}\n"
        f"[3] 赵六. 电池回收与生命周期评价. {P3}\n"
        f"[4] 孙七. 卫星图像语义分割. {P4}\n"
        f"[5] 周八. 第五篇授权材料研究. {P5}\n"
    )


def good_idea_markdown() -> str:
    sections = {
        "研究问题与边界": long_sentence("研究问题的界定")
        + "；"
        + long_sentence("不纳入范围的部分")
        + "。",
        "动机与已有局限": long_sentence("已有工作的第一点局限")
        + " [1]。"
        + long_sentence("第二点局限")
        + "。",
        "核心机制": long_sentence("所提机制的因果链条")
        + "；"
        + long_sentence("关键假设与其可检验性")
        + "。",
        "技术路线": long_sentence("四个阶段的技术路线")
        + " [2]。"
        + long_sentence("各阶段之间的依赖关系")
        + "。",
        "基线与改进组": "基线组采用原始 TF-IDF 加 softmax 分类器，改进组在此之上加入所提机制，"
        "其余训练条件、数据划分与随机种子保持一致，两组直接对比 Macro-F1。",
        "数据集": long_sentence("两个公开基准数据集")
        + "；"
        + long_sentence("其划分方式与规模")
        + "。",
        "实验与消融": long_sentence("三组消融实验分别关闭各组件")
        + "；"
        + long_sentence("重复次数与显著性检验")
        + "。",
        "量化指标": "以 Macro-F1 为主指标，预期相对基线提升 3.5%，同时报告 Accuracy 与运行时间，"
        "并给出 95% 置信区间。",
        "多文献互补": long_sentence("第一篇提供数据与标注")
        + " [1]；"
        + long_sentence("第二篇提供方法骨架")
        + " [2]。",
        "可行性与成本": long_sentence("单卡即可完成的训练规模")
        + "；"
        + long_sentence("预计时间与算力成本")
        + "。",
        "失败边界与可证伪条件": long_sentence("三个可证伪条件")
        + "；"
        + long_sentence("各自的观测方式与判据")
        + "。",
    }
    body = "# 研究方案\n\n"
    for name in REQUIRED_SECTIONS:
        body += f"## {name}\n\n{sections[name]}\n\n"
    body += (
        "## 证据边界\n\n"
        "上述模拟观察尚未验证，所提改进尚未执行，全部效果均为待验证的预期，"
        "不得据此认为已经取得实验结论。\n\n"
        "## 参考文献\n\n"
        f"[1] 张三. 城市绿地遥感. {P1}\n"
        f"[2] 王五. 街区植被. {P2}\n"
    )
    return body


@pytest.fixture
def five_papers(papers_root: Path) -> list[str]:
    from tests.fixtures.papers import write_paper

    names = [P1, P2, P3, P4, P5]
    for index, name in enumerate(names):
        write_paper(
            papers_root,
            name,
            title=f"Paper {index} title about urban green space and temperature",
            author="Zhang, Wei; Li, Na",
            keywords="remote sensing, urban heat, green space",
            abstract=(
                "We analyse satellite imagery to quantify how urban green space coverage "
                "correlates with land surface temperature across districts."
            ),
            body_paragraphs=[
                long_sentence(f"evidence paragraph one of paper {index}"),
                long_sentence(f"evidence paragraph two of paper {index}"),
            ],
            pages=2,
        )
    return names


def survey_task(materials: list[str]) -> Task:
    return Task.parse(
        {
            "case_id": "C-01",
            "function": "Survey",
            "title": "城市绿地与地表温度综述",
            "input": "比较各论文的研究问题、方法、数据、指标、结论与局限，形成技术脉络。",
            "materials": materials,
            "constraints": ["只依据本题指定论文", "不得编造事实、数据、页码或参考文献"],
            "expected_output": "返回 case_id、path 和完整 Markdown 综述。",
            "output_schema_ref": "Survey",
        }
    )


def idea_task(materials: list[str]) -> Task:
    return Task.parse(
        {
            "case_id": "E-01",
            "function": "Idea",
            "title": "研究方案设计",
            "input": "基于指定论文提出一个具体可证伪的研究方案。",
            "materials": materials,
            "constraints": ["不得把未验证的改进效果写成已完成实验"],
            "expected_output": "返回 case_id、path 和完整 Markdown 方案。",
            "output_schema_ref": "Idea",
        }
    )


def context_for(
    base_env: dict[str, str],
    task: Task,
    responses: list[Any],
    papers_root: Path,
    tmp_workspace: Path,
):
    env = {
        **base_env,
        "RESEARCH_PAPERS_ROOT": str(papers_root),
        "RESEARCH_WORKSPACE_ROOT": str(tmp_workspace),
    }
    return load_task_context(task, Settings.from_env(env), gateway=FakeGateway(responses))


# --- C 类 ---


def test_survey_writes_the_canonical_expert_file_and_returns_matching_markdown(
    base_env: dict[str, str], five_papers: list[str], papers_root: Path, tmp_workspace: Path
) -> None:
    long_body = (
        "We analyse satellite imagery to quantify how urban green space coverage correlates "
        "with land surface temperature across districts, with repeated methodology discussion "
        "and explicit comparisons of NDVI, thermal bands and regression design. " * 40
    )
    write_paper(
        papers_root,
        P1,
        title="A very long methods appendix about urban green space and surface temperature",
        author="Zhang, Wei; Li, Na",
        keywords="remote sensing, urban heat, green space",
        abstract="We quantify how urban green space coverage correlates with land surface temperature.",
        body_paragraphs=[long_body],
    )
    # 其余四篇换成题目，让这道题里有五篇材料但第一篇明显超出预算
    markdown = good_survey_markdown()
    task = survey_task(five_papers)
    context = context_for(base_env, task, [markdown], papers_root, tmp_workspace)
    result = solve_survey(task, context)

    assert set(result) == {"case_id", "path", "markdown"}
    assert result["case_id"] == "C-01"
    assert result["path"] == f"data/expert/C-01-综述-{TEAM}.md"
    # handler 会 strip 掉首尾空白；关键不变量是 JSON 正文与落盘文件逐字一致
    assert result["markdown"] == markdown.strip()

    written = (tmp_workspace / "data" / "expert" / f"C-01-综述-{TEAM}.md").read_text("utf-8")
    assert written == result["markdown"]
    assert len(markdown.strip()) >= MIN_BODY_CHARACTERS


def test_survey_does_not_call_the_model_twice_when_the_draft_is_clean(
    base_env: dict[str, str], five_papers: list[str], papers_root: Path, tmp_workspace: Path
) -> None:
    task = survey_task(five_papers)
    fake = FakeGateway([good_survey_markdown()])
    env = {
        **base_env,
        "RESEARCH_PAPERS_ROOT": str(papers_root),
        "RESEARCH_WORKSPACE_ROOT": str(tmp_workspace),
    }
    context = load_task_context(task, Settings.from_env(env), gateway=fake)
    solve_survey(task, context)
    assert len(fake.calls) == 1
    assert fake.calls[0][0] == "survey.generate"


def test_survey_runs_one_repair_round_when_citations_have_problems(
    base_env: dict[str, str], five_papers: list[str], papers_root: Path, tmp_workspace: Path
) -> None:
    # broken 够长（过长度闸）但引文有悬空编号，才能测到引文修复轮而不是长度重问
    filler = long_sentence("用来把初稿撑过 800 字符下限的填充陈述")
    broken = (
        "# 综述\n\n"
        + (filler + "。") * 20
        + "\n\n"
        + cited("引用了一个参考文献里不存在的编号", 9)
        + "\n\n## 参考文献\n\n"
        f"[1] 张三. 标题. {P1}\n"
    )
    task = survey_task(five_papers)
    fake = FakeGateway([broken, good_survey_markdown()])
    env = {
        **base_env,
        "RESEARCH_PAPERS_ROOT": str(papers_root),
        "RESEARCH_WORKSPACE_ROOT": str(tmp_workspace),
    }
    context = load_task_context(task, Settings.from_env(env), gateway=fake)
    result = solve_survey(task, context)
    assert [stage for stage, _ in fake.calls] == ["survey.generate", "survey.repair"]
    assert result["markdown"] == good_survey_markdown().strip()


def test_survey_keeps_the_original_when_the_repair_is_not_better(
    base_env: dict[str, str], five_papers: list[str], papers_root: Path, tmp_workspace: Path
) -> None:
    """修复不得反而破坏正文：问题没变少就保留原稿。"""
    task = survey_task(five_papers)
    # 初稿本身合规长度，但引文有问题（悬空编号）；修复稿更长却问题更多（没有任何引用）
    filler = long_sentence("用来把稿子撑到最小长度要求的填充陈述")
    broken = (
        "# 综述\n\n"
        + (filler + "。") * 22
        + "\n\n"
        + cited("引用了一个参考文献里不存在的编号", 9)
        + "\n\n## 参考文献\n\n"
        f"[1] 张三. 标题. {P1}\n"
    )
    # 修复稿必须问题**更多**，否则会被采纳：既有悬空编号，又有打包引用，
    # 还有两处没有实质上下文的裸引用
    worse = (
        "# 综述\n\n"
        + (filler + "。") * 20
        + "\n\n"
        + cited("一次打包引用了三个不存在的编号", 7)
        + "[8, 9, 10]\n\n[11] [12]\n\n"
        + "## 参考文献\n\n"
        f"[1] 张三. 标题. {P1}\n"
    )
    context = context_for(base_env, task, [broken, worse], papers_root, tmp_workspace)
    result = solve_survey(task, context)
    assert result["markdown"] == broken.strip()


def test_survey_strips_a_code_fence_the_model_added(
    base_env: dict[str, str], five_papers: list[str], papers_root: Path, tmp_workspace: Path
) -> None:
    fenced = "```markdown\n" + good_survey_markdown() + "```"
    task = survey_task(five_papers)
    context = context_for(base_env, task, [fenced], papers_root, tmp_workspace)
    result = solve_survey(task, context)
    assert not result["markdown"].startswith("```")
    assert "```" not in result["markdown"]


def test_survey_without_usable_materials_raises_instead_of_returning_a_shell(
    base_env: dict[str, str], papers_root: Path, tmp_workspace: Path
) -> None:
    task = survey_task([])
    context = context_for(base_env, task, [], papers_root, tmp_workspace)
    with pytest.raises(MaterialUnavailableError):
        solve_survey(task, context)


def test_survey_with_only_unparseable_materials_raises(
    base_env: dict[str, str], papers_root: Path, tmp_workspace: Path
) -> None:
    from tests.fixtures.papers import write_scanned_pdf

    write_scanned_pdf(papers_root, "扫描版.pdf")
    task = survey_task(["扫描版.pdf"])
    context = context_for(base_env, task, [], papers_root, tmp_workspace)
    with pytest.raises(MaterialUnavailableError, match=re.escape("扫描版.pdf")):
        solve_survey(task, context)


def test_survey_with_fewer_than_five_papers_does_not_demand_a_fifth(
    base_env: dict[str, str], papers_root: Path, tmp_workspace: Path
) -> None:
    """材料不足时如实记录问题，不捏造第 5 篇。"""
    from tests.fixtures.papers import write_paper

    for name in (P1, P2):
        write_paper(
            papers_root,
            name,
            title=f"A study {name}",
            body_paragraphs=[long_sentence("body evidence for the survey")],
        )
    # 只有两篇授权材料：初稿必须本身零引文问题，才不会触发修复轮
    two_paper_md = (
        "# 综述\n\n"
        + cited("第一项研究采用的方法与数据来源", 1)
        + cited("第二项研究采用的方法与数据来源", 2)
        + cited("两项研究在样本规模与采样频率上的差异", 1)
        + "\n\n"
        + cited("第一项研究的主要局限是缺少夜间观测", 1)
        + cited("第二项研究的主要局限是空间覆盖有限", 2)
        + cited("二者结论适用边界的差别由此产生", 2)
        + "\n\n"
        + cited("两项研究在指标定义上并不一致", 1)
        + cited("这种不一致直接妨碍了跨研究比较", 2)
        + cited("后续工作应当先统一评测协议再比较", 1)
        + "\n\n"
        + cited("第一项研究给出的效应量与其置信区间", 1)
        + cited("第二项研究给出的效应量与其置信区间", 2)
        + cited("两者在方法学上的互补之处与共同缺口", 2)
        + cited("把这些缺口作为下一步的研究起点", 1)
        + cited("在统一协议下重跑两项研究的原始数据", 2)
        + cited("这样得到的比较结果才具备可解释性", 1)
        + "\n\n## 参考文献\n\n[1] a. "
        + P1
        + "\n[2] b. "
        + P2
        + "\n"
    )
    task = survey_task([P1, P2])
    fake = FakeGateway([two_paper_md])
    env = {
        **base_env,
        "RESEARCH_PAPERS_ROOT": str(papers_root),
        "RESEARCH_WORKSPACE_ROOT": str(tmp_workspace),
    }
    context = load_task_context(task, Settings.from_env(env), gateway=fake)
    result = solve_survey(task, context)
    # 只有一轮生成，没有因为「不足 5 篇」而反复要求模型补篇
    assert len(fake.calls) == 1
    assert result["markdown"] == two_paper_md.strip()


def test_survey_prompt_lists_materials_with_citation_numbers(
    base_env: dict[str, str], five_papers: list[str], papers_root: Path, tmp_workspace: Path
) -> None:
    from research_assistant.documents.evidence import build_evidence_pack
    from research_assistant.documents.extractor import extract_documents
    from research_assistant.runtime.paths import WorkspacePaths

    task = survey_task(five_papers)
    workspace = WorkspacePaths(root=tmp_workspace, team=TEAM, papers_root=papers_root)
    pack = build_evidence_pack(extract_documents(five_papers, workspace).documents)
    prompt = build_user_prompt(task, pack)
    for name in five_papers:
        assert name in prompt
    assert "[1]" in prompt and "[5]" in prompt
    assert "只依据本题指定论文" in prompt


# --- E 类 ---


def test_idea_writes_the_canonical_expert_file(
    base_env: dict[str, str], five_papers: list[str], papers_root: Path, tmp_workspace: Path
) -> None:
    markdown = good_idea_markdown()
    task = idea_task(five_papers)
    context = context_for(base_env, task, [markdown], papers_root, tmp_workspace)
    result = solve_idea(task, context)
    assert set(result) == {"case_id", "path", "markdown"}
    assert result["path"] == f"data/expert/E-01-灵感Idea报告-{TEAM}.md"
    assert result["markdown"] == markdown.strip()
    written = (tmp_workspace / "data" / "expert" / f"E-01-灵感Idea报告-{TEAM}.md").read_text(
        "utf-8"
    )
    assert written == result["markdown"]


def test_idea_does_not_use_the_legacy_structure(
    base_env: dict[str, str], five_papers: list[str], papers_root: Path, tmp_workspace: Path
) -> None:
    task = idea_task(five_papers)
    context = context_for(base_env, task, [good_idea_markdown()], papers_root, tmp_workspace)
    result = solve_idea(task, context)
    for legacy in ("research_question", "technical_route", "metrics"):
        assert legacy not in result


def test_idea_without_materials_raises(
    base_env: dict[str, str], papers_root: Path, tmp_workspace: Path
) -> None:
    task = idea_task([])
    context = context_for(base_env, task, [], papers_root, tmp_workspace)
    with pytest.raises(MaterialUnavailableError):
        solve_idea(task, context)


def test_idea_runs_a_repair_round_when_sections_are_missing(
    base_env: dict[str, str], five_papers: list[str], papers_root: Path, tmp_workspace: Path
) -> None:
    incomplete = (
        "# 方案\n\n"
        + (long_sentence("只写了动机与背景，缺了其余全部要求的小节") + "。") * 24
        + "\n"
    )
    task = idea_task(five_papers)
    fake = FakeGateway([incomplete, good_idea_markdown()])
    env = {
        **base_env,
        "RESEARCH_PAPERS_ROOT": str(papers_root),
        "RESEARCH_WORKSPACE_ROOT": str(tmp_workspace),
    }
    context = load_task_context(task, Settings.from_env(env), gateway=fake)
    result = solve_idea(task, context)
    assert [stage for stage, _ in fake.calls] == ["idea.generate", "idea.repair"]
    assert result["markdown"] == good_idea_markdown().strip()


# --- Idea 自检器 ---


def test_a_complete_idea_report_has_no_problems() -> None:
    assert check_idea_report(good_idea_markdown()).problems == ()


def test_every_required_section_is_checked() -> None:
    for missing in REQUIRED_SECTIONS:
        sections = [s for s in REQUIRED_SECTIONS if s != missing]
        md = (
            "# 方案\n\n"
            + "".join(f"## {name}\n\n{long_sentence(name)}\n\n" for name in sections)
            + "## 证据边界\n\n模拟观察尚未验证，所提改进尚未执行。\n"
        )
        check = check_idea_report(md)
        assert missing in check.missing_sections, missing


def test_missing_boundary_statements_are_detected() -> None:
    """整句删掉两层边界意思后必须被检出（只删半句会留下语义等价表达，属正常放行）。"""
    md = good_idea_markdown().replace(
        "上述模拟观察尚未验证，所提改进尚未执行，全部效果均为待验证的预期，"
        "不得据此认为已经取得实验结论。",
        "上述效果均已在实验中取得，结论可直接采用。",
    )
    assert md != good_idea_markdown()
    check = check_idea_report(md)
    assert set(check.missing_boundary_statements) == set(REQUIRED_BOUNDARY_STATEMENTS)


def test_semantically_equivalent_boundary_wording_is_accepted() -> None:
    """实测教训：模型写「未经任何实验取得」同样合规，字面匹配会触发无谓的修复轮。"""
    md = good_idea_markdown().replace(
        "上述模拟观察尚未验证，所提改进尚未执行",
        "上述数值未经任何实验取得，所提改进方案也还没有执行",
    )
    assert check_idea_report(md).missing_boundary_statements == ()


def test_quantitative_metrics_accepts_realistic_notation() -> None:
    """实测教训：R²（0.52±0.02）、RMSE 3.2K、高4个百分点 都是合格的量化指标写法，
    早先按固定单位后缀的正则会把它们全部漏判为缺失。"""
    for notation in (
        "主要指标：R²（基线组预期0.52±0.02，改进组预期0.60±0.02）。",
        "均方根误差 RMSE（单位K，基线组预期3.2K，改进组预期2.8K）。",
        "边界 F1-score 预期改进组比基线组高4个百分点。",
        "以 Macro-F1 为主指标，预期相对基线提升 3.5%。",
    ):
        md = good_idea_markdown().replace(
            "以 Macro-F1 为主指标，预期相对基线提升 3.5%，同时报告 Accuracy 与运行时间，"
            "并给出 95% 置信区间。",
            notation,
        )
        assert check_idea_report(md).has_quantitative_metrics is True, notation


def test_quantitative_metrics_still_rejects_a_purely_qualitative_section() -> None:
    md = good_idea_markdown().replace(
        "以 Macro-F1 为主指标，预期相对基线提升 3.5%，同时报告 Accuracy 与运行时间，"
        "并给出 95% 置信区间。",
        "将从多个角度综合评估方案效果，并给出定性的好坏判断。",
    )
    assert check_idea_report(md).has_quantitative_metrics is False


def test_boundary_statements_tolerate_inserted_whitespace() -> None:
    """自检先把空格全部去掉再比对，因此排版空格不该造成漏检。"""
    md = good_idea_markdown().replace("模拟观察尚未验证", "模拟观察 尚未验证")
    assert check_idea_report(md).missing_boundary_statements == ()


def test_generic_template_phrasing_is_flagged() -> None:
    md = (
        good_idea_markdown().replace("## 研究问题与边界", "## 研究问题与边界")
        + "\n随着深度学习的快速发展，本方案具有重要意义。\n"
    )
    check = check_idea_report(md)
    assert check.generic_template_hits
    assert any("generic_template" in p for p in check.problems)


def test_missing_baseline_comparison_is_flagged() -> None:
    md = good_idea_markdown().replace(
        "基线组采用原始 TF-IDF 加 softmax 分类器，改进组在此之上加入所提机制，"
        "其余训练条件、数据划分与随机种子保持一致，两组直接对比 Macro-F1。",
        long_sentence("只描述了所提方法本身的内部结构与设计动机") + "。",
    )
    check = check_idea_report(md)
    assert check.has_baseline_comparison is False
    assert any("基线" in p for p in check.problems)


def test_missing_quantitative_metrics_is_flagged() -> None:
    md = good_idea_markdown().replace(
        "以 Macro-F1 为主指标，预期相对基线提升 3.5%，同时报告 Accuracy 与运行时间，"
        "并给出 95% 置信区间。",
        long_sentence("只说会评估效果，但没有给出任何可测量的指标名称或数值") + "。",
    )
    check = check_idea_report(md)
    assert check.has_quantitative_metrics is False
    assert any("量化指标" in p for p in check.problems)


def test_short_technical_content_is_flagged() -> None:
    md = (
        "# 方案\n\n"
        + "".join(f"## {name}\n\n短。\n\n" for name in REQUIRED_SECTIONS)
        + "## 证据边界\n\n模拟观察尚未验证，所提改进尚未执行。\n"
    )
    check = check_idea_report(md)
    assert check.technical_characters < 200
    assert any("技术内容" in p for p in check.problems)


def test_repeated_sentences_do_not_count_toward_technical_length() -> None:
    filler = long_sentence("重复的句子用来凑篇幅但不该被计入技术篇幅") + "。"
    original = (
        "## 研究问题与边界\n\n"
        + long_sentence("研究问题的界定")
        + "；"
        + long_sentence("不纳入范围的部分")
        + "。"
    )
    assert original in good_idea_markdown()
    md = good_idea_markdown().replace(
        original,
        "## 研究问题与边界\n\n" + filler * 12,
    )
    check = check_idea_report(md)
    assert check.repeated_sentence_count >= 11
    baseline = check_idea_report(good_idea_markdown()).technical_characters
    assert check.technical_characters < baseline + len(filler) * 2


def test_references_and_code_do_not_count_toward_technical_length() -> None:
    md = good_idea_markdown()
    without = check_idea_report(md).technical_characters
    padded = md.replace(
        "## 参考文献",
        "## 附录\n\n```python\n" + "x = 1\n" * 200 + "```\n\n## 参考文献",
    )
    assert check_idea_report(padded).technical_characters == without


def test_survey_markdown_shorter_than_800_characters_is_rejected_by_the_builder() -> None:
    from research_assistant.contracts.results import survey_result
    from research_assistant.errors import TaskContractError

    with pytest.raises(TaskContractError, match="800"):
        survey_result("C-01", path="data/expert/x.md", markdown="# 太短\n\n内容不足。")


def test_result_json_is_parseable_and_utf8(
    base_env: dict[str, str], five_papers: list[str], papers_root: Path, tmp_workspace: Path
) -> None:
    from research_assistant.contracts.results import serialize_result

    task = survey_task(five_papers)
    context = context_for(base_env, task, [good_survey_markdown()], papers_root, tmp_workspace)
    result = solve_survey(task, context)
    data = serialize_result(result)
    assert json.loads(data.decode("utf-8"))["case_id"] == "C-01"
    assert "城市绿地".encode() in data


# --- 上下文超限时的证据包缩减重试（Q01：正式模型上下文上限未知） ---


def _context_overflow_error() -> Exception:
    from research_assistant.errors import GatewayError

    return GatewayError(
        "gateway returned HTTP 400 during survey.generate: "
        "prompt is too long: 400000 tokens > max context length"
    )


def test_survey_shrinks_the_evidence_pack_when_the_context_is_too_small(
    base_env: dict[str, str],
    five_papers: list[str],
    papers_root: Path,
    tmp_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """上下文超限时把每篇证据预算减半重建提示，再试一次且仅一次。"""
    from research_assistant.handlers import survey as survey_module

    budgets: list[int] = []
    real_pack = survey_module.build_evidence_pack

    def recording_pack(documents, *, max_chars_per_document: int):
        budgets.append(max_chars_per_document)
        return real_pack(documents, max_chars_per_document=max_chars_per_document)

    monkeypatch.setattr(survey_module, "build_evidence_pack", recording_pack)
    markdown = good_survey_markdown()
    task = survey_task(five_papers)
    fake = FakeGateway([_context_overflow_error(), markdown])
    env = {
        **base_env,
        "RESEARCH_PAPERS_ROOT": str(papers_root),
        "RESEARCH_WORKSPACE_ROOT": str(tmp_workspace),
    }
    context = load_task_context(task, Settings.from_env(env), gateway=fake)
    result = solve_survey(task, context)

    assert budgets == [6000, 3000], budgets
    assert [stage for stage, _ in fake.calls] == ["survey.generate", "survey.generate.shrunk"]
    assert result["markdown"] == markdown.strip()


def test_idea_shrinks_the_evidence_pack_when_the_context_is_too_small(
    base_env: dict[str, str], five_papers: list[str], papers_root: Path, tmp_workspace: Path
) -> None:
    long_body = (
        "We benchmark convolutional and transformer architectures for semantic segmentation "
        "of satellite imagery and discuss training data construction and boundary accuracy in "
        "detail across many ablations. " * 40
    )
    write_paper(
        papers_root,
        P1,
        title="A very long methods appendix about urban green space and surface temperature",
        abstract="We quantify how urban green space coverage correlates with land surface temperature.",
        body_paragraphs=[long_body],
    )
    task = idea_task(five_papers)
    fake = FakeGateway([_context_overflow_error(), good_idea_markdown()])
    env = {
        **base_env,
        "RESEARCH_PAPERS_ROOT": str(papers_root),
        "RESEARCH_WORKSPACE_ROOT": str(tmp_workspace),
    }
    context = load_task_context(task, Settings.from_env(env), gateway=fake)
    result = solve_idea(task, context)
    assert result["case_id"] == "E-01"
    assert [stage for stage, _ in fake.calls] == ["idea.generate", "idea.generate.shrunk"]
    assert len(fake.calls[1][1][-1].content) < len(fake.calls[0][1][-1].content)


def test_survey_does_not_shrink_on_unrelated_gateway_errors(
    base_env: dict[str, str], five_papers: list[str], papers_root: Path, tmp_workspace: Path
) -> None:
    """只有上下文超限才缩减；普通 502 不得触发无意义的二次调用。"""
    from research_assistant.errors import GatewayError

    task = survey_task(five_papers)
    fake = FakeGateway([GatewayError("gateway returned HTTP 502 during survey.generate")])
    env = {
        **base_env,
        "RESEARCH_PAPERS_ROOT": str(papers_root),
        "RESEARCH_WORKSPACE_ROOT": str(tmp_workspace),
    }
    context = load_task_context(task, Settings.from_env(env), gateway=fake)
    with pytest.raises(GatewayError):
        solve_survey(task, context)
    assert len(fake.calls) == 1


def test_shrink_budget_never_drops_below_a_usable_floor() -> None:
    from research_assistant.documents.evidence import shrink_budget

    assert shrink_budget(6000) == 3000
    assert shrink_budget(2200) == 1100
    assert shrink_budget(1600) == 800
    assert shrink_budget(100) == 800  # 有下限，不能把证据削到无法支撑引用


def test_context_error_detection_matches_real_gateway_wording() -> None:
    from research_assistant.documents.evidence import is_context_length_error

    for message in [
        "prompt is too long: 400000 tokens > max context length",
        "This request exceeds the maximum context length of the model.",
        "context_length_exceeded",
        "maximum context reached",
        "Request too large",
    ]:
        assert is_context_length_error(message), message
    assert not is_context_length_error("gateway returned HTTP 502 during survey.generate")
    assert not is_context_length_error("upstream timeout")
