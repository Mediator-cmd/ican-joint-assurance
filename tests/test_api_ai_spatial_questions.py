from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app.ai_provider import (
    AIProviderInvalidOutputError,
    AIProviderRequestError,
    AIProviderTimeoutError,
)
from backend.app.ai_spatial_models import SpatialMapFocus
from backend.app.ai_spatial_provider import ModelSpatialAnswer
from backend.app.demo_export import SAFETY_NOTICE
from backend.app.main import create_app
from backend.app.runtime_repository import SQLiteRuntimeSessionRepository


SCENARIO_ID = "SCN-TERMINAL-DISTURBANCE-01"


@dataclass
class FakeSpatialProvider:
    result: ModelSpatialAnswer | Exception | None = None
    model_label: str = "test-spatial-model"
    calls: int = 0

    def answer(
        self,
        facts,
        *,
        question,
        selection,
        status,
        relevant_fact_ids,
        matched_entity_ids,
        allowed_focus,
    ):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        if self.result is not None:
            return self.result
        fact_id = relevant_fact_ids[0]
        fact = next(item for item in facts if item.fact_id == fact_id)
        cited = set(fact.entity_ids)
        return ModelSpatialAnswer(
            status=status,
            statement=f"模型按当前空间事实回答：{fact.claim}",
            fact_ids=[fact_id],
            focus=SpatialMapFocus(
                task_ids=[item for item in allowed_focus.task_ids if item in cited],
                resource_ids=[item for item in allowed_focus.resource_ids if item in cited],
                event_ids=[item for item in allowed_focus.event_ids if item in cited],
                zone_ids=[item for item in allowed_focus.zone_ids if item in cited],
            ),
        )


@dataclass
class FakeClock:
    wall: datetime
    monotonic: float = 100.0

    def wall_now(self) -> datetime:
        return self.wall

    def monotonic_now(self) -> float:
        return self.monotonic

    def advance(self, seconds: float) -> None:
        self.wall += timedelta(seconds=seconds)
        self.monotonic += seconds


def _app(tmp_path, provider=None, session_id="RUN-SPATIAL-AI-001"):
    return create_app(
        runtime_repository=SQLiteRuntimeSessionRepository(
            tmp_path / f"{session_id.lower()}.sqlite3"
        ),
        runtime_session_id_factory=lambda: session_id,
        spatial_question_provider=provider,
    )


