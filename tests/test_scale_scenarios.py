from __future__ import annotations

from collections import Counter

import pytest
from pydantic import ValidationError

from backend.app.models import (
    DataClassification,
    ResourceStatus,
    ResourceType,
    RunMode,
    Scenario,
    TaskType,
)
from backend.app.scale_models import ScaleTier
from backend.app.scale_scenario_factory import (
    ScaleScenarioArtifact,
    build_scale_scenario,
    compute_scale_scenario_fingerprint,
)


EXPECTED_FINGERPRINTS = {
    ScaleTier.SMALL: "7dc3827de6a1d702f73a2383473008f04170ba3ed1491e1817bcab73bb0bcf11",
    ScaleTier.MEDIUM: "5775f0c58d2d64e47cca1a8879ae14077ea65236a66f18a15816ce244a73d649",
    ScaleTier.LARGE_AGGREGATE: (
        "a5e0bae31aa7a91b70568df58326fe93a850ce26d3701234a39e14e620c9c70e"
    ),
}

EXPECTED_COUNTS = {
    ScaleTier.SMALL: (100, 20, 5, 10),
    ScaleTier.MEDIUM: (500, 50, 10, 50),
    ScaleTier.LARGE_AGGREGATE: (2000, 200, 20, 200),
}


@pytest.mark.parametrize("tier", list(ScaleTier))
def test_scale_factory_matches_canonical_counts_and_fingerprints(
    tier: ScaleTier,
) -> None:
    artifact = build_scale_scenario(tier)
    scenario = artifact.scenario
    task_count, resource_count, zone_count, flight_count = EXPECTED_COUNTS[tier]

    assert artifact.profile.tier is tier
    assert len(scenario.tasks) == task_count
    assert len(scenario.resources) == resource_count
    assert len(scenario.zones) == zone_count
    assert len(scenario.flights) == flight_count
    assert scenario.events == []
    assert scenario.version == 1
    assert scenario.run_mode is RunMode.SIMULATION
    assert scenario.data_classification is DataClassification.SYNTHETIC
    assert scenario.scenario_id == (
        f"SCN-SCALE-{tier.value.upper().replace('_', '-')}-01"
    )
    assert artifact.fingerprint_sha256 == EXPECTED_FINGERPRINTS[tier]
    assert artifact.fingerprint_sha256 == compute_scale_scenario_fingerprint(scenario)


@pytest.mark.parametrize("tier", list(ScaleTier))
def test_scale_factory_is_repeatable_isolated_and_json_stable(tier: ScaleTier) -> None:
    first = build_scale_scenario(tier)
    second = build_scale_scenario(tier.value)

    assert first == second
    assert first is not second
    assert first.scenario is not second.scenario
    assert first.scenario.tasks[0] is not second.scenario.tasks[0]

    first.scenario.tasks[0].priority = 5
    assert second.scenario.tasks[0].priority == 1
    assert compute_scale_scenario_fingerprint(first.scenario) != second.fingerprint_sha256

    round_tripped = ScaleScenarioArtifact.model_validate_json(second.model_dump_json())
    assert round_tripped == second
    assert Scenario.model_validate_json(second.scenario.model_dump_json()) == second.scenario


@pytest.mark.parametrize("tier", list(ScaleTier))
def test_scale_scenario_references_routes_and_time_windows_are_complete(
    tier: ScaleTier,
) -> None:
    scenario = build_scale_scenario(tier).scenario
    zone_ids = {zone.zone_id for zone in scenario.zones}
    flight_ids = {flight.flight_id for flight in scenario.flights}
    capacities_by_type = {
        resource_type: max(
            resource.capacity
            for resource in scenario.resources
            if resource.resource_type is resource_type
        )
        for resource_type in ResourceType
    }

    for zone in scenario.zones:
        assert set(zone.travel_minutes) == zone_ids
        assert zone.travel_minutes[zone.zone_id] == 0
        for target_zone_id, minutes in zone.travel_minutes.items():
            reverse = next(
                item for item in scenario.zones if item.zone_id == target_zone_id
            )
            assert reverse.travel_minutes[zone.zone_id] == minutes

    assert {task.flight_id for task in scenario.tasks} == flight_ids
    for task in scenario.tasks:
        assert task.origin_zone_id in zone_ids
        assert task.destination_zone_id in zone_ids
        assert task.origin_zone_id != task.destination_zone_id
        assert scenario.window_start <= task.release_at < task.deadline_at
        assert task.deadline_at <= scenario.window_end
        assert task.duration_minutes <= int(
            (task.deadline_at - task.release_at).total_seconds() // 60
        )
        assert task.party_size <= capacities_by_type[task.required_resource_type]

    for resource in scenario.resources:
        assert resource.home_zone_id in zone_ids
        assert resource.current_zone_id in zone_ids
        assert resource.status is ResourceStatus.AVAILABLE
        assert resource.available_from == scenario.window_start
        assert resource.available_to == scenario.window_end

    for flight in scenario.flights:
        assert flight.flight_id.startswith("FL-SCALE-")
        assert flight.display_code.startswith("SCL")
        assert flight.gate_id in zone_ids
        assert scenario.window_start <= flight.boarding_starts_at
        assert flight.boarding_starts_at < flight.scheduled_departure
        assert flight.scheduled_departure <= scenario.window_end


@pytest.mark.parametrize("tier", list(ScaleTier))
def test_scale_workload_keeps_every_resource_type_represented(tier: ScaleTier) -> None:
    scenario = build_scale_scenario(tier).scenario
    resource_counts = Counter(item.resource_type for item in scenario.resources)
    required_counts = Counter(item.required_resource_type for item in scenario.tasks)
    task_type_counts = Counter(item.task_type for item in scenario.tasks)

    assert set(resource_counts) == set(ResourceType)
    assert set(required_counts) == set(ResourceType)
    assert set(task_type_counts) == set(TaskType)
    assert all(count > 0 for count in resource_counts.values())
    assert all(count > 0 for count in required_counts.values())
    assert all(count > 0 for count in task_type_counts.values())


def test_scale_artifact_rejects_content_tampering_and_unknown_tiers() -> None:
    artifact = build_scale_scenario(ScaleTier.SMALL)
    payload = artifact.model_dump(mode="python")
    payload["scenario"]["tasks"][0]["priority"] = 2

    with pytest.raises(ValidationError, match="fingerprint does not match"):
        ScaleScenarioArtifact.model_validate(payload)
    with pytest.raises(ValueError):
        build_scale_scenario("production")
