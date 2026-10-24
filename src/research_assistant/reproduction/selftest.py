"""生成源码的隔离自测。

为什么必须有这一步：官方 `model_source.py` 的 AST 检查**只**确认「Python 3.9 可解析」
与「存在顶层 run_experiment」，不查名字解析、标准库限制或语义正确性（总纲 §6.2 明确写了）。
实测中模型生成过 `re.findall(...)` 却没有 `import re` 的源码——它通过来源准入，
但一运行就 NameError。而 helper 一旦执行就会锁源，锁源后不得更换源码，
于是整道 40 分的题直接归零。因此必须在调用正式 helper **之前**自测。

线上没有 Docker socket（总纲 Q05 未确认），所以自测不用容器，改用**受限子进程**：
独立解释器进程、无 shell、CPU 与地址空间 rlimit、墙钟超时、工作目录限定在本队 work 子目录。
这不是真沙箱，但容器本身就是平台给的隔离边界，且此时还没有任何结果落盘，
失败的代价只是重生成一次，而不是污染答卷。

自测只调用入口函数，不复制也不改写官方 worker/experiment/artifacts 代码。
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from research_assistant.reproduction.invariants import check_invariants
from research_assistant.reproduction.materials import CaseMaterials
from research_assistant.runtime.logging import BoundLogger

DEFAULT_SELF_TEST_TIMEOUT = 20.0
DEFAULT_MEMORY_LIMIT_BYTES = 1024 * 1024 * 1024
DEFAULT_CPU_LIMIT_SECONDS = 20
PUBLIC_REQUEST_DIRNAME = "公开样例请求"

_RUNNER_TEMPLATE = """\
import importlib.util
import json
import os
import resource
import sys

# 受限子进程：CPU 与地址空间 rlimit，工作目录锁定在本队 work 子目录。
# 这不是真沙箱，但容器本身就是平台给的隔离边界，且此时尚无任何结果落盘。
for _name, _limit in (("RLIMIT_CPU", {cpu}), ("RLIMIT_NPROC", 64)):
    _res = getattr(resource, _name, None)
    if _res is not None:
        try:
            resource.setrlimit(_res, (_limit, _limit))
        except (ValueError, OSError):
            pass
try:
    resource.setrlimit(resource.RLIMIT_AS, ({mem}, {mem}))
except (ValueError, OSError):
    pass
os.chdir({workdir!r})

_spec = importlib.util.spec_from_file_location("implementation", sys.argv[1])
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
with open(sys.argv[2], encoding="utf-8") as _handle:
    _payload = json.load(_handle)
_result = _module.run_experiment(_payload)
# 结果写文件而不是打印到 stdout：
# 1) 不得截断——D-02 的 run 阶段要返回 27 行搜索记录加全部查询排名，远超任何固定上限；
# 2) 生成的源码若自己 print 了调试信息，靠「stdout 最后一行是 JSON」会取错行。
with open(sys.argv[3], "w", encoding="utf-8") as _out:
    json.dump(_result, _out, ensure_ascii=False, allow_nan=False)
