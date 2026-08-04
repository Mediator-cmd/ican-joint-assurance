from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import perf_counter

from fastapi.testclient import TestClient

from backend.app.demo_export import SAFETY_NOTICE
from backend.app.main import create_app
from backend.app.runtime_repository import SQLiteRuntimeSessionRepository


SCENARIO_ID = "SCN-TERMINAL-DISTURBANCE-01"
SESSION_ID = "RUN-M46-ACCEPTANCE"
EVENT_MINUTES = [0, 4, 16, 28, 40, 52]


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
    clock = FakeClock(
        datetime(2026, 8, 3, 7, 55, tzinfo=timezone(timedelta(hours=8)))
    )
    repository = SQLiteRuntimeSessionRepository(tmp_path / "m4-acceptance.sqlite3")
    app = create_app(
        runtime_repository=repository,
        wall_clock=clock.wall_now,
        monotonic_clock=clock.monotonic_now,
        runtime_session_id_factory=lambda: SESSION_ID,
    )
    return app, repository, clock


def _create_runtime(client: TestClient) -> dict:
    plan_response = client.post(
        f"/api/v1/scenarios/{SCENARIO_ID}/plans",
        json={
            "expected_version": 1,
            "algorithm": "cp_sat",
            "max_time_seconds": 2,
        },
    )
    assert plan_response.status_code == 201
    plan = plan_response.json()["plan"]
    assert plan["violations"] == []

    runtime_response = client.post(
        "/api/v1/runtime-sessions",
        json={
            "scenario_id": SCENARIO_ID,
            "scenario_version": 1,
            "active_plan_id": plan["plan_id"],
            "speed": 15,
        },
    )
    assert runtime_response.status_code == 201
    return runtime_response.json()


def _assignment_map(plan: dict | None) -> dict[str, dict]:
    if plan is None:
        return {}
    return {assignment["task_id"]: assignment for assignment in plan["assignments"]}


def _collect_projection_states(
    snapshot: dict,
    task_states: dict[str, set[str]],
    resource_states: dict[str, set[str]],
) -> None:
    for task in snapshot["tasks"]:
        task_states.setdefault(task["task_id"], set()).add(task["status"])
    for resource in snapshot["resources"]:
        resource_states.setdefault(resource["resource_id"], set()).add(resource["status"])


def _assert_awaiting_snapshot(snapshot: dict, event_index: int) -> None:
    simulation_time = datetime.fromisoformat(snapshot["clock"]["simulation_time"])
    assert snapshot["status"] == "awaiting_confirmation"
    assert simulation_time.hour == 8
    assert simulation_time.minute == EVENT_MINUTES[event_index]
    assert snapshot["current_scenario_version"] == event_index + 2
    assert snapshot["candidate_plan_id"] is not None
    assert snapshot["candidate_plan_detail"] is not None
    assert snapshot["candidate_plan_detail"]["violations"] == []
    assert snapshot["active_plan_detail"] is not None

    locked_task_ids = {
        task["task_id"] for task in snapshot["tasks"] if task["is_locked"]
    }
    active_assignments = _assignment_map(snapshot["active_plan_detail"])
    candidate_assignments = _assignment_map(snapshot["candidate_plan_detail"])
    for task_id in locked_task_ids:
        assert candidate_assignments.get(task_id) == active_assignments.get(task_id)


