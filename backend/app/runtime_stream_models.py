"""Typed SSE envelopes for the M4 runtime stream."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, computed_field, field_validator, model_validator

from .models import ModelBase
from .runtime_models import (
    SESSION_ID_PATTERN,
    STREAM_ID_PATTERN,
    ReplanTrigger,
    RuntimeClockSnapshot,
    RuntimeSessionSnapshot,
    TaskRuntimeStatus,
    _require_aware,
    _require_unique,
)


class RuntimeSnapshotStreamPayload(ModelBase):
    event_type: Literal["runtime.snapshot"] = "runtime.snapshot"
    snapshot: RuntimeSessionSnapshot


class RuntimeTickStreamPayload(ModelBase):
    event_type: Literal["runtime.tick"] = "runtime.tick"
    clock: RuntimeClockSnapshot


class TaskTransitionStreamPayload(ModelBase):
    event_type: Literal["task.transition"] = "task.transition"
    task_id: str = Field(min_length=1)
    previous_status: TaskRuntimeStatus
    current_status: TaskRuntimeStatus
    transition_at: datetime

    @field_validator("transition_at")
    @classmethod
    def validate_transition_at(cls, value: datetime) -> datetime:
        return _require_aware(value, "transition_at")

    @model_validator(mode="after")
    def validate_status_change(self) -> TaskTransitionStreamPayload:
        if self.previous_status == self.current_status:
            raise ValueError("task transition must change status")
        return self


class EventAppliedStreamPayload(ModelBase):
    event_type: Literal["event.applied"] = "event.applied"
    event_ids: list[str] = Field(min_length=1)
    scenario_version_before: int = Field(ge=1)
    scenario_version_after: int = Field(ge=1)

    @field_validator("event_ids")
    @classmethod
    def validate_event_ids(cls, value: list[str]) -> list[str]:
        return _require_unique(value, "event_ids")

    @model_validator(mode="after")
    def validate_version_increment(self) -> EventAppliedStreamPayload:
        if self.scenario_version_after != self.scenario_version_before + 1:
            raise ValueError("one atomic event batch must increment scenario version by one")
        return self


class ReplanStartedStreamPayload(ModelBase):
    event_type: Literal["replan.started"] = "replan.started"
    trigger: ReplanTrigger
    event_ids: list[str] = Field(default_factory=list)

    @field_validator("event_ids")
    @classmethod
    def validate_event_ids(cls, value: list[str]) -> list[str]:
        return _require_unique(value, "event_ids")


class ReplanReadyStreamPayload(ModelBase):
    event_type: Literal["replan.ready"] = "replan.ready"
    active_plan_id: str = Field(min_length=1)
    candidate_plan_id: str = Field(min_length=1)
    affected_task_ids: list[str] = Field(default_factory=list)
    violation_count: Literal[0] = 0

    @field_validator("affected_task_ids")
    @classmethod
    def validate_affected_task_ids(cls, value: list[str]) -> list[str]:
        return _require_unique(value, "affected_task_ids")

    @model_validator(mode="after")
    def validate_candidate(self) -> ReplanReadyStreamPayload:
        if self.active_plan_id == self.candidate_plan_id:
            raise ValueError("candidate plan must differ from active plan")
        return self


class ReplanFailedStreamPayload(ModelBase):
    event_type: Literal["replan.failed"] = "replan.failed"
    error_code: str = Field(pattern=r"^[a-z0-9_]+$")
    message: str = Field(min_length=1, max_length=240)
    fallback_available: bool


class PlanAcceptedStreamPayload(ModelBase):
    event_type: Literal["plan.accepted"] = "plan.accepted"
    previous_plan_id: str = Field(min_length=1)
    active_plan_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_plan_change(self) -> PlanAcceptedStreamPayload:
        if self.previous_plan_id == self.active_plan_id:
            raise ValueError("accepted plan must differ from previous plan")
        return self


class PlanRejectedStreamPayload(ModelBase):
    event_type: Literal["plan.rejected"] = "plan.rejected"
    active_plan_id: str = Field(min_length=1)
    rejected_plan_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_rejected_plan(self) -> PlanRejectedStreamPayload:
        if self.active_plan_id == self.rejected_plan_id:
            raise ValueError("rejected plan must differ from active plan")
        return self


class RuntimeCompletedStreamPayload(ModelBase):
    event_type: Literal["runtime.completed"] = "runtime.completed"
    completed_at: datetime
    completed_task_count: int = Field(ge=0)
    unassigned_task_count: int = Field(ge=0)

    @field_validator("completed_at")
    @classmethod
    def validate_completed_at(cls, value: datetime) -> datetime:
        return _require_aware(value, "completed_at")


class HeartbeatStreamPayload(ModelBase):
    event_type: Literal["heartbeat"] = "heartbeat"
    server_time: datetime

    @field_validator("server_time")
    @classmethod
    def validate_server_time(cls, value: datetime) -> datetime:
        return _require_aware(value, "server_time")


RuntimeStreamPayload = Annotated[
    RuntimeSnapshotStreamPayload
    | RuntimeTickStreamPayload
    | TaskTransitionStreamPayload
    | EventAppliedStreamPayload
    | ReplanStartedStreamPayload
    | ReplanReadyStreamPayload
    | ReplanFailedStreamPayload
    | PlanAcceptedStreamPayload
    | PlanRejectedStreamPayload
    | RuntimeCompletedStreamPayload
    | HeartbeatStreamPayload,
    Field(discriminator="event_type"),
]


class RuntimeStreamEvent(ModelBase):
    stream_id: str = Field(pattern=STREAM_ID_PATTERN)
    sequence: int = Field(ge=1)
    session_id: str = Field(pattern=SESSION_ID_PATTERN)
    revision: int = Field(ge=1)
    emitted_at: datetime
    payload: RuntimeStreamPayload

    @field_validator("emitted_at")
    @classmethod
    def validate_emitted_at(cls, value: datetime) -> datetime:
        return _require_aware(value, "emitted_at")

    @computed_field(return_type=str)
    @property
    def sse_id(self) -> str:
        return f"{self.stream_id}:{self.sequence}"
