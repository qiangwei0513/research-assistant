"""D 类（Reproduction）测试。

最关键的是**差分测试**：把同一批响应分别喂给我们的 extract_source 和官方
`../赛题信息/research-experiments-20261020-source-v1/model_source.py`，
两者的判定与归一化结果必须完全一致——因为平台的来源准入用的就是官方那份逻辑。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from research_assistant.contracts.task import Task
from research_assistant.handlers import reproduction as reproduction_handler
from research_assistant.handlers.reproduction import solve_reproduction
from research_assistant.reproduction.client import ExperimentOutcome, FakeExperimentClient
from research_assistant.reproduction.materials import load_case_materials
from research_assistant.reproduction.source import (
    MAX_SOURCE_BYTES,
    SourceExtractionError,
    extract_source,
    normalize_source,
    save_source,
)
from research_assistant.runtime.context import load_task_context
from research_assistant.runtime.gateway import FakeGateway, GatewayResponse, GatewayUsage
from research_assistant.runtime.settings import Settings
from research_assistant.solve import PersistedResult

TEAM = "测试队伍 A+1"
OFFICIAL_MODEL_SOURCE = (
    Path(__file__).resolve().parents[3]
    / "赛题信息"
    / "research-experiments-20261020-source-v1"
    / "model_source.py"
)

VALID_SOURCE = """import math


def run_experiment(request):
    data = request["data"]
    return {"baseline": None, "candidate": None, "details": {"n": len(data)}}
