# P1 最小合规服务 —— 设计规格

> 版本：1.0 · 编写日期：2026-10-23（Asia/Shanghai）· 状态：设计已获项目负责人批准，待实现
> 上游文档：[开发规范与Agent接力](../../开发规范与Agent接力.md)（总纲）、[开发进度与接力记录](../../开发进度与接力记录.md)
> 本文件只覆盖 P1。P2–P6 的设计在各自阶段另立规格，不得据本文件推断后续阶段已实现。

## 1. 目标与范围

P1 的目标是消除协议、路径和交付层面的风险：让一个符合赛题一 v3 合同的 HTTP 服务能在本地跑通，A 类（Chat）端到端可用，其余四类只保证正确路由与明确报错。

**本轮包含**

- `contracts/`：八字段 Task、v3 派题记录、五类输出的类型与校验
- `runtime/`：配置适配器、monotonic 预算、授权路径、原子落盘与回读、Gateway（真实 + 假）、SDK telemetry 降级适配、脱敏日志
- `api.py` + `solve.py`：`GET /health`、`POST /solve` 与单题编排
- `handlers/`：路由表 + A 类实现；B/C/D/E 为显式未实现桩
- 测试：unit / contract / integration / live / fixtures
- `pyproject.toml`、`.env.example`

**本轮明确不包含**

- Dockerfile、镜像构建、`docker load` 基础镜像、容器内验证（验收 R02 未执行）
- 平台联调与正式评测（R03 未执行）
- PDF/文本解析、证据层、OCR（P2）
- C/E 报告生成与引用质量校验（P3）
- D 类源码生成、来源准入、helper 调用（P4/P5）
- 网页 UI、账号系统、数据库、向量服务

**已批准的决策**

| 决策项 | 选择 | 理由 |
|---|---|---|
| P1 验证深度 | 代码 + 本地 pytest；不建 Dockerfile、不 load 镜像 | 先消除协议风险，镜像验证集中到 P6 |
| 真实 Gateway 适配器 | 本轮实现，采用「整组探测 + 冲突报错」 | 文档 §10 警告网关不匹配应尽早暴露 |
| SDK telemetry | 复用 `agenthack` 的 interceptor 与 session，本地无 SDK 时降级 no-op | 保留平台可能用于 30% 复核的 trace 证据 |
| 执行模型 | 混合：HTTP 层 async，业务 handler 同步，经 `anyio.to_thread` 桥接 | D 类阻塞子进程与 monotonic 预算天然契合，handler 可读可测 |

## 2. 已核验的环境事实

以下均为本轮对 `agenthack-base-1.1.1.tar` 的**只读归档检查**结果（未 `docker load`、未启动容器），以及对开发网关的真实 HTTP 探测结果。证据级别：静态核对 + 本地网络探测。

### 2.1 基础镜像 `agenthack/base:1.1.1`

- SHA-256 `57ce57fff6836a60e48954715d94db1142d1a8dbd1cbed77e014fc95da07f7d2`，linux/amd64，`USER agent`（非 root），`WORKDIR /app`，`EXPOSE 8080/tcp`
- 默认 `CMD` 故意打印错误并 `exit 1`，必须覆盖
- `HEALTHCHECK` 为 `curl -fsS http://localhost:8080/health`，Interval 5s / Timeout 3s / StartPeriod 10s / Retries 6 → **`/health` 必须返回 2xx 且不需要认证**
- Python 3.11.16；`AGENTHACK_SDK_VERSION=1.1.1`、`AGENTHACK_LOG_LEVEL=INFO`

最终生效的关键版本（同名多版本时以最高层的 whiteout 结果为准）：

| 包 | 版本 | 备注 |
|---|---|---|
| fastapi | 0.111.0 | |
| starlette | 0.37.2 | fastapi 传递依赖 |
| pydantic / pydantic-core | 2.8.0 / 2.20.0 | |
| uvicorn | 0.30.0 | |
| httpx | **0.27.0** | 层 6 装了 0.28.1，层 7 装 openai 1.35.0 时降级并 whiteout 掉 0.28.1 |
| httpcore | 1.0.9 | |
| anyio | 4.15.1 | |
| typing_extensions | 4.16.0 | |
| requests | 2.32.0 | |
| openai | 1.35.0 | |
| numpy / pandas | 1.26.4 / 2.2.2 | |
| tiktoken / orjson | 0.14.0 / 3.12.0 | |
| langchain / langchain-openai | 0.2.0 / 0.1.8 | |
| agenthack-sdk | 1.1.1 | |
| pytest / ruff | **不存在** | 仅本地 dev 依赖，不进镜像 |

