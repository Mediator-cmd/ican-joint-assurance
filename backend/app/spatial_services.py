"""Derive one-to-one spatial facts from one authoritative runtime revision."""

from __future__ import annotations

from collections import deque
from math import hypot

from .models import FlightEventType, ServiceTask
from .planning_models import Assignment, Plan
from .runtime_models import RuntimeSessionSnapshot, TaskRuntimeStatus
from .runtime_projection import RuntimeProjectionSource
from .runtime_services import RuntimeSessionService
from .spatial_layout import build_spatial_layout
from .spatial_models import (
    NormalizedPoint,
    RuntimeSpatialOverlay,
    RuntimeSpatialView,
    SpatialAssignmentState,
    SpatialCoverage,
    SpatialEventMarker,
    SpatialFact,
    SpatialFactCategory,
    SpatialLayout,
    SpatialResourceMarker,
    SpatialRouteChange,
    SpatialRouteKind,
    SpatialRouteLeg,
    SpatialTaskRoute,
)


class RuntimeSpatialService:
    def __init__(self, runtime_service: RuntimeSessionService) -> None:
        self.runtime_service = runtime_service

    def get_view(self, session_id: str, expected_revision: int) -> RuntimeSpatialView:
        snapshot, source = self.runtime_service.get_explanation_context(
            session_id,
            expected_revision,
        )
        return build_runtime_spatial_view(snapshot, source)


def build_runtime_spatial_view(
    snapshot: RuntimeSessionSnapshot,
    source: RuntimeProjectionSource,
) -> RuntimeSpatialView:
    if (
        snapshot.scenario_id != source.scenario.scenario_id
        or snapshot.current_scenario_version != source.scenario.version
        or snapshot.active_plan_id != source.plan.plan_id
    ):
        raise ValueError("runtime spatial source does not match the bound snapshot")
    candidate_plan_id = source.candidate_plan.plan_id if source.candidate_plan else None
    if snapshot.candidate_plan_id != candidate_plan_id:
        raise ValueError("runtime spatial candidate does not match the bound snapshot")

    layout = build_spatial_layout(source.scenario)
    task_status_by_id = {task.task_id: task.status for task in snapshot.tasks}
    scenario_task_ids = {task.task_id for task in source.scenario.tasks}
    if set(task_status_by_id) != scenario_task_ids:
        raise ValueError("runtime snapshot must project every spatial source task")

    active_routes = [
        _build_task_route(
            task,
            source.plan,
            task_status_by_id[task.task_id],
            layout,
            SpatialRouteKind.ACTIVE,
            SpatialRouteChange.CURRENT,
        )
        for task in sorted(source.scenario.tasks, key=lambda item: item.task_id)
    ]
    candidate_routes: list[SpatialTaskRoute] = []
    if source.candidate_plan is not None:
        for task in sorted(source.scenario.tasks, key=lambda item: item.task_id):
            change = _candidate_change(task.task_id, source.plan, source.candidate_plan)
            if change is None:
                continue
            candidate_routes.append(
                _build_task_route(
                    task,
                    source.candidate_plan,
                    task_status_by_id[task.task_id],
                    layout,
                    SpatialRouteKind.CANDIDATE,
                    change,
                )
            )

    resource_markers = _build_resource_markers(snapshot, layout)
    event_markers = _build_event_markers(snapshot, source, layout)
    overlay = RuntimeSpatialOverlay(
        session_id=snapshot.session_id,
        revision=snapshot.revision,
        simulation_time=snapshot.clock.simulation_time,
        layout_id=layout.layout_id,
        task_routes=[*active_routes, *candidate_routes],
        resource_markers=resource_markers,
        event_markers=event_markers,
    )
    coverage = SpatialCoverage(
        source_task_ids=sorted(scenario_task_ids),
        projected_active_task_ids=sorted(route.task_id for route in active_routes),
        changed_candidate_task_ids=sorted(route.task_id for route in candidate_routes),
        projected_candidate_task_ids=sorted(route.task_id for route in candidate_routes),
        source_resource_ids=sorted(resource.resource_id for resource in source.scenario.resources),
        projected_resource_ids=sorted(marker.resource_id for marker in resource_markers),
        source_event_ids=sorted(event.event_id for event in source.event_catalog),
        projected_event_ids=sorted(marker.event_id for marker in event_markers),
    )
    return RuntimeSpatialView(
        scenario_id=source.scenario.scenario_id,
        scenario_version=source.scenario.version,
        layout=layout,
        overlay=overlay,
        coverage=coverage,
        facts=_build_spatial_facts(snapshot, active_routes, candidate_routes, resource_markers, event_markers),
    )


