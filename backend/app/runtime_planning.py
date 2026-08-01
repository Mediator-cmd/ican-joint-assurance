"""Deterministic event application and rolling-plan construction for M4."""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import ValidationError

from .constraints import validate_plan
from .events import EventApplicationError
from .fifo_scheduler import build_plan_metrics
from .models import FlightEvent, FlightEventType, FlightStatus, ResourceStatus, Scenario
from .optimizer import OptimizationError, build_optimized_plan
from .planning_models import Plan, PlanStatus
from .runtime_projection import RuntimeProjectionSource


class RuntimePlanningError(RuntimeError):
    """Raised when runtime facts cannot produce a safe candidate plan."""


def apply_runtime_event_batch(
    source: RuntimeProjectionSource,
    events: list[FlightEvent],
    frozen_task_ids: set[str],
) -> RuntimeProjectionSource:
    """Apply one same-time event batch without replaying earlier revisions."""

    if not events:
        raise ValueError("runtime event batch cannot be empty")
    ordered = sorted(events, key=lambda item: item.event_id)
    occurred_at = ordered[0].occurred_at
    if any(event.occurred_at != occurred_at for event in ordered):
        raise ValueError("runtime event batch must share one occurred_at boundary")
    event_ids = [event.event_id for event in ordered]
    if len(event_ids) != len(set(event_ids)):
        raise ValueError("runtime event batch cannot contain duplicate IDs")
    if set(event_ids) & set(source.applied_event_versions):
        raise ValueError("runtime event batch contains an already applied event")

    catalog = {event.event_id: event for event in source.event_catalog}
    if any(catalog.get(event.event_id) != event for event in ordered):
        raise ValueError("runtime event batch must come from the persisted event catalog")

    scenario = source.scenario.model_copy(deep=True)
    tasks = {task.task_id: task for task in scenario.tasks}
    for task_id in frozen_task_ids:
        tasks[task_id].locked = True
    flights = {flight.flight_id: flight for flight in scenario.flights}

    try:
        for event in ordered:
            flight = flights[event.flight_id]
            if event.event_type is FlightEventType.DELAY:
                delay = timedelta(minutes=event.delay_minutes or 0)
                flight.scheduled_departure += delay
                flight.boarding_starts_at += delay
                flight.status = FlightStatus.DELAYED
                for task in scenario.tasks:
                    if task.flight_id == flight.flight_id and not task.locked:
                        task.deadline_at += delay
            elif event.event_type is FlightEventType.GATE_CHANGE:
                if flight.gate_id != event.previous_gate_id:
                    raise EventApplicationError(
                        f"event {event.event_id} expected {event.previous_gate_id}, "
                        f"found {flight.gate_id}"
                    )
                flight.gate_id = event.new_gate_id or flight.gate_id
                for task in scenario.tasks:
                    if (
                        task.flight_id == flight.flight_id
                        and task.destination_zone_id == event.previous_gate_id
                        and not task.locked
                    ):
                        task.destination_zone_id = flight.gate_id
        scenario.events.extend(event.model_copy(deep=True) for event in ordered)
        scenario.version += 1
        scenario = Scenario.model_validate(scenario.model_dump(mode="python"))
    except (KeyError, EventApplicationError, ValidationError, ValueError) as error:
        raise RuntimePlanningError("runtime event batch is inconsistent with current facts") from error

    applied_versions = dict(source.applied_event_versions)
    applied_versions.update({event_id: scenario.version for event_id in event_ids})
    return RuntimeProjectionSource(
        scenario=scenario,
        plan=source.plan,
        event_catalog=source.event_catalog,
        applied_event_versions=applied_versions,
        initial_scenario=source.initial_scenario,
        initial_plan=source.initial_plan,
        initial_applied_event_versions=source.initial_applied_event_versions,
        candidate_plan=None,
        frozen_task_ids=sorted(frozen_task_ids),
        event_candidate_plan_ids=source.event_candidate_plan_ids,
        resolved_event_ids=source.resolved_event_ids,
        failed_event_codes={
            event_id: code
            for event_id, code in source.failed_event_codes.items()
            if event_id not in event_ids
        },
    )