**镜像中缺失且后续阶段必须自行解决的能力**：任何 PDF 解析库（无 pypdf / pdfminer.six / PyMuPDF）、无 Pillow、无 matplotlib、无 OCR（pytesseract/tesseract）、无 scikit-learn。这是 P2 的前置风险，P1 不处理，但必须记录。

D 类图表由平台侧冻结实验容器生成（官方接入说明：helper「执行真实环境检查、数据读取、实验计算及图表生成」），因此镜像内无 matplotlib 不构成 D 类阻塞。此结论待在 P4 用真实 helper 复核。

### 2.2 官方 SDK `agenthack` 1.1.1 的实际接口

`RECORD` 确认最终文件集为 `__init__.py`、`base_agent.py`、`interceptor.py`、`network.py`、`recorder.py`、`runtime.py`、`serve.py`、`telemetry.py`。导出 `AgentRuntime`、`BaseAgent`、`turn/state/tool/tool_call/llm_call/llm_context/recovery`。

**关键冲突（总纲未记录，本轮新发现）**：`AgentRuntime` 自带的 `/solve` 使用通用赛题合同——

- 请求模型 `TaskRequest`：`task_id: str`、`task_type: str = "qa"`、`input: Dict[str, Any]`、`constraints: {timeout_ms: int, max_tokens: int|None}`、`session_id`、`user_id_hash`
- 响应模型 `TaskResponse`：`task_id`、`status`、`output`、`metadata`
- 且当返回值不含 `answer` 键时会把结果强制包成 `{"answer": str(result)}`

赛题一要求收到八字段（其中 `constraints` 是**字符串数组**，不是对象），并直接返回 `case_id/answer/clarifying_question` 等**恰好该类型允许的顶层字段**、不得增加额外顶层字段。

结论：**不得使用 `AgentRuntime` 与 `BaseAgent` 承载赛题一的 `/solve`**，否则请求会被 FastAPI 判 422、响应会违规。必须自建 FastAPI app。SDK 的 telemetry 能力可独立复用（见 §5.6）。

`install_all_interceptors()` 会 patch `httpx.Client._send_single_request`、`httpx.AsyncClient._send_single_request`、`requests.adapters.HTTPAdapter.send` 以及 `openai` 的 `Completions.create` / `AsyncCompletions.create`。但 `start_llm` 仅在 `current_session()` 非空时记录，即**必须同时处于 `Telemetry.session(...)` 上下文内**才能拿到 LLM trace。telemetry 导出端点取 `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` 或 `OTEL_EXPORTER_OTLP_ENDPOINT`，`AGENTHACK_TELEMETRY_REQUIRED` 默认 `false`。

### 2.3 开发网关实测

对本轮负责人提供的开发网关做了真实 HTTP 探测（凭据只存于被 `.gitignore` 忽略的 `.env.local`，不写入本文件、源码、镜像或结果文件）：

- `GET {base_url}/models` → 200，`data` 中列出 `deepseek_v4`
- `POST {base_url}/chat/completions`，`stream=false`、`max_tokens=32` → 200，耗时 1.24s
- 响应为标准 OpenAI Chat Completion 形状：`choices[0].message.content`、`choices[0].finish_reason == "stop"`、`usage.{prompt_tokens,completion_tokens,total_tokens}`、`usage.prompt_tokens_details.cached_tokens`；`usage.completion_tokens_details` 为 `null`
- `system_fingerprint` 为 `vllm-0.23.0-tp4-dp8-ep-cbfdf7f4` → 后端是 vLLM，OpenAI 兼容
- `message` 含 `reasoning` 字段（本次为 `null`）→ 推理型模型，实现时不得假定该字段不存在
- **不带 `X-Session-ID` 也返回 200**

这回答了总纲 Q01 的一部分：开发网关是 OpenAI 兼容、无需会话头。但**正式评测时由平台注入的变量名、会话头要求和模型名仍可能不同**，因此 §5.1 的整组探测设计保留，且本结论不得外推为「平台一定不要求会话头」。

## 3. 架构与目录

分层依据总纲 §7.1「Python 单服务 + 显式任务处理器」。P1 只建骨架必需的模块，避免空壳目录。

