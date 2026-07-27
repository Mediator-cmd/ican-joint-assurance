"""OR-Tools CP-SAT scheduler used after the FIFO baseline is established."""

from __future__ import annotations

from datetime import timedelta

from ortools.sat.python import cp_model

from .constraints import validate_plan
from .fifo_scheduler import build_plan_metrics
from .models import ResourceStatus, Scenario, ServiceTask
from .planning_models import (
    Assignment,
    Plan,
    PlanStatus,
    UnassignedReason,
    UnassignedTask,
)
from .travel import RouteNotFoundError, shortest_travel_minutes


class OptimizationError(RuntimeError):
    """Raised when CP-SAT cannot return a feasible planning state."""


def _minute(scenario: Scenario, value) -> int:
    return int((value - scenario.window_start).total_seconds() // 60)


def _solve(model: cp_model.CpModel, solver: cp_model.CpSolver) -> None:
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise OptimizationError(f"CP-SAT returned status {solver.StatusName(status)}")


def _unassigned_detail(scenario: Scenario, task: ServiceTask) -> UnassignedTask:
    matching = [
        resource
        for resource in scenario.resources
        if resource.resource_type == task.required_resource_type
        and resource.capacity >= task.party_size
    ]
    if not matching:
        return UnassignedTask(
            task_id=task.task_id,
            reason=UnassignedReason.NO_COMPATIBLE_RESOURCE,
            detail="没有类型和容量同时匹配的资源",
        )
    available = [resource for resource in matching if resource.status is ResourceStatus.AVAILABLE]
    if not available:
        return UnassignedTask(
            task_id=task.task_id,
            reason=UnassignedReason.RESOURCE_UNAVAILABLE,
            detail="匹配资源当前不可用",
        )
    reachable = []
    for resource in available:
        try:
            travel = shortest_travel_minutes(
                scenario,
                resource.current_zone_id,
                task.origin_zone_id,
            )
            reachable.append((resource, travel))
        except RouteNotFoundError:
            continue
    if not reachable:
        return UnassignedTask(
            task_id=task.task_id,
            reason=UnassignedReason.NO_ROUTE,
            detail="资源当前位置到任务起点没有可用路线",
        )
    if all(
        resource.available_from + timedelta(minutes=travel + task.duration_minutes) > task.deadline_at
        for resource, travel in reachable
    ):
        return UnassignedTask(
            task_id=task.task_id,
            reason=UnassignedReason.TIME_WINDOW,
            detail="匹配资源无法在任务截止时间前完成服务",
        )
    return UnassignedTask(
        task_id=task.task_id,
        reason=UnassignedReason.PRIORITY_TRADEOFF,
        detail="资源和时间窗冲突下，优化器优先保障更高优先级任务",
    )


def build_optimized_plan(scenario: Scenario, max_time_seconds: float = 5.0) -> Plan:
    """Build a lexicographic CP-SAT plan and validate it independently."""

    model = cp_model.CpModel()
    horizon = _minute(scenario, scenario.window_end)
    max_duration = max((task.duration_minutes for task in scenario.tasks), default=0)
    task_by_id = {task.task_id: task for task in scenario.tasks}
    resource_by_id = {resource.resource_id: resource for resource in scenario.resources}

    assigned: dict[str, cp_model.IntVar] = {}
    starts: dict[str, cp_model.IntVar] = {}
    ends: dict[str, cp_model.IntVar] = {}
    waits: dict[str, cp_model.IntVar] = {}
    use_resource: dict[tuple[str, str], cp_model.IntVar] = {}
    resource_rank_costs: list[cp_model.LinearExpr] = []

    eligible_by_task: dict[str, list[str]] = {}
    for task in scenario.tasks:
        assigned[task.task_id] = model.NewBoolVar(f"assigned_{task.task_id}")
        starts[task.task_id] = model.NewIntVar(0, horizon, f"start_{task.task_id}")
        ends[task.task_id] = model.NewIntVar(
            0,
            horizon + max_duration,
            f"end_{task.task_id}",
        )
        waits[task.task_id] = model.NewIntVar(0, horizon, f"wait_{task.task_id}")
        model.Add(ends[task.task_id] == starts[task.task_id] + task.duration_minutes)

        eligible_resources: list[str] = []
        for resource_rank, resource in enumerate(
            sorted(scenario.resources, key=lambda item: item.resource_id),
            start=1,
        ):
            if (
                resource.status is not ResourceStatus.AVAILABLE
                or resource.resource_type != task.required_resource_type
                or resource.capacity < task.party_size
            ):
                continue
            try:
                initial_travel = shortest_travel_minutes(
                    scenario,
                    resource.current_zone_id,
                    task.origin_zone_id,
                )
            except RouteNotFoundError:
                continue

            resource_var = model.NewBoolVar(f"use_{task.task_id}_{resource.resource_id}")
            use_resource[(task.task_id, resource.resource_id)] = resource_var
            resource_rank_costs.append(resource_var * resource_rank)
            eligible_resources.append(resource.resource_id)
            model.Add(
                starts[task.task_id]
                >= _minute(scenario, resource.available_from) + initial_travel
            ).OnlyEnforceIf(resource_var)
            model.Add(
                ends[task.task_id] <= _minute(scenario, resource.available_to)
            ).OnlyEnforceIf(resource_var)

        eligible_by_task[task.task_id] = eligible_resources
        resource_vars = [use_resource[(task.task_id, resource_id)] for resource_id in eligible_resources]
        model.Add(sum(resource_vars) == assigned[task.task_id])
        release = _minute(scenario, task.release_at)
        deadline = _minute(scenario, task.deadline_at)
        model.Add(starts[task.task_id] >= release).OnlyEnforceIf(assigned[task.task_id])
        model.Add(ends[task.task_id] <= deadline).OnlyEnforceIf(assigned[task.task_id])
        model.Add(waits[task.task_id] == starts[task.task_id] - release).OnlyEnforceIf(
            assigned[task.task_id]
        )
        model.Add(starts[task.task_id] == release).OnlyEnforceIf(assigned[task.task_id].Not())
        model.Add(waits[task.task_id] == 0).OnlyEnforceIf(assigned[task.task_id].Not())

    transition_costs: list[cp_model.LinearExpr] = []
    for resource in scenario.resources:
        task_ids = [
            task.task_id
            for task in scenario.tasks
            if resource.resource_id in eligible_by_task[task.task_id]
        ]
        for left_index, left_id in enumerate(task_ids):
            for right_id in task_ids[left_index + 1 :]:
                left_before = model.NewBoolVar(
                    f"before_{left_id}_{right_id}_{resource.resource_id}"
                )
                right_before = model.NewBoolVar(
                    f"before_{right_id}_{left_id}_{resource.resource_id}"
                )
                left_use = use_resource[(left_id, resource.resource_id)]
                right_use = use_resource[(right_id, resource.resource_id)]
                model.Add(left_before + right_before <= left_use)
                model.Add(left_before + right_before <= right_use)
                model.Add(left_before + right_before >= left_use + right_use - 1)

                left_task = task_by_id[left_id]
                right_task = task_by_id[right_id]
                try:
                    left_travel = shortest_travel_minutes(
                        scenario,
                        left_task.destination_zone_id,
                        right_task.origin_zone_id,
                    )
                    model.Add(starts[right_id] >= ends[left_id] + left_travel).OnlyEnforceIf(
                        left_before
                    )
                    transition_costs.append(left_before * left_travel)
                except RouteNotFoundError:
                    model.Add(left_before == 0)
                try:
                    right_travel = shortest_travel_minutes(
                        scenario,
                        right_task.destination_zone_id,
                        left_task.origin_zone_id,
                    )
                    model.Add(starts[left_id] >= ends[right_id] + right_travel).OnlyEnforceIf(
                        right_before
                    )
                    transition_costs.append(right_before * right_travel)
                except RouteNotFoundError:
                    model.Add(right_before == 0)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max_time_seconds
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0

    critical_vars = [assigned[task.task_id] for task in scenario.tasks if task.priority == 1]
    if critical_vars:
        critical_count = sum(critical_vars)
        model.Maximize(critical_count)
        _solve(model, solver)
        model.Add(critical_count == int(solver.Value(critical_count)))

    total_count = sum(assigned.values())
    model.Maximize(total_count)
    _solve(model, solver)
    model.Add(total_count == int(solver.Value(total_count)))

    priority_score = sum(
        assigned[task.task_id] * (6 - task.priority) for task in scenario.tasks
    )
    model.Maximize(priority_score)
    _solve(model, solver)
    model.Add(priority_score == int(solver.Value(priority_score)))

    model.Minimize(
        sum(waits.values()) * 1000
        + sum(transition_costs) * 10
        + sum(resource_rank_costs)
    )
    _solve(model, solver)

    assigned_by_resource: dict[str, list[ServiceTask]] = {
        resource_id: [] for resource_id in resource_by_id
    }
    unassigned_tasks: list[UnassignedTask] = []
    for task in scenario.tasks:
        if not solver.Value(assigned[task.task_id]):
            unassigned_tasks.append(_unassigned_detail(scenario, task))
            continue
        resource_id = next(
            resource_id
            for resource_id in eligible_by_task[task.task_id]
            if solver.Value(use_resource[(task.task_id, resource_id)])
        )
        assigned_by_resource[resource_id].append(task)

    assignments: list[Assignment] = []
    for resource_id, resource_tasks in assigned_by_resource.items():
        resource = resource_by_id[resource_id]
        previous_end = resource.available_from
        previous_zone = resource.current_zone_id
        for task in sorted(
            resource_tasks,
            key=lambda item: (solver.Value(starts[item.task_id]), item.task_id),
        ):
            reposition_minutes = shortest_travel_minutes(
                scenario,
                previous_zone,
                task.origin_zone_id,
            )
            travel_started_at = previous_end
            travel_ended_at = travel_started_at + timedelta(minutes=reposition_minutes)
            service_started_at = scenario.window_start + timedelta(
                minutes=solver.Value(starts[task.task_id])
            )
            service_ended_at = service_started_at + timedelta(minutes=task.duration_minutes)
            assignments.append(
                Assignment(
                    assignment_id=f"ASG-{task.task_id.removeprefix('TASK-')}",
                    task_id=task.task_id,
                    resource_id=resource_id,
                    resource_type=resource.resource_type,
                    resource_start_zone_id=previous_zone,
                    origin_zone_id=task.origin_zone_id,
                    destination_zone_id=task.destination_zone_id,
                    travel_started_at=travel_started_at,
                    travel_ended_at=travel_ended_at,
                    service_started_at=service_started_at,
                    service_ended_at=service_ended_at,
                    reposition_minutes=reposition_minutes,
                    service_minutes=task.duration_minutes,
                    wait_minutes=solver.Value(waits[task.task_id]),
                )
            )
            previous_end = service_ended_at
            previous_zone = task.destination_zone_id

    assignments.sort(key=lambda item: (item.service_started_at, item.task_id))
    unassigned_tasks.sort(key=lambda item: item.task_id)
    violations = validate_plan(scenario, assignments, unassigned_tasks)
    if violations:
        status = PlanStatus.INVALID
    elif unassigned_tasks:
        status = PlanStatus.PARTIAL
    else:
        status = PlanStatus.EXECUTABLE

    return Plan(
        plan_id=f"PLAN-{scenario.scenario_id.removeprefix('SCN-')}-V{scenario.version}-CP-SAT",
        scenario_id=scenario.scenario_id,
        scenario_version=scenario.version,
        algorithm="cp_sat_priority_v1",
        generated_at=scenario.window_start,
        status=status,
        assignments=assignments,
        unassigned_tasks=unassigned_tasks,
        violations=violations,
        metrics=build_plan_metrics(scenario, assignments, unassigned_tasks),
    )
