"""P1 终审回归测试。

终审用变异测试证明：以下四条安全/合同不变量即使被破坏，436 个测试仍全绿——
它们是代码注释明确声称的防御，却没有任何测试守着。本文件逐条钉住。

另含终审的 1 个 Critical 与 2 个 Important 的回归：
遥测 __exit__ 失败不得丢弃已落盘的正确答案；/health 探针并发不得误报 503；
超长 materials 条目必须落 422 而不是裸 OSError 逃成 500。
"""

from __future__ import annotations

import sys
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from research_assistant import solve as solve_module
from research_assistant.api import create_app
from research_assistant.contracts.task import Task
from research_assistant.runtime.gateway import FakeGateway
from research_assistant.runtime.paths import WorkspacePaths
from research_assistant.runtime.settings import Settings

TEAM = "测试队伍 A+1"


def answer_json() -> str:
    import json

    return json.dumps(
        {"answer": "光照强度影响光合速率。", "clarifying_question": "研究对象是哪种植物？"},
        ensure_ascii=False,
    )


def chat_payload() -> dict[str, Any]:
    return {
        "case_id": "A-01",
        "function": "Chat",
        "title": "光照与生长",
        "input": "解释关系并提一个限定问题。",
        "materials": [],
        "constraints": [],
        "expected_output": "解释关系并提问。",
        "output_schema_ref": "Chat",
    }


def build_client(base_env: dict[str, str], responses: list[Any]) -> TestClient:
    return TestClient(create_app(Settings.from_env(base_env), gateway=FakeGateway(responses)))


def install_fake_sdk(monkeypatch: pytest.MonkeyPatch, *, exit_raises: bool) -> dict[str, Any]:
    """装一个假 agenthack.telemetry，记录 session 参数，可让 __exit__ 抛异常。"""
    calls: dict[str, Any] = {"sessions": [], "entered": 0, "exited": 0}

    class Session:
        def __init__(self, *args: Any) -> None:
            calls["sessions"].append(args)

        def __enter__(self) -> Session:
            calls["entered"] += 1
            return self

        def __exit__(self, *exc: Any) -> bool:
            calls["exited"] += 1
            if exit_raises:
                raise RuntimeError("otel collector unreachable")
            return False

    class Telemetry:
        def session(self, *args: Any) -> Session:
            return Session(*args)

        def flush(self) -> None:
            pass

        def shutdown(self) -> None:
            pass

    module = types.ModuleType("agenthack.telemetry")
    module.Telemetry = Telemetry  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agenthack.telemetry", module)
    return calls


# --- Critical：遥测失败不得丢弃已算出且已落盘的正确答案 ---