```text
research-assistant/
├── pyproject.toml
├── .env.example
├── .gitignore
├── src/research_assistant/
│   ├── __init__.py
│   ├── api.py                  # FastAPI app 工厂：GET /health、POST /solve
│   ├── solve.py                # solve(task, context) 编排
│   ├── errors.py               # 异常层级 → HTTP 映射
│   ├── contracts/
│   │   ├── __init__.py
│   │   ├── task.py             # 八字段 Task
│   │   ├── dispatch.py         # v3 派题记录
│   │   └── results.py          # 五类输出构造器与字段白名单
│   ├── runtime/
│   │   ├── __init__.py
│   │   ├── settings.py         # 配置适配器：整组探测 + 冲突报错
│   │   ├── deadline.py         # monotonic 预算
│   │   ├── paths.py            # 授权路径解析与越界拒绝
│   │   ├── persistence.py      # 原子落盘 + 回读校验
│   │   ├── gateway.py          # Gateway 协议 / HttpGateway / FakeGateway
│   │   ├── telemetry.py        # agenthack SDK 可选复用 + no-op 降级
│   │   └── logging.py          # 脱敏结构化日志
│   └── handlers/
│       ├── __init__.py
│       ├── registry.py         # function ↔ output_schema_ref 路由表
│       └── chat.py             # A 类实现
└── tests/
    ├── unit/
    ├── contract/
    ├── integration/
    ├── live/
    └── fixtures/
```

**不建** `documents/`、`validation/`、`reproduction/`：分别属于 P2、P3、P4。校验逻辑在 P1 内聚于 `contracts/`，等 P2 出现跨模块复用需求再抽出，避免过早抽象。

### 3.1 依赖与工具链

- 包管理 `uv`；本地解释器 CPython 3.11.15（本机已由 uv 安装，与镜像 3.11.16 同系列）
- `requires-python = ">=3.11,<3.12"`，与镜像严格对齐，防止本地用到 3.12+ 语法
- 运行时依赖全部锁定为 §2.1 的镜像实测版本；P1 阶段镜像内已存在，构建时无需 pip install
- dev 依赖：`pytest`、`ruff`；两者不进镜像
- Ruff 配置必须排除官方材料目录与模型生成源码（总纲 §8.3）；P1 尚无生成源码，但排除规则现在就写进去

## 4. 核心接口

```python
# contracts/task.py
class TaskFunction(StrEnum):
    CHAT = "Chat"
    LITERATURE_RETRIEVAL = "Literature_Retrieval"
    SURVEY = "Survey"
    REPRODUCTION = "Reproduction"
    IDEA = "Idea"

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
    unknown_fields: Mapping[str, Any]   # 只读；记录协议差异，不静默丢弃

    @classmethod
    def parse(cls, payload: Mapping[str, Any]) -> "Task": ...
```

`Task.parse` 只做**不依赖文件系统**的三重校验，任一失败抛 `TaskContractError`：

1. **身份**：八字段全部存在；`case_id` 非空；形如 `A-01`…`A-100` 的编号不得假定为两位后缀（正则 `^[A-E]-\d{1,3}$`）
2. **类型**：`case_id/function/title/input/expected_output/output_schema_ref` 为 `str`；`materials/constraints` 为 `list[str]`（缺材料/约束时是空数组，不是 `null`）
3. **业务语义**：`function` 属于五类枚举；`output_schema_ref` 与 `function` 的映射一致，不一致时**不得猜测路由**

第四重**材料范围**校验放在 `load_task_context()`，因为它需要 `WorkspacePaths`：`materials` 中每个条目必须能解析到授权论文目录内的真实文件。P1 只校验解析与归属，不读取 PDF 内容。这样切分的理由是 `Task` 保持为纯值对象、可独立单测，路径授权只在持有工作区的层发生一次。

未识别的扩展字段收进 `unknown_fields` 并以 warning 记为协议差异（总纲 §3.2），既不报错也不透传到输出。

```python
# contracts/dispatch.py
@dataclass(frozen=True)
class DispatchCase:
    case_id: str; function: str; title: str
    question_file: str; question_sha256: str; difficulty: str | None

@dataclass(frozen=True)
class DispatchRecord:
    dispatch_id: str; team: str; schema_version: int
    selection_mode: str; dispatch_policy_version: str; dispatch_policy_sha256: str
    rules_version: str
    cases_per_category: Mapping[str, int]      # v3 是字典，不是整数
    difficulty_by_case: Mapping[str, str]
    case_ids: tuple[str, ...]
    cases: tuple[DispatchCase, ...]

    @classmethod
    def load(cls, path: Path) -> "DispatchRecord": ...
    def validate(self) -> None: ...
```