def _build_task_route(
    task: ServiceTask,
    plan: Plan,
    task_status: TaskRuntimeStatus,
    layout: SpatialLayout,
    route_kind: SpatialRouteKind,
    change_kind: SpatialRouteChange,
) -> SpatialTaskRoute:
    assignment = _assignment_for_task(plan, task.task_id)
    if assignment is None:
        origin_zone_id = task.origin_zone_id
        destination_zone_id = task.destination_zone_id
        assignment_state = SpatialAssignmentState.UNASSIGNED
        resource_id = None
        demand_only = True
    else:
        origin_zone_id = assignment.origin_zone_id
        destination_zone_id = assignment.destination_zone_id
        assignment_state = SpatialAssignmentState.ASSIGNED
        resource_id = assignment.resource_id
        demand_only = False
    return SpatialTaskRoute(
        route_id=f"ROUTE-{route_kind.value.upper()}-{task.task_id}",
        task_id=task.task_id,
        route_kind=route_kind,
        plan_id=plan.plan_id,
        task_status=task_status,
        assignment_state=assignment_state,
        change_kind=change_kind,
        resource_id=resource_id,
        origin_zone_id=origin_zone_id,
        destination_zone_id=destination_zone_id,
        legs=find_spatial_route(layout, origin_zone_id, destination_zone_id),
        demand_only=demand_only,
    )


def _candidate_change(
    task_id: str,
    active_plan: Plan,
    candidate_plan: Plan,
) -> SpatialRouteChange | None:
    active = _assignment_for_task(active_plan, task_id)
    candidate = _assignment_for_task(candidate_plan, task_id)
    if active is None and candidate is None:
        return None
    if active is None:
        return SpatialRouteChange.ASSIGNMENT_ADDED
    if candidate is None:
        return SpatialRouteChange.ASSIGNMENT_REMOVED
    if (active.origin_zone_id, active.destination_zone_id) != (
        candidate.origin_zone_id,
        candidate.destination_zone_id,
    ):
        return SpatialRouteChange.ROUTE_CHANGED
    if active.resource_id != candidate.resource_id:
        return SpatialRouteChange.RESOURCE_CHANGED
    if _assignment_schedule(active) != _assignment_schedule(candidate):
        return SpatialRouteChange.SCHEDULE_CHANGED
    return None


def _assignment_for_task(plan: Plan, task_id: str) -> Assignment | None:
    return next(
        (assignment for assignment in plan.assignments if assignment.task_id == task_id),
        None,
    )


def _assignment_schedule(assignment: Assignment) -> tuple[object, ...]:
    return (
        assignment.resource_start_zone_id,
        assignment.travel_started_at,
        assignment.travel_ended_at,
        assignment.service_started_at,
        assignment.service_ended_at,
    )


def find_spatial_route(
    layout: SpatialLayout,
    from_zone_id: str,
    to_zone_id: str,
) -> list[SpatialRouteLeg]:
    zone_ids = {zone.zone_id for zone in layout.zones}
    if from_zone_id not in zone_ids or to_zone_id not in zone_ids:
        raise ValueError("spatial route endpoints are not present in the layout")
    if from_zone_id == to_zone_id:
        return []

    adjacency: dict[str, list[SpatialRouteLeg]] = {zone_id: [] for zone_id in zone_ids}
    for path in layout.paths:
        adjacency[path.from_zone_id].append(
            SpatialRouteLeg(
                path_id=path.path_id,
                from_zone_id=path.from_zone_id,
                to_zone_id=path.to_zone_id,
                traversal="forward",
            )
        )
        adjacency[path.to_zone_id].append(
            SpatialRouteLeg(
                path_id=path.path_id,
                from_zone_id=path.to_zone_id,
                to_zone_id=path.from_zone_id,
                traversal="reverse",
            )
        )
    for legs in adjacency.values():
        legs.sort(key=lambda leg: (leg.to_zone_id, leg.path_id))

    queue: deque[tuple[str, list[SpatialRouteLeg]]] = deque([(from_zone_id, [])])
    visited = {from_zone_id}
    while queue:
        zone_id, route = queue.popleft()
        for leg in adjacency[zone_id]:
            if leg.to_zone_id in visited:
                continue
            next_route = [*route, leg]
            if leg.to_zone_id == to_zone_id:
                return next_route
            visited.add(leg.to_zone_id)
            queue.append((leg.to_zone_id, next_route))
    raise ValueError(f"no spatial path connects {from_zone_id} to {to_zone_id}")


