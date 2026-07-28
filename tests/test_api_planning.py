from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.audit_models import AuditAction
from backend.app.main import create_app


SCENARIO_ID = "SCN-TERMINAL-DISTURBANCE-01"
SCENARIO_PATH = f"/api/v1/scenarios/{SCENARIO_ID}"
SCENARIO_FILE = Path(__file__).parents[1] / "data" / "scenarios" / "terminal-disturbance-demo.json"


def _flight(record: dict, flight_id: str) -> dict:
    return next(
        flight
        for flight in record["scenario"]["flights"]
        if flight["flight_id"] == flight_id
    )


def _apply(client: TestClient, version: int, event_ids: list[str]) -> object:
    return client.post(
        f"{SCENARIO_PATH}/events/apply",
        json={"expected_version": version, "event_ids": event_ids, "events": []},
    )


def _create_plan(client: TestClient, version: int, algorithm: str) -> object:
    return client.post(
        f"{SCENARIO_PATH}/plans",
        json={
            "expected_version": version,
            "algorithm": algorithm,
            "max_time_seconds": 2,
        },
    )


def test_full_fifo_event_optimization_workflow_is_queryable_and_understandable() -> None:
    app = create_app()
    with TestClient(app) as client:
        fifo_response = _create_plan(client, version=1, algorithm="fifo")
        events_response = _apply(
            client,
            version=1,
            event_ids=["EVT-SIM102-DELAY", "EVT-SIM218-GATE"],
        )
        optimized_response = _create_plan(client, version=2, algorithm="cp_sat")
        plans_response = client.get(f"{SCENARIO_PATH}/plans")

    assert fifo_response.status_code == 201
    fifo_record = fifo_response.json()
    assert fifo_record["guidance"]["display_name"] == "原规则方案"
    assert fifo_record["plan"]["scenario_version"] == 1
    assert fifo_record["plan"]["metrics"]["assigned_tasks"] == 4
    assert fifo_record["plan"]["violations"] == []

    assert events_response.status_code == 200
    assert events_response.json()["current_version"] == 2
    assert events_response.json()["applied_event_ids"] == [
        "EVT-SIM102-DELAY",
        "EVT-SIM218-GATE",
    ]

    assert optimized_response.status_code == 201
    optimized_record = optimized_response.json()
    assert optimized_record["guidance"]["display_name"] == "系统优化建议"
    assert "全部 5 项任务已安排" in optimized_record["guidance"]["result_summary"]
    assert optimized_record["guidance"]["requires_human_confirmation"] is True
    assert optimized_record["plan"]["scenario_version"] == 2
    assert optimized_record["plan"]["metrics"]["assigned_tasks"] == 5
    assert optimized_record["plan"]["metrics"]["critical_task_completion_rate_pct"] == 100
    assert optimized_record["plan"]["violations"] == []
    assert "仅供教学仿真" in optimized_record["safety_notice"]

    assert plans_response.status_code == 200
    plans = plans_response.json()
    assert plans["total"] == 2
    assert [item["display_name"] for item in plans["items"]] == [
        "原规则方案",
        "系统优化建议",
    ]
    assert [record.action for record in app.state.scenario_repository.list_audit_records(SCENARIO_ID)] == [
        AuditAction.SCENARIO_IMPORTED,
        AuditAction.PLAN_CREATED,
        AuditAction.EVENTS_APPLIED,
        AuditAction.PLAN_CREATED,
    ]


def test_events_are_replayed_from_baseline_without_double_delay() -> None:
    with TestClient(create_app()) as client:
        delay_response = _apply(client, version=1, event_ids=["EVT-SIM102-DELAY"])
        gate_response = _apply(client, version=2, event_ids=["EVT-SIM218-GATE"])

    assert delay_response.status_code == gate_response.status_code == 200
    delayed_departure = datetime.fromisoformat(
        _flight(delay_response.json(), "FL-SIM102")["scheduled_departure"]
    )
    departure_after_second_batch = datetime.fromisoformat(
        _flight(gate_response.json(), "FL-SIM102")["scheduled_departure"]
    )
    assert delayed_departure.hour == 9 and delayed_departure.minute == 0
    assert departure_after_second_batch == delayed_departure
    assert gate_response.json()["current_version"] == 3


