from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.fifo_scheduler import build_fifo_plan
from backend.app.main import create_app
from backend.app.optimizer import build_optimized_plan
from backend.app.planning_objectives import PlanningObjectiveProfile
from backend.app.runtime_repository import SQLiteRuntimeSessionRepository
from backend.app.runtime_projection import RuntimeProjectionSource
from backend.app.scenario_loader import load_scenario
from backend.app.demo_export import DEMO_SCENARIO_PATH


SCENARIO_ID = "SCN-TERMINAL-DISTURBANCE-01"
SCENARIO_PATH = f"/api/v1/scenarios/{SCENARIO_ID}"


def test_balanced_objective_is_exactly_the_existing_optimizer_default() -> None:
    scenario = load_scenario(DEMO_SCENARIO_PATH)

    default_plan = build_optimized_plan(scenario)
    explicit_plan = build_optimized_plan(
        scenario,
        objective_profile=PlanningObjectiveProfile.BALANCED,
    )

    assert explicit_plan == default_plan


def test_explicit_static_objectives_require_confirmation_and_remain_finite() -> None:
    app = create_app(
        runtime_repository=SQLiteRuntimeSessionRepository(":memory:"),
    )
    with TestClient(app) as client:
        unconfirmed = client.post(
            f"{SCENARIO_PATH}/plans",
            json={
                "expected_version": 1,
                "algorithm": "cp_sat",
                "objective_profile": "critical_first",
            },
        )
        balanced = client.post(
            f"{SCENARIO_PATH}/plans",
            json={"expected_version": 1, "algorithm": "cp_sat"},
        )
        critical = client.post(
            f"{SCENARIO_PATH}/plans",
            json={
                "expected_version": 1,
                "algorithm": "cp_sat",
                "objective_profile": "critical_first",
                "confirm_objective": True,
            },
        )
        minimum_wait = client.post(
            f"{SCENARIO_PATH}/plans",
            json={
                "expected_version": 1,
                "algorithm": "cp_sat",
                "objective_profile": "minimum_wait",
                "confirm_objective": True,
            },
        )
        minimum_change = client.post(
            f"{SCENARIO_PATH}/plans",
            json={
                "expected_version": 1,
                "algorithm": "cp_sat",
                "objective_profile": "minimum_change",
                "confirm_objective": True,
            },
        )
        listed = client.get(
            f"{SCENARIO_PATH}/plans",
            params={"algorithm": "cp_sat"},
        )

    assert unconfirmed.status_code == 422
    assert unconfirmed.json()["error"]["code"] == "validation_error"
    assert balanced.status_code == critical.status_code == minimum_wait.status_code == 201
    balanced_plan = balanced.json()["plan"]
    assert balanced_plan["objective_profile"] == "balanced"
    assert balanced_plan["algorithm"] == "cp_sat_priority_v1"
    assert balanced_plan["plan_id"].endswith("-CP-SAT")
    assert balanced.json()["guidance"]["display_name"] == "系统优化建议"
    assert critical.json()["plan"]["objective_profile"] == "critical_first"
    assert critical.json()["plan"]["plan_id"].endswith("-CRITICAL-FIRST")
    assert minimum_wait.json()["plan"]["objective_profile"] == "minimum_wait"
    assert minimum_wait.json()["plan"]["plan_id"].endswith("-MINIMUM-WAIT")
    assert all(
        response.json()["plan"]["violations"] == []
        for response in (balanced, critical, minimum_wait)
    )
    assert minimum_change.status_code == 400
    assert minimum_change.json()["error"]["code"] == (
        "planning_objective_not_supported"
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 3
    assert {item["objective_profile"] for item in listed.json()["items"]} == {
        "balanced",
        "critical_first",
        "minimum_wait",
    }


def test_minimum_change_requires_and_uses_a_deterministic_baseline() -> None:
    scenario = load_scenario(DEMO_SCENARIO_PATH)
    baseline = build_fifo_plan(scenario)

    first = build_optimized_plan(
        scenario,
        objective_profile=PlanningObjectiveProfile.MINIMUM_CHANGE,
        baseline_plan=baseline,
    )
    second = build_optimized_plan(
        scenario,
        objective_profile=PlanningObjectiveProfile.MINIMUM_CHANGE,
        baseline_plan=baseline,
    )

    assert first == second
    assert first.objective_profile is PlanningObjectiveProfile.MINIMUM_CHANGE
    assert first.plan_id.endswith("-MINIMUM-CHANGE")
    assert first.violations == []


def test_runtime_replan_applies_confirmed_minimum_change_without_auto_adoption() -> None:
    runtime_repository = SQLiteRuntimeSessionRepository(":memory:")
    app = create_app(
        runtime_repository=runtime_repository,
        runtime_session_id_factory=lambda: "RUN-M53-OBJECTIVE",
    )
    with TestClient(app) as client:
        plan = client.post(
            f"{SCENARIO_PATH}/plans",
            json={"expected_version": 1, "algorithm": "fifo"},
        ).json()["plan"]
        created = client.post(
            "/api/v1/runtime-sessions",
            json={
                "scenario_id": SCENARIO_ID,
                "scenario_version": 1,
                "active_plan_id": plan["plan_id"],
                "speed": 1,
            },
        ).json()
        first_candidate = client.post(
            f"/api/v1/runtime-sessions/{created['session_id']}/start",
            json={"expected_revision": created["revision"]},
        ).json()
        paused = client.post(
            f"/api/v1/runtime-sessions/{created['session_id']}/candidate/reject",
            json={
                "expected_revision": first_candidate["revision"],
                "candidate_plan_id": first_candidate["candidate_plan_id"],
            },
        ).json()
        unconfirmed = client.post(
            f"/api/v1/runtime-sessions/{created['session_id']}/replan",
            json={
                "expected_revision": paused["revision"],
                "objective_profile": "minimum_change",
            },
        )
        candidate = client.post(
            f"/api/v1/runtime-sessions/{created['session_id']}/replan",
            json={
                "expected_revision": paused["revision"],
                "objective_profile": "minimum_change",
                "confirm_objective": True,
                "reason": "尽量保留当前资源安排",
            },
        )

    assert unconfirmed.status_code == 422
    assert candidate.status_code == 202
    payload = candidate.json()
    assert payload["status"] == "awaiting_confirmation"
    assert payload["objective_profile"] == "minimum_change"
    assert payload["active_plan_id"] == paused["active_plan_id"]
    assert payload["candidate_plan_id"] != payload["active_plan_id"]
    assert payload["candidate_plan_detail"]["objective_profile"] == "minimum_change"
    assert payload["candidate_plan_detail"]["violations"] == []


def test_legacy_runtime_projection_defaults_to_balanced_objective() -> None:
    scenario = load_scenario(DEMO_SCENARIO_PATH)
    scenario_payload = scenario.model_dump(mode="python")
    scenario_payload["events"] = []
    scenario = type(scenario).model_validate(scenario_payload)
    payload = {
        "scenario": scenario,
        "plan": build_fifo_plan(scenario),
    }

    restored = RuntimeProjectionSource.model_validate(payload)

    assert restored.objective_profile is PlanningObjectiveProfile.BALANCED
