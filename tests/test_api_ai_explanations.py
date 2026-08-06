from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from backend.app.ai_explanation_provider import ModelPlanExplanation
from backend.app.ai_provider import (
    AIProviderInvalidOutputError,
    AIProviderRequestError,
    AIProviderTimeoutError,
)
from backend.app.demo_export import SAFETY_NOTICE
from backend.app.main import create_app
from backend.app.runtime_repository import SQLiteRuntimeSessionRepository


SCENARIO_ID = "SCN-TERMINAL-DISTURBANCE-01"
SCENARIO_PATH = f"/api/v1/scenarios/{SCENARIO_ID}"


@dataclass
class FakeExplanationProvider:
    result: ModelPlanExplanation | Exception | None = None
    model_label: str = "test-explanation-model"
    calls: int = 0

    def explain(self, evidence, *, focus, question):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        if self.result is not None:
            return self.result
        evidence_id = evidence[0].evidence_id
        return ModelPlanExplanation(
            summary={
                "statement": "模型基于后端事实说明方案状态。",
                "evidence_ids": [evidence_id],
            },
            tradeoffs=[],
            recommended_next_step={
                "statement": "请人工复核任务书后决定后续操作。",
                "evidence_ids": [evidence_id],
            },
        )


def _app(provider=None, session_id="RUN-M54-EXPLANATION"):
    return create_app(
        runtime_repository=SQLiteRuntimeSessionRepository(":memory:"),
        runtime_session_id_factory=lambda: session_id,
        plan_explanation_provider=provider,
    )


def _plans(client: TestClient) -> tuple[dict, dict]:
    fifo = client.post(
        f"{SCENARIO_PATH}/plans",
        json={"expected_version": 1, "algorithm": "fifo"},
    )
    optimized = client.post(
        f"{SCENARIO_PATH}/plans",
        json={"expected_version": 1, "algorithm": "cp_sat"},
    )
    assert fifo.status_code == optimized.status_code == 201
    return fifo.json()["plan"], optimized.json()["plan"]


def _assert_claims_are_fact_bound(payload: dict) -> None:
    fact_ids = {item["evidence_id"] for item in payload["evidence"]}
    claims = [
        payload["summary"],
        *payload["tradeoffs"],
        payload["recommended_next_step"],
    ]
    assert fact_ids
    assert all(claim["evidence_ids"] for claim in claims)
    assert all(set(claim["evidence_ids"]) <= fact_ids for claim in claims)


def test_scenario_explanation_is_read_only_fact_bound_and_available_without_model() -> None:
    app = _app()
    with TestClient(app) as client:
        fifo, optimized = _plans(client)
        audit_before = client.get(f"{SCENARIO_PATH}/audit-records").json()
        response = client.post(
            "/api/v1/assistant/plan-explanations",
            json={
                "context": {
                    "scope": "scenario_plan",
                    "scenario_id": SCENARIO_ID,
                    "scenario_version": 1,
                    "plan_id": optimized["plan_id"],
                    "baseline_plan_id": fifo["plan_id"],
                },
                "focus": "tradeoffs",
                "assistance_mode": "auto",
            },
        )
        audit_after = client.get(f"{SCENARIO_PATH}/audit-records").json()

    assert response.status_code == 200
    payload = response.json()
    assert payload["trace"] == {
        "source": "deterministic_rules",
        "provider_attempted": False,
        "model_label": None,
        "fallback_reason": "model_not_configured",
    }
    assert payload["modifies_plan"] is False
    assert payload["requires_human_confirmation"] is True
    assert payload["safety_notice"] == SAFETY_NOTICE
    assert audit_after == audit_before
    assert any(item["kind"] == "plan_change" for item in payload["evidence"])
    _assert_claims_are_fact_bound(payload)


