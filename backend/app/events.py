"""Deterministic application of structured disruption events."""

from __future__ import annotations

from datetime import datetime, timedelta

from .models import FlightEventType, FlightStatus, Scenario


class EventApplicationError(ValueError):
    """Raised when an event conflicts with the baseline scenario state."""


def apply_events(scenario: Scenario, up_to: datetime | None = None) -> Scenario:
    """Return a validated scenario copy with events applied in timestamp order."""

    updated = scenario.model_copy(deep=True)
    flights = {flight.flight_id: flight for flight in updated.flights}

    for event in sorted(updated.events, key=lambda item: (item.occurred_at, item.event_id)):
        if up_to is not None and event.occurred_at > up_to:
            continue

        flight = flights[event.flight_id]
        if event.event_type is FlightEventType.DELAY:
            delay = timedelta(minutes=event.delay_minutes or 0)
            flight.scheduled_departure += delay
            flight.boarding_starts_at += delay
            flight.status = FlightStatus.DELAYED
            for task in updated.tasks:
                if task.flight_id == flight.flight_id and not task.locked:
                    task.deadline_at += delay

        elif event.event_type is FlightEventType.GATE_CHANGE:
            if flight.gate_id != event.previous_gate_id:
                raise EventApplicationError(
                    f"event {event.event_id} expected {event.previous_gate_id}, found {flight.gate_id}"
                )
            flight.gate_id = event.new_gate_id or flight.gate_id
            for task in updated.tasks:
                if (
                    task.flight_id == flight.flight_id
                    and task.destination_zone_id == event.previous_gate_id
                    and not task.locked
                ):
                    task.destination_zone_id = flight.gate_id

    updated.version += 1
    return Scenario.model_validate(updated.model_dump())