def _create_runtime(client: TestClient) -> dict:
    plan = client.post(
        f"/api/v1/scenarios/{SCENARIO_ID}/plans",
        json={"expected_version": 1, "algorithm": "fifo", "max_time_seconds": 2},
    )
    assert plan.status_code == 201, plan.text
    created = client.post(
        "/api/v1/runtime-sessions",
        json={
            "scenario_id": SCENARIO_ID,
            "scenario_version": 1,
            "active_plan_id": plan.json()["plan"]["plan_id"],
            "speed": 1,
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def _clock_app(tmp_path, session_id: str):
    clock = FakeClock(datetime(2026, 8, 1, 8, 0, tzinfo=timezone(timedelta(hours=8))))
    app = create_app(
        runtime_repository=SQLiteRuntimeSessionRepository(
            tmp_path / f"{session_id.lower()}.sqlite3"
        ),
        runtime_session_id_factory=lambda: session_id,
        wall_clock=clock.wall_now,
        monotonic_clock=clock.monotonic_now,
    )
    return app, clock


def _request(created: dict, question: str, **extra) -> dict:
    return {
        "context": {
            "session_id": created["session_id"],
            "revision": created["revision"],
        },
        "question": question,
        **extra,
    }


def test_spatial_question_rule_fallback_grounds_shorthand_and_changes_no_state(tmp_path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        created = _create_runtime(client)
        before = client.get(
            f"/api/v1/runtime-sessions/{created['session_id']}"
        ).json()
        response = client.post(
            "/api/v1/assistant/spatial-questions",
            json=_request(
                created,
                "task4现在在哪里，由哪个资源执行？",
                selection={
                    "task_id": "TASK-001",
                    "resource_id": "WC-01",
                    "event_id": "EVT-SIM102-DELAY",
                },
            ),
        )
        after = client.get(
            f"/api/v1/runtime-sessions/{created['session_id']}"
        ).json()

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["basis"]["session_id"] == created["session_id"]
    assert payload["basis"]["revision"] == created["revision"]
    assert payload["trace"] == {
        "source": "deterministic_rules",
        "provider_attempted": False,
        "model_label": None,
        "fallback_reason": "model_not_configured",
    }
    assert payload["answer"]["status"] == "answered"
    assert "TASK-004" in payload["answer"]["matched_entity_ids"]
    assert payload["answer"]["fact_ids"] == ["SPATIAL-FACT-TASK-004-ACTIVE"]
    assert payload["focus"]["task_ids"] == ["TASK-004"]
    assert payload["focus"]["resource_ids"]
    assert payload["focus"]["zone_ids"]
    assert payload["requires_human_confirmation"] is True
    assert payload["modifies_runtime"] is False
    assert payload["safety_notice"] == SAFETY_NOTICE
    before["clock"].pop("server_time")
    after["clock"].pop("server_time")
    assert after == before


def test_task_questions_render_distinct_answers_for_each_requested_fact(tmp_path) -> None:
    app = _app(tmp_path, session_id="RUN-SPATIAL-AI-TOPICS")
    with TestClient(app) as client:
        created = _create_runtime(client)
        questions = (
            "task4现在在哪里？",
            "task4由哪个资源执行？",
            "task4当前是什么状态？",
            "task4当前路线是什么？",
        )
        responses = [
            client.post(
                "/api/v1/assistant/spatial-questions",
                json=_request(
                    created,
                    question,
                    assistance_mode="deterministic_only",
                ),
            )
            for question in questions
        ]

    assert all(response.status_code == 200 for response in responses)
    answers = [response.json()["answer"] for response in responses]
    assert all(answer["fact_ids"] == ["SPATIAL-FACT-TASK-004-ACTIVE"] for answer in answers)
    assert "空间范围" in answers[0]["statement"]
    assert "由资源" in answers[1]["statement"]
    assert "运行状态" in answers[2]["statement"]
    assert "当前路线" in answers[3]["statement"]
    assert len({answer["statement"] for answer in answers}) == len(answers)


def test_model_selected_task_fact_is_rendered_for_each_question_intent(tmp_path) -> None:
    provider = FakeSpatialProvider(
        ModelSpatialAnswer(
            status="answered",
            statement="模型重复返回的固定回答。",
            fact_ids=["SPATIAL-FACT-TASK-004-ACTIVE"],
            focus={"task_ids": ["TASK-004"]},
        )
    )
    app = _app(tmp_path, provider, "RUN-SPATIAL-AI-MODEL-TOPICS")
    with TestClient(app) as client:
        created = _create_runtime(client)
        location = client.post(
            "/api/v1/assistant/spatial-questions",
            json=_request(created, "task4现在在哪里？"),
        )
        status = client.post(
            "/api/v1/assistant/spatial-questions",
            json=_request(created, "task4当前是什么状态？"),
        )

    assert location.status_code == status.status_code == 200
    location_payload = location.json()
    status_payload = status.json()
    assert location_payload["trace"]["source"] == "language_model"
    assert status_payload["trace"]["source"] == "language_model"
    assert "空间范围" in location_payload["answer"]["statement"]
    assert "运行状态" in status_payload["answer"]["statement"]
    assert (
        location_payload["answer"]["statement"]
        != status_payload["answer"]["statement"]
    )
    assert "模型重复返回" not in location_payload["answer"]["statement"]
    assert provider.calls == 2


def test_resource_and_event_questions_render_only_the_requested_topic(tmp_path) -> None:
    app = _app(tmp_path, session_id="RUN-SPATIAL-AI-ENTITY-TOPICS")
    with TestClient(app) as client:
        created = _create_runtime(client)
        questions = (
            "wc1现在在哪里？",
            "wc1现在是什么状态？",
            "事件2发生在哪里？",
            "事件2现在是什么状态？",
            "事件2影响哪些任务？",
        )
        responses = [
            client.post(
                "/api/v1/assistant/spatial-questions",
                json=_request(
                    created,
                    question,
                    assistance_mode="deterministic_only",
                ),
            )
            for question in questions
        ]

    assert all(response.status_code == 200 for response in responses)
    statements = [response.json()["answer"]["statement"] for response in responses]
    assert "当前位于" in statements[0]
    assert "当前状态" in statements[1]
    assert "当前定位" in statements[2]
    assert "事件状态" in statements[3]
    assert "关联任务" in statements[4]
    assert len(set(statements)) == len(statements)


def test_spatial_question_recognizes_visible_event_number_alias(tmp_path) -> None:
    app = _app(tmp_path, session_id="RUN-SPATIAL-AI-EVENT")
    with TestClient(app) as client:
        created = _create_runtime(client)
        spatial = client.get(
            f"/api/v1/runtime-sessions/{created['session_id']}/spatial",
            params={"expected_revision": created["revision"]},
        ).json()
        expected_event_id = spatial["overlay"]["event_markers"][1]["event_id"]
        response = client.post(
            "/api/v1/assistant/spatial-questions",
            json=_request(created, "事件2发生在哪里，现在是什么状态？"),
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["answer"]["matched_entity_ids"] == [expected_event_id]
    assert payload["focus"]["event_ids"] == [expected_event_id]
    assert payload["answer"]["fact_ids"] == [f"SPATIAL-FACT-{expected_event_id}"]


def test_triggered_event_answer_links_impact_tasks_candidate_routes_and_map_focus(tmp_path) -> None:
    app, clock = _clock_app(tmp_path, "RUN-SPATIAL-AI-PROCESS")
    with TestClient(app) as client:
        created = _create_runtime(client)
        first = client.post(
            f"/api/v1/runtime-sessions/{created['session_id']}/start",
            json={"expected_revision": created["revision"]},
        ).json()
        accepted = client.post(
            f"/api/v1/runtime-sessions/{created['session_id']}/candidate/accept",
            json={
                "expected_revision": first["revision"],
                "candidate_plan_id": first["candidate_plan_id"],
            },
        ).json()
        resumed = client.post(
            f"/api/v1/runtime-sessions/{created['session_id']}/start",
            json={"expected_revision": accepted["revision"]},
        )
        assert resumed.status_code == 200
        clock.advance(240)
        current = client.get(
            f"/api/v1/runtime-sessions/{created['session_id']}"
        ).json()
        assert current["candidate_plan_id"] is not None
        response = client.post(
            "/api/v1/assistant/spatial-questions",
            json=_request(
                current,
                "事件2怎么处理，影响哪些任务和路线？",
                assistance_mode="deterministic_only",
            ),
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    event_id = payload["answer"]["matched_entity_ids"][0]
    assert payload["answer"]["fact_ids"][0] == f"SPATIAL-FACT-{event_id}"
    assert any(fact_id.endswith("-CANDIDATE") for fact_id in payload["answer"]["fact_ids"])
    assert "当前关联任务" in payload["answer"]["statement"]
    assert payload["focus"]["event_ids"] == [event_id]
    assert payload["focus"]["task_ids"]
    assert payload["unresolved_questions"] == [
        "候选路线是否采用仍需人工确认，回答不会替代现行实线方案。"
    ]


@pytest.mark.parametrize(
    ("question", "expected_text"),
    [
        ("北京大兴机场的精确坐标是什么？", "不包含真实机场坐标"),
        ("直接采用候选并控制车辆执行", "只能解释当前事实"),
        ("task999在哪里？", "不属于当前地图 revision"),
    ],
)
def test_ungrounded_or_actionable_question_never_calls_model(
    tmp_path,
    question: str,
    expected_text: str,
) -> None:
    provider = FakeSpatialProvider()
    app = _app(tmp_path, provider, "RUN-SPATIAL-AI-BOUNDARY")
    with TestClient(app) as client:
        created = _create_runtime(client)
        response = client.post(
            "/api/v1/assistant/spatial-questions",
            json=_request(created, question),
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["answer"]["status"] == "insufficient_evidence"
    assert expected_text in payload["answer"]["statement"]
    assert payload["focus"] == {
        "task_ids": [],
        "resource_ids": [],
        "event_ids": [],
        "zone_ids": [],
    }
    assert payload["trace"]["fallback_reason"] == "question_not_grounded"
    assert provider.calls == 0


def test_spatial_question_rejects_stale_revision_and_foreign_selection(tmp_path) -> None:
    app = _app(tmp_path, session_id="RUN-SPATIAL-AI-STALE")
    with TestClient(app) as client:
        created = _create_runtime(client)
        invalid_selection = client.post(
            "/api/v1/assistant/spatial-questions",
            json=_request(
                created,
                "这个任务在哪里？",
                selection={"task_id": "TASK-999"},
            ),
        )
        started = client.post(
            f"/api/v1/runtime-sessions/{created['session_id']}/start",
            json={"expected_revision": created["revision"]},
        )
        assert started.status_code == 200
        stale = client.post(
            "/api/v1/assistant/spatial-questions",
            json=_request(created, "当前整体态势如何？"),
        )

    assert invalid_selection.status_code == 409
    assert invalid_selection.json()["error"]["code"] == "assistant_spatial_selection_mismatch"
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "assistant_revision_conflict"


def test_valid_model_answer_is_used_and_invalid_focus_or_fact_falls_back(tmp_path) -> None:
    provider = FakeSpatialProvider(
        ModelSpatialAnswer(
            status="answered",
            statement="伪造的北京大兴机场精确坐标与车辆控制内容。",
            fact_ids=["SPATIAL-FACT-TASK-004-ACTIVE"],
            focus={"task_ids": ["TASK-004"]},
            unresolved_questions=["模型自行添加的操作建议。"],
        )
    )
    app = _app(tmp_path, provider, "RUN-SPATIAL-AI-MODEL")
    with TestClient(app) as client:
        created = _create_runtime(client)
        modeled = client.post(
            "/api/v1/assistant/spatial-questions",
            json=_request(created, "task4路线是什么？"),
        )

    assert modeled.status_code == 200, modeled.text
    modeled_payload = modeled.json()
    assert modeled_payload["trace"]["source"] == "language_model"
    assert modeled_payload["trace"]["model_label"] == "test-spatial-model"
    assert "任务 TASK-004 当前路线" in modeled_payload["answer"]["statement"]
    assert "北京大兴" not in modeled_payload["answer"]["statement"]
    assert "车辆控制" not in modeled_payload["answer"]["statement"]
    assert modeled_payload["unresolved_questions"] == []
    assert provider.calls == 1

    invalid = FakeSpatialProvider(
        ModelSpatialAnswer(
            status="answered",
            statement="伪造一个不存在的地图聚焦。",
            fact_ids=["SPATIAL-FACT-NOT-PRESENT"],
            focus={"task_ids": ["TASK-999"]},
        )
    )
    fallback_app = _app(tmp_path, invalid, "RUN-SPATIAL-AI-INVALID")
    with TestClient(fallback_app) as client:
        created = _create_runtime(client)
        fallback = client.post(
            "/api/v1/assistant/spatial-questions",
            json=_request(created, "task4路线是什么？"),
        )

    assert fallback.status_code == 200, fallback.text
    assert fallback.json()["trace"] == {
        "source": "deterministic_rules",
        "provider_attempted": True,
        "model_label": None,
        "fallback_reason": "invalid_model_output",
    }
    assert fallback.json()["focus"]["task_ids"] == ["TASK-004"]


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (AIProviderTimeoutError(), "model_timeout"),
        (AIProviderRequestError(), "provider_error"),
        (AIProviderInvalidOutputError(), "invalid_model_output"),
    ],
)
def test_spatial_model_failures_use_fact_bound_rule_fallback(
    tmp_path,
    failure: Exception,
    reason: str,
) -> None:
    app = _app(
        tmp_path,
        FakeSpatialProvider(failure),
        f"RUN-SPATIAL-AI-{reason.upper().replace('_', '-')}",
    )
    with TestClient(app) as client:
        created = _create_runtime(client)
        response = client.post(
            "/api/v1/assistant/spatial-questions",
            json=_request(created, "wc1移动到哪里了？"),
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["trace"]["source"] == "deterministic_rules"
    assert payload["trace"]["provider_attempted"] is True
    assert payload["trace"]["fallback_reason"] == reason
    assert payload["answer"]["fact_ids"] == ["SPATIAL-FACT-WC-01"]