`validate()` 检查（验收 G01）：`schema_version == 3`；`rules_version == "research-assistant-objective-v3"`；`cases_per_category == {"A":1,"B":3,"C":2,"D":3,"E":2}`；`case_ids` 恰好 11 个且互不重复；各类计数为 1/3/2/3/2；`cases` 与 `case_ids` 一一对应；每个 `question_file` 位于本队 `assigned/` 下且文件字节的 SHA-256 等于 `question_sha256`；`team` 与运行时 `RESEARCH_WORKSPACE_TEAM` 一致。

`schema_version == 2` 或 `cases_per_category` 为整数的记录只作为**明确标识的历史调试配置**接受，并在日志与结果元数据中打标，不生成 v3 完整榜分预期。

派题文件缺失不是致命错误：总纲 §3.3 指出外部平台可能逐题直传 JSON 而不挂载派题文件。此时 `SolveContext.dispatch` 为 `None`，不得伪造派题记录。

```python
# runtime/paths.py
class WorkspacePaths:
    def __init__(self, root: Path, team: str) -> None: ...
    @property
    def team_dir(self) -> Path: ...          # <root>/data/<team>
    @property
    def assigned_dir(self) -> Path: ...      # <root>/data/<team>/assigned
    @property
    def expert_dir(self) -> Path: ...        # <root>/data/expert
    @property
    def figure_dir(self) -> Path: ...        # <root>/data/figure/<team>
    def paper(self, material: str) -> Path: ...        # /data/papers 内的授权文件
    def result_json(self, case_id: str) -> Path: ...
    def survey_md(self, case_id: str) -> Path: ...     # <case_id>-综述-<team>.md
    def idea_md(self, case_id: str) -> Path: ...       # <case_id>-灵感Idea报告-<team>.md
    def work_dir(self, case_id: str) -> Path: ...      # data/<team>/work/<case_id>/
    def relative_to_output(self, path: Path) -> str: ...
```

安全要求（验收 G03）：拒绝提交用绝对路径、拒绝 `..` 路径片段、拒绝反斜杠绕过；对解析结果调 `Path.resolve()` 后确认仍在授权根内，以挡住符号链接越界；拒绝访问其他队伍目录与平台内部目录。中文、空格、`+` 等合法字符必须原样保留。**任何文件名都不得拼进 shell 命令**。

`relative_to_output` 产出 JSON 内的 `path`（相对 `/output`）；调用 helper 时另传绝对路径，两者不得混用（总纲 §4.2）。

```python
# runtime/deadline.py
@dataclass(frozen=True)
class Deadline:
    started_at: float                 # time.monotonic()
    budget_seconds: float

    def remaining(self) -> float: ...
    def take(self, stage: str, minimum: float) -> float:
        """领取本阶段可用秒数；不足 minimum 时抛 BudgetExhausted。"""
    def guard(self, stage: str) -> ContextManager[None]:
        """with 块结束时若已超支则抛 BudgetExhausted。"""
```

预算沿参数显式传递，禁止模块级可变状态。整题 180s（D 类新版材料依据）；A/B/C/E 的精确限时属总纲 Q03【待确认】，P1 用可配置的同一默认值并在日志中记录实际耗时，供联调后校准。`max_duration_ms=5400000`（90 分钟）是平台侧阶段总上限，不由单题 Agent 自行分配。`timeslice_budget=300` 的单位与作用属 Q07【待确认】，P1 **不得**把它解释成 300 秒或 Token 额度。

```python
# runtime/gateway.py
@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant"]
    content: str

@dataclass(frozen=True)
class GatewayUsage:
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    cached_tokens: int | None

@dataclass(frozen=True)
class GatewayResponse:
    content: str
    finish_reason: str | None
    usage: GatewayUsage
    reasoning: str | None            # §2.3 实测存在该字段，不得假定缺失
    raw: Mapping[str, Any]           # 完整原始响应，供 D 类源码提取使用

class Gateway(Protocol):
    def complete(self, messages: Sequence[Message], *, context: SolveContext,
                 stage: str, max_tokens: int | None = None) -> GatewayResponse: ...

class HttpGateway:    # httpx.Client，同步；超时取 deadline.take(stage, ...)
    ...
class FakeGateway:    # 测试用；按脚本返回，可注入延迟、截断、非 stop 的 finish_reason
    ...
```

