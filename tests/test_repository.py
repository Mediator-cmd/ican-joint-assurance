from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.audit_models import AuditAction, AuditRecord
from backend.app.events import apply_events
from backend.app.fifo_scheduler import build_fifo_plan
from backend.app.models import FlightEvent, FlightEventType, Scenario
from backend.app.repository import (
    EventAlreadyAppliedError,
    InMemoryScenarioRepository,
    InvalidRevisionError,
    PlanAlreadyExistsError,
    PlanNotFoundError,
    ScenarioAlreadyExistsError,
    VersionConflictError,
)
from backend.app.scenario_loader import load_scenario


SCENARIO_PATH = Path(__file__).parents[1] / "data" / "scenarios" / "terminal-disturbance-demo.json"


def load_demo() -> Scenario:
    return load_scenario(SCENARIO_PATH)


def build_revision(
    repository: InMemoryScenarioRepository,
    event_ids: tuple[str, ...],
    version: int,
) -> Scenario:
    scenario_id = "SCN-TERMINAL-DISTURBANCE-01"
    baseline = repository.get_baseline(scenario_id)
    baseline.events = list(repository.get_events(scenario_id, event_ids))
    revision = apply_events(baseline)
    revision.version = version
    return revision


def test_create_scenario_separates_pending_events_from_immutable_baseline() -> None:
    scenario = load_demo()
    repository = InMemoryScenarioRepository()

    created = repository.create_scenario(scenario)
    scenario.name = "外部修改"
    created.name = "返回值修改"

    baseline = repository.get_baseline("SCN-TERMINAL-DISTURBANCE-01")
    assert baseline.name == "东区登机口扰动演示场景"
    assert baseline.events == []
    assert repository.get_scenario(baseline.scenario_id).events == []
    assert repository.get_pending_event_ids(baseline.scenario_id) == (
        "EVT-SIM102-DELAY",
        "EVT-SIM218-GATE",
    )
    assert repository.get_applied_event_ids(baseline.scenario_id) == ()


def test_duplicate_scenario_does_not_create_another_audit_record() -> None:
    scenario = load_demo()
    repository = InMemoryScenarioRepository()
    repository.create_scenario(scenario)

    with pytest.raises(ScenarioAlreadyExistsError):
        repository.create_scenario(scenario)

    assert len(repository.list_audit_records(scenario.scenario_id)) == 1
    assert repository.list_versions(scenario.scenario_id) == (1,)


def test_event_revision_preserves_history_and_updates_event_state() -> None:
    repository = InMemoryScenarioRepository()
    baseline = repository.create_scenario(load_demo())
    event_id = "EVT-SIM102-DELAY"
    revision = build_revision(repository, (event_id,), version=2)

    committed = repository.commit_event_revision(
        baseline.scenario_id,
        expected_version=1,
        revision=revision,
        event_ids=(event_id,),
    )
    committed.name = "外部修改"
    revision.name = "原修订修改"

    original = repository.get_scenario(baseline.scenario_id, version=1)
    current = repository.get_scenario(baseline.scenario_id)
    assert current.name == "东区登机口扰动演示场景"
    assert original.version == 1
    assert current.version == 2
    assert original.flights[0].scheduled_departure.hour == 8
    assert current.flights[0].scheduled_departure.hour == 9
    assert repository.list_versions(baseline.scenario_id) == (1, 2)
    assert repository.get_applied_event_ids(baseline.scenario_id) == (event_id,)
    assert repository.get_pending_event_ids(baseline.scenario_id) == (
        "EVT-SIM218-GATE",
    )


def test_new_structured_event_is_registered_and_applied_atomically() -> None:
    repository = InMemoryScenarioRepository()
    baseline = repository.create_scenario(load_demo())
    new_event = FlightEvent(
        event_id="EVT-SIM102-EXTRA-DELAY",
        event_type=FlightEventType.DELAY,
        flight_id="FL-SIM102",
        occurred_at="2026-08-01T08:02:00+08:00",
        delay_minutes=10,
        note="新增结构化测试事件",
    )
    revision_input = repository.get_baseline(baseline.scenario_id)
    revision_input.events = [new_event]
    revision = apply_events(revision_input)

    repository.commit_event_revision(
        baseline.scenario_id,
        expected_version=1,
        revision=revision,
        new_events=(new_event,),
    )

    assert repository.get_events(baseline.scenario_id, (new_event.event_id,)) == (
        new_event,
    )
    assert repository.get_applied_event_ids(baseline.scenario_id) == (
        new_event.event_id,
    )
    assert repository.get_pending_event_ids(baseline.scenario_id) == (
        "EVT-SIM102-DELAY",
        "EVT-SIM218-GATE",
    )


def test_version_conflict_is_atomic() -> None:
    repository = InMemoryScenarioRepository()
    baseline = repository.create_scenario(load_demo())
    revision = build_revision(repository, ("EVT-SIM102-DELAY",), version=2)
    audit_before = repository.list_audit_records(baseline.scenario_id)

    with pytest.raises(VersionConflictError) as captured:
        repository.commit_event_revision(
            baseline.scenario_id,
            expected_version=2,
            revision=revision,
            event_ids=("EVT-SIM102-DELAY",),
        )

    assert captured.value.expected_version == 2
    assert captured.value.current_version == 1
    assert repository.list_versions(baseline.scenario_id) == (1,)
    assert repository.get_applied_event_ids(baseline.scenario_id) == ()
    assert repository.list_audit_records(baseline.scenario_id) == audit_before


