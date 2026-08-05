from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from backend.app.ai_models import (
    AssistanceFallbackReason,
    AssistanceSource,
    AssistanceTrace,
    ClarificationQuestion,
    EventDraftBasis,
    EventDraftField,
    EventDraftRequest,
    EventDraftResponse,
    EventDraftStatus,
    EvidenceOrigin,
    ExplanationEvidence,
    ExtractedFieldEvidence,
    ObjectiveRecommendation,
    PlanExplanationRequest,
    PlanExplanationResponse,
    PlanningObjectiveProfile,
)
from backend.app.demo_export import SAFETY_NOTICE
from backend.app.models import FlightEvent


BASE_TIME = datetime(2026, 8, 5, 8, 3, tzinfo=timezone(timedelta(hours=8)))


def _basis() -> EventDraftBasis:
    return EventDraftBasis(
        scenario_id="SCN-TERMINAL-DISTURBANCE-01",
        scenario_version=1,
        reference_time=BASE_TIME,
    )


def _rule_trace() -> AssistanceTrace:
    return AssistanceTrace(
        source="deterministic_rules",
        provider_attempted=False,
        fallback_reason="model_not_configured",
    )


def _delay_event() -> FlightEvent:
    return FlightEvent(
        event_id="EVT-DRAFT-0001",
        event_type="delay",
        flight_id="FL-SIM102",
        occurred_at=BASE_TIME,
        delay_minutes=20,
    )


def _delay_evidence() -> list[ExtractedFieldEvidence]:
    return [
        ExtractedFieldEvidence(
            field="event_type",
            normalized_value="delay",
            origin="user_text",
            source_quote="延误",
        ),
        ExtractedFieldEvidence(
            field="flight_id",
            normalized_value="FL-SIM102",
            origin="user_text",
            source_quote="SIM102",
        ),
        ExtractedFieldEvidence(
            field="occurred_at",
            normalized_value=BASE_TIME.isoformat(),
            origin="authoritative_context",
        ),
        ExtractedFieldEvidence(
            field="delay_minutes",
            normalized_value="20",
            origin="user_text",
            source_quote="20分钟",
        ),
    ]


def _gate_event() -> FlightEvent:
    return FlightEvent(
        event_id="EVT-DRAFT-0002",
        event_type="gate_change",
        flight_id="FL-SIM218",
        occurred_at=BASE_TIME,
        previous_gate_id="GATE-E01",
        new_gate_id="GATE-W03",
    )


def _gate_evidence() -> list[ExtractedFieldEvidence]:
    return [
        ExtractedFieldEvidence(
            field="event_type",
            normalized_value="gate_change",
            origin="user_text",
            source_quote="改到",
        ),
        ExtractedFieldEvidence(
            field="flight_id",
            normalized_value="FL-SIM218",
            origin="user_text",
            source_quote="SIM218",
        ),
        ExtractedFieldEvidence(
            field="occurred_at",
            normalized_value=BASE_TIME.isoformat(),
            origin="authoritative_context",
        ),
        ExtractedFieldEvidence(
            field="previous_gate_id",
            normalized_value="GATE-E01",
            origin="authoritative_context",
        ),
        ExtractedFieldEvidence(
            field="new_gate_id",
            normalized_value="GATE-W03",
            origin="user_text",
            source_quote="W03",
        ),
    ]


def _runtime_context() -> dict:
    return {
        "scope": "runtime_plan",
        "session_id": "RUN-DEMO-001",
        "revision": 4,
        "plan_id": "PLAN-CANDIDATE-001",
        "baseline_plan_id": "PLAN-ACTIVE-001",
    }


def _evidence() -> ExplanationEvidence:
    return ExplanationEvidence(
        evidence_id="FACT-METRIC-ASSIGNED",
        kind="plan_metric",
        entity_ids=["PLAN-CANDIDATE-001"],
        field="assigned_tasks",
        value="10/10",
        statement="候选方案已安排全部 10 项任务。",
    )


def test_event_draft_request_uses_discriminated_authoritative_context() -> None:
    scenario_request = EventDraftRequest.model_validate(
        {
            "context": {
                "scope": "scenario",
                "scenario_id": "SCN-TERMINAL-DISTURBANCE-01",
                "expected_version": 1,
                "reference_time": BASE_TIME,
            },
            "text": "SIM102 延误 20 分钟",
        }
    )
    runtime_request = EventDraftRequest.model_validate(
        {
            "context": {
                "scope": "runtime",
                "session_id": "RUN-DEMO-001",
                "expected_revision": 4,
            },
            "text": "刚刚 SIM102 延误 20 分钟",
            "assistance_mode": "deterministic_only",
        }
    )

    assert scenario_request.context.scope == "scenario"
    assert runtime_request.context.scope == "runtime"

    with pytest.raises(ValidationError):
        EventDraftRequest.model_validate(
            {
                "context": {
                    "scope": "scenario",
                    "scenario_id": "SCN-TERMINAL-DISTURBANCE-01",
                    "expected_version": 1,
                    "reference_time": BASE_TIME.replace(tzinfo=None),
                },
                "text": "SIM102 延误 20 分钟",
            }
        )


