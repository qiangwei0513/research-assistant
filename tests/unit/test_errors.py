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
