from __future__ import annotations

import json
import math

import pytest

from research_assistant.contracts.results import (
    ALLOWED_TOP_LEVEL_FIELDS,
    MIN_BODY_CHARACTERS,
    Metric,
    chat_result,
    idea_result,
    reproduction_result,
    retrieval_result,
    serialize_result,
    survey_result,
    validate_result_shape,
)
from research_assistant.contracts.task import TaskFunction
from research_assistant.errors import TaskContractError


def test_allowed_fields_match_the_spec_exactly() -> None:
    assert ALLOWED_TOP_LEVEL_FIELDS[TaskFunction.CHAT] == frozenset(
        {"case_id", "answer", "clarifying_question"}
    )
    assert ALLOWED_TOP_LEVEL_FIELDS[TaskFunction.LITERATURE_RETRIEVAL] == frozenset(
        {"case_id", "papers"}
    )
    assert ALLOWED_TOP_LEVEL_FIELDS[TaskFunction.SURVEY] == frozenset(
        {"case_id", "path", "markdown"}
    )
    assert ALLOWED_TOP_LEVEL_FIELDS[TaskFunction.IDEA] == frozenset({"case_id", "path", "markdown"})
    assert ALLOWED_TOP_LEVEL_FIELDS[TaskFunction.REPRODUCTION] == frozenset(
        {"case_id", "status", "metrics", "figures"}
    )


def test_chat_result_has_exactly_three_fields_in_order() -> None:
    result = chat_result(
        "A-01", answer="光照强度影响光合速率。", clarifying_question="研究哪种植物？"
    )
    assert list(result) == ["case_id", "answer", "clarifying_question"]
    assert result["case_id"] == "A-01"


@pytest.mark.parametrize("answer", ["", "   ", "\n\t "])
def test_chat_rejects_blank_answer(answer: str) -> None:
    with pytest.raises(TaskContractError, match="answer"):
        chat_result("A-01", answer=answer, clarifying_question="问一个范围问题？")


@pytest.mark.parametrize("question", ["", "   "])
def test_chat_rejects_blank_clarifying_question(question: str) -> None:
    with pytest.raises(TaskContractError, match="clarifying_question"):
        chat_result("A-01", answer="回答", clarifying_question=question)


def test_chat_rejects_non_string_fields() -> None:
    with pytest.raises(TaskContractError):
        chat_result("A-01", answer=123, clarifying_question="q")  # type: ignore[arg-type]


def test_retrieval_accepts_an_empty_list() -> None:
    """没有相关文献时返回空数组是合法的，不得凑固定篇数。"""
    assert retrieval_result("B-01", papers=[]) == {"case_id": "B-01", "papers": []}


def test_retrieval_preserves_order_and_original_filenames() -> None:
    papers = [
        {"filename": "深度 学习+v2.pdf", "reason": "正文第 3 节给出同任务基线。"},
        {"filename": "Another Paper.pdf", "reason": "数据集一致。"},
    ]
    result = retrieval_result("B-02", papers=papers)
    assert [p["filename"] for p in result["papers"]] == ["深度 学习+v2.pdf", "Another Paper.pdf"]


def test_retrieval_rejects_extra_keys_in_elements() -> None:
    with pytest.raises(TaskContractError, match="score"):
        retrieval_result("B-01", papers=[{"filename": "a.pdf", "reason": "r", "score": 0.9}])


def test_retrieval_rejects_missing_keys() -> None:
    with pytest.raises(TaskContractError, match="reason"):
        retrieval_result("B-01", papers=[{"filename": "a.pdf"}])


def test_retrieval_rejects_blank_filename_or_reason() -> None:
    with pytest.raises(TaskContractError):
        retrieval_result("B-01", papers=[{"filename": " ", "reason": "r"}])
    with pytest.raises(TaskContractError):
        retrieval_result("B-01", papers=[{"filename": "a.pdf", "reason": ""}])