def build_rolling_candidate(
    scenario: Scenario,
    active_plan: Plan,
    simulation_time: datetime,
    frozen_task_ids: set[str],
    session_id: str,
    replan_revision: int,
    max_time_seconds: float = 5.0,
) -> Plan:
    """Optimize only future work, then independently validate the merged plan."""

    if simulation_time.tzinfo is None or simulation_time.utcoffset() is None:
        raise ValueError("rolling-plan simulation_time must include a timezone offset")
    if active_plan.scenario_id != scenario.scenario_id:
        raise RuntimePlanningError("active plan does not belong to the runtime scenario")

    assignments_by_task = {
        assignment.task_id: assignment for assignment in active_plan.assignments
    }
    missing_frozen = frozen_task_ids - set(assignments_by_task)
    if missing_frozen:
        raise RuntimePlanningError("frozen runtime tasks require active assignments")
    frozen_assignments = [
        assignment.model_copy(deep=True)
        for assignment in active_plan.assignments
        if assignment.task_id in frozen_task_ids
    ]

    residual = scenario.model_copy(deep=True)
    residual.tasks = [
        task.model_copy(deep=True)
        for task in scenario.tasks
        if task.task_id not in frozen_task_ids
    ]
    frozen_by_resource: dict[str, list] = {}
    for assignment in frozen_assignments:
        frozen_by_resource.setdefault(assignment.resource_id, []).append(assignment)

    for resource in residual.resources:
        frozen = sorted(
            frozen_by_resource.get(resource.resource_id, []),
            key=lambda item: (item.service_ended_at, item.assignment_id),
        )
        available_from = max(resource.available_from, simulation_time)
        if frozen:
            last = frozen[-1]
            resource.current_zone_id = last.destination_zone_id
            available_from = max(available_from, last.service_ended_at)
        if available_from >= resource.available_to:
            resource.status = ResourceStatus.UNAVAILABLE
        else:
            resource.available_from = available_from
    residual = Scenario.model_validate(residual.model_dump(mode="python"))

    try:
        future_plan = build_optimized_plan(
            residual,
            max_time_seconds=max_time_seconds,
        )
    except OptimizationError:
        raise
    except (ValidationError, ValueError) as error:
        raise RuntimePlanningError("rolling optimizer input is invalid") from error

    assignments = [
        *frozen_assignments,
        *(assignment.model_copy(deep=True) for assignment in future_plan.assignments),
    ]
    assignments.sort(
        key=lambda item: (item.travel_started_at, item.service_started_at, item.assignment_id)
    )
    unassigned_tasks = [
        item.model_copy(deep=True) for item in future_plan.unassigned_tasks
    ]
    unassigned_tasks.sort(key=lambda item: item.task_id)
    violations = validate_plan(scenario, assignments, unassigned_tasks)
    if violations:
        raise RuntimePlanningError("rolling candidate failed independent constraint validation")

    status = PlanStatus.PARTIAL if unassigned_tasks else PlanStatus.EXECUTABLE
    candidate = Plan(
        plan_id=(
            f"PLAN-{session_id.removeprefix('RUN-')}-R{replan_revision}-CP-SAT"
        ),
        scenario_id=scenario.scenario_id,
        scenario_version=scenario.version,
        algorithm="rolling_cp_sat_v1",
        generated_at=simulation_time,
        status=status,
        assignments=assignments,
        unassigned_tasks=unassigned_tasks,
        violations=[],
        metrics=build_plan_metrics(scenario, assignments, unassigned_tasks),
    )
    return Plan.model_validate(candidate.model_dump(mode="python"))


def source_with_candidate(
    source: RuntimeProjectionSource,
    candidate: Plan,
) -> RuntimeProjectionSource:
    unresolved_event_ids = (
        set(source.applied_event_versions)
        - set(source.resolved_event_ids)
    )
    event_candidate_ids = dict(source.event_candidate_plan_ids)
    event_candidate_ids.update(
        {event_id: candidate.plan_id for event_id in unresolved_event_ids}
    )
    return RuntimeProjectionSource(
        scenario=source.scenario,
        plan=source.plan,
        event_catalog=source.event_catalog,
        applied_event_versions=source.applied_event_versions,
        initial_scenario=source.initial_scenario,
        initial_plan=source.initial_plan,
        initial_applied_event_versions=source.initial_applied_event_versions,
        candidate_plan=candidate,
        frozen_task_ids=source.frozen_task_ids,
        event_candidate_plan_ids=event_candidate_ids,
        resolved_event_ids=source.resolved_event_ids,
        failed_event_codes={
            event_id: code
            for event_id, code in source.failed_event_codes.items()
            if event_id not in unresolved_event_ids
        },
    )


def source_after_candidate_decision(
    source: RuntimeProjectionSource,
    *,
    accept: bool,
) -> RuntimeProjectionSource:
    candidate = source.candidate_plan
    if candidate is None:
        raise RuntimePlanningError("runtime candidate facts are missing")
    resolved = set(source.resolved_event_ids)
    resolved.update(
        event_id
        for event_id, plan_id in source.event_candidate_plan_ids.items()
        if plan_id == candidate.plan_id
    )
    return RuntimeProjectionSource(
        scenario=source.scenario,
        plan=candidate if accept else source.plan,
        event_catalog=source.event_catalog,
        applied_event_versions=source.applied_event_versions,
        initial_scenario=source.initial_scenario,
        initial_plan=source.initial_plan,
        initial_applied_event_versions=source.initial_applied_event_versions,
        candidate_plan=None,
        frozen_task_ids=source.frozen_task_ids,
        event_candidate_plan_ids=source.event_candidate_plan_ids,
        resolved_event_ids=sorted(resolved),
        failed_event_codes=source.failed_event_codes,
    )


def source_after_replan_failure(
    source: RuntimeProjectionSource,
    failure_code: str,
) -> RuntimeProjectionSource:
    unresolved_event_ids = (
        set(source.applied_event_versions)
        - set(source.resolved_event_ids)
    )
    failed = dict(source.failed_event_codes)
    failed.update({event_id: failure_code for event_id in unresolved_event_ids})
    return RuntimeProjectionSource(
        scenario=source.scenario,
        plan=source.plan,
        event_catalog=source.event_catalog,
        applied_event_versions=source.applied_event_versions,
        initial_scenario=source.initial_scenario,
        initial_plan=source.initial_plan,
        initial_applied_event_versions=source.initial_applied_event_versions,
        candidate_plan=None,
        frozen_task_ids=source.frozen_task_ids,
        event_candidate_plan_ids=source.event_candidate_plan_ids,
        resolved_event_ids=source.resolved_event_ids,
        failed_event_codes=failed,
    )


def reset_runtime_source(source: RuntimeProjectionSource) -> RuntimeProjectionSource:
    initial_scenario = source.initial_scenario
    initial_plan = source.initial_plan
    if initial_scenario is None or initial_plan is None:
        raise RuntimePlanningError("runtime initial projection facts are missing")
    return RuntimeProjectionSource(
        scenario=initial_scenario,
        plan=initial_plan,
        event_catalog=source.event_catalog,
        applied_event_versions=source.initial_applied_event_versions or {},
        initial_scenario=initial_scenario,
        initial_plan=initial_plan,
        initial_applied_event_versions=source.initial_applied_event_versions or {},
    )