def test_telemetry_exit_failure_does_not_drop_a_persisted_answer(
    base_env: dict[str, str], tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec §5.6：遥测失败绝不影响答案返回。

    答案在 trace_session 内部就已算出并落盘校验通过；若 SDK 的 __exit__ 抛异常
    逃出 with 块，会变成 HTTP 500，等于静默丢弃一个正确结果。
    """
    calls = install_fake_sdk(monkeypatch, exit_raises=True)
    with build_client(base_env, [answer_json()]) as client:
        response = client.post("/solve", json=chat_payload())

    assert calls["entered"] == 1, "假 SDK 未被进入，测试没有覆盖目标路径"
    assert response.status_code == 200, response.text
    written = (tmp_workspace / "data" / TEAM / "A-01.json").read_bytes()
    assert response.content == written
    assert response.json()["answer"] == "光照强度影响光合速率。"


def test_telemetry_enter_failure_still_returns_the_answer(
    base_env: dict[str, str], tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = install_fake_sdk(monkeypatch, exit_raises=False)

    class BrokenTelemetry:
        def session(self, *args: Any) -> Any:
            raise RuntimeError("cannot create span exporter")

        def flush(self) -> None:
            pass

        def shutdown(self) -> None:
            pass

    module = types.ModuleType("agenthack.telemetry")
    module.Telemetry = BrokenTelemetry  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agenthack.telemetry", module)

    with build_client(base_env, [answer_json()]) as client:
        response = client.post("/solve", json=chat_payload())
    assert response.status_code == 200, response.text
    assert calls["entered"] == 0


def test_business_exception_still_propagates_through_trace_session(
    base_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """遥测不得吞掉业务异常：网关失败仍须报 502。"""
    from research_assistant.errors import GatewayError

    install_fake_sdk(monkeypatch, exit_raises=True)
    with build_client(base_env, [GatewayError("upstream 502")]) as client:
        response = client.post("/solve", json=chat_payload())
    assert response.status_code == 502
    assert response.json()["error"]["type"] == "GatewayError"


# --- Important：/health 探针并发不得误报 503 ---


def test_concurrent_health_probes_all_return_200(base_env: dict[str, str]) -> None:
    """固定探针文件名会让并发探测在 unlink 时抛 FileNotFoundError 而误报 503。"""
    app = create_app(Settings.from_env(base_env), gateway=FakeGateway([]))
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(lambda _: client.get("/health").status_code, range(48)))
    assert set(statuses) == {200}, f"并发探测出现非 200：{sorted(set(statuses))}"


def test_health_leaves_no_probe_files(base_env: dict[str, str], tmp_workspace: Path) -> None:
    app = create_app(Settings.from_env(base_env), gateway=FakeGateway([]))
    with TestClient(app) as client:
        for _ in range(3):
            client.get("/health")
    leftovers = [p.name for p in tmp_workspace.iterdir() if p.name.startswith(".health-probe")]
    assert leftovers == []


# --- Important：超长 materials 必须落 422 而不是裸 OSError ---


def test_overlong_material_returns_422_not_500(base_env: dict[str, str]) -> None:
    """pathlib 只对 ENOENT/ENOTDIR/EBADF/ELOOP 静默，ENAMETOOLONG 会抛 OSError。"""
    with build_client(base_env, [answer_json()]) as client:
        response = client.post("/solve", json=chat_payload() | {"materials": ["x" * 5000]})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["type"] == "MaterialUnavailableError"


def test_overlong_material_error_message_is_truncated(base_env: dict[str, str]) -> None:
    with build_client(base_env, [answer_json()]) as client:
        response = client.post("/solve", json=chat_payload() | {"materials": ["y" * 5000]})
    body = response.json()["error"]["message"]
    assert len(body) < 200
    assert "y" * 200 not in body


def test_deeply_nested_material_returns_422_not_500(base_env: dict[str, str]) -> None:
    with build_client(base_env, [answer_json()]) as client:
        response = client.post("/solve", json=chat_payload() | {"materials": ["a/" * 4000]})
    assert response.status_code in (403, 422), response.text


# --- Important：四条被变异测试证明零覆盖的安全/合同不变量 ---


def test_unexpected_error_body_does_not_leak_the_exception_message(
    base_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """未预期异常只回类名，不回消息——消息可能含路径、负载片段等内部细节。"""

    def exploding(task: Task, context: Any) -> dict[str, Any]:
        raise RuntimeError("secret internal path /output/data/别的队/A-01.json")

    monkeypatch.setattr(
        solve_module,
        "HANDLER_BY_FUNCTION",
        {Task.parse(chat_payload()).function: exploding},
    )
    with build_client(base_env, [answer_json()]) as client:
        response = client.post("/solve", json=chat_payload())
    assert response.status_code == 500
    body = response.json()["error"]
    assert body["type"] == "UnexpectedError"
    assert body["message"] == "RuntimeError"
    assert "secret internal path" not in response.text
    assert "别的队" not in response.text


def test_only_session_and_trace_context_headers_reach_telemetry(
    base_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """平台可能带 Authorization/Cookie；进 OTEL 的只允许会话标识与 W3C trace context。"""
    calls = install_fake_sdk(monkeypatch, exit_raises=False)
    traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    with build_client(base_env, [answer_json()]) as client:
        client.post(
            "/solve",
            json=chat_payload(),
            headers={
                "Authorization": "Bearer sk-super-secret",
                "Cookie": "session=secret-cookie",
                "X-Session-ID": "sess-9",
                "traceparent": traceparent,
            },
        )
    assert calls["entered"] == 1
    args = calls["sessions"][0]
    assert args[2] == "sess-9"
    assert args[4] == {"traceparent": traceparent}
    assert "authorization" not in args[4]
    assert "cookie" not in args[4]
    assert "sk-super-secret" not in str(calls["sessions"])


def test_prefix_sibling_directory_is_not_treated_as_the_expert_dir(
    tmp_workspace: Path, papers_root: Path
) -> None:
    """`data/expert-backup/<本队命名>.md` 不得被当成本队报告。

    字符串前缀匹配会放行它；只有解析后的祖先关系判断才能挡住。
    data/expert 是所有队伍共享且可写的，这条是 P3 的写入门槛。
    """
    ws = WorkspacePaths(root=tmp_workspace, team=TEAM, papers_root=papers_root)
    sibling = tmp_workspace.parent / "output-evil"
    (sibling / "data" / "expert").mkdir(parents=True)
    decoy = sibling / "data" / "expert" / f"C-01-综述-{TEAM}.md"
    decoy.write_text("x", encoding="utf-8")

    from research_assistant.errors import PathAuthorizationError

    with pytest.raises(PathAuthorizationError):
        ws.relative_to_output(decoy)

    backup = tmp_workspace / "data" / "expert-backup"
    backup.mkdir(parents=True)
    planted = backup / f"C-01-综述-{TEAM}.md"
    planted.write_text("x", encoding="utf-8")
    with pytest.raises(PathAuthorizationError):
        ws.assert_own_expert_file(planted)


def test_own_expert_file_is_still_accepted(tmp_workspace: Path, papers_root: Path) -> None:
    """上一条的对照：不能把合法报告也一起挡掉。"""
    ws = WorkspacePaths(root=tmp_workspace, team=TEAM, papers_root=papers_root)
    own = ws.survey_md("C-01")
    own.write_text("x", encoding="utf-8")
    assert ws.assert_own_expert_file(own) == own.resolve()


def test_no_shell_interpolation_anywhere_in_the_source() -> None:
    """真正的不变量是「源码里没有任何 shell 调用」。

    `~`、`$()`、反引号、`|`、`*` 只在 shell 中才有特殊含义；只要从不拼 shell 命令，
    它们就只是字面文件名。所以守这条比逐个拦字符更根本。
    """
    src_root = Path(__file__).resolve().parents[2] / "src"
    # 不禁 subprocess 本身：P4 必须以 argv 列表方式调用平台挂载的 run_experiment.py。
    # 禁的是「经由 shell 解释文件名」——shell=True 与 os.system/os.popen 才是漏洞面。
    forbidden = ("os.system", "os.popen", "shell=True", "popen(")
    offenders: list[str] = []
    for path in sorted(src_root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                offenders.append(f"{path.relative_to(src_root)}: {token}")
    assert offenders == []


def test_shell_metacharacters_stay_inside_the_authorized_root(
    tmp_workspace: Path, papers_root: Path
) -> None:
    """含 shell 元字符的材料名不得逃出授权根，只能落成本目录内的字面路径。"""
    from research_assistant.errors import PathAuthorizationError

    ws = WorkspacePaths(root=tmp_workspace, team=TEAM, papers_root=papers_root)
    papers_resolved = papers_root.resolve()
    for name in ["~root/.ssh/id_rsa", "*/x.pdf", "$(whoami).pdf", "`id`.pdf", "a|b.pdf"]:
        try:
            resolved = ws.paper(name)
        except PathAuthorizationError:
            # 嵌套形式会被 _check_segment 的字符白名单直接挡掉，这是更强的结果
            continue
        # 平铺形式只是字面文件名：必须仍被关在授权根内，且不会执行任何东西
        assert papers_resolved in resolved.parents, name
        assert not resolved.exists()


def test_traversal_and_absolute_forms_are_still_rejected(
    tmp_workspace: Path, papers_root: Path
) -> None:
    """上一条的对照：真正能越界的形式必须被挡。"""
    from research_assistant.errors import PathAuthorizationError

    ws = WorkspacePaths(root=tmp_workspace, team=TEAM, papers_root=papers_root)
    for hostile in ["../secret.pdf", "/etc/passwd", "sub/../../secret.pdf", "a\\..\\b.pdf"]:
        with pytest.raises(PathAuthorizationError):
            ws.paper(hostile)


def test_quota_v3_is_immutable() -> None:
    """1/3/2/3/2 是评分关键常量；全局约束禁止模块级可变状态。"""
    from research_assistant.contracts.dispatch import QUOTA_V3

    assert dict(QUOTA_V3) == {"A": 1, "B": 3, "C": 2, "D": 3, "E": 2}
    assert not isinstance(QUOTA_V3, dict)
    with pytest.raises(TypeError):
        QUOTA_V3["A"] = 99  # type: ignore[index]
