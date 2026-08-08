"""Deterministic synthetic scenario fixtures for M6 scale validation."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from math import ceil

from pydantic import Field, model_validator

from .models import (
    DataClassification,
    Flight,
    FlightStatus,
    ModelBase,
    PassengerGroup,
    Resource,
    ResourceStatus,
    ResourceType,
    RunMode,
    Scenario,
    ServiceTask,
    TaskType,
    Zone,
)
from .scale_models import ScaleProfile, ScaleTier, get_scale_profile


_FIXTURE_TIMEZONE = timezone(timedelta(hours=8))
_WINDOW_START = datetime(2026, 8, 1, 8, 0, tzinfo=_FIXTURE_TIMEZONE)
_WINDOW_END = _WINDOW_START + timedelta(hours=12)
_FINGERPRINT_NAMESPACE = b"ican-m6-scale-scenario-v1\n"

_RESOURCE_CYCLE = (
    ResourceType.WHEELCHAIR,
    ResourceType.SERVICE_AGENT,
    ResourceType.SHUTTLE_BUS,
    ResourceType.WHEELCHAIR,
)
_TASK_CYCLE = (
    (TaskType.WHEELCHAIR_TRANSFER, ResourceType.WHEELCHAIR),
    (TaskType.ESCORT, ResourceType.SERVICE_AGENT),
    (TaskType.SHUTTLE_TRANSFER, ResourceType.SHUTTLE_BUS),
    (TaskType.BOARDING_ASSISTANCE, ResourceType.WHEELCHAIR),
)
_PASSENGER_GROUP_CYCLE = (
    PassengerGroup.SPECIAL_ASSISTANCE,
    PassengerGroup.URGENT_CONNECTION,
    PassengerGroup.GENERAL_ASSISTANCE,
)


def compute_scale_scenario_fingerprint(scenario: Scenario) -> str:
    """Return a canonical SHA-256 fingerprint for a validated scenario."""

    canonical_payload = json.dumps(
        scenario.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(_FINGERPRINT_NAMESPACE + canonical_payload).hexdigest()


class ScaleScenarioArtifact(ModelBase):
    """A canonical scale profile bound to its generated scenario fingerprint."""

    profile: ScaleProfile
    scenario: Scenario
    fingerprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_artifact(self) -> ScaleScenarioArtifact:
        canonical_profile = get_scale_profile(self.profile.tier)
        if self.profile != canonical_profile:
            raise ValueError("scale scenario artifact must use the canonical profile")
        if len(self.scenario.tasks) != self.profile.task_count:
            raise ValueError("scale scenario task count must match its profile")
        if len(self.scenario.resources) != self.profile.resource_count:
            raise ValueError("scale scenario resource count must match its profile")
        if len(self.scenario.zones) != self.profile.zone_count:
            raise ValueError("scale scenario zone count must match its profile")
        if self.scenario.data_classification is not DataClassification.SYNTHETIC:
            raise ValueError("scale scenarios must use synthetic data")
        if self.scenario.run_mode is not RunMode.SIMULATION:
            raise ValueError("scale scenarios must use simulation mode")
        if self.scenario.events:
            raise ValueError("M6 scale fixtures cannot contain runtime events")
        expected_fingerprint = compute_scale_scenario_fingerprint(self.scenario)
        if self.fingerprint_sha256 != expected_fingerprint:
            raise ValueError("scale scenario fingerprint does not match its content")
        return self


def _scenario_slug(tier: ScaleTier) -> str:
    return tier.value.upper().replace("_", "-")


def _build_zones(profile: ScaleProfile) -> list[Zone]:
    zone_ids = [f"ZONE-{index:03d}" for index in range(1, profile.zone_count + 1)]
    zones: list[Zone] = []
    for source_index, zone_id in enumerate(zone_ids):
        travel_minutes: dict[str, int] = {}
        for target_index, target_zone_id in enumerate(zone_ids):
            direct_steps = abs(source_index - target_index)
            ring_steps = min(direct_steps, profile.zone_count - direct_steps)
            travel_minutes[target_zone_id] = ring_steps * 3
        zones.append(
            Zone(
                zone_id=zone_id,
                name=f"Synthetic zone {source_index + 1:03d}",
                travel_minutes=travel_minutes,
            )
        )
    return zones


def _build_flights(profile: ScaleProfile, zones: list[Zone]) -> list[Flight]:
    flight_count = max(profile.zone_count, ceil(profile.task_count / 10))
    flights: list[Flight] = []
    for index in range(flight_count):
        departure = _WINDOW_START + timedelta(hours=6, minutes=index % 120)
        flights.append(
            Flight(
                flight_id=f"FL-SCALE-{index + 1:04d}",
                display_code=f"SCL{index + 1:04d}",
                scheduled_departure=departure,
                boarding_starts_at=departure - timedelta(minutes=45),
                gate_id=zones[index % len(zones)].zone_id,
                status=FlightStatus.SCHEDULED,
            )
        )
    return flights


def _resource_capacity(resource_type: ResourceType) -> int:
    if resource_type is ResourceType.SHUTTLE_BUS:
        return 24
    if resource_type is ResourceType.SERVICE_AGENT:
        return 6
    return 2


def _resource_prefix(resource_type: ResourceType) -> str:
    if resource_type is ResourceType.SHUTTLE_BUS:
        return "BUS"
    if resource_type is ResourceType.SERVICE_AGENT:
        return "AGENT"
    return "WC"


def _build_resources(profile: ScaleProfile, zones: list[Zone]) -> list[Resource]:
    type_counts = {resource_type: 0 for resource_type in ResourceType}
    resources: list[Resource] = []
    for index in range(profile.resource_count):
        resource_type = _RESOURCE_CYCLE[index % len(_RESOURCE_CYCLE)]
        type_counts[resource_type] += 1
        zone_id = zones[index % len(zones)].zone_id
        resources.append(
            Resource(
                resource_id=(
                    f"{_resource_prefix(resource_type)}-"
                    f"{type_counts[resource_type]:04d}"
                ),
                resource_type=resource_type,
                home_zone_id=zone_id,
                current_zone_id=zone_id,
                capacity=_resource_capacity(resource_type),
                available_from=_WINDOW_START,
                available_to=_WINDOW_END,
                status=ResourceStatus.AVAILABLE,
            )
        )
    return resources


def _task_party_size(resource_type: ResourceType, index: int) -> int:
    if resource_type is ResourceType.SHUTTLE_BUS:
        return 4 + (index % 9)
    if resource_type is ResourceType.SERVICE_AGENT:
        return 1 + (index % 4)
    return 1 + (index % 2)


def _build_tasks(
    profile: ScaleProfile,
    zones: list[Zone],
    flights: list[Flight],
) -> list[ServiceTask]:
    tasks: list[ServiceTask] = []
    for index in range(profile.task_count):
        task_type, required_resource_type = _TASK_CYCLE[index % len(_TASK_CYCLE)]
        origin_index = index % len(zones)
        destination_step = 1 + ((index // len(zones)) % (len(zones) - 1))
        destination_index = (origin_index + destination_step) % len(zones)
        release_offset = (index // profile.resource_count) * 30 + origin_index
        release_at = _WINDOW_START + timedelta(minutes=release_offset)
        tasks.append(
            ServiceTask(
                task_id=f"TASK-SCALE-{index + 1:05d}",
                flight_id=flights[index % len(flights)].flight_id,
                task_type=task_type,
                passenger_group=_PASSENGER_GROUP_CYCLE[
                    index % len(_PASSENGER_GROUP_CYCLE)
                ],
                origin_zone_id=zones[origin_index].zone_id,
                destination_zone_id=zones[destination_index].zone_id,
                release_at=release_at,
                deadline_at=release_at + timedelta(minutes=90),
                duration_minutes=6 + (index % 4),
                party_size=_task_party_size(required_resource_type, index),
                priority=1 + (index % 5),
                required_resource_type=required_resource_type,
                locked=False,
            )
        )
    return tasks


def build_scale_scenario(tier: ScaleTier | str) -> ScaleScenarioArtifact:
    """Build a fresh deterministic, anonymous scenario for a canonical M6 tier."""

    profile = get_scale_profile(tier)
    zones = _build_zones(profile)
    flights = _build_flights(profile, zones)
    resources = _build_resources(profile, zones)
    tasks = _build_tasks(profile, zones, flights)
    slug = _scenario_slug(profile.tier)
    scenario = Scenario(
        scenario_id=f"SCN-SCALE-{slug}-01",
        name=f"Synthetic {profile.tier.value} scale scenario",
        version=1,
        run_mode=RunMode.SIMULATION,
        data_classification=DataClassification.SYNTHETIC,
        window_start=_WINDOW_START,
        window_end=_WINDOW_END,
        zones=zones,
        flights=flights,
        events=[],
        tasks=tasks,
        resources=resources,
    )
    return ScaleScenarioArtifact(
        profile=profile,
        scenario=scenario,
        fingerprint_sha256=compute_scale_scenario_fingerprint(scenario),
    )
