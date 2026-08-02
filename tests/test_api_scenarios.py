from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.events import apply_events
from backend.app.main import create_app
from backend.app.repository import InMemoryScenarioRepository
from backend.app.scenario_loader import load_scenario


SCENARIO_PATH = Path(__file__).parents[1] / "data" / "scenarios" / "terminal-disturbance-demo.json"


def load_payload() -> dict:
    return json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))


def test_default_app_opens_with_a_ready_to_explore_demo_scenario() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/scenarios")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["storage_scope"] == "process_memory"
    summary = payload["items"][0]
    assert summary["scenario_id"] == "SCN-TERMINAL-DISTURBANCE-01"
    assert summary["data_classification"] == "synthetic"
    assert summary["operational"] == {
        "flight_count": 3,
        "task_count": 10,
        "resource_count": 5,
        "zone_count": 3,
        "pending_event_count": 6,
        "applied_event_count": 0,
        "ready_for_planning": True,
        "warnings": [],
        "recommended_action": "先生成当前版本 FIFO 基线，再选择待处理事件进行重规划",
    }


def test_scenario_detail_explains_version_events_storage_and_safety() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/scenarios/SCN-TERMINAL-DISTURBANCE-01")

    assert response.status_code == 200
    record = response.json()
    assert record["selected_version"] == record["current_version"] == 1
    assert record["is_current_version"] is True
    assert record["available_versions"] == [1]
    assert record["scenario"]["events"] == []
    assert record["pending_event_ids"] == [
        "EVT-SIM102-DELAY",
        "EVT-SIM218-GATE",
        "EVT-SIM330-GATE",
        "EVT-SIM218-DELAY",
        "EVT-SIM330-DELAY",
        "EVT-SIM218-GATE-RETURN",
    ]
    assert record["applied_event_ids"] == []
    assert record["storage_scope"] == "process_memory"
    assert "仅供教学仿真" in record["safety_notice"]


def test_import_returns_validated_record_and_creates_one_audit() -> None:
    app = create_app(seed_demo=False)
    with TestClient(app) as client:
        response = client.post("/api/v1/scenarios", json=load_payload())

    assert response.status_code == 201
    record = response.json()
    assert record["summary"]["operational"]["ready_for_planning"] is True
    assert record["scenario"]["events"] == []
    assert len(record["pending_event_ids"]) == 6
    repository = app.state.scenario_repository
    assert len(repository.list_audit_records(record["summary"]["scenario_id"])) == 1


def test_incomplete_scenario_returns_deterministic_guidance() -> None:
    payload = load_payload()
    payload["scenario_id"] = "SCN-INCOMPLETE-DEMO"
    payload["name"] = "待补全资源场景"
    payload["tasks"] = []
    payload["resources"] = []

    with TestClient(create_app(seed_demo=False)) as client:
        response = client.post("/api/v1/scenarios", json=payload)

    assert response.status_code == 201
    operational = response.json()["summary"]["operational"]
    assert operational["ready_for_planning"] is False
    assert operational["warnings"] == [
        "场景没有保障任务，请先补充任务",
        "场景没有保障资源，请先补充资源",
    ]
    assert operational["recommended_action"] == "根据警告补全任务或可用资源后再规划"


def test_incompatible_available_resource_does_not_claim_planning_readiness() -> None:
    payload = load_payload()
    payload["scenario_id"] = "SCN-INCOMPATIBLE-RESOURCE"
    payload["name"] = "资源类型不匹配场景"
    payload["tasks"] = [payload["tasks"][0]]
    payload["resources"] = [
        resource
        for resource in payload["resources"]
        if resource["resource_type"] == "service_agent"
    ]

    with TestClient(create_app(seed_demo=False)) as client:
        response = client.post("/api/v1/scenarios", json=payload)

    assert response.status_code == 201
    operational = response.json()["summary"]["operational"]
    assert operational["ready_for_planning"] is False
    assert operational["warnings"] == ["缺少可用任务资源类型：wheelchair"]


