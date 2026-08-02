from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.app.demo_export import SAFETY_NOTICE
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


def _build_app(tmp_path, *, session_id: str = "RUN-API-001", clock: FakeClock | None = None):
    fake_clock = clock or FakeClock(
        datetime(2026, 7, 30, 7, 55, tzinfo=timezone(timedelta(hours=8)))
    )
    runtime_repository = SQLiteRuntimeSessionRepository(tmp_path / "runtime.sqlite3")
    app = create_app(
        runtime_repository=runtime_repository,
        wall_clock=fake_clock.wall_now,
        monotonic_clock=fake_clock.monotonic_now,
        runtime_session_id_factory=lambda: session_id,
    )
    return app, fake_clock


def _create_plan(client: TestClient, version: int = 1, algorithm: str = "fifo") -> str:
    response = client.post(
        f"/api/v1/scenarios/{SCENARIO_ID}/plans",
        json={
            "expected_version": version,
            "algorithm": algorithm,
            "max_time_seconds": 2,
        },
    )
    assert response.status_code == 201
    return response.json()["plan"]["plan_id"]


def _create_runtime(client: TestClient, plan_id: str, *, version: int = 1) -> dict:
    response = client.post(
        "/api/v1/runtime-sessions",
        json={
            "scenario_id": SCENARIO_ID,
            "scenario_version": version,
            "active_plan_id": plan_id,
            "speed": 15,
        },
    )
    assert response.status_code == 201
    return response.json()


def _apply_first_event(client: TestClient) -> None:
    response = client.post(
        f"/api/v1/scenarios/{SCENARIO_ID}/events/apply",
        json={
            "expected_version": 1,
            "event_ids": ["EVT-SIM102-DELAY"],
            "events": [],
        },
    )
    assert response.status_code == 200


def test_runtime_api_controls_authoritative_clock_and_persists_boundaries(tmp_path) -> None:
    app, clock = _build_app(tmp_path)
    with TestClient(app) as client:
        _apply_first_event(client)
        plan_id = _create_plan(client, version=2)
        created = _create_runtime(client, plan_id, version=2)
        listed = client.get(
            "/api/v1/runtime-sessions",
            params={"scenario_id": SCENARIO_ID, "status": "ready"},
        )
        started = client.post(
            f"/api/v1/runtime-sessions/{created['session_id']}/start",
            json={"expected_revision": 1},
        )
        clock.advance(2)
        advancing = client.get(
            f"/api/v1/runtime-sessions/{created['session_id']}"
        )
        speed = client.post(
            f"/api/v1/runtime-sessions/{created['session_id']}/speed",
            json={"expected_revision": 2, "speed": 5},
        )
        clock.advance(2)
        paused = client.post(
            f"/api/v1/runtime-sessions/{created['session_id']}/pause",
            json={"expected_revision": 3},
        )
        reset = client.post(
            f"/api/v1/runtime-sessions/{created['session_id']}/reset",
            json={"expected_revision": 4, "confirm_reset": True},
        )

    assert created["status"] == "ready"
    assert created["storage_scope"] == "sqlite"
    assert created["safety_notice"] == SAFETY_NOTICE
    assert len(created["tasks"]) == 10
    assert len(created["resources"]) == 5
    assert len(created["flights"]) == 3
    assert len(created["events"]) == 6
    assert created["active_plan_detail"]["plan_id"] == created["active_plan_id"]
    assert created["candidate_plan_detail"] is None
    first_event = next(item for item in created["events"] if item["event_id"] == "EVT-SIM102-DELAY")
    assert first_event["event_type"] == "delay"
    assert first_event["flight_id"] == "FL-SIM102"
    assert first_event["detail"] == "SIM102 预计离港时间顺延 25 分钟"
    assert {item["status"] for item in created["tasks"]} == {"pending", "unassigned"}
    assert {item["status"] for item in created["events"]} == {"pending", "resolved"}
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["safety_notice"] == SAFETY_NOTICE
    assert started.status_code == 200
    assert started.json()["status"] == "running"
    start_time = datetime.fromisoformat(created["clock"]["simulation_time"])
    assert datetime.fromisoformat(advancing.json()["clock"]["simulation_time"]) == (
        start_time + timedelta(seconds=30)
    )
    assert speed.json()["clock"]["speed"] == 5
    assert datetime.fromisoformat(paused.json()["clock"]["simulation_time"]) == (
        start_time + timedelta(seconds=40)
    )
    assert paused.json()["status"] == "paused"
    assert reset.json()["status"] == "ready"
    assert reset.json()["revision"] == 5
    assert reset.json()["clock"]["simulation_time"] == created["clock"]["simulation_time"]