"""


def chat_response(content: str, *, finish_reason: str = "stop") -> dict[str, Any]:
    """一个**完整**的 chat/completions 响应对象，不是单独的 message.content。"""
    return {
        "id": "chatcmpl-d",
        "object": "chat.completion",
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content, "reasoning": None},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }


def fenced(source: str) -> str:
    return f"这是实现：\n\n```python\n{source}```\n\n以上。"


def gateway_response(content: str, *, finish_reason: str = "stop") -> GatewayResponse:
    """FakeGateway 需要 GatewayResponse：`raw` 必须是**完整**的 chat/completions 对象，
    因为 extract_source 只认完整响应，不认单独的 message.content。"""
    return GatewayResponse(
        content=content,
        finish_reason=finish_reason,
        usage=GatewayUsage(100, 50, 150, None),
        reasoning=None,
        raw=chat_response(content, finish_reason=finish_reason),
    )


# --- 提取规则 ---


def test_accepts_a_bare_complete_source() -> None:
    assert extract_source(chat_response(VALID_SOURCE)) == VALID_SOURCE


def test_accepts_a_source_inside_a_python_fence() -> None:
    assert extract_source(chat_response(fenced(VALID_SOURCE))) == VALID_SOURCE


def test_rejects_a_truncated_completion() -> None:
    with pytest.raises(SourceExtractionError, match="exactly one"):
        extract_source(chat_response(VALID_SOURCE, finish_reason="length"))


def test_rejects_a_missing_entry_point() -> None:
    # extract_source 收集候选后只报「不恰好一份」；具体原因在 normalize_source 层
    with pytest.raises(SourceExtractionError, match="exactly one"):
        extract_source(chat_response("def solve(request):\n    return None\n"))
    with pytest.raises(SourceExtractionError, match="run_experiment"):
        normalize_source("def solve(request):\n    return None\n")


def test_rejects_unparsable_python() -> None:
    with pytest.raises(SourceExtractionError):
        extract_source(chat_response("def run_experiment(request:\n"))


def test_rejects_two_different_implementations_in_one_reply() -> None:
    """不得拼多个回复，也不得从多份实现里挑一份。"""
    other = "def run_experiment(request):\n    return {'a': 1}\n"
    content = f"```python\n{VALID_SOURCE}```\n\n或者：\n\n```python\n{other}```\n"
    with pytest.raises(SourceExtractionError, match="exactly one"):
        extract_source(chat_response(content))


def test_accepts_the_same_implementation_repeated_verbatim() -> None:
    """同一份源码出现两次归一化后是同一个集合元素，仍是「恰好一份」。"""
    content = f"```python\n{VALID_SOURCE}```\n\n```python\n{VALID_SOURCE}```\n"
    assert extract_source(chat_response(content)) == VALID_SOURCE


def test_rejects_empty_and_oversized_source() -> None:
    with pytest.raises(SourceExtractionError, match="exactly one"):
        extract_source(chat_response(""))
    with pytest.raises(SourceExtractionError, match="65536"):
        normalize_source("")
    huge = "def run_experiment(request):\n    x = '" + "a" * MAX_SOURCE_BYTES + "'\n"
    with pytest.raises(SourceExtractionError, match="65536"):
        normalize_source(huge)


def test_rejects_a_non_mapping_response() -> None:
    with pytest.raises(SourceExtractionError, match="complete chat/completions"):
        extract_source("just the content string")


def test_normalization_only_touches_newlines() -> None:
    crlf = VALID_SOURCE.replace("\n", "\r\n")
    assert normalize_source(crlf) == VALID_SOURCE
    assert normalize_source(VALID_SOURCE.rstrip("\n")) == VALID_SOURCE
    assert normalize_source(VALID_SOURCE + "\n\n\n") == VALID_SOURCE


def test_normalization_does_not_reformat_or_strip_comments() -> None:
    """只允许换行归一：不格式化、不删注释、不补 import。"""
    source = "# 模型自己写的注释\ndef run_experiment(request):\n    return   {'a':1}\n"
    assert normalize_source(source) == source


def test_python_39_syntax_is_enforced() -> None:
    """match 语句是 3.10 语法，必须被 3.9 特性门挡下。"""
    source = (
        "def run_experiment(request):\n"
        "    match request:\n"
        "        case {}:\n"
        "            return None\n"
    )
    with pytest.raises(SourceExtractionError):
        extract_source(chat_response(source))


def test_save_source_writes_the_normalized_bytes_verbatim(tmp_path: Path) -> None:
    destination = tmp_path / "work" / "D-01" / "implementation.py"
    source, digest = save_source(chat_response(fenced(VALID_SOURCE)), destination)
    assert source == VALID_SOURCE
    assert destination.read_bytes() == VALID_SOURCE.encode("utf-8")
    assert len(digest) == 64
    assert destination.parent.name == "D-01"


# --- 与官方 model_source.py 差分 ---


@pytest.fixture
def official_module():
    if not OFFICIAL_MODEL_SOURCE.is_file():
        pytest.skip(f"official model_source.py not found at {OFFICIAL_MODEL_SOURCE}")
    spec = importlib.util.spec_from_file_location("official_model_source", OFFICIAL_MODEL_SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DIFF_CASES: list[tuple[str, Any]] = [
    ("bare", chat_response(VALID_SOURCE)),
    ("fenced", chat_response(fenced(VALID_SOURCE))),
    ("truncated", chat_response(VALID_SOURCE, finish_reason="length")),
    ("no-entry", chat_response("def solve(request):\n    return None\n")),
    ("syntax", chat_response("def run_experiment(request:\n")),
    (
        "two-impls",
        chat_response(
            f"```python\n{VALID_SOURCE}```\n\n```python\n"
            "def run_experiment(request):\n    return {'a': 1}\n```\n"
        ),
    ),
    (
        "same-twice",
        chat_response(f"```python\n{VALID_SOURCE}```\n\n```python\n{VALID_SOURCE}```\n"),
    ),
    ("crlf", chat_response(VALID_SOURCE.replace("\n", "\r\n"))),
    (
        "py310",
        chat_response(
            "def run_experiment(request):\n    match request:\n        case {}:\n            return None\n"
        ),
    ),
    ("empty", chat_response("")),
    ("not-a-dict", "plain string"),
]


@pytest.mark.parametrize(("name", "response"), DIFF_CASES)
def test_matches_the_official_extractor(official_module, name: str, response: Any) -> None:
    """我们的判定必须与官方逐字一致，否则本地自测通过而平台来源准入失败。"""
    try:
        expected = official_module.extract_source(response)
        expected_error = None
    except Exception as exc:
        expected = None
        expected_error = type(exc).__name__

    try:
        actual = extract_source(response)
        actual_error = None
    except SourceExtractionError:
        actual = None
        actual_error = "SourceExtractionError"

    assert (actual is None) == (expected is None), name
    if expected is not None:
        assert actual == expected, name
    else:
        # 两边都必须拒绝；异常类型可以不同，但拒绝与否必须一致
        assert expected_error is not None and actual_error is not None, name


# --- 材料读取 ---


def test_missing_experiment_directory_is_reported(tmp_path: Path) -> None:
    materials = load_case_materials("D-01", tmp_path / "absent")
    assert materials.available is False
    assert "not mounted" in (materials.missing_reason or "")


def test_materials_require_method_and_protocol(tmp_path: Path) -> None:
    case_root = tmp_path / "D-01"
    case_root.mkdir()
    (case_root / "protocol.json").write_text("{}", encoding="utf-8")
    materials = load_case_materials("D-01", tmp_path)
    assert materials.available is False
    assert "method.md" in (materials.missing_reason or "")


def test_materials_load_when_method_and_protocol_exist(tmp_path: Path) -> None:
    case_root = tmp_path / "D-01"
    case_root.mkdir()
    (case_root / "method.md").write_text("# 方法\n分词规则……", encoding="utf-8")
    (case_root / "protocol.json").write_text('{"phases": ["main"]}', encoding="utf-8")
    (case_root / "sample.json").write_text('{"case_id": "D-01"}', encoding="utf-8")
    materials = load_case_materials("D-01", tmp_path)
    assert materials.available is True
    assert materials.method is not None and "分词规则" in materials.method
    assert materials.protocol == {"phases": ["main"]}
    assert materials.sample == {"case_id": "D-01"}
    assert set(materials.present_files) == {"method.md", "protocol.json", "sample.json"}


def test_unparsable_protocol_marks_materials_unavailable(tmp_path: Path) -> None:
    case_root = tmp_path / "D-01"
    case_root.mkdir()
    (case_root / "method.md").write_text("# 方法", encoding="utf-8")
    (case_root / "protocol.json").write_text("{not json", encoding="utf-8")
    assert load_case_materials("D-01", tmp_path).available is False


# --- handler 编排 ---

D_INPUT = (
    "核心指标恰好三项：name=Macro-F1，unit=分数；name=Accuracy，unit=%；"
    "name=运行时间，unit=秒。使用本轮冻结数据与脚本实际执行。"
)


def reproduction_task(case_id: str = "D-01") -> Task:
    return Task.parse(
        {
            "case_id": case_id,
            "function": "Reproduction",
            "title": "复现实验",
            "input": D_INPUT,
            "materials": [],
            "constraints": ["指标必须来自本次真实运行"],
            "expected_output": "返回 case_id、status、恰好三项 metrics 和 figures。",
            "output_schema_ref": "Reproduction",
        }
    )


@pytest.fixture
def mounted_materials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "experiments"
    case_root = root / "D-01"
    case_root.mkdir(parents=True)
    (case_root / "method.md").write_text("# D-01 方法\n分词 lowercase + [a-z0-9]+。", "utf-8")
    (case_root / "protocol.json").write_text('{"phases": ["main"]}', "utf-8")
    monkeypatch.setenv("RESEARCH_EXPERIMENT_ROOT", str(root))
    return root


def submission(status: str = "completed", figures: list[dict[str, str]] | None = None) -> dict:
    return {
        "case_id": "D-01",
        "status": status,
        "metrics": [
            {
                "name": "Macro-F1",
                "unit": "分数",
                "reference": 0.82,
                "observed": 0.80,
                "relative_error": 0.024,
            },
            {
                "name": "Accuracy",
                "unit": "%",
                "reference": 91.0,
                "observed": 90.0,
                "relative_error": 0.011,
            },
            {
                "name": "运行时间",
                "unit": "秒",
                "reference": 1.5,
                "observed": 1.7,
                "relative_error": 0.133,
            },
        ],
        "figures": figures if figures is not None else [],
    }


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


def install_fake_client(monkeypatch: pytest.MonkeyPatch, outcomes: list[Any]):
    fake = FakeExperimentClient(outcomes)
    monkeypatch.setattr(reproduction_handler, "build_experiment_client", lambda context: fake)
    return fake


def test_materials_missing_returns_a_legal_answer_without_calling_the_model(
    base_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    papers_root: Path,
    tmp_workspace: Path,
) -> None:
    monkeypatch.setenv("RESEARCH_EXPERIMENT_ROOT", str(tmp_path / "absent"))
    fake = FakeGateway([gateway_response(VALID_SOURCE)])
    task = reproduction_task()
    context = context_for(
        base_env, task, [gateway_response(VALID_SOURCE)], papers_root, tmp_workspace
    )
    result = solve_reproduction(task, context)
    assert result["status"] == "materials_missing"
    assert result["figures"] == []
    assert [m["name"] for m in result["metrics"]] == ["Macro-F1", "Accuracy", "运行时间"]
    assert all(m["observed"] is None for m in result["metrics"])
    assert fake.calls == []


def test_successful_run_preserves_the_helper_written_file(
    base_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    mounted_materials: Path,
    papers_root: Path,
    tmp_workspace: Path,
) -> None:
    """helper 自己写的结果文件是权威产物：不得被外层重新序列化覆盖。"""
    helper_bytes = (json.dumps(submission(), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    result_path = tmp_workspace / "data" / TEAM / "D-01.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_bytes(helper_bytes)

    install_fake_client(
        monkeypatch,
        [
            ExperimentOutcome(
                returncode=0,
                stdout=json.dumps(submission(), ensure_ascii=False),
                stderr="",
                submission=submission(),
                result_file_bytes=helper_bytes,
                result_file_path=result_path,
            )
        ],
    )
    task = reproduction_task()
    context = context_for(
        base_env, task, [gateway_response(VALID_SOURCE)], papers_root, tmp_workspace
    )
    result = solve_reproduction(task, context)

    assert isinstance(result, PersistedResult)
    assert result.data == helper_bytes
    assert result.path == result_path
    # 落盘文件仍是 helper 写下的那份，一个字节都没变
    assert result_path.read_bytes() == helper_bytes
    assert result.payload["status"] == "completed"
    assert result.payload["case_id"] == "D-01"


def test_helper_file_is_trusted_even_when_stdout_is_unparsable(
    base_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    mounted_materials: Path,
    papers_root: Path,
    tmp_workspace: Path,
) -> None:
    """正式结果已产生而外层解析出错时，先核对真实结果，禁止用失败占位覆盖。"""
    helper_bytes = (json.dumps(submission(), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    result_path = tmp_workspace / "data" / TEAM / "D-01.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_bytes(helper_bytes)

    install_fake_client(
        monkeypatch,
        [
            ExperimentOutcome(
                returncode=1,
                stdout="Traceback (most recent call last):\n  ...",
                stderr="boom",
                submission=None,
                result_file_bytes=helper_bytes,
                result_file_path=result_path,
            )
        ],
    )
    task = reproduction_task()
    context = context_for(
        base_env, task, [gateway_response(VALID_SOURCE)], papers_root, tmp_workspace
    )
    result = solve_reproduction(task, context)
    assert isinstance(result, PersistedResult)
    assert result.payload["status"] == "completed"


def test_helper_failure_returns_failed_with_null_observed(
    base_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    mounted_materials: Path,
    papers_root: Path,
    tmp_workspace: Path,
) -> None:
    install_fake_client(
        monkeypatch,
        [
            ExperimentOutcome(
                returncode=1,
                stdout="",
                stderr="Trusted experiment failed",
                submission=None,
                result_file_bytes=None,
                result_file_path=None,
            )
        ],
    )
    task = reproduction_task()
    context = context_for(
        base_env, task, [gateway_response(VALID_SOURCE)], papers_root, tmp_workspace
    )
    result = solve_reproduction(task, context)
    assert result["status"] == "failed"
    assert [m["name"] for m in result["metrics"]] == ["Macro-F1", "Accuracy", "运行时间"]
    assert all(m["observed"] is None and m["relative_error"] is None for m in result["metrics"])
    assert result["figures"] == []


def test_unmounted_helper_is_reported_as_failed_not_as_success(
    base_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    mounted_materials: Path,
    papers_root: Path,
    tmp_workspace: Path,
) -> None:
    install_fake_client(monkeypatch, [FileNotFoundError("platform experiment helper not mounted")])
    task = reproduction_task()
    context = context_for(
        base_env, task, [gateway_response(VALID_SOURCE)], papers_root, tmp_workspace
    )
    assert solve_reproduction(task, context)["status"] == "failed"


def test_figure_objects_are_passed_through_with_all_five_fields(
    base_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    mounted_materials: Path,
    papers_root: Path,
    tmp_workspace: Path,
) -> None:
    """figures 是对象数组，键集固定为 path/caption/x_label/y_label/legend。"""
    figure = {
        "path": f"data/figure/{TEAM}/D-01-result.png",
        "caption": "各条件下的 Macro-F1 对比",
        "x_label": "条件",
        "y_label": "Macro-F1 / 分数",
        "legend": "baseline 与 candidate 两条曲线",
    }
    payload = submission(figures=[figure])
    install_fake_client(
        monkeypatch,
        [
            ExperimentOutcome(
                returncode=0,
                stdout=json.dumps(payload, ensure_ascii=False),
                stderr="",
                submission=payload,
                result_file_bytes=None,
                result_file_path=None,
            )
        ],
    )
    task = reproduction_task()
    context = context_for(
        base_env, task, [gateway_response(VALID_SOURCE)], papers_root, tmp_workspace
    )
    result = solve_reproduction(task, context)
    assert result["figures"] == [figure]


def test_illegal_submission_status_is_not_forwarded(
    base_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    mounted_materials: Path,
    papers_root: Path,
    tmp_workspace: Path,
) -> None:
    """evidence_pending 是平台状态，不能加到答案顶层，也不能当合法 status 透传。"""
    payload = {**submission(), "status": "evidence_pending"}
    install_fake_client(
        monkeypatch,
        [
            ExperimentOutcome(
                returncode=0,
                stdout=json.dumps(payload, ensure_ascii=False),
                stderr="",
                submission=payload,
                result_file_bytes=None,
                result_file_path=None,
            )
        ],
    )
    task = reproduction_task()
    context = context_for(
        base_env, task, [gateway_response(VALID_SOURCE)], papers_root, tmp_workspace
    )
    result = solve_reproduction(task, context)
    assert result["status"] == "failed"


def test_submission_with_wrong_metric_count_is_rejected(
    base_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    mounted_materials: Path,
    papers_root: Path,
    tmp_workspace: Path,
) -> None:
    payload = {**submission(), "metrics": submission()["metrics"][:2]}
    install_fake_client(
        monkeypatch,
        [
            ExperimentOutcome(
                returncode=0,
                stdout=json.dumps(payload, ensure_ascii=False),
                stderr="",
                submission=payload,
                result_file_bytes=None,
                result_file_path=None,
            )
        ],
    )
    task = reproduction_task()
    context = context_for(
        base_env, task, [gateway_response(VALID_SOURCE)], papers_root, tmp_workspace
    )
    assert solve_reproduction(task, context)["status"] == "failed"


def test_rejected_source_triggers_a_full_regeneration_not_a_local_patch(
    base_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    mounted_materials: Path,
    papers_root: Path,
    tmp_workspace: Path,
) -> None:
    """提取失败只能让同一会话模型重新返回完整版本，绝不本地补码。"""
    install_fake_client(
        monkeypatch,
        [
            ExperimentOutcome(
                returncode=0,
                stdout=json.dumps(submission(), ensure_ascii=False),
                stderr="",
                submission=submission(),
                result_file_bytes=None,
                result_file_path=None,
            )
        ],
    )
    task = reproduction_task()
    fake = FakeGateway(
        [
            gateway_response("def solve(request):\n    return None\n"),  # 缺入口函数
            gateway_response(VALID_SOURCE),
        ]
    )
    env = {
        **base_env,
        "RESEARCH_PAPERS_ROOT": str(papers_root),
        "RESEARCH_WORKSPACE_ROOT": str(tmp_workspace),
    }
    context = load_task_context(task, Settings.from_env(env), gateway=fake)
    result = solve_reproduction(task, context)
    assert [stage for stage, _ in fake.calls] == [
        "reproduction.generate_source",
        "reproduction.regenerate_source",
    ]
    # 第二次请求里带上了上一轮的完整回复与「重新返回完整版本」的要求
    second_messages = fake.calls[1][1]
    assert any(m.role == "assistant" for m in second_messages)
    assert result["status"] == "completed"
    saved = (tmp_workspace / "data" / TEAM / "work" / "D-01" / "implementation.py").read_bytes()
    assert saved == VALID_SOURCE.encode("utf-8")


def test_source_never_admissible_yields_failed_answer(
    base_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    mounted_materials: Path,
    papers_root: Path,
    tmp_workspace: Path,
) -> None:
    client = install_fake_client(monkeypatch, [])
    task = reproduction_task()
    context = context_for(
        base_env,
        task,
        [gateway_response("not python at all")] * 3,
        papers_root,
        tmp_workspace,
    )
    result = solve_reproduction(task, context)
    assert result["status"] == "failed"
    assert client.calls == []  # 源码不可用时根本不去调正式执行


def test_stdout_and_stderr_are_preserved_before_parsing(
    base_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    mounted_materials: Path,
    papers_root: Path,
    tmp_workspace: Path,
) -> None:
    install_fake_client(
        monkeypatch,
        [
            ExperimentOutcome(
                returncode=1,
                stdout="partial stdout",
                stderr="partial stderr",
                submission=None,
                result_file_bytes=None,
                result_file_path=None,
            )
        ],
    )
    task = reproduction_task()
    context = context_for(
        base_env, task, [gateway_response(VALID_SOURCE)], papers_root, tmp_workspace
    )
    solve_reproduction(task, context)
    work = tmp_workspace / "data" / TEAM / "work" / "D-01"
    assert (work / "run_stdout.txt").read_text("utf-8") == "partial stdout"
    assert (work / "run_stderr.txt").read_text("utf-8") == "partial stderr"
    assert json.loads((work / "run_meta.json").read_text("utf-8"))["returncode"] == 1


def test_official_run_timeout_comes_from_the_deadline(
    base_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    mounted_materials: Path,
    papers_root: Path,
    tmp_workspace: Path,
) -> None:
    fake = install_fake_client(
        monkeypatch,
        [
            ExperimentOutcome(
                returncode=0,
                stdout=json.dumps(submission(), ensure_ascii=False),
                stderr="",
                submission=submission(),
                result_file_bytes=None,
                result_file_path=None,
            )
        ],
    )
    task = reproduction_task()
    context = context_for(
        base_env, task, [gateway_response(VALID_SOURCE)], papers_root, tmp_workspace
    )
    solve_reproduction(task, context)
    _, implementation_path, timeout = fake.calls[0]
    assert implementation_path.is_absolute()
    assert implementation_path.name == "implementation.py"
    # handler 传的是「剩余预算减去落盘预留」，硬上限由 client 负责
    assert 0 < timeout <= context.deadline.budget_seconds
    assert timeout == pytest.approx(context.deadline.budget_seconds - 5.0, abs=1.0)


def test_subprocess_client_caps_the_timeout_below_the_case_budget(tmp_path: Path) -> None:
    """helper 内部 RPC 上限是 600 秒，但整题只有 180 秒；client 必须自己收口。"""
    from research_assistant.reproduction.client import (
        HARD_TIMEOUT_CEILING_SECONDS,
        SubprocessExperimentClient,
    )

    helper = tmp_path / "run_experiment.py"
    helper.write_text("import sys; print(sys.argv)", encoding="utf-8")
    client = SubprocessExperimentClient(helper, result_root=tmp_path, team=TEAM)
    assert HARD_TIMEOUT_CEILING_SECONDS < 180.0
    outcome = client.run("D-01", tmp_path / "impl.py", timeout=600.0)
    assert outcome.returncode == 0


def test_insufficient_budget_for_the_official_run_is_reported(
    base_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    mounted_materials: Path,
    papers_root: Path,
    tmp_workspace: Path,
) -> None:
    """生成阶段必须为正式运行预留预算，不能把 180 秒全花在模型上。"""
    install_fake_client(monkeypatch, [])
    task = reproduction_task()
    env = {
        **base_env,
        "RESEARCH_PAPERS_ROOT": str(papers_root),
        "RESEARCH_WORKSPACE_ROOT": str(tmp_workspace),
        "RESEARCH_CASE_BUDGET_SECONDS": "30",
    }
    context = load_task_context(
        task, Settings.from_env(env), gateway=FakeGateway([gateway_response(VALID_SOURCE)])
    )
    result = solve_reproduction(task, context)
    assert result["status"] == "failed"


def test_no_shell_is_used_to_invoke_the_helper() -> None:
    """argv 列表调用；绝不经 shell，绝不拼接命令字符串。"""
    from research_assistant.reproduction import client as client_module

    for module in (reproduction_handler, client_module):
        text = Path(module.__file__).read_text(encoding="utf-8")
        assert "shell=True" not in text
        assert "os.system" not in text
        assert "os.popen" not in text


def test_subprocess_client_builds_an_argv_list(tmp_path: Path) -> None:
    helper = tmp_path / "run_experiment.py"
    helper.write_text("print('ok')", encoding="utf-8")
    from research_assistant.reproduction.client import SubprocessExperimentClient

    client = SubprocessExperimentClient(helper, result_root=tmp_path, team=TEAM)
    outcome = client.run("D-01", tmp_path / "impl.py", timeout=10.0)
    assert outcome.returncode == 0
    assert outcome.stdout.strip() == "ok"


def test_subprocess_client_raises_when_the_helper_is_not_mounted(tmp_path: Path) -> None:
    from research_assistant.reproduction.client import SubprocessExperimentClient

    client = SubprocessExperimentClient(tmp_path / "absent.py", result_root=tmp_path, team=TEAM)
    with pytest.raises(FileNotFoundError, match="not mounted"):
        client.run("D-01", tmp_path / "impl.py", timeout=5.0)


def test_generated_source_is_never_prewritten_into_the_repository() -> None:
    """镜像内不得预置完整算法：仓库源码里不应出现任何 run_experiment 的完整实现。"""
    src_root = Path(__file__).resolve().parents[2] / "src"
    for path in src_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "def run_experiment(" not in text, path


# --- 自测回路（保护 40 分不被「通过准入却运行即崩」的源码毁掉） ---

from research_assistant.reproduction.selftest import (  # noqa: E402
    SelfTestCase,
    build_self_test_cases,
    run_self_test,
)


def _write_impl(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "implementation.py"
    path.write_text(body, encoding="utf-8")
    return path


def _cases(tmp_path: Path) -> tuple[SelfTestCase, ...]:
    request = tmp_path / "request.json"
    request.write_text("{}", encoding="utf-8")
    return (SelfTestCase(name="main", payload={"case_id": "D-01", "phase": "run"}, origin="t"),)


def test_self_test_passes_for_a_working_implementation(tmp_path: Path) -> None:
    impl = _write_impl(
        tmp_path,
        "def run_experiment(request):\n    return {'baseline': [1], 'details': {'ok': True}}\n",
    )
    result = run_self_test(impl, _cases(tmp_path), work_dir=tmp_path / "work")
    assert result.ok is True
    assert result.passed == 1
    assert result.output_keys == ("baseline", "details")


def test_self_test_catches_a_name_error_that_admission_would_miss(tmp_path: Path) -> None:
    """实测发生过的真实故障：用了 re 却没 import re。

    官方 AST 检查只看语法与入口函数名，这类源码能通过来源准入却运行即崩；
    helper 一执行就锁源，整题 40 分归零。自测必须在调用 helper 之前拦住它。
    """
    impl = _write_impl(
        tmp_path,
        "def run_experiment(request):\n    return re.findall(r'[a-z]+', 'abc')\n",
    )
    from research_assistant.reproduction.source import extract_source

    # 先确认它确实能通过来源准入
    source = impl.read_text(encoding="utf-8")
    assert extract_source(_response_wrapping(source)) == source
    result = run_self_test(impl, _cases(tmp_path), work_dir=tmp_path / "work")
    assert result.ok is False
    assert "NameError" in (result.error or "")


def _response_wrapping(source: str) -> dict[str, Any]:
    return chat_response(source)


def test_self_test_does_not_truncate_a_large_result(tmp_path: Path) -> None:
    """回归：曾把结果 print 时截断到 4000 字符，D-02 的 27 行搜索记录必然解析失败。"""
    impl = _write_impl(
        tmp_path,
        "def run_experiment(request):\n"
        "    return {'details': {'search': [{'i': i, 'pad': 'x' * 40} for i in range(200)]}}\n",
    )
    result = run_self_test(impl, _cases(tmp_path), work_dir=tmp_path / "work")
    assert result.ok is True, result.error
    assert result.passed == 1


def test_self_test_ignores_debug_output_from_the_implementation(tmp_path: Path) -> None:
    """回归：曾靠「stdout 最后一行是 JSON」取结果，生成源码自己 print 就会取错行。"""
    impl = _write_impl(
        tmp_path,
        "def run_experiment(request):\n"
        "    print('debug: loading data')\n"
        "    print('not json at all')\n"
        "    return {'baseline': [], 'candidate': None}\n",
    )
    result = run_self_test(impl, _cases(tmp_path), work_dir=tmp_path / "work")
    assert result.ok is True, result.error
    assert result.output_keys == ("baseline", "candidate")


def test_self_test_reports_a_nonzero_exit(tmp_path: Path) -> None:
    impl = _write_impl(tmp_path, "def run_experiment(request):\n    raise ValueError('boom')\n")
    result = run_self_test(impl, _cases(tmp_path), work_dir=tmp_path / "work")
    assert result.ok is False
    assert "boom" in (result.error or "")


def test_self_test_reports_a_timeout(tmp_path: Path) -> None:
    impl = _write_impl(tmp_path, "def run_experiment(request):\n    while True:\n        pass\n")
    result = run_self_test(impl, _cases(tmp_path), work_dir=tmp_path / "work", timeout=2.0)
    assert result.ok is False
    assert "超时" in (result.error or "")


def test_self_test_with_no_cases_is_a_pass(tmp_path: Path) -> None:
    impl = _write_impl(tmp_path, "def run_experiment(request):\n    return {}\n")
    result = run_self_test(impl, (), work_dir=tmp_path / "work")
    assert result.ok is True
    assert result.case_count == 0


def test_public_sample_requests_are_preferred_over_the_synthesised_one(
    tmp_path: Path,
) -> None:
    root = tmp_path / "experiments"
    (root / "D-01").mkdir(parents=True)
    (root / "D-01" / "sample.json").write_text(
        json.dumps({"case_id": "D-01", "phase": "run", "data": {}}), encoding="utf-8"
    )
    (root / "D-01" / "protocol.json").write_text('{"seed": 42}', encoding="utf-8")
    (root / "公开样例请求" / "D-01").mkdir(parents=True)
    (root / "公开样例请求" / "D-01.json").write_text(
        json.dumps({"stage": "run", "payload": {"case_id": "D-01", "phase": "run"}}),
        encoding="utf-8",
    )
    (root / "公开样例请求" / "D-01" / "边界.json").write_text(
        json.dumps({"stage": "run", "payload": {"case_id": "D-01", "phase": "run"}}),
        encoding="utf-8",
    )
    materials = load_case_materials("D-01", root)
    cases = build_self_test_cases(materials, root)
    assert [c.name for c in cases] == ["D-01", "边界"]


def test_sample_json_is_synthesised_into_a_callable_payload(tmp_path: Path) -> None:
    """sample.json 是 {case_id,phase,data}，不是完整 worker 请求；必须补上 protocol。"""
    root = tmp_path / "experiments"
    (root / "D-01").mkdir(parents=True)
    (root / "D-01" / "sample.json").write_text(
        json.dumps({"case_id": "D-01", "phase": "run", "data": {"texts": []}}), encoding="utf-8"
    )
    (root / "D-01" / "protocol.json").write_text('{"seed": 42, "epochs": 100}', encoding="utf-8")
    materials = load_case_materials("D-01", root)
    (case,) = build_self_test_cases(materials, root)
    assert case.payload["protocol"] == {"seed": 42, "epochs": 100}
    assert case.payload["phase"] == "run"


def test_no_self_test_cases_when_nothing_is_available(tmp_path: Path) -> None:
    root = tmp_path / "experiments"
    (root / "D-01").mkdir(parents=True)
    (root / "D-01" / "method.md").write_text("# m", encoding="utf-8")
    (root / "D-01" / "protocol.json").write_text("{}", encoding="utf-8")
    materials = load_case_materials("D-01", root)
    assert build_self_test_cases(materials, root) == ()