`HttpGateway` 不做无界重试（总纲 §6.6）。请求超时来自 `Deadline`，不写死常量。响应解析容忍 `usage` 子字段为 `null`。凭据只经 `Settings` 注入，任何日志、异常消息、结果文件都不得出现 key 或完整请求头。

```python
# runtime/persistence.py
def write_json_verified(path: Path, payload: Mapping[str, Any]) -> bytes:
    """临时文件 + os.replace 原子替换；回读并断言可解析、内容等价；返回落盘字节。"""

def write_text_verified(path: Path, text: str) -> bytes:
    """UTF-8 原样落盘后回读，断言与入参逐字节一致。用于 C/E 的 markdown。"""
```

写入失败、回读不一致或权限不足时抛异常，**不得报告成功**（验收 G05）。D 类规则「已成功的实验产物不得被后续解析失败覆盖」属 P4，届时由 D 的编排层在调用前判断，`persistence` 本身不预留开关。

```python
# runtime/settings.py
@dataclass(frozen=True)
class GatewaySettings:
    family: Literal["challenge", "platform"]   # 只记组名，不记值
    base_url: str
    api_key: str                               # repr/str 必须屏蔽
    model: str
    session_id: str | None

@dataclass(frozen=True)
class Settings:
    gateway: GatewaySettings | None            # None 表示两组都不齐备
    team: str
    workspace_root: Path                       # 默认 /output
    papers_root: Path                          # 默认 /data/papers
    case_budget_seconds: float                 # 默认 180.0
    log_level: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Settings": ...

# runtime/logging.py
class BoundLogger(Protocol):
    """已绑定 case_id / dispatch_id / 版本号的日志器；只输出 §5.2 允许且脱敏的字段。"""
    def info(self, event: str, **fields: Any) -> None: ...
    def warning(self, event: str, **fields: Any) -> None: ...
    def error(self, event: str, **fields: Any) -> None: ...

# handlers/registry.py
class Handler(Protocol):
    def __call__(self, task: Task, context: SolveContext) -> Mapping[str, Any]: ...

HANDLER_BY_FUNCTION: Mapping[TaskFunction, Handler]
SCHEMA_REF_BY_FUNCTION: Mapping[TaskFunction, str]   # 用于一致性校验

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

def load_task_context(task: Task, settings: Settings) -> SolveContext: ...
def solve(task: Task, context: SolveContext) -> Mapping[str, Any]: ...
def persist_and_verify(result: Mapping[str, Any], context: SolveContext) -> bytes: ...
```

签名与总纲 §7.2 草案一致。`solve()` 不遍历整轮、不生成官方评分、不实现自己的「裁判」。`load_task_context()` 负责第四重材料范围校验（见 §4 `Task.parse`）。

```python
# contracts/results.py
@dataclass(frozen=True)
class Metric:
    name: str                    # 原样保留，不翻译、不归一化
    unit: str                    # 同上
    reference: float | None
    observed: float | None       # 失败时为 None，序列化为 JSON null
    relative_error: float | None

def chat_result(case_id: str, *, answer: str, clarifying_question: str) -> dict[str, Any]
def retrieval_result(case_id: str, *, papers: Sequence[Mapping[str, str]]) -> dict[str, Any]
def survey_result(case_id: str, *, path: str, markdown: str) -> dict[str, Any]
def reproduction_result(case_id: str, *, status: str,
                        metrics: Sequence[Metric], figures: Sequence[str]) -> dict[str, Any]
def idea_result(case_id: str, *, path: str, markdown: str) -> dict[str, Any]
```

每个构造器强制字段白名单（验收 G02）：

| 类型 | 恰好允许的顶层字段 |
|---|---|
| A / Chat | `case_id`, `answer`, `clarifying_question` |
| B / Literature_Retrieval | `case_id`, `papers`（元素仅 `filename`, `reason`） |
| C / Survey | `case_id`, `path`, `markdown` |
| D / Reproduction | `case_id`, `status`, `metrics`, `figures` |
| E / Idea | `case_id`, `path`, `markdown` |

构造器额外保证：`answer`/`clarifying_question` 为非空字符串；C/E 的 `markdown` 正文长度 ≥ 800 字符；数字为有限 JSON 数字或规则允许的 `null`，禁止数字字符串、`NaN`、`Infinity`；D 的 `metrics` 恰好三项，每项含 `name/unit/reference/observed/relative_error`，指标名与单位原样保留不翻译。E 不使用旧版 `research_question/technical_route/metrics` 结构。

