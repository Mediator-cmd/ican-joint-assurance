from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from backend.app.ai_explanation_provider import ModelPlanExplanation
from backend.app.ai_explanation_services import (
    ground_explanation_question,
    select_model_explanation_evidence,
)
from backend.app.ai_models import ExplanationEvidence
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

    def explain(
        self,
        evidence,
        *,
        focus,
        question,
        question_status,
        relevant_evidence_ids,
        matched_entity_ids,
        focus_evidence_ids,
    ):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        if self.result is not None:
            return self.result
        evidence_id = (
            relevant_evidence_ids[0]
            if relevant_evidence_ids
            else evidence[0].evidence_id
        )
        return ModelPlanExplanation(
            question_answer={
                "status": question_status,
                "statement": (
                    "模型直接回答了当前问题。"
                    if question is not None
                    else "未提出补充问题。"
                ),
                "evidence_ids": [evidence_id] if question is not None else [],
                "matched_entity_ids": matched_entity_ids,
            },
            summary={
                "statement": "模型基于后端事实说明方案状态。",
                "evidence_ids": [evidence_id],
            },
            tradeoffs=[{
                "statement": "模型说明目标和指标取舍。",
                "evidence_ids": [evidence_id],
            }],
            task_changes=[{
                "statement": "模型说明任务安排或基线变化。",
                "evidence_ids": [evidence_id],
            }],
            manual_handling=[{
                "statement": "模型说明人员需要复核的事项。",
                "evidence_ids": [evidence_id],
            }],
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
        *payload["task_changes"],
        *payload["manual_handling"],
        payload["recommended_next_step"],
    ]
    assert fact_ids
    assert all(claim["evidence_ids"] for claim in claims)
    assert all(set(claim["evidence_ids"]) <= fact_ids for claim in claims)
    assert set(payload["question_answer"]["evidence_ids"]) <= fact_ids


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
            question_answer={
                "status": "not_asked",
                "statement": "未提出问题。",
                "evidence_ids": [],
                "matched_entity_ids": [],
            },
            summary={
                "statement": "引用不存在事实。",
                "evidence_ids": ["FACT-NOT-PRESENT"],
            },
            tradeoffs=[{
                "statement": "错误取舍引用。",
                "evidence_ids": ["FACT-NOT-PRESENT"],
            }],
            task_changes=[{
                "statement": "错误任务引用。",
                "evidence_ids": ["FACT-NOT-PRESENT"],
            }],
            manual_handling=[{
                "statement": "错误人工引用。",
                "evidence_ids": ["FACT-NOT-PRESENT"],
            }],
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


def test_different_questions_receive_grounded_answers_and_distinct_focus_content() -> None:
    app = _app(session_id="RUN-M65-QUESTION-DIFFERENCE")
    with TestClient(app) as client:
        _, optimized = _plans(client)
        task_id = optimized["assignments"][-1]["task_id"]
        base_context = {
            "scope": "scenario_plan",
            "scenario_id": SCENARIO_ID,
            "scenario_version": 1,
            "plan_id": optimized["plan_id"],
        }
        utilization = client.post(
            "/api/v1/assistant/plan-explanations",
            json={
                "context": base_context,
                "focus": "tradeoffs",
                "question": "当前方案总体资源利用率是多少，意味着什么？",
                "assistance_mode": "deterministic_only",
            },
        )
        assignment = client.post(
            "/api/v1/assistant/plan-explanations",
            json={
                "context": base_context,
                "focus": "task_changes",
                "question": f"任务 {task_id} 由哪个资源执行，服务时间和路线是什么？",
                "assistance_mode": "deterministic_only",
            },
        )

    assert utilization.status_code == assignment.status_code == 200
    utilization_payload = utilization.json()
    assignment_payload = assignment.json()
    assert utilization_payload["question_answer"]["status"] == "answered"
    assert "FACT-PRIMARY-UTILIZATION" in utilization_payload["question_answer"]["evidence_ids"]
    assert any(
        "FACT-PRIMARY-UTILIZATION" in claim["evidence_ids"]
        for claim in utilization_payload["tradeoffs"]
    )
    assignment_fact_id = f"FACT-PRIMARY-ASSIGNMENT-{task_id}"
    assert task_id in assignment_payload["question_answer"]["matched_entity_ids"]
    assert assignment_fact_id in assignment_payload["question_answer"]["evidence_ids"]
    assert any(
        assignment_fact_id in claim["evidence_ids"]
        for claim in assignment_payload["task_changes"]
    )
    assert (
        utilization_payload["question_answer"]["statement"]
        != assignment_payload["question_answer"]["statement"]
    )
    for payload in (utilization_payload, assignment_payload):
        assert payload["tradeoffs"]
        assert payload["task_changes"]
        assert payload["manual_handling"]
        _assert_claims_are_fact_bound(payload)


