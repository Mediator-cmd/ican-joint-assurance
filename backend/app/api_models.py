"""HTTP response models shared by the versioned API surface."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .models import DataClassification, ModelBase, RunMode, Scenario


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