def test_new_structured_event_is_applied_and_original_catalog_remains_pending() -> None:
    new_event = {
        "event_id": "EVT-SIM102-EXTRA-DELAY",
        "event_type": "delay",
        "flight_id": "FL-SIM102",
        "occurred_at": "2026-08-01T08:03:00+08:00",
        "delay_minutes": 5,
    }
    with TestClient(create_app()) as client:
        response = client.post(
            f"{SCENARIO_PATH}/events/apply",
            json={"expected_version": 1, "event_ids": [], "events": [new_event]},
        )

    assert response.status_code == 200
    record = response.json()
    assert record["applied_event_ids"] == [new_event["event_id"]]
    assert record["pending_event_ids"] == ["EVT-SIM102-DELAY", "EVT-SIM218-GATE"]
    departure = datetime.fromisoformat(_flight(record, "FL-SIM102")["scheduled_departure"])
    assert departure.hour == 8 and departure.minute == 40


def test_stale_event_request_is_rejected_without_changing_state() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = _apply(client, version=2, event_ids=["EVT-SIM102-DELAY"])
        current = client.get(SCENARIO_PATH)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "version_conflict"
    assert "版本 1" in response.json()["error"]["message"]
    assert current.json()["current_version"] == 1
    assert current.json()["applied_event_ids"] == []
    assert len(app.state.scenario_repository.list_audit_records(SCENARIO_ID)) == 1


def test_unknown_and_repeated_events_return_actionable_errors() -> None:
    with TestClient(create_app()) as client:
        missing = _apply(client, version=1, event_ids=["EVT-MISSING"])
        first = _apply(client, version=1, event_ids=["EVT-SIM102-DELAY"])
        repeated = _apply(client, version=2, event_ids=["EVT-SIM102-DELAY"])

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "event_not_found"
    assert "pending_event_ids" in missing.json()["error"]["message"]
    assert first.status_code == 200
    assert repeated.status_code == 409
    assert repeated.json()["error"]["code"] == "event_already_applied"


def test_inconsistent_gate_change_is_rejected_as_one_atomic_batch() -> None:
    bad_event = {
        "event_id": "EVT-SIM218-BAD-GATE",
        "event_type": "gate_change",
        "flight_id": "FL-SIM218",
        "occurred_at": "2026-08-01T08:04:00+08:00",
        "previous_gate_id": "GATE-E01",
        "new_gate_id": "GATE-W03",
    }
    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            f"{SCENARIO_PATH}/events/apply",
            json={"expected_version": 1, "event_ids": [], "events": [bad_event]},
        )
        current = client.get(SCENARIO_PATH)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "event_batch_not_applicable"
    assert current.json()["current_version"] == 1
    assert current.json()["applied_event_ids"] == []
    assert len(app.state.scenario_repository.list_audit_records(SCENARIO_ID)) == 1


def test_new_event_for_unknown_flight_returns_safe_business_error() -> None:
    bad_event = {
        "event_id": "EVT-UNKNOWN-FLIGHT-DELAY",
        "event_type": "delay",
        "flight_id": "FL-MISSING",
        "occurred_at": "2026-08-01T08:04:00+08:00",
        "delay_minutes": 5,
    }
    with TestClient(create_app()) as client:
        response = client.post(
            f"{SCENARIO_PATH}/events/apply",
            json={"expected_version": 1, "event_ids": [], "events": [bad_event]},
        )
        current = client.get(SCENARIO_PATH)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "event_batch_not_applicable"
    assert "FL-MISSING" not in response.text
    assert current.json()["current_version"] == 1


