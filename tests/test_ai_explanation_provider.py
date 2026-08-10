from __future__ import annotations

import json

import httpx2
import pytest

from backend.app.ai_explanation_provider import (
    OpenAICompatiblePlanExplanationProvider,
)
from backend.app.ai_models import (
    ExplanationEvidence,
    ExplanationFocus,
    QuestionAnswerStatus,
)
from backend.app.ai_provider import (
    AIProviderInvalidOutputError,
    AIProviderSettings,
)


def _settings() -> AIProviderSettings:
    return AIProviderSettings(
        api_key="sk-test-only-placeholder",
        base_url="https://provider.example/v1",
        model="test-chat-model",
    )


def _evidence() -> list[ExplanationEvidence]:
    return [
        ExplanationEvidence(
            evidence_id="FACT-PRIMARY-ASSIGNED",
            kind="plan_metric",
            entity_ids=["PLAN-DEMO"],
            field="metrics.assigned_tasks",
            value="10",
            statement="方案已安排任务数为 10。",
        )
    ]


def _completion(content: str, request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={"choices": [{"message": {"content": content}}]},
        request=request,
    )


def test_explanation_adapter_sends_only_bounded_facts_and_question() -> None:
    captured: dict = {}
    output = json.dumps(
        {
            "question_answer": {
                "status": "answered",
                "statement": "任务已全部安排。",
                "evidence_ids": ["FACT-PRIMARY-ASSIGNED"],
                "matched_entity_ids": [],
            },
            "summary": {
                "statement": "全部任务已安排。",
                "evidence_ids": ["FACT-PRIMARY-ASSIGNED"],
            },
            "tradeoffs": [{
                "statement": "覆盖数量是当前可核对指标。",
                "evidence_ids": ["FACT-PRIMARY-ASSIGNED"],
            }],
            "task_changes": [{
                "statement": "当前只确认已安排数量。",
                "evidence_ids": ["FACT-PRIMARY-ASSIGNED"],
            }],
            "manual_handling": [{
                "statement": "人员需要复核完整任务书。",
                "evidence_ids": ["FACT-PRIMARY-ASSIGNED"],
            }],
            "recommended_next_step": {
                "statement": "请人工复核任务书。",
                "evidence_ids": ["FACT-PRIMARY-ASSIGNED"],
            },
            "unresolved_questions": [],
        },
        ensure_ascii=False,
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["authorization"] = request.headers["authorization"]
        captured["body"] = json.loads(request.content)
        return _completion(output, request)

    provider = OpenAICompatiblePlanExplanationProvider(
        _settings(),
        transport=httpx2.MockTransport(handler),
    )
    result = provider.explain(
        _evidence(),
        focus=ExplanationFocus.SUMMARY,
        question="为什么任务都已安排？",
        question_status=QuestionAnswerStatus.ANSWERED,
        relevant_evidence_ids=["FACT-PRIMARY-ASSIGNED"],
        matched_entity_ids=[],
        focus_evidence_ids=["FACT-PRIMARY-ASSIGNED"],
    )

    assert result.summary.evidence_ids == ["FACT-PRIMARY-ASSIGNED"]
    assert captured["authorization"] == "Bearer sk-test-only-placeholder"
    body = captured["body"]
    assert body["temperature"] == 0
    assert body["stream"] is False
    assert body["response_format"] == {"type": "json_object"}
    user_payload = json.loads(body["messages"][1]["content"])
    assert set(user_payload) == {
        "focus",
        "question",
        "question_grounding",
        "focus_evidence_ids",
        "facts",
    }
    assert user_payload["question_grounding"] == {
        "status": "answered",
        "relevant_evidence_ids": ["FACT-PRIMARY-ASSIGNED"],
        "matched_entity_ids": [],
    }
    assert user_payload["focus_evidence_ids"] == ["FACT-PRIMARY-ASSIGNED"]
    assert user_payload["facts"] == [item.model_dump(mode="json") for item in _evidence()]
    serialized = json.dumps(body, ensure_ascii=False)
    for forbidden in (
        "session_id",
        "revision",
        "sqlite",
        "audit",
        "sk-test-only-placeholder",
    ):
        assert forbidden not in serialized.lower()


def test_explanation_adapter_normalizes_single_claim_section_objects() -> None:
    output = json.dumps(
        {
            "question_answer": {
                "status": "answered",
                "statement": "任务已安排。",
                "evidence_ids": ["FACT-PRIMARY-ASSIGNED"],
                "matched_entity_ids": [],
            },
            "summary": {
                "statement": "方案覆盖任务。",
                "evidence_ids": ["FACT-PRIMARY-ASSIGNED"],
            },
            "tradeoffs": {
                "statement": "覆盖数量是取舍依据。",
                "evidence_ids": ["FACT-PRIMARY-ASSIGNED"],
            },
            "task_changes": {
                "statement": "当前确认任务已安排。",
                "evidence_ids": ["FACT-PRIMARY-ASSIGNED"],
            },
            "manual_handling": {
                "statement": "人员仍需复核任务书。",
                "evidence_ids": ["FACT-PRIMARY-ASSIGNED"],
            },
            "recommended_next_step": {
                "statement": "请人工复核。",
                "evidence_ids": ["FACT-PRIMARY-ASSIGNED"],
            },
            "unresolved_questions": [],
        },
        ensure_ascii=False,
    )

    provider = OpenAICompatiblePlanExplanationProvider(
        _settings(),
        transport=httpx2.MockTransport(
            lambda request: _completion(output, request)
        ),
    )
    result = provider.explain(
        _evidence(),
        focus=ExplanationFocus.SUMMARY,
        question="为什么已安排？",
        question_status=QuestionAnswerStatus.ANSWERED,
        relevant_evidence_ids=["FACT-PRIMARY-ASSIGNED"],
        matched_entity_ids=[],
        focus_evidence_ids=["FACT-PRIMARY-ASSIGNED"],
    )

    assert len(result.tradeoffs) == len(result.task_changes) == len(result.manual_handling) == 1


@pytest.mark.parametrize(
    "content",
    [
        "not-json",
        json.dumps({"summary": {"statement": "缺少引用", "evidence_ids": []}}),
        json.dumps(
            {
                "summary": {
                    "statement": "说明",
                    "evidence_ids": ["FACT-PRIMARY-ASSIGNED"],
                },
                "recommended_next_step": {
                    "statement": "复核",
                    "evidence_ids": ["FACT-PRIMARY-ASSIGNED"],
                },
                "execute_plan": True,
            }
        ),
    ],
)
def test_explanation_adapter_rejects_invalid_or_actionable_output(content: str) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return _completion(content, request)

    provider = OpenAICompatiblePlanExplanationProvider(
        _settings(),
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(AIProviderInvalidOutputError):
        provider.explain(
            _evidence(),
            focus=ExplanationFocus.SUMMARY,
            question=None,
            question_status=QuestionAnswerStatus.NOT_ASKED,
            relevant_evidence_ids=[],
            matched_entity_ids=[],
            focus_evidence_ids=[],
        )