def test_runtime_api_maps_revision_transition_missing_and_validation_errors(tmp_path) -> None:
    app, _ = _build_app(tmp_path)
    with TestClient(app) as client:
        _apply_first_event(client)
        plan_id = _create_plan(client, version=2)
        created = _create_runtime(client, plan_id, version=2)
        session_path = f"/api/v1/runtime-sessions/{created['session_id']}"
        assert client.post(f"{session_path}/start", json={"expected_revision": 1}).status_code == 200

        stale = client.post(f"{session_path}/pause", json={"expected_revision": 1})
        duplicate_start = client.post(f"{session_path}/start", json={"expected_revision": 2})
        missing = client.get("/api/v1/runtime-sessions/RUN-MISSING")
        invalid_reset = client.post(
            f"{session_path}/reset",
            json={"expected_revision": 2, "confirm_reset": False},
        )
        missing_plan = client.post(
            "/api/v1/runtime-sessions",
            json={
                "scenario_id": SCENARIO_ID,
                "scenario_version": 1,
                "active_plan_id": "PLAN-MISSING",
            },
        )

    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "runtime_revision_conflict"
    assert "当前修订 2" in stale.json()["error"]["details"][0]["message"]
    assert duplicate_start.status_code == 409
    assert duplicate_start.json()["error"]["code"] == "runtime_invalid_transition"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "runtime_session_not_found"
    assert invalid_reset.status_code == 422
    assert invalid_reset.json()["error"]["code"] == "validation_error"
    assert missing_plan.status_code == 404
    assert missing_plan.json()["error"]["code"] == "runtime_plan_not_found"


def test_runtime_creation_rejects_plan_from_another_scenario_version(tmp_path) -> None:
    app, _ = _build_app(tmp_path)
    with TestClient(app) as client:
        plan_id = _create_plan(client, version=1)
        applied = client.post(
            f"/api/v1/scenarios/{SCENARIO_ID}/events/apply",
            json={
                "expected_version": 1,
                "event_ids": ["EVT-SIM102-DELAY"],
                "events": [],
            },
        )
        assert applied.status_code == 200
        mismatch = client.post(
            "/api/v1/runtime-sessions",
            json={
                "scenario_id": SCENARIO_ID,
                "scenario_version": 2,
                "active_plan_id": plan_id,
            },
        )

    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "runtime_plan_version_mismatch"


def test_runtime_openapi_exposes_m44_stream_route(tmp_path) -> None:
    app, _ = _build_app(tmp_path)
    with TestClient(app) as client:
        document = client.get("/api/v1/openapi.json").json()

    paths = document["paths"]
    expected = {
        "/api/v1/runtime-sessions",
        "/api/v1/runtime-sessions/{session_id}",
        "/api/v1/runtime-sessions/{session_id}/start",
        "/api/v1/runtime-sessions/{session_id}/pause",
        "/api/v1/runtime-sessions/{session_id}/speed",
        "/api/v1/runtime-sessions/{session_id}/reset",
    }
    assert expected <= set(paths)
    assert "/api/v1/runtime-sessions/{session_id}/replan" in paths
    stream_path = "/api/v1/runtime-sessions/{session_id}/stream"
    assert stream_path in paths
    assert "text/event-stream" in paths[stream_path]["get"]["responses"]["200"]["content"]
    assert "/api/v1/runtime-sessions/{session_id}/candidate/accept" in paths
    assert "/api/v1/runtime-sessions/{session_id}/candidate/reject" in paths
    assert (
        paths["/api/v1/runtime-sessions/{session_id}/replan"]["post"]["responses"]
        ["202"]["content"]["application/json"]["schema"]
        == {"$ref": "#/components/schemas/RuntimeSessionSnapshot"}
    )
    response_schema = paths["/api/v1/runtime-sessions"]["post"]["responses"]["201"]
    assert response_schema["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/RuntimeSessionSnapshot"
    }