def _build_resource_markers(
    snapshot: RuntimeSessionSnapshot,
    layout: SpatialLayout,
) -> list[SpatialResourceMarker]:
    markers: list[SpatialResourceMarker] = []
    for resource in sorted(snapshot.resources, key=lambda item: item.resource_id):
        to_zone_id = resource.position.to_zone_id
        legs = (
            find_spatial_route(layout, resource.position.from_zone_id, to_zone_id)
            if to_zone_id is not None
            else []
        )
        position = interpolate_spatial_route(
            layout,
            resource.position.from_zone_id,
            legs,
            resource.position.progress_pct,
        )
        markers.append(
            SpatialResourceMarker(
                resource_id=resource.resource_id,
                status=resource.status,
                current_task_id=resource.current_task_id,
                next_task_id=resource.next_task_id,
                from_zone_id=resource.position.from_zone_id,
                to_zone_id=to_zone_id,
                progress_pct=resource.position.progress_pct,
                position=position,
                route_legs=legs,
            )
        )
    return markers


def interpolate_spatial_route(
    layout: SpatialLayout,
    from_zone_id: str,
    legs: list[SpatialRouteLeg],
    progress_pct: float,
) -> NormalizedPoint:
    zone_by_id = {zone.zone_id: zone for zone in layout.zones}
    if not legs:
        return zone_by_id[from_zone_id].anchor
    path_by_id = {path.path_id: path for path in layout.paths}
    points: list[NormalizedPoint] = []
    for leg in legs:
        path_points = list(path_by_id[leg.path_id].points)
        if leg.traversal == "reverse":
            path_points.reverse()
        points.extend(path_points if not points else path_points[1:])

    segments = [
        hypot(end.x - start.x, end.y - start.y)
        for start, end in zip(points, points[1:])
    ]
    total = sum(segments)
    if total <= 0:
        return points[-1]
    remaining = total * max(0, min(100, progress_pct)) / 100
    for index, length in enumerate(segments):
        if remaining <= length or index == len(segments) - 1:
            ratio = 0 if length <= 0 else min(1, remaining / length)
            start = points[index]
            end = points[index + 1]
            return NormalizedPoint(
                x=round(start.x + (end.x - start.x) * ratio, 6),
                y=round(start.y + (end.y - start.y) * ratio, 6),
            )
        remaining -= length
    return points[-1]


def _build_event_markers(
    snapshot: RuntimeSessionSnapshot,
    source: RuntimeProjectionSource,
    layout: SpatialLayout,
) -> list[SpatialEventMarker]:
    runtime_event_by_id = {event.event_id: event for event in snapshot.events}
    flight_by_id = {flight.flight_id: flight for flight in snapshot.flights}
    zone_by_id = {zone.zone_id: zone for zone in layout.zones}
    if set(runtime_event_by_id) != {event.event_id for event in source.event_catalog}:
        raise ValueError("runtime snapshot must project every spatial source event")

    markers: list[SpatialEventMarker] = []
    for event in sorted(source.event_catalog, key=lambda item: (item.occurred_at, item.event_id)):
        runtime_event = runtime_event_by_id[event.event_id]
        if event.event_type is FlightEventType.GATE_CHANGE:
            assert event.previous_gate_id is not None
            assert event.new_gate_id is not None
            primary_zone_id = event.new_gate_id
            previous_zone_id = event.previous_gate_id
            new_zone_id = event.new_gate_id
            route_legs = find_spatial_route(layout, previous_zone_id, new_zone_id)
        else:
            flight = flight_by_id.get(event.flight_id)
            if flight is None:
                raise ValueError("delay event references a missing runtime flight")
            primary_zone_id = flight.gate_id
            previous_zone_id = None
            new_zone_id = None
            route_legs = []
        markers.append(
            SpatialEventMarker(
                event_id=event.event_id,
                event_type=event.event_type,
                flight_id=event.flight_id,
                status=runtime_event.status,
                detail=runtime_event.detail,
                primary_zone_id=primary_zone_id,
                position=zone_by_id[primary_zone_id].anchor,
                previous_zone_id=previous_zone_id,
                new_zone_id=new_zone_id,
                route_legs=route_legs,
            )
        )
    return markers


