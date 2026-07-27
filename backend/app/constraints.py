"""Independent hard-constraint checks for generated plans."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from .models import Scenario
from .planning_models import (
    Assignment,
    ConstraintCode,
    ConstraintViolation,
    UnassignedTask,
)
from .travel import RouteNotFoundError, shortest_travel_minutes


def validate_plan(
    scenario: Scenario,
    assignments: list[Assignment],
    unassigned_tasks: list[UnassignedTask],
) -> list[ConstraintViolation]:
    violations: list[ConstraintViolation] = []
    tasks = {task.task_id: task for task in scenario.tasks}
    resources = {resource.resource_id: resource for resource in scenario.resources}

    assignment_ids: set[str] = set()
    assigned_task_ids: set[str] = set()
    for assignment in assignments:
        if assignment.assignment_id in assignment_ids or assignment.task_id in assigned_task_ids:
            violations.append(
                ConstraintViolation(
                    code=ConstraintCode.DUPLICATE_ASSIGNMENT,
                    message=f"duplicate assignment for task {assignment.task_id}",
                    task_id=assignment.task_id,
                    resource_id=assignment.resource_id,
                )
            )
        assignment_ids.add(assignment.assignment_id)
        assigned_task_ids.add(assignment.task_id)

        task = tasks.get(assignment.task_id)
        resource = resources.get(assignment.resource_id)
        if task is None:
            violations.append(
                ConstraintViolation(
                    code=ConstraintCode.UNKNOWN_TASK,
                    message=f"assignment references unknown task {assignment.task_id}",
                    task_id=assignment.task_id,
                )
            )
            continue
        if resource is None:
            violations.append(
                ConstraintViolation(
                    code=ConstraintCode.UNKNOWN_RESOURCE,
                    message=f"assignment references unknown resource {assignment.resource_id}",
                    task_id=assignment.task_id,
                    resource_id=assignment.resource_id,
                )
            )
            continue

        if assignment.resource_type != resource.resource_type or resource.resource_type != task.required_resource_type:
            violations.append(
                ConstraintViolation(
                    code=ConstraintCode.RESOURCE_TYPE,
                    message=f"resource {resource.resource_id} type does not satisfy task {task.task_id}",
                    task_id=task.task_id,
                    resource_id=resource.resource_id,
                )
            )
        if resource.capacity < task.party_size:
            violations.append(
                ConstraintViolation(
                    code=ConstraintCode.RESOURCE_CAPACITY,
                    message=f"resource {resource.resource_id} capacity is below task party size",
                    task_id=task.task_id,
                    resource_id=resource.resource_id,
                )
            )
        if (
            assignment.origin_zone_id != task.origin_zone_id
            or assignment.destination_zone_id != task.destination_zone_id
        ):
            violations.append(
                ConstraintViolation(
                    code=ConstraintCode.TASK_ROUTE,
                    message=f"assignment route does not match task {task.task_id}",
                    task_id=task.task_id,
                    resource_id=resource.resource_id,
                )
            )
        if assignment.service_started_at < task.release_at or assignment.service_ended_at > task.deadline_at:
            violations.append(
                ConstraintViolation(
                    code=ConstraintCode.TASK_WINDOW,
                    message=f"task {task.task_id} service falls outside its time window",
                    task_id=task.task_id,
                    resource_id=resource.resource_id,
                )
            )
        expected_service_end = assignment.service_started_at + timedelta(minutes=task.duration_minutes)
        if assignment.service_ended_at != expected_service_end or assignment.service_minutes != task.duration_minutes:
            violations.append(
                ConstraintViolation(
                    code=ConstraintCode.TASK_DURATION,
                    message=f"task {task.task_id} service duration is inconsistent",
                    task_id=task.task_id,
                    resource_id=resource.resource_id,
                )
            )
        expected_wait = int((assignment.service_started_at - task.release_at).total_seconds() // 60)
        if assignment.wait_minutes != expected_wait:
            violations.append(
                ConstraintViolation(
                    code=ConstraintCode.WAIT_TIME,
                    message=f"task {task.task_id} wait time is inconsistent",
                    task_id=task.task_id,
                    resource_id=resource.resource_id,
                )
            )
        if (
            assignment.travel_started_at < resource.available_from
            or assignment.service_ended_at > resource.available_to
        ):
            violations.append(
                ConstraintViolation(
                    code=ConstraintCode.RESOURCE_WINDOW,
                    message=f"resource {resource.resource_id} is used outside its availability window",
                    task_id=task.task_id,
                    resource_id=resource.resource_id,
                )
            )

    by_resource: dict[str, list[Assignment]] = defaultdict(list)
    for assignment in assignments:
        if assignment.resource_id in resources and assignment.task_id in tasks:
            by_resource[assignment.resource_id].append(assignment)

    for resource_id, resource_assignments in by_resource.items():
        resource = resources[resource_id]
        previous_end = resource.available_from
        previous_zone = resource.current_zone_id
        for assignment in sorted(
            resource_assignments,
            key=lambda item: (item.travel_started_at, item.assignment_id),
        ):
            if assignment.travel_started_at < previous_end:
                violations.append(
                    ConstraintViolation(
                        code=ConstraintCode.RESOURCE_OVERLAP,
                        message=f"resource {resource_id} has overlapping reserved intervals",
                        task_id=assignment.task_id,
                        resource_id=resource_id,
                    )
                )
            if assignment.resource_start_zone_id != previous_zone:
                violations.append(
                    ConstraintViolation(
                        code=ConstraintCode.TASK_ROUTE,
                        message=f"resource {resource_id} starts from an inconsistent zone",
                        task_id=assignment.task_id,
                        resource_id=resource_id,
                    )
                )
            try:
                expected_travel = shortest_travel_minutes(
                    scenario,
                    previous_zone,
                    assignment.origin_zone_id,
                )
            except RouteNotFoundError:
                expected_travel = -1
            actual_travel = int(
                (assignment.travel_ended_at - assignment.travel_started_at).total_seconds() // 60
            )
            if expected_travel < 0 or assignment.reposition_minutes != expected_travel or actual_travel != expected_travel:
                violations.append(
                    ConstraintViolation(
                        code=ConstraintCode.TRAVEL_TIME,
                        message=f"resource {resource_id} travel time is inconsistent",
                        task_id=assignment.task_id,
                        resource_id=resource_id,
                    )
                )
            previous_end = assignment.service_ended_at
            previous_zone = assignment.destination_zone_id

    unassigned_ids = [item.task_id for item in unassigned_tasks]
    covered_ids = assigned_task_ids | set(unassigned_ids)
    if len(unassigned_ids) != len(set(unassigned_ids)) or covered_ids != set(tasks):
        violations.append(
            ConstraintViolation(
                code=ConstraintCode.TASK_COVERAGE,
                message="every scenario task must appear exactly once as assigned or unassigned",
            )
        )
    if assigned_task_ids & set(unassigned_ids):
        violations.append(
            ConstraintViolation(
                code=ConstraintCode.TASK_COVERAGE,
                message="a task cannot be both assigned and unassigned",
            )
        )

    return violations
