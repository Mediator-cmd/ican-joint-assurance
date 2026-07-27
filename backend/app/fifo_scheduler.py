"""Deterministic FIFO baseline scheduler."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .constraints import validate_plan
from .models import Resource, ResourceStatus, Scenario, ServiceTask
from .planning_models import (
    Assignment,
    Plan,
    PlanMetric,
    PlanStatus,
    ResourceMetric,
    UnassignedReason,
    UnassignedTask,
)
from .travel import RouteNotFoundError, shortest_travel_minutes


@dataclass
class _ResourceState:
    resource: Resource
    available_at: datetime
    zone_id: str


@dataclass(frozen=True)
class _Candidate:
    state: _ResourceState
    reposition_minutes: int
    travel_started_at: datetime
    travel_ended_at: datetime
    service_started_at: datetime
    service_ended_at: datetime


def _candidate_for(
    scenario: Scenario,
    state: _ResourceState,
    task: ServiceTask,
) -> _Candidate | None:
    try:
        reposition_minutes = shortest_travel_minutes(scenario, state.zone_id, task.origin_zone_id)
    except RouteNotFoundError:
        return None

    travel_started_at = state.available_at
    travel_ended_at = travel_started_at + timedelta(minutes=reposition_minutes)
    service_started_at = max(task.release_at, travel_ended_at)
    service_ended_at = service_started_at + timedelta(minutes=task.duration_minutes)
    if service_ended_at > task.deadline_at or service_ended_at > state.resource.available_to:
        return None

    return _Candidate(
        state=state,
        reposition_minutes=reposition_minutes,
        travel_started_at=travel_started_at,
        travel_ended_at=travel_ended_at,
        service_started_at=service_started_at,
        service_ended_at=service_ended_at,
    )


def _unassigned_reason(
    scenario: Scenario,
    task: ServiceTask,
    states: list[_ResourceState],
) -> UnassignedTask:
    same_type = [state for state in states if state.resource.resource_type == task.required_resource_type]
    enough_capacity = [state for state in same_type if state.resource.capacity >= task.party_size]
    available = [state for state in enough_capacity if state.resource.status is ResourceStatus.AVAILABLE]
    if not same_type or not enough_capacity:
        return UnassignedTask(
            task_id=task.task_id,
            reason=UnassignedReason.NO_COMPATIBLE_RESOURCE,
            detail="没有类型和容量同时匹配的资源",
        )
    if not available:
        return UnassignedTask(
            task_id=task.task_id,
            reason=UnassignedReason.RESOURCE_UNAVAILABLE,
            detail="匹配资源当前不可用",
        )

    has_route = False
    for state in available:
        try:
            shortest_travel_minutes(scenario, state.zone_id, task.origin_zone_id)
            has_route = True
            break
        except RouteNotFoundError:
            continue
    if not has_route:
        return UnassignedTask(
            task_id=task.task_id,
            reason=UnassignedReason.NO_ROUTE,
            detail="资源当前位置到任务起点没有可用路线",
        )
    return UnassignedTask(
        task_id=task.task_id,
        reason=UnassignedReason.TIME_WINDOW,
        detail="匹配资源无法在任务截止时间前完成服务",
    )


def build_plan_metrics(
    scenario: Scenario,
    assignments: list[Assignment],
    unassigned_tasks: list[UnassignedTask],
) -> PlanMetric:
    waits = [assignment.wait_minutes for assignment in assignments]
    assigned_task_ids = {assignment.task_id for assignment in assignments}
    critical_tasks = [task for task in scenario.tasks if task.priority == 1]
    critical_assigned = sum(task.task_id in assigned_task_ids for task in critical_tasks)

    resource_metrics: list[ResourceMetric] = []
    total_busy = 0
    total_available = 0
    busy_by_resource: dict[str, int] = {}
    for assignment in assignments:
        busy_by_resource[assignment.resource_id] = (
            busy_by_resource.get(assignment.resource_id, 0)
            + assignment.reposition_minutes
            + assignment.service_minutes
        )

    for resource in sorted(scenario.resources, key=lambda item: item.resource_id):
        available_minutes = int(
            (resource.available_to - resource.available_from).total_seconds() // 60
        )
        busy_minutes = busy_by_resource.get(resource.resource_id, 0)
        total_busy += busy_minutes
        total_available += available_minutes
        resource_metrics.append(
            ResourceMetric(
                resource_id=resource.resource_id,
                resource_type=resource.resource_type,
                busy_minutes=busy_minutes,
                available_minutes=available_minutes,
                utilization_pct=round(busy_minutes / available_minutes * 100, 2),
            )
        )

    total_tasks = len(scenario.tasks)
    assigned_count = len(assignments)
    return PlanMetric(
        total_tasks=total_tasks,
        assigned_tasks=assigned_count,
        unassigned_tasks=len(unassigned_tasks),
        average_wait_minutes=round(sum(waits) / len(waits), 2) if waits else 0,
        max_wait_minutes=max(waits, default=0),
        task_completion_rate_pct=round(assigned_count / total_tasks * 100, 2) if total_tasks else 100,
        critical_task_completion_rate_pct=(
            round(critical_assigned / len(critical_tasks) * 100, 2) if critical_tasks else 100
        ),
        overall_resource_utilization_pct=(
            round(total_busy / total_available * 100, 2) if total_available else 0
        ),
        resource_metrics=resource_metrics,
    )


def build_fifo_plan(scenario: Scenario) -> Plan:
    """Build a safe FIFO baseline; infeasible tasks remain explicitly unassigned."""

    states = [
        _ResourceState(
            resource=resource,
            available_at=resource.available_from,
            zone_id=resource.current_zone_id,
        )
        for resource in scenario.resources
    ]
    assignments: list[Assignment] = []
    unassigned_tasks: list[UnassignedTask] = []

    for task in sorted(scenario.tasks, key=lambda item: (item.release_at, item.task_id)):
        eligible_states = [
            state
            for state in states
            if state.resource.resource_type == task.required_resource_type
            and state.resource.capacity >= task.party_size
            and state.resource.status is ResourceStatus.AVAILABLE
        ]
        candidates = [
            candidate
            for state in eligible_states
            if (candidate := _candidate_for(scenario, state, task)) is not None
        ]
        if not candidates:
            unassigned_tasks.append(_unassigned_reason(scenario, task, states))
            continue

        selected = min(
            candidates,
            key=lambda candidate: (
                candidate.service_ended_at,
                candidate.service_started_at,
                candidate.state.resource.resource_id,
            ),
        )
        wait_minutes = int((selected.service_started_at - task.release_at).total_seconds() // 60)
        assignment = Assignment(
            assignment_id=f"ASG-{task.task_id.removeprefix('TASK-')}",
            task_id=task.task_id,
            resource_id=selected.state.resource.resource_id,
            resource_type=selected.state.resource.resource_type,
            resource_start_zone_id=selected.state.zone_id,
            origin_zone_id=task.origin_zone_id,
            destination_zone_id=task.destination_zone_id,
            travel_started_at=selected.travel_started_at,
            travel_ended_at=selected.travel_ended_at,
            service_started_at=selected.service_started_at,
            service_ended_at=selected.service_ended_at,
            reposition_minutes=selected.reposition_minutes,
            service_minutes=task.duration_minutes,
            wait_minutes=wait_minutes,
        )
        assignments.append(assignment)
        selected.state.available_at = selected.service_ended_at
        selected.state.zone_id = task.destination_zone_id

    violations = validate_plan(scenario, assignments, unassigned_tasks)
    if violations:
        status = PlanStatus.INVALID
    elif unassigned_tasks:
        status = PlanStatus.PARTIAL
    else:
        status = PlanStatus.EXECUTABLE

    return Plan(
        plan_id=f"PLAN-{scenario.scenario_id.removeprefix('SCN-')}-V{scenario.version}-FIFO",
        scenario_id=scenario.scenario_id,
        scenario_version=scenario.version,
        algorithm="fifo_baseline_v1",
        generated_at=scenario.window_start,
        status=status,
        assignments=assignments,
        unassigned_tasks=unassigned_tasks,
        violations=violations,
        metrics=build_plan_metrics(scenario, assignments, unassigned_tasks),
    )
