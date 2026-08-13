from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.demo_export import SAFETY_NOTICE
from backend.app.main import create_app
from backend.app.runtime_repository import SQLiteRuntimeSessionRepository


SCENARIO_ID = "SCN-TERMINAL-DISTURBANCE-01"
SESSION_ID = "RUN-M65A3-ACCEPTANCE"
EVENT_MINUTES = [0, 4, 16, 28, 40, 52]
DECISIONS = ["accept", "reject", "accept", "reject", "accept", "reject"]


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


def _app(database_path: Path, clock: FakeClock):
    return create_app(
        runtime_repository=SQLiteRuntimeSessionRepository(database_path),
        wall_clock=clock.wall_now,
        monotonic_clock=clock.monotonic_now,
        runtime_session_id_factory=lambda: SESSION_ID,
    )


def _create_runtime(client: TestClient) -> dict:
    plan = client.post(
        f"/api/v1/scenarios/{SCENARIO_ID}/plans",
        json={"expected_version": 1, "algorithm": "cp_sat", "max_time_seconds": 2},
    )
    assert plan.status_code == 201, plan.text
    created = client.post(
        "/api/v1/runtime-sessions",
        json={
            "scenario_id": SCENARIO_ID,
            "scenario_version": 1,
            "active_plan_id": plan.json()["plan"]["plan_id"],
            "speed": 15,
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def _spatial(client: TestClient, snapshot: dict) -> dict:
    response = client.get(
        f"/api/v1/runtime-sessions/{SESSION_ID}/spatial",
        params={"expected_revision": snapshot["revision"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _business_snapshot(snapshot: dict) -> dict:
    result = {**snapshot, "clock": {**snapshot["clock"]}}
    result["clock"].pop("server_time", None)
    return result


def _assert_awaiting_spatial(
    client: TestClient,
    snapshot: dict,
    event_index: int,
) -> tuple[dict, str]:
    assert snapshot["status"] == "awaiting_confirmation"
    assert snapshot["candidate_plan_id"] is not None
    assert snapshot["current_scenario_version"] == event_index + 2
    assert datetime.fromisoformat(snapshot["clock"]["simulation_time"]).minute == (
        EVENT_MINUTES[event_index]
    )

    spatial = _spatial(client, snapshot)
    assert spatial["overlay"]["revision"] == snapshot["revision"]
    assert spatial["overlay"]["session_id"] == SESSION_ID
    assert spatial["coverage"]["complete"] is True
    assert len(spatial["coverage"]["source_task_ids"]) == 10
    assert len(spatial["overlay"]["resource_markers"]) == 5
    assert len(spatial["overlay"]["event_markers"]) == 6

    active_routes = [
        route
        for route in spatial["overlay"]["task_routes"]
        if route["route_kind"] == "active"
    ]
    candidate_routes = [
        route
        for route in spatial["overlay"]["task_routes"]
        if route["route_kind"] == "candidate"
    ]
    assert len(active_routes) == 10
    assert {route["task_id"] for route in candidate_routes} == set(
        spatial["coverage"]["changed_candidate_task_ids"]
    )
    assert all(route["plan_id"] == snapshot["candidate_plan_id"] for route in candidate_routes)

    markers = spatial["overlay"]["event_markers"]
    assert [marker["status"] for marker in markers[:event_index]] == [
        "resolved"
    ] * event_index
    assert markers[event_index]["status"] == "awaiting_confirmation"
    assert [marker["status"] for marker in markers[event_index + 1 :]] == [
        "pending"
    ] * (len(markers) - event_index - 1)
    event_id = markers[event_index]["event_id"]

    before_question = _business_snapshot(
        client.get(f"/api/v1/runtime-sessions/{SESSION_ID}").json()
    )
    answer = client.post(
        "/api/v1/assistant/spatial-questions",
        json={
            "context": {
                "session_id": SESSION_ID,
                "revision": snapshot["revision"],
            },
            "question": f"事件{event_index + 1}怎么处理，影响哪些任务和路线？",
            "assistance_mode": "deterministic_only",
        },
    )
    after_question = _business_snapshot(
        client.get(f"/api/v1/runtime-sessions/{SESSION_ID}").json()
    )
    assert answer.status_code == 200, answer.text
    payload = answer.json()
    assert payload["basis"]["revision"] == snapshot["revision"]
    assert payload["answer"]["matched_entity_ids"] == [event_id]
    assert payload["answer"]["fact_ids"][0] == f"SPATIAL-FACT-{event_id}"
    assert payload["focus"]["event_ids"] == [event_id]
    assert payload["trace"]["source"] == "deterministic_rules"
    assert payload["requires_human_confirmation"] is True
    assert payload["modifies_runtime"] is False
    assert payload["safety_notice"] == SAFETY_NOTICE
    assert after_question == before_question
    return spatial, event_id


def _decide(
    client: TestClient,
    snapshot: dict,
    event_index: int,
) -> dict:
    active_plan_id = snapshot["active_plan_id"]
    candidate_plan_id = snapshot["candidate_plan_id"]
    decision = DECISIONS[event_index]
    response = client.post(
        f"/api/v1/runtime-sessions/{SESSION_ID}/candidate/{decision}",
        json={
            "expected_revision": snapshot["revision"],
            "candidate_plan_id": candidate_plan_id,
            "reason": f"M6-5A-3 事件 {event_index + 1} 空间验收",
        },
    )
    assert response.status_code == 200, response.text
    decided = response.json()
    assert decided["status"] == "paused"
    assert decided["candidate_plan_id"] is None
    assert decided["active_plan_id"] == (
        candidate_plan_id if decision == "accept" else active_plan_id
    )

    spatial = _spatial(client, decided)
    assert len(
        [route for route in spatial["overlay"]["task_routes"] if route["route_kind"] == "active"]
    ) == 10
    assert not [
        route for route in spatial["overlay"]["task_routes"] if route["route_kind"] == "candidate"
    ]
    assert spatial["coverage"]["changed_candidate_task_ids"] == []
    assert spatial["overlay"]["event_markers"][event_index]["status"] == "resolved"
    return decided


def _advance_to_next_event(
    client: TestClient,
    clock: FakeClock,
    snapshot: dict,
    event_index: int,
) -> dict:
    started = client.post(
        f"/api/v1/runtime-sessions/{SESSION_ID}/start",
        json={"expected_revision": snapshot["revision"]},
    )
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "running"
    clock.advance(
        (EVENT_MINUTES[event_index + 1] - EVENT_MINUTES[event_index]) * 60 / 15
    )
    current = client.get(f"/api/v1/runtime-sessions/{SESSION_ID}")
    assert current.status_code == 200, current.text
    return current.json()


def test_six_events_spatial_flow_restart_and_no_loop(tmp_path) -> None:
    database_path = tmp_path / "m6-5a3.sqlite3"
    clock = FakeClock(
        datetime(2026, 8, 12, 7, 55, tzinfo=timezone(timedelta(hours=8)))
    )
    awaiting_signatures: list[tuple] = []
    candidate_ids: list[str] = []
    event_ids: list[str] = []

    with TestClient(_app(database_path, clock)) as first_client:
        snapshot = _create_runtime(first_client)
        started = first_client.post(
            f"/api/v1/runtime-sessions/{SESSION_ID}/start",
            json={"expected_revision": snapshot["revision"]},
        )
        assert started.status_code == 200, started.text
        snapshot = started.json()

        for event_index in range(3):
            spatial, event_id = _assert_awaiting_spatial(
                first_client, snapshot, event_index
            )
            candidate_ids.append(snapshot["candidate_plan_id"])
            event_ids.append(event_id)
            current_marker = spatial["overlay"]["event_markers"][event_index]
            awaiting_signatures.append(
                (
                    current_marker["event_type"],
                    current_marker["primary_zone_id"],
                    tuple(spatial["coverage"]["changed_candidate_task_ids"]),
                )
            )
            if event_index < 2:
                snapshot = _decide(first_client, snapshot, event_index)
                snapshot = _advance_to_next_event(
                    first_client, clock, snapshot, event_index
                )

        before_restart = snapshot
        before_restart_spatial = spatial

    with TestClient(_app(database_path, clock)) as recovered_client:
        recovered_response = recovered_client.get(
            f"/api/v1/runtime-sessions/{SESSION_ID}"
        )
        assert recovered_response.status_code == 200, recovered_response.text
        recovered = recovered_response.json()
        assert _business_snapshot(recovered) == _business_snapshot(before_restart)
        assert _spatial(recovered_client, recovered) == before_restart_spatial

        snapshot = _decide(recovered_client, recovered, 2)
        snapshot = _advance_to_next_event(recovered_client, clock, snapshot, 2)
        stale_spatial = recovered_client.get(
            f"/api/v1/runtime-sessions/{SESSION_ID}/spatial",
            params={"expected_revision": before_restart["revision"]},
        )
        stale_answer = recovered_client.post(
            "/api/v1/assistant/spatial-questions",
            json={
                "context": {
                    "session_id": SESSION_ID,
                    "revision": before_restart["revision"],
                },
                "question": "事件3现在怎么样？",
                "assistance_mode": "deterministic_only",
            },
        )
        assert stale_spatial.status_code == 409
        assert stale_answer.status_code == 409

        for event_index in range(3, 6):
            spatial, event_id = _assert_awaiting_spatial(
                recovered_client, snapshot, event_index
            )
            candidate_ids.append(snapshot["candidate_plan_id"])
            event_ids.append(event_id)
            current_marker = spatial["overlay"]["event_markers"][event_index]
            awaiting_signatures.append(
                (
                    current_marker["event_type"],
                    current_marker["primary_zone_id"],
                    tuple(spatial["coverage"]["changed_candidate_task_ids"]),
                )
            )
            snapshot = _decide(recovered_client, snapshot, event_index)
            if event_index < 5:
                snapshot = _advance_to_next_event(
                    recovered_client, clock, snapshot, event_index
                )

        final_start = recovered_client.post(
            f"/api/v1/runtime-sessions/{SESSION_ID}/start",
            json={"expected_revision": snapshot["revision"]},
        )
        assert final_start.status_code == 200, final_start.text
        clock.advance((90 - EVENT_MINUTES[-1]) * 60 / 15)
        completed = recovered_client.get(
            f"/api/v1/runtime-sessions/{SESSION_ID}"
        ).json()
        completed_spatial = _spatial(recovered_client, completed)
        assert completed["status"] == "completed"
        assert completed["current_scenario_version"] == 7
        assert datetime.fromisoformat(completed["clock"]["simulation_time"]).strftime(
            "%H:%M"
        ) == "09:30"
        assert {event["status"] for event in completed["events"]} == {"resolved"}
        assert completed["candidate_plan_id"] is None
        assert completed_spatial["coverage"]["changed_candidate_task_ids"] == []
        assert len(completed_spatial["overlay"]["task_routes"]) == 10

        completed_business = _business_snapshot(completed)
        clock.advance(3600)
        later = recovered_client.get(
            f"/api/v1/runtime-sessions/{SESSION_ID}"
        ).json()
        later_spatial = _spatial(recovered_client, later)
        assert _business_snapshot(later) == completed_business
        assert later_spatial == completed_spatial

    assert len(candidate_ids) == len(set(candidate_ids)) == 6
    assert len(event_ids) == len(set(event_ids)) == 6
    assert len(awaiting_signatures) == len(set(awaiting_signatures)) == 6