def _build_spatial_facts(
    snapshot: RuntimeSessionSnapshot,
    active_routes: list[SpatialTaskRoute],
    candidate_routes: list[SpatialTaskRoute],
    resource_markers: list[SpatialResourceMarker],
    event_markers: list[SpatialEventMarker],
) -> list[SpatialFact]:
    task_projection_by_id = {task.task_id: task for task in snapshot.tasks}
    runtime_event_by_id = {event.event_id: event for event in snapshot.events}
    facts = [
        SpatialFact(
            fact_id="SPATIAL-FACT-CONTEXT",
            category=SpatialFactCategory.CONTEXT,
            claim=(
                f"运行会话 {snapshot.session_id} 的空间态势绑定修订 {snapshot.revision}，"
                f"仿真时间为 {snapshot.clock.simulation_time.isoformat()}。"
            ),
            entity_ids=[snapshot.session_id, snapshot.active_plan_id],
        ),
        SpatialFact(
            fact_id="SPATIAL-FACT-COVERAGE",
            category=SpatialFactCategory.COVERAGE,
            claim=(
                f"本修订一一投影 {len(active_routes)} 个任务、"
                f"{len(resource_markers)} 个资源和 {len(event_markers)} 个事件；"
                f"候选方案包含 {len(candidate_routes)} 个发生变化的任务路线。"
            ),
            entity_ids=[snapshot.session_id],
        ),
    ]
    for route in active_routes:
        affecting_event_ids = task_projection_by_id[route.task_id].affected_by_event_ids
        assignment = (
            f"由资源 {route.resource_id} 执行"
            if route.resource_id is not None
            else "当前未分配资源，作为待协调需求保留"
        )
        impact = (
            f"，受事件 {', '.join(affecting_event_ids)} 影响"
            if affecting_event_ids
            else ""
        )
        facts.append(
            SpatialFact(
                fact_id=f"SPATIAL-FACT-{route.task_id}-ACTIVE",
                category=SpatialFactCategory.TASK,
                claim=(
                    f"任务 {route.task_id} 当前路线从 {route.origin_zone_id} 到 "
                    f"{route.destination_zone_id}，{assignment}，运行状态为 "
                    f"{route.task_status.value}{impact}。"
                ),
                entity_ids=[
                    route.task_id,
                    route.plan_id,
                    *([route.resource_id] if route.resource_id else []),
                    route.origin_zone_id,
                    route.destination_zone_id,
                    *affecting_event_ids,
                ],
            )
        )
    for route in candidate_routes:
        affecting_event_ids = task_projection_by_id[route.task_id].affected_by_event_ids
        facts.append(
            SpatialFact(
                fact_id=f"SPATIAL-FACT-{route.task_id}-CANDIDATE",
                category=SpatialFactCategory.PLAN,
                claim=(
                    f"待人工确认候选对任务 {route.task_id} 的变化类型为 "
                    f"{route.change_kind.value}，候选路线从 {route.origin_zone_id} 到 "
                    f"{route.destination_zone_id}；采用前不替换当前路线。"
                ),
                entity_ids=[
                    route.task_id,
                    route.plan_id,
                    *([route.resource_id] if route.resource_id else []),
                    *affecting_event_ids,
                ],
            )
        )
    for marker in resource_markers:
        location = marker.from_zone_id
        if marker.to_zone_id is not None:
            location = (
                f"{marker.from_zone_id} 至 {marker.to_zone_id} 路径的 "
                f"{marker.progress_pct:.2f}%"
            )
        facts.append(
            SpatialFact(
                fact_id=f"SPATIAL-FACT-{marker.resource_id}",
                category=SpatialFactCategory.RESOURCE,
                claim=f"资源 {marker.resource_id} 位于 {location}，状态为 {marker.status.value}。",
                entity_ids=[
                    marker.resource_id,
                    *([marker.current_task_id] if marker.current_task_id else []),
                    marker.from_zone_id,
                    *([marker.to_zone_id] if marker.to_zone_id else []),
                ],
            )
        )
    for marker in event_markers:
        runtime_event = runtime_event_by_id[marker.event_id]
        affected_task_ids = sorted(
            task.task_id
            for task in snapshot.tasks
            if marker.event_id in task.affected_by_event_ids
        )
        impact = (
            f"，当前关联任务为 {', '.join(affected_task_ids)}"
            if affected_task_ids
            else "，当前没有被标记为受影响的任务"
        )
        candidate = (
            f"，关联待确认候选 {runtime_event.candidate_plan_id}"
            if runtime_event.candidate_plan_id is not None
            else ""
        )
        facts.append(
            SpatialFact(
                fact_id=f"SPATIAL-FACT-{marker.event_id}",
                category=SpatialFactCategory.EVENT,
                claim=(
                    f"事件 {marker.event_id} 定位于 {marker.primary_zone_id}，"
                    f"状态为 {marker.status.value}{impact}{candidate}：{marker.detail}。"
                ),
                entity_ids=[
                    marker.event_id,
                    marker.flight_id,
                    marker.primary_zone_id,
                    *affected_task_ids,
                    *([runtime_event.candidate_plan_id] if runtime_event.candidate_plan_id else []),
                ],
            )
        )
    return facts