@pytest.mark.parametrize(
    "question",
    [
        "task4的具体细节是什么",
        "task-4的具体细节是什么",
        "task 004的具体细节是什么",
        "TASK004的具体细节是什么",
        "任务4的具体细节是什么",
        "ＴＡＳＫ４的具体细节是什么",
    ],
)
def test_task_shorthand_aliases_match_one_authoritative_task(question: str) -> None:
    evidence = [
        ExplanationEvidence(
            evidence_id="FACT-PRIMARY-ASSIGNMENT-TASK-004",
            kind="assignment",
            entity_ids=["PLAN-DEMO", "TASK-004", "WC-01", "FL-SIM218"],
            field="assignment",
            value="A-004",
            statement="TASK-004 由 WC-01 执行。",
        ),
        ExplanationEvidence(
            evidence_id="FACT-PRIMARY-ASSIGNMENT-TASK-040",
            kind="assignment",
            entity_ids=["PLAN-DEMO", "TASK-040", "WC-02", "FL-SIM330"],
            field="assignment",
            value="A-040",
            statement="TASK-040 由 WC-02 执行。",
        ),
    ]

    grounding = ground_explanation_question(question, evidence)

    assert grounding.status.value == "answered"
    assert grounding.matched_entity_ids == ("TASK-004",)
    assert grounding.relevant_evidence_ids == (
        "FACT-PRIMARY-ASSIGNMENT-TASK-004",
    )


@pytest.mark.parametrize(
    ("question", "expected_entity_id"),
    [
        ("wc1现在负责什么任务？", "WC-01"),
        ("sim218有哪些保障任务？", "FL-SIM218"),
    ],
)
def test_resource_and_flight_shorthand_aliases_remain_fact_bound(
    question: str,
    expected_entity_id: str,
) -> None:
    evidence = [
        ExplanationEvidence(
            evidence_id="FACT-PRIMARY-ASSIGNMENT-TASK-004",
            kind="assignment",
            entity_ids=["PLAN-DEMO", "TASK-004", "WC-01", "FL-SIM218"],
            field="assignment",
            value="A-004",
            statement="TASK-004 由 WC-01 执行。",
        )
    ]

    grounding = ground_explanation_question(question, evidence)

    assert grounding.status.value == "answered"
    assert grounding.matched_entity_ids == (expected_entity_id,)
    assert grounding.relevant_evidence_ids == (
        "FACT-PRIMARY-ASSIGNMENT-TASK-004",
    )


def test_exact_entity_id_takes_precedence_over_shorter_numeric_alias() -> None:
    evidence = [
        ExplanationEvidence(
            evidence_id="FACT-PADDED",
            kind="assignment",
            entity_ids=["TASK-004"],
            field="assignment",
            value="padded",
            statement="TASK-004 使用补零规范 ID。",
        ),
        ExplanationEvidence(
            evidence_id="FACT-UNPADDED",
            kind="assignment",
            entity_ids=["TASK-4"],
            field="assignment",
            value="unpadded",
            statement="TASK-4 使用未补零 ID。",
        ),
    ]

    grounding = ground_explanation_question("TASK-004的细节", evidence)

    assert grounding.status.value == "answered"
    assert grounding.matched_entity_ids == ("TASK-004",)
    assert grounding.relevant_evidence_ids == ("FACT-PADDED",)