"""


@dataclass(frozen=True)
class SelfTestCase:
    name: str
    payload: Mapping[str, object]
    origin: str


@dataclass(frozen=True)
class SelfTestResult:
    ok: bool
    case_count: int
    passed: int
    error: str | None
    output_keys: tuple[str, ...]
    duration_seconds: float


def _synthesise_from_sample(materials: CaseMaterials) -> SelfTestCase | None:
    """sample.json 是 {case_id,phase,data}，不是完整 worker 请求；补上 protocol 即可直接调用。"""
    if materials.sample is None:
        return None
    payload: dict[str, object] = dict(materials.sample)
    if materials.protocol is not None:
        payload["protocol"] = dict(materials.protocol)
    return SelfTestCase(
        name=f"{materials.case_id}-sample",
        payload=payload,
        origin="sample.json + protocol.json",
    )


def _public_requests(experiment_root: Path, case_id: str) -> list[SelfTestCase]:
    """公开样例请求是完整的 {stage,payload} worker 请求，取其中的 payload。"""
    root = experiment_root / PUBLIC_REQUEST_DIRNAME
    files: list[Path] = []
    single = root / f"{case_id}.json"
    if single.is_file():
        files.append(single)
    extra = root / case_id
    if extra.is_dir():
        files.extend(sorted(extra.glob("*.json")))

    cases: list[SelfTestCase] = []
    for path in files:
        try:
            message = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(message, Mapping):
            continue
        payload = message.get("payload")
        if isinstance(payload, Mapping):
            cases.append(
                SelfTestCase(name=path.stem, payload=payload, origin=f"公开样例请求/{path.name}")
            )
    return cases


def build_self_test_cases(
    materials: CaseMaterials, experiment_root: Path
) -> tuple[SelfTestCase, ...]:
    """优先用官方公开样例请求（含边界用例）；没有就用 sample.json 合成一个。"""
    cases = _public_requests(experiment_root, materials.case_id)
    if cases:
        return tuple(cases)
    synthesised = _synthesise_from_sample(materials)
    return (synthesised,) if synthesised is not None else ()


def run_self_test(
    implementation: Path,
    cases: Sequence[SelfTestCase],
    *,
    work_dir: Path,
    timeout: float = DEFAULT_SELF_TEST_TIMEOUT,
    python: str | None = None,
    log: BoundLogger | None = None,
) -> SelfTestResult:
    """在受限子进程里逐个跑自测用例，并比对算法不变量。

    任一用例运行失败、输出非法或违反不变量都算整体失败，并带回可诊断的错误摘要——
    该摘要会回传给同一会话模型用于完整重生成。
    """
    import time

    max_captured_output = 2000
    if not cases:
        return SelfTestResult(True, 0, 0, None, (), 0.0)

    work_dir.mkdir(parents=True, exist_ok=True)
    # runner 会 os.chdir 到 work_dir，因此传给子进程的路径必须都是绝对的，
    # 否则相对路径会在 chdir 之后失效
    implementation = Path(implementation).resolve()
    work_dir = Path(work_dir).resolve()
    runner = work_dir / "_selftest_runner.py"
    interpreter = python or sys.executable
    started = time.monotonic()
    passed = 0
    first_error: str | None = None
    keys: tuple[str, ...] = ()

    for case in cases:
        request_file = work_dir / f"_selftest_{case.name}.json"
        request_file.write_text(json.dumps(case.payload, ensure_ascii=False), encoding="utf-8")
        runner.write_text(
            _RUNNER_TEMPLATE.format(
                cpu=int(DEFAULT_CPU_LIMIT_SECONDS),
                mem=DEFAULT_MEMORY_LIMIT_BYTES,
                workdir=str(work_dir),
            ),
            encoding="utf-8",
        )
        result_file = work_dir / f"_selftest_{case.name}.out.json"
        result_file.unlink(missing_ok=True)
        # argv 列表调用，不经 shell；-I 隔离模式避免继承 PYTHONPATH 等外部影响
        command = [
            interpreter,
            "-B",
            "-I",
            str(runner),
            str(implementation),
            str(request_file),
            str(result_file),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=max(1.0, timeout),
                cwd=str(work_dir),
                check=False,
            )
        except subprocess.TimeoutExpired:
            first_error = first_error or f"{case.name}: 自测超时（>{timeout:.0f}s）"
            continue
        except OSError as exc:
            first_error = first_error or f"{case.name}: 无法启动自测子进程 {exc.__class__.__name__}"
            continue

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip().splitlines()
            tail = "; ".join(detail[-3:])[:max_captured_output]
            first_error = first_error or f"{case.name}: {tail}"
            continue

        try:
            parsed = json.loads(result_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            detail = (completed.stderr or completed.stdout).strip().splitlines()
            tail = "; ".join(detail[-3:])[:max_captured_output]
            first_error = first_error or (
                f"{case.name}: 自测未产出合法 JSON 结果" + (f"（{tail}）" if tail else "")
            )
            continue
        if not isinstance(parsed, Mapping):
            first_error = first_error or f"{case.name}: 自测输出不是 JSON 对象"
            continue
        if not keys:
            keys = tuple(sorted(parsed))
        # 结构合法不等于算法正确：再比对官方 method 的不变量。
        # 实测发现模型生成过「并列类别未取字典序最小」与「任务重复进入 trace/dropped」
        # 这类错误，两者都能通过来源准入与结构检查，只有不变量能拦住。
        violations = check_invariants(str(case.payload.get("case_id", "")), case.payload, parsed)
        if violations:
            first_error = first_error or f"{case.name}: " + "；".join(violations[:2])
            continue
        passed += 1

    duration = time.monotonic() - started
    result = SelfTestResult(
        ok=first_error is None and passed == len(cases),
        case_count=len(cases),
        passed=passed,
        error=first_error,
        output_keys=keys,
        duration_seconds=duration,
    )
    if log is not None:
        log.info(
            "reproduction.self_test",
            ok=result.ok,
            cases=result.case_count,
            passed=result.passed,
            duration_seconds=round(duration, 2),
            output_keys=list(result.output_keys),
            error=(result.error or "")[:300],
        )
    return result
