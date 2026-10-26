"""平台挂载的实验客户端适配。

正式调用只有一个入口：平台挂载的 `run_experiment.py`。
Agent 不自制凭证、不绕过客户端、不调用管理员接口、不直接连 Unix socket。

helper 的真实行为（已读官方源码核对）：
- 需要 `RESEARCH_EXPERIMENT_CASE/URL/TOKEN`、`RESEARCH_LAYERED_D`、`RESEARCH_D_SOURCE_PROVENANCE`、
  `RESEARCH_WORKSPACE_TEAM`，全部由平台注入，我们不构造也不伪造
- 内部先 POST `/source` 取来源凭证，再 POST `/run`
- **自己**把 submission 写到 `<RESEARCH_RESULT_ROOT|/output>/data/<team>/<case_id>.json`（indent=2）
  并把图片从 `/data/experiment-results` 拷到 `data/figure/<team>/`
- stdout 打印的就是最终 submission 对象

因此：helper 写下的文件是权威产物，外层不得用占位失败结果覆盖它；
stdout 解析失败时必须先保留 stdout/stderr 与已落盘产物，再据实核对。
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from research_assistant.runtime.logging import BoundLogger

DEFAULT_HELPER_PATH = Path("/data/experiments/run_experiment.py")
# helper 内部 RPC 上限是 600 秒，但整题只有 180 秒；实际超时一律由 Deadline 决定，
# 这里只是没有预算信息时的最后防线，绝不能被当成整题额度。
HARD_TIMEOUT_CEILING_SECONDS = 170.0


@dataclass(frozen=True)
class ExperimentOutcome:
    returncode: int
    stdout: str
    stderr: str
    submission: Mapping[str, Any] | None
    result_file_bytes: bytes | None
    result_file_path: Path | None
    timed_out: bool = False

    @property
    def produced_result_file(self) -> bool:
        return self.result_file_bytes is not None

    @property
    def authoritative_submission(self) -> Mapping[str, Any] | None:
        """优先用 helper 自己落盘的结果，其次才是 stdout。"""
        if self.result_file_bytes is not None:
            try:
                parsed = json.loads(self.result_file_bytes.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                parsed = None
            if isinstance(parsed, Mapping):
                return parsed
        return self.submission


class ExperimentClient(Protocol):
    def run(
        self, case_id: str, implementation_path: Path, *, timeout: float
    ) -> ExperimentOutcome: ...


def archive_previous_result(result_path: Path, case_id: str) -> None:
    """保护上一轮原始答卷；当前任务可能在生成/材料阶段就失败。"""
    if result_path.exists():
        history = result_path.parent / "work" / case_id / "previous-results"
        history.mkdir(parents=True, exist_ok=True)
        result_path.replace(history / f"{uuid4().hex}.json")


class SubprocessExperimentClient:
    """以 argv 列表方式调用平台 helper。绝不经 shell，绝不拼接命令字符串。"""

    def __init__(
        self,
        helper_path: Path = DEFAULT_HELPER_PATH,
        *,
        python: str | None = None,
        result_root: Path | None = None,
        team: str = "",
        log: BoundLogger | None = None,
    ) -> None:
        self.helper_path = Path(helper_path)
        self.python = python or sys.executable
        self.result_root = Path(result_root) if result_root else Path("/output")
        self.team = team
        self.log = log

    def _expected_result_file(self, case_id: str) -> Path:
        return self.result_root / "data" / self.team / f"{case_id}.json"

    def run(self, case_id: str, implementation_path: Path, *, timeout: float) -> ExperimentOutcome:
        if not self.helper_path.is_file():
            raise FileNotFoundError(f"platform experiment helper not mounted: {self.helper_path}")
        command = [
            self.python,
            str(self.helper_path),
            "--case",
            case_id,
            "--implementation",
            str(Path(implementation_path).resolve()),
        ]
        result_path = self._expected_result_file(case_id)
        # 旧答卷保留作诊断，但必须先移出正式输出路径；本次 helper 失败时不能冒认旧结果。
        archive_previous_result(result_path, case_id)
        bounded = max(1.0, min(timeout, HARD_TIMEOUT_CEILING_SECONDS))
        timed_out = False
        try:
            # argv 列表调用，不经 shell，因此不存在命令注入面
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=bounded,
                check=False,
            )
            returncode, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            returncode = -1
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = (exc.stderr if isinstance(exc.stderr, str) else "") + "\n[timeout]"

        # 先把 helper 已落盘的产物读出来：即使 stdout 解析失败也不得丢失真实结果
        result_bytes: bytes | None = None
        if result_path.is_file():
            try:
                result_bytes = result_path.read_bytes()
            except OSError:
                result_bytes = None

        submission: Mapping[str, Any] | None = None
        for chunk in _json_objects(stdout):
            if isinstance(chunk, Mapping) and "status" in chunk and "metrics" in chunk:
                submission = chunk
                break

        if self.log is not None:
            self.log.info(
                "experiment.finished",
                case_id=case_id,
                returncode=returncode,
                timed_out=timed_out,
                stdout_chars=len(stdout),
                stderr_chars=len(stderr),
                result_file_present=result_bytes is not None,
                submission_parsed=submission is not None,
            )
        return ExperimentOutcome(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            submission=submission,
            result_file_bytes=result_bytes,
            result_file_path=result_path if result_bytes is not None else None,
            timed_out=timed_out,
        )


def _json_objects(text: str) -> list[Any]:
    """从 stdout 里取出可解析的 JSON。

    CLI stdout 已是最终 submission 对象，因此**不能**再盲目取 result["submission"]。
    容忍前后有日志行：逐行尝试，最后再整体尝试一次。
    """
    out: list[Any] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            out.append(json.loads(stripped))
        except ValueError:
            continue
    with contextlib.suppress(ValueError):
        out.append(json.loads(text))
    return out


class FakeExperimentClient:
    """测试替身：按脚本返回，可模拟超时、非零退出、只落盘不打印等真实故障形态。"""

    def __init__(self, outcomes: list[ExperimentOutcome | Exception]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[tuple[str, Path, float]] = []

    def run(self, case_id: str, implementation_path: Path, *, timeout: float) -> ExperimentOutcome:
        self.calls.append((case_id, Path(implementation_path), timeout))
        if not self._outcomes:
            raise AssertionError(f"no scripted outcome left for {case_id}")
        item = self._outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