def test_scenario_explanation_rejects_missing_or_wrong_version_plan() -> None:
    app = _app(session_id="RUN-M54-SCENARIO-MISMATCH")
    with TestClient(app) as client:
        _, optimized = _plans(client)
        missing = client.post(
            "/api/v1/assistant/plan-explanations",
            json={
                "context": {
                    "scope": "scenario_plan",
                    "scenario_id": SCENARIO_ID,
                    "scenario_version": 1,
                    "plan_id": "PLAN-NOT-FOUND",
                },
                "assistance_mode": "deterministic_only",
            },
        )
        applied = client.post(
            f"{SCENARIO_PATH}/events/apply",
            json={"expected_version": 1, "event_ids": ["EVT-SIM102-DELAY"]},
        )
        assert applied.status_code == 200
        version_two = client.post(
            f"{SCENARIO_PATH}/plans",
            json={"expected_version": 2, "algorithm": "cp_sat"},
        ).json()["plan"]
        mismatched = client.post(
            "/api/v1/assistant/plan-explanations",
            json={
                "context": {
                    "scope": "scenario_plan",
                    "scenario_id": SCENARIO_ID,
                    "scenario_version": 1,
                    "plan_id": version_two["plan_id"],
                    "baseline_plan_id": optimized["plan_id"],
                },
                "assistance_mode": "deterministic_only",
            },
        )

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "assistant_context_not_found"
    assert mismatched.status_code == 409
    assert mismatched.json()["error"]["code"] == "assistant_plan_context_mismatch"


def test_runtime_candidate_explanation_rejects_stale_revision_and_changes_no_state() -> None:
    app = _app(session_id="RUN-M54-CANDIDATE")
    with TestClient(app) as client:
        fifo, _ = _plans(client)
        created = client.post(
            "/api/v1/runtime-sessions",
            json={
                "scenario_id": SCENARIO_ID,
                "scenario_version": 1,
                "active_plan_id": fifo["plan_id"],
                "speed": 1,
            },
        ).json()
        awaiting = client.post(
            f"/api/v1/runtime-sessions/{created['session_id']}/start",
            json={"expected_revision": created["revision"]},
        ).json()
        request = {
            "context": {
                "scope": "runtime_plan",
                "session_id": created["session_id"],
                "revision": awaiting["revision"],
                "plan_id": awaiting["candidate_plan_id"],
                "baseline_plan_id": awaiting["active_plan_id"],
            },
            "focus": "task_changes",
            "assistance_mode": "deterministic_only",
        }
        explained = client.post(
            "/api/v1/assistant/plan-explanations",
            json=request,
        )
        current = client.get(
            f"/api/v1/runtime-sessions/{created['session_id']}"
        ).json()
        stale_request = {**request, "context": {**request["context"], "revision": 1}}
        stale = client.post(
            "/api/v1/assistant/plan-explanations",
            json=stale_request,
        )
        wrong = client.post(
            "/api/v1/assistant/plan-explanations",
            json={
                **request,
                "context": {
                    **request["context"],
                    "plan_id": "PLAN-OTHER-RUNTIME",
                },
            },
        )

    assert explained.status_code == 200
    payload = explained.json()
    assert payload["context"] == request["context"]
    assert current["revision"] == awaiting["revision"]
    assert current["status"] == "awaiting_confirmation"
    assert current["candidate_plan_id"] == awaiting["candidate_plan_id"]
    assert any(item["evidence_id"] == "FACT-CHANGE-SUMMARY" for item in payload["evidence"])
    _assert_claims_are_fact_bound(payload)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "assistant_revision_conflict"
    assert wrong.status_code == 409
    assert wrong.json()["error"]["code"] == "assistant_plan_context_mismatch"


def test_runtime_explains_older_active_plan_after_candidate_rejection() -> None:
    app = _app(session_id="RUN-M54-OLDER-ACTIVE")
    with TestClient(app) as client:
        fifo, _ = _plans(client)
        created = client.post(
            "/api/v1/runtime-sessions",
            json={
                "scenario_id": SCENARIO_ID,
                "scenario_version": 1,
                "active_plan_id": fifo["plan_id"],
                "speed": 1,
            },
        ).json()
        awaiting = client.post(
            f"/api/v1/runtime-sessions/{created['session_id']}/start",
            json={"expected_revision": created["revision"]},
        ).json()
        paused = client.post(
            f"/api/v1/runtime-sessions/{created['session_id']}/candidate/reject",
            json={
                "expected_revision": awaiting["revision"],
                "candidate_plan_id": awaiting["candidate_plan_id"],
            },
        ).json()
        response = client.post(
            "/api/v1/assistant/plan-explanations",
            json={
                "context": {
                    "scope": "runtime_plan",
                    "session_id": created["session_id"],
                    "revision": paused["revision"],
                    "plan_id": paused["active_plan_id"],
                },
                "focus": "summary",
                "assistance_mode": "deterministic_only",
            },
        )

    assert paused["current_scenario_version"] == 2
    assert paused["active_plan_detail"]["scenario_version"] == 1
    assert response.status_code == 200
    assert response.json()["context"]["plan_id"] == paused["active_plan_id"]