def test_ambiguous_numeric_shorthand_is_not_guessed() -> None:
    evidence = [
        ExplanationEvidence(
            evidence_id="FACT-WC-01",
            kind="assignment",
            entity_ids=["WC-01"],
            field="assignment",
            value="wc-01",
            statement="WC-01 执行任务。",
        ),
        ExplanationEvidence(
            evidence_id="FACT-WC-001",
            kind="assignment",
            entity_ids=["WC-001"],
            field="assignment",
            value="wc-001",
            statement="WC-001 执行任务。",
        ),
    ]

    grounding = ground_explanation_question("wc1现在负责什么？", evidence)

    assert grounding.status.value == "insufficient_evidence"
    assert grounding.matched_entity_ids == ()
    assert grounding.relevant_evidence_ids == ()


def test_task_shorthand_question_returns_assignment_details_end_to_end() -> None:
    with TestClient(_app(session_id="RUN-M65-TASK-SHORTHAND")) as client:
        _, optimized = _plans(client)
        assert any(
            item["task_id"] == "TASK-004"
            for item in optimized["assignments"]
        )
        response = client.post(
            "/api/v1/assistant/plan-explanations",
            json={
                "context": {
                    "scope": "scenario_plan",
                    "scenario_id": SCENARIO_ID,
                    "scenario_version": 1,
                    "plan_id": optimized["plan_id"],
                },
                "focus": "task_changes",
                "question": "task4的具体细节是什么",
                "assistance_mode": "deterministic_only",
            },
        )

    assert response.status_code == 200
    answer = response.json()["question_answer"]
    assert answer["status"] == "answered"
    assert answer["matched_entity_ids"] == ["TASK-004"]
    assert answer["evidence_ids"] == [
        "FACT-PRIMARY-ASSIGNMENT-TASK-004"
    ]
    assert "TASK-004" in answer["statement"]
    assert "WC-01" in answer["statement"]


def test_manual_question_explains_actual_tasks_requiring_coordination() -> None:
    with TestClient(_app(session_id="RUN-M65-MANUAL-ANSWER")) as client:
        _, optimized = _plans(client)
        response = client.post(
            "/api/v1/assistant/plan-explanations",
            json={
                "context": {
                    "scope": "scenario_plan",
                    "scenario_id": SCENARIO_ID,
                    "scenario_version": 1,
                    "plan_id": optimized["plan_id"],
                },
                "focus": "manual_handling",
                "question": "哪些任务需要人工协调，为什么？",
                "assistance_mode": "deterministic_only",
            },
        )

    assert response.status_code == 200
    answer = response.json()["question_answer"]
    assert answer["status"] == "answered"
    assert "需要人工协调的未安排任务" in answer["statement"]
    assert any(
        evidence_id.startswith("FACT-PRIMARY-UNASSIGNED-")
        for evidence_id in answer["evidence_ids"]
    )


