"""Strict revision-bound contracts for spatial situation questions."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .ai_models import AssistanceTrace, RequestedAssistanceMode, ShortMessage
from .demo_export import SAFETY_NOTICE
from .models import ModelBase
from .spatial_models import SpatialFact


class SpatialAnswerStatus(str, Enum):
    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class RuntimeSpatialQuestionContext(ModelBase):
    session_id: str = Field(min_length=1, pattern=r"^RUN-[A-Z0-9-]+$")
    revision: int = Field(ge=1)


class SpatialQuestionSelection(ModelBase):
    task_id: str | None = Field(default=None, pattern=r"^TASK-[A-Z0-9-]+$")
    resource_id: str | None = Field(default=None, pattern=r"^[A-Z0-9-]+$")
    event_id: str | None = Field(default=None, pattern=r"^EVT-[A-Z0-9-]+$")


class SpatialQuestionRequest(ModelBase):
    context: RuntimeSpatialQuestionContext
    question: str = Field(min_length=1, max_length=500)
    selection: SpatialQuestionSelection = Field(default_factory=SpatialQuestionSelection)
    assistance_mode: RequestedAssistanceMode = RequestedAssistanceMode.AUTO

    @field_validator("question")
    @classmethod
    def reject_blank_question(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("spatial question cannot be blank")
        return normalized


class SpatialQuestionBasis(ModelBase):
    session_id: str = Field(min_length=1, pattern=r"^RUN-[A-Z0-9-]+$")
    revision: int = Field(ge=1)
    simulation_time: datetime
    layout_id: str = Field(pattern=r"^LAYOUT-[A-Z0-9-]+$")

    @field_validator("simulation_time")
    @classmethod
    def require_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("spatial answer simulation_time must include a timezone offset")
        return value


class SpatialMapFocus(ModelBase):
    task_ids: list[str] = Field(default_factory=list, max_length=20)
    resource_ids: list[str] = Field(default_factory=list, max_length=20)
    event_ids: list[str] = Field(default_factory=list, max_length=20)
    zone_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("task_ids", "resource_ids", "event_ids", "zone_ids")
    @classmethod
    def require_unique_focus_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("spatial map focus IDs must be unique")
        return values


class SpatialQuestionAnswer(ModelBase):
    status: SpatialAnswerStatus
    statement: str = Field(min_length=1, max_length=1600)
    fact_ids: list[str] = Field(default_factory=list, max_length=20)
    matched_entity_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("fact_ids", "matched_entity_ids")
    @classmethod
    def require_unique_answer_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("spatial answer IDs must be unique")
        return values

    @model_validator(mode="after")
    def validate_status(self) -> SpatialQuestionAnswer:
        if self.status is SpatialAnswerStatus.ANSWERED and not self.fact_ids:
            raise ValueError("an answered spatial question requires cited facts")
        return self


class SpatialQuestionResponse(ModelBase):
    question_id: str = Field(pattern=r"^SPATIAL-Q-[A-F0-9]{16}$")
    basis: SpatialQuestionBasis
    question: str = Field(min_length=1, max_length=500)
    selection: SpatialQuestionSelection
    trace: AssistanceTrace
    answer: SpatialQuestionAnswer
    focus: SpatialMapFocus
    facts: list[SpatialFact] = Field(min_length=1, max_length=100)
    unresolved_questions: list[ShortMessage] = Field(default_factory=list, max_length=20)
    requires_human_confirmation: Literal[True] = True
    modifies_runtime: Literal[False] = False
    safety_notice: Literal[
        "仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。"
    ] = SAFETY_NOTICE

    @model_validator(mode="after")
    def validate_fact_references(self) -> SpatialQuestionResponse:
        fact_ids = [fact.fact_id for fact in self.facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("spatial answer facts must be unique")
        if not set(self.answer.fact_ids) <= set(fact_ids):
            raise ValueError("spatial answer citations must exist in the response")
        if self.answer.status is SpatialAnswerStatus.INSUFFICIENT_EVIDENCE and any(
            (
                self.focus.task_ids,
                self.focus.resource_ids,
                self.focus.event_ids,
                self.focus.zone_ids,
            )
        ):
            raise ValueError("an ungrounded spatial answer cannot focus map objects")
        cited_entities = {
            entity_id
            for fact in self.facts
            if fact.fact_id in self.answer.fact_ids
            for entity_id in fact.entity_ids
        }
        focused_entities = {
            *self.focus.task_ids,
            *self.focus.resource_ids,
            *self.focus.event_ids,
            *self.focus.zone_ids,
        }
        if not focused_entities <= cited_entities:
            raise ValueError("every spatial map focus must be supported by a cited fact")
        return self
