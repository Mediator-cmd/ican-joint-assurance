"""Frozen M4 runtime contracts shared by the API, persistence, and frontend."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field, computed_field, field_validator, model_validator

from .demo_export import SAFETY_NOTICE
from .models import FlightEventType, FlightStatus, ModelBase
from .planning_models import Plan
from .planning_objectives import PlanningObjectiveProfile


SESSION_ID_PATTERN = r"^RUN-[A-Z0-9-]+$"
STREAM_ID_PATTERN = r"^STREAM-[A-Z0-9-]+$"


def _require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone offset")
    return value


def _require_unique(values: list[str], field_name: str) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must contain unique IDs")
    return values


class SimulationSpeed(int, Enum):
    REAL_TIME = 1
    FAST = 5
    DEMO = 15


class RuntimeStatus(str, Enum):
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    REPLANNING = "replanning"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"


RUNTIME_STATUS_LABELS = {
    RuntimeStatus.READY: "准备就绪",
    RuntimeStatus.RUNNING: "运行中",
    RuntimeStatus.PAUSED: "已暂停",
    RuntimeStatus.REPLANNING: "正在重新规划",
    RuntimeStatus.AWAITING_CONFIRMATION: "等待确认新方案",
    RuntimeStatus.COMPLETED: "运行已完成",
    RuntimeStatus.FAILED: "运行异常",
}


class TaskRuntimeStatus(str, Enum):
    PENDING = "pending"
    EN_ROUTE = "en_route"
    WAITING = "waiting"
    IN_SERVICE = "in_service"
    COMPLETED = "completed"
    UNASSIGNED = "unassigned"
    AFFECTED = "affected"


TASK_STATUS_LABELS = {
    TaskRuntimeStatus.PENDING: "待出发",
    TaskRuntimeStatus.EN_ROUTE: "前往服务点",
    TaskRuntimeStatus.WAITING: "现场等待",
    TaskRuntimeStatus.IN_SERVICE: "保障中",
    TaskRuntimeStatus.COMPLETED: "已完成",
    TaskRuntimeStatus.UNASSIGNED: "待协调",
    TaskRuntimeStatus.AFFECTED: "受扰动待确认",
}


class ResourceRuntimeStatus(str, Enum):
    IDLE = "idle"
    MOVING = "moving"
    WAITING = "waiting"
    SERVING = "serving"
    UNAVAILABLE = "unavailable"


RESOURCE_STATUS_LABELS = {
    ResourceRuntimeStatus.IDLE: "空闲待命",
    ResourceRuntimeStatus.MOVING: "前往任务点",
    ResourceRuntimeStatus.WAITING: "现场待命",
    ResourceRuntimeStatus.SERVING: "保障作业中",
    ResourceRuntimeStatus.UNAVAILABLE: "暂不可用",
}


class RuntimeEventStatus(str, Enum):
    PENDING = "pending"
    TRIGGERED = "triggered"
    APPLIED = "applied"
    REPLANNING = "replanning"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    RESOLVED = "resolved"
    FAILED = "failed"


EVENT_STATUS_LABELS = {
    RuntimeEventStatus.PENDING: "等待发生",
    RuntimeEventStatus.TRIGGERED: "已触发",
    RuntimeEventStatus.APPLIED: "已应用",
    RuntimeEventStatus.REPLANNING: "正在重新规划",
    RuntimeEventStatus.AWAITING_CONFIRMATION: "方案待确认",
    RuntimeEventStatus.RESOLVED: "处理完成",
    RuntimeEventStatus.FAILED: "处理失败",
}


class ReplanTrigger(str, Enum):
    AUTOMATIC_EVENT = "automatic_event"
    MANUAL = "manual"
    RESOURCE_CHANGE = "resource_change"
    PLAN_INVALID = "plan_invalid"


class RuntimePosition(ModelBase):
    from_zone_id: str = Field(min_length=1)
    to_zone_id: str | None = Field(default=None, min_length=1)
    progress_pct: float = Field(default=0, ge=0, le=100)

    @model_validator(mode="after")
    def validate_stationary_progress(self) -> RuntimePosition:
        if self.to_zone_id is None and self.progress_pct != 0:
            raise ValueError("stationary position must have zero progress")
        return self


class RuntimeClockSnapshot(ModelBase):
    simulation_time: datetime
    server_time: datetime
    speed: SimulationSpeed
    is_advancing: bool
    next_boundary_at: datetime | None = None

    @field_validator("simulation_time", "server_time", "next_boundary_at")
    @classmethod
    def validate_timestamps(cls, value: datetime | None, info) -> datetime | None:
        if value is not None:
            _require_aware(value, info.field_name)
        return value

    @model_validator(mode="after")
    def validate_next_boundary(self) -> RuntimeClockSnapshot:
        if self.next_boundary_at is not None and self.next_boundary_at < self.simulation_time:
            raise ValueError("next_boundary_at cannot be before simulation_time")
        return self


class TaskRuntimeProjection(ModelBase):
    task_id: str = Field(min_length=1)
    status: TaskRuntimeStatus
    assignment_id: str | None = Field(default=None, min_length=1)
    resource_id: str | None = Field(default=None, min_length=1)
    current_zone_id: str | None = Field(default=None, min_length=1)
    next_transition_at: datetime | None = None
    affected_by_event_ids: list[str] = Field(default_factory=list)

    @field_validator("next_transition_at")
    @classmethod
    def validate_next_transition(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            _require_aware(value, "next_transition_at")
        return value

    @field_validator("affected_by_event_ids")
    @classmethod
    def validate_affected_events(cls, value: list[str]) -> list[str]:
        return _require_unique(value, "affected_by_event_ids")

    @model_validator(mode="after")
    def validate_assignment_reference(self) -> TaskRuntimeProjection:
        scheduled_statuses = {
            TaskRuntimeStatus.PENDING,
            TaskRuntimeStatus.EN_ROUTE,
            TaskRuntimeStatus.WAITING,
            TaskRuntimeStatus.IN_SERVICE,
            TaskRuntimeStatus.COMPLETED,
        }
        if self.status in scheduled_statuses and (
            self.assignment_id is None or self.resource_id is None
        ):
            raise ValueError("scheduled task status requires assignment_id and resource_id")
        if self.status is TaskRuntimeStatus.UNASSIGNED and (
            self.assignment_id is not None or self.resource_id is not None
        ):
            raise ValueError("unassigned task cannot reference an assignment or resource")
        if self.status is TaskRuntimeStatus.AFFECTED and not self.affected_by_event_ids:
            raise ValueError("affected task requires at least one event ID")
        if self.status is TaskRuntimeStatus.AFFECTED and (
            self.assignment_id is None or self.resource_id is None
        ):
            raise ValueError("affected task requires its current assignment and resource")
        return self

    @computed_field(return_type=str)
    @property
    def status_label(self) -> str:
        return TASK_STATUS_LABELS[self.status]

    @computed_field(return_type=bool)
    @property
    def is_locked(self) -> bool:
        return self.status in {
            TaskRuntimeStatus.EN_ROUTE,
            TaskRuntimeStatus.WAITING,
            TaskRuntimeStatus.IN_SERVICE,
            TaskRuntimeStatus.COMPLETED,
        }


class ResourceRuntimeProjection(ModelBase):
    resource_id: str = Field(min_length=1)
    status: ResourceRuntimeStatus
    position: RuntimePosition
    current_task_id: str | None = Field(default=None, min_length=1)
    next_task_id: str | None = Field(default=None, min_length=1)
    next_available_at: datetime | None = None

    @field_validator("next_available_at")
    @classmethod
    def validate_next_available(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            _require_aware(value, "next_available_at")
        return value

    @model_validator(mode="after")
    def validate_activity(self) -> ResourceRuntimeProjection:
        active_statuses = {
            ResourceRuntimeStatus.MOVING,
            ResourceRuntimeStatus.WAITING,
            ResourceRuntimeStatus.SERVING,
        }
        if self.status in active_statuses and self.current_task_id is None:
            raise ValueError("active resource status requires current_task_id")
        if self.status is ResourceRuntimeStatus.MOVING and self.position.to_zone_id is None:
            raise ValueError("moving resource requires a target zone")
        if self.status in {
            ResourceRuntimeStatus.IDLE,
            ResourceRuntimeStatus.WAITING,
            ResourceRuntimeStatus.UNAVAILABLE,
        } and self.position.to_zone_id is not None:
            raise ValueError("stationary resource status cannot have a target zone")
        if self.status in {ResourceRuntimeStatus.IDLE, ResourceRuntimeStatus.UNAVAILABLE} and (
            self.current_task_id is not None
        ):
            raise ValueError("idle or unavailable resource cannot have a current task")
        return self

    @computed_field(return_type=str)
    @property
    def status_label(self) -> str:
        return RESOURCE_STATUS_LABELS[self.status]


class FlightRuntimeProjection(ModelBase):
    flight_id: str = Field(min_length=1)
    status: FlightStatus
    estimated_departure: datetime
    gate_id: str = Field(min_length=1)
    last_event_id: str | None = Field(default=None, min_length=1)

    @field_validator("estimated_departure")
    @classmethod
    def validate_estimated_departure(cls, value: datetime) -> datetime:
        return _require_aware(value, "estimated_departure")


class EventRuntimeProjection(ModelBase):
    event_id: str = Field(min_length=1)
    event_type: FlightEventType
    flight_id: str = Field(min_length=1)
    detail: str = Field(min_length=1, max_length=240)
    note: str | None = Field(default=None, max_length=240)
    status: RuntimeEventStatus
    occurred_at: datetime
    applied_at: datetime | None = None
    scenario_version_after: int | None = Field(default=None, ge=1)
    candidate_plan_id: str | None = Field(default=None, min_length=1)
    failure_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]+$")

    @field_validator("occurred_at", "applied_at")
    @classmethod
    def validate_event_timestamps(cls, value: datetime | None, info) -> datetime | None:
        if value is not None:
            _require_aware(value, info.field_name)
        return value

    @model_validator(mode="after")
    def validate_event_result(self) -> EventRuntimeProjection:
        applied_statuses = {
            RuntimeEventStatus.APPLIED,
            RuntimeEventStatus.REPLANNING,
            RuntimeEventStatus.AWAITING_CONFIRMATION,
            RuntimeEventStatus.RESOLVED,
        }
        if self.status in applied_statuses and (
            self.applied_at is None or self.scenario_version_after is None
        ):
            raise ValueError("applied event status requires applied_at and scenario version")
        if self.status is RuntimeEventStatus.AWAITING_CONFIRMATION and self.candidate_plan_id is None:
            raise ValueError("event awaiting confirmation requires candidate_plan_id")
        if self.candidate_plan_id is not None and self.status not in {
            RuntimeEventStatus.AWAITING_CONFIRMATION,
            RuntimeEventStatus.RESOLVED,
        }:
            raise ValueError("candidate_plan_id is only valid for confirmation or resolved states")
        if self.status is RuntimeEventStatus.FAILED and self.failure_code is None:
            raise ValueError("failed event requires failure_code")
        if self.status is not RuntimeEventStatus.FAILED and self.failure_code is not None:
            raise ValueError("failure_code is only valid for a failed event")
        return self

    @computed_field(return_type=str)
    @property
    def status_label(self) -> str:
        return EVENT_STATUS_LABELS[self.status]


class RuntimeFailure(ModelBase):
    code: str = Field(pattern=r"^[a-z0-9_]+$")
    message: str = Field(min_length=1, max_length=240)
    recoverable: bool


class RuntimeGuidance(ModelBase):
    headline: str = Field(min_length=1, max_length=80)
    detail: str = Field(min_length=1, max_length=240)
    action_required: bool
    recommended_action: str = Field(min_length=1, max_length=160)


class RuntimeSessionSnapshot(ModelBase):
    session_id: str = Field(pattern=SESSION_ID_PATTERN)
    scenario_id: str = Field(min_length=1)
    initial_scenario_version: int = Field(ge=1)
    current_scenario_version: int = Field(ge=1)
    initial_plan_id: str = Field(min_length=1)
    active_plan_id: str = Field(min_length=1)
    candidate_plan_id: str | None = Field(default=None, min_length=1)
    active_plan_detail: Plan | None = None
    candidate_plan_detail: Plan | None = None
    objective_profile: PlanningObjectiveProfile = PlanningObjectiveProfile.BALANCED
    status: RuntimeStatus
    revision: int = Field(ge=1)
    clock: RuntimeClockSnapshot
    tasks: list[TaskRuntimeProjection] = Field(default_factory=list)
    resources: list[ResourceRuntimeProjection] = Field(default_factory=list)
    flights: list[FlightRuntimeProjection] = Field(default_factory=list)
    events: list[EventRuntimeProjection] = Field(default_factory=list)
    guidance: RuntimeGuidance
    failure: RuntimeFailure | None = None
    created_at: datetime
    updated_at: datetime
    storage_scope: Literal["sqlite"] = "sqlite"
    safety_notice: Literal[
        "仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。"
    ] = SAFETY_NOTICE

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_snapshot_timestamps(cls, value: datetime, info) -> datetime:
        return _require_aware(value, info.field_name)

    @model_validator(mode="after")
    def validate_snapshot_state(self) -> RuntimeSessionSnapshot:
        if self.current_scenario_version < self.initial_scenario_version:
            raise ValueError("current scenario version cannot precede initial version")
        if self.status is RuntimeStatus.RUNNING and not self.clock.is_advancing:
            raise ValueError("running session requires an advancing clock")
        if self.status is not RuntimeStatus.RUNNING and self.clock.is_advancing:
            raise ValueError("only a running session may have an advancing clock")
        if self.status is RuntimeStatus.AWAITING_CONFIRMATION:
            if self.candidate_plan_id is None:
                raise ValueError("awaiting confirmation requires candidate_plan_id")
        elif self.candidate_plan_id is not None:
            raise ValueError("candidate_plan_id is only valid while awaiting confirmation")
        if (
            self.active_plan_detail is not None
            and self.active_plan_detail.plan_id != self.active_plan_id
        ):
            raise ValueError("active plan detail must match active_plan_id")
        if self.candidate_plan_detail is not None:
            if self.candidate_plan_id is None:
                raise ValueError("candidate plan detail requires candidate_plan_id")
            if self.candidate_plan_detail.plan_id != self.candidate_plan_id:
                raise ValueError("candidate plan detail must match candidate_plan_id")
        if self.status is RuntimeStatus.FAILED:
            if self.failure is None:
                raise ValueError("failed session requires failure details")
        elif self.failure is not None:
            raise ValueError("failure details are only valid for a failed session")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be before created_at")

        for field_name, values in (
            ("tasks", [item.task_id for item in self.tasks]),
            ("resources", [item.resource_id for item in self.resources]),
            ("flights", [item.flight_id for item in self.flights]),
            ("events", [item.event_id for item in self.events]),
        ):
            _require_unique(values, field_name)
        return self

    @computed_field(return_type=str)
    @property
    def status_label(self) -> str:
        return RUNTIME_STATUS_LABELS[self.status]


class RuntimeSessionSummary(ModelBase):
    session_id: str = Field(pattern=SESSION_ID_PATTERN)
    scenario_id: str = Field(min_length=1)
    current_scenario_version: int = Field(ge=1)
    active_plan_id: str = Field(min_length=1)
    status: RuntimeStatus
    revision: int = Field(ge=1)
    simulation_time: datetime
    speed: SimulationSpeed
    action_required: bool
    updated_at: datetime

    @field_validator("simulation_time", "updated_at")
    @classmethod
    def validate_summary_timestamps(cls, value: datetime, info) -> datetime:
        return _require_aware(value, info.field_name)

    @computed_field(return_type=str)
    @property
    def status_label(self) -> str:
        return RUNTIME_STATUS_LABELS[self.status]


class RuntimeSessionListResponse(ModelBase):
    items: list[RuntimeSessionSummary] = Field(default_factory=list)
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    storage_scope: Literal["sqlite"] = "sqlite"
    safety_notice: Literal[
        "仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。"
    ] = SAFETY_NOTICE

    @model_validator(mode="after")
    def validate_total_and_ids(self) -> RuntimeSessionListResponse:
        if self.total < len(self.items):
            raise ValueError("total cannot be smaller than the returned page")
        _require_unique([item.session_id for item in self.items], "items")
        return self


class CreateRuntimeSessionRequest(ModelBase):
    scenario_id: str = Field(min_length=1)
    scenario_version: int = Field(ge=1)
    active_plan_id: str = Field(min_length=1)
    speed: SimulationSpeed = SimulationSpeed.REAL_TIME


class RuntimeRevisionRequest(ModelBase):
    expected_revision: int = Field(ge=1)


class SetRuntimeSpeedRequest(RuntimeRevisionRequest):
    speed: SimulationSpeed


class ResetRuntimeSessionRequest(RuntimeRevisionRequest):
    confirm_reset: Literal[True]


class ReplanRuntimeSessionRequest(RuntimeRevisionRequest):
    reason: str | None = Field(default=None, max_length=240)
    objective_profile: PlanningObjectiveProfile | None = None
    confirm_objective: bool = False

    @model_validator(mode="after")
    def validate_objective_confirmation(self) -> ReplanRuntimeSessionRequest:
        if self.objective_profile is None and self.confirm_objective:
            raise ValueError("objective confirmation requires an explicit profile")
        if self.objective_profile is not None and not self.confirm_objective:
            raise ValueError("an explicit planning objective requires confirmation")
        return self


class CandidateDecisionRequest(RuntimeRevisionRequest):
    candidate_plan_id: str = Field(min_length=1)
    reason: str | None = Field(default=None, max_length=240)