def test_duplicate_plan_is_immutable_and_existing_plan_remains_queryable() -> None:
    app = create_app()
    with TestClient(app) as client:
        created = _create_plan(client, version=1, algorithm="fifo")
        duplicate = _create_plan(client, version=1, algorithm="fifo")
        plan_id = created.json()["plan"]["plan_id"]
        fetched = client.get(f"/api/v1/plans/{plan_id}")

    assert created.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "plan_already_exists"
    assert fetched.status_code == 200
    assert fetched.json() == created.json()
    assert len(app.state.scenario_repository.list_audit_records(SCENARIO_ID)) == 2


def test_plan_creation_rejects_stale_version_and_invalid_algorithm() -> None:
    app = create_app()
    with TestClient(app) as client:
        stale = _create_plan(client, version=2, algorithm="fifo")
        invalid = _create_plan(client, version=1, algorithm="chat_answer")
        plans = client.get(f"{SCENARIO_PATH}/plans")

    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"
    assert plans.json()["total"] == 0
    assert len(app.state.scenario_repository.list_audit_records(SCENARIO_ID)) == 1


def test_plan_creation_rejects_scenario_without_tasks_or_resources() -> None:
    payload = json.loads(SCENARIO_FILE.read_text(encoding="utf-8"))
    payload["scenario_id"] = "SCN-NOT-READY"
    payload["name"] = "待补全场景"
    payload["tasks"] = []
    payload["resources"] = []
    with TestClient(create_app(seed_demo=False)) as client:
        assert client.post("/api/v1/scenarios", json=payload).status_code == 201
        response = client.post(
            "/api/v1/scenarios/SCN-NOT-READY/plans",
            json={"expected_version": 1, "algorithm": "fifo", "max_time_seconds": 2},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "scenario_not_ready_for_planning"
    assert "没有保障任务" in response.json()["error"]["message"]


def test_plan_list_filters_by_version_and_plain_algorithm_name() -> None:
    with TestClient(create_app()) as client:
        fifo = _create_plan(client, version=1, algorithm="fifo")
        assert fifo.status_code == 201
        assert _apply(client, version=1, event_ids=["EVT-SIM102-DELAY"]).status_code == 200
        optimized = _create_plan(client, version=2, algorithm="cp_sat")
        assert optimized.status_code == 201

        version_one = client.get(f"{SCENARIO_PATH}/plans", params={"version": 1})
        cp_sat = client.get(f"{SCENARIO_PATH}/plans", params={"algorithm": "cp_sat"})

    assert version_one.status_code == cp_sat.status_code == 200
    assert version_one.json()["total"] == 1
    assert version_one.json()["items"][0]["algorithm"] == "fifo"
    assert cp_sat.json()["total"] == 1
    assert cp_sat.json()["items"][0]["algorithm"] == "cp_sat"

    with TestClient(create_app()) as client:
        missing_version = client.get(f"{SCENARIO_PATH}/plans", params={"version": 99})
    assert missing_version.status_code == 404
    assert missing_version.json()["error"]["code"] == "scenario_version_not_found"


def test_missing_plan_and_empty_event_batch_have_clear_contracts() -> None:
    with TestClient(create_app()) as client:
        missing_plan = client.get("/api/v1/plans/PLAN-MISSING")
        empty_batch = client.post(
            f"{SCENARIO_PATH}/events/apply",
            json={"expected_version": 1, "event_ids": [], "events": []},
        )

    assert missing_plan.status_code == 404
    assert missing_plan.json()["error"]["code"] == "plan_not_found"
    assert "方案列表" in missing_plan.json()["error"]["message"]
    assert empty_batch.status_code == 422
    assert empty_batch.json()["error"]["code"] == "validation_error"


def test_openapi_exposes_structured_events_and_plans_without_ai_dependency() -> None:
    with TestClient(create_app()) as client:
        paths = client.get("/api/v1/openapi.json").json()["paths"]

    assert "/api/v1/scenarios/{scenario_id}/events/apply" in paths
    assert "/api/v1/scenarios/{scenario_id}/plans" in paths
    assert "/api/v1/plans/{plan_id}" in paths
