"""Thread-safe in-memory storage for versioned simulation scenarios."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock

from .audit_models import AuditAction, AuditRecord
from .models import FlightEvent, Scenario


class RepositoryError(RuntimeError):
    """Base class for repository state and consistency failures."""


class ScenarioAlreadyExistsError(RepositoryError):
    pass


class ScenarioNotFoundError(RepositoryError):
    pass


class ScenarioVersionNotFoundError(RepositoryError):
    pass


class VersionConflictError(RepositoryError):
    def __init__(self, expected_version: int, current_version: int) -> None:
        super().__init__(
            f"expected scenario version {expected_version}, current version is {current_version}"
        )
        self.expected_version = expected_version
        self.current_version = current_version


class EventNotFoundError(RepositoryError):
    pass


class EventAlreadyRegisteredError(RepositoryError):
    pass


class EventAlreadyAppliedError(RepositoryError):
    pass


class InvalidRevisionError(RepositoryError):
    pass


@dataclass(slots=True)
class _ScenarioAggregate:
    baseline: Scenario
    event_catalog: dict[str, FlightEvent]
    applied_event_ids: list[str]
    revisions: dict[int, Scenario]
    audit_records: list[AuditRecord]

    @property
    def current_version(self) -> int:
        return max(self.revisions)


class InMemoryScenarioRepository:
    """Store defensive scenario copies and apply revision commits atomically."""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        self._scenarios: dict[str, _ScenarioAggregate] = {}
        self._audit_sequence = 0

    def create_scenario(self, scenario: Scenario) -> Scenario:
        with self._lock:
            if scenario.scenario_id in self._scenarios:
                raise ScenarioAlreadyExistsError(
                    f"scenario {scenario.scenario_id} is already registered"
                )

            source = scenario.model_copy(deep=True)
            event_catalog = {
                event.event_id: event.model_copy(deep=True) for event in source.events
            }
            baseline_payload = source.model_dump(mode="python")
            baseline_payload["events"] = []
            baseline = Scenario.model_validate(baseline_payload)
            audit_record = self._build_audit_record(
                scenario_id=baseline.scenario_id,
                action=AuditAction.SCENARIO_IMPORTED,
                version_before=None,
                version_after=baseline.version,
                related_entity_ids=[baseline.scenario_id],
                summary="场景已导入",
            )

            self._scenarios[baseline.scenario_id] = _ScenarioAggregate(
                baseline=baseline,
                event_catalog=event_catalog,
                applied_event_ids=[],
                revisions={baseline.version: baseline.model_copy(deep=True)},
                audit_records=[audit_record],
            )
            return baseline.model_copy(deep=True)

    def list_scenario_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._scenarios))

    def list_versions(self, scenario_id: str) -> tuple[int, ...]:
        with self._lock:
            aggregate = self._require_scenario(scenario_id)
            return tuple(sorted(aggregate.revisions))

    def get_scenario(self, scenario_id: str, version: int | None = None) -> Scenario:
        with self._lock:
            aggregate = self._require_scenario(scenario_id)
            target_version = aggregate.current_version if version is None else version
            try:
                scenario = aggregate.revisions[target_version]
            except KeyError as exc:
                raise ScenarioVersionNotFoundError(
                    f"scenario {scenario_id} has no version {target_version}"
                ) from exc
            return scenario.model_copy(deep=True)

    def get_baseline(self, scenario_id: str) -> Scenario:
        with self._lock:
            return self._require_scenario(scenario_id).baseline.model_copy(deep=True)

    def get_events(
        self,
        scenario_id: str,
        event_ids: Sequence[str] | None = None,
    ) -> tuple[FlightEvent, ...]:
        with self._lock:
            aggregate = self._require_scenario(scenario_id)
            requested_ids = tuple(aggregate.event_catalog) if event_ids is None else tuple(event_ids)
            events: list[FlightEvent] = []
            for event_id in requested_ids:
                try:
                    event = aggregate.event_catalog[event_id]
                except KeyError as exc:
                    raise EventNotFoundError(
                        f"event {event_id} is not registered for scenario {scenario_id}"
                    ) from exc
                events.append(event.model_copy(deep=True))
            return tuple(events)

    def get_pending_event_ids(self, scenario_id: str) -> tuple[str, ...]:
        with self._lock:
            aggregate = self._require_scenario(scenario_id)
            applied = set(aggregate.applied_event_ids)
            return tuple(
                event_id for event_id in aggregate.event_catalog if event_id not in applied
            )

    def get_applied_event_ids(self, scenario_id: str) -> tuple[str, ...]:
        with self._lock:
            aggregate = self._require_scenario(scenario_id)
            return tuple(aggregate.applied_event_ids)

    def commit_event_revision(
        self,
        scenario_id: str,
        expected_version: int,
        revision: Scenario,
        event_ids: Sequence[str] = (),
        new_events: Sequence[FlightEvent] = (),
    ) -> Scenario:
        with self._lock:
            aggregate = self._require_scenario(scenario_id)
            if aggregate.current_version != expected_version:
                raise VersionConflictError(expected_version, aggregate.current_version)

            stored_new_events = [event.model_copy(deep=True) for event in new_events]
            registered_ids = tuple(event_ids)
            new_event_ids = tuple(event.event_id for event in stored_new_events)
            batch_event_ids = (*registered_ids, *new_event_ids)
            if not batch_event_ids:
                raise InvalidRevisionError("an event revision must apply at least one event")
            if len(batch_event_ids) != len(set(batch_event_ids)):
                raise InvalidRevisionError("an event batch must not contain duplicate event IDs")

            for event_id in registered_ids:
                if event_id not in aggregate.event_catalog:
                    raise EventNotFoundError(
                        f"event {event_id} is not registered for scenario {scenario_id}"
                    )
            for event_id in new_event_ids:
                if event_id in aggregate.event_catalog:
                    raise EventAlreadyRegisteredError(
                        f"event {event_id} is already registered for scenario {scenario_id}"
                    )
            for event_id in batch_event_ids:
                if event_id in aggregate.applied_event_ids:
                    raise EventAlreadyAppliedError(
                        f"event {event_id} was already applied to scenario {scenario_id}"
                    )

            expected_revision_version = expected_version + 1
            if revision.scenario_id != scenario_id:
                raise InvalidRevisionError("revision scenario ID does not match the aggregate")
            if revision.version != expected_revision_version:
                raise InvalidRevisionError(
                    f"revision version must be {expected_revision_version}"
                )

            candidate_catalog = dict(aggregate.event_catalog)
            candidate_catalog.update(
                {event.event_id: event for event in stored_new_events}
            )
            next_applied_event_ids = [*aggregate.applied_event_ids, *batch_event_ids]
            expected_events = tuple(
                candidate_catalog[event_id] for event_id in next_applied_event_ids
            )
            if tuple(revision.events) != expected_events:
                raise InvalidRevisionError(
                    "revision events must exactly match the applied event sequence"
                )

            stored_revision = revision.model_copy(deep=True)
            audit_record = self._build_audit_record(
                scenario_id=scenario_id,
                action=AuditAction.EVENTS_APPLIED,
                version_before=expected_version,
                version_after=stored_revision.version,
                related_entity_ids=list(batch_event_ids),
                summary="结构化事件已应用",
            )

            aggregate.event_catalog = candidate_catalog
            aggregate.applied_event_ids = next_applied_event_ids
            aggregate.revisions[stored_revision.version] = stored_revision
            aggregate.audit_records.append(audit_record)
            return stored_revision.model_copy(deep=True)

    def list_audit_records(self, scenario_id: str) -> tuple[AuditRecord, ...]:
        with self._lock:
            aggregate = self._require_scenario(scenario_id)
            return tuple(record.model_copy(deep=True) for record in aggregate.audit_records)

    def _require_scenario(self, scenario_id: str) -> _ScenarioAggregate:
        try:
            return self._scenarios[scenario_id]
        except KeyError as exc:
            raise ScenarioNotFoundError(f"scenario {scenario_id} is not registered") from exc

    def _build_audit_record(
        self,
        scenario_id: str,
        action: AuditAction,
        version_before: int | None,
        version_after: int | None,
        related_entity_ids: list[str],
        summary: str,
    ) -> AuditRecord:
        next_sequence = self._audit_sequence + 1
        record = AuditRecord(
            audit_id=f"AUDIT-{next_sequence:06d}",
            scenario_id=scenario_id,
            action=action,
            occurred_at=self._clock(),
            version_before=version_before,
            version_after=version_after,
            related_entity_ids=related_entity_ids,
            summary=summary,
        )
        self._audit_sequence = next_sequence
        return record