def test_configured_model_output_is_revalidated_and_failures_fall_back() -> None:
    successful = FakeExplanationProvider()
    app = _app(successful, "RUN-M54-MODEL-SUCCESS")
    with TestClient(app) as client:
        _, optimized = _plans(client)
        request = {
            "context": {
                "scope": "scenario_plan",
                "scenario_id": SCENARIO_ID,
                "scenario_version": 1,
                "plan_id": optimized["plan_id"],
            },
            "question": "为什么这个方案可执行？",
        }
        modeled = client.post("/api/v1/assistant/plan-explanations", json=request)
        deterministic = client.post(
            "/api/v1/assistant/plan-explanations",
            json={**request, "assistance_mode": "deterministic_only"},
        )

    assert modeled.status_code == deterministic.status_code == 200
    assert modeled.json()["trace"]["source"] == "language_model"
    assert modeled.json()["trace"]["model_label"] == "test-explanation-model"
    assert successful.calls == 1
    _assert_claims_are_fact_bound(modeled.json())

    invalid = FakeExplanationProvider(
        ModelPlanExplanation(
            summary={
                "statement": "引用不存在事实。",
                "evidence_ids": ["FACT-NOT-PRESENT"],
            },
            recommended_next_step={
                "statement": "人工复核。",
                "evidence_ids": ["FACT-NOT-PRESENT"],
            },
        )
    )
    invalid_app = _app(invalid, "RUN-M54-MODEL-INVALID")
    with TestClient(invalid_app) as client:
        _, optimized = _plans(client)
        fallback = client.post(
            "/api/v1/assistant/plan-explanations",
            json={
                "context": {
                    "scope": "scenario_plan",
                    "scenario_id": SCENARIO_ID,
                    "scenario_version": 1,
                    "plan_id": optimized["plan_id"],
                }
            },
        )

    assert fallback.status_code == 200
    assert fallback.json()["trace"]["fallback_reason"] == "invalid_model_output"
    assert invalid.calls == 1
    _assert_claims_are_fact_bound(fallback.json())


@pytest.mark.parametrize(
    ("error", "fallback_reason"),
    [
        (AIProviderTimeoutError(), "model_timeout"),
        (AIProviderRequestError(), "provider_error"),
        (AIProviderInvalidOutputError(), "invalid_model_output"),
    ],
)
def test_provider_failures_return_deterministic_explanation(error, fallback_reason) -> None:
    provider = FakeExplanationProvider(error)
    app = _app(provider, f"RUN-M54-{fallback_reason.upper().replace('_', '-')}")
    with TestClient(app) as client:
        _, optimized = _plans(client)
        response = client.post(
            "/api/v1/assistant/plan-explanations",
            json={
                "context": {
                    "scope": "scenario_plan",
                    "scenario_id": SCENARIO_ID,
                    "scenario_version": 1,
                    "plan_id": optimized["plan_id"],
                }
            },
        )

    assert response.status_code == 200
    assert response.json()["trace"]["source"] == "deterministic_rules"
    assert response.json()["trace"]["provider_attempted"] is True
    assert response.json()["trace"]["fallback_reason"] == fallback_reason
    _assert_claims_are_fact_bound(response.json())


def test_explanation_openapi_exposes_read_only_contract() -> None:
    with TestClient(_app(session_id="RUN-M54-OPENAPI")) as client:
        document = client.get("/api/v1/openapi.json").json()

    route = document["paths"]["/api/v1/assistant/plan-explanations"]["post"]
    assert route["requestBody"]["required"] is True
    response = document["components"]["schemas"]["PlanExplanationResponse"]
    assert {
        "context",
        "trace",
        "summary",
        "recommended_next_step",
        "evidence",
        "requires_human_confirmation",
        "modifies_plan",
        "safety_notice",
    } <= set(response["properties"])