def long_body(n: int = MIN_BODY_CHARACTERS) -> str:
    return "# 综述\n\n" + "证据段落。" * ((n // 5) + 1)


def test_survey_result_keeps_path_and_markdown() -> None:
    body = long_body()
    result = survey_result("C-01", path="data/expert/C-01-综述-队.md", markdown=body)
    assert result["markdown"] == body
    assert result["path"] == "data/expert/C-01-综述-队.md"


def test_idea_result_has_the_same_shape_as_survey() -> None:
    result = idea_result("E-01", path="data/expert/E-01-灵感Idea报告-队.md", markdown=long_body())
    assert list(result) == ["case_id", "path", "markdown"]


def test_idea_result_does_not_use_the_legacy_structure() -> None:
    result = idea_result("E-01", path="p", markdown=long_body())
    for legacy in ("research_question", "technical_route", "metrics"):
        assert legacy not in result


@pytest.mark.parametrize("markdown", ["", "太短", "x" * (MIN_BODY_CHARACTERS - 1)])
def test_survey_rejects_body_shorter_than_800_characters(markdown: str) -> None:
    with pytest.raises(TaskContractError, match="800"):
        survey_result("C-01", path="data/expert/x.md", markdown=markdown)


def test_survey_rejects_absolute_path() -> None:
    with pytest.raises(TaskContractError, match="path"):
        survey_result("C-01", path="/output/data/expert/x.md", markdown=long_body())


@pytest.mark.parametrize("path", ["../expert/x.md", "data/../secret.md", "data\\expert\\x.md"])
def test_survey_rejects_unsafe_path(path: str) -> None:
    with pytest.raises(TaskContractError, match="path"):
        survey_result("C-01", path=path, markdown=long_body())


def three_metrics() -> list[Metric]:
    return [
        Metric(
            name="Macro-F1 / 分数", unit="", reference=0.82, observed=0.80, relative_error=0.024
        ),
        Metric(name="Accuracy / %", unit="%", reference=91.0, observed=90.0, relative_error=0.011),
        Metric(name="运行时间 / 秒", unit="秒", reference=1.5, observed=1.7, relative_error=0.133),
    ]


@pytest.mark.parametrize("status", ["completed", "failed", "materials_missing"])
def test_reproduction_accepts_the_three_legal_statuses(status: str) -> None:
    result = reproduction_result("D-01", status=status, metrics=three_metrics(), figures=[])
    assert result["status"] == status


def test_reproduction_rejects_unknown_status() -> None:
    with pytest.raises(TaskContractError, match="status"):
        reproduction_result("D-01", status="evidence_pending", metrics=three_metrics(), figures=[])


def test_reproduction_requires_exactly_three_metrics() -> None:
    with pytest.raises(TaskContractError, match="three"):
        reproduction_result("D-01", status="failed", metrics=three_metrics()[:2], figures=[])


def test_failed_reproduction_keeps_all_metric_keys_with_null_observed() -> None:
    metrics = [
        Metric(
            name="公平性 Jain 指数 / 分数",
            unit="",
            reference=0.91,
            observed=None,
            relative_error=None,
        ),
        Metric(name="归一化吞吐 / %", unit="%", reference=88.0, observed=None, relative_error=None),
        Metric(
            name="平均等待时间 / ms", unit="ms", reference=120.0, observed=None, relative_error=None
        ),
    ]
    result = reproduction_result("D-05", status="failed", metrics=metrics, figures=[])
    payload = json.loads(serialize_result(result))
    assert len(payload["metrics"]) == 3
    for item in payload["metrics"]:
        assert set(item) == {"name", "unit", "reference", "observed", "relative_error"}
        assert item["observed"] is None
    assert payload["figures"] == []


def test_metric_names_and_units_are_preserved_verbatim() -> None:
    result = reproduction_result("D-01", status="completed", metrics=three_metrics(), figures=[])
    assert [m["name"] for m in result["metrics"]] == [
        "Macro-F1 / 分数",
        "Accuracy / %",
        "运行时间 / 秒",
    ]


def test_figures_must_be_relative_strings() -> None:
    with pytest.raises(TaskContractError, match="figures"):
        reproduction_result(
            "D-01",
            status="completed",
            metrics=three_metrics(),
            figures=["/output/data/figure/team/a.png"],
        )


def test_validate_result_shape_rejects_extra_top_level_fields() -> None:
    result = chat_result("A-01", answer="a", clarifying_question="q")
    result["score"] = 5
    with pytest.raises(TaskContractError, match="score"):
        validate_result_shape(TaskFunction.CHAT, result)


def test_validate_result_shape_rejects_missing_fields() -> None:
    with pytest.raises(TaskContractError, match="clarifying_question"):
        validate_result_shape(TaskFunction.CHAT, {"case_id": "A-01", "answer": "a"})


def test_validate_result_shape_rejects_wrong_category_shape() -> None:
    with pytest.raises(TaskContractError):
        validate_result_shape(
            TaskFunction.SURVEY, chat_result("C-01", answer="a", clarifying_question="q")
        )


def test_serialize_keeps_non_ascii_characters_readable() -> None:
    data = serialize_result(chat_result("A-01", answer="光照", clarifying_question="哪种植物？"))
    assert "光照".encode() in data
    assert b"\\u" not in data


def test_nan_metric_is_rejected_at_construction() -> None:
    """构造器就先挡住 NaN，不用等到序列化。"""
    metrics = [
        Metric(
            name="Macro-F1 / 分数", unit="", reference=0.8, observed=math.nan, relative_error=None
        ),
        Metric(name="Accuracy / %", unit="%", reference=90.0, observed=1.0, relative_error=None),
        Metric(name="运行时间 / 秒", unit="秒", reference=1.0, observed=2.0, relative_error=None),
    ]
    with pytest.raises(TaskContractError, match="finite"):
        reproduction_result("D-01", status="completed", metrics=metrics, figures=[])


def test_infinity_metric_is_rejected_at_construction() -> None:
    metrics = [
        Metric(
            name="Macro-F1 / 分数", unit="", reference=0.8, observed=math.inf, relative_error=None
        ),
        Metric(name="Accuracy / %", unit="%", reference=90.0, observed=1.0, relative_error=None),
        Metric(name="运行时间 / 秒", unit="秒", reference=1.0, observed=2.0, relative_error=None),
    ]
    with pytest.raises(TaskContractError, match="finite"):
        reproduction_result("D-01", status="completed", metrics=metrics, figures=[])


def test_serialize_rejects_nan_even_if_a_builder_is_bypassed() -> None:
    """allow_nan=False 是最后一道防线。"""
    with pytest.raises(ValueError):
        serialize_result({"case_id": "D-01", "observed": math.nan})


def test_serialize_rejects_numeric_strings_becoming_numbers() -> None:
    """observed 必须是有限 JSON 数字或 null，不能是数字字符串。"""
    metrics = [
        Metric(
            name="Accuracy / %",
            unit="%",
            reference=90.0,
            observed="90.0",  # type: ignore[arg-type]
            relative_error=None,
        ),
        Metric(name="Macro-F1 / 分数", unit="", reference=0.8, observed=0.7, relative_error=None),
        Metric(name="运行时间 / 秒", unit="秒", reference=1.0, observed=2.0, relative_error=None),
    ]
    with pytest.raises(TaskContractError, match="observed"):
        reproduction_result("D-01", status="completed", metrics=metrics, figures=[])


def test_serialized_output_has_no_code_fence_or_prefix() -> None:
    text = serialize_result(chat_result("A-01", answer="a", clarifying_question="q")).decode(
        "utf-8"
    )
    assert text.startswith("{")
    assert text.endswith("}")
    assert "```" not in text