def test_assistance_trace_cannot_misrepresent_model_or_fallback() -> None:
    model = AssistanceTrace(
        source="language_model",
        provider_attempted=True,
        model_label="configured-model",
    )
    fallback = AssistanceTrace(
        source="deterministic_rules",
        provider_attempted=True,
        fallback_reason="model_timeout",
    )

    assert model.source is AssistanceSource.LANGUAGE_MODEL
    assert fallback.fallback_reason is AssistanceFallbackReason.MODEL_TIMEOUT

    with pytest.raises(ValidationError):
        AssistanceTrace(source="language_model", provider_attempted=False)
    with pytest.raises(ValidationError):
        AssistanceTrace(
            source="deterministic_rules",
            provider_attempted=False,
            fallback_reason="provider_error",
        )


def test_reviewable_event_requires_matching_evidence_and_never_auto_applies() -> None:
    response = EventDraftResponse(
        draft_id="DRAFT-0123456789ABCDEF",
        basis=_basis(),
        status="ready_for_review",
        trace=_rule_trace(),
        event=_delay_event(),
        evidence=_delay_evidence(),
    )

    assert response.status is EventDraftStatus.READY_FOR_REVIEW
    assert response.requires_human_confirmation is True
    assert response.applies_automatically is False
    assert response.safety_notice == SAFETY_NOTICE

    with pytest.raises(ValidationError):
        EventDraftResponse(
            draft_id="DRAFT-0123456789ABCDEF",
            basis=_basis(),
            status="ready_for_review",
            trace=_rule_trace(),
            event=_delay_event(),
            evidence=_delay_evidence()[:-1],
        )
    conflicting = _delay_evidence()
    conflicting[-1] = conflicting[-1].model_copy(update={"normalized_value": "30"})
    with pytest.raises(ValidationError):
        EventDraftResponse(
            draft_id="DRAFT-0123456789ABCDEF",
            basis=_basis(),
            status="ready_for_review",
            trace=_rule_trace(),
            event=_delay_event(),
            evidence=conflicting,
        )


def test_reviewable_gate_change_requires_both_authoritative_routes() -> None:
    response = EventDraftResponse(
        draft_id="DRAFT-3333333333333333",
        basis=_basis(),
        status="ready_for_review",
        trace=_rule_trace(),
        event=_gate_event(),
        evidence=_gate_evidence(),
    )

    assert response.event is not None
    assert response.event.previous_gate_id == "GATE-E01"
    assert response.event.new_gate_id == "GATE-W03"

    with pytest.raises(ValidationError):
        EventDraftResponse(
            draft_id="DRAFT-3333333333333333",
            basis=_basis(),
            status="ready_for_review",
            trace=_rule_trace(),
            event=_gate_event(),
            evidence=_gate_evidence()[:-1],
        )


def test_clarification_questions_cover_exact_missing_fields() -> None:
    response = EventDraftResponse(
        draft_id="DRAFT-1111111111111111",
        basis=_basis(),
        status="needs_clarification",
        trace=_rule_trace(),
        evidence=_delay_evidence()[:2],
        missing_fields=["delay_minutes", "occurred_at"],
        clarification_questions=[
            ClarificationQuestion(
                field="delay_minutes",
                question="预计延误多少分钟？",
                reason="生成结构化延误事件需要明确分钟数。",
            ),
            ClarificationQuestion(
                field="occurred_at",
                question="该延误在什么时间确认？",
                reason="事件时间必须明确且包含时区。",
            ),
        ],
    )

    assert response.event is None
    assert set(response.missing_fields) == {
        EventDraftField.DELAY_MINUTES,
        EventDraftField.OCCURRED_AT,
    }

    with pytest.raises(ValidationError):
        payload = response.model_dump(mode="python")
        payload["clarification_questions"] = payload["clarification_questions"][:1]
        EventDraftResponse.model_validate(payload)


def test_unsupported_input_returns_safe_status_without_structured_event() -> None:
    response = EventDraftResponse(
        draft_id="DRAFT-2222222222222222",
        basis=_basis(),
        status="unsupported",
        trace=_rule_trace(),
        warnings=["当前仅支持航班延误和登机口变更事件。"],
    )

    assert response.event is None
    assert response.applies_automatically is False

    with pytest.raises(ValidationError):
        EventDraftResponse(
            draft_id="DRAFT-2222222222222222",
            basis=_basis(),
            status="unsupported",
            trace=_rule_trace(),
        )


