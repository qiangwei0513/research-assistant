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
