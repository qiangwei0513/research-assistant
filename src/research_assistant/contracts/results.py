"""五类输出的构造器与字段白名单。

构造器是唯一能产出答案 JSON 的入口：任何额外顶层字段、空内容、非法数字都在这里被挡住。
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from research_assistant.contracts.task import TaskFunction
from research_assistant.errors import TaskContractError

MIN_BODY_CHARACTERS = 800

ALLOWED_TOP_LEVEL_FIELDS: Mapping[TaskFunction, frozenset[str]] = MappingProxyType(
    {
        TaskFunction.CHAT: frozenset({"case_id", "answer", "clarifying_question"}),
        TaskFunction.LITERATURE_RETRIEVAL: frozenset({"case_id", "papers"}),
        TaskFunction.SURVEY: frozenset({"case_id", "path", "markdown"}),
        TaskFunction.IDEA: frozenset({"case_id", "path", "markdown"}),
        TaskFunction.REPRODUCTION: frozenset({"case_id", "status", "metrics", "figures"}),
    }
)

REPRODUCTION_STATUSES: frozenset[str] = frozenset({"completed", "failed", "materials_missing"})

PAPER_ITEM_FIELDS: frozenset[str] = frozenset({"filename", "reason"})
# D 类 figures 是**对象数组**，不是字符串数组；键集由官方输出规范固定
FIGURE_FIELDS: tuple[str, ...] = ("path", "caption", "x_label", "y_label", "legend")
FIGURE_PATH_PREFIX = "data/figure/"
METRIC_FIELDS: tuple[str, ...] = ("name", "unit", "reference", "observed", "relative_error")


def _require_non_blank(value: Any, field: str, case_id: str) -> str:
    if not isinstance(value, str):
        raise TaskContractError(f"{field} must be a string", case_id=case_id)
    if not value.strip():
        raise TaskContractError(f"{field} must be a non-empty string", case_id=case_id)
    return value


def _optional_finite(value: Any, field: str, case_id: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TaskContractError(
            f"{field} must be a finite JSON number or null, not {type(value).__name__}",
            case_id=case_id,
        )
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        raise TaskContractError(f"{field} must be finite, got {value!r}", case_id=case_id)
    return number


def _require_relative_path(value: Any, field: str, case_id: str) -> str:
    if not isinstance(value, str):
        raise TaskContractError(f"{field} must be a string", case_id=case_id)
    if not value.strip():
        raise TaskContractError(f"{field} must not be empty", case_id=case_id)
    if value.startswith("/"):
        raise TaskContractError(f"{field} must be relative to the workspace root", case_id=case_id)
    if "\\" in value:
        raise TaskContractError(f"{field} must use forward slashes", case_id=case_id)
    if any(part == ".." for part in value.split("/")):
        raise TaskContractError(f"{field} must not traverse parents", case_id=case_id)
    return value


@dataclass(frozen=True)
class Metric:
    name: str
    unit: str
    reference: float | None
    observed: float | None
    relative_error: float | None

    def to_json(self, *, case_id: str) -> dict[str, Any]:
        return {
            "name": _require_non_blank(self.name, "metric name", case_id),
            "unit": self.unit if isinstance(self.unit, str) else "",
            "reference": _optional_finite(self.reference, "reference", case_id),
            "observed": _optional_finite(self.observed, "observed", case_id),
            "relative_error": _optional_finite(self.relative_error, "relative_error", case_id),
        }


def chat_result(case_id: str, *, answer: str, clarifying_question: str) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "answer": _require_non_blank(answer, "answer", case_id),
        "clarifying_question": _require_non_blank(
            clarifying_question, "clarifying_question", case_id
        ),
    }


def retrieval_result(case_id: str, *, papers: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    items: list[dict[str, str]] = []
    for index, paper in enumerate(papers):
        if not isinstance(paper, Mapping):
            raise TaskContractError(f"papers[{index}] must be an object", case_id=case_id)
        extra = set(paper) - PAPER_ITEM_FIELDS
        if extra:
            raise TaskContractError(
                f"papers[{index}] has disallowed fields: {', '.join(sorted(extra))}",
                case_id=case_id,
            )
        missing = PAPER_ITEM_FIELDS - set(paper)
        if missing:
            raise TaskContractError(
                f"papers[{index}] is missing: {', '.join(sorted(missing))}", case_id=case_id
            )
        items.append(
            {
                "filename": _require_non_blank(
                    paper["filename"], f"papers[{index}].filename", case_id
                ),
                "reason": _require_non_blank(paper["reason"], f"papers[{index}].reason", case_id),
            }
        )
    return {"case_id": case_id, "papers": items}


def _report_result(case_id: str, *, path: str, markdown: str) -> dict[str, Any]:
    if not isinstance(markdown, str):
        raise TaskContractError("markdown must be a string", case_id=case_id)
    if len(markdown.strip()) < MIN_BODY_CHARACTERS:
        raise TaskContractError(
            f"markdown body must be at least {MIN_BODY_CHARACTERS} characters, "
            f"got {len(markdown.strip())}",
            case_id=case_id,
        )
    return {
        "case_id": case_id,
        "path": _require_relative_path(path, "path", case_id),
        "markdown": markdown,
    }


def survey_result(case_id: str, *, path: str, markdown: str) -> dict[str, Any]:
    return _report_result(case_id, path=path, markdown=markdown)


def idea_result(case_id: str, *, path: str, markdown: str) -> dict[str, Any]:
    return _report_result(case_id, path=path, markdown=markdown)


def _require_figure(figure: Any, index: int, case_id: str) -> dict[str, Any]:
    """D 类 figures 是**对象数组**：键集由官方输出规范固定为五个。"""
    if not isinstance(figure, Mapping):
        raise TaskContractError(f"figures[{index}] must be an object", case_id=case_id)
    extra = set(figure) - set(FIGURE_FIELDS)
    if extra:
        raise TaskContractError(
            f"figures[{index}] has disallowed fields: {', '.join(sorted(extra))}",
            case_id=case_id,
        )
    missing = set(FIGURE_FIELDS) - set(figure)
    if missing:
        raise TaskContractError(
            f"figures[{index}] is missing: {', '.join(sorted(missing))}", case_id=case_id
        )
    path = _require_relative_path(figure["path"], f"figures[{index}].path", case_id)
    if not path.startswith(FIGURE_PATH_PREFIX):
        raise TaskContractError(
            f"figures[{index}].path must live under {FIGURE_PATH_PREFIX}<team>/",
            case_id=case_id,
        )
    out: dict[str, Any] = {"path": path}
    for key in FIGURE_FIELDS[1:]:
        value = figure[key]
        out[key] = value if isinstance(value, str) else ""
    return out


def reproduction_result(
    case_id: str,
    *,
    status: str,
    metrics: Sequence[Metric],
    figures: Sequence[Any],
) -> dict[str, Any]:
    if status not in REPRODUCTION_STATUSES:
        raise TaskContractError(
            f"status must be one of {', '.join(sorted(REPRODUCTION_STATUSES))}, got {status!r}",
            case_id=case_id,
        )
    if len(metrics) != 3:
        raise TaskContractError(
            f"metrics must contain exactly three complete entries, got {len(metrics)}",
            case_id=case_id,
        )
    figure_items = [_require_figure(figure, index, case_id) for index, figure in enumerate(figures)]
    return {
        "case_id": case_id,
        "status": status,
        "metrics": [metric.to_json(case_id=case_id) for metric in metrics],
        "figures": figure_items,
    }


def validate_result_shape(function: TaskFunction, result: Mapping[str, Any]) -> None:
    allowed = ALLOWED_TOP_LEVEL_FIELDS[function]
    actual = set(result)
    extra = actual - allowed
    if extra:
        raise TaskContractError(
            f"{function.value} result has disallowed top-level fields: {', '.join(sorted(extra))}"
        )
    missing = allowed - actual
    if missing:
        raise TaskContractError(
            f"{function.value} result is missing required fields: {', '.join(sorted(missing))}"
        )


def serialize_result(payload: Mapping[str, Any]) -> bytes:
    """答案 JSON 的唯一序列化入口：UTF-8、保留中文、拒绝 NaN/Infinity、无围栏。"""
    return json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")


# 官方把三项指标的精确名称与单位直接写进本题 input，形如：
# 「核心指标恰好三项：name=MAE，unit=kW；name=RMSE，unit=kW；name=Inference Latency，unit=ms。」
_METRIC_SPEC = re.compile(
    r"name\s*=\s*(?P<name>[^，,；;。\n]+?)\s*[，,]\s*unit\s*=\s*(?P<unit>[^；;。\n]*)"
)


def parse_metric_specs(task_input: str) -> tuple[tuple[str, str], ...]:
    """按题目 input 原样取出 (name, unit) 序列；不翻译、不归一化、不改写尺度。"""
    return tuple(
        (match.group("name").strip(), match.group("unit").strip())
        for match in _METRIC_SPEC.finditer(task_input)
    )


def placeholder_metrics(task_input: str) -> tuple[Metric, ...]:
    """failed / materials_missing 时用：保留题目指定的三个指标名称与单位，未知值填 null。

    官方要求失败答卷仍保留三项完整指标对象。题目没给出可解析的 name=/unit= 时
    用带序号的占位名并如实记录，绝不臆造官方指标名。
    """
    specs = parse_metric_specs(task_input)
    if len(specs) != 3:
        specs = tuple((f"指标 {i}", "") for i in (1, 2, 3))
    return tuple(
        Metric(name=name, unit=unit, reference=None, observed=None, relative_error=None)
        for name, unit in specs
    )
