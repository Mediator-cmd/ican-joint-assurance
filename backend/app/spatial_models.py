"""Strict contracts for revision-bound airport simulation spatial views."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .demo_export import SAFETY_NOTICE
from .models import FlightEventType, ModelBase
from .runtime_models import (
    ResourceRuntimeStatus,
    RuntimeEventStatus,
    TaskRuntimeStatus,
)


class NormalizedPoint(ModelBase):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class SpatialCanvas(ModelBase):
    width: int = Field(gt=0, le=10000)
    height: int = Field(gt=0, le=10000)


class SpatialAsset(ModelBase):
    kind: Literal["svg"] = "svg"
    source_class: Literal["original_local"] = "original_local"
    public_path: str = Field(pattern=r"^/assets/[a-z0-9-]+\.svg\?v=[a-f0-9]{64}$")
    license_id: Literal["project-original"] = "project-original"
    integrity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    safety_classification: Literal["anonymous_training_simulation"] = (
        "anonymous_training_simulation"
    )

    @field_validator("public_path")
    @classmethod
    def reject_remote_or_script_paths(cls, value: str) -> str:
        lowered = value.lower()
        if "://" in lowered or "javascript:" in lowered:
            raise ValueError("spatial assets must be local and script-free")
        return value

    @model_validator(mode="after")
    def require_content_addressed_url(self) -> SpatialAsset:
        version = self.public_path.rsplit("?v=", maxsplit=1)[-1]
        if version != self.integrity_sha256:
            raise ValueError("spatial asset URL version must match its content hash")
        return self


class SpatialZone(ModelBase):
    zone_id: str = Field(pattern=r"^[A-Z0-9-]+$")
    label: str = Field(min_length=1, max_length=80)
    floor: str = Field(min_length=1, max_length=24)
    anchor: NormalizedPoint
    shape: list[NormalizedPoint] = Field(min_length=3, max_length=24)


class SpatialPath(ModelBase):
    path_id: str = Field(pattern=r"^PATH-[A-Z0-9-]+$")
    from_zone_id: str = Field(min_length=1)
    to_zone_id: str = Field(min_length=1)
    points: list[NormalizedPoint] = Field(min_length=2, max_length=32)
    direction: Literal["bidirectional"] = "bidirectional"
    accessible: bool = True

    @model_validator(mode="after")
    def validate_endpoints_differ(self) -> SpatialPath:
        if self.from_zone_id == self.to_zone_id:
            raise ValueError("spatial path endpoints must differ")
        return self


class SpatialLayout(ModelBase):
    layout_id: str = Field(pattern=r"^LAYOUT-[A-Z0-9-]+$")
    layout_version: int = Field(ge=1)
    scenario_id: str = Field(min_length=1)
    scenario_version: int = Field(ge=1)
    projection: Literal["normalized_cartesian"] = "normalized_cartesian"
    canvas: SpatialCanvas
    asset: SpatialAsset
    zones: list[SpatialZone] = Field(min_length=1, max_length=100)
    paths: list[SpatialPath] = Field(default_factory=list, max_length=300)
    safety_notice: Literal[
        "仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。"
    ] = SAFETY_NOTICE

    @model_validator(mode="after")
    def validate_graph_integrity(self) -> SpatialLayout:
        zone_by_id = {zone.zone_id: zone for zone in self.zones}
        if len(zone_by_id) != len(self.zones):
            raise ValueError("spatial layout zone IDs must be unique")
        path_ids = [path.path_id for path in self.paths]
        if len(path_ids) != len(set(path_ids)):
            raise ValueError("spatial layout path IDs must be unique")
        endpoint_pairs: set[frozenset[str]] = set()
        for path in self.paths:
            if path.from_zone_id not in zone_by_id or path.to_zone_id not in zone_by_id:
                raise ValueError("spatial path references an unknown zone")
            pair = frozenset({path.from_zone_id, path.to_zone_id})
            if pair in endpoint_pairs:
                raise ValueError("spatial layout cannot duplicate a zone connection")
            endpoint_pairs.add(pair)
            if path.points[0] != zone_by_id[path.from_zone_id].anchor:
                raise ValueError("spatial path must start at its from-zone anchor")
            if path.points[-1] != zone_by_id[path.to_zone_id].anchor:
                raise ValueError("spatial path must end at its to-zone anchor")
        return self


class SpatialRouteKind(str, Enum):
    ACTIVE = "active"
    CANDIDATE = "candidate"


class SpatialAssignmentState(str, Enum):
    ASSIGNED = "assigned"
    UNASSIGNED = "unassigned"


class SpatialRouteChange(str, Enum):
    CURRENT = "current"
    ASSIGNMENT_ADDED = "assignment_added"
    ASSIGNMENT_REMOVED = "assignment_removed"
    RESOURCE_CHANGED = "resource_changed"
    ROUTE_CHANGED = "route_changed"
    SCHEDULE_CHANGED = "schedule_changed"


class SpatialRouteLeg(ModelBase):
    path_id: str = Field(min_length=1)
    from_zone_id: str = Field(min_length=1)
    to_zone_id: str = Field(min_length=1)
    traversal: Literal["forward", "reverse"]

    @model_validator(mode="after")
    def validate_leg_endpoints(self) -> SpatialRouteLeg:
        if self.from_zone_id == self.to_zone_id:
            raise ValueError("spatial route leg endpoints must differ")
        return self


class SpatialTaskRoute(ModelBase):
    route_id: str = Field(pattern=r"^ROUTE-(ACTIVE|CANDIDATE)-TASK-[A-Z0-9-]+$")
    task_id: str = Field(min_length=1)
    route_kind: SpatialRouteKind
    plan_id: str = Field(min_length=1)
    task_status: TaskRuntimeStatus
    assignment_state: SpatialAssignmentState
    change_kind: SpatialRouteChange
    resource_id: str | None = Field(default=None, min_length=1)
    origin_zone_id: str = Field(min_length=1)
    destination_zone_id: str = Field(min_length=1)
    legs: list[SpatialRouteLeg] = Field(default_factory=list, max_length=24)
    demand_only: bool

    @model_validator(mode="after")
    def validate_route_semantics(self) -> SpatialTaskRoute:
        expected_prefix = f"ROUTE-{self.route_kind.value.upper()}-{self.task_id}"
        if self.route_id != expected_prefix:
            raise ValueError("spatial route ID must encode route kind and task ID")
        if self.route_kind is SpatialRouteKind.ACTIVE:
            if self.change_kind is not SpatialRouteChange.CURRENT:
                raise ValueError("active routes must describe the current plan")
        elif self.change_kind is SpatialRouteChange.CURRENT:
            raise ValueError("candidate routes must describe a plan change")
        if self.assignment_state is SpatialAssignmentState.ASSIGNED:
            if self.resource_id is None or self.demand_only:
                raise ValueError("assigned route requires a resource and is not demand-only")
        elif self.resource_id is not None or not self.demand_only:
            raise ValueError("unassigned route must remain visible as demand-only")
        if self.origin_zone_id != self.destination_zone_id and not self.legs:
            raise ValueError("route between different zones requires spatial legs")
        if self.legs and (
            self.legs[0].from_zone_id != self.origin_zone_id
            or self.legs[-1].to_zone_id != self.destination_zone_id
        ):
            raise ValueError("task route legs must connect its declared endpoints")
        return self


class SpatialResourceMarker(ModelBase):
    resource_id: str = Field(min_length=1)
    status: ResourceRuntimeStatus
    current_task_id: str | None = Field(default=None, min_length=1)
    next_task_id: str | None = Field(default=None, min_length=1)
    from_zone_id: str = Field(min_length=1)
    to_zone_id: str | None = Field(default=None, min_length=1)
    progress_pct: float = Field(ge=0, le=100)
    position: NormalizedPoint
    route_legs: list[SpatialRouteLeg] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def validate_marker_route(self) -> SpatialResourceMarker:
        if self.to_zone_id is None:
            if self.progress_pct != 0 or self.route_legs:
                raise ValueError("stationary resource marker cannot have movement geometry")
        elif self.from_zone_id != self.to_zone_id and not self.route_legs:
            raise ValueError("moving resource marker requires spatial route legs")
        if self.route_legs and (
            self.route_legs[0].from_zone_id != self.from_zone_id
            or self.route_legs[-1].to_zone_id != self.to_zone_id
        ):
            raise ValueError("resource route legs must connect its reported position endpoints")
        return self


class SpatialEventMarker(ModelBase):
    event_id: str = Field(min_length=1)
    event_type: FlightEventType
    flight_id: str = Field(min_length=1)
    status: RuntimeEventStatus
    detail: str = Field(min_length=1, max_length=240)
    primary_zone_id: str = Field(min_length=1)
    position: NormalizedPoint
    previous_zone_id: str | None = Field(default=None, min_length=1)
    new_zone_id: str | None = Field(default=None, min_length=1)
    route_legs: list[SpatialRouteLeg] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def validate_event_geometry(self) -> SpatialEventMarker:
        if self.event_type is FlightEventType.GATE_CHANGE:
            if self.previous_zone_id is None or self.new_zone_id is None:
                raise ValueError("gate-change marker requires both zones")
            if self.previous_zone_id != self.new_zone_id and not self.route_legs:
                raise ValueError("gate-change marker requires a spatial route")
            if self.primary_zone_id != self.new_zone_id:
                raise ValueError("gate-change marker must focus its new zone")
            if self.route_legs and (
                self.route_legs[0].from_zone_id != self.previous_zone_id
                or self.route_legs[-1].to_zone_id != self.new_zone_id
            ):
                raise ValueError("gate-change route must connect the reported gates")
        elif self.previous_zone_id is not None or self.new_zone_id is not None:
            raise ValueError("delay marker cannot publish a gate-change route")
        return self


class RuntimeSpatialOverlay(ModelBase):
    session_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    simulation_time: datetime
    layout_id: str = Field(min_length=1)
    task_routes: list[SpatialTaskRoute] = Field(default_factory=list)
    resource_markers: list[SpatialResourceMarker] = Field(default_factory=list)
    event_markers: list[SpatialEventMarker] = Field(default_factory=list)

    @field_validator("simulation_time")
    @classmethod
    def require_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("spatial simulation_time must include a timezone offset")
        return value

    @model_validator(mode="after")
    def validate_unique_projection_ids(self) -> RuntimeSpatialOverlay:
        route_keys = [(route.task_id, route.route_kind) for route in self.task_routes]
        if len(route_keys) != len(set(route_keys)):
            raise ValueError("each task may have only one route per route kind")
        resource_ids = [marker.resource_id for marker in self.resource_markers]
        event_ids = [marker.event_id for marker in self.event_markers]
        if len(resource_ids) != len(set(resource_ids)):
            raise ValueError("resource spatial markers must be unique")
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("event spatial markers must be unique")
        return self


class SpatialCoverage(ModelBase):
    source_task_ids: list[str]
    projected_active_task_ids: list[str]
    changed_candidate_task_ids: list[str]
    projected_candidate_task_ids: list[str]
    source_resource_ids: list[str]
    projected_resource_ids: list[str]
    source_event_ids: list[str]
    projected_event_ids: list[str]
    complete: Literal[True] = True

    @field_validator(
        "source_task_ids",
        "projected_active_task_ids",
        "changed_candidate_task_ids",
        "projected_candidate_task_ids",
        "source_resource_ids",
        "projected_resource_ids",
        "source_event_ids",
        "projected_event_ids",
    )
    @classmethod
    def require_unique_sorted_ids(cls, values: list[str], info) -> list[str]:
        if values != sorted(set(values)):
            raise ValueError(f"{info.field_name} must contain sorted unique IDs")
        return values

    @model_validator(mode="after")
    def validate_one_to_one_coverage(self) -> SpatialCoverage:
        if self.source_task_ids != self.projected_active_task_ids:
            raise ValueError("every source task must have exactly one active spatial route")
        if self.changed_candidate_task_ids != self.projected_candidate_task_ids:
            raise ValueError("every changed candidate task must have exactly one candidate route")
        if self.source_resource_ids != self.projected_resource_ids:
            raise ValueError("every source resource must have exactly one spatial marker")
        if self.source_event_ids != self.projected_event_ids:
            raise ValueError("every source event must have exactly one spatial marker")
        return self


class SpatialFactCategory(str, Enum):
    CONTEXT = "context"
    COVERAGE = "coverage"
    TASK = "task"
    RESOURCE = "resource"
    EVENT = "event"
    PLAN = "plan"


class SpatialFact(ModelBase):
    fact_id: str = Field(pattern=r"^SPATIAL-FACT-[A-Z0-9-]+$")
    category: SpatialFactCategory
    claim: str = Field(min_length=1, max_length=360)
    entity_ids: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("entity_ids")
    @classmethod
    def require_unique_entities(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("spatial fact entity IDs must be unique")
        return values


class RuntimeSpatialView(ModelBase):
    scenario_id: str = Field(min_length=1)
    scenario_version: int = Field(ge=1)
    layout: SpatialLayout
    overlay: RuntimeSpatialOverlay
    coverage: SpatialCoverage
    facts: list[SpatialFact] = Field(min_length=1)
    requires_human_confirmation: Literal[True] = True
    modifies_runtime: Literal[False] = False
    safety_notice: Literal[
        "仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。"
    ] = SAFETY_NOTICE

    @model_validator(mode="after")
    def validate_view_integrity(self) -> RuntimeSpatialView:
        if (
            self.layout.scenario_id != self.scenario_id
            or self.layout.scenario_version != self.scenario_version
        ):
            raise ValueError("spatial layout must match the runtime scenario revision")
        if self.overlay.layout_id != self.layout.layout_id:
            raise ValueError("spatial overlay must reference the returned layout")

        zone_ids = {zone.zone_id for zone in self.layout.zones}
        path_by_id = {path.path_id: path for path in self.layout.paths}
        for route in self.overlay.task_routes:
            if route.origin_zone_id not in zone_ids or route.destination_zone_id not in zone_ids:
                raise ValueError("task route references an unknown spatial zone")
            self._validate_legs(route.legs, zone_ids, path_by_id)
        for marker in self.overlay.resource_markers:
            if marker.from_zone_id not in zone_ids or (
                marker.to_zone_id is not None and marker.to_zone_id not in zone_ids
            ):
                raise ValueError("resource marker references an unknown spatial zone")
            self._validate_legs(marker.route_legs, zone_ids, path_by_id)
        for marker in self.overlay.event_markers:
            if marker.primary_zone_id not in zone_ids:
                raise ValueError("event marker references an unknown spatial zone")
            self._validate_legs(marker.route_legs, zone_ids, path_by_id)

        active_ids = sorted(
            route.task_id
            for route in self.overlay.task_routes
            if route.route_kind is SpatialRouteKind.ACTIVE
        )
        candidate_ids = sorted(
            route.task_id
            for route in self.overlay.task_routes
            if route.route_kind is SpatialRouteKind.CANDIDATE
        )
        if active_ids != self.coverage.projected_active_task_ids:
            raise ValueError("task route coverage does not match the overlay")
        if candidate_ids != self.coverage.projected_candidate_task_ids:
            raise ValueError("candidate route coverage does not match the overlay")
        if sorted(marker.resource_id for marker in self.overlay.resource_markers) != (
            self.coverage.projected_resource_ids
        ):
            raise ValueError("resource coverage does not match the overlay")
        if sorted(marker.event_id for marker in self.overlay.event_markers) != (
            self.coverage.projected_event_ids
        ):
            raise ValueError("event coverage does not match the overlay")
        fact_ids = [fact.fact_id for fact in self.facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("spatial fact IDs must be unique")
        return self

    @staticmethod
    def _validate_legs(
        legs: list[SpatialRouteLeg],
        zone_ids: set[str],
        path_by_id: dict[str, SpatialPath],
    ) -> None:
        previous_zone_id: str | None = None
        for leg in legs:
            path = path_by_id.get(leg.path_id)
            if path is None or leg.from_zone_id not in zone_ids or leg.to_zone_id not in zone_ids:
                raise ValueError("spatial route leg references unknown geometry")
            expected = (
                (path.from_zone_id, path.to_zone_id, "forward")
                if leg.traversal == "forward"
                else (path.to_zone_id, path.from_zone_id, "reverse")
            )
            if (leg.from_zone_id, leg.to_zone_id, leg.traversal) != expected:
                raise ValueError("spatial route leg direction does not match its path")
            if previous_zone_id is not None and previous_zone_id != leg.from_zone_id:
                raise ValueError("spatial route legs must form one continuous route")
            previous_zone_id = leg.to_zone_id
