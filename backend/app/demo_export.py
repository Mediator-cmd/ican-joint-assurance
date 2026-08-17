"""Build the stable JSON contract consumed by the M2 dashboard prototype."""

from __future__ import annotations

from typing import Any

from .events import apply_events
from .fifo_scheduler import build_fifo_plan
from .models import FlightEvent, FlightEventType, Scenario
from .optimizer import build_optimized_plan
from .planning_models import Plan
from .runtime_paths import project_root
from .scenario_loader import load_scenario


SAFETY_NOTICE = (
    "仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。"
)
DEMO_SCENARIO_PATH = project_root() / "data" / "scenarios" / "terminal-disturbance-demo.json"


def _event_view(event: FlightEvent) -> dict[str, Any]:
    if event.event_type is FlightEventType.DELAY:
        title = "航班延误"
        detail = f"{event.flight_id.removeprefix('FL-')} 延误 {event.delay_minutes} 分钟"
    else:
        title = "登机口变更"
        detail = (
            f"{event.flight_id.removeprefix('FL-')} "
            f"由 {event.previous_gate_id} 调整至 {event.new_gate_id}"
        )
    return {
        "event_id": event.event_id,
        "event_type": event.event_type.value,
        "occurred_at": event.occurred_at.isoformat(),
        "title": title,
        "detail": detail,
        "note": event.note,
    }


def _scenario_changes(baseline: Scenario, updated: Scenario) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    baseline_flights = {flight.flight_id: flight for flight in baseline.flights}
    for flight in updated.flights:
        before = baseline_flights[flight.flight_id]
        if before.scheduled_departure != flight.scheduled_departure:
            changes.append(
                {
                    "entity_type": "flight",
                    "entity_id": flight.flight_id,
                    "field": "scheduled_departure",
                    "before": before.scheduled_departure.isoformat(),
                    "after": flight.scheduled_departure.isoformat(),
                }
            )
        if before.gate_id != flight.gate_id:
            changes.append(
                {
                    "entity_type": "flight",
                    "entity_id": flight.flight_id,
                    "field": "gate_id",
                    "before": before.gate_id,
                    "after": flight.gate_id,
                }
            )

    baseline_tasks = {task.task_id: task for task in baseline.tasks}
    for task in updated.tasks:
        before = baseline_tasks[task.task_id]
        if before.deadline_at != task.deadline_at:
            changes.append(
                {
                    "entity_type": "task",
                    "entity_id": task.task_id,
                    "field": "deadline_at",
                    "before": before.deadline_at.isoformat(),
                    "after": task.deadline_at.isoformat(),
                }
            )
        if before.destination_zone_id != task.destination_zone_id:
            changes.append(
                {
                    "entity_type": "task",
                    "entity_id": task.task_id,
                    "field": "destination_zone_id",
                    "before": before.destination_zone_id,
                    "after": task.destination_zone_id,
                }
            )
    return changes


def _legacy_plan_view(plan: Plan) -> dict[str, Any]:
    """Keep the M2/M3 offline demo payload stable as the live plan model evolves."""

    payload = plan.model_dump(mode="json")
    payload.pop("objective_profile", None)
    return payload


def build_demo_payload(baseline: Scenario) -> dict[str, Any]:
    updated = apply_events(baseline)
    baseline_plan = build_fifo_plan(baseline)
    updated_fifo_plan = build_fifo_plan(updated)
    optimized_plan = build_optimized_plan(updated)

    return {
        "project": {
            "name": "联保智调",
            "subtitle": "航班扰动下特殊旅客地面保障资源优化与推演平台",
            "safety_notice": SAFETY_NOTICE,
            "data_classification": baseline.data_classification.value,
        },
        "events": [_event_view(event) for event in sorted(baseline.events, key=lambda item: item.occurred_at)],
        "changes": _scenario_changes(baseline, updated),
        "views": {
            "baseline": {
                "label": "扰动前 FIFO 基线",
                "scenario": baseline.model_dump(mode="json"),
                "plan": _legacy_plan_view(baseline_plan),
            },
            "after_events_fifo": {
                "label": "事件后 FIFO 重规划",
                "scenario": updated.model_dump(mode="json"),
                "plan": _legacy_plan_view(updated_fifo_plan),
            },
            "optimized": {
                "label": "事件后 CP-SAT 优化",
                "scenario": updated.model_dump(mode="json"),
                "plan": _legacy_plan_view(optimized_plan),
            },
        },
    }


def build_default_demo_payload() -> dict[str, Any]:
    """Rebuild the canonical browser demo with the shared deterministic engine."""

    return build_demo_payload(load_scenario(DEMO_SCENARIO_PATH))
