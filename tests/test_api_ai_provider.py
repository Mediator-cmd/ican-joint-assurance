from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app.ai_provider import (
    AIProviderInvalidOutputError,
    AIProviderRequestError,
    AIProviderTimeoutError,
    ModelEventExtraction,
)
from backend.app.main import create_app
from backend.app.runtime_repository import SQLiteRuntimeSessionRepository


SCENARIO_ID = "SCN-TERMINAL-DISTURBANCE-01"
TZ = timezone(timedelta(hours=8))


@dataclass
class FakeClock:
    wall: datetime
    monotonic: float = 100.0

    def wall_now(self) -> datetime:
        return self.wall

    def monotonic_now(self) -> float:
        return self.monotonic


class FakeProvider:
    model_label = "fake-structured-model"

    def __init__(self, outcome) -> None:
        self.outcome = outcome
        self.calls = 0

    def extract_event(self, text, context) -> ModelEventExtraction:
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def extraction(**overrides) -> ModelEventExtraction:
    payload = {
        "event_type": "delay",
        "event_type_quote": "往后挪",
        "flight_id": "FL-SIM102",
        "flight_quote": "SIM102",
        "occurred_at_quote": "08:12",
        "delay_minutes": 20,
        "delay_minutes_quote": "二十分钟",
        "new_gate_id": None,
        "new_gate_quote": None,
    }
    payload.update(overrides)
    return ModelEventExtraction.model_validate(payload)


def create_test_app(tmp_path, provider):
    clock = FakeClock(datetime(2026, 8, 1, 7, 55, tzinfo=TZ))
    return create_app(
        runtime_repository=SQLiteRuntimeSessionRepository(
            tmp_path / "runtime.sqlite3"
        ),
        wall_clock=clock.wall_now,
        monotonic_clock=clock.monotonic_now,
        event_provider=provider,
    )


def request_payload(text: str, *, mode: str = "auto") -> dict:
    return {
        "context": {
            "scope": "scenario",
            "scenario_id": SCENARIO_ID,
            "expected_version": 1,
            "reference_time": "2026-08-01T08:20:00+08:00",
        },
        "text": text,
        "assistance_mode": mode,
    }


def test_auto_mode_accepts_valid_model_extraction_after_authoritative_validation(
    tmp_path,
) -> None:
    provider = FakeProvider(extraction())
    app = create_test_app(tmp_path, provider)
    raw_text = "SIM102 在 08:12 的出发安排要往后挪二十分钟"

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/assistant/event-drafts",
            json=request_payload(raw_text),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready_for_review"
    assert payload["trace"] == {
        "source": "language_model",
        "provider_attempted": True,
        "model_label": "fake-structured-model",
        "fallback_reason": None,
    }
    assert payload["event"]["flight_id"] == "FL-SIM102"
    assert payload["event"]["occurred_at"] == "2026-08-01T08:12:00+08:00"
    assert payload["event"]["delay_minutes"] == 20
    assert payload["requires_human_confirmation"] is True
    assert payload["applies_automatically"] is False
    assert raw_text not in response.text
    assert provider.calls == 1


def test_model_can_return_exact_clarification_without_partial_event(tmp_path) -> None:
    provider = FakeProvider(extraction(delay_minutes=None, delay_minutes_quote=None))
    app = create_test_app(tmp_path, provider)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/assistant/event-drafts",
            json=request_payload("SIM102 在 08:12 的出发安排需要往后挪"),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "needs_clarification"
    assert payload["trace"]["source"] == "language_model"
    assert payload["event"] is None
    assert payload["missing_fields"] == ["delay_minutes"]
    assert payload["clarification_questions"][0]["question"] == (
        "请补充该航班延误的分钟数。"
    )


def test_deterministic_only_never_calls_configured_provider(tmp_path) -> None:
    provider = FakeProvider(extraction())
    app = create_test_app(tmp_path, provider)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/assistant/event-drafts",
            json=request_payload(
                "SIM102 刚刚确认延误 20 分钟",
                mode="deterministic_only",
            ),
        )

    assert response.status_code == 200
    assert response.json()["trace"] == {
        "source": "deterministic_rules",
        "provider_attempted": False,
        "model_label": None,
        "fallback_reason": None,
    }
    assert provider.calls == 0


@pytest.mark.parametrize(
    ("error", "fallback_reason"),
    [
        (AIProviderTimeoutError(), "model_timeout"),
        (AIProviderRequestError(), "provider_error"),
        (AIProviderInvalidOutputError(), "invalid_model_output"),
    ],
)
def test_provider_failures_fall_back_to_deterministic_parser(
    tmp_path,
    error,
    fallback_reason,
) -> None:
    provider = FakeProvider(error)
    app = create_test_app(tmp_path, provider)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/assistant/event-drafts",
            json=request_payload("SIM102 刚刚确认延误 20 分钟"),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready_for_review"
    assert payload["trace"] == {
        "source": "deterministic_rules",
        "provider_attempted": True,
        "model_label": None,
        "fallback_reason": fallback_reason,
    }
    assert payload["event"]["delay_minutes"] == 20
    assert provider.calls == 1


def test_unknown_model_entity_is_invalid_and_falls_back_without_guessing(tmp_path) -> None:
    provider = FakeProvider(extraction(flight_id="FL-NOT-AUTHORITATIVE"))
    app = create_test_app(tmp_path, provider)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/assistant/event-drafts",
            json=request_payload("SIM102 刚刚确认延误 20 分钟"),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["trace"]["source"] == "deterministic_rules"
    assert payload["trace"]["fallback_reason"] == "invalid_model_output"
    assert payload["event"]["flight_id"] == "FL-SIM102"


@pytest.mark.parametrize(
    "invalid_extraction",
    [
        extraction(delay_minutes=30, delay_minutes_quote="20 分钟"),
        extraction(
            event_type="gate_change",
            event_type_quote="延误",
            delay_minutes=None,
            delay_minutes_quote=None,
            new_gate_id="GATE-W03",
            new_gate_quote="GATE-W03",
        ),
    ],
)
def test_semantically_conflicting_model_values_fall_back_to_rules(
    tmp_path,
    invalid_extraction,
) -> None:
    provider = FakeProvider(invalid_extraction)
    app = create_test_app(tmp_path, provider)
    text = "SIM102 刚刚确认延误 20 分钟，并提到 GATE-W03"

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/assistant/event-drafts",
            json=request_payload(text),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["trace"]["fallback_reason"] == "invalid_model_output"
    assert payload["event"]["event_type"] == "delay"
    assert payload["event"]["delay_minutes"] == 20
