"""Validated domain models for anonymous airport service simulations.

The models intentionally describe training data rather than production airport
records. Cross-entity references are checked at the Scenario boundary so a
loaded JSON file cannot silently contain orphan tasks or resources.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ModelBase(BaseModel):
    """Shared strict settings for every persisted model."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class RunMode(str, Enum):
    SIMULATION = "simulation"
    REPLAY = "replay"


class DataClassification(str, Enum):
    SYNTHETIC = "synthetic"
    ANONYMIZED_REPLAY = "anonymized_replay"


class FlightStatus(str, Enum):
    SCHEDULED = "scheduled"
    BOARDING = "boarding"
    DELAYED = "delayed"
    DEPARTED = "departed"


class FlightEventType(str, Enum):
    DELAY = "delay"
    GATE_CHANGE = "gate_change"


class PassengerGroup(str, Enum):
    SPECIAL_ASSISTANCE = "special_assistance"
    URGENT_CONNECTION = "urgent_connection"
    GENERAL_ASSISTANCE = "general_assistance"


class TaskType(str, Enum):
    WHEELCHAIR_TRANSFER = "wheelchair_transfer"
    ESCORT = "escort"
    SHUTTLE_TRANSFER = "shuttle_transfer"
    BOARDING_ASSISTANCE = "boarding_assistance"


class ResourceType(str, Enum):
    SHUTTLE_BUS = "shuttle_bus"
    WHEELCHAIR = "wheelchair"
    SERVICE_AGENT = "service_agent"


class ResourceStatus(str, Enum):
    AVAILABLE = "available"
    BUSY = "busy"
    UNAVAILABLE = "unavailable"


def _require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone offset")
    return value


