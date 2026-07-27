from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.models import Scenario
from backend.app.scenario_loader import ScenarioLoadError, load_scenario


SCENARIO_PATH = Path(__file__).parents[1] / "data" / "scenarios" / "terminal-disturbance-demo.json"
INVALID_JSON_PATH = Path(__file__).parent / "fixtures" / "invalid-json.json"


def test_demo_scenario_loads_with_cross_references() -> None:
    scenario = load_scenario(SCENARIO_PATH)

    assert scenario.scenario_id == "SCN-TERMINAL-DISTURBANCE-01"
    assert scenario.data_classification.value == "synthetic"
    assert len(scenario.zones) == 3
    assert len(scenario.flights) == 2
    assert len(scenario.events) == 2
    assert len(scenario.tasks) == 5
    assert len(scenario.resources) == 4
    assert {flight.display_code for flight in scenario.flights} == {"SIM102", "SIM218"}
    assert {task.flight_id for task in scenario.tasks} == {"FL-SIM102", "FL-SIM218"}


def test_scenario_rejects_unknown_task_flight() -> None:
    payload = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    payload["tasks"][0]["flight_id"] = "FL-MISSING"

    with pytest.raises(ValidationError, match="unknown flight"):
        Scenario.model_validate(payload)


def test_scenario_rejects_duplicate_resource_id() -> None:
    payload = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    payload["resources"].append(deepcopy(payload["resources"][0]))

    with pytest.raises(ValidationError, match="resource IDs must be unique"):
        Scenario.model_validate(payload)


def test_scenario_rejects_unknown_travel_zone() -> None:
    payload = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    payload["zones"][0]["travel_minutes"]["ZONE-MISSING"] = 5

    with pytest.raises(ValidationError, match="travel time references unknown zone"):
        Scenario.model_validate(payload)


def test_scenario_rejects_naive_datetime() -> None:
    payload = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    payload["window_start"] = "2026-08-01T08:00:00"

    with pytest.raises(ValidationError, match="timezone offset"):
        Scenario.model_validate(payload)


def test_loader_reports_invalid_json() -> None:
    with pytest.raises(ScenarioLoadError, match="invalid JSON"):
        load_scenario(INVALID_JSON_PATH)