序列化统一 `json.dumps(..., ensure_ascii=False, allow_nan=False)`，UTF-8，直接返回对象——无自然语言前缀、无代码围栏。

## 5. 运行时组件

### 5.1 配置适配器（整组探测）

两组互斥写法（总纲 §7.3、Q01）：

| 组 | 变量 |
|---|---|
| `CHALLENGE`（赛题一规范记载） | `CHALLENGE_LLM_BASE_URL`、`CHALLENGE_LLM_API_KEY`、`CHALLENGE_LLM_MODEL`，可选 `CHALLENGE_SESSION_ID` |
| `PLATFORM`（D 类材料包示例） | `PLATFORM_LLM_URL`、`PLATFORM_API_KEY`、`PLATFORM_LLM_MODEL` |

规则：

- 一组的**必需变量全部非空**才算「齐备」
- 恰好一组齐备 → 使用该组，记录组名（不记录值）
- 两组都齐备且解析出的 base_url / model / key 不一致 → 抛 `ConfigError`
- 两组都齐备且完全一致 → 使用 `CHALLENGE` 组，记 info
- 两组都不齐备 → 抛 `ConfigError`；`/health` 仍返回 200 但 `gateway_configured=false`
- **禁止逐变量混合 fallback**（例如取 A 组的 URL 配 B 组的 key）

`base_url` 保留平台给出的完整路径，不得裁剪或自猜补 `/v1`。存在 `CHALLENGE_SESSION_ID` 时按规范附 `X-Session-ID` 请求头；§2.3 实测开发网关不要求该头，但这不外推到正式环境。

其余设置：`RESEARCH_WORKSPACE_TEAM`（稳定队伍标识，优先于任何展示队名）、`RESEARCH_WORKSPACE_ROOT`（默认 `/output`）、`RESEARCH_PAPERS_ROOT`（默认 `/data/papers`）、整题预算秒数、日志级别。

### 5.2 日志

结构化、脱敏。允许记录：错误类型、阶段耗时、`finish_reason`、usage 计数、材料/源码的 SHA-256 摘要、规则与派题版本号、`case_id`、`dispatch_id`。禁止记录：请求头原文、API key、全量环境变量、论文正文全文、模型响应全文（D 类源码摘要除外，只记哈希）。

### 5.3 `/health`

- 不触发任何付费模型请求或实验（总纲 §7.4）
- 进程存活且输出根可写 → `200`，body 形如 `{"status":"ok","workspace_writable":true,"gateway_configured":true,"dispatch_mounted":false,"rules_version":"research-assistant-objective-v3"}`
- 输出根不可写 → `503`
- **不因 Gateway 配置缺失返回 5xx**

理由：若平台实际注入方式与我们的探测规则不符，`/health` 返回 5xx 会让基础镜像的 healthcheck 连续失败、整轮评测起不来，代价远大于单题报配置错误。响应体字段属总纲 Q03【待确认】，联调时按平台要求调整；`200 + 小 JSON` 是满足 `curl -fsS` 的最小安全形状。

### 5.4 `/solve`

只解当前题，**不得在一次请求中遍历整轮 11 题**（总纲 §3.3）。流程：

```
POST /solve (async)
  → Pydantic 严格解析请求体为八字段对象
  → Task.parse()：身份 / 类型 / 业务语义三重校验（不触文件系统）
  → function ↔ output_schema_ref 一致性检查（不一致 → 422，不猜路由）
  → load_task_context()：第四重材料范围校验、team、授权材料、工作目录、Deadline、可选 dispatch
  → telemetry.session(case_id, function, ...) 包裹
  → anyio.to_thread(solve, task, context)
      → registry 取 handler；未实现的类型抛 HandlerNotImplemented
      → handler 同步执行（模型调用 / 文件读取）
      → results 构造器强制字段白名单
  → persist_and_verify()：原子落盘 + 回读
  → 返回与落盘字节一致的同一 JSON 对象
```

响应必须是**直接 JSON 对象**，`Content-Type: application/json`，无代码围栏、无自然语言前缀、无额外顶层字段。

### 5.5 错误处理与 HTTP 映射

A/C/E 失败**绝不返回伪装成功的空壳报告**（总纲 §7.4）。异常层级集中在 `errors.py`：

