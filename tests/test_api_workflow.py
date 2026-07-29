from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.demo_export import SAFETY_NOTICE
from backend.app.main import create_app


SCENARIO_ID = "SCN-TERMINAL-DISTURBANCE-01"
SCENARIO_PATH = f"/api/v1/scenarios/{SCENARIO_ID}"
SCENARIO_FILE = Path(__file__).parents[1] / "data" / "scenarios" / "terminal-disturbance-demo.json"
STATIC_DEMO_FILE = Path(__file__).parents[1] / "frontend" / "public" / "demo-output.json"


def _create_plan(client: TestClient, scenario_id: str, version: int, algorithm: str) -> dict:
    response = client.post(
        f"/api/v1/scenarios/{scenario_id}/plans",
        json={
            "expected_version": version,
            "algorithm": algorithm,
            "max_time_seconds": 2,
        },
    )
    assert response.status_code == 201
    return response.json()


def _build_comparable_plans(client: TestClient) -> tuple[str, str]:
    baseline = _create_plan(client, SCENARIO_ID, version=1, algorithm="fifo")
    events = client.post(
        f"{SCENARIO_PATH}/events/apply",
        json={
            "expected_version": 1,
            "event_ids": ["EVT-SIM102-DELAY", "EVT-SIM218-GATE"],
            "events": [],
        },
    )
    assert events.status_code == 200
    candidate = _create_plan(client, SCENARIO_ID, version=2, algorithm="cp_sat")
    return baseline["plan"]["plan_id"], candidate["plan"]["plan_id"]


def _compare(client: TestClient, baseline_plan_id: str, candidate_plan_id: str):
    return client.post(
        f"{SCENARIO_PATH}/comparisons",
        json={
            "baseline_plan_id": baseline_plan_id,
            "candidate_plan_id": candidate_plan_id,
        },
    )


def test_full_workflow_compares_saved_plans_without_mutating_state() -> None:
    app = create_app()
    with TestClient(app) as client:
        baseline_plan_id, candidate_plan_id = _build_comparable_plans(client)
        scenario_before = client.get(SCENARIO_PATH).json()
        plans_before = client.get(f"{SCENARIO_PATH}/plans").json()

        response = _compare(client, baseline_plan_id, candidate_plan_id)

        scenario_after = client.get(SCENARIO_PATH).json()
        plans_after = client.get(f"{SCENARIO_PATH}/plans").json()
        audit = client.get(f"{SCENARIO_PATH}/audit-records").json()

    assert response.status_code == 201
    comparison = response.json()
    assert comparison["baseline_plan_id"] == baseline_plan_id
    assert comparison["candidate_plan_id"] == candidate_plan_id
    assert comparison["baseline_metrics"]["scenario_version"] == 1
    assert comparison["candidate_metrics"]["scenario_version"] == 2
    assert comparison["baseline_metrics"]["assigned_tasks"] == 4
    assert comparison["candidate_metrics"]["assigned_tasks"] == 5
    assert comparison["baseline_metrics"]["violation_count"] == 0
    assert comparison["candidate_metrics"]["violation_count"] == 0
    assert comparison["requires_human_confirmation"] is True
    assert comparison["safety_notice"] == SAFETY_NOTICE
    assert "候选方案综合优先" in comparison["conclusion"]

    assert scenario_after == scenario_before
    assert plans_after == plans_before
    assert [record["action"] for record in audit] == [
        "scenario_imported",
        "plan_created",
        "events_applied",
        "plan_created",
        "comparison_created",
    ]


def test_comparison_returns_candidate_minus_baseline_deltas() -> None:
    with TestClient(create_app()) as client:
        baseline_plan_id, candidate_plan_id = _build_comparable_plans(client)
        response = _compare(client, baseline_plan_id, candidate_plan_id)

    assert response.status_code == 201
    delta = response.json()["candidate_minus_baseline"]
    assert delta == {
        "assigned_tasks": 1,
        "unassigned_tasks": -1,
        "total_tasks": 0,
        "task_completion_rate_pct": 20.0,
        "critical_task_completion_rate_pct": 50.0,
        "average_wait_minutes": 6.35,
        "max_wait_minutes": 20,
        "overall_resource_utilization_pct": 5.83,
        "violation_count": 0,
    }


