from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.runtime_repository import SQLiteRuntimeSessionRepository


SCENARIO_ID = "SCN-TERMINAL-DISTURBANCE-01"


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


def _build_app(tmp_path):
    clock = FakeClock(datetime(2026, 7, 30, 7, 55, tzinfo=timezone(timedelta(hours=8))))
    app = create_app(
        runtime_repository=SQLiteRuntimeSessionRepository(tmp_path / "runtime.sqlite3"),
        wall_clock=clock.wall_now,
        monotonic_clock=clock.monotonic_now,
        runtime_session_id_factory=lambda: "RUN-SPATIAL-001",
    )
    return app, clock


def _create_runtime(client: TestClient) -> dict:
    plan = client.post(
        f"/api/v1/scenarios/{SCENARIO_ID}/plans",
        json={"expected_version": 1, "algorithm": "fifo", "max_time_seconds": 2},
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


def test_spatial_api_covers_every_default_task_resource_and_event(tmp_path) -> None:
    app, _ = _build_app(tmp_path)
    with TestClient(app) as client:
        created = _create_runtime(client)
        response = client.get(
            f"/api/v1/runtime-sessions/{created['session_id']}/spatial",
            params={"expected_revision": created["revision"]},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["scenario_version"] == 1
    assert len(payload["overlay"]["task_routes"]) == 10
    assert len(payload["overlay"]["resource_markers"]) == 5
    assert len(payload["overlay"]["event_markers"]) == 6
    assert payload["coverage"]["complete"] is True
    assert payload["coverage"]["source_task_ids"] == payload["coverage"]["projected_active_task_ids"]
    assert payload["coverage"]["source_resource_ids"] == payload["coverage"]["projected_resource_ids"]
    assert payload["coverage"]["source_event_ids"] == payload["coverage"]["projected_event_ids"]
    assert any(
        marker["event_id"] == "EVT-SIM218-GATE"
        and marker["primary_zone_id"] == "GATE-E01"
        and marker["route_legs"]
        for marker in payload["overlay"]["event_markers"]
    )
    assert all(fact["fact_id"].startswith("SPATIAL-FACT-") for fact in payload["facts"])


def test_spatial_api_rejects_stale_runtime_revision(tmp_path) -> None:
    app, _ = _build_app(tmp_path)
    with TestClient(app) as client:
        created = _create_runtime(client)
        started = client.post(
            f"/api/v1/runtime-sessions/{created['session_id']}/start",
            json={"expected_revision": created["revision"]},
        )
        assert started.status_code == 200
        stale = client.get(
            f"/api/v1/runtime-sessions/{created['session_id']}/spatial",
            params={"expected_revision": created["revision"]},
        )

    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "runtime_revision_conflict"


def test_spatial_api_preserves_active_routes_when_candidate_appears(tmp_path) -> None:
    app, clock = _build_app(tmp_path)
    with TestClient(app) as client:
        created = _create_runtime(client)
        started = client.post(
            f"/api/v1/runtime-sessions/{created['session_id']}/start",
            json={"expected_revision": created["revision"]},
        )
        assert started.status_code == 200
        first_candidate = started.json()
        assert first_candidate["candidate_plan_id"] is not None
        unchanged = client.get(
            f"/api/v1/runtime-sessions/{created['session_id']}/spatial",
            params={"expected_revision": first_candidate["revision"]},
        )
        assert unchanged.status_code == 200
        assert unchanged.json()["coverage"]["changed_candidate_task_ids"] == []

        accepted = client.post(
            f"/api/v1/runtime-sessions/{created['session_id']}/candidate/accept",
            json={
                "expected_revision": first_candidate["revision"],
                "candidate_plan_id": first_candidate["candidate_plan_id"],
            },
        )
        assert accepted.status_code == 200
        resumed = client.post(
            f"/api/v1/runtime-sessions/{created['session_id']}/start",
            json={"expected_revision": accepted.json()["revision"]},
        )
        assert resumed.status_code == 200
        clock.advance(16)
        current = client.get(
            f"/api/v1/runtime-sessions/{created['session_id']}"
        ).json()
        assert current["candidate_plan_id"] is not None
        response = client.get(
            f"/api/v1/runtime-sessions/{created['session_id']}/spatial",
            params={"expected_revision": current["revision"]},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    active_routes = [
        route for route in payload["overlay"]["task_routes"] if route["route_kind"] == "active"
    ]
    candidate_routes = [
        route
        for route in payload["overlay"]["task_routes"]
        if route["route_kind"] == "candidate"
    ]
    assert len(active_routes) == 10
    assert candidate_routes
    assert {route["task_id"] for route in candidate_routes} == set(
        payload["coverage"]["changed_candidate_task_ids"]
    )
    assert all(route["plan_id"] == current["candidate_plan_id"] for route in candidate_routes)
    assert all(route["change_kind"] != "current" for route in candidate_routes)
