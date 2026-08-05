"""HTTP response models shared by the versioned API surface."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from .models import DataClassification, FlightEvent, ModelBase, RunMode, Scenario
from .planning_models import Plan, PlanStatus
from .planning_objectives import PlanningObjectiveProfile


class HealthResponse(ModelBase):
    service: Literal["joint-assurance-api"] = "joint-assurance-api"
    version: str = Field(min_length=1)
    api_version: Literal["v1"] = "v1"
    status: Literal["ok"] = "ok"


class ApiErrorDetail(ModelBase):
    location: list[str | int] = Field(default_factory=list)
    message: str = Field(min_length=1)
    type: str = Field(min_length=1)


class ApiErrorBody(ModelBase):
    code: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
    message: str = Field(min_length=1)
    details: list[ApiErrorDetail] = Field(default_factory=list)
    request_id: str = Field(pattern=r"^REQ-[A-F0-9]{12}$")


class ApiErrorResponse(ModelBase):
    error: ApiErrorBody


class ScenarioOperationalSummary(ModelBase):
    flight_count: int = Field(ge=0)
    task_count: int = Field(ge=0)
    resource_count: int = Field(ge=0)
    zone_count: int = Field(ge=0)
    pending_event_count: int = Field(ge=0)
    applied_event_count: int = Field(ge=0)
    ready_for_planning: bool
    warnings: list[str] = Field(default_factory=list)
    recommended_action: str = Field(min_length=1)


class ScenarioSummary(ModelBase):
    scenario_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: int = Field(ge=1)
    run_mode: RunMode
    data_classification: DataClassification
    operational: ScenarioOperationalSummary


class ScenarioRecord(ModelBase):
    summary: ScenarioSummary
    scenario: Scenario
    current_version: int = Field(ge=1)
    selected_version: int = Field(ge=1)
    is_current_version: bool
    available_versions: list[int] = Field(min_length=1)
    pending_event_ids: list[str] = Field(default_factory=list)
    applied_event_ids: list[str] = Field(default_factory=list)
    storage_scope: Literal["process_memory"] = "process_memory"
    safety_notice: str = Field(min_length=1)


class ScenarioListResponse(ModelBase):
    items: list[ScenarioSummary] = Field(default_factory=list)
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    storage_scope: Literal["process_memory"] = "process_memory"


class ApplyEventsRequest(ModelBase):
    expected_version: int = Field(ge=1)
    event_ids: list[str] = Field(default_factory=list)
    events: list[FlightEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_event_batch(self) -> ApplyEventsRequest:
        new_event_ids = [event.event_id for event in self.events]
        combined_ids = [*self.event_ids, *new_event_ids]
        if not combined_ids:
            raise ValueError("event_ids and events must include at least one event")
        if any(not event_id for event_id in self.event_ids):
            raise ValueError("event IDs must be non-empty")
        if len(combined_ids) != len(set(combined_ids)):
            raise ValueError("event IDs must be unique within one request")
        return self


class PlanAlgorithm(str, Enum):
    FIFO = "fifo"
    CP_SAT = "cp_sat"


class CreatePlanRequest(ModelBase):
    expected_version: int = Field(ge=1)
    algorithm: PlanAlgorithm
    max_time_seconds: float = Field(default=5.0, gt=0, le=30)
    objective_profile: PlanningObjectiveProfile | None = None
    confirm_objective: bool = False

    @model_validator(mode="after")
    def validate_objective_confirmation(self) -> CreatePlanRequest:
        if self.algorithm is PlanAlgorithm.FIFO:
            if self.objective_profile is not None or self.confirm_objective:
                raise ValueError("FIFO does not accept an optimization objective")
            return self
        if self.objective_profile is None and self.confirm_objective:
            raise ValueError("objective confirmation requires an explicit profile")
        if self.objective_profile is not None and not self.confirm_objective:
            raise ValueError("an explicit planning objective requires confirmation")
        return self

    @property
    def applied_objective(self) -> PlanningObjectiveProfile:
        return self.objective_profile or PlanningObjectiveProfile.BALANCED


class PlanGuidance(ModelBase):
    display_name: str = Field(min_length=1)
    status_label: str = Field(min_length=1)
    result_summary: str = Field(min_length=1)
    tradeoff_summary: str = Field(min_length=1)
    recommended_action: str = Field(min_length=1)
    calculation_basis: list[str] = Field(min_length=1)
    requires_human_confirmation: Literal[True] = True


class PlanRecord(ModelBase):
    plan: Plan
    guidance: PlanGuidance
    storage_scope: Literal["process_memory"] = "process_memory"
    safety_notice: str = Field(min_length=1)


class PlanSummary(ModelBase):
    plan_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    scenario_version: int = Field(ge=1)
    algorithm: PlanAlgorithm
    objective_profile: PlanningObjectiveProfile
    display_name: str = Field(min_length=1)
    status: PlanStatus
    status_label: str = Field(min_length=1)
    assigned_tasks: int = Field(ge=0)
    total_tasks: int = Field(ge=0)
    urgent_task_completion_rate_pct: float = Field(ge=0, le=100)
    average_wait_minutes: float = Field(ge=0)
    needs_manual_handling: int = Field(ge=0)
    constraint_conflicts: int = Field(ge=0)
    result_summary: str = Field(min_length=1)
    tradeoff_summary: str = Field(min_length=1)
    recommended_action: str = Field(min_length=1)


class PlanListResponse(ModelBase):
    items: list[PlanSummary] = Field(default_factory=list)
    total: int = Field(ge=0)
    storage_scope: Literal["process_memory"] = "process_memory"


class ComparePlansRequest(ModelBase):
    baseline_plan_id: str = Field(min_length=1)
    candidate_plan_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_distinct_plans(self) -> ComparePlansRequest:
        if self.baseline_plan_id == self.candidate_plan_id:
            raise ValueError("baseline and candidate plans must be different")
        return self


class PlanComparisonMetrics(ModelBase):
    plan_id: str = Field(min_length=1)
    scenario_version: int = Field(ge=1)
    algorithm: PlanAlgorithm
    assigned_tasks: int = Field(ge=0)
    unassigned_tasks: int = Field(ge=0)
    total_tasks: int = Field(ge=0)
    task_completion_rate_pct: float = Field(ge=0, le=100)
    critical_task_completion_rate_pct: float = Field(ge=0, le=100)
    average_wait_minutes: float = Field(ge=0)
    max_wait_minutes: int = Field(ge=0)
    overall_resource_utilization_pct: float = Field(ge=0, le=100)
    violation_count: int = Field(ge=0)


class PlanComparisonDelta(ModelBase):
    assigned_tasks: int
    unassigned_tasks: int
    total_tasks: int
    task_completion_rate_pct: float
    critical_task_completion_rate_pct: float
    average_wait_minutes: float
    max_wait_minutes: int
    overall_resource_utilization_pct: float
    violation_count: int


class PlanComparison(ModelBase):
    scenario_id: str = Field(min_length=1)
    baseline_plan_id: str = Field(min_length=1)
    candidate_plan_id: str = Field(min_length=1)
    baseline_metrics: PlanComparisonMetrics
    candidate_metrics: PlanComparisonMetrics
    candidate_minus_baseline: PlanComparisonDelta
    conclusion: str = Field(min_length=1)
    recommendation: str = Field(min_length=1)
    requires_human_confirmation: Literal[True] = True
    safety_notice: str = Field(min_length=1)
