"""Persisted planning results shared by the scheduler, API, and frontend."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field, model_validator

from .models import ModelBase, ResourceType


class PlanStatus(str, Enum):
    EXECUTABLE = "executable"
    PARTIAL = "partial"
    INVALID = "invalid"


class UnassignedReason(str, Enum):
    NO_COMPATIBLE_RESOURCE = "no_compatible_resource"
    RESOURCE_UNAVAILABLE = "resource_unavailable"
    NO_ROUTE = "no_route"
    TIME_WINDOW = "time_window"
    PRIORITY_TRADEOFF = "priority_tradeoff"


class ConstraintCode(str, Enum):
    UNKNOWN_TASK = "unknown_task"
    UNKNOWN_RESOURCE = "unknown_resource"
    DUPLICATE_ASSIGNMENT = "duplicate_assignment"
    TASK_COVERAGE = "task_coverage"
    RESOURCE_TYPE = "resource_type"
    RESOURCE_CAPACITY = "resource_capacity"
    RESOURCE_WINDOW = "resource_window"
    RESOURCE_OVERLAP = "resource_overlap"
    TASK_ROUTE = "task_route"
    TASK_WINDOW = "task_window"
    TASK_DURATION = "task_duration"
    TRAVEL_TIME = "travel_time"
    WAIT_TIME = "wait_time"


class Assignment(ModelBase):
    assignment_id: str = Field(min_length=1, pattern=r"^ASG-[A-Z0-9-]+$")
    task_id: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    resource_type: ResourceType
    resource_start_zone_id: str = Field(min_length=1)
    origin_zone_id: str = Field(min_length=1)
    destination_zone_id: str = Field(min_length=1)
    travel_started_at: datetime
    travel_ended_at: datetime
    service_started_at: datetime
    service_ended_at: datetime
    reposition_minutes: int = Field(ge=0)
    service_minutes: int = Field(gt=0)
    wait_minutes: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_timeline(self) -> Assignment:
        timestamps = (
            self.travel_started_at,
            self.travel_ended_at,
            self.service_started_at,
            self.service_ended_at,
        )
        if any(value.tzinfo is None or value.utcoffset() is None for value in timestamps):
            raise ValueError("assignment timestamps must include timezone offsets")
        if self.travel_started_at > self.travel_ended_at:
            raise ValueError("travel_started_at must not be after travel_ended_at")
        if self.travel_ended_at > self.service_started_at:
            raise ValueError("travel must finish before service starts")
        if self.service_started_at >= self.service_ended_at:
            raise ValueError("service_started_at must be before service_ended_at")
        return self


class UnassignedTask(ModelBase):
    task_id: str = Field(min_length=1)
    reason: UnassignedReason
    detail: str = Field(min_length=1, max_length=240)


class ConstraintViolation(ModelBase):
    code: ConstraintCode
    message: str = Field(min_length=1, max_length=320)
    task_id: str | None = None
    resource_id: str | None = None


class ResourceMetric(ModelBase):
    resource_id: str = Field(min_length=1)
    resource_type: ResourceType
    busy_minutes: int = Field(ge=0)
    available_minutes: int = Field(gt=0)
    utilization_pct: float = Field(ge=0, le=100)


class PlanMetric(ModelBase):
    total_tasks: int = Field(ge=0)
    assigned_tasks: int = Field(ge=0)
    unassigned_tasks: int = Field(ge=0)
    average_wait_minutes: float = Field(ge=0)
    max_wait_minutes: int = Field(ge=0)
    task_completion_rate_pct: float = Field(ge=0, le=100)
    critical_task_completion_rate_pct: float = Field(ge=0, le=100)
    overall_resource_utilization_pct: float = Field(ge=0, le=100)
    resource_metrics: list[ResourceMetric] = Field(default_factory=list)


class Plan(ModelBase):
    plan_id: str = Field(min_length=1, pattern=r"^PLAN-[A-Z0-9-]+$")
    scenario_id: str = Field(min_length=1)
    scenario_version: int = Field(ge=1)
    algorithm: str = Field(min_length=1)
    generated_at: datetime
    status: PlanStatus
    assignments: list[Assignment] = Field(default_factory=list)
    unassigned_tasks: list[UnassignedTask] = Field(default_factory=list)
    violations: list[ConstraintViolation] = Field(default_factory=list)
    metrics: PlanMetric

    @model_validator(mode="after")
    def validate_integrity(self) -> Plan:
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError("generated_at must include a timezone offset")
        if self.violations and self.status is not PlanStatus.INVALID:
            raise ValueError("a plan with hard-constraint violations must be invalid")
        if not self.violations and self.status is PlanStatus.INVALID:
            raise ValueError("an invalid plan must include at least one violation")
        if self.unassigned_tasks and self.status is PlanStatus.EXECUTABLE:
            raise ValueError("an executable plan cannot contain unassigned tasks")
        if not self.unassigned_tasks and self.status is PlanStatus.PARTIAL:
            raise ValueError("a partial plan must contain at least one unassigned task")
        return self