def test_missing_and_cross_scenario_plans_are_rejected_without_audit() -> None:
    app = create_app()
    with TestClient(app) as client:
        baseline = _create_plan(client, SCENARIO_ID, version=1, algorithm="fifo")
        baseline_plan_id = baseline["plan"]["plan_id"]
        audit_count = len(client.get(f"{SCENARIO_PATH}/audit-records").json())

        missing_baseline = _compare(client, "PLAN-MISSING", baseline_plan_id)
        missing_candidate = _compare(client, baseline_plan_id, "PLAN-MISSING")

        second_payload = json.loads(SCENARIO_FILE.read_text(encoding="utf-8"))
        second_payload["scenario_id"] = "SCN-SECOND-DEMO"
        second_payload["name"] = "第二仿真场景"
        assert client.post("/api/v1/scenarios", json=second_payload).status_code == 201
        second = _create_plan(client, "SCN-SECOND-DEMO", version=1, algorithm="fifo")
        cross_scenario = _compare(
            client,
            baseline_plan_id,
            second["plan"]["plan_id"],
        )
        audit_after = client.get(f"{SCENARIO_PATH}/audit-records").json()

    assert missing_baseline.status_code == missing_candidate.status_code == 404
    assert missing_baseline.json()["error"]["code"] == "plan_not_found"
    assert missing_candidate.json()["error"]["code"] == "plan_not_found"
    assert cross_scenario.status_code == 400
    assert cross_scenario.json()["error"]["code"] == "plan_comparison_not_allowed"
    assert len(audit_after) == audit_count


def test_audit_timeline_is_bounded_ordered_sanitized_and_defensive() -> None:
    app = create_app()
    with TestClient(app) as client:
        baseline_plan_id, candidate_plan_id = _build_comparable_plans(client)
        assert _compare(client, baseline_plan_id, candidate_plan_id).status_code == 201

        full = client.get(f"{SCENARIO_PATH}/audit-records")
        recent = client.get(f"{SCENARIO_PATH}/audit-records", params={"limit": 2})
        too_small = client.get(f"{SCENARIO_PATH}/audit-records", params={"limit": 0})
        too_large = client.get(f"{SCENARIO_PATH}/audit-records", params={"limit": 101})

        copied_records = app.state.scenario_repository.list_audit_records(SCENARIO_ID)
        copied_records[0].summary = "外部修改"
        after_mutation = client.get(f"{SCENARIO_PATH}/audit-records").json()

    assert full.status_code == recent.status_code == 200
    records = full.json()
    assert [record["audit_id"] for record in records] == [
        "AUDIT-000001",
        "AUDIT-000002",
        "AUDIT-000003",
        "AUDIT-000004",
        "AUDIT-000005",
    ]
    assert [record["action"] for record in recent.json()] == [
        "plan_created",
        "comparison_created",
    ]
    assert too_small.status_code == too_large.status_code == 422
    assert after_mutation[0]["summary"] == "场景已导入"

    public_text = json.dumps(records, ensure_ascii=False)
    for forbidden in ("C:\\\\", "E:\\\\", "request", "姓名", "证件", "电话"):
        assert forbidden not in public_text


def test_audit_missing_scenario_and_same_plan_have_unified_errors() -> None:
    with TestClient(create_app()) as client:
        missing = client.get("/api/v1/scenarios/SCN-MISSING/audit-records")
        plan = _create_plan(client, SCENARIO_ID, version=1, algorithm="fifo")
        plan_id = plan["plan"]["plan_id"]
        same_plan = _compare(client, plan_id, plan_id)

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "scenario_not_found"
    assert same_plan.status_code == 422
    assert same_plan.json()["error"]["code"] == "validation_error"


def test_demo_endpoint_is_exactly_compatible_with_static_payload() -> None:
    expected = json.loads(STATIC_DEMO_FILE.read_text(encoding="utf-8"))
    with TestClient(create_app(seed_demo=False)) as client:
        response = client.get("/api/v1/demo")
        paths = client.get("/api/v1/openapi.json").json()["paths"]

    assert response.status_code == 200
    assert response.json() == expected
    assert response.json()["project"]["safety_notice"] == SAFETY_NOTICE
    views = response.json()["views"]
    assert set(views) == {"baseline", "after_events_fifo", "optimized"}
    assert views["after_events_fifo"]["plan"]["metrics"]["assigned_tasks"] == 4
    assert views["optimized"]["plan"]["metrics"]["assigned_tasks"] == 5
    assert (
        views["after_events_fifo"]["plan"]["metrics"]["critical_task_completion_rate_pct"]
        == 50
    )
    assert views["optimized"]["plan"]["metrics"]["critical_task_completion_rate_pct"] == 100
    assert all(not view["plan"]["violations"] for view in views.values())
    assert "/api/v1/demo" in paths
    assert "/api/v1/scenarios/{scenario_id}/comparisons" in paths
    assert "/api/v1/scenarios/{scenario_id}/audit-records" in paths
