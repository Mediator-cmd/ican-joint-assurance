from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.app.demo_export import SAFETY_NOTICE
from backend.app.main import create_app
from backend.app.runtime_repository import SQLiteRuntimeSessionRepository


SCENARIO_ID = "SCN-TERMINAL-DISTURBANCE-01"
SCENARIO_PATH = f"/api/v1/scenarios/{SCENARIO_ID}"
TZ = timezone(timedelta(hours=8))


@dataclass
class FakeClock:
    wall: datetime
    monotonic: float = 100.0

    def wall_now(self) -> datetime:
        return self.wall

    def monotonic_now(self) -> float:
        return self.monotonic

    def advance(self, seconds: float) -> None:
        self.wall += timedelta(seconds=seconds)
        self.monotonic += seconds


def _app(session_id: str = "RUN-M53-SUBMISSION"):
    return create_app(
        runtime_repository=SQLiteRuntimeSessionRepository(":memory:"),
        runtime_session_id_factory=lambda: session_id,
    )


def _scenario_draft(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/assistant/event-drafts",
        json={
            "context": {
                "scope": "scenario",
                "scenario_id": SCENARIO_ID,
                "expected_version": 1,
                "reference_time": "2026-08-01T08:20:00+08:00",
            },
            "text": "SIM102 于 08:12 确认延误 20 分钟",
            "assistance_mode": "deterministic_only",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ready_for_review"
    return response.json()


def _runtime(client: TestClient) -> dict:
    plan = client.post(
        f"{SCENARIO_PATH}/plans",
        json={"expected_version": 1, "algorithm": "fifo"},
    )
    assert plan.status_code == 201
    created = client.post(
        "/api/v1/runtime-sessions",
        json={
            "scenario_id": SCENARIO_ID,
            "scenario_version": 1,
            "active_plan_id": plan.json()["plan"]["plan_id"],
            "speed": 1,
        },
    )
    assert created.status_code == 201
    return created.json()


def _runtime_draft(client: TestClient, runtime: dict) -> dict:
    response = client.post(
        "/api/v1/assistant/event-drafts",
        json={
            "context": {
                "scope": "runtime",
                "session_id": runtime["session_id"],
                "expected_revision": runtime["revision"],
            },
            "text": "SIM330 刚刚确认延误 10 分钟",
            "assistance_mode": "deterministic_only",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ready_for_review"
    return response.json()


def test_scenario_draft_submission_reuses_versioned_event_application() -> None:
    app = _app("RUN-M53-SCENARIO")
    with TestClient(app) as client:
        draft = _scenario_draft(client)
        before = client.get(SCENARIO_PATH).json()
        submitted = client.post(
            "/api/v1/assistant/event-drafts/submit",
            json={
                "scope": "scenario",
                "draft": draft,
                "confirm_event": True,
            },
        )
        stale = client.post(
            "/api/v1/assistant/event-drafts/submit",
            json={
                "scope": "scenario",
                "draft": draft,
                "confirm_event": True,
            },
        )
        plans = client.get(f"{SCENARIO_PATH}/plans")

    assert before["current_version"] == 1
    assert submitted.status_code == 200
    payload = submitted.json()
    assert payload["current_version"] == 2
    assert payload["applied_event_ids"] == [draft["event"]["event_id"]]
    assert payload["scenario"]["events"] == [draft["event"]]
    assert payload["safety_notice"] == SAFETY_NOTICE
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "assistant_version_conflict"
    assert plans.json()["total"] == 0


def test_runtime_submission_requires_both_human_confirmations_without_mutation() -> None:
    app = _app()
    with TestClient(app) as client:
        runtime = _runtime(client)
        draft = _runtime_draft(client, runtime)
        missing_event_confirmation = client.post(
            "/api/v1/assistant/event-drafts/submit",
            json={
                "scope": "runtime",
                "draft": draft,
                "confirm_event": False,
                "objective_profile": "balanced",
                "confirm_objective": True,
            },
        )
        missing_objective_confirmation = client.post(
            "/api/v1/assistant/event-drafts/submit",
            json={
                "scope": "runtime",
                "draft": draft,
                "confirm_event": True,
                "objective_profile": "balanced",
            },
        )
        current = client.get(
            f"/api/v1/runtime-sessions/{runtime['session_id']}"
        )

    assert missing_event_confirmation.status_code == 422
    assert missing_objective_confirmation.status_code == 422
    assert current.status_code == 200
    assert current.json()["revision"] == runtime["revision"]
    assert current.json()["candidate_plan_id"] is None
    assert all(
        event["event_id"] != draft["event"]["event_id"]
        for event in current.json()["events"]
    )


def test_runtime_submission_rejects_time_rollback_without_mutation() -> None:
    app = _app("RUN-M53-STALE-TIME")
    with TestClient(app) as client:
        runtime = _runtime(client)
        draft = _runtime_draft(client, runtime)
        stale_time = "2026-08-01T07:59:00+08:00"
        draft["event"]["occurred_at"] = stale_time
        next(
            item for item in draft["evidence"] if item["field"] == "occurred_at"
        )["normalized_value"] = stale_time
        submitted = client.post(
            "/api/v1/assistant/event-drafts/submit",
            json={
                "scope": "runtime",
                "draft": draft,
                "confirm_event": True,
                "objective_profile": "balanced",
                "confirm_objective": True,
            },
        )
        current = client.get(
            f"/api/v1/runtime-sessions/{runtime['session_id']}"
        ).json()

    assert submitted.status_code == 409
    assert submitted.json()["error"]["code"] == "runtime_event_time_conflict"
    assert current["revision"] == runtime["revision"]
    assert current["objective_profile"] == "balanced"


def test_runtime_submission_rejects_unknown_entity_without_mutation() -> None:
    app = _app("RUN-M53-UNKNOWN-ENTITY")
    with TestClient(app) as client:
        runtime = _runtime(client)
        draft = _runtime_draft(client, runtime)
        draft["event"]["flight_id"] = "SIM999"
        next(
            item for item in draft["evidence"] if item["field"] == "flight_id"
        )["normalized_value"] = "SIM999"
        submitted = client.post(
            "/api/v1/assistant/event-drafts/submit",
            json={
                "scope": "runtime",
                "draft": draft,
                "confirm_event": True,
                "objective_profile": "balanced",
                "confirm_objective": True,
            },
        )
        current = client.get(
            f"/api/v1/runtime-sessions/{runtime['session_id']}"
        ).json()

    assert submitted.status_code == 400
    assert submitted.json()["error"]["code"] == "runtime_event_not_applicable"
    assert current["revision"] == runtime["revision"]
    assert all(
        event["event_id"] != draft["event"]["event_id"]
        for event in current["events"]
    )


def test_runtime_reviewed_event_enters_candidate_chain_but_is_not_auto_adopted() -> None:
    app = _app()
    with TestClient(app) as client:
        runtime = _runtime(client)
        draft = _runtime_draft(client, runtime)
        submitted = client.post(
            "/api/v1/assistant/event-drafts/submit",
            json={
                "scope": "runtime",
                "draft": draft,
                "confirm_event": True,
                "objective_profile": "minimum_wait",
                "confirm_objective": True,
            },
        )
        stale = client.post(
            "/api/v1/assistant/event-drafts/submit",
            json={
                "scope": "runtime",
                "draft": draft,
                "confirm_event": True,
                "objective_profile": "minimum_wait",
                "confirm_objective": True,
            },
        )

    assert submitted.status_code == 200
    payload = submitted.json()
    assert payload["status"] == "awaiting_confirmation"
    assert payload["objective_profile"] == "minimum_wait"
    assert payload["active_plan_id"] == runtime["active_plan_id"]
    assert payload["candidate_plan_id"] != payload["active_plan_id"]
    assert payload["candidate_plan_detail"]["objective_profile"] == "minimum_wait"
    assert payload["candidate_plan_detail"]["violations"] == []
    event = next(
        item for item in payload["events"] if item["event_id"] == draft["event"]["event_id"]
    )
    assert event["status"] == "awaiting_confirmation"
    assert event["candidate_plan_id"] == payload["candidate_plan_id"]
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "assistant_revision_conflict"
    actions = [
        item.action
        for item in app.state.runtime_repository.list_audit_records(runtime["session_id"])
    ]
    assert actions[-2:] == ["event_batch_applied", "candidate_created"]


def test_future_reviewed_event_is_new_catalog_fact_and_triggers_its_own_candidate() -> None:
    clock = FakeClock(datetime(2026, 8, 1, 7, 55, tzinfo=TZ))
    app = create_app(
        runtime_repository=SQLiteRuntimeSessionRepository(":memory:"),
        runtime_session_id_factory=lambda: "RUN-M53-FUTURE",
        wall_clock=clock.wall_now,
        monotonic_clock=clock.monotonic_now,
    )
    with TestClient(app) as client:
        runtime = _runtime(client)
        draft_response = client.post(
            "/api/v1/assistant/event-drafts",
            json={
                "context": {
                    "scope": "runtime",
                    "session_id": runtime["session_id"],
                    "expected_revision": runtime["revision"],
                },
                "text": "SIM330 于 08:10 确认延误 10 分钟",
                "assistance_mode": "deterministic_only",
            },
        )
        assert draft_response.status_code == 200
        draft = draft_response.json()
        registered_response = client.post(
            "/api/v1/assistant/event-drafts/submit",
            json={
                "scope": "runtime",
                "draft": draft,
                "confirm_event": True,
                "objective_profile": "critical_first",
                "confirm_objective": True,
            },
        )
        assert registered_response.status_code == 200
        snapshot = registered_response.json()
        candidate_ids: list[str] = []

        for _ in range(10):
            target = next(
                item
                for item in snapshot["events"]
                if item["event_id"] == draft["event"]["event_id"]
            )
            if target["status"] == "awaiting_confirmation":
                break
            if snapshot["status"] == "awaiting_confirmation":
                candidate_ids.append(snapshot["candidate_plan_id"])
                snapshot = client.post(
                    f"/api/v1/runtime-sessions/{runtime['session_id']}/candidate/accept",
                    json={
                        "expected_revision": snapshot["revision"],
                        "candidate_plan_id": snapshot["candidate_plan_id"],
                    },
                ).json()
            if snapshot["status"] in {"ready", "paused"}:
                snapshot = client.post(
                    f"/api/v1/runtime-sessions/{runtime['session_id']}/start",
                    json={"expected_revision": snapshot["revision"]},
                ).json()
            if snapshot["status"] == "running":
                next_boundary = datetime.fromisoformat(snapshot["clock"]["next_boundary_at"])
                simulation_time = datetime.fromisoformat(
                    snapshot["clock"]["simulation_time"]
                )
                clock.advance((next_boundary - simulation_time).total_seconds())
                snapshot = client.get(
                    f"/api/v1/runtime-sessions/{runtime['session_id']}"
                ).json()

    target = next(
        item
        for item in snapshot["events"]
        if item["event_id"] == draft["event"]["event_id"]
    )
    assert registered_response.json()["status"] == "ready"
    assert registered_response.json()["candidate_plan_id"] is None
    assert target["status"] == "awaiting_confirmation"
    assert target["candidate_plan_id"] == snapshot["candidate_plan_id"]
    assert snapshot["candidate_plan_detail"]["objective_profile"] == "critical_first"
    assert snapshot["candidate_plan_id"] not in candidate_ids
    assert len(candidate_ids) == len(set(candidate_ids))


def test_submission_openapi_keeps_review_and_planner_confirmation_explicit() -> None:
    with TestClient(_app("RUN-M53-OPENAPI")) as client:
        document = client.get("/api/v1/openapi.json").json()

    path = document["paths"]["/api/v1/assistant/event-drafts/submit"]["post"]
    assert path["requestBody"]["required"] is True
    schema = document["components"]["schemas"]["EventDraftSubmissionRequest"]
    assert {"scope", "draft", "confirm_event"} <= set(schema["required"])