def test_spatial_question_without_spatial_facts_does_not_call_model_or_guess() -> None:
    provider = FakeExplanationProvider()
    with TestClient(_app(provider, "RUN-M65-SPATIAL-NOT-GROUNDED")) as client:
        _, optimized = _plans(client)
        task_id = optimized["assignments"][0]["task_id"]
        response = client.post(
            "/api/v1/assistant/plan-explanations",
            json={
                "context": {
                    "scope": "scenario_plan",
                    "scenario_id": SCENARIO_ID,
                    "scenario_version": 1,
                    "plan_id": optimized["plan_id"],
                },
                "question": f"任务 {task_id} 在机场平面图上的实时位置和坐标是什么？",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert provider.calls == 0
    assert payload["trace"]["fallback_reason"] == "question_not_grounded"
    assert payload["question_answer"]["status"] == "insufficient_evidence"
    assert task_id in payload["question_answer"]["matched_entity_ids"]
    assert "尚未提供平面图坐标" in payload["question_answer"]["statement"]


def test_model_with_legal_but_question_irrelevant_fact_references_falls_back() -> None:
    irrelevant = ModelPlanExplanation(
        question_answer={
            "status": "answered",
            "statement": "任务都已安排。",
            "evidence_ids": ["FACT-PRIMARY-ASSIGNED"],
            "matched_entity_ids": [],
        },
        summary={
            "statement": "模型只重复安排数量。",
            "evidence_ids": ["FACT-PRIMARY-ASSIGNED"],
        },
        tradeoffs=[{
            "statement": "模型没有解释利用率取舍。",
            "evidence_ids": ["FACT-PRIMARY-ASSIGNED"],
        }],
        task_changes=[{
            "statement": "模型没有给出任务变化。",
            "evidence_ids": ["FACT-PRIMARY-ASSIGNED"],
        }],
        manual_handling=[{
            "statement": "模型只要求人工复核。",
            "evidence_ids": ["FACT-PRIMARY-ASSIGNED"],
        }],
        recommended_next_step={
            "statement": "请人工复核。",
            "evidence_ids": ["FACT-PRIMARY-ASSIGNED"],
        },
    )
    provider = FakeExplanationProvider(irrelevant)
    with TestClient(_app(provider, "RUN-M65-IRRELEVANT-MODEL")) as client:
        _, optimized = _plans(client)
        response = client.post(
            "/api/v1/assistant/plan-explanations",
            json={
                "context": {
                    "scope": "scenario_plan",
                    "scenario_id": SCENARIO_ID,
                    "scenario_version": 1,
                    "plan_id": optimized["plan_id"],
                },
                "focus": "summary",
                "question": "当前总体资源利用率是多少？",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert provider.calls == 1
    assert payload["trace"]["source"] == "deterministic_rules"
    assert payload["trace"]["fallback_reason"] == "invalid_model_output"
    assert "FACT-PRIMARY-UTILIZATION" in payload["question_answer"]["evidence_ids"]


def test_task_question_hides_unrelated_entities_from_model_context() -> None:
    evidence = [
        ExplanationEvidence(
            evidence_id="FACT-PRIMARY-STATUS",
            kind="constraint",
            entity_ids=["PLAN-DEMO"],
            field="status",
            value="executable",
            statement="方案可执行。",
        ),
        ExplanationEvidence(
            evidence_id="FACT-PRIMARY-ASSIGNMENT-TASK-010",
            kind="assignment",
            entity_ids=["PLAN-DEMO", "TASK-010", "WC-01", "FL-SIM330"],
            field="assignment",
            value="A-010",
            statement="TASK-010 由 WC-01 执行。",
        ),
        ExplanationEvidence(
            evidence_id="FACT-PRIMARY-ASSIGNMENT-TASK-001",
            kind="assignment",
            entity_ids=["PLAN-DEMO", "TASK-001", "WC-01", "FL-SIM102"],
            field="assignment",
            value="A-001",
            statement="TASK-001 由 WC-01 执行。",
        ),
        ExplanationEvidence(
            evidence_id="FACT-EVENT-EVT-SIM330-DELAY",
            kind="event",
            entity_ids=["EVT-SIM330-DELAY", "FL-SIM330"],
            field="event",
            value="delay",
            statement="SIM330 延误。",
        ),
        ExplanationEvidence(
            evidence_id="FACT-EVENT-EVT-SIM218-DELAY",
            kind="event",
            entity_ids=["EVT-SIM218-DELAY", "FL-SIM218"],
            field="event",
            value="delay",
            statement="SIM218 延误。",
        ),
    ]
    grounding = ground_explanation_question(
        "任务 TASK-010 由哪个资源执行，服务时间和路线是什么？",
        evidence,
    )
    selected = select_model_explanation_evidence(evidence, grounding)
    selected_ids = {item.evidence_id for item in selected}

    assert "FACT-PRIMARY-STATUS" in selected_ids
    assert "FACT-PRIMARY-ASSIGNMENT-TASK-010" in selected_ids
    assert "FACT-EVENT-EVT-SIM330-DELAY" in selected_ids
    assert "FACT-PRIMARY-ASSIGNMENT-TASK-001" not in selected_ids
    assert "FACT-EVENT-EVT-SIM218-DELAY" not in selected_ids


def test_explanation_openapi_exposes_read_only_contract() -> None:
    with TestClient(_app(session_id="RUN-M54-OPENAPI")) as client:
        document = client.get("/api/v1/openapi.json").json()

    route = document["paths"]["/api/v1/assistant/plan-explanations"]["post"]
    assert route["requestBody"]["required"] is True
    response = document["components"]["schemas"]["PlanExplanationResponse"]
    assert {
        "context",
        "trace",
        "focus",
        "question",
        "question_answer",
        "summary",
        "tradeoffs",
        "task_changes",
        "manual_handling",
        "recommended_next_step",
        "evidence",
        "requires_human_confirmation",
        "modifies_plan",
        "safety_notice",
    } <= set(response["properties"])