| 异常 | HTTP | 场景 |
|---|---|---|
| `TaskContractError` | 422 | 八字段缺失/类型错/编号非法/`function` 与 `output_schema_ref` 不一致 |
| `DispatchValidationError` | 422 | 派题记录版本、配额、题号唯一性、题目哈希不符 |
| `PathAuthorizationError` | 403 | 绝对提交路径、`..`、符号链接越界、他队目录、未授权材料 |
| `ConfigError` | 500 | Gateway 变量组缺失或冲突；只报类型与组名，不报值 |
| `BudgetExhausted` | 504 | 预算不足或阶段超时 |
| `GatewayError` | 502 | 网络失败、非 2xx、响应不可解析、`finish_reason != "stop"` |
| `PersistenceError` | 500 | 写入失败、回读不一致、权限不足 |
| `HandlerNotImplemented` | 501 | B/C/D/E 在 P1 尚未实现 |

错误响应体统一为 `{"error": {"type": "<异常类名>", "message": "<脱敏说明>", "case_id": "<原样或 null>"}}`。这个形状**只用于非 2xx 响应**，与五类答案 JSON 的字段白名单互不相干，不得混入 200 响应。

D 类的 `failed` / `materials_missing` 是 **200 + 合法答卷**（三项完整指标、`observed` 全 `null`、无真实图时 `figures: []`），走的是正常返回通道而非错误通道。P1 只在 `results.py` 定义该形状并写单元测试，不实现 D 的编排。

平台侧规则：非法答案保留 invalid 计 0，失败/超时/判题失败也计 0，且不缩小该类分母。因此「诚实报错」与「返回空壳」在自动分上等价，但前者保留了可诊断证据——这是选择报错而非降级的依据。

### 5.6 Telemetry 适配

```python
# runtime/telemetry.py
def install_interceptors() -> bool:
    """try import agenthack.interceptor.install_all_interceptors；成功返回 True。"""

@contextmanager
def trace_session(case_id: str, function: str, headers: Mapping[str, str]) -> Iterator[None]:
    """有 SDK 时进入 Telemetry.session(...)；无 SDK 时为 no-op，只记一条 warning。"""
```

- 本地开发机没有 `agenthack` 包 → 走 no-op 分支，测试必须覆盖该分支
- 镜像内有 SDK → interceptor 自动抓 `httpx` 调用的 token / `finish_reason` / 耗时
- `AGENTHACK_FLUSH_ON_SOLVE` 由 SDK 自行处理；我们不依赖 `AgentRuntime`，因此需在 `/solve` 结束时自行触发一次 flush（若 SDK 可用）
- telemetry 失败**绝不影响**答案返回：整个适配层用 try/except 包住，异常只记日志

## 6. A 类（Chat）实现

P1 唯一实现的业务 handler。产出 `{case_id, answer, clarifying_question}`，两项均非空字符串且**必须与题意相关**——官方自动项为 `answer` 3 分、`clarifying_question` 2 分，但用无关非空字符串应付会丢内容分（总纲 §5.2）。

实现方式：一次 Gateway 调用，system 提示词固定要求「直接回答研究问题，明确变量、可能关系与证据限制；再提出一个最有价值的研究范围限定问题；输出严格 JSON，无代码围栏」。程序侧解析该 JSON，解析失败时按 `GatewayError` 处理而不是把原始文本塞进 `answer`。

模型只产内容：`case_id` 由程序从 `Task` 注入，模型返回的 `case_id` 或任何未知字段一律丢弃，不透传（总纲 §3.2）。

`materials` 为空的 A 题不读任何文件；若某道 A 题带了材料，P1 只校验路径授权，不解析内容（P2 能力）。

## 7. 测试策略

先写能暴露问题的合同/异常测试，再实现关键行为；不写只重复实现细节的测试（总纲 §8.5）。

