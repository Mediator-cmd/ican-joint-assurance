from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.constraints import validate_plan
from backend.app.events import apply_events
from backend.app.fifo_scheduler import build_fifo_plan
from backend.app.models import Scenario
from backend.app.planning_models import ConstraintCode, Plan, PlanStatus, UnassignedReason
from backend.app.scenario_loader import load_scenario
from backend.app.travel import shortest_travel_minutes


SCENARIO_PATH = Path(__file__).parents[1] / "data" / "scenarios" / "terminal-disturbance-demo.json"


def load_demo() -> Scenario:
    return load_scenario(SCENARIO_PATH)


def test_fifo_plan_is_safe_partial_and_deterministic() -> None:
    scenario = load_demo()

    first = build_fifo_plan(scenario)
    second = build_fifo_plan(scenario)

    assert first == second
    assert first.status is PlanStatus.PARTIAL
    assert first.violations == []
    assert len(first.assignments) == 9
    assert [item.task_id for item in first.unassigned_tasks] == ["TASK-005"]
    assert first.metrics.task_completion_rate_pct == 90
    assert first.metrics.critical_task_completion_rate_pct == 75


def test_fifo_resource_selection_is_stable() -> None:
    plan = build_fifo_plan(load_demo())
    resources_by_task = {
        assignment.task_id: assignment.resource_id for assignment in plan.assignments
    }

    assert resources_by_task == {
        "TASK-001": "WC-01",
        "TASK-002": "AGENT-01",
        "TASK-003": "BUS-01",
        "TASK-004": "WC-01",
        "TASK-006": "AGENT-02",
        "TASK-007": "BUS-01",
        "TASK-008": "WC-01",
        "TASK-009": "AGENT-01",
        "TASK-010": "WC-02",
    }


def test_travel_time_uses_shortest_connected_route() -> None:
    scenario = load_demo()

    assert shortest_travel_minutes(scenario, "GATE-E01", "GATE-W03") == 20


def test_events_create_new_version_without_mutating_baseline() -> None:
    baseline = load_demo()
    updated = apply_events(baseline)

    baseline_flights = {flight.flight_id: flight for flight in baseline.flights}
    updated_flights = {flight.flight_id: flight for flight in updated.flights}
    baseline_tasks = {task.task_id: task for task in baseline.tasks}
    updated_tasks = {task.task_id: task for task in updated.tasks}

    assert baseline.version == 1
    assert updated.version == 2
    assert baseline_flights["FL-SIM102"].scheduled_departure.hour == 8
    assert updated_flights["FL-SIM102"].scheduled_departure.hour == 9
    assert baseline_flights["FL-SIM218"].gate_id == "GATE-W03"
    assert updated_flights["FL-SIM218"].gate_id == "GATE-W03"
    assert updated_flights["FL-SIM218"].scheduled_departure.minute == 5
    assert updated_flights["FL-SIM330"].gate_id == "GATE-W03"
    assert baseline_tasks["TASK-002"].destination_zone_id == "GATE-W03"
    assert updated_tasks["TASK-002"].destination_zone_id == "GATE-W03"
    assert updated_tasks["TASK-006"].destination_zone_id == "GATE-W03"
    assert updated_tasks["TASK-001"].deadline_at == baseline_tasks["TASK-001"].deadline_at + timedelta(minutes=25)


def test_event_updated_fifo_scenario_still_produces_safe_partial_plan() -> None:
    plan = build_fifo_plan(apply_events(load_demo()))

    assert plan.status is PlanStatus.PARTIAL
    assert plan.violations == []
    assert [item.task_id for item in plan.unassigned_tasks] == ["TASK-005"]


def test_resource_shortage_is_explicitly_unassigned() -> None:
    payload = load_demo().model_dump()
    payload["resources"] = [
        resource for resource in payload["resources"] if resource["resource_type"] != "wheelchair"
    ]
    scenario = Scenario.model_validate(payload)

    plan = build_fifo_plan(scenario)

    assert plan.status is PlanStatus.PARTIAL
    assert {item.task_id for item in plan.unassigned_tasks} == {
        "TASK-001",
        "TASK-004",
        "TASK-005",
        "TASK-008",
        "TASK-010",
    }
    assert {item.reason for item in plan.unassigned_tasks} == {
        UnassignedReason.NO_COMPATIBLE_RESOURCE
    }
    assert plan.violations == []


def test_time_window_conflict_is_explicitly_unassigned() -> None:
    payload = load_demo().model_dump()
    payload["tasks"][0]["deadline_at"] = payload["tasks"][0]["release_at"] + timedelta(minutes=5)
    scenario = Scenario.model_validate(payload)

    plan = build_fifo_plan(scenario)

    unassigned = {item.task_id: item.reason for item in plan.unassigned_tasks}
    assert unassigned["TASK-001"] is UnassignedReason.TIME_WINDOW
    assert plan.violations == []


def test_constraint_checker_rejects_tampered_duration() -> None:
    scenario = load_demo()
    plan = build_fifo_plan(scenario)
    assignment = plan.assignments[0]
    tampered = assignment.model_copy(
        update={"service_ended_at": assignment.service_ended_at + timedelta(minutes=1)}
    )

    violations = validate_plan(
        scenario,
        [tampered, *plan.assignments[1:]],
        plan.unassigned_tasks,
    )

    assert ConstraintCode.TASK_DURATION in {violation.code for violation in violations}


def test_constraint_checker_rejects_tampered_wait_metric() -> None:
    scenario = load_demo()
    plan = build_fifo_plan(scenario)
    assignment = plan.assignments[0]
    tampered = assignment.model_copy(update={"wait_minutes": assignment.wait_minutes + 1})

    violations = validate_plan(
        scenario,
        [tampered, *plan.assignments[1:]],
        plan.unassigned_tasks,
    )

    assert ConstraintCode.WAIT_TIME in {violation.code for violation in violations}


def test_plan_model_rejects_executable_status_with_unassigned_tasks() -> None:
    payload = build_fifo_plan(load_demo()).model_dump()
    payload["status"] = "executable"

    with pytest.raises(ValidationError, match="cannot contain unassigned tasks"):
        Plan.model_validate(payload)
