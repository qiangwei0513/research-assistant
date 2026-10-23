# P1 最小合规服务 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建一个符合赛题一 v3 合同的 Python HTTP 服务，A 类（Chat）端到端可用，其余四类正确路由并明确报错。

**Architecture:** 混合执行模型——FastAPI 路由为 async，业务 handler 为同步代码，经 `anyio.to_thread` 桥接；预算用显式 `Deadline`（monotonic）沿参数传递。分层为 `contracts`（纯值对象与校验，不触文件系统）/ `runtime`（配置、预算、路径、落盘、网关、遥测、日志）/ `handlers`（业务）/ `api`+`solve`（编排）。

**Tech Stack:** Python 3.11（`>=3.11,<3.12`，对齐镜像 3.11.16）、FastAPI 0.111.0、Starlette 0.37.2、Pydantic 2.8.0、uvicorn 0.30.0、httpx 0.27.0、anyio 4.15.1；dev 用 uv + pytest + ruff。全部运行时依赖基础镜像已预装。

**Spec:** [docs/superpowers/specs/2026-10-23-p1-minimal-compliant-service-design.md](../specs/2026-10-23-p1-minimal-compliant-service-design.md)

## Global Constraints

- Python `>=3.11,<3.12`；四空格缩进、类型注解、`snake_case` 函数与变量、`PascalCase` 类型。
- 运行时依赖锁定为镜像实测版本：`fastapi==0.111.0`、`starlette==0.37.2`、`pydantic==2.8.0`、`uvicorn==0.30.0`、`httpx==0.27.0`、`anyio==4.15.1`。不得引入镜像中不存在的运行时依赖。
- 五类输出的顶层字段白名单**恰好**为：A `case_id,answer,clarifying_question`；B `case_id,papers`（元素仅 `filename,reason`）；C `case_id,path,markdown`；D `case_id,status,metrics,figures`；E `case_id,path,markdown`。禁止任何额外顶层字段。
- JSON 序列化统一 `ensure_ascii=False, allow_nan=False`，UTF-8，直接返回对象：无自然语言前缀、无 Markdown 代码围栏。
- `case_id`、指标 `name`、`unit`、`rules_version`、schema 键一律原样保留，不翻译、不归一化。
- 凭据只运行时注入。日志、异常消息、结果文件、测试断言中都不得出现 API key 或完整请求头。
- 禁止模块级可变状态；一题一 `SolveContext`。
- 不得在一次 `/solve` 请求中遍历整轮 11 题。
- `timeslice_budget=300` 的单位未确认，P1 不得使用该值，也不得解释成 300 秒或 Token 额度。
- Ruff 必须排除 `docs/references`（官方快照只读）与 `work/`（模型生成源码需保持原样）。
- 本轮**不建** Dockerfile、不 `docker load` 基础镜像；R02/R03 标为未执行。
- 提交信息用 `feat:` / `test:` / `chore:` / `docs:` / `fix:` 前缀加简洁祈使句。

## 对 Spec 的四处收敛性细化

实现时以下列定义为准。它们收窄而非改变已批准的设计：

1. `SolveContext` 增加 `gateway: Gateway` 字段。理由：spec 禁止模块级可变状态，handler 又必须拿到网关，注入 context 是唯一不引入全局的通路。
2. `runtime/persistence.py` 只暴露 `write_bytes_verified(path, data)` 与 `write_text_verified(path, text)`；序列化由 `contracts/results.serialize_result(payload) -> bytes` 负责。理由：只有让 HTTP 响应体直接复用落盘的那份 bytes，才能保证「返回与落盘字节一致」——FastAPI 默认的 `JSONResponse` 会用不同的分隔符重新序列化，做不到逐字节相同。
3. `Settings` 用 `gateway_config_error: str | None` 字段承载配置缺失/冲突原因，`from_env` 不抛异常；需要网关时调 `settings.require_gateway()` 才抛 `ConfigError`。理由：spec §5.3 要求 `/health` 不因网关配置问题返回 5xx，若 `from_env` 直接抛异常，应用根本起不来。
4. 新增 `MaterialUnavailableError`（HTTP 422）：`materials` 中的条目路径合法但文件不存在。spec §5.5 的错误表漏了这一行，而 §4 的材料范围校验明确要求「真实文件」。

---

### Task 1: 项目脚手架与工具链

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `.python-version`
- Create: `src/research_assistant/__init__.py`
- Create: `src/research_assistant/contracts/__init__.py`
- Create: `src/research_assistant/runtime/__init__.py`
- Create: `src/research_assistant/handlers/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Test: `tests/unit/test_smoke.py`

**Interfaces:**
- Consumes: 无（首个任务）
- Produces: 可运行的 `uv run pytest` 与 `uv run ruff`；包名 `research_assistant`；pytest 标记 `live`（默认排除）；`tests/conftest.py` 中的 `tmp_workspace` fixture（后续所有集成测试使用）。

- [ ] **Step 1: 写 pyproject.toml**

```toml
[project]
name = "research-assistant"
version = "0.1.0"
description = "赛题一科研助手 Agent（objective-v3 / dispatch-v3）"
requires-python = ">=3.11,<3.12"
dependencies = [
    "fastapi==0.111.0",
    "starlette==0.37.2",
    "pydantic==2.8.0",
    "uvicorn==0.30.0",
    "httpx==0.27.0",
    "anyio==4.15.1",
]

[dependency-groups]
dev = [
    "pytest>=8.3,<9",
    "ruff>=0.6,<1",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/research_assistant"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra -m 'not live'"
markers = [
    "live: 需要真实模型网关与网络，默认排除；设置 .env.local 后用 -m live 单独运行",
]

[tool.ruff]
line-length = 100
target-version = "py311"
# 官方快照只读；work/ 内是模型生成源码，必须保持原样，不得被格式化
extend-exclude = ["docs/references", "work", ".venv"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "RUF"]
ignore = ["E501"]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["B011"]
```

- [ ] **Step 2: 写 .python-version 与 .env.example**

`.python-version`:

```
3.11
```

`.env.example`（只写变量名，**不写值**）:

```sh
# 复制为 .env.local 后填入真实值；.env.local 已被 .gitignore 忽略。
# 两组变量互斥，程序按「整组」探测，禁止逐变量混合。
# 赛题一规范记载的写法：
CHALLENGE_LLM_BASE_URL=
CHALLENGE_LLM_API_KEY=
CHALLENGE_LLM_MODEL=
CHALLENGE_SESSION_ID=
# D 类材料包示例的写法：
PLATFORM_LLM_URL=
PLATFORM_API_KEY=
PLATFORM_LLM_MODEL=
# 运行时身份与路径：
RESEARCH_WORKSPACE_TEAM=
RESEARCH_WORKSPACE_ROOT=/output
RESEARCH_PAPERS_ROOT=/data/papers
RESEARCH_CASE_BUDGET_SECONDS=180
AGENTHACK_LOG_LEVEL=INFO
```

- [ ] **Step 3: 建包骨架**

`src/research_assistant/__init__.py`:

```python
"""赛题一科研助手 Agent。"""

__version__ = "0.1.0"
```

`src/research_assistant/contracts/__init__.py`、`runtime/__init__.py`、`handlers/__init__.py`、`tests/__init__.py` 均为空文件。

- [ ] **Step 4: 写 tests/conftest.py**

```python
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
```

`TEAM` 从 conftest 导出，各测试文件各自定义同值常量（`TEAM = "测试队伍 A+1"`）以免测试间产生隐式耦合；如需统一，可 `from tests.conftest import TEAM`。

- [ ] **Step 5: 写 smoke 测试**

`tests/unit/test_smoke.py`:

```python
from __future__ import annotations

import research_assistant


def test_package_imports_and_exposes_version() -> None:
    assert research_assistant.__version__ == "0.1.0"
```

- [ ] **Step 6: 同步依赖并跑测试**

```bash
cd /Users/luananwei/科研助手/research-assistant
uv sync
uv run pytest -v
uv run ruff check .
uv run ruff format --check .
```

预期：`uv sync` 解析出 Python 3.11 环境并装好全部依赖；`pytest` 收集到 1 个测试并通过；`ruff check` 无告警；`ruff format --check` 无差异（若有差异，运行 `uv run ruff format .` 后重跑）。

若 `uv sync` 报 Python 3.11 不可用，运行 `uv python install 3.11` 后重试——本机已装 3.11.15，正常无需下载。

- [ ] **Step 7: 提交**

```bash
git add pyproject.toml .python-version .env.example uv.lock src/research_assistant tests
git commit -m "feat: scaffold research-assistant package and toolchain"
```

---

### Task 2: 异常层级与八字段 Task 合同

**Files:**
- Create: `src/research_assistant/errors.py`
- Create: `src/research_assistant/contracts/task.py`
- Test: `tests/unit/test_errors.py`
- Test: `tests/unit/test_task_contract.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `ResearchAssistantError(message: str, *, case_id: str | None = None)`，属性 `.message`、`.case_id`、类属性 `.http_status: int`
  - 子类与状态码：`TaskContractError` 422、`DispatchValidationError` 422、`PathAuthorizationError` 403、`MaterialUnavailableError` 422、`ConfigError` 500、`BudgetExhausted` 504、`GatewayError` 502、`PersistenceError` 500、`HandlerNotImplemented` 501
  - `error_body(exc: ResearchAssistantError) -> dict[str, Any]`，返回 `{"error": {"type", "message", "case_id"}}`
  - `TaskFunction(StrEnum)`：`CHAT="Chat"`、`LITERATURE_RETRIEVAL="Literature_Retrieval"`、`SURVEY="Survey"`、`REPRODUCTION="Reproduction"`、`IDEA="Idea"`
  - `SCHEMA_REF_BY_FUNCTION: Mapping[TaskFunction, str]`（值与枚举字面量相同）
  - `Task.parse(payload: Mapping[str, Any]) -> Task`
  - `Task` 字段：`case_id, function, title, input, materials, constraints, expected_output, output_schema_ref, unknown_fields`

- [ ] **Step 1: 写 errors 的失败测试**

`tests/unit/test_errors.py`:

```python
from __future__ import annotations

import pytest

from research_assistant.errors import (
    BudgetExhausted,
    ConfigError,
    DispatchValidationError,
    GatewayError,
    HandlerNotImplemented,
    MaterialUnavailableError,
    PathAuthorizationError,
    PersistenceError,
    ResearchAssistantError,
    TaskContractError,
    error_body,
)


@pytest.mark.parametrize(
    ("exc_type", "status"),
    [
        (TaskContractError, 422),
        (DispatchValidationError, 422),
        (PathAuthorizationError, 403),
        (MaterialUnavailableError, 422),
        (ConfigError, 500),
        (BudgetExhausted, 504),
        (GatewayError, 502),
        (PersistenceError, 500),
        (HandlerNotImplemented, 501),
    ],
)
def test_each_error_maps_to_its_http_status(
    exc_type: type[ResearchAssistantError], status: int
) -> None:
    assert exc_type.http_status == status
    assert exc_type("boom").http_status == status


def test_error_body_carries_type_message_and_case_id() -> None:
    body = error_body(TaskContractError("missing case_id", case_id="A-01"))
    assert body == {
        "error": {
            "type": "TaskContractError",
            "message": "missing case_id",
            "case_id": "A-01",
        }
    }


def test_error_body_case_id_defaults_to_none() -> None:
    assert error_body(ConfigError("no gateway"))["error"]["case_id"] is None


def test_error_message_never_leaks_into_type_field() -> None:
    body = error_body(GatewayError("upstream 500"))
    assert body["error"]["type"] == "GatewayError"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/unit/test_errors.py -v
```

预期：FAIL，`ModuleNotFoundError: No module named 'research_assistant.errors'`

- [ ] **Step 3: 实现 errors.py**

```python
"""异常层级与 HTTP 状态映射。

错误响应体只用于非 2xx，与五类答案 JSON 的字段白名单互不相干。
消息必须脱敏：不得包含 API key、完整请求头或模型响应全文。
"""

from __future__ import annotations

from typing import Any


class ResearchAssistantError(Exception):
    """所有可预期错误的基类。"""

    http_status: int = 500

    def __init__(self, message: str, *, case_id: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.case_id = case_id


class TaskContractError(ResearchAssistantError):
    """八字段缺失、类型错误、编号非法或 function 与 output_schema_ref 不一致。"""

    http_status = 422


class DispatchValidationError(ResearchAssistantError):
    """派题记录版本、配额、题号唯一性或题目哈希不符。"""

    http_status = 422


class PathAuthorizationError(ResearchAssistantError):
    """绝对提交路径、`..`、反斜杠、符号链接越界或访问他队目录。"""

    http_status = 403


class MaterialUnavailableError(ResearchAssistantError):
    """材料路径合法但文件不存在或不可读。"""

    http_status = 422


class ConfigError(ResearchAssistantError):
    """Gateway 变量组缺失或两组冲突；只报组名与原因，不报值。"""

    http_status = 500


class BudgetExhausted(ResearchAssistantError):
    """整题预算耗尽或某阶段剩余时间不足。"""

    http_status = 504


class GatewayError(ResearchAssistantError):
    """网络失败、非 2xx、响应不可解析或 finish_reason 不是 stop。"""

    http_status = 502


class PersistenceError(ResearchAssistantError):
    """写入失败、回读不一致或权限不足；不得报告成功。"""

    http_status = 500


class HandlerNotImplemented(ResearchAssistantError):
    """该类别在 P1 尚未实现。"""

    http_status = 501


def error_body(exc: ResearchAssistantError) -> dict[str, Any]:
    return {
        "error": {
            "type": type(exc).__name__,
            "message": exc.message,
            "case_id": exc.case_id,
        }
    }
```

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run pytest tests/unit/test_errors.py -v
```

预期：PASS（4 个测试）

- [ ] **Step 5: 写 Task 合同的失败测试**

`tests/unit/test_task_contract.py`:

```python
from __future__ import annotations

import copy
from typing import Any

import pytest

from research_assistant.contracts.task import SCHEMA_REF_BY_FUNCTION, Task, TaskFunction
from research_assistant.errors import TaskContractError


def payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "case_id": "A-01",
        "function": "Chat",
        "title": "光照与生长",
        "input": "解释关系并提一个限定问题。",
        "materials": [],
        "constraints": [],
        "expected_output": "解释关系并提问。",
        "output_schema_ref": "Chat",
    }
    base.update(overrides)
    return base


def test_parse_accepts_a_valid_chat_task() -> None:
    task = Task.parse(payload())
    assert task.case_id == "A-01"
    assert task.function is TaskFunction.CHAT
    assert task.materials == ()
    assert task.constraints == ()
    assert task.unknown_fields == {}


def test_parse_preserves_materials_and_constraints_as_ordered_tuples() -> None:
    task = Task.parse(
        payload(materials=["a b.pdf", "中文+报告.pdf"], constraints=["不超过 3 篇", "只用正文证据"])
    )
    assert task.materials == ("a b.pdf", "中文+报告.pdf")
    assert task.constraints == ("不超过 3 篇", "只用正文证据")


@pytest.mark.parametrize(
    "field",
    ["case_id", "function", "title", "input", "materials", "constraints",
     "expected_output", "output_schema_ref"],
)
def test_missing_field_is_rejected(field: str) -> None:
    data = payload()
    del data[field]
    with pytest.raises(TaskContractError, match=field):
        Task.parse(data)


@pytest.mark.parametrize("field", ["case_id", "title", "input", "expected_output",
                                   "output_schema_ref", "function"])
def test_non_string_scalar_field_is_rejected(field: str) -> None:
    with pytest.raises(TaskContractError, match=field):
        Task.parse(payload(**{field: 123}))


@pytest.mark.parametrize("field", ["materials", "constraints"])
def test_non_list_material_field_is_rejected(field: str) -> None:
    with pytest.raises(TaskContractError, match=field):
        Task.parse(payload(**{field: "a.pdf"}))


@pytest.mark.parametrize("field", ["materials", "constraints"])
def test_null_material_field_is_rejected(field: str) -> None:
    """无材料时必须是空数组，不是 null。"""
    with pytest.raises(TaskContractError, match=field):
        Task.parse(payload(**{field: None}))


def test_materials_must_contain_only_strings() -> None:
    with pytest.raises(TaskContractError, match="materials"):
        Task.parse(payload(materials=["ok.pdf", 7]))


@pytest.mark.parametrize("case_id", ["A-01", "A-1", "A-100", "B-03", "C-01", "D-05", "E-02"])
def test_valid_case_ids_are_accepted(case_id: str) -> None:
    function = {"A": "Chat", "B": "Literature_Retrieval", "C": "Survey",
                "D": "Reproduction", "E": "Idea"}[case_id[0]]
    task = Task.parse(payload(case_id=case_id, function=function, output_schema_ref=function))
    assert task.case_id == case_id


@pytest.mark.parametrize("case_id", ["", "A-", "A-1000", "a-01", "F-01", "A_01", "A-01 ", "01"])
def test_invalid_case_ids_are_rejected(case_id: str) -> None:
    with pytest.raises(TaskContractError, match="case_id"):
        Task.parse(payload(case_id=case_id))


def test_unknown_function_is_rejected() -> None:
    with pytest.raises(TaskContractError, match="function"):
        Task.parse(payload(function="Summarize"))


def test_function_and_schema_ref_mismatch_is_rejected_without_guessing() -> None:
    """不得猜测路由：function=Survey 而 output_schema_ref=Chat 必须直接报错。"""
    with pytest.raises(TaskContractError, match="output_schema_ref"):
        Task.parse(payload(function="Survey", output_schema_ref="Chat"))


@pytest.mark.parametrize("function", list(SCHEMA_REF_BY_FUNCTION.values()))
def test_every_function_pairs_with_its_own_schema_ref(function: str) -> None:
    case_id = {"Chat": "A-01", "Literature_Retrieval": "B-01", "Survey": "C-01",
               "Reproduction": "D-01", "Idea": "E-01"}[function]
    task = Task.parse(payload(case_id=case_id, function=function, output_schema_ref=function))
    assert task.function.value == function


def test_schema_ref_by_function_covers_all_five_types() -> None:
    assert set(SCHEMA_REF_BY_FUNCTION) == set(TaskFunction)
    assert SCHEMA_REF_BY_FUNCTION[TaskFunction.LITERATURE_RETRIEVAL] == "Literature_Retrieval"


def test_unrecognized_extension_fields_are_recorded_not_dropped_or_forwarded() -> None:
    data = payload()
    data["future_field"] = {"nested": 1}
    data["another"] = "x"
    task = Task.parse(data)
    assert task.unknown_fields == {"future_field": {"nested": 1}, "another": "x"}


def test_unknown_fields_mapping_is_immutable() -> None:
    task = Task.parse(payload(extra=1))
    with pytest.raises(TypeError):
        task.unknown_fields["extra"] = 2  # type: ignore[index]


def test_parse_does_not_mutate_the_incoming_payload() -> None:
    data = payload(materials=["a.pdf"])
    snapshot = copy.deepcopy(data)
    Task.parse(data)
    assert data == snapshot


def test_task_is_hashable_and_frozen() -> None:
    task = Task.parse(payload())
    with pytest.raises(Exception):
        task.case_id = "B-01"  # type: ignore[misc]
    assert hash(task) == hash(Task.parse(payload()))