def test_m4_three_complete_rest_rounds_preserve_runtime_invariants(tmp_path) -> None:
    app, repository, clock = _build_app(tmp_path)
    decision_patterns = [
        ["accept"] * 6,
        ["accept", "reject", "accept", "reject", "accept", "reject"],
        ["accept"] * 6,
    ]
    all_candidate_ids: list[str] = []
    replan_durations: list[float] = []
    task_states: dict[str, set[str]] = {}
    resource_states: dict[str, set[str]] = {}

    with TestClient(app) as client:
        snapshot = _create_runtime(client)
        assert snapshot["status"] == "ready"
        assert snapshot["safety_notice"] == SAFETY_NOTICE
        assert len(snapshot["events"]) == 6

        for round_index, decisions in enumerate(decision_patterns):
            if round_index > 0:
                reset_response = client.post(
                    f"/api/v1/runtime-sessions/{SESSION_ID}/reset",
                    json={
                        "expected_revision": snapshot["revision"],
                        "confirm_reset": True,
                    },
                )
                assert reset_response.status_code == 200
                snapshot = reset_response.json()
                assert snapshot["status"] == "ready"
                assert snapshot["current_scenario_version"] == 1
                assert {event["status"] for event in snapshot["events"]} == {"pending"}

            round_started = perf_counter()
            event_started = perf_counter()
            start_response = client.post(
                f"/api/v1/runtime-sessions/{SESSION_ID}/start",
                json={"expected_revision": snapshot["revision"]},
            )
            replan_durations.append(perf_counter() - event_started)
            assert start_response.status_code == 200
            snapshot = start_response.json()

            for event_index, decision in enumerate(decisions):
                _assert_awaiting_snapshot(snapshot, event_index)
                _collect_projection_states(snapshot, task_states, resource_states)
                candidate_id = snapshot["candidate_plan_id"]
                assert candidate_id is not None
                all_candidate_ids.append(candidate_id)

                if round_index == 2 and event_index == 0:
                    before_error = client.get(
                        f"/api/v1/runtime-sessions/{SESSION_ID}"
                    ).json()
                    wrong_candidate = client.post(
                        f"/api/v1/runtime-sessions/{SESSION_ID}/candidate/accept",
                        json={
                            "expected_revision": snapshot["revision"],
                            "candidate_plan_id": "PLAN-WRONG-CANDIDATE",
                        },
                    )
                    stale_revision = client.post(
                        f"/api/v1/runtime-sessions/{SESSION_ID}/candidate/reject",
                        json={
                            "expected_revision": snapshot["revision"] - 1,
                            "candidate_plan_id": candidate_id,
                        },
                    )
                    duplicate_start = client.post(
                        f"/api/v1/runtime-sessions/{SESSION_ID}/start",
                        json={"expected_revision": snapshot["revision"]},
                    )
                    after_error = client.get(
                        f"/api/v1/runtime-sessions/{SESSION_ID}"
                    ).json()
                    assert wrong_candidate.status_code == 409
                    assert wrong_candidate.json()["error"]["code"] == "runtime_candidate_mismatch"
                    assert stale_revision.status_code == 409
                    assert stale_revision.json()["error"]["code"] == "runtime_revision_conflict"
                    assert duplicate_start.status_code == 409
                    assert duplicate_start.json()["error"]["code"] == "runtime_invalid_transition"
                    assert after_error == before_error

                decision_response = client.post(
                    f"/api/v1/runtime-sessions/{SESSION_ID}/candidate/{decision}",
                    json={
                        "expected_revision": snapshot["revision"],
                        "candidate_plan_id": candidate_id,
                        "reason": f"M4-6 第 {round_index + 1} 轮验收",
                    },
                )
                assert decision_response.status_code == 200
                snapshot = decision_response.json()
                assert snapshot["status"] == "paused"
                assert snapshot["candidate_plan_id"] is None

                if event_index == len(EVENT_MINUTES) - 1:
                    break

                running_response = client.post(
                    f"/api/v1/runtime-sessions/{SESSION_ID}/start",
                    json={"expected_revision": snapshot["revision"]},
                )
                assert running_response.status_code == 200
                assert running_response.json()["status"] == "running"
                next_minute = EVENT_MINUTES[event_index + 1]
                clock.advance((next_minute - EVENT_MINUTES[event_index]) * 60 / 15)
                event_started = perf_counter()
                snapshot_response = client.get(
                    f"/api/v1/runtime-sessions/{SESSION_ID}"
                )
                replan_durations.append(perf_counter() - event_started)
                assert snapshot_response.status_code == 200
                snapshot = snapshot_response.json()

            final_start = client.post(
                f"/api/v1/runtime-sessions/{SESSION_ID}/start",
                json={"expected_revision": snapshot["revision"]},
            )
            assert final_start.status_code == 200
            assert final_start.json()["status"] == "running"
            clock.advance((90 - EVENT_MINUTES[-1]) * 60 / 15)
            completed_response = client.get(
                f"/api/v1/runtime-sessions/{SESSION_ID}"
            )
            assert completed_response.status_code == 200
            snapshot = completed_response.json()
            _collect_projection_states(snapshot, task_states, resource_states)

            assert snapshot["status"] == "completed"
            assert datetime.fromisoformat(snapshot["clock"]["simulation_time"]).strftime(
                "%H:%M"
            ) == "09:30"
            assert snapshot["current_scenario_version"] == 7
            assert snapshot["candidate_plan_id"] is None
            assert {event["status"] for event in snapshot["events"]} == {"resolved"}
            assert len({event["event_id"] for event in snapshot["events"]}) == 6
            assert snapshot["active_plan_detail"] is not None
            assert snapshot["active_plan_detail"]["violations"] == []
            assert perf_counter() - round_started < 12

        final_snapshot = client.get(
            f"/api/v1/runtime-sessions/{SESSION_ID}"
        ).json()
        assert final_snapshot["clock"]["simulation_time"] == snapshot["clock"]["simulation_time"]

    assert len(all_candidate_ids) == 18
    assert len(set(all_candidate_ids)) == 18
    assert replan_durations
    assert max(replan_durations) < 2
    assert any(len(states) >= 3 for states in task_states.values())
    assert any(len(states) >= 3 for states in resource_states.values())

    audit_actions = [
        record.action for record in repository.list_audit_records(SESSION_ID)
    ]
    assert audit_actions.count("event_batch_applied") == 18
    assert audit_actions.count("candidate_created") == 18
    assert audit_actions.count("runtime_completed") == 3
    assert audit_actions.count("runtime_reset") == 2


def test_m4_two_clients_observe_one_authoritative_snapshot(tmp_path) -> None:
    app, _, _ = _build_app(tmp_path)
    with TestClient(app) as first_client, TestClient(app) as second_client:
        created = _create_runtime(first_client)
        awaiting_response = first_client.post(
            f"/api/v1/runtime-sessions/{SESSION_ID}/start",
            json={"expected_revision": created["revision"]},
        )
        assert awaiting_response.status_code == 200
        awaiting = awaiting_response.json()

        first_view = first_client.get(
            f"/api/v1/runtime-sessions/{SESSION_ID}"
        ).json()
        second_view = second_client.get(
            f"/api/v1/runtime-sessions/{SESSION_ID}"
        ).json()
        assert second_view == first_view == awaiting

        accepted_response = second_client.post(
            f"/api/v1/runtime-sessions/{SESSION_ID}/candidate/accept",
            json={
                "expected_revision": awaiting["revision"],
                "candidate_plan_id": awaiting["candidate_plan_id"],
            },
        )
        assert accepted_response.status_code == 200
        accepted = accepted_response.json()
        refreshed = first_client.get(
            f"/api/v1/runtime-sessions/{SESSION_ID}"
        ).json()
        assert refreshed == accepted
        assert refreshed["status"] == "paused"
        assert refreshed["active_plan_id"] == awaiting["candidate_plan_id"]
