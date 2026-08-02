"""Pure M4 runtime projections derived from persisted scenario and plan facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import Field, field_validator, model_validator

from .models import (
    FlightEvent,
    FlightStatus,
    ModelBase,
    Resource,
    ResourceStatus,
    Scenario,
)
from .planning_models import Assignment, Plan
from .runtime_models import (
    EventRuntimeProjection,
    FlightRuntimeProjection,
    ResourceRuntimeProjection,
    ResourceRuntimeStatus,
    RuntimeEventStatus,
    RuntimePosition,
    RuntimeStatus,
    TaskRuntimeProjection,
    TaskRuntimeStatus,
)


class RuntimeProjectionSource(ModelBase):
    """Durable facts required to rebuild one runtime session after restart."""

    scenario: Scenario
    plan: Plan
    event_catalog: list[FlightEvent] = Field(default_factory=list)
    applied_event_versions: dict[str, int] = Field(default_factory=dict)
    initial_scenario: Scenario | None = None
    initial_plan: Plan | None = None
    initial_applied_event_versions: dict[str, int] | None = None
    candidate_plan: Plan | None = None
    frozen_task_ids: list[str] = Field(default_factory=list)
    event_candidate_plan_ids: dict[str, str] = Field(default_factory=dict)
    resolved_event_ids: list[str] = Field(default_factory=list)
    failed_event_codes: dict[str, str] = Field(default_factory=dict)

    @field_validator("event_catalog")
    @classmethod
    def validate_event_catalog_ids(cls, events: list[FlightEvent]) -> list[FlightEvent]:
        event_ids = [event.event_id for event in events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("runtime event catalog must contain unique IDs")
        return events

    @field_validator("frozen_task_ids", "resolved_event_ids")
    @classmethod
    def validate_unique_ids(cls, values: list[str], info) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError(f"{info.field_name} must contain unique IDs")
        return values

    @model_validator(mode="after")
    def validate_source_consistency(self) -> RuntimeProjectionSource:
        if self.initial_scenario is None:
            object.__setattr__(self, "initial_scenario", self.scenario.model_copy(deep=True))
        if self.initial_plan is None:
            object.__setattr__(self, "initial_plan", self.plan.model_copy(deep=True))
        initial_scenario = self.initial_scenario
        initial_plan = self.initial_plan
        assert initial_scenario is not None
        assert initial_plan is not None
        if self.initial_applied_event_versions is None:
            initial_event_ids = {event.event_id for event in initial_scenario.events}
            object.__setattr__(
                self,
                "initial_applied_event_versions",
                {
                    event_id: version
                    for event_id, version in self.applied_event_versions.items()
                    if event_id in initial_event_ids
                },
            )

        if self.plan.scenario_id != self.scenario.scenario_id:
            raise ValueError("runtime projection plan must belong to the scenario")
        if self.plan.scenario_version > self.scenario.version:
            raise ValueError("active runtime plan cannot be newer than the scenario")
        if self.plan.violations:
            raise ValueError("runtime projection plan cannot contain hard violations")
        if initial_plan.scenario_id != initial_scenario.scenario_id:
            raise ValueError("initial runtime plan must belong to the initial scenario")
        if initial_plan.scenario_version != initial_scenario.version:
            raise ValueError("initial runtime plan and scenario versions must match")
        if initial_plan.violations:
            raise ValueError("initial runtime plan cannot contain hard violations")
        if initial_scenario.scenario_id != self.scenario.scenario_id:
            raise ValueError("initial and current runtime scenarios must match")
        if initial_scenario.version > self.scenario.version:
            raise ValueError("initial runtime scenario cannot be newer than current scenario")
        if self.candidate_plan is not None:
            if self.candidate_plan.scenario_id != self.scenario.scenario_id:
                raise ValueError("runtime candidate plan must belong to the scenario")
            if self.candidate_plan.scenario_version != self.scenario.version:
                raise ValueError("runtime candidate plan must match the current scenario version")
            if self.candidate_plan.violations:
                raise ValueError("runtime candidate plan cannot contain hard violations")

        task_ids = {task.task_id for task in self.scenario.tasks}
        initial_task_ids = {task.task_id for task in initial_scenario.tasks}
        if initial_task_ids != task_ids:
            raise ValueError("runtime event revisions cannot add or remove tasks")
        resource_by_id = {resource.resource_id: resource for resource in self.scenario.resources}
        for label, checked_plan in (
            ("active", self.plan),
            ("candidate", self.candidate_plan),
        ):
            if checked_plan is None:
                continue
            assigned_task_ids = [assignment.task_id for assignment in checked_plan.assignments]
            unassigned_task_ids = [item.task_id for item in checked_plan.unassigned_tasks]
            covered_task_ids = [*assigned_task_ids, *unassigned_task_ids]
            if len(covered_task_ids) != len(set(covered_task_ids)):
                raise ValueError(f"runtime {label} plan must cover each task at most once")
            if set(covered_task_ids) != task_ids:
                raise ValueError(f"runtime {label} plan must cover every scenario task")
            for assignment in checked_plan.assignments:
                resource = resource_by_id.get(assignment.resource_id)
                if resource is None:
                    raise ValueError(f"runtime {label} plan references an unknown resource")
                if resource.resource_type is not assignment.resource_type:
                    raise ValueError(f"runtime {label} plan resource type does not match")

        if not set(self.frozen_task_ids) <= task_ids:
            raise ValueError("frozen runtime tasks must exist in the scenario")
        active_assigned_ids = {assignment.task_id for assignment in self.plan.assignments}
        if not set(self.frozen_task_ids) <= active_assigned_ids:
            raise ValueError("frozen runtime tasks require active assignments")

        flight_ids = {flight.flight_id for flight in self.scenario.flights}
        catalog_by_id = {event.event_id: event for event in self.event_catalog}
        if any(event.flight_id not in flight_ids for event in self.event_catalog):
            raise ValueError("runtime event catalog references an unknown flight")
        applied_ids = {event.event_id for event in self.scenario.events}
        if set(self.applied_event_versions) != applied_ids:
            raise ValueError("applied event version evidence must match scenario events")
        for event in self.scenario.events:
            catalog_event = catalog_by_id.get(event.event_id)
            if catalog_event is None or catalog_event != event:
                raise ValueError("applied scenario events must exist unchanged in the catalog")
        if any(
            version < 1 or version > self.scenario.version
            for version in self.applied_event_versions.values()
        ):
            raise ValueError("applied event versions must not exceed the scenario version")
        initial_versions = self.initial_applied_event_versions or {}
        initial_applied_ids = {event.event_id for event in initial_scenario.events}
        if set(initial_versions) != initial_applied_ids:
            raise ValueError("initial applied event evidence must match the initial scenario")
        if any(
            version < 1 or version > initial_scenario.version
            for version in initial_versions.values()
        ):
            raise ValueError("initial event versions must not exceed the initial scenario version")
        if not initial_applied_ids <= set(self.resolved_event_ids):
            object.__setattr__(
                self,
                "resolved_event_ids",
                sorted({*self.resolved_event_ids, *initial_applied_ids}),
            )
        applied_ids = set(self.applied_event_versions)
        if not set(self.event_candidate_plan_ids) <= applied_ids:
            raise ValueError("event candidate evidence requires an applied event")
        if any(not plan_id for plan_id in self.event_candidate_plan_ids.values()):
            raise ValueError("event candidate plan IDs must be non-empty")
        if not set(self.resolved_event_ids) <= applied_ids:
            raise ValueError("resolved runtime events must already be applied")
        catalog_ids = set(catalog_by_id)
        if not set(self.failed_event_codes) <= catalog_ids:
            raise ValueError("failed runtime events must exist in the event catalog")
        if any(
            not code or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in code)
            for code in self.failed_event_codes.values()
        ):
            raise ValueError("runtime event failure codes must use lowercase snake case")
        return self


@dataclass(frozen=True, slots=True)
class RuntimeProjectionResult:
    tasks: list[TaskRuntimeProjection]
    resources: list[ResourceRuntimeProjection]
    flights: list[FlightRuntimeProjection]
    events: list[EventRuntimeProjection]
    next_boundary_at: datetime | None


def project_runtime_state(
    source: RuntimeProjectionSource,
    simulation_time: datetime,
    runtime_status: RuntimeStatus,
) -> RuntimeProjectionResult:
    _require_aware(simulation_time)
    if not source.scenario.window_start <= simulation_time <= source.scenario.window_end:
        raise ValueError("runtime projection time must stay inside the scenario window")

    assignments_by_task = {
        assignment.task_id: assignment for assignment in source.plan.assignments
    }
    unresolved_event_ids = (
        set(source.applied_event_versions)
        - set(source.resolved_event_ids)
        - set(source.failed_event_codes)
    )
    unresolved_events_by_flight: dict[str, list[str]] = {}
    for event in source.event_catalog:
        if event.event_id in unresolved_event_ids:
            unresolved_events_by_flight.setdefault(event.flight_id, []).append(event.event_id)
    tasks = [
        _project_task(
            task_id=task.task_id,
            origin_zone_id=(
                assignments_by_task[task.task_id].origin_zone_id
                if task.task_id in assignments_by_task
                else task.origin_zone_id
            ),
            destination_zone_id=(
                assignments_by_task[task.task_id].destination_zone_id
                if task.task_id in assignments_by_task
                else task.destination_zone_id
            ),
            assignment=assignments_by_task.get(task.task_id),
            simulation_time=simulation_time,
            runtime_status=runtime_status,
            affected_by_event_ids=sorted(
                unresolved_events_by_flight.get(task.flight_id, [])
            ),
        )
        for task in source.scenario.tasks
    ]

    assignments_by_resource: dict[str, list[Assignment]] = {
        resource.resource_id: [] for resource in source.scenario.resources
    }
    for assignment in source.plan.assignments:
        assignments_by_resource[assignment.resource_id].append(assignment)
    for assignments in assignments_by_resource.values():
        assignments.sort(
            key=lambda item: (
                item.travel_started_at,
                item.service_started_at,
                item.assignment_id,
            )
        )
    resources = [
        _project_resource(
            resource,
            assignments_by_resource[resource.resource_id],
            simulation_time,
            runtime_status,
        )
        for resource in source.scenario.resources
    ]

    applied_events = {
        event.event_id: event for event in source.scenario.events
    }
    flights = [
        _project_flight(
            flight_id=flight.flight_id,
            base_status=flight.status,
            boarding_starts_at=flight.boarding_starts_at,
            estimated_departure=flight.scheduled_departure,
            gate_id=flight.gate_id,
            applied_events=[
                event
                for event in applied_events.values()
                if event.flight_id == flight.flight_id
            ],
            simulation_time=simulation_time,
            runtime_status=runtime_status,
        )
        for flight in source.scenario.flights
    ]
    events = [
        _project_event(
            event,
            applied_version=source.applied_event_versions.get(event.event_id),
            candidate_plan_id=source.event_candidate_plan_ids.get(event.event_id),
            is_resolved=event.event_id in source.resolved_event_ids,
            failure_code=source.failed_event_codes.get(event.event_id),
            simulation_time=simulation_time,
            runtime_status=runtime_status,
        )
        for event in sorted(
            source.event_catalog,
            key=lambda item: (item.occurred_at, item.event_id),
        )
    ]

    return RuntimeProjectionResult(
        tasks=tasks,
        resources=resources,
        flights=flights,
        events=events,
        next_boundary_at=_next_boundary(source, simulation_time, runtime_status),
    )


def _project_task(
    *,
    task_id: str,
    origin_zone_id: str,
    destination_zone_id: str,
    assignment: Assignment | None,
    simulation_time: datetime,
    runtime_status: RuntimeStatus,
    affected_by_event_ids: list[str],
) -> TaskRuntimeProjection:
    if assignment is None:
        return TaskRuntimeProjection(
            task_id=task_id,
            status=TaskRuntimeStatus.UNASSIGNED,
            current_zone_id=origin_zone_id,
            affected_by_event_ids=affected_by_event_ids,
        )
    if runtime_status is RuntimeStatus.READY:
        status = TaskRuntimeStatus.PENDING
        next_transition_at = assignment.travel_started_at
        current_zone_id = origin_zone_id
    elif simulation_time >= assignment.service_ended_at:
        status = TaskRuntimeStatus.COMPLETED
        next_transition_at = None
        current_zone_id = destination_zone_id
    elif simulation_time >= assignment.service_started_at:
        status = TaskRuntimeStatus.IN_SERVICE
        next_transition_at = assignment.service_ended_at
        current_zone_id = origin_zone_id
    elif simulation_time >= assignment.travel_ended_at:
        status = TaskRuntimeStatus.WAITING
        next_transition_at = assignment.service_started_at
        current_zone_id = origin_zone_id
    elif simulation_time >= assignment.travel_started_at:
        status = TaskRuntimeStatus.EN_ROUTE
        next_transition_at = assignment.travel_ended_at
        current_zone_id = origin_zone_id
    else:
        status = TaskRuntimeStatus.PENDING
        next_transition_at = assignment.travel_started_at
        current_zone_id = origin_zone_id
    if status is TaskRuntimeStatus.PENDING and affected_by_event_ids:
        status = TaskRuntimeStatus.AFFECTED
    return TaskRuntimeProjection(
        task_id=task_id,
        status=status,
        assignment_id=assignment.assignment_id,
        resource_id=assignment.resource_id,
        current_zone_id=current_zone_id,
        next_transition_at=next_transition_at,
        affected_by_event_ids=affected_by_event_ids,
    )


def _project_resource(
    resource: Resource,
    assignments: list[Assignment],
    simulation_time: datetime,
    runtime_status: RuntimeStatus,
) -> ResourceRuntimeProjection:
    if runtime_status is RuntimeStatus.READY:
        unavailable = _resource_unavailable(resource, simulation_time)
        return ResourceRuntimeProjection(
            resource_id=resource.resource_id,
            status=(
                ResourceRuntimeStatus.UNAVAILABLE
                if unavailable
                else ResourceRuntimeStatus.IDLE
            ),
            position=RuntimePosition(from_zone_id=resource.current_zone_id),
            next_task_id=assignments[0].task_id if assignments else None,
            next_available_at=(
                resource.available_from
                if unavailable and simulation_time < resource.available_from
                else None
            ),
        )

    last_zone = resource.current_zone_id
    for assignment in assignments:
        if assignment.service_ended_at <= simulation_time:
            last_zone = assignment.destination_zone_id

    if _resource_unavailable(resource, simulation_time):
        return ResourceRuntimeProjection(
            resource_id=resource.resource_id,
            status=ResourceRuntimeStatus.UNAVAILABLE,
            position=RuntimePosition(from_zone_id=last_zone),
            next_task_id=_first_future_task(assignments, simulation_time),
            next_available_at=(
                resource.available_from
                if simulation_time < resource.available_from
                and resource.status is not ResourceStatus.UNAVAILABLE
                else None
            ),
        )

    for index, assignment in enumerate(assignments):
        next_task_id = assignments[index + 1].task_id if index + 1 < len(assignments) else None
        if assignment.travel_started_at <= simulation_time < assignment.travel_ended_at:
            return ResourceRuntimeProjection(
                resource_id=resource.resource_id,
                status=ResourceRuntimeStatus.MOVING,
                position=RuntimePosition(
                    from_zone_id=assignment.resource_start_zone_id,
                    to_zone_id=assignment.origin_zone_id,
                    progress_pct=_progress(
                        assignment.travel_started_at,
                        assignment.travel_ended_at,
                        simulation_time,
                    ),
                ),
                current_task_id=assignment.task_id,
                next_task_id=next_task_id,
                next_available_at=assignment.service_ended_at,
            )
        if assignment.travel_ended_at <= simulation_time < assignment.service_started_at:
            return ResourceRuntimeProjection(
                resource_id=resource.resource_id,
                status=ResourceRuntimeStatus.WAITING,
                position=RuntimePosition(from_zone_id=assignment.origin_zone_id),
                current_task_id=assignment.task_id,
                next_task_id=next_task_id,
                next_available_at=assignment.service_ended_at,
            )
        if assignment.service_started_at <= simulation_time < assignment.service_ended_at:
            return ResourceRuntimeProjection(
                resource_id=resource.resource_id,
                status=ResourceRuntimeStatus.SERVING,
                position=RuntimePosition(
                    from_zone_id=assignment.origin_zone_id,
                    to_zone_id=assignment.destination_zone_id,
                    progress_pct=_progress(
                        assignment.service_started_at,
                        assignment.service_ended_at,
                        simulation_time,
                    ),
                ),
                current_task_id=assignment.task_id,
                next_task_id=next_task_id,
                next_available_at=assignment.service_ended_at,
            )

    return ResourceRuntimeProjection(
        resource_id=resource.resource_id,
        status=ResourceRuntimeStatus.IDLE,
        position=RuntimePosition(from_zone_id=last_zone),
        next_task_id=_first_future_task(assignments, simulation_time),
    )


def _project_flight(
    *,
    flight_id: str,
    base_status: FlightStatus,
    boarding_starts_at: datetime,
    estimated_departure: datetime,
    gate_id: str,
    applied_events: list[FlightEvent],
    simulation_time: datetime,
    runtime_status: RuntimeStatus,
) -> FlightRuntimeProjection:
    status = base_status
    if runtime_status is not RuntimeStatus.READY:
        if simulation_time >= estimated_departure:
            status = FlightStatus.DEPARTED
        elif simulation_time >= boarding_starts_at:
            status = FlightStatus.BOARDING
    last_event_id = None
    if applied_events:
        last_event_id = max(
            applied_events,
            key=lambda event: (event.occurred_at, event.event_id),
        ).event_id
    return FlightRuntimeProjection(
        flight_id=flight_id,
        status=status,
        estimated_departure=estimated_departure,
        gate_id=gate_id,
        last_event_id=last_event_id,
    )


def _project_event(
    event: FlightEvent,
    *,
    applied_version: int | None,
    candidate_plan_id: str | None,
    is_resolved: bool,
    failure_code: str | None,
    simulation_time: datetime,
    runtime_status: RuntimeStatus,
) -> EventRuntimeProjection:
    detail = _event_detail(event)
    event_facts = {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "flight_id": event.flight_id,
        "detail": detail,
        "note": event.note,
        "occurred_at": event.occurred_at,
    }
    if failure_code is not None:
        return EventRuntimeProjection(
            **event_facts,
            status=RuntimeEventStatus.FAILED,
            applied_at=event.occurred_at if applied_version is not None else None,
            scenario_version_after=applied_version,
            failure_code=failure_code,
        )
    if applied_version is not None:
        if is_resolved:
            status = RuntimeEventStatus.RESOLVED
        elif runtime_status is RuntimeStatus.REPLANNING:
            status = RuntimeEventStatus.REPLANNING
        elif runtime_status is RuntimeStatus.AWAITING_CONFIRMATION:
            status = RuntimeEventStatus.AWAITING_CONFIRMATION
        else:
            status = RuntimeEventStatus.APPLIED
        return EventRuntimeProjection(
            **event_facts,
            status=status,
            applied_at=event.occurred_at,
            scenario_version_after=applied_version,
            candidate_plan_id=(
                candidate_plan_id
                if status in {
                    RuntimeEventStatus.AWAITING_CONFIRMATION,
                    RuntimeEventStatus.RESOLVED,
                }
                else None
            ),
        )
    status = (
        RuntimeEventStatus.TRIGGERED
        if runtime_status is not RuntimeStatus.READY and simulation_time >= event.occurred_at
        else RuntimeEventStatus.PENDING
    )
    return EventRuntimeProjection(
        **event_facts,
        status=status,
    )


def _event_detail(event: FlightEvent) -> str:
    flight_code = event.flight_id.removeprefix("FL-")
    if event.event_type.value == "delay":
        return f"{flight_code} 预计离港时间顺延 {event.delay_minutes or 0} 分钟"
    return (
        f"{flight_code} 保障地点由 {event.previous_gate_id} "
        f"调整至 {event.new_gate_id}"
    )


def _next_boundary(
    source: RuntimeProjectionSource,
    simulation_time: datetime,
    runtime_status: RuntimeStatus,
) -> datetime | None:
    candidates = {source.scenario.window_end}
    for assignment in source.plan.assignments:
        candidates.update(
            {
                assignment.travel_started_at,
                assignment.travel_ended_at,
                assignment.service_started_at,
                assignment.service_ended_at,
            }
        )
    for resource in source.scenario.resources:
        candidates.update({resource.available_from, resource.available_to})
    for flight in source.scenario.flights:
        candidates.update({flight.boarding_starts_at, flight.scheduled_departure})
    applied_ids = set(source.applied_event_versions)
    candidates.update(
        event.occurred_at
        for event in source.event_catalog
        if event.event_id not in applied_ids
    )
    if runtime_status is RuntimeStatus.READY:
        future = [value for value in candidates if value >= simulation_time]
    else:
        future = [value for value in candidates if value > simulation_time]
    return min(future) if future else None


def _resource_unavailable(resource: Resource, simulation_time: datetime) -> bool:
    return (
        resource.status is ResourceStatus.UNAVAILABLE
        or simulation_time < resource.available_from
        or simulation_time >= resource.available_to
    )


def _first_future_task(
    assignments: list[Assignment],
    simulation_time: datetime,
) -> str | None:
    for assignment in assignments:
        if assignment.travel_started_at > simulation_time:
            return assignment.task_id
    return None


def _progress(start: datetime, end: datetime, current: datetime) -> float:
    total = (end - start).total_seconds()
    if total <= 0:
        return 100.0
    elapsed = (current - start).total_seconds()
    return round(max(0.0, min(100.0, elapsed / total * 100.0)), 2)


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("runtime projection time must include a timezone offset")