def test_event_cannot_be_applied_twice() -> None:
    repository = InMemoryScenarioRepository()
    baseline = repository.create_scenario(load_demo())
    event_id = "EVT-SIM102-DELAY"
    first_revision = build_revision(repository, (event_id,), version=2)
    repository.commit_event_revision(
        baseline.scenario_id,
        expected_version=1,
        revision=first_revision,
        event_ids=(event_id,),
    )
    repeated_revision = first_revision.model_copy(deep=True)
    repeated_revision.version = 3

    with pytest.raises(EventAlreadyAppliedError):
        repository.commit_event_revision(
            baseline.scenario_id,
            expected_version=2,
            revision=repeated_revision,
            event_ids=(event_id,),
        )

    assert repository.list_versions(baseline.scenario_id) == (1, 2)
    assert len(repository.list_audit_records(baseline.scenario_id)) == 2


def test_revision_event_sequence_mismatch_is_atomic() -> None:
    repository = InMemoryScenarioRepository()
    baseline = repository.create_scenario(load_demo())
    revision = build_revision(repository, ("EVT-SIM102-DELAY",), version=2)
    revision.events = []

    with pytest.raises(InvalidRevisionError, match="event sequence"):
        repository.commit_event_revision(
            baseline.scenario_id,
            expected_version=1,
            revision=revision,
            event_ids=("EVT-SIM102-DELAY",),
        )

    assert repository.list_versions(baseline.scenario_id) == (1,)
    assert repository.get_applied_event_ids(baseline.scenario_id) == ()
    assert len(repository.list_audit_records(baseline.scenario_id)) == 1


def test_audit_records_are_timezone_aware_and_stably_ordered() -> None:
    fixed_time = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
    repository = InMemoryScenarioRepository(clock=lambda: fixed_time)
    baseline = repository.create_scenario(load_demo())
    event_id = "EVT-SIM102-DELAY"
    revision = build_revision(repository, (event_id,), version=2)
    repository.commit_event_revision(
        baseline.scenario_id,
        expected_version=1,
        revision=revision,
        event_ids=(event_id,),
    )

    records = repository.list_audit_records(baseline.scenario_id)
    assert [record.audit_id for record in records] == ["AUDIT-000001", "AUDIT-000002"]
    assert [record.action for record in records] == [
        AuditAction.SCENARIO_IMPORTED,
        AuditAction.EVENTS_APPLIED,
    ]
    assert [record.version_after for record in records] == [1, 2]
    assert all(record.occurred_at == fixed_time for record in records)


def test_audit_record_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError, match="timezone offset"):
        AuditRecord(
            audit_id="AUDIT-000001",
            scenario_id="SCN-TEST",
            action=AuditAction.SCENARIO_IMPORTED,
            occurred_at=datetime(2026, 7, 28, 9, 0),
            version_after=1,
            related_entity_ids=["SCN-TEST"],
            summary="场景已导入",
        )


def test_concurrent_commits_allow_only_one_revision() -> None:
    repository = InMemoryScenarioRepository()
    baseline = repository.create_scenario(load_demo())
    event_id = "EVT-SIM102-DELAY"
    revision = build_revision(repository, (event_id,), version=2)

    def commit_once() -> str:
        try:
            repository.commit_event_revision(
                baseline.scenario_id,
                expected_version=1,
                revision=revision,
                event_ids=(event_id,),
            )
        except VersionConflictError:
            return "conflict"
        return "committed"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: commit_once(), range(2)))

    assert sorted(results) == ["committed", "conflict"]
    assert repository.list_versions(baseline.scenario_id) == (1, 2)
    assert repository.get_applied_event_ids(baseline.scenario_id) == (event_id,)
    assert len(repository.list_audit_records(baseline.scenario_id)) == 2


def test_saved_plans_are_immutable_queryable_and_audited() -> None:
    repository = InMemoryScenarioRepository()
    scenario = repository.create_scenario(load_demo())
    plan = build_fifo_plan(scenario)

    stored = repository.save_plan(scenario.scenario_id, expected_version=1, plan=plan)
    stored.metrics.assigned_tasks = 0
    plan.metrics.assigned_tasks = 0

    fetched = repository.get_plan("PLAN-TERMINAL-DISTURBANCE-01-V1-FIFO")
    listed = repository.list_plans(scenario.scenario_id, scenario_version=1)
    assert fetched.metrics.assigned_tasks == 4
    assert len(listed) == 1
    assert listed[0] == fetched
    assert [record.action for record in repository.list_audit_records(scenario.scenario_id)] == [
        AuditAction.SCENARIO_IMPORTED,
        AuditAction.PLAN_CREATED,
    ]

    with pytest.raises(PlanAlreadyExistsError):
        repository.save_plan(scenario.scenario_id, expected_version=1, plan=fetched)
    with pytest.raises(PlanNotFoundError):
        repository.get_plan("PLAN-MISSING")
    assert len(repository.list_audit_records(scenario.scenario_id)) == 2


def test_stale_plan_save_is_atomic() -> None:
    repository = InMemoryScenarioRepository()
    scenario = repository.create_scenario(load_demo())
    plan = build_fifo_plan(scenario)

    with pytest.raises(VersionConflictError):
        repository.save_plan(scenario.scenario_id, expected_version=2, plan=plan)

    assert repository.list_plans(scenario.scenario_id) == ()
    assert len(repository.list_audit_records(scenario.scenario_id)) == 1