| 目录 | 内容 | 对应验收 |
|---|---|---|
| `tests/unit/` | Task 三重校验与 `unknown_fields`；`load_task_context` 的材料范围校验；dispatch v3 校验（11 唯一 ID、字典配额、策略/题目哈希、错 team、重复 ID、缺题、错哈希被拒）；路径（中文/空格/`+`/`..`/绝对路径/symlink 越界/他队目录）；Deadline 领取与耗尽；settings 整组探测与冲突；五类结果构造器的字段白名单与 800 字符/三项指标约束 | G01 G03 R01 |
| `tests/contract/` | 五类 `function` 正确路由；响应顶层键恰好等于允许集合；无代码围栏无自然语言前缀；`case_id` 原样返回；错误映射表逐项覆盖；`/health` 形状与不触发模型请求 | G02 |
| `tests/integration/` | 临时工作区 + FakeGateway，A 类端到端；断言落盘 JSON 与 HTTP 响应逐字节相同；回读校验失败时不报成功；权限失败可见 | G05 A01 |
| `tests/live/` | 真实网关 A 类；`@pytest.mark.live`，`register_markers` 注册；无凭据或无网络时 `skip`，不计入默认测试run | A01（真实模型） |
| `tests/fixtures/` | 自建 v3 dispatch（11 题、配额 1/3/2/3/2、含 `A-100` 三位后缀样例）+ 五类八字段题目样例 + 假论文文件名清单；每个文件头部注释明确标注「自建测试材料，非官方题目，不含正式答案」 | — |

无私有金标时只检查方法与不变量，不把推测输出写成官方答案。B 类冻结集合、C/E 冻结事实、D 参考实现均不提供给 Agent，因此**本地测试不得声称算出了官方分数**。

## 8. 验收映射与证据级别

| 验收 ID | P1 目标 | 本轮证据级别 |
|---|---|---|
| G01 v3 dispatch | 11 唯一 ID、字典配额、策略/题目哈希核验；错 team、重复 ID、缺题、错哈希被拒 | 本地测试 |
| G02 单题 HTTP | 五类正确路由；`case_id` 原样；不遍历整轮；无围栏无额外键 | 本地测试 |
| G03 授权路径 | 中文/空格/`+` 正常；绝对路径、`..`、symlink 越界、他队报告被拒 | 本地测试 |
| G05 落盘一致性 | JSON 可回读、HTTP 对象一致；权限失败不报成功 | 本地测试（C/E 逐字一致与图存在留 P3/P4） |
| A01 Chat | 回答与追问均非空且相关 | 本地测试（FakeGateway）+ live 测试（真实网关，需联网） |
| R01 截止与取消 | 模型慢/超时、重试限额、剩余预算不足；保留已完成产物 | 本地测试（子进程挂起留 P4） |
| R02 镜像交付 | — | **未执行**（本轮不建 Dockerfile、不 load 镜像） |
| R03 平台联调 | — | **未执行**（无正式会话与评测证据） |

G04（文档解析）、B01、C01/C02、E01、D01–D05 属 P2–P5，本轮**未执行**，不得在进度记录中标为通过。

## 9. 未决项

| ID | 未决项 | 是否阻塞 P1 |
|---|---|---|
| Q01 | 正式环境的变量族、会话头要求、模型名、Token/上下文限额。开发网关已实测为 OpenAI 兼容且不需要 `X-Session-ID`，但不得外推 | 否；整组探测已覆盖两种写法 |
| Q03 | `/health` 响应体字段、非 D 失败的精确 HTTP 状态、重复请求与并发行为、A/B/C/E 精确限时 | 否；P1 采用 §5.3/§5.5 的明确形状，联调时按平台要求调整 |
| Q02 | 外部平台是否挂载 dispatch、真实论文挂载路径、输出目录 UID 权限 | 否；dispatch 缺失已设计为可运行 |
| Q07 | `timeslice_budget=300` 的单位与作用 | 否；P1 不使用该值 |
| 新增 N01 | 镜像无任何 PDF 解析库、无 Pillow、无 OCR。P2 必须决定：构建时 pip 安装（需构建机联网，依赖打进镜像）还是纯标准库自写解析器 | 否；阻塞 P2 |
| 新增 N02 | SDK telemetry 的 OTEL 端点由谁注入、平台是否真的消费这些 trace、是否影响 30% 复核 | 否；已做可降级设计 |

## 10. 完成定义

P1 标记完成需同时满足：

1. `uv run pytest` 全绿（`live` 标记默认排除）
2. `uv run ruff check` 与 `uv run ruff format --check` 无告警
3. 本地 `uvicorn` 起服务后，`GET /health` 返回 200、`POST /solve` 对 A 类返回合规 JSON 且与落盘文件逐字节一致
4. `docs/开发进度与接力记录.md` 追加本轮交接：真实命令、退出状态、关键输出、证据路径、未执行的检查
5. R02/R03 及 G04/B01/C01/C02/E01/D01–D05 明确标为「未执行」，不得用「基本完成」掩盖

本地测试通过 ≠ 镜像验证通过 ≠ 平台验证通过，三者不得互相替代。