def test_objective_recommendation_is_advisory_until_human_confirmation() -> None:
    objective = ObjectiveRecommendation(
        profile="critical_first",
        display_name="关键任务优先",
        rationale="输入明确要求优先保障紧急中转任务。",
        source_quote="优先保障紧急中转",
    )

    assert objective.profile is PlanningObjectiveProfile.CRITICAL_FIRST
    assert objective.requires_human_confirmation is True
    assert objective.applied_to_planner is False

    with pytest.raises(ValidationError):
        ObjectiveRecommendation(
            profile="critical_first",
            display_name="关键任务优先",
            rationale="测试",
            applied_to_planner=True,
        )


def test_plan_explanation_context_is_scoped_and_revision_bound() -> None:
    runtime = PlanExplanationRequest(
        context=_runtime_context(),
        focus="task_changes",
        question="为什么调整这两项任务？",
    )
    scenario = PlanExplanationRequest.model_validate(
        {
            "context": {
                "scope": "scenario_plan",
                "scenario_id": "SCN-TERMINAL-DISTURBANCE-01",
                "scenario_version": 2,
                "plan_id": "PLAN-OPTIMIZED-001",
            }
        }
    )

    assert runtime.context.scope == "runtime_plan"
    assert scenario.context.scope == "scenario_plan"

    with pytest.raises(ValidationError):
        PlanExplanationRequest.model_validate(
            {
                "context": {
                    "scope": "runtime_plan",
                    "session_id": "RUN-DEMO-001",
                    "revision": 4,
                    "plan_id": "PLAN-SAME",
                    "baseline_plan_id": "PLAN-SAME",
                }
            }
        )


def test_plan_explanation_must_cite_existing_authoritative_facts() -> None:
    response = PlanExplanationResponse(
        explanation_id="EXPL-0123456789ABCDEF",
        context=_runtime_context(),
        trace=_rule_trace(),
        summary={
            "statement": "候选方案安排了全部任务。",
            "evidence_ids": ["FACT-METRIC-ASSIGNED"],
        },
        tradeoffs=[
            {
                "statement": "当前证据只覆盖任务安排数量。",
                "evidence_ids": ["FACT-METRIC-ASSIGNED"],
            }
        ],
        recommended_next_step={
            "statement": "复核完整任务书后，再决定是否采用候选方案。",
            "evidence_ids": ["FACT-METRIC-ASSIGNED"],
        },
        evidence=[_evidence()],
    )

    assert response.modifies_plan is False
    assert response.requires_human_confirmation is True
    assert response.safety_notice == SAFETY_NOTICE

    with pytest.raises(ValidationError):
        PlanExplanationResponse(
            explanation_id="EXPL-0123456789ABCDEF",
            context=_runtime_context(),
            trace=_rule_trace(),
            summary={
                "statement": "无依据说明",
                "evidence_ids": ["FACT-NOT-PRESENT"],
            },
            recommended_next_step={
                "statement": "人工复核。",
                "evidence_ids": ["FACT-METRIC-ASSIGNED"],
            },
            evidence=[_evidence()],
        )


def test_ai_contracts_reject_unknown_fields_and_safety_overrides() -> None:
    with pytest.raises(ValidationError):
        EventDraftRequest.model_validate(
            {
                "context": {
                    "scope": "scenario",
                    "scenario_id": "SCN-TERMINAL-DISTURBANCE-01",
                    "expected_version": 1,
                    "reference_time": BASE_TIME,
                },
                "text": "SIM102 延误 20 分钟",
                "execute_immediately": True,
            }
        )
    with pytest.raises(ValidationError):
        EventDraftResponse(
            draft_id="DRAFT-0123456789ABCDEF",
            basis=_basis(),
            status="ready_for_review",
            trace=_rule_trace(),
            event=_delay_event(),
            evidence=_delay_evidence(),
            safety_notice="可直接用于真实机场控制。",
        )


def test_ai_contract_schema_exposes_safety_and_human_review_fields() -> None:
    draft_schema = EventDraftResponse.model_json_schema(mode="serialization")
    explanation_schema = PlanExplanationResponse.model_json_schema(mode="serialization")

    assert {
        "status",
        "trace",
        "event",
        "evidence",
        "missing_fields",
        "clarification_questions",
        "requires_human_confirmation",
        "applies_automatically",
        "safety_notice",
    } <= set(draft_schema["properties"])
    assert {
        "context",
        "trace",
        "evidence",
        "summary",
        "tradeoffs",
        "recommended_next_step",
        "requires_human_confirmation",
        "modifies_plan",
        "safety_notice",
    } <= set(explanation_schema["properties"])
