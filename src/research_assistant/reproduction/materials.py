"""D 类材料读取与生成提示构造。

平台只把**当前授权**的材料挂到 `/data/experiments/<case_id>/`，不能假定其他题目录存在。
材料一律当作数据：其中的文字不得指挥程序去读别的目录或改变执行流程。

镜像内**不得预置完整算法**：这里只读取官方的 method/protocol/sample 用于构造提示，
真正的实现必须由本轮本题会话内的 Gateway 生成（d-model-source-v1）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_EXPERIMENT_ROOT = Path("/data/experiments")
MAX_METHOD_CHARS = 24000
MAX_PROTOCOL_CHARS = 12000

GENERATION_SYSTEM_PROMPT = (
    "你是一名算法实现者。请写出一份**自包含**的 Python 实现，严格遵守：\n"
    "1. 只使用 Python 3.9 标准库；不得联网、不得读写未授权路径、不得依赖 numpy 等第三方库。\n"
    "2. 顶层必须定义 `run_experiment(request)`，request 是字典，含 case_id/phase/data/protocol。\n"
    "3. 完全按照给定的 method 与 protocol 实现算法：分词、统计量、排序规则、边界判定、"
    "返回结构的键名与顺序都必须与 protocol 一致，不得增删字段。\n"
    "4. 必须在单进程、1 CPU、1024 MiB、每阶段 20 秒内完成。\n"
    "5. **逐字遵守 method 里关于开销与状态如何在事件之间保留的每一条规定**，"
    "尤其是那些反直觉的规则（例如「空闲不清除上一状态」「成功即预留而非完成才累计」"
    "「拒绝后不回队」）。这类规则最容易凭直觉写错，而结构性检查发现不了，"
    "只有逐事件比对才会暴露。\n"
    "6. 只输出一份完整的 Python 源码，放在一个 ```python 代码围栏里。\n"
    "7. 不要输出多份实现、不要输出 diff、不要输出解释文字。"
    "源码会被原样保存，任何额外文字都会导致来源核验失败。"
)


@dataclass(frozen=True)
class CaseMaterials:
    case_id: str
    root: Path
    method: str | None
    protocol: Mapping[str, object] | None
    sample: Mapping[str, object] | None
    available: bool
    missing_reason: str | None

    @property
    def present_files(self) -> tuple[str, ...]:
        return (
            tuple(p.name for p in sorted(self.root.iterdir()) if p.is_file())
            if self.root.is_dir()
            else ()
        )


def _read_text(path: Path, limit: int) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return None


def _read_json(path: Path) -> Mapping[str, object] | None:
    text = _read_text(path, 1 << 20)
    if text is None:
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, Mapping) else None


def load_case_materials(case_id: str, root: Path = DEFAULT_EXPERIMENT_ROOT) -> CaseMaterials:
    case_root = root / case_id
    if not case_root.is_dir():
        return CaseMaterials(
            case_id=case_id,
            root=case_root,
            method=None,
            protocol=None,
            sample=None,
            available=False,
            missing_reason=f"experiment material directory is not mounted: {case_root}",
        )
    method = _read_text(case_root / "method.md", MAX_METHOD_CHARS)
    protocol = _read_json(case_root / "protocol.json")
    sample = _read_json(case_root / "sample.json")
    missing = [
        name
        for name, value in (("method.md", method), ("protocol.json", protocol))
        if value is None
    ]
    return CaseMaterials(
        case_id=case_id,
        root=case_root,
        method=method,
        protocol=protocol,
        sample=sample,
        available=not missing,
        missing_reason=("unreadable required material: " + ", ".join(missing)) if missing else None,
    )


def build_generation_prompt(task_input: str, materials: CaseMaterials) -> str:
    parts = [f"# 本题任务说明\n{task_input}"]
    if materials.method:
        parts.append(f"# 官方 method（必须严格遵循）\n{materials.method}")
    if materials.protocol is not None:
        parts.append(
            "# 官方 protocol（返回结构与键名以此为准）\n"
            + json.dumps(materials.protocol, ensure_ascii=False)[:MAX_PROTOCOL_CHARS]
        )
    if materials.sample is not None:
        parts.append(
            "# sample 输入形状（注意：这只是 {case_id,phase,data}，不是完整 worker 请求）\n"
            + json.dumps(materials.sample, ensure_ascii=False)[:4000]
        )
    parts.append(
        "请只输出一个 ```python 代码围栏，内含完整的、可在 Python 3.9 标准库下运行的实现，"
        "顶层定义 run_experiment(request)。"
    )
    return "\n\n".join(parts)