```

- [ ] **Step 6: 跑测试确认失败**

```bash
uv run pytest tests/unit/test_task_contract.py -v
```

预期：FAIL，`ModuleNotFoundError: No module named 'research_assistant.contracts.task'`

- [ ] **Step 7: 实现 contracts/task.py**

```python
"""八字段任务合同。

Task 是纯值对象，不触文件系统：材料是否存在由 runtime/context.load_task_context 校验。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from research_assistant.errors import TaskContractError


class TaskFunction(StrEnum):
    CHAT = "Chat"
    LITERATURE_RETRIEVAL = "Literature_Retrieval"
    SURVEY = "Survey"
    REPRODUCTION = "Reproduction"
    IDEA = "Idea"


SCHEMA_REF_BY_FUNCTION: Mapping[TaskFunction, str] = MappingProxyType(
    {member: member.value for member in TaskFunction}
)

# 规范允许 A-01 至 A-100，不得假定只有两位后缀
CASE_ID_PATTERN = re.compile(r"^[A-E]-\d{1,3}$")

STRING_FIELDS: tuple[str, ...] = (
    "case_id",
    "function",
    "title",
    "input",
    "expected_output",
    "output_schema_ref",
)
LIST_FIELDS: tuple[str, ...] = ("materials", "constraints")
KNOWN_FIELDS: frozenset[str] = frozenset(STRING_FIELDS + LIST_FIELDS)


@dataclass(frozen=True)
class Task:
    case_id: str
    function: TaskFunction
    title: str
    input: str
    materials: tuple[str, ...]
    constraints: tuple[str, ...]
    expected_output: str
    output_schema_ref: str
    unknown_fields: Mapping[str, Any]

    @classmethod
    def parse(cls, payload: Mapping[str, Any]) -> Task:
        if not isinstance(payload, Mapping):
            raise TaskContractError("request body must be a JSON object")

        for field in STRING_FIELDS + LIST_FIELDS:
            if field not in payload:
                raise TaskContractError(f"missing required field: {field}")

        strings: dict[str, str] = {}
        for field in STRING_FIELDS:
            value = payload[field]
            if not isinstance(value, str):
                raise TaskContractError(f"field {field} must be a string")
            strings[field] = value

        lists: dict[str, tuple[str, ...]] = {}
        for field in LIST_FIELDS:
            value = payload[field]
            if not isinstance(value, list):
                raise TaskContractError(f"field {field} must be an array (empty array when unused)")
            for index, item in enumerate(value):
                if not isinstance(item, str):
                    raise TaskContractError(f"field {field}[{index}] must be a string")
            lists[field] = tuple(value)

        case_id = strings["case_id"]
        if not CASE_ID_PATTERN.fullmatch(case_id):
            raise TaskContractError(
                f"case_id {case_id!r} does not match the required <A-E>-<1..3 digits> form"
            )

        try:
            function = TaskFunction(strings["function"])
        except ValueError:
            allowed = ", ".join(member.value for member in TaskFunction)
            raise TaskContractError(
                f"function {strings['function']!r} is not one of: {allowed}"
            ) from None

        schema_ref = strings["output_schema_ref"]
        if SCHEMA_REF_BY_FUNCTION[function] != schema_ref:
            # 明确不猜路由
            raise TaskContractError(
                f"output_schema_ref {schema_ref!r} does not match function {function.value!r}"
            )

        unknown = {key: value for key, value in payload.items() if key not in KNOWN_FIELDS}

        return cls(
            case_id=case_id,
            function=function,
            title=strings["title"],
            input=strings["input"],
            materials=lists["materials"],
            constraints=lists["constraints"],
            expected_output=strings["expected_output"],
            output_schema_ref=schema_ref,
            unknown_fields=MappingProxyType(unknown),
        )
```

注意 `dataclass(frozen=True)` 已使实例不可变且可哈希；`MappingProxyType` 让 `unknown_fields` 只读。`test_task_is_hashable_and_frozen` 用 `pytest.raises(Exception)` 是因为 frozen dataclass 抛 `FrozenInstanceError`（`AttributeError` 子类）。

- [ ] **Step 8: 跑测试确认通过**

```bash
uv run pytest tests/unit/test_task_contract.py tests/unit/test_errors.py -v
```

预期：PASS（全部）

- [ ] **Step 9: 提交**

```bash
git add src/research_assistant/errors.py src/research_assistant/contracts/task.py \
        tests/unit/test_errors.py tests/unit/test_task_contract.py
git commit -m "feat: add error hierarchy and eight-field task contract"
```

---

### Task 3: 配置适配器（整组探测 + 冲突报错）

**Files:**
- Create: `src/research_assistant/runtime/settings.py`
- Test: `tests/unit/test_settings.py`

**Interfaces:**
- Consumes: `ConfigError`（Task 2）
- Produces:
  - `GatewaySettings(family: Literal["challenge","platform"], base_url: str, api_key: str, model: str, session_id: str | None)`；`__repr__` 与 `__str__` 屏蔽 `api_key`
  - `Settings(gateway: GatewaySettings | None, gateway_config_error: str | None, team: str, workspace_root: Path, papers_root: Path, case_budget_seconds: float, log_level: str)`
  - `Settings.from_env(env: Mapping[str, str]) -> Settings`（**不抛异常**）
  - `Settings.require_gateway() -> GatewaySettings`（缺失或冲突时抛 `ConfigError`）
  - 常量 `CHALLENGE_GROUP`、`PLATFORM_GROUP`

- [ ] **Step 1: 写失败测试**

`tests/unit/test_settings.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from research_assistant.errors import ConfigError
from research_assistant.runtime.settings import Settings

CHALLENGE = {
    "CHALLENGE_LLM_BASE_URL": "http://gw.invalid/v1",
    "CHALLENGE_LLM_API_KEY": "sk-challenge",
    "CHALLENGE_LLM_MODEL": "model-c",
}
PLATFORM = {
    "PLATFORM_LLM_URL": "http://gw.invalid/v1",
    "PLATFORM_API_KEY": "sk-platform",
    "PLATFORM_LLM_MODEL": "model-p",
}


def test_challenge_group_is_used_when_complete() -> None:
    settings = Settings.from_env(CHALLENGE)
    gw = settings.require_gateway()
    assert gw.family == "challenge"
    assert gw.base_url == "http://gw.invalid/v1"
    assert gw.model == "model-c"
    assert gw.session_id is None
    assert settings.gateway_config_error is None


def test_platform_group_is_used_when_challenge_absent() -> None:
    gw = Settings.from_env(PLATFORM).require_gateway()
    assert gw.family == "platform"
    assert gw.base_url == "http://gw.invalid/v1"
    assert gw.model == "model-p"


def test_session_id_is_picked_up_from_challenge_group() -> None:
    env = {**CHALLENGE, "CHALLENGE_SESSION_ID": "sess-42"}
    assert Settings.from_env(env).require_gateway().session_id == "sess-42"


def test_challenge_wins_when_both_groups_are_identical() -> None:
    env = {
        "CHALLENGE_LLM_BASE_URL": "http://same/v1",
        "CHALLENGE_LLM_API_KEY": "sk-same",
        "CHALLENGE_LLM_MODEL": "m",
        "PLATFORM_LLM_URL": "http://same/v1",
        "PLATFORM_API_KEY": "sk-same",
        "PLATFORM_LLM_MODEL": "m",
    }
    settings = Settings.from_env(env)
    assert settings.require_gateway().family == "challenge"
    assert settings.gateway_config_error is None


def test_conflicting_groups_raise_config_error_without_leaking_values() -> None:
    env = {**CHALLENGE, **PLATFORM}
    settings = Settings.from_env(env)
    with pytest.raises(ConfigError) as excinfo:
        settings.require_gateway()
    message = str(excinfo.value)
    assert "sk-challenge" not in message
    assert "sk-platform" not in message
    assert settings.gateway is None


def test_incomplete_challenge_group_falls_through_to_platform() -> None:
    env = {"CHALLENGE_LLM_BASE_URL": "http://gw.invalid/v1", **PLATFORM}
    assert Settings.from_env(env).require_gateway().family == "platform"


def test_empty_string_treated_as_missing() -> None:
    env = {**CHALLENGE, "CHALLENGE_LLM_API_KEY": "   "}
    settings = Settings.from_env(env)
    assert settings.gateway is None
    assert settings.gateway_config_error is not None


def test_no_group_configured_leaves_gateway_none_and_reports_reason() -> None:
    settings = Settings.from_env({})
    assert settings.gateway is None
    assert "CHALLENGE" in (settings.gateway_config_error or "")
    with pytest.raises(ConfigError):
        settings.require_gateway()


def test_per_variable_mixing_is_forbidden() -> None:
    """只有 URL 来自 CHALLENGE、key 来自 PLATFORM 时不得拼出一个可用配置。"""
    env = {
        "CHALLENGE_LLM_BASE_URL": "http://gw.invalid/v1",
        "PLATFORM_API_KEY": "sk-platform",
        "PLATFORM_LLM_MODEL": "model-p",
    }
    settings = Settings.from_env(env)
    # PLATFORM 组缺 URL，CHALLENGE 组缺 key 与 model → 两组都不齐备
    assert settings.gateway is None
    with pytest.raises(ConfigError):
        settings.require_gateway()


def test_api_key_never_appears_in_repr_or_str() -> None:
    gw = Settings.from_env(CHALLENGE).require_gateway()
    assert "sk-challenge" not in repr(gw)
    assert "sk-challenge" not in str(gw)
    assert "sk-challenge" not in repr(Settings.from_env(CHALLENGE))


def test_workspace_and_team_defaults() -> None:
    settings = Settings.from_env(CHALLENGE)
    assert settings.team == ""
    assert settings.workspace_root == Path("/output")
    assert settings.papers_root == Path("/data/papers")
    assert settings.case_budget_seconds == 180.0
    assert settings.log_level == "INFO"


def test_workspace_overrides_are_honoured() -> None:
    env = {
        **CHALLENGE,
        "RESEARCH_WORKSPACE_TEAM": "测试队伍 A+1",
        "RESEARCH_WORKSPACE_ROOT": "/tmp/ws",
        "RESEARCH_PAPERS_ROOT": "/tmp/papers",
        "RESEARCH_CASE_BUDGET_SECONDS": "90",
        "AGENTHACK_LOG_LEVEL": "DEBUG",
    }
    settings = Settings.from_env(env)
    assert settings.team == "测试队伍 A+1"
    assert settings.workspace_root == Path("/tmp/ws")
    assert settings.papers_root == Path("/tmp/papers")
    assert settings.case_budget_seconds == 90.0
    assert settings.log_level == "DEBUG"


def test_non_positive_budget_is_rejected() -> None:
    env = {**CHALLENGE, "RESEARCH_CASE_BUDGET_SECONDS": "0"}
    with pytest.raises(ConfigError, match="RESEARCH_CASE_BUDGET_SECONDS"):
        Settings.from_env(env)


def test_non_numeric_budget_is_rejected() -> None:
    env = {**CHALLENGE, "RESEARCH_CASE_BUDGET_SECONDS": "abc"}
    with pytest.raises(ConfigError, match="RESEARCH_CASE_BUDGET_SECONDS"):
        Settings.from_env(env)


def test_base_url_path_is_preserved_verbatim() -> None:
    """不得裁剪或自猜补 /v1。"""
    env = {**CHALLENGE, "CHALLENGE_LLM_BASE_URL": "http://gw.invalid:3000/custom/prefix/v1"}
    assert Settings.from_env(env).require_gateway().base_url == (
        "http://gw.invalid:3000/custom/prefix/v1"
    )
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/unit/test_settings.py -v
```

预期：FAIL，`ModuleNotFoundError: No module named 'research_assistant.runtime.settings'`

- [ ] **Step 3: 实现 runtime/settings.py**

```python
"""运行时配置适配器。

两组互斥的 Gateway 变量写法按「整组」探测，禁止逐变量混合 fallback。
from_env 不抛网关相关异常，以便 /health 在配置缺失时仍能返回 2xx。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from research_assistant.errors import ConfigError

CHALLENGE_GROUP: tuple[tuple[str, str], ...] = (
    ("base_url", "CHALLENGE_LLM_BASE_URL"),
    ("api_key", "CHALLENGE_LLM_API_KEY"),
    ("model", "CHALLENGE_LLM_MODEL"),
)
PLATFORM_GROUP: tuple[tuple[str, str], ...] = (
    ("base_url", "PLATFORM_LLM_URL"),
    ("api_key", "PLATFORM_API_KEY"),
    ("model", "PLATFORM_LLM_MODEL"),
)
CHALLENGE_SESSION_VAR = "CHALLENGE_SESSION_ID"

DEFAULT_WORKSPACE_ROOT = Path("/output")
DEFAULT_PAPERS_ROOT = Path("/data/papers")
DEFAULT_CASE_BUDGET_SECONDS = 180.0


@dataclass(frozen=True)
class GatewaySettings:
    family: Literal["challenge", "platform"]
    base_url: str
    api_key: str
    model: str
    session_id: str | None = None

    def __repr__(self) -> str:
        return (
            f"GatewaySettings(family={self.family!r}, base_url={self.base_url!r}, "
            f"model={self.model!r}, api_key='***', "
            f"session_id={'set' if self.session_id else None})"
        )

    __str__ = __repr__


