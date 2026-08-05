from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.app.demo_export import SAFETY_NOTICE
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


def scenario_request(text: str, **overrides) -> dict:
    payload = {
        "context": {
            "scope": "scenario",
            "scenario_id": SCENARIO_ID,
            "expected_version": 1,
            "reference_time": "2026-08-01T08:20:00+08:00",
        },
        "text": text,
        "assistance_mode": "auto",
    }
    payload.update(overrides)
    return payload


def create_runtime_app(tmp_path):
    clock = FakeClock(datetime(2026, 8, 1, 7, 55, tzinfo=TZ))
    runtime_repository = SQLiteRuntimeSessionRepository(tmp_path / "runtime.sqlite3")
    app = create_app(
        runtime_repository=runtime_repository,
        wall_clock=clock.wall_now,
        monotonic_clock=clock.monotonic_now,
        runtime_session_id_factory=lambda: "RUN-AI-001",
    )
    return app, runtime_repository


def create_runtime(client: TestClient) -> dict:
    plan = client.post(
        f"/api/v1/scenarios/{SCENARIO_ID}/plans",
        json={
            "expected_version": 1,
            "algorithm": "fifo",
            "max_time_seconds": 2,
        },
    )
    assert plan.status_code == 201
    response = client.post(
        "/api/v1/runtime-sessions",
        json={
            "scenario_id": SCENARIO_ID,
            "scenario_version": 1,
            "active_plan_id": plan.json()["plan"]["plan_id"],
            "speed": 1,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_scenario_event_draft_is_review_only_and_does_not_echo_raw_text(tmp_path) -> None:
    app, _ = create_runtime_app(tmp_path)
    raw_text = "SIM102 于 08:12 确认延误 20 分钟，请不要自动执行"
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/assistant/event-drafts",
            json=scenario_request(raw_text),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready_for_review"
    assert payload["basis"] == {
        "scenario_id": SCENARIO_ID,
        "scenario_version": 1,
        "reference_time": "2026-08-01T08:20:00+08:00",
        "runtime_session_id": None,
        "runtime_revision": None,
    }
    assert payload["trace"] == {
        "source": "deterministic_rules",
        "provider_attempted": False,
        "model_label": None,
        "fallback_reason": "model_not_configured",
    }
    assert payload["event"]["flight_id"] == "FL-SIM102"
    assert payload["event"]["delay_minutes"] == 20
    assert payload["requires_human_confirmation"] is True
    assert payload["applies_automatically"] is False
    assert payload["safety_notice"] == SAFETY_NOTICE
    assert raw_text not in response.text


def test_deterministic_only_does_not_claim_model_fallback(tmp_path) -> None:
    app, _ = create_runtime_app(tmp_path)
    request = scenario_request(
        "SIM102 刚刚确认延误 20 分钟",
        assistance_mode="deterministic_only",
    )
    with TestClient(app) as client:
        response = client.post("/api/v1/assistant/event-drafts", json=request)

    assert response.status_code == 200
    assert response.json()["trace"] == {
        "source": "deterministic_rules",
        "provider_attempted": False,
        "model_label": None,
        "fallback_reason": None,
    }


def test_missing_fields_return_review_questions_without_partial_event(tmp_path) -> None:
    app, _ = create_runtime_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/assistant/event-drafts",
            json=scenario_request("SIM102 08:12 确认延误"),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "needs_clarification"
    assert payload["event"] is None
    assert payload["missing_fields"] == ["delay_minutes"]
    assert payload["clarification_questions"][0]["question"] == (
        "请补充该航班延误的分钟数。"
    )


def test_scenario_context_maps_missing_and_stale_versions_to_assistant_errors(tmp_path) -> None:
    app, _ = create_runtime_app(tmp_path)
    stale_request = scenario_request("SIM102 刚刚确认延误 20 分钟")
    stale_request["context"]["expected_version"] = 9
    missing_request = scenario_request("SIM102 刚刚确认延误 20 分钟")
    missing_request["context"]["scenario_id"] = "SCN-MISSING"

    with TestClient(app) as client:
        stale = client.post("/api/v1/assistant/event-drafts", json=stale_request)
        missing = client.post("/api/v1/assistant/event-drafts", json=missing_request)

    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "assistant_version_conflict"
    assert "当前版本 1" in stale.json()["error"]["details"][0]["message"]
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "assistant_context_not_found"


def test_runtime_context_uses_authoritative_clock_revision_and_current_gate(tmp_path) -> None:
    app, _ = create_runtime_app(tmp_path)
    with TestClient(app) as client:
        runtime = create_runtime(client)
        response = client.post(
            "/api/v1/assistant/event-drafts",
            json={
                "context": {
                    "scope": "runtime",
                    "session_id": runtime["session_id"],
                    "expected_revision": runtime["revision"],
                },
                "text": "SIM218 刚刚改到 GATE-E01",
                "assistance_mode": "deterministic_only",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["basis"] == {
        "scenario_id": SCENARIO_ID,
        "scenario_version": 1,
        "reference_time": runtime["clock"]["simulation_time"],
        "runtime_session_id": runtime["session_id"],
        "runtime_revision": runtime["revision"],
    }
    assert payload["event"]["occurred_at"] == runtime["clock"]["simulation_time"]
    assert payload["event"]["previous_gate_id"] == "GATE-W03"
    assert payload["event"]["new_gate_id"] == "GATE-E01"


def test_runtime_context_rejects_stale_revision_and_missing_session(tmp_path) -> None:
    app, _ = create_runtime_app(tmp_path)
    with TestClient(app) as client:
        runtime = create_runtime(client)
        stale = client.post(
            "/api/v1/assistant/event-drafts",
            json={
                "context": {
                    "scope": "runtime",
                    "session_id": runtime["session_id"],
                    "expected_revision": runtime["revision"] + 1,
                },
                "text": "SIM102 刚刚确认延误 20 分钟",
            },
        )
        missing = client.post(
            "/api/v1/assistant/event-drafts",
            json={
                "context": {
                    "scope": "runtime",
                    "session_id": "RUN-MISSING",
                    "expected_revision": 1,
                },
                "text": "SIM102 刚刚确认延误 20 分钟",
            },
        )

    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "assistant_revision_conflict"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "assistant_context_not_found"


def test_event_draft_requests_do_not_mutate_scenario_runtime_or_audits(tmp_path) -> None:
    app, runtime_repository = create_runtime_app(tmp_path)
    with TestClient(app) as client:
        runtime = create_runtime(client)
        scenario_repository = app.state.scenario_repository
        scenario_before = scenario_repository.get_state_snapshot(SCENARIO_ID)
        scenario_audits_before = scenario_repository.list_audit_records(SCENARIO_ID)
        runtime_before = runtime_repository.get_session(runtime["session_id"])
        runtime_audits_before = runtime_repository.list_audit_records(
            runtime["session_id"]
        )

        scenario_response = client.post(
            "/api/v1/assistant/event-drafts",
            json=scenario_request("SIM102 刚刚确认延误 20 分钟"),
        )
        runtime_response = client.post(
            "/api/v1/assistant/event-drafts",
            json={
                "context": {
                    "scope": "runtime",
                    "session_id": runtime["session_id"],
                    "expected_revision": runtime["revision"],
                },
                "text": "SIM218 刚刚改到 GATE-E01",
            },
        )

        scenario_after = scenario_repository.get_state_snapshot(SCENARIO_ID)
        scenario_audits_after = scenario_repository.list_audit_records(SCENARIO_ID)
        runtime_after = runtime_repository.get_session(runtime["session_id"])
        runtime_audits_after = runtime_repository.list_audit_records(
            runtime["session_id"]
        )

    assert scenario_response.status_code == 200
    assert runtime_response.status_code == 200
    assert scenario_after == scenario_before
    assert scenario_audits_after == scenario_audits_before
    assert runtime_after == runtime_before
    assert runtime_audits_after == runtime_audits_before