def test_runtime_stream_missing_session_uses_json_error(tmp_path) -> None:
    app, _ = _build_app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/api/v1/runtime-sessions/RUN-MISSING/stream")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "runtime_session_not_found"


def test_runtime_snapshot_polling_does_not_depend_on_stream_broker(tmp_path) -> None:
    app, _ = _build_app(tmp_path, session_id="RUN-POLL-001")
    with TestClient(app) as client:
        plan_id = _create_plan(client)
        created = _create_runtime(client, plan_id)
        app.state.runtime_stream_broker = None
        response = client.get(
            f"/api/v1/runtime-sessions/{created['session_id']}"
        )

    assert response.status_code == 200
    assert response.json()["session_id"] == created["session_id"]
    assert response.json()["revision"] == created["revision"]


def test_runtime_api_restart_recovers_and_rediscovers_session(tmp_path) -> None:
    path = tmp_path / "runtime.sqlite3"
    clock = FakeClock(
        datetime(2026, 7, 30, 7, 55, tzinfo=timezone(timedelta(hours=8)))
    )
    first_app, _ = _build_app(tmp_path, session_id="RUN-RESTART-001", clock=clock)
    with TestClient(first_app) as client:
        _apply_first_event(client)
        plan_id = _create_plan(client, version=2)
        created = _create_runtime(client, plan_id, version=2)
        started = client.post(
            f"/api/v1/runtime-sessions/{created['session_id']}/start",
            json={"expected_revision": 1},
        )
        assert started.status_code == 200
    first_app.state.runtime_repository.close()

    second_repository = SQLiteRuntimeSessionRepository(path)
    second_app = create_app(
        runtime_repository=second_repository,
        wall_clock=clock.wall_now,
        monotonic_clock=clock.monotonic_now,
    )
    with TestClient(second_app) as client:
        listed = client.get(
            "/api/v1/runtime-sessions",
            params={"status": "paused"},
        )
        recovered = client.get(
            f"/api/v1/runtime-sessions/{created['session_id']}"
        )

    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert recovered.status_code == 200
    assert recovered.json()["status"] == "paused"
    assert recovered.json()["revision"] == 3
    assert recovered.json()["clock"]["simulation_time"] == created["clock"]["simulation_time"]


def test_runtime_api_replan_candidate_errors_and_decisions(tmp_path) -> None:
    app, _ = _build_app(tmp_path, session_id="RUN-API-M43")
    with TestClient(app) as client:
        plan_id = _create_plan(client)
        created = _create_runtime(client, plan_id)
        session_path = f"/api/v1/runtime-sessions/{created['session_id']}"
        awaiting = client.post(
            f"{session_path}/start",
            json={"expected_revision": created["revision"]},
        ).json()
        candidate_id = awaiting["candidate_plan_id"]

        mismatch = client.post(
            f"{session_path}/candidate/accept",
            json={
                "expected_revision": awaiting["revision"],
                "candidate_plan_id": "PLAN-WRONG-CANDIDATE",
            },
        )
        stale = client.post(
            f"{session_path}/candidate/reject",
            json={
                "expected_revision": awaiting["revision"] - 1,
                "candidate_plan_id": candidate_id,
            },
        )
        accepted = client.post(
            f"{session_path}/candidate/accept",
            json={
                "expected_revision": awaiting["revision"],
                "candidate_plan_id": candidate_id,
                "reason": "确认教学演示方案",
            },
        )
        manual = client.post(
            f"{session_path}/replan",
            json={
                "expected_revision": accepted.json()["revision"],
                "reason": "再次复核未来任务",
            },
        )
        rejected = client.post(
            f"{session_path}/candidate/reject",
            json={
                "expected_revision": manual.json()["revision"],
                "candidate_plan_id": manual.json()["candidate_plan_id"],
            },
        )

    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "runtime_candidate_mismatch"
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "runtime_revision_conflict"
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "paused"
    assert accepted.json()["active_plan_id"] == candidate_id
    assert manual.status_code == 202
    assert manual.json()["status"] == "awaiting_confirmation"
    assert manual.json()["candidate_plan_id"] != candidate_id
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "paused"
    assert rejected.json()["active_plan_id"] == candidate_id
