from __future__ import annotations

import io
import json
from dataclasses import asdict

import pytest

from research_assistant.runtime.logging import JsonLogger, redact
from research_assistant.runtime.settings import GatewaySettings


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
    [
        "api_key",
        "apikey",
        "authorization",
        "password",
        "secret",
        "token",
        "CHALLENGE_LLM_API_KEY",
        "PLATFORM_API_KEY",
        "X-Session-ID",
        "headers",
    ],
)
def test_sensitive_keys_are_redacted(key: str) -> None:
    assert redact({key: "sk-live-secret"})[key] == "***"


@pytest.mark.parametrize(
    "key",
    ["access_token", "refresh_token", "id_token", "bearer_token", "x-api-key", "llm_api_key"],
)
def test_credential_names_merely_containing_token_are_still_redacted(key: str) -> None:
    """白名单只放行「令牌计数」，凡含 token 的凭据形字段名仍必须脱敏。"""
    assert redact({key: "sk-live-secret"})[key] == "***"


@pytest.mark.parametrize("key", ["my_prompt_tokens", "prompt_tokens_raw", "tokens", "token"])
def test_usage_count_allowlist_is_exact_match_not_substring(key: str) -> None:
    """白名单是精确匹配：allowlist 名字的超串/变体仍走子串脱敏——失效关闭方向正确。"""
    assert redact({key: "sk-live-secret"})[key] == "***"


@pytest.mark.parametrize(
    "key",
    [
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cached_tokens",
        "reasoning_tokens",
        "max_tokens",
        "token_count",
        "total_input_tokens",
        "total_output_tokens",
    ],
)
def test_usage_count_keys_pass_through_with_real_values(key: str) -> None:
    """usage 计数是「令牌计数」而非「令牌值」，属模块文档字符串明确允许记录项。"""
    assert redact({key: 9})[key] == 9


@pytest.mark.parametrize(
    "key",
    ["Prompt-Tokens", "PROMPT_TOKENS", "prompt-tokens", "Total_Tokens", " Completion-TOKENS "],
)
def test_usage_count_allowlist_ignores_case_and_separators(key: str) -> None:
    """白名单两侧用同一套归一化（小写 + 去 `_`/`-`），故大小写与分隔符变体同样放行。"""
    assert redact({key: 7})[key] == 7


def test_redaction_is_recursive() -> None:
    out = redact({"nested": {"api_key": "sk-x", "keep": 1}})
    assert out["nested"] == {"api_key": "***", "keep": 1}


@pytest.mark.parametrize("key", ["session_id", "SESSION_ID", "Session-Id", "challenge_session_id"])
def test_session_identifier_is_redacted(key: str) -> None:
    """dataclasses.asdict(GatewaySettings) 产出的键名正是 session_id，必须挡住。"""
    assert redact({key: "sess-42"})[key] == "***"


def test_gateway_settings_asdict_leaks_neither_key_nor_session() -> None:
    """跨任务约束：redact 是 asdict 旁路的第二道防线，api_key 与 session_id 都要挡住。"""
    payload = asdict(
        GatewaySettings(
            family="challenge",
            base_url="https://example.test/v1",
            api_key="sk-live-REAL-KEY",
            model="m",
            session_id="sess-REAL-123",
        )
    )
    out = redact(payload)
    assert out["api_key"] == "***"
    assert out["session_id"] == "***"
    dumped = json.dumps(out, ensure_ascii=False)
    assert "sk-live-REAL-KEY" not in dumped
    assert "sess-REAL-123" not in dumped


def test_redaction_handles_lists_of_dicts() -> None:
    out = redact({"items": [{"token": "t"}, {"ok": True}]})
    assert out["items"] == [{"token": "***"}, {"ok": True}]


def test_redaction_recurses_into_lists_nested_in_lists() -> None:
    """list 套 list 也必须递归，否则 `{"a": [[{"api_key": ...}]]}` 原样泄漏。"""
    out = redact({"a": [[{"api_key": "sk-LEAK"}], [{"token": "t"}, "plain"]]})
    assert out == {"a": [[{"api_key": "***"}], [{"token": "***"}, "plain"]]}
    assert "sk-LEAK" not in json.dumps(out, ensure_ascii=False)


def test_redaction_recurses_into_tuples_nested_in_tuples() -> None:
    """tuple 套 tuple 同理；容器统一降级为 list（与 brief 既有行为一致）。"""
    out = redact({"t": (({"api_key": "sk-LEAK"},),)})
    assert out == {"t": [[{"api_key": "***"}]]}
    assert "sk-LEAK" not in json.dumps(out, ensure_ascii=False)


def test_redaction_handles_tuple_containing_mapping() -> None:
    out = redact({"t": ({"api_key": "sk-LEAK"}, "plain")})
    assert out == {"t": [{"api_key": "***"}, "plain"]}
    assert "sk-LEAK" not in json.dumps(out, ensure_ascii=False)


def test_redaction_recurses_through_mixed_nesting() -> None:
    """mapping → list → tuple → mapping → list → list → mapping 的混合嵌套必须逐层递归。"""
    out = redact({"a": [{"b": ({"c": [[{"token": "sk-LEAK-DEEP", "keep": 1}]]},)}]})
    assert out == {"a": [{"b": [{"c": [[{"token": "***", "keep": 1}]]}]}]}
    assert "sk-LEAK-DEEP" not in json.dumps(out, ensure_ascii=False)


def test_secret_never_reaches_the_stream(stream: io.StringIO) -> None:
    log = JsonLogger("test", stream=stream)
    log.info("gateway.call", api_key="sk-live-secret", model="m")
    text = stream.getvalue()
    assert "sk-live-secret" not in text
    assert '"api_key": "***"' in text


def test_non_secret_fields_pass_through(stream: io.StringIO) -> None:
    log = JsonLogger("test", stream=stream)
    log.info(
        "stage.done",
        stage="persist",
        duration_ms=12.5,
        finish_reason="stop",
        prompt_tokens=9,
        completion_tokens=2,
        source_sha256="a" * 64,
    )
    record = read_lines(stream)[0]
    assert record["finish_reason"] == "stop"
    assert record["prompt_tokens"] == 9
    assert record["source_sha256"] == "a" * 64


def test_unserialisable_values_are_stringified_not_raised(stream: io.StringIO) -> None:
    log = JsonLogger("test", stream=stream)
    log.info("weird", obj=object())
    assert read_lines(stream)[0]["event"] == "weird"


def test_circular_payload_never_raises_and_degrades(stream: io.StringIO) -> None:
    """日志是旁路：病态载荷只能降级为最小记录，绝不能把异常抛回答题路径。"""
    log = JsonLogger("test", stream=stream)
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    cyclic["api_key"] = "sk-live-secret"
    log.info("boom", payload=cyclic)
    (record,) = read_lines(stream)
    assert record["level"] == "info"
    assert record["event"] == "boom"
    assert record["unserialisable"] is True
    assert "sk-live-secret" not in stream.getvalue()


def test_circular_bound_field_does_not_raise(stream: io.StringIO) -> None:
    """bind() 的字段同样走 redact，也必须在守卫区内。"""
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    log = JsonLogger("test", stream=stream).bind(ctx=cyclic)
    log.info("evt")
    assert read_lines(stream)[0]["event"] == "evt"
