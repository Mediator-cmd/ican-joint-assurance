from __future__ import annotations

import json

import httpx2
import pytest

from backend.app.ai_provider import AIProviderInvalidOutputError, AIProviderSettings
from backend.app.ai_spatial_models import (
    SpatialAnswerStatus,
    SpatialMapFocus,
    SpatialQuestionSelection,
)
from backend.app.ai_spatial_provider import OpenAICompatibleSpatialQuestionProvider
from backend.app.spatial_models import SpatialFact


def _settings() -> AIProviderSettings:
    return AIProviderSettings(
        api_key="sk-test-only-placeholder",
        base_url="https://provider.example/v1",
        model="test-spatial-model",
    )


def _fact() -> SpatialFact:
    return SpatialFact(
        fact_id="SPATIAL-FACT-TASK-004-ACTIVE",
        category="task",
        claim="任务 TASK-004 当前路线从 TRANSFER-DESK 到 GATE-E01，由资源 WC-01 执行。",
        entity_ids=["TASK-004", "PLAN-DEMO", "WC-01", "TRANSFER-DESK", "GATE-E01"],
    )


def _completion(content: str, request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={"choices": [{"message": {"content": content}}]},
        request=request,
    )


def test_spatial_adapter_sends_only_bounded_facts_and_allowed_focus() -> None:
    captured: dict = {}
    output = json.dumps(
        {
            "status": "answered",
            "statement": "TASK-004 从中转服务台前往东侧匿名登机口，由 WC-01 执行。",
            "fact_ids": ["SPATIAL-FACT-TASK-004-ACTIVE"],
            "focus": {
                "task_ids": ["TASK-004"],
                "resource_ids": ["WC-01"],
                "event_ids": [],
                "zone_ids": ["TRANSFER-DESK", "GATE-E01"],
            },
            "unresolved_questions": [],
        },
        ensure_ascii=False,
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["authorization"] = request.headers["authorization"]
        captured["body"] = json.loads(request.content)
        return _completion(output, request)

    provider = OpenAICompatibleSpatialQuestionProvider(
        _settings(),
        transport=httpx2.MockTransport(handler),
    )
    allowed = SpatialMapFocus(
        task_ids=["TASK-004"],
        resource_ids=["WC-01"],
        zone_ids=["TRANSFER-DESK", "GATE-E01"],
    )
    result = provider.answer(
        [_fact()],
        question="task4路线是什么？",
        selection=SpatialQuestionSelection(task_id="TASK-004"),
        status=SpatialAnswerStatus.ANSWERED,
        relevant_fact_ids=["SPATIAL-FACT-TASK-004-ACTIVE"],
        matched_entity_ids=["TASK-004"],
        allowed_focus=allowed,
    )

    assert result.focus.task_ids == ["TASK-004"]
    assert captured["authorization"] == "Bearer sk-test-only-placeholder"
    body = captured["body"]
    assert body["temperature"] == 0
    assert body["stream"] is False
    assert body["response_format"] == {"type": "json_object"}
    payload = json.loads(body["messages"][1]["content"])
    assert set(payload) == {
        "question",
        "selection",
        "grounding",
        "allowed_focus",
        "facts",
    }
    assert payload["grounding"] == {
        "status": "answered",
        "relevant_fact_ids": ["SPATIAL-FACT-TASK-004-ACTIVE"],
        "matched_entity_ids": ["TASK-004"],
    }
    serialized_payload = json.dumps(payload, ensure_ascii=False).lower()
    for forbidden in (
        "session_id",
        "revision",
        "simulation_time",
        "sqlite",
        "audit",
        "progress_pct",
        "normalized",
        "sk-test-only-placeholder",
    ):
        assert forbidden not in serialized_payload


@pytest.mark.parametrize(
    "content",
    [
        "not-json",
        json.dumps({"status": "answered", "statement": "缺少引用"}),
        json.dumps(
            {
                "status": "answered",
                "statement": "越权执行。",
                "fact_ids": ["SPATIAL-FACT-TASK-004-ACTIVE"],
                "focus": {},
                "execute_candidate": True,
            }
        ),
    ],
)
def test_spatial_adapter_rejects_invalid_or_actionable_output(content: str) -> None:
    provider = OpenAICompatibleSpatialQuestionProvider(
        _settings(),
        transport=httpx2.MockTransport(
            lambda request: _completion(content, request)
        ),
    )

    with pytest.raises(AIProviderInvalidOutputError):
        provider.answer(
            [_fact()],
            question="task4路线是什么？",
            selection=SpatialQuestionSelection(),
            status=SpatialAnswerStatus.ANSWERED,
            relevant_fact_ids=["SPATIAL-FACT-TASK-004-ACTIVE"],
            matched_entity_ids=["TASK-004"],
            allowed_focus=SpatialMapFocus(task_ids=["TASK-004"]),
        )