def test_list_pagination_is_stable_and_reports_total() -> None:
    app = create_app(seed_demo=False)
    with TestClient(app) as client:
        for suffix in ("C", "A", "B"):
            payload = load_payload()
            payload["scenario_id"] = f"SCN-PAGE-{suffix}"
            payload["name"] = f"分页场景 {suffix}"
            assert client.post("/api/v1/scenarios", json=payload).status_code == 201

        response = client.get("/api/v1/scenarios", params={"offset": 1, "limit": 1})
        invalid = client.get("/api/v1/scenarios", params={"limit": 101})

    assert response.status_code == 200
    assert response.json()["total"] == 3
    assert [item["scenario_id"] for item in response.json()["items"]] == ["SCN-PAGE-B"]
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"
    assert invalid.json()["error"]["details"][0]["location"] == ["query", "limit"]


def test_history_query_distinguishes_selected_and_current_versions() -> None:
    repository = InMemoryScenarioRepository()
    baseline = repository.create_scenario(load_scenario(SCENARIO_PATH))
    event_id = "EVT-SIM102-DELAY"
    event = repository.get_events(baseline.scenario_id, (event_id,))[0]
    revision_input = repository.get_baseline(baseline.scenario_id)
    revision_input.events = [event]
    revision = apply_events(revision_input)
    repository.commit_event_revision(
        baseline.scenario_id,
        expected_version=1,
        revision=revision,
        event_ids=(event_id,),
    )
    app = create_app(repository=repository, seed_demo=False)

    with TestClient(app) as client:
        current = client.get(f"/api/v1/scenarios/{baseline.scenario_id}")
        historical = client.get(
            f"/api/v1/scenarios/{baseline.scenario_id}",
            params={"version": 1},
        )

    assert current.status_code == historical.status_code == 200
    assert current.json()["selected_version"] == current.json()["current_version"] == 2
    assert current.json()["applied_event_ids"] == [event_id]
    assert historical.json()["selected_version"] == 1
    assert historical.json()["current_version"] == 2
    assert historical.json()["is_current_version"] is False
    assert historical.json()["applied_event_ids"] == []
    assert historical.json()["available_versions"] == [1, 2]
    assert (
        historical.json()["scenario"]["flights"][0]["scheduled_departure"]
        != current.json()["scenario"]["flights"][0]["scheduled_departure"]
    )


def test_duplicate_import_returns_actionable_conflict_without_extra_audit() -> None:
    app = create_app(seed_demo=False)
    payload = load_payload()
    with TestClient(app) as client:
        assert client.post("/api/v1/scenarios", json=payload).status_code == 201
        response = client.post("/api/v1/scenarios", json=payload)

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "scenario_already_exists"
    assert "查询现有场景" in error["message"]
    assert error["details"][0]["location"] == ["body", "scenario_id"]
    repository = app.state.scenario_repository
    assert len(repository.list_audit_records(payload["scenario_id"])) == 1


def test_missing_scenario_and_version_return_distinct_guidance() -> None:
    with TestClient(create_app()) as client:
        missing_scenario = client.get("/api/v1/scenarios/SCN-MISSING")
        missing_version = client.get(
            "/api/v1/scenarios/SCN-TERMINAL-DISTURBANCE-01",
            params={"version": 99},
        )

    assert missing_scenario.status_code == 404
    assert missing_scenario.json()["error"]["code"] == "scenario_not_found"
    assert "查询场景列表" in missing_scenario.json()["error"]["message"]
    assert missing_version.status_code == 404
    assert missing_version.json()["error"]["code"] == "scenario_version_not_found"
    assert "available_versions" in missing_version.json()["error"]["message"]


def test_invalid_cross_reference_uses_field_level_validation_envelope() -> None:
    payload = deepcopy(load_payload())
    payload["scenario_id"] = "SCN-INVALID-REFERENCE"
    payload["tasks"][0]["flight_id"] = "FL-MISSING"

    with TestClient(create_app(seed_demo=False)) as client:
        response = client.post("/api/v1/scenarios", json=payload)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert any("unknown flight" in detail["message"] for detail in error["details"])
    assert "TASK-001" in response.text
    assert "前序航班保障延误" not in response.text


def test_openapi_exposes_scenario_workflow_without_chat_dependency() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/openapi.json")

    paths = response.json()["paths"]
    assert "/api/v1/scenarios" in paths
    assert set(paths["/api/v1/scenarios"]) >= {"get", "post"}
    assert "/api/v1/scenarios/{scenario_id}" in paths
