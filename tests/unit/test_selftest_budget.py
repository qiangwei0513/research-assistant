from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from research_assistant.reproduction.selftest import SelfTestCase, run_self_test


def test_selftest_timeout_is_shared_by_whole_batch(tmp_path, monkeypatch):
    clock = [100.0]
    calls = []
    monkeypatch.setattr("time.monotonic", lambda: clock[0])

    def execute(command, *, timeout, **kwargs):
        calls.append(timeout)
        clock[0] += min(0.6, timeout)
        return subprocess.CompletedProcess(command, 1, "", "failed")

    monkeypatch.setattr("research_assistant.reproduction.selftest._run_process", execute)
    cases = tuple(SelfTestCase(str(i), {}, "test") for i in range(4))
    result = run_self_test(Path("unused.py"), cases, work_dir=tmp_path, timeout=1.0)
    assert calls == pytest.approx([1.0, 0.4])
    assert result.duration_seconds <= 1.0
    assert not result.ok


def test_zero_selftest_budget_never_starts_child(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("must not start after budget is exhausted")

    monkeypatch.setattr("research_assistant.reproduction.selftest._run_process", forbidden)
    result = run_self_test(
        Path("unused.py"), [SelfTestCase("x", {}, "test")], work_dir=tmp_path, timeout=0
    )
    assert not result.ok


def test_timeout_terminates_descendants_too(tmp_path):
    import sys
    import time

    from research_assistant.reproduction.selftest import _run_process

    marker = tmp_path / "escaped-child"
    ready = tmp_path / "child-started"
    child = f"import pathlib,time; pathlib.Path({str(ready)!r}).touch(); time.sleep(1); pathlib.Path({str(marker)!r}).touch()"
    parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(5)"
    with pytest.raises(subprocess.TimeoutExpired):
        _run_process([sys.executable, "-c", parent], cwd=str(tmp_path), timeout=0.3)
    assert ready.exists(), "test must actually start a descendant before cancellation"
    time.sleep(1.1)
    assert not marker.exists()
