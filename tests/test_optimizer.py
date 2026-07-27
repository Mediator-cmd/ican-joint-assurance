from __future__ import annotations

from pathlib import Path

from backend.app.events import apply_events
from backend.app.fifo_scheduler import build_fifo_plan
from backend.app.optimizer import build_optimized_plan
from backend.app.planning_models import PlanStatus, UnassignedReason
from backend.app.scenario_loader import load_scenario


SCENARIO_PATH = Path(__file__).parents[1] / "data" / "scenarios" / "terminal-disturbance-demo.json"


def test_optimizer_protects_critical_task_under_resource_conflict() -> None:
    scenario = load_scenario(SCENARIO_PATH)
    fifo = build_fifo_plan(scenario)
    optimized = build_optimized_plan(scenario)

    assert fifo.metrics.assigned_tasks == optimized.metrics.assigned_tasks == 4
    assert fifo.metrics.critical_task_completion_rate_pct == 50
    assert optimized.metrics.critical_task_completion_rate_pct == 100
    assert optimized.status is PlanStatus.PARTIAL
    assert optimized.violations == []
    assert [(item.task_id, item.reason) for item in optimized.unassigned_tasks] == [
        ("TASK-001", UnassignedReason.PRIORITY_TRADEOFF)
    ]


def test_event_updated_optimizer_completes_every_task_deterministically() -> None:
    scenario = apply_events(load_scenario(SCENARIO_PATH))

    first = build_optimized_plan(scenario)
    second = build_optimized_plan(scenario)

    assert first == second
    assert first.status is PlanStatus.EXECUTABLE
    assert first.metrics.assigned_tasks == 5
    assert first.metrics.unassigned_tasks == 0
    assert first.metrics.critical_task_completion_rate_pct == 100
    assert first.violations == []