def _unique_ids(items: list[Any], id_field: str, label: str) -> set[str]:
    ids = [getattr(item, id_field) for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{label} IDs must be unique")
    return set(ids)


class Zone(ModelBase):
    zone_id: str = Field(min_length=1, pattern=r"^[A-Z0-9-]+$")
    name: str = Field(min_length=1)
    travel_minutes: dict[str, int] = Field(default_factory=dict)

    @field_validator("travel_minutes")
    @classmethod
    def validate_travel_minutes(cls, value: dict[str, int]) -> dict[str, int]:
        if any(not target or minutes < 0 for target, minutes in value.items()):
            raise ValueError("travel_minutes keys must be non-empty and values must be non-negative")
        return value


class Flight(ModelBase):
    flight_id: str = Field(min_length=1, pattern=r"^FL-[A-Z0-9-]+$")
    display_code: str = Field(min_length=2, max_length=12)
    scheduled_departure: datetime
    boarding_starts_at: datetime
    gate_id: str = Field(min_length=1)
    status: FlightStatus = FlightStatus.SCHEDULED

    @model_validator(mode="after")
    def validate_timeline(self) -> Flight:
        _require_aware(self.scheduled_departure, "scheduled_departure")
        _require_aware(self.boarding_starts_at, "boarding_starts_at")
        if self.boarding_starts_at >= self.scheduled_departure:
            raise ValueError("boarding_starts_at must be before scheduled_departure")
        return self


class FlightEvent(ModelBase):
    event_id: str = Field(min_length=1, pattern=r"^EVT-[A-Z0-9-]+$")
    event_type: FlightEventType
    flight_id: str = Field(min_length=1)
    occurred_at: datetime
    delay_minutes: int | None = Field(default=None, ge=0)
    previous_gate_id: str | None = Field(default=None, min_length=1)
    new_gate_id: str | None = Field(default=None, min_length=1)
    note: str | None = Field(default=None, max_length=240)

    @model_validator(mode="after")
    def validate_event_payload(self) -> FlightEvent:
        _require_aware(self.occurred_at, "occurred_at")
        if self.event_type is FlightEventType.DELAY and self.delay_minutes is None:
            raise ValueError("delay event requires delay_minutes")
        if self.event_type is FlightEventType.GATE_CHANGE:
            if not self.previous_gate_id or not self.new_gate_id:
                raise ValueError("gate_change event requires previous_gate_id and new_gate_id")
            if self.previous_gate_id == self.new_gate_id:
                raise ValueError("gate_change must change to a different gate")
        return self


class ServiceTask(ModelBase):
    task_id: str = Field(min_length=1, pattern=r"^TASK-[A-Z0-9-]+$")
    flight_id: str = Field(min_length=1)
    task_type: TaskType
    passenger_group: PassengerGroup
    origin_zone_id: str = Field(min_length=1)
    destination_zone_id: str = Field(min_length=1)
    release_at: datetime
    deadline_at: datetime
    duration_minutes: int = Field(gt=0, le=180)
    party_size: int = Field(default=1, gt=0, le=50)
    priority: int = Field(default=3, ge=1, le=5)
    required_resource_type: ResourceType
    locked: bool = False

    @model_validator(mode="after")
    def validate_timeline(self) -> ServiceTask:
        _require_aware(self.release_at, "release_at")
        _require_aware(self.deadline_at, "deadline_at")
        if self.release_at >= self.deadline_at:
            raise ValueError("release_at must be before deadline_at")
        return self


class Resource(ModelBase):
    resource_id: str = Field(min_length=1, pattern=r"^[A-Z]+-[A-Z0-9-]+$")
    resource_type: ResourceType
    home_zone_id: str = Field(min_length=1)
    current_zone_id: str = Field(min_length=1)
    capacity: int = Field(gt=0, le=100)
    available_from: datetime
    available_to: datetime
    status: ResourceStatus = ResourceStatus.AVAILABLE

    @model_validator(mode="after")
    def validate_availability(self) -> Resource:
        _require_aware(self.available_from, "available_from")
        _require_aware(self.available_to, "available_to")
        if self.available_from >= self.available_to:
            raise ValueError("available_from must be before available_to")
        return self


class Scenario(ModelBase):
    scenario_id: str = Field(min_length=1, pattern=r"^SCN-[A-Z0-9-]+$")
    name: str = Field(min_length=1, max_length=120)
    version: int = Field(default=1, ge=1)
    run_mode: RunMode = RunMode.SIMULATION
    data_classification: DataClassification
    window_start: datetime
    window_end: datetime
    zones: list[Zone] = Field(min_length=1)
    flights: list[Flight] = Field(default_factory=list)
    events: list[FlightEvent] = Field(default_factory=list)
    tasks: list[ServiceTask] = Field(default_factory=list)
    resources: list[Resource] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_integrity(self) -> Scenario:
        _require_aware(self.window_start, "window_start")
        _require_aware(self.window_end, "window_end")
        if self.window_start >= self.window_end:
            raise ValueError("window_start must be before window_end")

        zone_ids = _unique_ids(self.zones, "zone_id", "zone")
        flight_ids = _unique_ids(self.flights, "flight_id", "flight")
        _unique_ids(self.events, "event_id", "event")
        _unique_ids(self.tasks, "task_id", "task")
        _unique_ids(self.resources, "resource_id", "resource")

        def ensure_in_window(value: datetime, label: str) -> None:
            if value < self.window_start or value > self.window_end:
                raise ValueError(f"{label} must fall within the scenario window")

        for zone in self.zones:
            for target_zone_id in zone.travel_minutes:
                if target_zone_id not in zone_ids:
                    raise ValueError(
                        f"zone {zone.zone_id} travel time references unknown zone {target_zone_id}"
                    )

        for flight in self.flights:
            ensure_in_window(flight.scheduled_departure, f"flight {flight.flight_id} departure")
            if flight.gate_id not in zone_ids:
                raise ValueError(f"flight {flight.flight_id} references unknown gate/zone {flight.gate_id}")

        for event in self.events:
            if event.flight_id not in flight_ids:
                raise ValueError(f"event {event.event_id} references unknown flight {event.flight_id}")
            ensure_in_window(event.occurred_at, f"event {event.event_id} occurred_at")
            for gate_id in (event.previous_gate_id, event.new_gate_id):
                if gate_id and gate_id not in zone_ids:
                    raise ValueError(f"event {event.event_id} references unknown gate/zone {gate_id}")

        for task in self.tasks:
            if task.flight_id not in flight_ids:
                raise ValueError(f"task {task.task_id} references unknown flight {task.flight_id}")
            for zone_id in (task.origin_zone_id, task.destination_zone_id):
                if zone_id not in zone_ids:
                    raise ValueError(f"task {task.task_id} references unknown zone {zone_id}")
            ensure_in_window(task.release_at, f"task {task.task_id} release_at")
            ensure_in_window(task.deadline_at, f"task {task.task_id} deadline_at")

        for resource in self.resources:
            for zone_id in (resource.home_zone_id, resource.current_zone_id):
                if zone_id not in zone_ids:
                    raise ValueError(f"resource {resource.resource_id} references unknown zone {zone_id}")
            ensure_in_window(resource.available_from, f"resource {resource.resource_id} available_from")
            ensure_in_window(resource.available_to, f"resource {resource.resource_id} available_to")

        return self
