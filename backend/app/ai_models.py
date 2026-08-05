"""Strict public contracts for M5 advisory AI assistance."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from .demo_export import SAFETY_NOTICE
from .models import FlightEvent, FlightEventType, ModelBase


ShortMessage = Annotated[str, Field(min_length=1, max_length=320)]


class RequestedAssistanceMode(str, Enum):
    AUTO = "auto"
    DETERMINISTIC_ONLY = "deterministic_only"


class AssistanceSource(str, Enum):
    LANGUAGE_MODEL = "language_model"
    DETERMINISTIC_RULES = "deterministic_rules"


class AssistanceFallbackReason(str, Enum):
    MODEL_NOT_CONFIGURED = "model_not_configured"
    MODEL_TIMEOUT = "model_timeout"
    PROVIDER_ERROR = "provider_error"
    INVALID_MODEL_OUTPUT = "invalid_model_output"


class AssistanceTrace(ModelBase):
    source: AssistanceSource
    provider_attempted: bool = False
    model_label: str | None = Field(default=None, min_length=1, max_length=80)
    fallback_reason: AssistanceFallbackReason | None = None

    @model_validator(mode="after")
    def validate_source(self) -> AssistanceTrace:
        if self.source is AssistanceSource.LANGUAGE_MODEL:
            if not self.provider_attempted or not self.model_label or self.fallback_reason:
                raise ValueError("language-model results require an attempted labeled model")
            return self
        if self.model_label is not None:
            raise ValueError("deterministic results cannot claim a model label")
        if self.provider_attempted and self.fallback_reason is None:
            raise ValueError("a failed provider attempt requires a fallback reason")
        if (
            self.fallback_reason is AssistanceFallbackReason.MODEL_NOT_CONFIGURED
            and self.provider_attempted
        ):
            raise ValueError("an unconfigured model cannot have been attempted")
        if (
            self.fallback_reason
            in {
                AssistanceFallbackReason.MODEL_TIMEOUT,
                AssistanceFallbackReason.PROVIDER_ERROR,
                AssistanceFallbackReason.INVALID_MODEL_OUTPUT,
            }
            and not self.provider_attempted
        ):
            raise ValueError("provider failures require an attempted provider")
        return self


class ScenarioEventDraftContext(ModelBase):
    scope: Literal["scenario"] = "scenario"
    scenario_id: str = Field(min_length=1)
    expected_version: int = Field(ge=1)
    reference_time: datetime

    @field_validator("reference_time")
    @classmethod
    def validate_reference_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reference_time must include a timezone offset")
        return value


class RuntimeEventDraftContext(ModelBase):
    scope: Literal["runtime"] = "runtime"
    session_id: str = Field(min_length=1, pattern=r"^RUN-[A-Z0-9-]+$")
    expected_revision: int = Field(ge=1)


EventDraftRequestContext = Annotated[
    ScenarioEventDraftContext | RuntimeEventDraftContext,
    Field(discriminator="scope"),
]


class EventDraftRequest(ModelBase):
    context: EventDraftRequestContext
    text: str = Field(min_length=1, max_length=1000)
    assistance_mode: RequestedAssistanceMode = RequestedAssistanceMode.AUTO


class EventDraftBasis(ModelBase):
    scenario_id: str = Field(min_length=1)
    scenario_version: int = Field(ge=1)
    reference_time: datetime
    runtime_session_id: str | None = Field(
        default=None,
        pattern=r"^RUN-[A-Z0-9-]+$",
    )
    runtime_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_runtime_basis(self) -> EventDraftBasis:
        if self.reference_time.tzinfo is None or self.reference_time.utcoffset() is None:
            raise ValueError("reference_time must include a timezone offset")
        if (self.runtime_session_id is None) != (self.runtime_revision is None):
            raise ValueError("runtime session ID and revision must appear together")
        return self


class EventDraftStatus(str, Enum):
    READY_FOR_REVIEW = "ready_for_review"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNSUPPORTED = "unsupported"


class EventDraftField(str, Enum):
    EVENT_TYPE = "event_type"
    FLIGHT_ID = "flight_id"
    OCCURRED_AT = "occurred_at"
    DELAY_MINUTES = "delay_minutes"
    PREVIOUS_GATE_ID = "previous_gate_id"
    NEW_GATE_ID = "new_gate_id"


class EvidenceOrigin(str, Enum):
    USER_TEXT = "user_text"
    AUTHORITATIVE_CONTEXT = "authoritative_context"
    DETERMINISTIC_DERIVATION = "deterministic_derivation"


class ExtractedFieldEvidence(ModelBase):
    field: EventDraftField
    normalized_value: str = Field(min_length=1, max_length=160)
    origin: EvidenceOrigin
    source_quote: str | None = Field(default=None, min_length=1, max_length=240)

    @model_validator(mode="after")
    def validate_source_quote(self) -> ExtractedFieldEvidence:
        if self.origin is EvidenceOrigin.USER_TEXT and self.source_quote is None:
            raise ValueError("user-text evidence requires a source quote")
        return self


class ClarificationOption(ModelBase):
    value: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=120)


class ClarificationQuestion(ModelBase):
    field: EventDraftField
    question: str = Field(min_length=1, max_length=240)
    reason: str = Field(min_length=1, max_length=240)
    options: list[ClarificationOption] = Field(default_factory=list, max_length=20)

    @field_validator("options")
    @classmethod
    def validate_unique_options(
        cls,
        value: list[ClarificationOption],
    ) -> list[ClarificationOption]:
        values = [item.value for item in value]
        if len(values) != len(set(values)):
            raise ValueError("clarification option values must be unique")
        return value


class PlanningObjectiveProfile(str, Enum):
    BALANCED = "balanced"
    CRITICAL_FIRST = "critical_first"
    MINIMUM_WAIT = "minimum_wait"
    MINIMUM_CHANGE = "minimum_change"


class ObjectiveRecommendation(ModelBase):
    profile: PlanningObjectiveProfile
    display_name: str = Field(min_length=1, max_length=80)
    rationale: str = Field(min_length=1, max_length=320)
    source_quote: str | None = Field(default=None, min_length=1, max_length=240)
    requires_human_confirmation: Literal[True] = True
    applied_to_planner: Literal[False] = False


class EventDraftResponse(ModelBase):
    draft_id: str = Field(pattern=r"^DRAFT-[A-F0-9]{16}$")
    basis: EventDraftBasis
    status: EventDraftStatus
    trace: AssistanceTrace
    event: FlightEvent | None = None
    evidence: list[ExtractedFieldEvidence] = Field(default_factory=list)
    missing_fields: list[EventDraftField] = Field(default_factory=list)
    clarification_questions: list[ClarificationQuestion] = Field(default_factory=list)
    objective_recommendation: ObjectiveRecommendation | None = None
    warnings: list[ShortMessage] = Field(default_factory=list, max_length=20)
    requires_human_confirmation: Literal[True] = True
    applies_automatically: Literal[False] = False
    safety_notice: Literal[
        "仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。"
    ] = SAFETY_NOTICE

    @field_validator("evidence")
    @classmethod
    def validate_unique_evidence(
        cls,
        value: list[ExtractedFieldEvidence],
    ) -> list[ExtractedFieldEvidence]:
        fields = [item.field for item in value]
        if len(fields) != len(set(fields)):
            raise ValueError("event draft evidence fields must be unique")
        return value

    @field_validator("missing_fields")
    @classmethod
    def validate_unique_missing_fields(
        cls,
        value: list[EventDraftField],
    ) -> list[EventDraftField]:
        if len(value) != len(set(value)):
            raise ValueError("missing event fields must be unique")
        return value

    @model_validator(mode="after")
    def validate_status_payload(self) -> EventDraftResponse:
        question_fields = [item.field for item in self.clarification_questions]
        if len(question_fields) != len(set(question_fields)):
            raise ValueError("clarification question fields must be unique")

        if self.status is EventDraftStatus.READY_FOR_REVIEW:
            if self.event is None:
                raise ValueError("a reviewable draft requires a validated event")
            if self.missing_fields or self.clarification_questions:
                raise ValueError("a reviewable draft cannot contain missing fields")
            self._validate_event_evidence()
        elif self.status is EventDraftStatus.NEEDS_CLARIFICATION:
            if self.event is not None or not self.missing_fields:
                raise ValueError("clarification status requires missing fields and no event")
            if set(question_fields) != set(self.missing_fields):
                raise ValueError("clarification questions must cover every missing field")
        else:
            if self.event is not None or self.missing_fields or self.clarification_questions:
                raise ValueError("unsupported input cannot expose an event or clarification state")
            if not self.warnings:
                raise ValueError("unsupported input requires a safe explanation")
        return self

    def _validate_event_evidence(self) -> None:
        if self.event is None:
            return
        expected = {
            EventDraftField.EVENT_TYPE: self.event.event_type.value,
            EventDraftField.FLIGHT_ID: self.event.flight_id,
            EventDraftField.OCCURRED_AT: self.event.occurred_at.isoformat(),
        }
        if self.event.event_type is FlightEventType.DELAY:
            expected[EventDraftField.DELAY_MINUTES] = str(self.event.delay_minutes)
        else:
            expected[EventDraftField.PREVIOUS_GATE_ID] = str(self.event.previous_gate_id)
            expected[EventDraftField.NEW_GATE_ID] = str(self.event.new_gate_id)
        evidence_by_field = {item.field: item for item in self.evidence}
        missing_evidence = set(expected) - set(evidence_by_field)
        if missing_evidence:
            raise ValueError("a reviewable event requires evidence for every required field")
        for field, expected_value in expected.items():
            if evidence_by_field[field].normalized_value != expected_value:
                raise ValueError("event evidence must match the normalized event")


class ExplanationFocus(str, Enum):
    SUMMARY = "summary"
    TRADEOFFS = "tradeoffs"
    TASK_CHANGES = "task_changes"
    MANUAL_HANDLING = "manual_handling"


class ScenarioPlanExplanationContext(ModelBase):
    scope: Literal["scenario_plan"] = "scenario_plan"
    scenario_id: str = Field(min_length=1)
    scenario_version: int = Field(ge=1)
    plan_id: str = Field(min_length=1, pattern=r"^PLAN-[A-Z0-9-]+$")
    baseline_plan_id: str | None = Field(
        default=None,
        pattern=r"^PLAN-[A-Z0-9-]+$",
    )

    @model_validator(mode="after")
    def validate_plan_ids(self) -> ScenarioPlanExplanationContext:
        if self.baseline_plan_id == self.plan_id:
            raise ValueError("baseline and explained plans must be different")
        return self


class RuntimePlanExplanationContext(ModelBase):
    scope: Literal["runtime_plan"] = "runtime_plan"
    session_id: str = Field(min_length=1, pattern=r"^RUN-[A-Z0-9-]+$")
    revision: int = Field(ge=1)
    plan_id: str = Field(min_length=1, pattern=r"^PLAN-[A-Z0-9-]+$")
    baseline_plan_id: str | None = Field(
        default=None,
        pattern=r"^PLAN-[A-Z0-9-]+$",
    )

    @model_validator(mode="after")
    def validate_plan_ids(self) -> RuntimePlanExplanationContext:
        if self.baseline_plan_id == self.plan_id:
            raise ValueError("baseline and explained plans must be different")
        return self


PlanExplanationContext = Annotated[
    ScenarioPlanExplanationContext | RuntimePlanExplanationContext,
    Field(discriminator="scope"),
]


class PlanExplanationRequest(ModelBase):
    context: PlanExplanationContext
    focus: ExplanationFocus = ExplanationFocus.SUMMARY
    question: str | None = Field(default=None, min_length=1, max_length=500)
    assistance_mode: RequestedAssistanceMode = RequestedAssistanceMode.AUTO


class ExplanationFactKind(str, Enum):
    PLAN_METRIC = "plan_metric"
    ASSIGNMENT = "assignment"
    UNASSIGNED_TASK = "unassigned_task"
    CONSTRAINT = "constraint"
    EVENT = "event"
    PLAN_CHANGE = "plan_change"
    OBJECTIVE = "objective"


class ExplanationEvidence(ModelBase):
    evidence_id: str = Field(pattern=r"^FACT-[A-Z0-9-]+$")
    kind: ExplanationFactKind
    entity_ids: list[str] = Field(min_length=1, max_length=20)
    field: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=240)
    statement: str = Field(min_length=1, max_length=320)

    @field_validator("entity_ids")
    @classmethod
    def validate_unique_entity_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("explanation evidence entity IDs must be unique")
        return value


class ExplanationClaim(ModelBase):
    statement: str = Field(min_length=1, max_length=1200)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)

    @field_validator("evidence_ids")
    @classmethod
    def validate_unique_evidence_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("claim evidence IDs must be unique")
        return value


class PlanExplanationResponse(ModelBase):
    explanation_id: str = Field(pattern=r"^EXPL-[A-F0-9]{16}$")
    context: PlanExplanationContext
    trace: AssistanceTrace
    summary: ExplanationClaim
    tradeoffs: list[ExplanationClaim] = Field(default_factory=list, max_length=20)
    recommended_next_step: ExplanationClaim
    evidence: list[ExplanationEvidence] = Field(min_length=1, max_length=100)
    unresolved_questions: list[ShortMessage] = Field(default_factory=list, max_length=20)
    requires_human_confirmation: Literal[True] = True
    modifies_plan: Literal[False] = False
    safety_notice: Literal[
        "仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。"
    ] = SAFETY_NOTICE

    @model_validator(mode="after")
    def validate_evidence_references(self) -> PlanExplanationResponse:
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("explanation evidence IDs must be unique")
        claims = [self.summary, *self.tradeoffs, self.recommended_next_step]
        cited_evidence_ids = {
            evidence_id for claim in claims for evidence_id in claim.evidence_ids
        }
        if not cited_evidence_ids <= set(evidence_ids):
            raise ValueError("every claim evidence ID must exist in the evidence list")
        return self