def _clean(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _read_group(
    env: Mapping[str, str], group: tuple[tuple[str, str], ...]
) -> dict[str, str] | None:
    values: dict[str, str] = {}
    for key, var in group:
        cleaned = _clean(env, var)
        if cleaned is None:
            return None
        values[key] = cleaned
    return values


@dataclass(frozen=True)
class Settings:
    gateway: GatewaySettings | None
    gateway_config_error: str | None
    team: str
    workspace_root: Path
    case_budget_seconds: float
    papers_root: Path = DEFAULT_PAPERS_ROOT
    log_level: str = "INFO"

    def require_gateway(self) -> GatewaySettings:
        if self.gateway is None:
            raise ConfigError(
                self.gateway_config_error
                or "model gateway is not configured; expected a complete CHALLENGE_LLM_* "
                "or PLATFORM_LLM_* group"
            )
        return self.gateway

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source: Mapping[str, str] = os.environ if env is None else env

        challenge = _read_group(source, CHALLENGE_GROUP)
        platform = _read_group(source, PLATFORM_GROUP)
        gateway: GatewaySettings | None = None
        config_error: str | None = None

        if challenge is not None and platform is not None:
            if challenge == platform:
                gateway = GatewaySettings(
                    family="challenge",
                    session_id=_clean(source, CHALLENGE_SESSION_VAR),
                    **challenge,
                )
            else:
                differing = sorted(k for k in challenge if challenge[k] != platform[k])
                config_error = (
                    "both CHALLENGE_LLM_* and PLATFORM_LLM_* groups are set but disagree on: "
                    + ", ".join(differing)
                    + "; per-variable mixing is not allowed"
                )
        elif challenge is not None:
            gateway = GatewaySettings(
                family="challenge",
                session_id=_clean(source, CHALLENGE_SESSION_VAR),
                **challenge,
            )
        elif platform is not None:
            gateway = GatewaySettings(family="platform", **platform)
        else:
            config_error = (
                "no complete gateway group found; expected all of "
                "CHALLENGE_LLM_BASE_URL/CHALLENGE_LLM_API_KEY/CHALLENGE_LLM_MODEL "
                "or all of PLATFORM_LLM_URL/PLATFORM_API_KEY/PLATFORM_LLM_MODEL"
            )

        budget_raw = _clean(source, "RESEARCH_CASE_BUDGET_SECONDS")
        if budget_raw is None:
            budget = DEFAULT_CASE_BUDGET_SECONDS
        else:
            try:
                budget = float(budget_raw)
            except ValueError:
                raise ConfigError(
                    f"RESEARCH_CASE_BUDGET_SECONDS must be a number, got {budget_raw!r}"
                ) from None
            if budget <= 0:
                raise ConfigError(
                    f"RESEARCH_CASE_BUDGET_SECONDS must be positive, got {budget_raw!r}"
                )

        return cls(
            gateway=gateway,
            gateway_config_error=config_error,
            team=_clean(source, "RESEARCH_WORKSPACE_TEAM") or "",
            workspace_root=Path(_clean(source, "RESEARCH_WORKSPACE_ROOT") or DEFAULT_WORKSPACE_ROOT),
            papers_root=Path(_clean(source, "RESEARCH_PAPERS_ROOT") or DEFAULT_PAPERS_ROOT),
            case_budget_seconds=budget,
            log_level=_clean(source, "AGENTHACK_LOG_LEVEL") or "INFO",
        )
```

`GatewaySettings(family=..., session_id=..., **challenge)` 依赖 `challenge` 的键恰好是 `base_url/api_key/model`，与 dataclass 字段名一致。

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run pytest tests/unit/test_settings.py -v
```

预期：PASS（15 个测试）

- [ ] **Step 5: 提交**

```bash
git add src/research_assistant/runtime/settings.py tests/unit/test_settings.py
git commit -m "feat: add gateway config adapter with whole-group detection"
```

---

### Task 4: 预算 Deadline 与脱敏日志

**Files:**
- Create: `src/research_assistant/runtime/deadline.py`
- Create: `src/research_assistant/runtime/logging.py`
- Test: `tests/unit/test_deadline.py`
- Test: `tests/unit/test_logging.py`

**Interfaces:**
- Consumes: `BudgetExhausted`（Task 2）
- Produces:
  - `Deadline.start(budget_seconds: float) -> Deadline`
  - `Deadline(started_at: float, budget_seconds: float)`；`.remaining() -> float`、`.elapsed() -> float`、`.take(stage: str, minimum: float) -> float`、`.guard(stage: str) -> ContextManager[None]`、`.expired -> bool`
  - `BoundLogger` Protocol：`.info(event, **fields)`、`.warning(event, **fields)`、`.error(event, **fields)`、`.bind(**fields) -> BoundLogger`
  - `JsonLogger(name: str, level: str = "INFO", stream: TextIO | None = None)`，实现 `BoundLogger`
  - `REDACTED_KEYS: frozenset[str]`、`redact(fields: Mapping[str, Any]) -> dict[str, Any]`

- [ ] **Step 1: 写 Deadline 失败测试**

`tests/unit/test_deadline.py`:

```python
from __future__ import annotations

import time

import pytest

from research_assistant.errors import BudgetExhausted
from research_assistant.runtime.deadline import Deadline


def test_start_uses_monotonic_and_reports_full_budget() -> None:
    before = time.monotonic()
    deadline = Deadline.start(180.0)
    after = time.monotonic()
    assert before <= deadline.started_at <= after
    assert deadline.budget_seconds == 180.0
    assert 179.0 < deadline.remaining() <= 180.0


def test_remaining_decreases_as_time_passes() -> None:
    deadline = Deadline(started_at=time.monotonic() - 30.0, budget_seconds=180.0)
    assert deadline.remaining() == pytest.approx(150.0, abs=0.5)
    assert deadline.elapsed() == pytest.approx(30.0, abs=0.5)
    assert deadline.expired is False


def test_expired_when_budget_consumed() -> None:
    deadline = Deadline(started_at=time.monotonic() - 200.0, budget_seconds=180.0)
    assert deadline.expired is True
    assert deadline.remaining() < 0


def test_take_returns_remaining_when_sufficient() -> None:
    deadline = Deadline(started_at=time.monotonic(), budget_seconds=180.0)
    assert deadline.take("model.complete", minimum=10.0) == pytest.approx(180.0, abs=1.0)


def test_take_raises_when_remaining_below_minimum() -> None:
    deadline = Deadline(started_at=time.monotonic() - 175.0, budget_seconds=180.0)
    with pytest.raises(BudgetExhausted, match="model.complete"):
        deadline.take("model.complete", minimum=10.0)


def test_take_raises_when_already_expired() -> None:
    deadline = Deadline(started_at=time.monotonic() - 181.0, budget_seconds=180.0)
    with pytest.raises(BudgetExhausted):
        deadline.take("persist", minimum=0.1)


def test_take_error_message_contains_stage_and_numbers_not_secrets() -> None:
    deadline = Deadline(started_at=time.monotonic() - 179.0, budget_seconds=180.0)
    with pytest.raises(BudgetExhausted) as excinfo:
        deadline.take("d.generate_source", minimum=20.0)
    message = str(excinfo.value)
    assert "d.generate_source" in message
    assert excinfo.value.case_id is None


def test_take_propagates_case_id() -> None:
    deadline = Deadline(started_at=time.monotonic() - 179.0, budget_seconds=180.0)
    with pytest.raises(BudgetExhausted) as excinfo:
        deadline.take("stage", minimum=20.0, case_id="D-05")
    assert excinfo.value.case_id == "D-05"


def test_guard_passes_when_budget_remains() -> None:
    deadline = Deadline.start(180.0)
    with deadline.guard("fast.stage"):
        pass


def test_guard_raises_when_stage_overruns() -> None:
    deadline = Deadline(started_at=time.monotonic() - 179.9, budget_seconds=180.0)
    with pytest.raises(BudgetExhausted, match="slow.stage"):
        with deadline.guard("slow.stage"):
            time.sleep(0.2)


def test_deadline_is_frozen() -> None:
    deadline = Deadline.start(10.0)
    with pytest.raises(Exception):
        deadline.budget_seconds = 5.0  # type: ignore[misc]
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/unit/test_deadline.py -v
```

预期：FAIL，`ModuleNotFoundError: No module named 'research_assistant.runtime.deadline'`

- [ ] **Step 3: 实现 runtime/deadline.py**

```python
"""monotonic 预算，沿调用链显式传递。

平台侧的 max_duration_ms=5400000 是阶段总上限，不由单题 Agent 分配；
timeslice_budget=300 的单位未确认，本模块不使用它。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from research_assistant.errors import BudgetExhausted


@dataclass(frozen=True)
class Deadline:
    started_at: float
    budget_seconds: float

    @classmethod
    def start(cls, budget_seconds: float) -> Deadline:
        return cls(started_at=time.monotonic(), budget_seconds=budget_seconds)

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def remaining(self) -> float:
        return self.budget_seconds - self.elapsed()

    @property
    def expired(self) -> bool:
        return self.remaining() <= 0.0

    def take(self, stage: str, minimum: float, *, case_id: str | None = None) -> float:
        """领取本阶段可用秒数；不足 minimum 时抛 BudgetExhausted。"""
        remaining = self.remaining()
        if remaining < minimum:
            raise BudgetExhausted(
                f"stage {stage} needs at least {minimum:.3f}s but only "
                f"{remaining:.3f}s of {self.budget_seconds:.3f}s remains",
                case_id=case_id,
            )
        return remaining

    @contextmanager
    def guard(self, stage: str, *, case_id: str | None = None) -> Iterator[None]:
        """with 块结束时若已超支则抛 BudgetExhausted。"""
        yield
        if self.expired:
            raise BudgetExhausted(
                f"stage {stage} overran the budget of {self.budget_seconds:.3f}s "
                f"by {-self.remaining():.3f}s",
                case_id=case_id,
            )
```

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run pytest tests/unit/test_deadline.py -v
```

预期：PASS（11 个测试）

- [ ] **Step 5: 写日志失败测试**

`tests/unit/test_logging.py`:

```python
from __future__ import annotations

import io
import json

import pytest

from research_assistant.runtime.logging import JsonLogger, redact


@pytest.fixture
def stream() -> io.StringIO:
    return io.StringIO()


def read_lines(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def test_emits_one_json_line_per_event(stream: io.StringIO) -> None:
    log = JsonLogger("test", stream=stream)
    log.info("solve.started", case_id="A-01")
    (record,) = read_lines(stream)
    assert record["event"] == "solve.started"
    assert record["level"] == "info"
    assert record["case_id"] == "A-01"
    assert "ts" in record


def test_bound_fields_appear_on_every_record(stream: io.StringIO) -> None:
    log = JsonLogger("test", stream=stream).bind(case_id="D-05", dispatch_id="d-1")
    log.info("stage.start", stage="generate")
    log.warning("stage.retry", stage="generate")
    records = read_lines(stream)
    assert all(r["case_id"] == "D-05" for r in records)
    assert all(r["dispatch_id"] == "d-1" for r in records)


def test_bind_does_not_mutate_the_parent_logger(stream: io.StringIO) -> None:
    parent = JsonLogger("test", stream=stream)
    parent.bind(case_id="A-01").info("child")
    parent.info("parent")
    records = read_lines(stream)
    assert records[0]["case_id"] == "A-01"
    assert "case_id" not in records[1]


def test_call_kwargs_override_bound_fields(stream: io.StringIO) -> None:
    log = JsonLogger("test", stream=stream).bind(stage="old")
    log.info("event", stage="new")
    assert read_lines(stream)[0]["stage"] == "new"


def test_level_filtering_drops_lower_levels(stream: io.StringIO) -> None:
    log = JsonLogger("test", level="ERROR", stream=stream)
    log.info("ignored")
    log.warning("ignored")
    log.error("kept")
    assert [r["event"] for r in read_lines(stream)] == ["kept"]


def test_debug_level_keeps_everything(stream: io.StringIO) -> None:
    log = JsonLogger("test", level="DEBUG", stream=stream)
    log.debug("d")
    log.info("i")
    assert len(read_lines(stream)) == 2


@pytest.mark.parametrize(
    "key",
    ["api_key", "apikey", "authorization", "password", "secret", "token",
     "CHALLENGE_LLM_API_KEY", "PLATFORM_API_KEY", "X-Session-ID", "headers"],
)
def test_sensitive_keys_are_redacted(key: str) -> None:
    assert redact({key: "sk-live-secret"})[key] == "***"


def test_redaction_is_recursive() -> None:
    out = redact({"nested": {"api_key": "sk-x", "keep": 1}})
    assert out["nested"] == {"api_key": "***", "keep": 1}


def test_redaction_handles_lists_of_dicts() -> None:
    out = redact({"items": [{"token": "t"}, {"ok": True}]})
    assert out["items"] == [{"token": "***"}, {"ok": True}]


def test_secret_never_reaches_the_stream(stream: io.StringIO) -> None:
    log = JsonLogger("test", stream=stream)
    log.info("gateway.call", api_key="sk-live-secret", model="m")
    text = stream.getvalue()
    assert "sk-live-secret" not in text
    assert '"api_key": "***"' in text


def test_non_secret_fields_pass_through(stream: io.StringIO) -> None:
    log = JsonLogger("test", stream=stream)
    log.info("stage.done", stage="persist", duration_ms=12.5, finish_reason="stop",
             prompt_tokens=9, completion_tokens=2, source_sha256="a" * 64)
    record = read_lines(stream)[0]
    assert record["finish_reason"] == "stop"
    assert record["prompt_tokens"] == 9
    assert record["source_sha256"] == "a" * 64


def test_unserialisable_values_are_stringified_not_raised(stream: io.StringIO) -> None:
    log = JsonLogger("test", stream=stream)
    log.info("weird", obj=object())
    assert read_lines(stream)[0]["event"] == "weird"
```

- [ ] **Step 6: 跑测试确认失败**

```bash
uv run pytest tests/unit/test_logging.py -v
```

预期：FAIL，`ModuleNotFoundError: No module named 'research_assistant.runtime.logging'`

- [ ] **Step 7: 实现 runtime/logging.py**

```python
"""脱敏结构化日志。

允许：错误类型、阶段耗时、finish_reason、usage 计数、SHA-256 摘要、版本号、case_id、dispatch_id。
禁止：请求头原文、API key、全量环境变量、论文正文全文、模型响应全文。
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Mapping
from typing import Any, Protocol, TextIO

REDACTED_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "password",
        "secret",
        "token",
        "headers",
        "challenge_llm_api_key",
        "platform_api_key",
        "x-session-id",
        "challenge_session_id",
    }
)
REDACTED = "***"

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}


class BoundLogger(Protocol):
    def debug(self, event: str, **fields: Any) -> None: ...
    def info(self, event: str, **fields: Any) -> None: ...
    def warning(self, event: str, **fields: Any) -> None: ...
    def error(self, event: str, **fields: Any) -> None: ...
    def bind(self, **fields: Any) -> "BoundLogger": ...


def _is_sensitive(key: str) -> bool:
    normalised = key.strip().lower().replace("_", "").replace("-", "")
    return any(candidate.replace("_", "").replace("-", "") in normalised
               for candidate in REDACTED_KEYS)


def redact(fields: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in fields.items():
        if _is_sensitive(key):
            out[key] = REDACTED
        elif isinstance(value, Mapping):
            out[key] = redact(value)
        elif isinstance(value, (list, tuple)):
            out[key] = [redact(item) if isinstance(item, Mapping) else item for item in value]
        else:
            out[key] = value
    return out


class JsonLogger:
    """每行一个 JSON 对象；bind 返回新实例，不修改父级。"""

    def __init__(
        self,
        name: str,
        level: str = "INFO",
        stream: TextIO | None = None,
        bound: Mapping[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.level = level.upper()
        self._stream = stream if stream is not None else sys.stderr
        self._bound: dict[str, Any] = dict(bound or {})

    def bind(self, **fields: Any) -> JsonLogger:
        return JsonLogger(self.name, self.level, self._stream, {**self._bound, **fields})

    def _enabled(self, level: str) -> bool:
        return _LEVELS[level] >= _LEVELS.get(self.level, 20)

    def _emit(self, level: str, event: str, fields: Mapping[str, Any]) -> None:
        if not self._enabled(level):
            return
        record: dict[str, Any] = {
            "ts": round(time.time(), 3),
            "level": level.lower(),
            "logger": self.name,
            "event": event,
        }
        record.update(redact(self._bound))
        record.update(redact(fields))
        try:
            line = json.dumps(record, ensure_ascii=False, default=str, sort_keys=False)
        except (TypeError, ValueError):
            line = json.dumps({"level": level.lower(), "event": event, "unserialisable": True})
        print(line, file=self._stream, flush=True)

    def debug(self, event: str, **fields: Any) -> None:
        self._emit("DEBUG", event, fields)

    def info(self, event: str, **fields: Any) -> None:
        self._emit("INFO", event, fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._emit("WARNING", event, fields)

    def error(self, event: str, **fields: Any) -> None:
        self._emit("ERROR", event, fields)
```

`_is_sensitive` 做去分隔符的子串匹配，因此 `CHALLENGE_LLM_API_KEY`、`X-Session-ID` 这类变体都会被挡住。

- [ ] **Step 8: 跑测试确认通过**

```bash
uv run pytest tests/unit/test_logging.py tests/unit/test_deadline.py -v
```

预期：PASS（全部）

- [ ] **Step 9: 提交**

```bash
git add src/research_assistant/runtime/deadline.py src/research_assistant/runtime/logging.py \
        tests/unit/test_deadline.py tests/unit/test_logging.py
git commit -m "feat: add monotonic deadline budget and redacted json logger"
```

---

### Task 5: 授权路径解析

**Files:**
- Create: `src/research_assistant/runtime/paths.py`
- Test: `tests/unit/test_paths.py`

**Interfaces:**
- Consumes: `PathAuthorizationError`、`ConfigError`（Task 2）
- Produces:
  - `WorkspacePaths(root: Path, team: str, papers_root: Path)`
  - 属性：`root`、`team`、`team_dir`、`assigned_dir`、`dispatch_file`、`expert_dir`、`figure_dir`
  - 方法：`paper(material: str) -> Path`、`result_json(case_id: str) -> Path`、`survey_md(case_id: str) -> Path`、`idea_md(case_id: str) -> Path`、`work_dir(case_id: str) -> Path`、`question_file(case_id: str) -> Path`、`relative_to_output(path: Path) -> str`、`assert_own_expert_file(path: Path) -> Path`
  - 常量 `SURVEY_INFIX = "综述"`、`IDEA_INFIX = "灵感Idea报告"`

- [ ] **Step 1: 写失败测试**

`tests/unit/test_paths.py`:

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/unit/test_paths.py -v
```

预期：FAIL，`ModuleNotFoundError: No module named 'research_assistant.runtime.paths'`

- [ ] **Step 3: 实现 runtime/paths.py**

```python
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
_CASE_ID_SEGMENT = re.compile(r"^[A-E]-\d{1,3}$")


def _reject_unsafe(raw: str, *, what: str) -> None:
    if not raw or not raw.strip():
        raise PathAuthorizationError(f"{what} must not be empty")
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
        base_resolved = base.resolve()
        candidate = (base / raw).resolve()
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
        return self._case_path(
            case_id, self.expert_dir, f"{case_id}-{IDEA_INFIX}-{self.team}.md"
        )

    def work_dir(self, case_id: str) -> Path:
        return self._case_path(case_id, self.team_dir / "work", case_id)

    def relative_to_output(self, path: Path) -> str:
        """产出 JSON 内使用的 path：相对工作区根，正斜杠，不含前导 /。"""
        resolved = path.resolve()
        if not _contains(self._root_resolved, resolved):
            raise PathAuthorizationError(f"path is outside the workspace root: {path!r}")
        return resolved.relative_to(self._root_resolved).as_posix()

    def assert_own_expert_file(self, path: Path) -> Path:
        """共享的 data/expert 内只允许操作本队规范命名的文件。"""
        resolved = path.resolve()
        expert_resolved = self.expert_dir.resolve()
        if not _contains(expert_resolved, resolved):
            raise PathAuthorizationError(f"path is not inside data/expert: {path!r}")
        suffixes = (
            f"-{SURVEY_INFIX}-{self.team}.md",
            f"-{IDEA_INFIX}-{self.team}.md",
        )
        if not resolved.name.endswith(suffixes):
            raise PathAuthorizationError(
                f"expert file does not follow this team's naming rule: {resolved.name!r}"
            )
        return resolved
```

`str.endswith` 接受元组，因此一次判断综述与 Idea 两种合法后缀；文件名由 `survey_md` / `idea_md` 构造，队伍名嵌在中间，他队报告必然不匹配。

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run pytest tests/unit/test_paths.py -v
```

预期：PASS（全部）。若 `test_symlink_escape_is_rejected` 失败，检查 `_resolve_within` 是否对 `base` 也调了 `resolve()`——macOS 上 `/tmp` 本身是指向 `/private/tmp` 的符号链接，两边都必须归一。

- [ ] **Step 5: 提交**

```bash
git add src/research_assistant/runtime/paths.py tests/unit/test_paths.py
git commit -m "feat: add authorized workspace path resolution"
```

---

### Task 6: 五类结果构造器与原子落盘

**Files:**
- Create: `src/research_assistant/contracts/results.py`
- Create: `src/research_assistant/runtime/persistence.py`
- Test: `tests/unit/test_results.py`
- Test: `tests/unit/test_persistence.py`

**Interfaces:**
- Consumes: `TaskFunction`（Task 2）、`PersistenceError`（Task 2）
- Produces:
  - `Metric(name: str, unit: str, reference: float | None, observed: float | None, relative_error: float | None)`、`Metric.to_json() -> dict[str, Any]`
  - `ALLOWED_TOP_LEVEL_FIELDS: Mapping[TaskFunction, frozenset[str]]`
  - `chat_result(case_id, *, answer, clarifying_question) -> dict[str, Any]`
  - `retrieval_result(case_id, *, papers) -> dict[str, Any]`
  - `survey_result(case_id, *, path, markdown) -> dict[str, Any]`
  - `idea_result(case_id, *, path, markdown) -> dict[str, Any]`
  - `reproduction_result(case_id, *, status, metrics, figures) -> dict[str, Any]`
  - `REPRODUCTION_STATUSES: frozenset[str]`（`completed`/`failed`/`materials_missing`）
  - `validate_result_shape(function: TaskFunction, result: Mapping[str, Any]) -> None`
  - `serialize_result(payload: Mapping[str, Any]) -> bytes`
  - `MIN_BODY_CHARACTERS = 800`
  - `write_bytes_verified(path: Path, data: bytes) -> bytes`
  - `write_text_verified(path: Path, text: str) -> bytes`

- [ ] **Step 1: 写 results 失败测试**

`tests/unit/test_results.py`:

```python
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
    assert ALLOWED_TOP_LEVEL_FIELDS[TaskFunction.SURVEY] == frozenset({"case_id", "path", "markdown"})
    assert ALLOWED_TOP_LEVEL_FIELDS[TaskFunction.IDEA] == frozenset({"case_id", "path", "markdown"})
    assert ALLOWED_TOP_LEVEL_FIELDS[TaskFunction.REPRODUCTION] == frozenset(
        {"case_id", "status", "metrics", "figures"}
    )


def test_chat_result_has_exactly_three_fields_in_order() -> None:
    result = chat_result("A-01", answer="光照强度影响光合速率。", clarifying_question="研究哪种植物？")
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
        Metric(name="Macro-F1 / 分数", unit="", reference=0.82, observed=0.80, relative_error=0.024),
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
        Metric(name="公平性 Jain 指数 / 分数", unit="", reference=0.91, observed=None,
               relative_error=None),
        Metric(name="归一化吞吐 / %", unit="%", reference=88.0, observed=None, relative_error=None),
        Metric(name="平均等待时间 / ms", unit="ms", reference=120.0, observed=None,
               relative_error=None),
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
        "Macro-F1 / 分数", "Accuracy / %", "运行时间 / 秒",
    ]


def test_figures_must_be_relative_strings() -> None:
    with pytest.raises(TaskContractError, match="figures"):
        reproduction_result(
            "D-01", status="completed", metrics=three_metrics(),
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
        validate_result_shape(TaskFunction.SURVEY,
                             chat_result("C-01", answer="a", clarifying_question="q"))


def test_serialize_keeps_non_ascii_characters_readable() -> None:
    data = serialize_result(chat_result("A-01", answer="光照", clarifying_question="哪种植物？"))
    assert "光照".encode("utf-8") in data
    assert b"\\u" not in data


def test_nan_metric_is_rejected_at_construction() -> None:
    """构造器就先挡住 NaN，不用等到序列化。"""
    metrics = [
        Metric(name="Macro-F1 / 分数", unit="", reference=0.8, observed=math.nan,
               relative_error=None),
        Metric(name="Accuracy / %", unit="%", reference=90.0, observed=1.0, relative_error=None),
        Metric(name="运行时间 / 秒", unit="秒", reference=1.0, observed=2.0, relative_error=None),
    ]
    with pytest.raises(TaskContractError, match="finite"):
        reproduction_result("D-01", status="completed", metrics=metrics, figures=[])


def test_infinity_metric_is_rejected_at_construction() -> None:
    metrics = [
        Metric(name="Macro-F1 / 分数", unit="", reference=0.8, observed=math.inf,
               relative_error=None),
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
        Metric(name="Accuracy / %", unit="%", reference=90.0, observed="90.0",  # type: ignore[arg-type]
               relative_error=None),
        Metric(name="Macro-F1 / 分数", unit="", reference=0.8, observed=0.7, relative_error=None),
        Metric(name="运行时间 / 秒", unit="秒", reference=1.0, observed=2.0, relative_error=None),
    ]
    with pytest.raises(TaskContractError, match="observed"):
        reproduction_result("D-01", status="completed", metrics=metrics, figures=[])


def test_serialized_output_has_no_code_fence_or_prefix() -> None:
    text = serialize_result(chat_result("A-01", answer="a", clarifying_question="q")).decode("utf-8")
    assert text.startswith("{")
    assert text.endswith("}")
    assert "```" not in text
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/unit/test_results.py -v
```

预期：FAIL，`ModuleNotFoundError: No module named 'research_assistant.contracts.results'`

- [ ] **Step 3: 实现 contracts/results.py**

```python
"""五类输出的构造器与字段白名单。

构造器是唯一能产出答案 JSON 的入口：任何额外顶层字段、空内容、非法数字都在这里被挡住。
"""

from __future__ import annotations

import json
import math
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


def retrieval_result(
    case_id: str, *, papers: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
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
                "filename": _require_non_blank(paper["filename"], f"papers[{index}].filename",
                                               case_id),
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


def reproduction_result(
    case_id: str,
    *,
    status: str,
    metrics: Sequence[Metric],
    figures: Sequence[str],
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
    figure_paths = [
        _require_relative_path(figure, f"figures[{i}]", case_id)
        for i, figure in enumerate(figures)
    ]
    return {
        "case_id": case_id,
        "status": status,
        "metrics": [metric.to_json(case_id=case_id) for metric in metrics],
        "figures": figure_paths,
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
```

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run pytest tests/unit/test_results.py -v
```

预期：PASS（全部）

- [ ] **Step 5: 写 persistence 失败测试**

`tests/unit/test_persistence.py`:

```python
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from research_assistant.errors import PersistenceError
from research_assistant.runtime.persistence import write_bytes_verified, write_text_verified


def test_write_bytes_creates_missing_parent_directories(tmp_path: Path) -> None:
    target = tmp_path / "data" / "队伍 A+1" / "A-01.json"
    write_bytes_verified(target, b'{"case_id": "A-01"}')
    assert target.read_bytes() == b'{"case_id": "A-01"}'


def test_write_bytes_returns_the_exact_bytes_written(tmp_path: Path) -> None:
    data = '{"case_id": "A-01", "answer": "光照"}'.encode("utf-8")
    returned = write_bytes_verified(tmp_path / "A-01.json", data)
    assert returned == data


def test_write_is_atomic_and_leaves_no_temp_file(tmp_path: Path) -> None:
    target = tmp_path / "A-01.json"
    write_bytes_verified(target, b"first")
    write_bytes_verified(target, b"second")
    assert target.read_bytes() == b"second"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["A-01.json"]


def test_existing_content_is_not_destroyed_when_write_fails(tmp_path: Path) -> None:
    target = tmp_path / "A-01.json"
    write_bytes_verified(target, b"good")
    blocker = tmp_path / "A-02.json"
    blocker.write_text("x", encoding="utf-8")
    blocker.chmod(0o444)
    os.chmod(tmp_path, 0o555)
    try:
        with pytest.raises(PersistenceError):
            write_bytes_verified(tmp_path / "new" / "A-03.json", b"data")
    finally:
        os.chmod(tmp_path, 0o755)
        blocker.chmod(0o644)
    assert target.read_bytes() == b"good"


def test_readback_mismatch_is_reported_as_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_read = Path.read_bytes

    def corrupted(self: Path, *args: object, **kwargs: object) -> bytes:
        return b"tampered" if self.name == "A-01.json" else real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", corrupted)
    with pytest.raises(PersistenceError, match="readback"):
        write_bytes_verified(tmp_path / "A-01.json", b"original")


def test_write_text_round_trips_utf8_verbatim(tmp_path: Path) -> None:
    body = "# 综述\n\n光照强度与光合速率正相关[1]。\n" * 100
    returned = write_text_verified(tmp_path / "C-01-综述-队.md", body)
    assert returned == body.encode("utf-8")
    assert (tmp_path / "C-01-综述-队.md").read_text(encoding="utf-8") == body


def test_write_text_rejects_non_string(tmp_path: Path) -> None:
    with pytest.raises(PersistenceError):
        write_text_verified(tmp_path / "x.md", 123)  # type: ignore[arg-type]


def test_unwritable_target_raises_persistence_error(tmp_path: Path) -> None:
    blocked = tmp_path / "readonly"
    blocked.mkdir()
    blocked.chmod(0o555)
    try:
        with pytest.raises(PersistenceError):
            write_bytes_verified(blocked / "A-01.json", b"data")
    finally:
        blocked.chmod(0o755)


def test_written_json_is_parseable(tmp_path: Path) -> None:
    target = tmp_path / "A-01.json"
    write_bytes_verified(target, json.dumps({"case_id": "A-01"}, ensure_ascii=False).encode("utf-8"))
    assert json.loads(target.read_text(encoding="utf-8")) == {"case_id": "A-01"}
```

- [ ] **Step 6: 跑测试确认失败**

```bash
uv run pytest tests/unit/test_persistence.py -v
```

预期：FAIL，`ModuleNotFoundError: No module named 'research_assistant.runtime.persistence'`

- [ ] **Step 7: 实现 runtime/persistence.py**

```python
"""原子落盘与回读校验。

写入失败、回读不一致或权限不足一律抛异常，绝不报告成功。
"""

from __future__ import annotations

import os
from pathlib import Path

from research_assistant.errors import PersistenceError


def _write_atomic(path: Path, data: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        raise PersistenceError(
            f"failed to write {path.name}: {exc.strerror or exc.__class__.__name__}"
        ) from exc


def _readback(path: Path, expected: bytes) -> None:
    try:
        actual = path.read_bytes()
    except OSError as exc:
        raise PersistenceError(
            f"readback failed for {path.name}: {exc.strerror or exc.__class__.__name__}"
        ) from exc
    if actual != expected:
        raise PersistenceError(
            f"readback mismatch for {path.name}: wrote {len(expected)} bytes, "
            f"read {len(actual)} bytes"
        )


def write_bytes_verified(path: Path, data: bytes) -> bytes:
    """临时文件 + os.replace 原子替换；回读断言逐字节一致；返回落盘字节。"""
    if not isinstance(data, bytes):
        raise PersistenceError(f"{path.name} requires bytes, got {type(data).__name__}")
    _write_atomic(path, data)
    _readback(path, data)
    return data


def write_text_verified(path: Path, text: str) -> bytes:
    """UTF-8 原样落盘后回读，断言与入参逐字节一致。用于 C/E 的 markdown。"""
    if not isinstance(text, str):
        raise PersistenceError(f"{path.name} requires str, got {type(text).__name__}")
    return write_bytes_verified(path, text.encode("utf-8"))
```

`test_readback_mismatch_is_reported_as_failure` 通过 monkeypatch `Path.read_bytes` 模拟回读被篡改；`_readback` 用 `path.read_bytes()` 因此会被拦截。注意 `Path.read_text` 内部也调 `read_bytes`，所以 monkeypatch 里对非目标文件名走原实现。

- [ ] **Step 8: 跑全部单测确认通过**

```bash
uv run pytest tests/unit -v
uv run ruff check .
uv run ruff format --check .
```

预期：全部 PASS，ruff 无告警。

若 `test_existing_content_is_not_destroyed_when_write_fails` 在 macOS 上因 root 权限绕过而失败，改用 `chmod 0o555` 作用于父目录并确认当前用户非 root（`id -u` 不为 0）。

- [ ] **Step 9: 提交**

```bash
git add src/research_assistant/contracts/results.py src/research_assistant/runtime/persistence.py \
        tests/unit/test_results.py tests/unit/test_persistence.py
git commit -m "feat: add five-category result builders and verified atomic persistence"
```

---

### Task 7: 模型网关与 SDK 遥测降级

**Files:**
- Create: `src/research_assistant/runtime/gateway.py`
- Create: `src/research_assistant/runtime/telemetry.py`
- Test: `tests/unit/test_gateway.py`
- Test: `tests/unit/test_telemetry.py`

**Interfaces:**
- Consumes: `GatewaySettings`（Task 3）、`Deadline`（Task 4）、`BoundLogger`（Task 4）、`GatewayError`/`BudgetExhausted`/`ConfigError`（Task 2）
- Produces:
  - `Message(role: Literal["system","user","assistant"], content: str)`、`Message.to_json() -> dict[str, str]`
  - `GatewayUsage(prompt_tokens, completion_tokens, total_tokens, cached_tokens)`（全部 `int | None`）、`GatewayUsage.from_response(usage: Mapping[str, Any] | None) -> GatewayUsage`
  - `GatewayResponse(content: str, finish_reason: str | None, usage: GatewayUsage, reasoning: str | None, raw: Mapping[str, Any])`
  - `Gateway` Protocol：`complete(messages: Sequence[Message], *, context: SolveContext, stage: str, max_tokens: int | None = None) -> GatewayResponse`
  - `HttpGateway(gateway: GatewaySettings, *, log: BoundLogger | None = None, transport: httpx.BaseTransport | None = None)`
  - `FakeGateway(responses: Sequence[GatewayResponse | str | Exception], *, delay_seconds: float = 0.0)`，属性 `.calls: list[tuple[str, tuple[Message, ...]]]`
  - `MIN_MODEL_SECONDS = 5.0`
  - `install_interceptors() -> bool`、`sdk_available() -> bool`、`trace_session(case_id, function, headers) -> ContextManager[None]`、`flush_telemetry() -> None`

`Gateway.complete` 的 `context` 参数类型是 Task 9 的 `SolveContext`。为避免循环导入，`gateway.py` 用 `TYPE_CHECKING` 导入并只依赖 `context.deadline`、`context.case_id`、`context.log` 三个属性；FakeGateway 的测试用一个最小的替身对象即可，不需要真正的 `SolveContext`。

- [ ] **Step 1: 写 gateway 失败测试**

`tests/unit/test_gateway.py`:

```python
from __future__ import annotations

import io
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from research_assistant.errors import BudgetExhausted, GatewayError
from research_assistant.runtime.deadline import Deadline
from research_assistant.runtime.gateway import (
    FakeGateway,
    GatewayResponse,
    GatewayUsage,
    HttpGateway,
    Message,
)
from research_assistant.runtime.logging import JsonLogger
from research_assistant.runtime.settings import GatewaySettings


@pytest.fixture
def log_stream() -> io.StringIO:
    return io.StringIO()


@dataclass
class StubContext:
    """只提供 gateway 真正用到的三个属性，避免与 SolveContext 循环依赖。"""

    case_id: str = "A-01"
    deadline: Deadline = Deadline(started_at=0.0, budget_seconds=180.0)
    log: Any = None

    def __post_init__(self) -> None:
        if self.log is None:
            self.log = JsonLogger("test")
        if self.deadline.started_at == 0.0:
            self.deadline = Deadline.start(180.0)


SETTINGS = GatewaySettings(
    family="challenge",
    base_url="http://gateway.invalid/v1",
    api_key="sk-test-not-a-real-key",
    model="test-model",
    session_id=None,
)


def completion_response(
    content: str = "OK",
    finish_reason: str = "stop",
    *,
    usage: dict[str, Any] | None = None,
    reasoning: Any = None,
) -> dict[str, Any]:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content, "reasoning": reasoning},
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage
        if usage is not None
        else {
            "prompt_tokens": 9,
            "completion_tokens": 2,
            "total_tokens": 11,
            "prompt_tokens_details": {"cached_tokens": 0},
            "completion_tokens_details": None,
        },
    }


def gateway_returning(payload: dict[str, Any], status_code: int = 200) -> HttpGateway:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    return HttpGateway(SETTINGS, transport=httpx.MockTransport(handler))


def test_successful_completion_is_parsed() -> None:
    gw = gateway_returning(completion_response(content="光照影响生长。"))
    response = gw.complete([Message(role="user", content="hi")], context=StubContext(), stage="t")
    assert response.content == "光照影响生长。"
    assert response.finish_reason == "stop"
    assert response.usage.prompt_tokens == 9
    assert response.usage.completion_tokens == 2
    assert response.usage.total_tokens == 11
    assert response.usage.cached_tokens == 0


def test_raw_response_is_preserved_for_source_extraction() -> None:
    payload = completion_response()
    gw = gateway_returning(payload)
    response = gw.complete([Message(role="user", content="hi")], context=StubContext(), stage="t")
    assert json.loads(json.dumps(response.raw)) == payload


def test_request_body_matches_openai_chat_completions() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json=completion_response())

    gw = HttpGateway(SETTINGS, transport=httpx.MockTransport(handler))
    gw.complete(
        [Message(role="system", content="s"), Message(role="user", content="u")],
        context=StubContext(),
        stage="t",
        max_tokens=256,
    )
    assert captured["url"] == "http://gateway.invalid/v1/chat/completions"
    assert captured["body"]["model"] == "test-model"
    assert captured["body"]["stream"] is False
    assert captured["body"]["max_tokens"] == 256
    assert captured["body"]["messages"] == [{"role": "system", "content": "s"},
                                            {"role": "user", "content": "u"}]
    assert captured["headers"]["content-type"] == "application/json"


def test_max_tokens_is_omitted_when_not_requested() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=completion_response())

    HttpGateway(SETTINGS, transport=httpx.MockTransport(handler)).complete(
        [Message(role="user", content="u")], context=StubContext(), stage="t"
    )
    assert "max_tokens" not in captured["body"]


def test_session_header_is_sent_only_when_configured() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json=completion_response())

    with_session = GatewaySettings(
        family="challenge",
        base_url="http://gateway.invalid/v1",
        api_key="sk-x",
        model="m",
        session_id="sess-42",
    )
    HttpGateway(with_session, transport=httpx.MockTransport(handler)).complete(
        [Message(role="user", content="u")], context=StubContext(), stage="t"
    )
    assert captured["headers"]["x-session-id"] == "sess-42"

    HttpGateway(SETTINGS, transport=httpx.MockTransport(handler)).complete(
        [Message(role="user", content="u")], context=StubContext(), stage="t"
    )
    assert "x-session-id" not in captured["headers"]


def test_base_url_trailing_slash_does_not_double_up() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=completion_response())

    settings = GatewaySettings(
        family="challenge", base_url="http://gateway.invalid/v1/", api_key="k", model="m"
    )
    HttpGateway(settings, transport=httpx.MockTransport(handler)).complete(
        [Message(role="user", content="u")], context=StubContext(), stage="t"
    )
    assert captured["url"] == "http://gateway.invalid/v1/chat/completions"


def test_api_key_is_never_logged(log_stream: io.StringIO) -> None:
    gw = HttpGateway(SETTINGS, log=JsonLogger("test", stream=log_stream),
                     transport=httpx.MockTransport(
                         lambda request: httpx.Response(200, json=completion_response())))
    gw.complete([Message(role="user", content="u")], context=StubContext(), stage="t")
    assert "sk-test-not-a-real-key" not in log_stream.getvalue()


def test_truncated_response_is_rejected() -> None:
    gw = gateway_returning(completion_response(content="partial", finish_reason="length"))
    with pytest.raises(GatewayError, match="finish_reason"):
        gw.complete([Message(role="user", content="u")], context=StubContext(), stage="t")


def test_non_200_is_rejected_without_leaking_the_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream unavailable")

    gw = HttpGateway(SETTINGS, transport=httpx.MockTransport(handler))
    with pytest.raises(GatewayError) as excinfo:
        gw.complete([Message(role="user", content="u")], context=StubContext(), stage="t")
    assert "503" in str(excinfo.value)
    assert "sk-test-not-a-real-key" not in str(excinfo.value)


def test_transport_error_is_wrapped_as_gateway_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    gw = HttpGateway(SETTINGS, transport=httpx.MockTransport(handler))
    with pytest.raises(GatewayError, match="boom"):
        gw.complete([Message(role="user", content="u")], context=StubContext(), stage="t")


def test_unparseable_body_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    gw = HttpGateway(SETTINGS, transport=httpx.MockTransport(handler))
    with pytest.raises(GatewayError):
        gw.complete([Message(role="user", content="u")], context=StubContext(), stage="t")


def test_missing_choices_is_rejected() -> None:
    gw = gateway_returning({"id": "x", "choices": []})
    with pytest.raises(GatewayError, match="choices"):
        gw.complete([Message(role="user", content="u")], context=StubContext(), stage="t")


def test_null_usage_is_tolerated() -> None:
    payload = completion_response()
    payload["usage"] = None
    gw = gateway_returning(payload)
    response = gw.complete([Message(role="user", content="u")], context=StubContext(), stage="t")
    assert response.usage == GatewayUsage(None, None, None, None)


def test_null_completion_token_details_is_tolerated() -> None:
    """实测开发网关会返回 completion_tokens_details: null。"""
    gw = gateway_returning(completion_response())
    response = gw.complete([Message(role="user", content="u")], context=StubContext(), stage="t")
    assert response.usage.cached_tokens == 0


def test_reasoning_field_is_surfaced_when_present() -> None:
    gw = gateway_returning(completion_response(reasoning="思考过程"))
    response = gw.complete([Message(role="user", content="u")], context=StubContext(), stage="t")
    assert response.reasoning == "思考过程"


def test_empty_message_list_is_rejected() -> None:
    gw = gateway_returning(completion_response())
    with pytest.raises(GatewayError, match="messages"):
        gw.complete([], context=StubContext(), stage="t")


def test_insufficient_budget_raises_before_any_request() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=completion_response())

    gw = HttpGateway(SETTINGS, transport=httpx.MockTransport(handler))
    context = StubContext(deadline=Deadline(started_at=time.monotonic() - 179.0,
                                           budget_seconds=180.0))
    with pytest.raises(BudgetExhausted):
        gw.complete([Message(role="user", content="u")], context=context, stage="t")
    assert calls == []


def test_timeout_is_derived_from_the_deadline() -> None:
    gw = HttpGateway(
        SETTINGS,
        transport=httpx.MockTransport(lambda request: httpx.Response(200,
                                                                    json=completion_response())),
    )
    context = StubContext(deadline=Deadline(started_at=time.monotonic(), budget_seconds=42.0))
    gw.complete([Message(role="user", content="u")], context=context, stage="t")
    assert gw.last_timeout_seconds == pytest.approx(42.0, abs=1.0)


def test_no_automatic_retry_on_failure() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(500, text="nope")

    gw = HttpGateway(SETTINGS, transport=httpx.MockTransport(handler))
    with pytest.raises(GatewayError):
        gw.complete([Message(role="user", content="u")], context=StubContext(), stage="t")
    assert len(attempts) == 1


def test_fake_gateway_returns_scripted_responses_in_order() -> None:
    fake = FakeGateway(["first", "second"])
    ctx = StubContext()
    assert fake.complete([Message(role="user", content="a")], context=ctx, stage="s1").content == "first"
    assert fake.complete([Message(role="user", content="b")], context=ctx, stage="s2").content == "second"
    assert [stage for stage, _ in fake.calls] == ["s1", "s2"]


def test_fake_gateway_records_messages() -> None:
    fake = FakeGateway(["x"])
    messages = (Message(role="system", content="s"), Message(role="user", content="u"))
    fake.complete(messages, context=StubContext(), stage="s")
    assert fake.calls[0][1] == messages


def test_fake_gateway_accepts_full_responses() -> None:
    response = GatewayResponse(
        content="c", finish_reason="stop", usage=GatewayUsage(1, 2, 3, 0),
        reasoning=None, raw={"k": "v"},
    )
    assert FakeGateway([response]).complete(
        [Message(role="user", content="u")], context=StubContext(), stage="s"
    ) is response


def test_fake_gateway_can_raise_scripted_exceptions() -> None:
    fake = FakeGateway([GatewayError("boom")])
    with pytest.raises(GatewayError, match="boom"):
        fake.complete([Message(role="user", content="u")], context=StubContext(), stage="s")


def test_fake_gateway_exhaustion_is_an_error() -> None:
    fake = FakeGateway(["only-one"])
    ctx = StubContext()
    fake.complete([Message(role="user", content="u")], context=ctx, stage="s")
    with pytest.raises(GatewayError, match="no scripted response"):
        fake.complete([Message(role="user", content="u")], context=ctx, stage="s")


def test_fake_gateway_can_simulate_slowness() -> None:
    fake = FakeGateway(["slow"], delay_seconds=0.05)
    started = time.monotonic()
    fake.complete([Message(role="user", content="u")], context=StubContext(), stage="s")
    assert time.monotonic() - started >= 0.05


def test_usage_from_response_handles_missing_and_partial_usage() -> None:
    assert GatewayUsage.from_response(None) == GatewayUsage(None, None, None, None)
    assert GatewayUsage.from_response({}) == GatewayUsage(None, None, None, None)
    parsed = GatewayUsage.from_response({"prompt_tokens": 5, "completion_tokens": None})
    assert parsed.prompt_tokens == 5
    assert parsed.completion_tokens is None


def test_usage_from_response_ignores_non_numeric_values() -> None:
    parsed = GatewayUsage.from_response({"prompt_tokens": "many", "total_tokens": 7})
    assert parsed.prompt_tokens is None
    assert parsed.total_tokens == 7


def test_message_to_json() -> None:
    assert Message(role="system", content="c").to_json() == {"role": "system", "content": "c"}
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/unit/test_gateway.py -v
```

预期：FAIL，`ModuleNotFoundError: No module named 'research_assistant.runtime.gateway'`

- [ ] **Step 3: 实现 runtime/gateway.py**

```python
"""赛事模型网关客户端。

同步 httpx.Client：由 SDK interceptor 自动抓取 token 与 finish_reason。
不做无界重试；超时来自 Deadline，不写死常量；凭据不出现在日志或异常消息中。
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

import httpx

from research_assistant.errors import GatewayError
from research_assistant.runtime.logging import BoundLogger, JsonLogger
from research_assistant.runtime.settings import GatewaySettings

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查，避免与 context 循环导入
    from research_assistant.runtime.context import SolveContext

MIN_MODEL_SECONDS = 5.0
COMPLETIONS_PATH = "/chat/completions"


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant"]
    content: str

    def to_json(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class GatewayUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None

    @classmethod
    def from_response(cls, usage: Mapping[str, Any] | None) -> GatewayUsage:
        def number(key: str, source: Mapping[str, Any] | None) -> int | None:
            if not source:
                return None
            value = source.get(key)
            return value if isinstance(value, int) and not isinstance(value, bool) else None

        details = usage.get("prompt_tokens_details") if usage else None
        if not isinstance(details, Mapping):
            details = None
        return cls(
            prompt_tokens=number("prompt_tokens", usage),
            completion_tokens=number("completion_tokens", usage),
            total_tokens=number("total_tokens", usage),
            cached_tokens=number("cached_tokens", details),
        )


@dataclass(frozen=True)
class GatewayResponse:
    content: str
    finish_reason: str | None
    usage: GatewayUsage
    reasoning: str | None
    raw: Mapping[str, Any]


class Gateway(Protocol):
    def complete(
        self,
        messages: Sequence[Message],
        *,
        context: SolveContext,
        stage: str,
        max_tokens: int | None = None,
    ) -> GatewayResponse: ...


class HttpGateway:
    """真实网关适配器。`transport` 仅用于测试注入。"""

    def __init__(
        self,
        gateway: GatewaySettings,
        *,
        log: BoundLogger | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = gateway
        self._log = log or JsonLogger("gateway")
        self._transport = transport
        self.last_timeout_seconds: float | None = None

    @property
    def endpoint(self) -> str:
        return self._settings.base_url.rstrip("/") + COMPLETIONS_PATH

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._settings.api_key}",
            "Content-Type": "application/json",
        }
        if self._settings.session_id:
            headers["X-Session-ID"] = self._settings.session_id
        return headers

    def complete(
        self,
        messages: Sequence[Message],
        *,
        context: SolveContext,
        stage: str,
        max_tokens: int | None = None,
    ) -> GatewayResponse:
        if not messages:
            raise GatewayError("messages must not be empty", case_id=context.case_id)

        timeout = context.deadline.take(
            stage, MIN_MODEL_SECONDS, case_id=context.case_id
        )
        self.last_timeout_seconds = timeout

        body: dict[str, Any] = {
            "model": self._settings.model,
            "stream": False,
            "messages": [message.to_json() for message in messages],
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens

        started = time.monotonic()
        context.log.info("gateway.request", stage=stage, model=self._settings.model,
                         family=self._settings.family, message_count=len(messages))
        try:
            with httpx.Client(
                timeout=httpx.Timeout(timeout), transport=self._transport
            ) as client:
                response = client.post(self.endpoint, json=body, headers=self._headers())
        except httpx.HTTPError as exc:
            raise GatewayError(
                f"gateway request failed during {stage}: {exc.__class__.__name__}: {exc}",
                case_id=context.case_id,
            ) from exc

        duration_ms = (time.monotonic() - started) * 1000
        if response.status_code != 200:
            raise GatewayError(
                f"gateway returned HTTP {response.status_code} during {stage}",
                case_id=context.case_id,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise GatewayError(
                f"gateway response is not valid JSON during {stage}", case_id=context.case_id
            ) from exc
        if not isinstance(payload, Mapping):
            raise GatewayError(
                f"gateway response must be a JSON object during {stage}",
                case_id=context.case_id,
            )

        choices = payload.get("choices")
        if not isinstance(choices, Sequence) or not choices:
            raise GatewayError(
                f"gateway response has no choices during {stage}", case_id=context.case_id
            )
        choice = choices[0]
        if not isinstance(choice, Mapping):
            raise GatewayError(
                f"gateway choice must be an object during {stage}", case_id=context.case_id
            )

        finish_reason = choice.get("finish_reason")
        if finish_reason != "stop":
            raise GatewayError(
                f"gateway finish_reason was {finish_reason!r}, expected 'stop' "
                f"(truncated or refused completion) during {stage}",
                case_id=context.case_id,
            )

        message = choice.get("message")
        if not isinstance(message, Mapping):
            raise GatewayError(
                f"gateway choice has no message object during {stage}", case_id=context.case_id
            )
        content = message.get("content")
        reasoning = message.get("reasoning")
        if reasoning is None:
            reasoning = message.get("reasoning_content")

        usage = GatewayUsage.from_response(
            payload.get("usage") if isinstance(payload.get("usage"), Mapping) else None
        )
        context.log.info(
            "gateway.response",
            stage=stage,
            duration_ms=round(duration_ms, 1),
            finish_reason=finish_reason,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            content_characters=len(content) if isinstance(content, str) else 0,
        )
        return GatewayResponse(
            content=content if isinstance(content, str) else "",
            finish_reason=finish_reason if isinstance(finish_reason, str) else None,
            usage=usage,
            reasoning=reasoning if isinstance(reasoning, str) else None,
            raw=payload,
        )


class FakeGateway:
    """测试替身：按脚本返回，可注入延迟与异常。绝不发起网络请求。"""

    def __init__(
        self,
        responses: Sequence[GatewayResponse | str | Exception],
        *,
        delay_seconds: float = 0.0,
    ) -> None:
        self._responses = list(responses)
        self._delay = delay_seconds
        self.calls: list[tuple[str, tuple[Message, ...]]] = []

    def complete(
        self,
        messages: Sequence[Message],
        *,
        context: SolveContext,
        stage: str,
        max_tokens: int | None = None,
    ) -> GatewayResponse:
        self.calls.append((stage, tuple(messages)))
        if self._delay:
            time.sleep(self._delay)
        if not self._responses:
            raise GatewayError(
                f"no scripted response left for stage {stage}", case_id=context.case_id
            )
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, GatewayResponse):
            return item
        return GatewayResponse(
            content=item,
            finish_reason="stop",
            usage=GatewayUsage(0, 0, 0, 0),
            reasoning=None,
            raw={"fake": True, "content": item},
        )
```

`httpx.MockTransport` 在 httpx 0.27.0 中可用；`HttpGateway` 每次调用新建 `httpx.Client` 以便注入 transport，正式运行时这也在 1 CPU/1024 MiB 的预算内，且避免跨题复用连接状态。

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run pytest tests/unit/test_gateway.py -v
```

预期：PASS（全部）

- [ ] **Step 5: 写 telemetry 失败测试**

`tests/unit/test_telemetry.py`:

```python
from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from research_assistant.runtime import telemetry


@pytest.fixture
def without_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "agenthack", None)
    monkeypatch.setitem(sys.modules, "agenthack.interceptor", None)
    monkeypatch.setitem(sys.modules, "agenthack.telemetry", None)
    telemetry.reset_cache()


@pytest.fixture
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, Any] = {"installed": 0, "sessions": [], "flushed": 0, "shutdown": 0}

    interceptor = types.ModuleType("agenthack.interceptor")

    def install_all_interceptors() -> list[Any]:
        calls["installed"] += 1
        return []

    interceptor.install_all_interceptors = install_all_interceptors  # type: ignore[attr-defined]

    sdk_telemetry = types.ModuleType("agenthack.telemetry")

    class Session:
        def __init__(self, *args: Any) -> None:
            calls["sessions"].append(args)

        def __enter__(self) -> Session:
            return self

        def __exit__(self, *exc: Any) -> bool:
            return False

    class Telemetry:
        def session(self, *args: Any) -> Session:
            return Session(*args)

        def flush(self) -> None:
            calls["flushed"] += 1

        def shutdown(self) -> None:
            calls["shutdown"] += 1

    sdk_telemetry.Telemetry = Telemetry  # type: ignore[attr-defined]

    root = types.ModuleType("agenthack")
    root.interceptor = interceptor  # type: ignore[attr-defined]
    root.telemetry = sdk_telemetry  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "agenthack", root)
    monkeypatch.setitem(sys.modules, "agenthack.interceptor", interceptor)
    monkeypatch.setitem(sys.modules, "agenthack.telemetry", sdk_telemetry)
    telemetry.reset_cache()
    return calls


def test_sdk_is_unavailable_locally(without_sdk: None) -> None:
    assert telemetry.sdk_available() is False


def test_install_interceptors_returns_false_without_sdk(without_sdk: None) -> None:
    assert telemetry.install_interceptors() is False


def test_install_interceptors_is_idempotent(without_sdk: None) -> None:
    assert telemetry.install_interceptors() is False
    assert telemetry.install_interceptors() is False


def test_trace_session_is_a_noop_without_sdk(without_sdk: None) -> None:
    with telemetry.trace_session("A-01", "Chat", {}):
        pass


def test_flush_is_a_noop_without_sdk(without_sdk: None) -> None:
    telemetry.flush_telemetry()


def test_sdk_is_detected_when_present(fake_sdk: dict[str, Any]) -> None:
    assert telemetry.sdk_available() is True


def test_install_interceptors_delegates_to_sdk(fake_sdk: dict[str, Any]) -> None:
    assert telemetry.install_interceptors() is True
    assert fake_sdk["installed"] == 1


def test_install_interceptors_only_installs_once(fake_sdk: dict[str, Any]) -> None:
    telemetry.install_interceptors()
    telemetry.install_interceptors()
    assert fake_sdk["installed"] == 1


def test_trace_session_opens_an_sdk_session(fake_sdk: dict[str, Any]) -> None:
    with telemetry.trace_session("A-01", "Chat", {"x-session-id": "s"}):
        pass
    assert len(fake_sdk["sessions"]) == 1
    assert fake_sdk["sessions"][0][0] == "A-01"
    assert fake_sdk["sessions"][0][1] == "Chat"


def test_flush_delegates_to_sdk(fake_sdk: dict[str, Any]) -> None:
    telemetry.flush_telemetry()
    assert fake_sdk["flushed"] == 1


def test_sdk_failures_never_propagate(monkeypatch: pytest.MonkeyPatch) -> None:
    """遥测失败绝不影响答案返回。"""
    broken = types.ModuleType("agenthack.interceptor")

    def explode() -> list[Any]:
        raise RuntimeError("collector down")

    broken.install_all_interceptors = explode  # type: ignore[attr-defined]
    root = types.ModuleType("agenthack")
    root.interceptor = broken  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agenthack", root)
    monkeypatch.setitem(sys.modules, "agenthack.interceptor", broken)
    telemetry.reset_cache()

    assert telemetry.install_interceptors() is False
    with telemetry.trace_session("A-01", "Chat", {}):
        pass
    telemetry.flush_telemetry()
```

- [ ] **Step 6: 跑测试确认失败**

```bash
uv run pytest tests/unit/test_telemetry.py -v
```

预期：FAIL，`ModuleNotFoundError: No module named 'research_assistant.runtime.telemetry'`

- [ ] **Step 7: 实现 runtime/telemetry.py**

```python
"""官方 agenthack SDK 遥测的可选复用。

本地开发机没有 agenthack 包，全部函数降级为 no-op。
遥测失败绝不影响答案返回：整个模块的每个入口都吞掉异常，只记日志。
"""

from __future__ import annotations

import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, nullcontext
from typing import Any

from research_assistant.runtime.logging import JsonLogger

_LOG = JsonLogger("telemetry")
_INTERCEPTORS_INSTALLED = False
_TELEMETRY: Any | None = None
_TELEMETRY_TRIED = False


def reset_cache() -> None:
    """仅测试使用：清掉进程级缓存，让 monkeypatch 后的模块状态生效。"""
    global _INTERCEPTORS_INSTALLED, _TELEMETRY, _TELEMETRY_TRIED
    _INTERCEPTORS_INSTALLED = False
    _TELEMETRY = None
    _TELEMETRY_TRIED = False


def _import_module(name: str) -> Any | None:
    module = sys.modules.get(name, "missing")
    if module is None:
        return None
    if module != "missing":
        return module
    try:
        import importlib

        return importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001 - 遥测缺失不得影响主流程
        _LOG.warning("telemetry.sdk_unavailable", module=name, reason=exc.__class__.__name__)
        return None


def sdk_available() -> bool:
    return _import_module("agenthack.interceptor") is not None


def install_interceptors() -> bool:
    """进程级、幂等。返回是否真的装上了 SDK interceptor。"""
    global _INTERCEPTORS_INSTALLED
    if _INTERCEPTORS_INSTALLED:
        return True
    module = _import_module("agenthack.interceptor")
    if module is None:
        return False
    try:
        module.install_all_interceptors()
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("telemetry.interceptor_failed", reason=exc.__class__.__name__)
        return False
    _INTERCEPTORS_INSTALLED = True
    _LOG.info("telemetry.interceptors_installed")
    return True


def _telemetry_instance() -> Any | None:
    global _TELEMETRY, _TELEMETRY_TRIED
    if _TELEMETRY_TRIED:
        return _TELEMETRY
    _TELEMETRY_TRIED = True
    module = _import_module("agenthack.telemetry")
    if module is None:
        return None
    try:
        _TELEMETRY = module.Telemetry()
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("telemetry.init_failed", reason=exc.__class__.__name__)
        _TELEMETRY = None
    return _TELEMETRY


@contextmanager
def trace_session(
    case_id: str, function: str, headers: Mapping[str, str] | None = None
) -> Iterator[None]:
    """有 SDK 时进入 Telemetry.session；无 SDK 时为 no-op。"""
    instance = _telemetry_instance()
    if instance is None:
        with nullcontext():
            yield
        return
    try:
        manager = instance.session(case_id, function, None, None, dict(headers or {}))
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("telemetry.session_failed", reason=exc.__class__.__name__, case_id=case_id)
        with nullcontext():
            yield
        return
    try:
        with manager:
            yield
    except Exception:
        # 业务异常照常向上抛；只有遥测自身的异常在此吞掉
        raise


def flush_telemetry() -> None:
    instance = _telemetry_instance()
    if instance is None:
        return
    try:
        instance.flush()
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("telemetry.flush_failed", reason=exc.__class__.__name__)


def shutdown_telemetry() -> None:
    instance = _telemetry_instance()
    if instance is None:
        return
    try:
        instance.shutdown()
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("telemetry.shutdown_failed", reason=exc.__class__.__name__)
```

`trace_session` 里的 `except Exception: raise` 是刻意的：业务异常必须继续传播，只有构造 session 本身的失败才被吞掉。`_import_module` 对 `sys.modules[name] is None` 的处理是为了兼容 `monkeypatch.setitem(sys.modules, "agenthack", None)`——Python 用 `None` 表示「已确定不存在」。

- [ ] **Step 8: 跑测试确认通过**

```bash
uv run pytest tests/unit/test_gateway.py tests/unit/test_telemetry.py -v
```

预期：PASS（全部）

- [ ] **Step 9: 提交**

```bash
git add src/research_assistant/runtime/gateway.py src/research_assistant/runtime/telemetry.py \
        tests/unit/test_gateway.py tests/unit/test_telemetry.py
git commit -m "feat: add model gateway client and degradable sdk telemetry"
```

---

### Task 8: v3 派题记录校验

**Files:**
- Create: `src/research_assistant/contracts/dispatch.py`
- Create: `tests/fixtures/dispatch_v3.py`
- Test: `tests/unit/test_dispatch.py`

**Interfaces:**
- Consumes: `DispatchValidationError`（Task 2）、`WorkspacePaths`（Task 5）
- Produces:
  - `DispatchCase(case_id, function, title, question_file, question_sha256, difficulty: str | None)`
  - `DispatchRecord(dispatch_id, team, schema_version, selection_mode, dispatch_policy_version, dispatch_policy_sha256, rules_version, cases_per_category, difficulty_by_case, case_ids, cases)`
  - `DispatchRecord.load(path: Path) -> DispatchRecord`（解析 + 结构校验）
  - `DispatchRecord.verify_questions(workspace: WorkspacePaths) -> None`（文件存在、位于本队 `assigned/`、SHA-256 与 `question_sha256` 一致）
  - `DispatchRecord.is_legacy -> bool`
  - 常量 `RULES_VERSION_V3 = "research-assistant-objective-v3"`、`DISPATCH_POLICY_VERSION_V3 = "research-assistant-dispatch-v3"`、`QUOTA_V3 = {"A":1,"B":3,"C":2,"D":3,"E":2}`、`TOTAL_CASES_V3 = 11`
  - `tests/fixtures/dispatch_v3.py`：`build_v3_dispatch(team: str, *, mutate: Callable[[dict], None] | None = None) -> dict[str, Any]` 与 `write_dispatch(workspace: WorkspacePaths, payload: Mapping) -> Path`、`write_questions(workspace: WorkspacePaths, record: Mapping) -> None`

**对 Spec 的一处细化**：spec 写的是单个 `validate()`。这里拆成 `load()` 内的结构校验与显式的 `verify_questions(workspace)`，因为哈希核验需要文件系统与队伍路径，而结构校验必须是纯函数才好单测。

- [ ] **Step 1: 写 fixture 构造器**

`tests/fixtures/__init__.py` 为空文件。`tests/fixtures/dispatch_v3.py`：

```python
"""自建的 v3 派题记录测试材料。

这些不是官方题目，不含任何正式答案；只用于验证解析与校验流程。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

# 覆盖三位后缀（A-100）以验证解析器不假定两位编号
V3_CASE_IDS: tuple[str, ...] = (
    "A-100", "B-01", "B-02", "B-03", "C-01", "C-03", "D-01", "D-02", "D-05", "E-01", "E-02",
)
FUNCTION_BY_PREFIX: dict[str, str] = {
    "A": "Chat",
    "B": "Literature_Retrieval",
    "C": "Survey",
    "D": "Reproduction",
    "E": "Idea",
}
DIFFICULTY_BY_CASE: dict[str, str] = {"D-01": "easy", "D-02": "medium", "D-05": "hard"}


def question_payload(case_id: str) -> dict[str, Any]:
    function = FUNCTION_BY_PREFIX[case_id[0]]
    return {
        "case_id": case_id,
        "function": function,
        "title": f"自建测试题目 {case_id}",
        "input": f"这是 {case_id} 的自建测试任务说明，不是官方题目。",
        "materials": [],
        "constraints": [],
        "expected_output": "自建的预期输出说明。",
        "output_schema_ref": function,
    }


def build_v3_dispatch(
    team: str, *, mutate: Callable[[dict[str, Any]], None] | None = None
) -> dict[str, Any]:
    cases = []
    for case_id in V3_CASE_IDS:
        body = json.dumps(question_payload(case_id), ensure_ascii=False, indent=2).encode("utf-8")
        entry: dict[str, Any] = {
            "case_id": case_id,
            "function": FUNCTION_BY_PREFIX[case_id[0]],
            "title": f"自建测试题目 {case_id}",
            "question_file": f"data/{team}/assigned/{case_id}.json",
            "question_sha256": hashlib.sha256(body).hexdigest(),
        }
        if case_id in DIFFICULTY_BY_CASE:
            entry["difficulty"] = DIFFICULTY_BY_CASE[case_id]
        cases.append(entry)

    record: dict[str, Any] = {
        "dispatch_id": "d-selftest-001",
        "team": team,
        "schema_version": 3,
        "selection_mode": "random_a_fixed_bcde",
        "dispatch_policy_version": "research-assistant-dispatch-v3",
        "dispatch_policy_sha256": "0" * 64,
        "difficulty_by_case": dict(DIFFICULTY_BY_CASE),
        "cases_per_category": {"A": 1, "B": 3, "C": 2, "D": 3, "E": 2},
        "rules_version": "research-assistant-objective-v3",
        "case_ids": list(V3_CASE_IDS),
        "cases": cases,
    }
    if mutate is not None:
        mutate(record)
    return record


def write_dispatch(workspace_root: Path, team: str, payload: Mapping[str, Any]) -> Path:
    path = workspace_root / "data" / team / "dispatch.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_questions(workspace_root: Path, team: str, record: Mapping[str, Any]) -> None:
    for case in record["cases"]:
        payload = question_payload(case["case_id"])
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        path = workspace_root / "data" / team / "assigned" / f"{case['case_id']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
```

`build_v3_dispatch` 里的 `question_sha256` 必须与 `write_questions` 落盘的字节完全对应，因此两处都用 `json.dumps(..., ensure_ascii=False, indent=2).encode("utf-8")`。改其中一处的序列化参数会破坏另一处。

- [ ] **Step 2: 写失败测试**

`tests/unit/test_dispatch.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from research_assistant.contracts.dispatch import (
    QUOTA_V3,
    TOTAL_CASES_V3,
    DispatchRecord,
)
from research_assistant.errors import DispatchValidationError, PathAuthorizationError
from research_assistant.runtime.paths import WorkspacePaths
from tests.fixtures.dispatch_v3 import build_v3_dispatch, write_dispatch, write_questions

TEAM = "测试队伍 A+1"


@pytest.fixture
def workspace(tmp_workspace: Path, papers_root: Path) -> WorkspacePaths:
    return WorkspacePaths(root=tmp_workspace, team=TEAM, papers_root=papers_root)


@pytest.fixture
def record_path(tmp_workspace: Path) -> Path:
    record = build_v3_dispatch(TEAM)
    write_questions(tmp_workspace, TEAM, record)
    return write_dispatch(tmp_workspace, TEAM, record)


def mutate(**changes: Any):
    def _apply(record: dict[str, Any]) -> None:
        record.update(changes)

    return _apply


def test_a_valid_v3_record_loads(record_path: Path) -> None:
    record = DispatchRecord.load(record_path)
    assert record.schema_version == 3
    assert record.rules_version == "research-assistant-objective-v3"
    assert record.team == TEAM
    assert len(record.case_ids) == TOTAL_CASES_V3 == 11
    assert record.cases_per_category == QUOTA_V3
    assert record.is_legacy is False


def test_every_case_entry_is_parsed(record_path: Path) -> None:
    record = DispatchRecord.load(record_path)
    assert len(record.cases) == 11
    by_id = {case.case_id: case for case in record.cases}
    assert by_id["D-01"].difficulty == "easy"
    assert by_id["D-05"].difficulty == "hard"
    assert by_id["A-100"].difficulty is None
    assert by_id["B-02"].function == "Literature_Retrieval"
    assert by_id["C-01"].question_file == f"data/{TEAM}/assigned/C-01.json"


def test_three_digit_case_suffix_is_supported(record_path: Path) -> None:
    assert "A-100" in DispatchRecord.load(record_path).case_ids


def test_verify_questions_passes_for_matching_hashes(record_path: Path,
                                                    workspace: WorkspacePaths) -> None:
    DispatchRecord.load(record_path).verify_questions(workspace)


def test_verify_questions_detects_a_tampered_question(record_path: Path,
                                                      workspace: WorkspacePaths) -> None:
    target = workspace.question_file("B-01")
    target.write_text('{"case_id": "B-01", "tampered": true}', encoding="utf-8")
    with pytest.raises(DispatchValidationError, match="sha256"):
        DispatchRecord.load(record_path).verify_questions(workspace)


def test_verify_questions_detects_a_missing_question(record_path: Path,
                                                     workspace: WorkspacePaths) -> None:
    workspace.question_file("E-02").unlink()
    with pytest.raises(DispatchValidationError, match="missing"):
        DispatchRecord.load(record_path).verify_questions(workspace)


def test_verify_questions_rejects_files_outside_assigned(record_path: Path,
                                                         workspace: WorkspacePaths) -> None:
    record = DispatchRecord.load(record_path)
    cases = list(record.cases)
    object.__setattr__(cases[0], "question_file", f"data/{TEAM}/dispatch.json")
    object.__setattr__(record, "cases", tuple(cases))
    with pytest.raises((DispatchValidationError, PathAuthorizationError)):
        record.verify_questions(workspace)


def test_verify_questions_rejects_another_teams_directory(record_path: Path,
                                                          workspace: WorkspacePaths) -> None:
    record = DispatchRecord.load(record_path)
    cases = list(record.cases)
    object.__setattr__(cases[0], "question_file", "data/别的队/assigned/A-100.json")
    object.__setattr__(record, "cases", tuple(cases))
    with pytest.raises((DispatchValidationError, PathAuthorizationError)):
        record.verify_questions(workspace)


@pytest.mark.parametrize("field", ["dispatch_id", "team", "schema_version", "selection_mode",
                                   "dispatch_policy_version", "dispatch_policy_sha256",
                                   "rules_version", "cases_per_category", "case_ids", "cases"])
def test_missing_field_is_rejected(tmp_workspace: Path, field: str) -> None:
    def drop(record: dict[str, Any]) -> None:
        del record[field]

    path = write_dispatch(tmp_workspace, TEAM, build_v3_dispatch(TEAM, mutate=drop))
    with pytest.raises(DispatchValidationError, match=field):
        DispatchRecord.load(path)


def test_wrong_schema_version_is_flagged_legacy(tmp_workspace: Path) -> None:
    path = write_dispatch(
        tmp_workspace, TEAM,
        build_v3_dispatch(TEAM, mutate=mutate(schema_version=2, cases_per_category=5,
                                              rules_version="research-assistant-objective-v2")),
    )
    record = DispatchRecord.load(path)
    assert record.is_legacy is True


def test_v3_rules_version_is_required_for_non_legacy(tmp_workspace: Path) -> None:
    path = write_dispatch(
        tmp_workspace, TEAM, build_v3_dispatch(TEAM, mutate=mutate(rules_version="something-else"))
    )
    with pytest.raises(DispatchValidationError, match="rules_version"):
        DispatchRecord.load(path)


def test_case_ids_must_be_unique(tmp_workspace: Path) -> None:
    def duplicate(record: dict[str, Any]) -> None:
        record["case_ids"] = [record["case_ids"][0]] * 11

    path = write_dispatch(tmp_workspace, TEAM, build_v3_dispatch(TEAM, mutate=duplicate))
    with pytest.raises(DispatchValidationError, match="duplicate"):
        DispatchRecord.load(path)


def test_case_ids_must_total_eleven(tmp_workspace: Path) -> None:
    """保持声明配额不变、只少给一题，触发总数校验。"""

    def shrink(record: dict[str, Any]) -> None:
        record["case_ids"] = record["case_ids"][:10]
        record["cases"] = record["cases"][:10]

    path = write_dispatch(tmp_workspace, TEAM, build_v3_dispatch(TEAM, mutate=shrink))
    with pytest.raises(DispatchValidationError, match="11"):
        DispatchRecord.load(path)


@pytest.mark.parametrize(
    "quota",
    [
        {"A": 2, "B": 3, "C": 2, "D": 3, "E": 2},
        {"A": 1, "B": 2, "C": 2, "D": 3, "E": 2},
        {"A": 1, "B": 3, "C": 2, "D": 2, "E": 2},
        {"A": 1, "B": 3, "C": 2, "D": 3, "E": 1},
        {"A": 1, "B": 3, "C": 1, "D": 3, "E": 2},
    ],
)
def test_quota_must_be_one_three_two_three_two(tmp_workspace: Path, quota: dict[str, int]) -> None:
    def set_quota(record: dict[str, Any]) -> None:
        record["cases_per_category"] = quota

    path = write_dispatch(tmp_workspace, TEAM, build_v3_dispatch(TEAM, mutate=set_quota))
    with pytest.raises(DispatchValidationError, match="cases_per_category"):
        DispatchRecord.load(path)


def test_declared_quota_must_match_actual_case_ids(tmp_workspace: Path) -> None:
    """把一道 B 题换成 A 题：总数仍是 11，但各类实际计数与声明配额不符 → 拒绝。"""

    def inconsistent(record: dict[str, Any]) -> None:
        record["case_ids"][1] = "A-02"
        record["cases"][1] = {**record["cases"][1], "case_id": "A-02", "function": "Chat"}

    path = write_dispatch(tmp_workspace, TEAM, build_v3_dispatch(TEAM, mutate=inconsistent))
    with pytest.raises(DispatchValidationError, match="do not match cases_per_category"):
        DispatchRecord.load(path)


def test_cases_must_correspond_one_to_one_with_case_ids(tmp_workspace: Path) -> None:
    def mismatch(record: dict[str, Any]) -> None:
        record["cases"] = record["cases"][:9]

    path = write_dispatch(tmp_workspace, TEAM, build_v3_dispatch(TEAM, mutate=mismatch))
    with pytest.raises(DispatchValidationError, match="one-to-one"):
        DispatchRecord.load(path)


def test_missing_case_entry_field_is_rejected(tmp_workspace: Path) -> None:
    def drop_field(record: dict[str, Any]) -> None:
        del record["cases"][0]["question_sha256"]

    path = write_dispatch(tmp_workspace, TEAM, build_v3_dispatch(TEAM, mutate=drop_field))
    with pytest.raises(DispatchValidationError, match="question_sha256"):
        DispatchRecord.load(path)


def test_team_mismatch_with_runtime_is_detected(workspace: WorkspacePaths,
                                                tmp_workspace: Path) -> None:
    path = write_dispatch(tmp_workspace, TEAM, build_v3_dispatch("别的队"))
    record = DispatchRecord.load(path)
    with pytest.raises(DispatchValidationError, match="team"):
        record.assert_team(TEAM)


def test_assert_team_accepts_a_matching_record(record_path: Path) -> None:
    DispatchRecord.load(record_path).assert_team(TEAM)


def test_unparseable_json_is_rejected(tmp_workspace: Path) -> None:
    path = tmp_workspace / "data" / TEAM / "dispatch.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(DispatchValidationError, match="JSON"):
        DispatchRecord.load(path)


def test_missing_dispatch_file_raises_file_not_found(tmp_workspace: Path) -> None:
    """派题文件缺失不是致命错误，由调用方决定降级；load 只报 FileNotFoundError。"""
    with pytest.raises(FileNotFoundError):
        DispatchRecord.load(tmp_workspace / "data" / TEAM / "dispatch.json")


def test_integer_cases_per_category_is_legacy_not_v3(tmp_workspace: Path) -> None:
    path = write_dispatch(
        tmp_workspace, TEAM,
        build_v3_dispatch(TEAM, mutate=mutate(schema_version=2, cases_per_category=5)),
    )
    record = DispatchRecord.load(path)
    assert record.is_legacy is True
    assert record.cases_per_category == {}


def test_loaded_record_is_serialisable_for_logging(record_path: Path) -> None:
    record = DispatchRecord.load(record_path)
    assert json.loads(json.dumps(record.summary()))["dispatch_id"] == "d-selftest-001"
    assert "team" in record.summary()
```

- [ ] **Step 3: 跑测试确认失败**

```bash
uv run pytest tests/unit/test_dispatch.py -v
```

预期：FAIL，`ModuleNotFoundError: No module named 'research_assistant.contracts.dispatch'`

- [ ] **Step 4: 实现 contracts/dispatch.py**

```python
"""v3 派题记录。

结构校验是纯函数；文件与哈希核验需要工作区路径，单独走 verify_questions。
schema_version=2 或整数 cases_per_category 的记录只作为明确标识的历史调试配置接受。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research_assistant.errors import DispatchValidationError
from research_assistant.runtime.paths import WorkspacePaths

RULES_VERSION_V3 = "research-assistant-objective-v3"
DISPATCH_POLICY_VERSION_V3 = "research-assistant-dispatch-v3"
SELECTION_MODE_V3 = "random_a_fixed_bcde"
QUOTA_V3: Mapping[str, int] = {"A": 1, "B": 3, "C": 2, "D": 3, "E": 2}
TOTAL_CASES_V3 = 11
SCHEMA_VERSION_V3 = 3

REQUIRED_RECORD_FIELDS: tuple[str, ...] = (
    "dispatch_id",
    "team",
    "schema_version",
    "selection_mode",
    "dispatch_policy_version",
    "dispatch_policy_sha256",
    "rules_version",
    "cases_per_category",
    "case_ids",
    "cases",
)
REQUIRED_CASE_FIELDS: tuple[str, ...] = (
    "case_id",
    "function",
    "title",
    "question_file",
    "question_sha256",
)


@dataclass(frozen=True)
class DispatchCase:
    case_id: str
    function: str
    title: str
    question_file: str
    question_sha256: str
    difficulty: str | None = None


@dataclass(frozen=True)
class DispatchRecord:
    dispatch_id: str
    team: str
    schema_version: int
    selection_mode: str
    dispatch_policy_version: str
    dispatch_policy_sha256: str
    rules_version: str
    cases_per_category: Mapping[str, int]
    difficulty_by_case: Mapping[str, str]
    case_ids: tuple[str, ...]
    cases: tuple[DispatchCase, ...]

    @property
    def is_legacy(self) -> bool:
        return self.schema_version != SCHEMA_VERSION_V3

    def summary(self) -> dict[str, Any]:
        """可写日志的摘要：不含题目正文，不含任何凭据。"""
        return {
            "dispatch_id": self.dispatch_id,
            "team": self.team,
            "schema_version": self.schema_version,
            "rules_version": self.rules_version,
            "dispatch_policy_version": self.dispatch_policy_version,
            "dispatch_policy_sha256": self.dispatch_policy_sha256,
            "case_count": len(self.case_ids),
            "cases_per_category": dict(self.cases_per_category),
            "legacy": self.is_legacy,
        }

    def assert_team(self, expected_team: str) -> None:
        if self.team != expected_team:
            raise DispatchValidationError(
                f"dispatch team {self.team!r} does not match the runtime team "
                f"{expected_team!r}"
            )

    @classmethod
    def load(cls, path: Path) -> DispatchRecord:
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise DispatchValidationError(
                f"cannot read dispatch record: {exc.strerror or exc.__class__.__name__}"
            ) from exc
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise DispatchValidationError(f"dispatch record is not valid JSON: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise DispatchValidationError("dispatch record must be a JSON object")
        return cls._parse(payload)

    @classmethod
    def _parse(cls, payload: Mapping[str, Any]) -> DispatchRecord:
        for field in REQUIRED_RECORD_FIELDS:
            if field not in payload:
                raise DispatchValidationError(f"dispatch record is missing field: {field}")

        schema_version = payload["schema_version"]
        if not isinstance(schema_version, int) or isinstance(schema_version, bool):
            raise DispatchValidationError("schema_version must be an integer")

        quota_raw = payload["cases_per_category"]
        legacy = schema_version != SCHEMA_VERSION_V3
        quota: dict[str, int] = {}
        if not legacy:
            if not isinstance(quota_raw, Mapping):
                raise DispatchValidationError(
                    "cases_per_category must be a dictionary in schema_version 3"
                )
            for category, count in quota_raw.items():
                if not isinstance(count, int) or isinstance(count, bool):
                    raise DispatchValidationError(
                        f"cases_per_category[{category!r}] must be an integer"
                    )
                quota[str(category)] = count

        case_ids_raw = payload["case_ids"]
        if not isinstance(case_ids_raw, list) or not all(
            isinstance(item, str) for item in case_ids_raw
        ):
            raise DispatchValidationError("case_ids must be an array of strings")
        case_ids = tuple(case_ids_raw)

        if len(set(case_ids)) != len(case_ids):
            seen: set[str] = set()
            duplicates: list[str] = []
            for case_id in case_ids:
                if case_id in seen and case_id not in duplicates:
                    duplicates.append(case_id)
                seen.add(case_id)
            raise DispatchValidationError(f"case_ids contains duplicate entries: {duplicates}")

        cases_raw = payload["cases"]
        if not isinstance(cases_raw, list):
            raise DispatchValidationError("cases must be an array")
        cases: list[DispatchCase] = []
        for index, entry in enumerate(cases_raw):
            if not isinstance(entry, Mapping):
                raise DispatchValidationError(f"cases[{index}] must be an object")
            for field in REQUIRED_CASE_FIELDS:
                if field not in entry:
                    raise DispatchValidationError(f"cases[{index}] is missing {field}")
                if not isinstance(entry[field], str):
                    raise DispatchValidationError(f"cases[{index}].{field} must be a string")
            difficulty = entry.get("difficulty")
            if difficulty is not None and not isinstance(difficulty, str):
                raise DispatchValidationError(f"cases[{index}].difficulty must be a string")
            cases.append(
                DispatchCase(
                    case_id=entry["case_id"],
                    function=entry["function"],
                    title=entry["title"],
                    question_file=entry["question_file"],
                    question_sha256=entry["question_sha256"],
                    difficulty=difficulty,
                )
            )

        difficulty_by_case_raw = payload.get("difficulty_by_case") or {}
        if not isinstance(difficulty_by_case_raw, Mapping):
            raise DispatchValidationError("difficulty_by_case must be an object")

        record = cls(
            dispatch_id=str(payload["dispatch_id"]),
            team=str(payload["team"]),
            schema_version=schema_version,
            selection_mode=str(payload["selection_mode"]),
            dispatch_policy_version=str(payload["dispatch_policy_version"]),
            dispatch_policy_sha256=str(payload["dispatch_policy_sha256"]),
            rules_version=str(payload["rules_version"]),
            cases_per_category=quota,
            difficulty_by_case={str(k): str(v) for k, v in difficulty_by_case_raw.items()},
            case_ids=case_ids,
            cases=tuple(cases),
        )
        record._validate_structure(legacy)
        return record

    def _validate_structure(self, legacy: bool) -> None:
        if legacy:
            return
        if self.rules_version != RULES_VERSION_V3:
            raise DispatchValidationError(
                f"rules_version must be {RULES_VERSION_V3!r} for schema_version 3, "
                f"got {self.rules_version!r}"
            )
        if dict(self.cases_per_category) != dict(QUOTA_V3):
            raise DispatchValidationError(
                f"cases_per_category must be {dict(QUOTA_V3)}, got {dict(self.cases_per_category)}"
            )
        if len(self.case_ids) != TOTAL_CASES_V3:
            raise DispatchValidationError(
                f"a v3 dispatch must list exactly {TOTAL_CASES_V3} case_ids, "
                f"got {len(self.case_ids)}"
            )
        if len(self.cases) != len(self.case_ids):
            raise DispatchValidationError(
                "cases must correspond one-to-one with case_ids "
                f"({len(self.cases)} cases vs {len(self.case_ids)} ids)"
            )
        if {case.case_id for case in self.cases} != set(self.case_ids):
            raise DispatchValidationError(
                "cases must correspond one-to-one with case_ids"
            )
        actual: dict[str, int] = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0}
        for case_id in self.case_ids:
            prefix = case_id.split("-", 1)[0]
            if prefix not in actual:
                raise DispatchValidationError(f"case_id {case_id!r} has an unknown category")
            actual[prefix] += 1
        if actual != dict(QUOTA_V3):
            raise DispatchValidationError(
                f"case_ids do not match cases_per_category: counted {actual}, "
                f"declared {dict(self.cases_per_category)}"
            )

    def verify_questions(self, workspace: WorkspacePaths) -> None:
        assigned_resolved = workspace.assigned_dir.resolve()
        for case in self.cases:
            relative = case.question_file
            if relative.startswith("/") or "\\" in relative or ".." in relative.split("/"):
                raise DispatchValidationError(
                    f"question_file must be a safe relative path: {relative!r}"
                )
            path = (workspace.root / relative).resolve()
            if assigned_resolved not in path.parents:
                raise DispatchValidationError(
                    f"question_file must live inside this team's assigned/: {relative!r}"
                )
            if not path.is_file():
                raise DispatchValidationError(f"question file is missing: {relative}")
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError as exc:
                raise DispatchValidationError(
                    f"cannot read question file {relative}: "
                    f"{exc.strerror or exc.__class__.__name__}"
                ) from exc
            if digest != case.question_sha256:
                raise DispatchValidationError(
                    f"question_sha256 mismatch for {case.case_id}: declared "
                    f"{case.question_sha256[:12]}, actual {digest[:12]}"
                )
```

`verify_questions` 用 `assigned_resolved not in path.parents` 而非字符串前缀比较，避免 `/output/data/team-evil` 被误判为 `/output/data/team` 的子路径。

- [ ] **Step 5: 跑测试确认通过**

```bash
uv run pytest tests/unit/test_dispatch.py -v
```

预期：PASS（全部）。`test_loaded_record_is_serialisable_for_logging` 依赖 `summary()` 返回纯 JSON 类型；`Mapping` 需先转 `dict`。

- [ ] **Step 6: 提交**

```bash
git add src/research_assistant/contracts/dispatch.py tests/fixtures \
        tests/unit/test_dispatch.py
git commit -m "feat: add v3 dispatch record validation"
```

---

### Task 9: 上下文装配、路由表、A 类 handler 与单题编排

**Files:**
- Create: `src/research_assistant/runtime/context.py`
- Create: `src/research_assistant/handlers/registry.py`
- Create: `src/research_assistant/handlers/chat.py`
- Create: `src/research_assistant/solve.py`
- Modify: `src/research_assistant/runtime/gateway.py`（新增 `UnavailableGateway`）
- Test: `tests/unit/test_context.py`
- Test: `tests/unit/test_registry.py`
- Test: `tests/unit/test_chat_handler.py`
- Test: `tests/unit/test_solve.py`

**Interfaces:**
- Consumes: `Task`/`TaskFunction`（Task 2）、`Settings`/`GatewaySettings`（Task 3）、`Deadline`（Task 4）、`JsonLogger`/`BoundLogger`（Task 4）、`WorkspacePaths`（Task 5）、`Gateway`/`HttpGateway`/`FakeGateway`/`Message`（Task 7）、`DispatchRecord`（Task 8）、`chat_result`/`validate_result_shape`/`serialize_result`（Task 6）、`write_bytes_verified`（Task 6）
- Produces:
  - `SolveContext(case_id, team, workspace, allowed_materials, output_dir, work_dir, deadline, settings, dispatch, log, gateway)`
  - `load_task_context(task: Task, settings: Settings, *, gateway: Gateway | None = None) -> SolveContext`
  - `UnavailableGateway(reason: str)`，`.complete(...)` 抛 `ConfigError`
  - `Handler` Protocol：`__call__(task: Task, context: SolveContext) -> Mapping[str, Any]`
  - `HANDLER_BY_FUNCTION: Mapping[TaskFunction, Handler]`
  - `solve_chat(task: Task, context: SolveContext) -> Mapping[str, Any]`
  - `CHAT_SYSTEM_PROMPT: str`、`build_chat_user_prompt(task: Task) -> str`、`parse_chat_payload(content: str, *, case_id: str) -> tuple[str, str]`
  - `CHAT_MAX_TOKENS = 2048`
  - `solve(task: Task, context: SolveContext) -> Mapping[str, Any]`
  - `persist_and_verify(result: Mapping[str, Any], context: SolveContext) -> bytes`

导入方向必须是 `api → solve → registry → chat → context → runtime/*`，且 `context` 在 `chat`/`registry`/`gateway` 中只用 `TYPE_CHECKING` 导入，否则会形成循环。

- [ ] **Step 1: 给 gateway.py 加 UnavailableGateway**

先把 `src/research_assistant/runtime/gateway.py` 顶部的

```python
from research_assistant.errors import GatewayError
```

改为

```python
from research_assistant.errors import ConfigError, GatewayError
```

然后在文件末尾追加：

```python
class UnavailableGateway:
    """网关未配置时的占位实现。

    让路由错误（501）先于配置错误（500）暴露，而不是在装配上下文时就失败。
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def complete(
        self,
        messages: Sequence[Message],
        *,
        context: SolveContext,
        stage: str,
        max_tokens: int | None = None,
    ) -> GatewayResponse:
        raise ConfigError(self.reason, case_id=context.case_id)
```

- [ ] **Step 2: 写 context 失败测试**

`tests/unit/test_context.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from research_assistant.contracts.dispatch import DispatchRecord
from research_assistant.contracts.task import Task
from research_assistant.errors import (
    ConfigError,
    DispatchValidationError,
    MaterialUnavailableError,
    PathAuthorizationError,
)
from research_assistant.runtime.context import SolveContext, load_task_context
from research_assistant.runtime.gateway import FakeGateway, UnavailableGateway
from research_assistant.runtime.settings import Settings
from tests.fixtures.dispatch_v3 import build_v3_dispatch, write_dispatch, write_questions

TEAM = "测试队伍 A+1"


def task(**overrides: object) -> Task:
    payload: dict[str, object] = {
        "case_id": "A-01",
        "function": "Chat",
        "title": "光照与生长",
        "input": "解释关系并提一个限定问题。",
        "materials": [],
        "constraints": [],
        "expected_output": "解释关系并提问。",
        "output_schema_ref": "Chat",
    }
    payload.update(overrides)
    return Task.parse(payload)


def settings_for(base_env: dict[str, str], **overrides: str) -> Settings:
    return Settings.from_env({**base_env, **overrides})


def test_context_carries_team_paths_budget_and_log(base_env: dict[str, str]) -> None:
    context = load_task_context(task(), settings_for(base_env), gateway=FakeGateway(["x"]))
    assert isinstance(context, SolveContext)
    assert context.case_id == "A-01"
    assert context.team == TEAM
    assert context.output_dir.name == TEAM
    assert context.work_dir == context.workspace.work_dir("A-01")
    assert context.deadline.budget_seconds == 180.0
    assert context.settings.gateway is not None
    assert context.dispatch is None


def test_allowed_materials_are_frozen(base_env: dict[str, str], papers_root: Path) -> None:
    (papers_root / "光照 研究+v1.pdf").write_text("body", encoding="utf-8")
    context = load_task_context(
        task(materials=["光照 研究+v1.pdf"]), settings_for(base_env), gateway=FakeGateway(["x"])
    )
    assert context.allowed_materials == frozenset({"光照 研究+v1.pdf"})
    with pytest.raises(AttributeError):
        context.allowed_materials.add("x")  # type: ignore[attr-defined]


def test_missing_material_file_raises_material_unavailable(
    base_env: dict[str, str], papers_root: Path
) -> None:
    with pytest.raises(MaterialUnavailableError, match="不存在.pdf"):
        load_task_context(
            task(materials=["不存在.pdf"]), settings_for(base_env), gateway=FakeGateway(["x"])
        )


def test_material_escaping_papers_root_raises_path_authorization(base_env: dict[str, str]) -> None:
    with pytest.raises(PathAuthorizationError):
        load_task_context(
            task(materials=["../secret.pdf"]), settings_for(base_env), gateway=FakeGateway(["x"])
        )


def test_empty_team_raises_config_error(base_env: dict[str, str]) -> None:
    env = {k: v for k, v in base_env.items() if k != "RESEARCH_WORKSPACE_TEAM"}
    with pytest.raises(ConfigError, match="RESEARCH_WORKSPACE_TEAM"):
        load_task_context(task(), Settings.from_env(env), gateway=FakeGateway(["x"]))


def test_dispatch_is_loaded_and_verified_when_mounted(
    base_env: dict[str, str], tmp_workspace: Path
) -> None:
    record = build_v3_dispatch(TEAM)
    write_questions(tmp_workspace, TEAM, record)
    write_dispatch(tmp_workspace, TEAM, record)
    context = load_task_context(
        task(case_id="A-100"), settings_for(base_env), gateway=FakeGateway(["x"])
    )
    assert isinstance(context.dispatch, DispatchRecord)
    assert context.dispatch.dispatch_id == "d-selftest-001"
    assert context.dispatch.is_legacy is False


def test_dispatch_team_mismatch_is_rejected(base_env: dict[str, str], tmp_workspace: Path) -> None:
    record = build_v3_dispatch("别的队")
    write_dispatch(tmp_workspace, TEAM, record)
    with pytest.raises(DispatchValidationError, match="team"):
        load_task_context(task(), settings_for(base_env), gateway=FakeGateway(["x"]))


def test_corrupt_dispatch_is_rejected(base_env: dict[str, str], tmp_workspace: Path) -> None:
    (tmp_workspace / "data" / TEAM / "dispatch.json").write_text("{bad", encoding="utf-8")
    with pytest.raises(DispatchValidationError):
        load_task_context(task(), settings_for(base_env), gateway=FakeGateway(["x"]))


def test_absent_dispatch_is_not_fatal(base_env: dict[str, str]) -> None:
    """外部平台可能逐题直传 JSON 而不挂载派题文件；不得伪造记录。"""
    context = load_task_context(task(), settings_for(base_env), gateway=FakeGateway(["x"]))
    assert context.dispatch is None


def test_gateway_defaults_to_unavailable_when_not_configured(
    tmp_workspace: Path, papers_root: Path
) -> None:
    env = {
        "RESEARCH_WORKSPACE_TEAM": TEAM,
        "RESEARCH_WORKSPACE_ROOT": str(tmp_workspace),
        "RESEARCH_PAPERS_ROOT": str(papers_root),
    }
    context = load_task_context(task(), Settings.from_env(env))
    assert isinstance(context.gateway, UnavailableGateway)


def test_unavailable_gateway_raises_config_error_on_use(
    tmp_workspace: Path, papers_root: Path
) -> None:
    env = {
        "RESEARCH_WORKSPACE_TEAM": TEAM,
        "RESEARCH_WORKSPACE_ROOT": str(tmp_workspace),
        "RESEARCH_PAPERS_ROOT": str(papers_root),
    }
    context = load_task_context(task(), Settings.from_env(env))
    from research_assistant.runtime.gateway import Message

    with pytest.raises(ConfigError):
        context.gateway.complete([Message(role="user", content="u")], context=context, stage="s")


def test_injected_gateway_is_used_verbatim(base_env: dict[str, str]) -> None:
    fake = FakeGateway(["hi"])
    context = load_task_context(task(), settings_for(base_env), gateway=fake)
    assert context.gateway is fake


def test_log_is_bound_to_case_and_team(base_env: dict[str, str]) -> None:
    context = load_task_context(task(), settings_for(base_env), gateway=FakeGateway(["x"]))
    assert context.log is not None


def test_budget_is_configurable(base_env: dict[str, str]) -> None:
    context = load_task_context(
        task(), settings_for(base_env, RESEARCH_CASE_BUDGET_SECONDS="42"), gateway=FakeGateway(["x"])
    )
    assert context.deadline.budget_seconds == 42.0
```

- [ ] **Step 3: 实现 runtime/context.py**

```python
"""单题上下文装配。

一题一实例，无模块级可变状态；材料范围校验在这里发生，因为它需要工作区路径。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from research_assistant.contracts.dispatch import DispatchRecord
from research_assistant.contracts.task import Task
from research_assistant.errors import ConfigError, MaterialUnavailableError
from research_assistant.runtime.deadline import Deadline
from research_assistant.runtime.gateway import Gateway, HttpGateway, UnavailableGateway
from research_assistant.runtime.logging import BoundLogger, JsonLogger
from research_assistant.runtime.paths import WorkspacePaths
from research_assistant.runtime.settings import Settings


@dataclass(frozen=True)
class SolveContext:
    case_id: str
    team: str
    workspace: WorkspacePaths
    allowed_materials: frozenset[str]
    output_dir: Path
    work_dir: Path
    deadline: Deadline
    settings: Settings
    dispatch: DispatchRecord | None
    log: BoundLogger
    gateway: Gateway


def _load_dispatch(workspace: WorkspacePaths, team: str, log: BoundLogger) -> DispatchRecord | None:
    path = workspace.dispatch_file
    if not path.is_file():
        log.info("dispatch.absent", path=str(path))
        return None
    record = DispatchRecord.load(path)
    record.assert_team(team)
    record.verify_questions(workspace)
    log.info("dispatch.loaded", **record.summary())
    if record.is_legacy:
        log.warning(
            "dispatch.legacy_version",
            schema_version=record.schema_version,
            rules_version=record.rules_version,
        )
    return record


def _verify_materials(task: Task, workspace: WorkspacePaths) -> frozenset[str]:
    allowed: set[str] = set()
    for material in task.materials:
        path = workspace.paper(material)
        if not path.is_file():
            raise MaterialUnavailableError(
                f"authorized material is not available: {material}", case_id=task.case_id
            )
        allowed.add(material)
    return frozenset(allowed)


def load_task_context(
    task: Task, settings: Settings, *, gateway: Gateway | None = None
) -> SolveContext:
    if not settings.team:
        raise ConfigError(
            "RESEARCH_WORKSPACE_TEAM is not set; refusing to guess a team directory",
            case_id=task.case_id,
        )

    workspace = WorkspacePaths(
        root=settings.workspace_root, team=settings.team, papers_root=settings.papers_root
    )
    log = JsonLogger("solve", settings.log_level).bind(
        case_id=task.case_id, function=task.function.value, team=settings.team
    )
    if task.unknown_fields:
        log.warning(
            "task.unknown_fields", fields=sorted(task.unknown_fields), reason="protocol drift"
        )

    allowed_materials = _verify_materials(task, workspace)
    dispatch = _load_dispatch(workspace, settings.team, log)

    if gateway is None:
        if settings.gateway is None:
            gateway = UnavailableGateway(
                settings.gateway_config_error or "model gateway is not configured"
            )
        else:
            gateway = HttpGateway(settings.gateway, log=log)

    return SolveContext(
        case_id=task.case_id,
        team=settings.team,
        workspace=workspace,
        allowed_materials=allowed_materials,
        output_dir=workspace.team_dir,
        work_dir=workspace.work_dir(task.case_id),
        deadline=Deadline.start(settings.case_budget_seconds),
        settings=settings,
        dispatch=dispatch,
        log=log,
        gateway=gateway,
    )
```

- [ ] **Step 4: 跑 context 测试**

```bash
uv run pytest tests/unit/test_context.py -v
```

预期：PASS（全部）

- [ ] **Step 5: 写 registry 与 chat 的失败测试**

`tests/unit/test_registry.py`:

```python
from __future__ import annotations

import pytest

from research_assistant.contracts.task import Task, TaskFunction
from research_assistant.errors import HandlerNotImplemented
from research_assistant.handlers.registry import HANDLER_BY_FUNCTION


def test_every_function_has_a_handler() -> None:
    assert set(HANDLER_BY_FUNCTION) == set(TaskFunction)


def test_unimplemented_categories_raise_with_their_phase() -> None:
    for case_id, function, phase in [
        ("B-01", TaskFunction.LITERATURE_RETRIEVAL, "P2"),
        ("C-01", TaskFunction.SURVEY, "P3"),
        ("E-01", TaskFunction.IDEA, "P3"),
        ("D-01", TaskFunction.REPRODUCTION, "P4"),
    ]:
        handler = HANDLER_BY_FUNCTION[function]
        task = Task.parse({
            "case_id": case_id,
            "function": function.value,
            "title": "t", "input": "i", "materials": [], "constraints": [],
            "expected_output": "e", "output_schema_ref": function.value,
        })
        with pytest.raises(HandlerNotImplemented, match=phase) as excinfo:
            handler(task, None)  # type: ignore[arg-type]
        assert excinfo.value.http_status == 501
        assert excinfo.value.case_id == case_id


def test_chat_is_wired_to_the_real_handler() -> None:
    from research_assistant.handlers.chat import solve_chat

    assert HANDLER_BY_FUNCTION[TaskFunction.CHAT] is solve_chat
```

`tests/unit/test_chat_handler.py`:

```python
from __future__ import annotations

import json
from typing import Any

import pytest

from research_assistant.contracts.task import Task
from research_assistant.errors import BudgetExhausted, GatewayError
from research_assistant.handlers.chat import (
    CHAT_MAX_TOKENS,
    build_chat_user_prompt,
    parse_chat_payload,
    solve_chat,
)
from research_assistant.runtime.context import load_task_context
from research_assistant.runtime.gateway import FakeGateway, GatewayResponse, GatewayUsage
from research_assistant.runtime.settings import Settings

TEAM = "测试队伍 A+1"


def chat_task(**overrides: Any) -> Task:
    payload: dict[str, Any] = {
        "case_id": "A-01",
        "function": "Chat",
        "title": "室内植物光照与生长：科研对话",
        "input": "解释光照强度与生长速度的关系，并问一个最关键的限定问题。",
        "materials": [],
        "constraints": [],
        "expected_output": "解释关系并提问。",
        "output_schema_ref": "Chat",
    }
    payload.update(overrides)
    return Task.parse(payload)


def context_with(base_env: dict[str, str], responses: list[Any], **kwargs: Any):
    fake = FakeGateway(responses, **kwargs)
    return load_task_context(chat_task(), Settings.from_env(base_env), gateway=fake), fake


def model_json(answer: str = "光照强度提高会加快光合作用。",
               question: str = "你研究的是哪种室内植物？") -> str:
    return json.dumps({"answer": answer, "clarifying_question": question}, ensure_ascii=False)


def test_successful_chat_returns_exactly_three_fields(base_env: dict[str, str]) -> None:
    context, fake = context_with(base_env, [model_json()])
    result = solve_chat(chat_task(), context)
    assert list(result) == ["case_id", "answer", "clarifying_question"]
    assert result["case_id"] == "A-01"
    assert result["answer"] == "光照强度提高会加快光合作用。"
    assert len(fake.calls) == 1


def test_case_id_comes_from_the_task_not_the_model(base_env: dict[str, str]) -> None:
    """模型返回的 case_id 与未知字段一律丢弃，不透传。"""
    payload = json.dumps({
        "case_id": "HACKED", "answer": "a", "clarifying_question": "q", "score": 100,
    }, ensure_ascii=False)
    context, _ = context_with(base_env, [payload])
    result = solve_chat(chat_task(case_id="A-100"), context)
    assert result["case_id"] == "A-100"
    assert "score" not in result


def test_prompt_includes_the_task_input_and_expected_output(base_env: dict[str, str]) -> None:
    prompt = build_chat_user_prompt(chat_task())
    assert "解释光照强度与生长速度的关系" in prompt
    assert "解释关系并提问。" in prompt


def test_constraints_are_forwarded_into_the_prompt(base_env: dict[str, str]) -> None:
    prompt = build_chat_user_prompt(chat_task(constraints=["不超过 300 字", "只讨论光照"]))
    assert "不超过 300 字" in prompt
    assert "只讨论光照" in prompt


def test_request_is_a_single_non_streaming_call(base_env: dict[str, str]) -> None:
    context, fake = context_with(base_env, [model_json()])
    solve_chat(chat_task(), context)
    stage, messages = fake.calls[0]
    assert stage == "chat.completion"
    assert [m.role for m in messages] == ["system", "user"]


def test_max_tokens_is_passed_to_the_gateway(base_env: dict[str, str]) -> None:
    recorded: dict[str, int | None] = {}

    class RecordingGateway:
        def complete(self, messages: Any, *, context: Any, stage: str,
                     max_tokens: int | None = None) -> GatewayResponse:
            recorded["max_tokens"] = max_tokens
            recorded["stage"] = stage
            return GatewayResponse(content=model_json(), finish_reason="stop",
                                   usage=GatewayUsage(1, 1, 2, None), reasoning=None, raw={})

    context = load_task_context(chat_task(), Settings.from_env(base_env),
                                gateway=RecordingGateway())
    solve_chat(chat_task(), context)
    assert recorded["max_tokens"] == CHAT_MAX_TOKENS == 2048
    assert recorded["stage"] == "chat.completion"


def test_code_fenced_model_output_is_still_parsed(base_env: dict[str, str]) -> None:
    """我们要求无围栏，但模型有时会加；解析要能挡住这种情况。"""
    fenced = f"```json\n{model_json()}\n```"
    context, _ = context_with(base_env, [fenced])
    assert solve_chat(chat_task(), context)["answer"] == "光照强度提高会加快光合作用。"


def test_non_json_model_output_is_a_gateway_error(base_env: dict[str, str]) -> None:
    context, _ = context_with(base_env, ["光照确实很重要，我认为……"])
    with pytest.raises(GatewayError, match="JSON"):
        solve_chat(chat_task(), context)


def test_json_array_model_output_is_rejected(base_env: dict[str, str]) -> None:
    context, _ = context_with(base_env, ['[{"answer": "a"}]'])
    with pytest.raises(GatewayError, match="object"):
        solve_chat(chat_task(), context)


def test_missing_clarifying_question_is_rejected(base_env: dict[str, str]) -> None:
    context, _ = context_with(base_env, [json.dumps({"answer": "a"}, ensure_ascii=False)])
    with pytest.raises(GatewayError, match="clarifying_question"):
        solve_chat(chat_task(), context)


def test_blank_answer_is_rejected(base_env: dict[str, str]) -> None:
    context, _ = context_with(base_env, [model_json(answer="   ")])
    with pytest.raises(GatewayError, match="answer"):
        solve_chat(chat_task(), context)


def test_non_string_answer_is_rejected(base_env: dict[str, str]) -> None:
    context, _ = context_with(base_env, [json.dumps({"answer": 1, "clarifying_question": "q"})])
    with pytest.raises(GatewayError, match="answer"):
        solve_chat(chat_task(), context)


def test_parse_chat_payload_returns_a_tuple() -> None:
    answer, question = parse_chat_payload(model_json(), case_id="A-01")
    assert answer == "光照强度提高会加快光合作用。"
    assert question == "你研究的是哪种室内植物？"


def test_gateway_failure_propagates(base_env: dict[str, str]) -> None:
    context, _ = context_with(base_env, [GatewayError("upstream 502")])
    with pytest.raises(GatewayError, match="upstream 502"):
        solve_chat(chat_task(), context)


def test_exhausted_budget_is_reported_before_calling_the_model(
    base_env: dict[str, str],
) -> None:
    context, fake = context_with(base_env, [model_json()])
    object.__setattr__(context.deadline, "started_at", context.deadline.started_at - 179.5)
    with pytest.raises(BudgetExhausted):
        solve_chat(chat_task(), context)
    assert fake.calls == []


def test_model_content_is_not_written_to_logs(base_env: dict[str, str], capsys) -> None:
    """日志只记长度与计数，不记模型响应全文。"""
    marker = "标记性内容不应进日志"
    context, _ = context_with(base_env, [model_json(answer=marker)])
    solve_chat(chat_task(), context)
    captured = capsys.readouterr()
    assert marker not in captured.err
    assert marker not in captured.out
```

- [ ] **Step 6: 实现 handlers/chat.py**

```python
"""A 类（Chat）handler。

模型只产内容：case_id 由程序注入，模型返回的 case_id 与任何未知字段一律丢弃。
自动项是 answer 3 分、clarifying_question 2 分，但内容必须与题意相关。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from research_assistant.contracts.results import chat_result
from research_assistant.contracts.task import Task
from research_assistant.errors import GatewayError
from research_assistant.runtime.gateway import Message

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查，避免与 context 循环导入
    from research_assistant.runtime.context import SolveContext

CHAT_MAX_TOKENS = 2048
STAGE = "chat.completion"

CHAT_SYSTEM_PROMPT = (
    "你是一名科研助手，正在回答研究者提出的科研对话问题。"
    "严格遵守以下要求：\n"
    "1. 直接回答当前研究问题，明确涉及的变量、变量之间可能的关系，以及现有证据的限制。\n"
    "2. 不得编造数据、文献或实验结果；无法确定时明确说明不确定性。\n"
    "3. 提出一个最有价值的研究范围限定问题，用于收窄研究边界。\n"
    "4. 只输出一个 JSON 对象，键恰好为 answer 与 clarifying_question，两项都是非空字符串。\n"
    "5. 不要输出 Markdown 代码围栏、自然语言前缀或任何额外字段。"
)


def build_chat_user_prompt(task: Task) -> str:
    parts = [f"# 案例标题\n{task.title}", f"# 研究者问题\n{task.input}"]
    if task.expected_output.strip():
        parts.append(f"# 回答应覆盖的内容\n{task.expected_output}")
    if task.constraints:
        parts.append("# 必须遵守的额外要求\n" + "\n".join(f"- {c}" for c in task.constraints))
    if task.materials:
        parts.append("# 本题允许的材料\n" + "\n".join(f"- {m}" for m in task.materials))
    parts.append(
        '请按系统要求输出 JSON：{"answer": "...", "clarifying_question": "..."}'
    )
    return "\n\n".join(parts)


def _strip_code_fence(content: str) -> str:
    text = content.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_chat_payload(content: str, *, case_id: str) -> tuple[str, str]:
    text = _strip_code_fence(content)
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise GatewayError(
            f"model did not return valid JSON for {case_id}: {exc.__class__.__name__}",
            case_id=case_id,
        ) from exc
    if not isinstance(payload, Mapping):
        raise GatewayError(
            f"model returned a JSON {type(payload).__name__}, expected an object",
            case_id=case_id,
        )

    values: list[str] = []
    for field in ("answer", "clarifying_question"):
        value = payload.get(field)
        if not isinstance(value, str):
            raise GatewayError(
                f"model output field {field} must be a string", case_id=case_id
            )
        if not value.strip():
            raise GatewayError(f"model output field {field} must not be blank", case_id=case_id)
        values.append(value)
    return values[0], values[1]


def solve_chat(task: Task, context: SolveContext) -> Mapping[str, Any]:
    messages = (
        Message(role="system", content=CHAT_SYSTEM_PROMPT),
        Message(role="user", content=build_chat_user_prompt(task)),
    )
    response = context.gateway.complete(
        messages, context=context, stage=STAGE, max_tokens=CHAT_MAX_TOKENS
    )
    answer, question = parse_chat_payload(response.content, case_id=task.case_id)
    context.log.info(
        "chat.completed",
        answer_characters=len(answer),
        question_characters=len(question),
        finish_reason=response.finish_reason,
        prompt_tokens=response.usage.prompt_tokens,
        completion_tokens=response.usage.completion_tokens,
    )
    return chat_result(task.case_id, answer=answer, clarifying_question=question)
```

`SolveContext` 只在 `TYPE_CHECKING` 下导入，因此运行时不产生 `chat → context → gateway → chat` 的循环；`from __future__ import annotations` 让注解保持为字符串。

- [ ] **Step 7: 实现 handlers/registry.py**

```python
"""function → handler 路由表。

B/C/D/E 在 P1 尚未实现，路由必须存在且报出所属阶段，而不是静默返回空壳。
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol

from research_assistant.contracts.task import Task, TaskFunction
from research_assistant.errors import HandlerNotImplemented
from research_assistant.handlers.chat import solve_chat

if TYPE_CHECKING:  # pragma: no cover
    from research_assistant.runtime.context import SolveContext


class Handler(Protocol):
    def __call__(self, task: Task, context: "SolveContext") -> Mapping[str, Any]: ...


def _pending(function: TaskFunction, phase: str) -> Handler:
    def handler(task: Task, context: "SolveContext") -> Mapping[str, Any]:
        raise HandlerNotImplemented(
            f"{function.value} is scheduled for {phase} and is not implemented in P1",
            case_id=task.case_id,
        )

    return handler


HANDLER_BY_FUNCTION: Mapping[TaskFunction, Handler] = MappingProxyType(
    {
        TaskFunction.CHAT: solve_chat,
        TaskFunction.LITERATURE_RETRIEVAL: _pending(TaskFunction.LITERATURE_RETRIEVAL, "P2"),
        TaskFunction.SURVEY: _pending(TaskFunction.SURVEY, "P3"),
        TaskFunction.IDEA: _pending(TaskFunction.IDEA, "P3"),
        TaskFunction.REPRODUCTION: _pending(TaskFunction.REPRODUCTION, "P4"),
    }
)
```

- [ ] **Step 8: 写 solve 编排的失败测试**

`tests/unit/test_solve.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from research_assistant.contracts.results import serialize_result
from research_assistant.contracts.task import Task
from research_assistant.errors import HandlerNotImplemented, TaskContractError
from research_assistant.runtime.context import load_task_context
from research_assistant.runtime.gateway import FakeGateway
from research_assistant.runtime.settings import Settings
from research_assistant.solve import persist_and_verify, solve

TEAM = "测试队伍 A+1"


def chat_payload() -> dict[str, Any]:
    return {
        "case_id": "A-01", "function": "Chat", "title": "光照与生长",
        "input": "解释关系并提一个限定问题。", "materials": [], "constraints": [],
        "expected_output": "解释关系并提问。", "output_schema_ref": "Chat",
    }


def model_json() -> str:
    return json.dumps({"answer": "光照强度影响光合速率。",
                       "clarifying_question": "研究对象是哪种室内植物？"}, ensure_ascii=False)


def context_for(base_env: dict[str, str], payload: dict[str, Any], responses: list[Any]):
    task = Task.parse(payload)
    return task, load_task_context(task, Settings.from_env(base_env),
                                   gateway=FakeGateway(responses))


def test_solve_returns_the_chat_result(base_env: dict[str, str]) -> None:
    task, context = context_for(base_env, chat_payload(), [model_json()])
    result = solve(task, context)
    assert result == {
        "case_id": "A-01",
        "answer": "光照强度影响光合速率。",
        "clarifying_question": "研究对象是哪种室内植物？",
    }


def test_solve_routes_by_function_not_by_schema_ref(base_env: dict[str, str]) -> None:
    payload = {**chat_payload(), "case_id": "B-01", "function": "Literature_Retrieval",
               "output_schema_ref": "Literature_Retrieval"}
    task, context = context_for(base_env, payload, [])
    with pytest.raises(HandlerNotImplemented, match="P2"):
        solve(task, context)


def test_solve_rejects_a_result_with_extra_fields(base_env: dict[str, str],
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """HANDLER_BY_FUNCTION 是 MappingProxyType，不能 setitem；改打 solve 模块内的名字。"""
    from research_assistant import solve as solve_module

    task, context = context_for(base_env, chat_payload(), [model_json()])

    def bad_handler(t: Task, c: Any) -> dict[str, Any]:
        return {"case_id": t.case_id, "answer": "a", "clarifying_question": "q", "score": 5}

    monkeypatch.setattr(solve_module, "HANDLER_BY_FUNCTION", {task.function: bad_handler})
    with pytest.raises(TaskContractError, match="score"):
        solve(task, context)


def test_persist_writes_the_team_result_json(base_env: dict[str, str],
                                             tmp_workspace: Path) -> None:
    task, context = context_for(base_env, chat_payload(), [model_json()])
    data = persist_and_verify(solve(task, context), context)
    written = tmp_workspace / "data" / TEAM / "A-01.json"
    assert written.read_bytes() == data


def test_persisted_bytes_match_the_serialized_result(base_env: dict[str, str],
                                                    tmp_workspace: Path) -> None:
    task, context = context_for(base_env, chat_payload(), [model_json()])
    result = solve(task, context)
    data = persist_and_verify(result, context)
    assert data == serialize_result(result)


def test_persisted_file_is_parseable_and_carries_the_same_case_id(
    base_env: dict[str, str], tmp_workspace: Path
) -> None:
    task, context = context_for(base_env, chat_payload(), [model_json()])
    persist_and_verify(solve(task, context), context)
    loaded = json.loads((tmp_workspace / "data" / TEAM / "A-01.json").read_text(encoding="utf-8"))
    assert loaded["case_id"] == "A-01"


def test_persist_creates_the_work_directory_only_when_needed(
    base_env: dict[str, str], tmp_workspace: Path
) -> None:
    task, context = context_for(base_env, chat_payload(), [model_json()])
    persist_and_verify(solve(task, context), context)
    assert not (tmp_workspace / "data" / TEAM / "work" / "A-01").exists()
```

- [ ] **Step 9: 实现 solve.py**

```python
"""单题编排：路由 → handler → 形状校验 → 落盘回读。

不遍历整轮，不生成官方评分，不实现自己的裁判。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from research_assistant.contracts.results import serialize_result, validate_result_shape
from research_assistant.contracts.task import Task
from research_assistant.handlers.registry import HANDLER_BY_FUNCTION
from research_assistant.runtime.context import SolveContext
from research_assistant.runtime.persistence import write_bytes_verified


def solve(task: Task, context: SolveContext) -> Mapping[str, Any]:
    handler = HANDLER_BY_FUNCTION[task.function]
    result = handler(task, context)
    validate_result_shape(task.function, result)
    return result


def persist_and_verify(result: Mapping[str, Any], context: SolveContext) -> bytes:
    """落盘当前题结果并回读校验；返回的字节必须与 HTTP 响应体完全一致。"""
    data = serialize_result(result)
    path = context.workspace.result_json(context.case_id)
    write_bytes_verified(path, data)
    context.log.info("result.persisted", path=context.workspace.relative_to_output(path),
                     bytes=len(data))
    return data
```

- [ ] **Step 10: 跑全部单测与静态检查**

```bash
uv run pytest tests/unit -v
uv run ruff check .
uv run ruff format .
```

预期：全部 PASS，ruff 无告警。

- [ ] **Step 11: 提交**

```bash
git add src/research_assistant/runtime/context.py src/research_assistant/runtime/gateway.py \
        src/research_assistant/handlers/registry.py src/research_assistant/handlers/chat.py \
        src/research_assistant/solve.py tests/unit/test_context.py tests/unit/test_registry.py \
        tests/unit/test_chat_handler.py tests/unit/test_solve.py
git commit -m "feat: add solve context, handler routing and chat handler"
```

---

### Task 10: HTTP 服务与端到端合同测试

**Files:**
- Create: `src/research_assistant/api.py`
- Create: `src/research_assistant/__main__.py`
- Test: `tests/contract/test_http_contract.py`
- Test: `tests/integration/test_solve_end_to_end.py`

**Interfaces:**
- Consumes: `Settings`（Task 3）、`load_task_context`（Task 9）、`solve`/`persist_and_verify`（Task 9）、`Task`（Task 2）、`error_body`（Task 2）、`install_interceptors`/`trace_session`/`flush_telemetry`/`shutdown_telemetry`（Task 7）、`RULES_VERSION_V3`（Task 8）
- Produces:
  - `create_app(settings: Settings | None = None, *, gateway: Gateway | None = None) -> FastAPI`
  - `build_settings() -> Settings`（从 `os.environ` 读取）
  - `python -m research_assistant` 启动 uvicorn，监听 `0.0.0.0:8080`
  - 环境变量 `RESEARCH_HOST`（默认 `0.0.0.0`）、`RESEARCH_PORT`（默认 `8080`）

- [ ] **Step 1: 实现 api.py**

```python
"""FastAPI 应用：GET /health 与 POST /solve。

不复用 agenthack.AgentRuntime：它自带的 /solve 是通用赛题合同
（TaskRequest/TaskResponse 包装），与赛题一的八字段输入和纯结果 JSON 冲突。
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from contextlib import asynccontextmanager
from functools import partial
from typing import Any

import anyio.to_thread
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from research_assistant import __version__
from research_assistant.contracts.dispatch import RULES_VERSION_V3
from research_assistant.contracts.task import Task
from research_assistant.errors import (
    ResearchAssistantError,
    TaskContractError,
    error_body,
)
from research_assistant.runtime.context import SolveContext, load_task_context
from research_assistant.runtime.gateway import Gateway
from research_assistant.runtime.logging import JsonLogger
from research_assistant.runtime.settings import Settings
from research_assistant.runtime.telemetry import (
    flush_telemetry,
    install_interceptors,
    shutdown_telemetry,
    trace_session,
)
from research_assistant.solve import persist_and_verify, solve

JSON_MEDIA_TYPE = "application/json; charset=utf-8"


def build_settings() -> Settings:
    return Settings.from_env(os.environ)


def _error_response(exc: ResearchAssistantError, log: JsonLogger) -> JSONResponse:
    log.error(
        "solve.failed",
        error_type=type(exc).__name__,
        case_id=exc.case_id,
        http_status=exc.http_status,
        detail=exc.message,
    )
    return JSONResponse(status_code=exc.http_status, content=error_body(exc))


def _workspace_writable(settings: Settings) -> bool:
    """只检查输出根；不检查队伍目录，避免 team 未注入时把整轮评测挡在健康检查外。"""
    try:
        settings.workspace_root.mkdir(parents=True, exist_ok=True)
        probe = settings.workspace_root / ".health-probe"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        return False
    return True


def _dispatch_mounted(settings: Settings) -> bool:
    if not settings.team:
        return False
    return (settings.workspace_root / "data" / settings.team / "dispatch.json").is_file()


def _solve_in_thread(task: Task, context: SolveContext) -> bytes:
    return persist_and_verify(solve(task, context), context)


def create_app(
    settings: Settings | None = None, *, gateway: Gateway | None = None
) -> FastAPI:
    resolved = settings if settings is not None else build_settings()
    log = JsonLogger("api", resolved.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        installed = install_interceptors()
        log.info(
            "service.starting",
            version=__version__,
            rules_version=RULES_VERSION_V3,
            sdk_telemetry=installed,
            gateway_configured=resolved.gateway is not None,
            gateway_family=resolved.gateway.family if resolved.gateway else None,
        )
        yield
        flush_telemetry()
        shutdown_telemetry()

    app = FastAPI(title="Research Assistant Agent", version=__version__, lifespan=lifespan)

    @app.get("/health")
    async def health() -> Response:
        writable = await anyio.to_thread.run_sync(_workspace_writable, resolved)
        body = {
            "status": "ok" if writable else "unavailable",
            "version": __version__,
            "rules_version": RULES_VERSION_V3,
            "workspace_writable": writable,
            "workspace_root": str(resolved.workspace_root),
            "team_configured": bool(resolved.team),
            "gateway_configured": resolved.gateway is not None,
            "dispatch_mounted": await anyio.to_thread.run_sync(_dispatch_mounted, resolved),
        }
        return Response(
            content=json.dumps(body, ensure_ascii=False),
            status_code=200 if writable else 503,
            media_type=JSON_MEDIA_TYPE,
        )

    @app.post("/solve")
    async def solve_endpoint(request: Request) -> Response:
        try:
            raw = await request.body()
        except Exception as exc:  # noqa: BLE001
            return _error_response(
                TaskContractError(f"cannot read request body: {exc.__class__.__name__}"), log
            )
        try:
            payload: Mapping[str, Any] = json.loads(raw)
        except ValueError as exc:
            return _error_response(TaskContractError(f"request body is not valid JSON: {exc}"), log)
        if not isinstance(payload, Mapping):
            return _error_response(
                TaskContractError("request body must be a JSON object"), log
            )

        try:
            task = Task.parse(payload)
        except ResearchAssistantError as exc:
            return _error_response(exc, log)

        headers = {k: v for k, v in request.headers.items() if k.lower() == "x-session-id"}
        try:
            with trace_session(task.case_id, task.function.value, headers):
                context = await anyio.to_thread.run_sync(
                    partial(load_task_context, task, resolved, gateway=gateway)
                )
                data = await anyio.to_thread.run_sync(_solve_in_thread, task, context)
                flush_telemetry()
        except ResearchAssistantError as exc:
            return _error_response(exc, log)
        except Exception as exc:  # noqa: BLE001 - 未预期异常不得泄漏内部细节
            log.error("solve.unexpected", error_type=exc.__class__.__name__,
                      case_id=task.case_id)
            return JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "type": "UnexpectedError",
                        "message": exc.__class__.__name__,
                        "case_id": task.case_id,
                    }
                },
            )
        # 响应体直接复用落盘字节，保证与文件逐字节一致
        return Response(content=data, status_code=200, media_type=JSON_MEDIA_TYPE)

    return app
```

`load_task_context` 的 `gateway` 是 keyword-only，而 `anyio.to_thread.run_sync` 只能传位置参数，所以用 `functools.partial` 包装；文件顶部需 `from functools import partial`。

只透传 `x-session-id` 一个请求头给 telemetry，避免把 Authorization 等敏感头带进 trace。

`src/research_assistant/__main__.py`:

```python
"""python -m research_assistant：启动 uvicorn，监听 0.0.0.0:8080。"""

from __future__ import annotations

import os

import uvicorn

from research_assistant.api import create_app


def main() -> None:
    host = os.environ.get("RESEARCH_HOST", "0.0.0.0")
    port = int(os.environ.get("RESEARCH_PORT", "8080"))
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 写合同测试**

`tests/contract/__init__.py` 为空文件。`tests/contract/test_http_contract.py`:

```python
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from research_assistant.api import create_app
from research_assistant.errors import GatewayError
from research_assistant.runtime.gateway import FakeGateway
from research_assistant.runtime.settings import Settings

TEAM = "测试队伍 A+1"

FIVE_FUNCTIONS = {
    "A-01": ("Chat", "Chat"),
    "B-01": ("Literature_Retrieval", "Literature_Retrieval"),
    "C-01": ("Survey", "Survey"),
    "D-01": ("Reproduction", "Reproduction"),
    "E-01": ("Idea", "Idea"),
}


def answer_json() -> str:
    return json.dumps({"answer": "光照强度影响光合速率。",
                       "clarifying_question": "研究对象是哪种室内植物？"}, ensure_ascii=False)


def payload(case_id: str = "A-01", **overrides: Any) -> dict[str, Any]:
    # 未在表中的 case_id（如非法编号 A-1000、三位后缀 A-100）按 Chat 处理，
    # 这样非法编号的用例才能走到 Task.parse 的校验分支而不是先 KeyError。
    function, schema_ref = FIVE_FUNCTIONS.get(case_id, ("Chat", "Chat"))
    base: dict[str, Any] = {
        "case_id": case_id, "function": function, "title": f"自建测试 {case_id}",
        "input": "解释关系并提一个限定问题。", "materials": [], "constraints": [],
        "expected_output": "解释关系并提问。", "output_schema_ref": schema_ref,
    }
    base.update(overrides)
    return base


@pytest.fixture
def client(base_env: dict[str, str]) -> Iterator[TestClient]:
    """两条脚本响应：够跑两次独立请求，用于验证不跨题串状态。"""
    app = create_app(Settings.from_env(base_env),
                     gateway=FakeGateway([answer_json(), answer_json()]))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def bare_client(base_env: dict[str, str]) -> Iterator[TestClient]:
    """网关未配置的应用，用于验证配置错误通道。"""
    env = {k: v for k, v in base_env.items() if not k.startswith("CHALLENGE_LLM")}
    app = create_app(Settings.from_env(env), gateway=None)
    with TestClient(app) as test_client:
        yield test_client


def test_health_returns_200_with_a_small_json_body(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["workspace_writable"] is True
    assert body["rules_version"] == "research-assistant-objective-v3"
    assert body["gateway_configured"] is True


def test_health_does_not_call_the_model(base_env: dict[str, str]) -> None:
    fake = FakeGateway([answer_json()])
    app = create_app(Settings.from_env(base_env), gateway=fake)
    with TestClient(app) as test_client:
        test_client.get("/health")
        test_client.get("/health")
    assert fake.calls == []


def test_health_reports_unconfigured_gateway_but_still_200(bare_client: TestClient) -> None:
    response = bare_client.get("/health")
    assert response.status_code == 200
    assert response.json()["gateway_configured"] is False


def test_health_reports_missing_dispatch(base_env: dict[str, str]) -> None:
    app = create_app(Settings.from_env(base_env), gateway=FakeGateway([]))
    with TestClient(app) as test_client:
        assert test_client.get("/health").json()["dispatch_mounted"] is False


def test_health_reports_mounted_dispatch(base_env: dict[str, str], tmp_workspace: Path) -> None:
    from tests.fixtures.dispatch_v3 import build_v3_dispatch, write_dispatch, write_questions

    record = build_v3_dispatch(TEAM)
    write_questions(tmp_workspace, TEAM, record)
    write_dispatch(tmp_workspace, TEAM, record)
    app = create_app(Settings.from_env(base_env), gateway=FakeGateway([]))
    with TestClient(app) as test_client:
        assert test_client.get("/health").json()["dispatch_mounted"] is True


def test_health_503_when_workspace_root_is_not_writable(
    base_env: dict[str, str], tmp_path: Path
) -> None:
    blocked = tmp_path / "readonly-root"
    blocked.mkdir()
    blocked.chmod(0o555)
    env = {**base_env, "RESEARCH_WORKSPACE_ROOT": str(blocked)}
    try:
        app = create_app(Settings.from_env(env), gateway=FakeGateway([]))
        with TestClient(app) as test_client:
            assert test_client.get("/health").status_code == 503
    finally:
        blocked.chmod(0o755)


def test_solve_returns_the_chat_answer(client: TestClient) -> None:
    response = client.post("/solve", json=payload())
    assert response.status_code == 200
    assert response.json() == {
        "case_id": "A-01",
        "answer": "光照强度影响光合速率。",
        "clarifying_question": "研究对象是哪种室内植物？",
    }


def test_solve_response_has_exactly_the_allowed_top_level_keys(client: TestClient) -> None:
    body = client.post("/solve", json=payload()).json()
    assert set(body) == {"case_id", "answer", "clarifying_question"}


def test_solve_response_is_json_without_fences_or_prefix(client: TestClient) -> None:
    response = client.post("/solve", json=payload())
    assert response.headers["content-type"].startswith("application/json")
    text = response.text
    assert text.startswith("{")
    assert text.endswith("}")
    assert "```" not in text


def test_case_id_is_returned_verbatim(client: TestClient) -> None:
    assert client.post("/solve", json=payload("A-100")).json()["case_id"] == "A-100"


def test_extra_input_fields_are_never_forwarded(client: TestClient) -> None:
    response = client.post("/solve", json=payload(score=100, function_hint="x"))
    assert set(response.json()) == {"case_id", "answer", "clarifying_question"}


@pytest.mark.parametrize(
    ("case_id", "phase"),
    [("B-01", "P2"), ("C-01", "P3"), ("D-01", "P4"), ("E-01", "P3")],
)
def test_unimplemented_categories_route_and_report_501(
    base_env: dict[str, str], case_id: str, phase: str
) -> None:
    app = create_app(Settings.from_env(base_env), gateway=FakeGateway([]))
    with TestClient(app) as test_client:
        response = test_client.post("/solve", json=payload(case_id))
    assert response.status_code == 501
    body = response.json()["error"]
    assert body["type"] == "HandlerNotImplemented"
    assert body["case_id"] == case_id
    assert phase in body["message"]


def test_solve_does_not_process_the_whole_round(client: TestClient, tmp_workspace: Path) -> None:
    """一次请求只解当前题，不得写出其他题的结果。"""
    client.post("/solve", json=payload())
    written = sorted(p.name for p in (tmp_workspace / "data" / TEAM).glob("*.json"))
    assert written == ["A-01.json"]


@pytest.mark.parametrize("field", ["case_id", "function", "title", "input", "materials",
                                   "constraints", "expected_output", "output_schema_ref"])
def test_missing_field_returns_422(client: TestClient, field: str) -> None:
    data = payload()
    del data[field]
    response = client.post("/solve", json=data)
    assert response.status_code == 422
    assert response.json()["error"]["type"] == "TaskContractError"


def test_function_and_schema_mismatch_returns_422(client: TestClient) -> None:
    response = client.post("/solve", json=payload(output_schema_ref="Survey"))
    assert response.status_code == 422
    assert "output_schema_ref" in response.json()["error"]["message"]


def test_invalid_case_id_returns_422(client: TestClient) -> None:
    assert client.post("/solve", json=payload(case_id="A-1000")).status_code == 422


def test_malformed_json_body_returns_422(client: TestClient) -> None:
    response = client.post("/solve", content=b"{not json",
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json()["error"]["type"] == "TaskContractError"


def test_json_array_body_returns_422(client: TestClient) -> None:
    response = client.post("/solve", json=[payload()])
    assert response.status_code == 422


def test_null_materials_returns_422(client: TestClient) -> None:
    assert client.post("/solve", json=payload(materials=None)).status_code == 422


def test_material_escape_returns_403(base_env: dict[str, str]) -> None:
    app = create_app(Settings.from_env(base_env), gateway=FakeGateway([answer_json()]))
    with TestClient(app) as test_client:
        response = test_client.post("/solve", json=payload(materials=["../secret.pdf"]))
    assert response.status_code == 403
    assert response.json()["error"]["type"] == "PathAuthorizationError"


def test_missing_material_returns_422(base_env: dict[str, str]) -> None:
    app = create_app(Settings.from_env(base_env), gateway=FakeGateway([answer_json()]))
    with TestClient(app) as test_client:
        response = test_client.post("/solve", json=payload(materials=["不存在.pdf"]))
    assert response.status_code == 422
    assert response.json()["error"]["type"] == "MaterialUnavailableError"


def test_missing_team_returns_500(base_env: dict[str, str]) -> None:
    env = {k: v for k, v in base_env.items() if k != "RESEARCH_WORKSPACE_TEAM"}
    app = create_app(Settings.from_env(env), gateway=FakeGateway([answer_json()]))
    with TestClient(app) as test_client:
        response = test_client.post("/solve", json=payload())
    assert response.status_code == 500
    assert response.json()["error"]["type"] == "ConfigError"


def test_unconfigured_gateway_returns_500_on_chat(bare_client: TestClient) -> None:
    response = bare_client.post("/solve", json=payload())
    assert response.status_code == 500
    body = response.json()["error"]
    assert body["type"] == "ConfigError"
    assert body["case_id"] == "A-01"


def test_gateway_failure_returns_502(base_env: dict[str, str]) -> None:
    app = create_app(Settings.from_env(base_env), gateway=FakeGateway([GatewayError("upstream 502")]))
    with TestClient(app) as test_client:
        response = test_client.post("/solve", json=payload())
    assert response.status_code == 502
    assert response.json()["error"]["type"] == "GatewayError"


def test_truncated_model_output_returns_502(base_env: dict[str, str]) -> None:
    app = create_app(Settings.from_env(base_env), gateway=FakeGateway(["不是 JSON 的自由文本"]))
    with TestClient(app) as test_client:
        assert test_client.post("/solve", json=payload()).status_code == 502


def test_budget_exhaustion_returns_504(base_env: dict[str, str]) -> None:
    env = {**base_env, "RESEARCH_CASE_BUDGET_SECONDS": "0.001"}
    app = create_app(Settings.from_env(env), gateway=FakeGateway([answer_json()]))
    with TestClient(app) as test_client:
        response = test_client.post("/solve", json=payload())
    assert response.status_code == 504
    assert response.json()["error"]["type"] == "BudgetExhausted"


def test_error_body_never_leaks_the_api_key(base_env: dict[str, str]) -> None:
    app = create_app(Settings.from_env(base_env), gateway=FakeGateway([GatewayError("boom")]))
    with TestClient(app) as test_client:
        response = test_client.post("/solve", json=payload())
    assert base_env["CHALLENGE_LLM_API_KEY"] not in response.text


def test_error_body_shape_is_stable(client: TestClient) -> None:
    response = client.post("/solve", json=payload(case_id="A-1000"))
    assert set(response.json()) == {"error"}
    assert set(response.json()["error"]) == {"type", "message", "case_id"}


def test_get_on_solve_is_not_allowed(client: TestClient) -> None:
    assert client.get("/solve").status_code == 405


def test_unknown_path_returns_404(client: TestClient) -> None:
    assert client.get("/nope").status_code == 404


def test_solve_is_not_idempotent_by_task_id(client: TestClient) -> None:
    """赛题一没有 task_id 幂等语义；两次请求各自独立求解。"""
    first = client.post("/solve", json=payload())
    second = client.post("/solve", json=payload())
    assert first.json() == second.json()
```

- [ ] **Step 3: 跑合同测试**

```bash
uv run pytest tests/contract -v
```

预期：全部 PASS。若 `test_budget_exhaustion_returns_504` 因机器太快而通过不了，把预算改为 `"0.0001"`——`MIN_MODEL_SECONDS` 是 5.0，任何小于 5 秒的预算都会在发请求前抛 `BudgetExhausted`。

- [ ] **Step 4: 写集成测试**

`tests/integration/__init__.py` 为空文件。`tests/integration/test_solve_end_to_end.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from research_assistant.api import create_app
from research_assistant.runtime.gateway import FakeGateway
from research_assistant.runtime.settings import Settings
from tests.fixtures.dispatch_v3 import build_v3_dispatch, write_dispatch, write_questions

TEAM = "测试队伍 A+1"


def answer_json(answer: str = "光照强度影响光合速率。") -> str:
    return json.dumps({"answer": answer, "clarifying_question": "研究对象是哪种室内植物？"},
                      ensure_ascii=False)


def chat_payload(case_id: str = "A-100") -> dict[str, Any]:
    return {
        "case_id": case_id, "function": "Chat", "title": "室内植物光照与生长",
        "input": "解释光照强度与生长速度的关系，并问一个最关键的限定问题。",
        "materials": [], "constraints": [], "expected_output": "解释关系并提问。",
        "output_schema_ref": "Chat",
    }


@pytest.fixture
def mounted_dispatch(tmp_workspace: Path) -> None:
    record = build_v3_dispatch(TEAM)
    write_questions(tmp_workspace, TEAM, record)
    write_dispatch(tmp_workspace, TEAM, record)


def test_response_body_is_byte_identical_to_the_persisted_file(
    base_env: dict[str, str], tmp_workspace: Path
) -> None:
    app = create_app(Settings.from_env(base_env), gateway=FakeGateway([answer_json()]))
    with TestClient(app) as client:
        response = client.post("/solve", json=chat_payload())
    assert response.status_code == 200
    written = (tmp_workspace / "data" / TEAM / "A-100.json").read_bytes()
    assert response.content == written


def test_persisted_file_is_valid_utf8_json_with_the_same_case_id(
    base_env: dict[str, str], tmp_workspace: Path
) -> None:
    app = create_app(Settings.from_env(base_env), gateway=FakeGateway([answer_json()]))
    with TestClient(app) as client:
        client.post("/solve", json=chat_payload())
    loaded = json.loads((tmp_workspace / "data" / TEAM / "A-100.json").read_text(encoding="utf-8"))
    assert loaded["case_id"] == "A-100"
    assert set(loaded) == {"case_id", "answer", "clarifying_question"}


def test_chinese_content_survives_the_round_trip(base_env: dict[str, str],
                                                 tmp_workspace: Path) -> None:
    app = create_app(Settings.from_env(base_env),
                     gateway=FakeGateway([answer_json(answer="光照强度与光合速率正相关。")]))
    with TestClient(app) as client:
        response = client.post("/solve", json=chat_payload())
    assert "光照强度与光合速率正相关。" in response.text
    assert b"\\u" not in response.content


def test_a_mounted_dispatch_is_accepted_and_does_not_block_solving(
    base_env: dict[str, str], tmp_workspace: Path, mounted_dispatch: None
) -> None:
    app = create_app(Settings.from_env(base_env), gateway=FakeGateway([answer_json()]))
    with TestClient(app) as client:
        assert client.get("/health").json()["dispatch_mounted"] is True
        assert client.post("/solve", json=chat_payload()).status_code == 200


def test_a_tampered_question_file_blocks_solving(base_env: dict[str, str],
                                                 tmp_workspace: Path,
                                                 mounted_dispatch: None) -> None:
    (tmp_workspace / "data" / TEAM / "assigned" / "B-01.json").write_text(
        '{"case_id": "B-01", "tampered": true}', encoding="utf-8"
    )
    app = create_app(Settings.from_env(base_env), gateway=FakeGateway([answer_json()]))
    with TestClient(app) as client:
        response = client.post("/solve", json=chat_payload())
    assert response.status_code == 422
    assert response.json()["error"]["type"] == "DispatchValidationError"


def test_no_result_file_is_written_when_the_handler_fails(base_env: dict[str, str],
                                                          tmp_workspace: Path) -> None:
    from research_assistant.errors import GatewayError

    app = create_app(Settings.from_env(base_env), gateway=FakeGateway([GatewayError("boom")]))
    with TestClient(app) as client:
        assert client.post("/solve", json=chat_payload()).status_code == 502
    assert not (tmp_workspace / "data" / TEAM / "A-100.json").exists()


def test_unwritable_team_dir_reports_failure_not_success(base_env: dict[str, str],
                                                         tmp_workspace: Path) -> None:
    team_dir = tmp_workspace / "data" / TEAM
    team_dir.chmod(0o555)
    try:
        app = create_app(Settings.from_env(base_env), gateway=FakeGateway([answer_json()]))
        with TestClient(app) as client:
            response = client.post("/solve", json=chat_payload())
        assert response.status_code == 500
        assert response.json()["error"]["type"] == "PersistenceError"
    finally:
        team_dir.chmod(0o755)


def test_sequential_requests_each_write_their_own_result(base_env: dict[str, str],
                                                         tmp_workspace: Path) -> None:
    app = create_app(
        Settings.from_env(base_env),
        gateway=FakeGateway([answer_json("第一个答案。"), answer_json("第二个答案。")]),
    )
    with TestClient(app) as client:
        client.post("/solve", json=chat_payload("A-01"))
        client.post("/solve", json=chat_payload("A-02"))
    first = json.loads((tmp_workspace / "data" / TEAM / "A-01.json").read_text(encoding="utf-8"))
    second = json.loads((tmp_workspace / "data" / TEAM / "A-02.json").read_text(encoding="utf-8"))
    assert first["answer"] == "第一个答案。"
    assert second["answer"] == "第二个答案。"
    assert first["case_id"] == "A-01" and second["case_id"] == "A-02"


def test_work_directory_is_not_polluted_by_chat(base_env: dict[str, str],
                                                tmp_workspace: Path) -> None:
    app = create_app(Settings.from_env(base_env), gateway=FakeGateway([answer_json()]))
    with TestClient(app) as client:
        client.post("/solve", json=chat_payload())
    assert list((tmp_workspace / "data" / TEAM / "work").iterdir()) == []


def test_assigned_questions_are_never_overwritten(base_env: dict[str, str],
                                                  tmp_workspace: Path,
                                                  mounted_dispatch: None) -> None:
    before = (tmp_workspace / "data" / TEAM / "assigned" / "A-100.json").read_bytes()
    app = create_app(Settings.from_env(base_env), gateway=FakeGateway([answer_json()]))
    with TestClient(app) as client:
        client.post("/solve", json=chat_payload())
    assert (tmp_workspace / "data" / TEAM / "assigned" / "A-100.json").read_bytes() == before
    assert (tmp_workspace / "data" / TEAM / "dispatch.json").is_file()
```

- [ ] **Step 5: 跑集成测试**

```bash
uv run pytest tests/integration -v
```

预期：全部 PASS。

- [ ] **Step 6: 跑全量测试与静态检查**

```bash
uv run pytest -v
uv run ruff check .
uv run ruff format --check .
```

预期：全部 PASS，无告警。

- [ ] **Step 7: 手工起服务验证一次（本地证据）**

```bash
cd /Users/luananwei/科研助手/research-assistant
RESEARCH_WORKSPACE_ROOT=/tmp/ra-smoke RESEARCH_WORKSPACE_TEAM=smoke \
  uv run python -m research_assistant &
sleep 3
curl -fsS http://localhost:8080/health
curl -sS -X POST http://localhost:8080/solve -H 'Content-Type: application/json' \
  -d '{"case_id":"B-01","function":"Literature_Retrieval","title":"t","input":"i",
       "materials":[],"constraints":[],"expected_output":"e",
       "output_schema_ref":"Literature_Retrieval"}'
kill %1
```

预期：`/health` 返回 200 与小 JSON；`/solve` 对 B-01 返回 501 且 `error.type == "HandlerNotImplemented"`（没有网关配置时 B 类应先报 501 而非 500，因为路由在装配网关之前就已确定）。记录实际输出到进度记录。

- [ ] **Step 8: 提交**

```bash
git add src/research_assistant/api.py src/research_assistant/__main__.py \
        tests/contract tests/integration
git commit -m "feat: add health and solve HTTP endpoints with contract tests"
```

---

### Task 11: 真实网关 live 测试与交接文档

**Files:**
- Create: `tests/live/test_live_chat.py`
- Modify: `docs/开发进度与接力记录.md`
- Modify: `docs/开发规范与Agent接力.md`（§7.3 与 §12 Q01：登记已实测的开发网关事实）
- Modify: `AGENTS.md`（补上现在真实存在的构建/测试/启动命令）

**Interfaces:**
- Consumes: `create_app`（Task 10）、`Settings`（Task 3）、`.env.local`（已被 `.gitignore` 忽略）
- Produces: `pytest -m live` 可运行的真实网关 A 类端到端测试；更新后的交接记录

- [ ] **Step 1: 写 live 测试**

`tests/live/__init__.py` 为空文件。`tests/live/test_live_chat.py`:

```python
"""真实网关 A 类端到端测试。

需要 .env.local 中的凭据与网络。默认被 addopts 的 `-m 'not live'` 排除。
运行：uv run pytest -m live -v
证据级别为「本地测试 + 真实模型调用」，仍不等于镜像验证或平台验证。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from research_assistant.api import create_app
from research_assistant.runtime.settings import Settings

pytestmark = pytest.mark.live

ENV_FILE = Path(__file__).resolve().parents[2] / ".env.local"


def load_local_env() -> dict[str, str]:
    if not ENV_FILE.is_file():
        pytest.skip(f"{ENV_FILE.name} not found; live gateway tests need real credentials")
    env: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        env[key.strip()] = value.strip()
    return env


@pytest.fixture
def live_settings(tmp_path: Path) -> Settings:
    env = {**load_local_env(), **os.environ}
    settings = Settings.from_env(env)
    if settings.gateway is None:
        pytest.skip(f"no complete gateway group: {settings.gateway_config_error}")
    return Settings(
        gateway=settings.gateway,
        gateway_config_error=settings.gateway_config_error,
        team="live-smoke",
        workspace_root=tmp_path / "output",
        papers_root=tmp_path / "papers",
        case_budget_seconds=settings.case_budget_seconds,
        log_level=settings.log_level,
    )


CHAT_TASK = {
    "case_id": "A-01",
    "function": "Chat",
    "title": "室内植物光照与生长：科研对话",
    "input": (
        "我想研究不同光照条件对室内植物生长速度的影响。请解释光照强度、每日照射时长和"
        "生长速度之间可能有什么关系，并问我一个最关键的研究范围限定问题。"
    ),
    "materials": [],
    "constraints": [],
    "expected_output": "解释光照条件与植物生长速度的可能关系，并提出一个研究范围限定问题。",
    "output_schema_ref": "Chat",
}


def test_live_chat_returns_a_schema_valid_answer(live_settings: Settings,
                                                 tmp_path: Path) -> None:
    (tmp_path / "output").mkdir(parents=True, exist_ok=True)
    (tmp_path / "papers").mkdir(parents=True, exist_ok=True)
    with TestClient(create_app(live_settings)) as client:
        response = client.post("/solve", json=CHAT_TASK)

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"case_id", "answer", "clarifying_question"}
    assert body["case_id"] == "A-01"
    assert isinstance(body["answer"], str) and len(body["answer"].strip()) >= 20
    assert isinstance(body["clarifying_question"], str)
    assert body["clarifying_question"].strip()

    written = (tmp_path / "output" / "data" / "live-smoke" / "A-01.json").read_bytes()
    assert response.content == written


def test_live_answer_mentions_the_light_variables(live_settings: Settings,
                                                  tmp_path: Path) -> None:
    """内容相关性只做弱断言：官方档位由裁判判定，本地不得声称算出了分数。"""
    (tmp_path / "output").mkdir(parents=True, exist_ok=True)
    (tmp_path / "papers").mkdir(parents=True, exist_ok=True)
    with TestClient(create_app(live_settings)) as client:
        body = client.post("/solve", json=CHAT_TASK).json()
    text = body["answer"] + body["clarifying_question"]
    assert any(word in text for word in ("光照", "光", "light")), text[:200]


def test_live_gateway_usage_is_captured(live_settings: Settings, tmp_path: Path) -> None:
    (tmp_path / "output").mkdir(parents=True, exist_ok=True)
    (tmp_path / "papers").mkdir(parents=True, exist_ok=True)
    with TestClient(create_app(live_settings)) as client:
        assert client.post("/solve", json=CHAT_TASK).status_code == 200
```

- [ ] **Step 2: 跑 live 测试**

```bash
cd /Users/luananwei/科研助手/research-assistant
uv run pytest -m live -v -s
```

预期：3 个测试 PASS（需要网络）。记录实际耗时与返回内容摘要到交接记录，**不要**把模型响应全文或 key 写进文档。

若 skip，检查 `.env.local` 是否存在且三个 `CHALLENGE_LLM_*` 变量都非空。

- [ ] **Step 3: 确认默认测试运行排除 live**

```bash
uv run pytest -v 2>&1 | tail -20
```

预期：输出中不出现 `tests/live/`，且末尾统计不含 live 测试。

- [ ] **Step 4: 跑全量验证并记录真实输出**

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
git status --short
```

把每条命令的退出状态与关键输出抄进交接记录。不得把未执行的检查写成通过。

- [ ] **Step 5: 更新 AGENTS.md**

在 `## Development and Verification` 一节，把「No project build, lint, or test command exists yet.」替换为 P1 实际可用的命令：

```markdown
## Development and Verification

Commands available since P1, run from this repository:

```sh
uv sync                      # 建立 Python 3.11 环境并装齐锁定依赖
uv run pytest                # 单元/合同/集成测试；默认排除 live
uv run pytest -m live        # 真实网关 A 类端到端，需要 .env.local 与网络
uv run ruff check .          # 静态检查
uv run ruff format --check . # 格式检查
RESEARCH_WORKSPACE_ROOT=/tmp/ra RESEARCH_WORKSPACE_TEAM=smoke uv run python -m research_assistant
```

Read-only checks that remain valid:

```sh
git status --short
python3 ../赛题信息/research-experiments-20261020-source-v1/model_source.py --help
shasum -a 256 ../agenthack-base-1.1.1.tar
```

There is still no Dockerfile and no image build. Local test passes are not image
verification and not platform verification.
```

同时在 `## Project Structure` 一节把「It currently contains documentation; application code, tests, dependency configuration, and Dockerfile are not implemented.」改为：application code、tests 与 `pyproject.toml` 已在 P1 建立；Dockerfile 仍未实现。

- [ ] **Step 6: 追加交接记录**

在 `docs/开发进度与接力记录.md` 中：

1. 更新「当前状态」表：P1 行改为「本地通过（R02/R03 未执行）」，证据列填真实命令与结果摘要；P2–P6 的依赖列改为指向 P1 已落地的接口文件路径。
2. 更新「已确认的开发基线」，追加三条本轮核验事实：
   - 基础镜像最终生效版本：`fastapi 0.111.0`、`starlette 0.37.2`、`pydantic 2.8.0`、`uvicorn 0.30.0`、`httpx 0.27.0`（0.28.1 被后层 whiteout 降级）、`anyio 4.15.1`、Python 3.11.16；镜像内**无** PDF 库、Pillow、matplotlib、OCR、scikit-learn，也**无** pytest/ruff。
   - `agenthack_sdk 1.1.1` 的 `AgentRuntime` 用的是通用赛题合同（`TaskRequest`/`TaskResponse`），与赛题一八字段输入和纯结果 JSON 冲突，**不得复用**；只复用 `install_all_interceptors()` 与 `Telemetry.session()`。
   - 开发网关实测为 OpenAI 兼容 vLLM（`vllm-0.23.0-tp4-dp8-ep`），`stream=false` 返回标准 `usage` 与 `finish_reason`，`message` 含 `reasoning` 字段，不带 `X-Session-ID` 也返回 200。凭据只存 `.env.local`（已忽略），不写入仓库。
3. 按模板追加一条完整交接记录，字段全部填实：日期/Agent、阶段 P1、任务状态、允许修改范围、分支与 commit、本次改动文件与接口、关键决定及依据、执行的验证（命令+退出状态+关键输出+证据路径）、尚未执行的验证及原因、已知问题、依赖与待确认项、下一条可执行动作。
4. 在「尚未执行的验证」中明确列出：R02 镜像构建与容器内验证、R03 平台联调、G04 文档解析、B01、C01/C02、E01、D01–D05。
5. 新增未决项 N01（镜像无 PDF 库，P2 必须决定构建时安装还是纯标准库自写）与 N02（SDK telemetry 的 OTEL 端点由谁注入、是否影响 30% 复核）。
6. 「下一条可执行动作」写 P2 的入口：先决定 PDF 解析方案（N01），再实现 `documents/` 与 B handler。
7. 保留历史记录，不删除上一轮的验证与未决项。

- [ ] **Step 7: 提交**

```bash
git add tests/live AGENTS.md docs/开发进度与接力记录.md docs/开发规范与Agent接力.md
git commit -m "test: add live gateway chat test and record P1 handoff"
```

- [ ] **Step 8: 核对完成定义**

逐条对照 spec §10：

1. `uv run pytest` 全绿（live 默认排除）
2. `uv run ruff check .` 与 `uv run ruff format --check .` 无告警
3. 本地 uvicorn 起服务后 `/health` 返回 200、A 类 `/solve` 返回合规 JSON 且与落盘文件逐字节一致
4. 交接记录已追加真实命令、退出状态、关键输出、证据路径与未执行的检查
5. R02/R03 与 G04/B01/C01/C02/E01/D01–D05 明确标为「未执行」

全部满足才可在交接记录里把 P1 标为「本地通过」。任一项不满足就保持「进行中」，并写明缺什么。

