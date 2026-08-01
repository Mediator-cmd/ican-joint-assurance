from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from backend.app.api_models import CreatePlanRequest
from backend.app.demo_export import DEMO_SCENARIO_PATH
from backend.app.events import apply_events
from backend.app.fifo_scheduler import build_fifo_plan
from backend.app.repository import InMemoryScenarioRepository
from backend.app.runtime_models import (
    CreateRuntimeSessionRequest,
    RuntimeRevisionRequest,
    RuntimeStatus,
    SimulationSpeed,
)
from backend.app.runtime_projection import RuntimeProjectionSource, project_runtime_state
from backend.app.runtime_repository import (
    RuntimeSessionRecord,
    SQLiteRuntimeSessionRepository,
)
from backend.app.runtime_services import RuntimeSessionService
from backend.app.scenario_loader import load_scenario
from backend.app.services import ScenarioService


LOCAL_TIMEZONE = timezone(timedelta(hours=8))


def _at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 8, 1, hour, minute, second, tzinfo=LOCAL_TIMEZONE)


def _baseline_source() -> RuntimeProjectionSource:
    imported = load_scenario(DEMO_SCENARIO_PATH)
    baseline = imported.model_copy(deep=True, update={"events": []})
    return RuntimeProjectionSource(
        scenario=baseline,
        plan=build_fifo_plan(baseline),
        event_catalog=imported.events,
    )


def _by_id(items, field_name: str):
    return {getattr(item, field_name): item for item in items}


def test_ready_projection_exposes_plan_without_starting_work() -> None:
    projection = project_runtime_state(
        _baseline_source(),
        _at(8, 0),
        RuntimeStatus.READY,
    )
    tasks = _by_id(projection.tasks, "task_id")
    resources = _by_id(projection.resources, "resource_id")
    flights = _by_id(projection.flights, "flight_id")

    assert {item.status.value for item in projection.tasks} == {"pending", "unassigned"}
    assert tasks["TASK-001"].status.value == "pending"
    assert tasks["TASK-005"].status.value == "unassigned"
    assert {item.status.value for item in projection.resources} == {"idle"}
    assert resources["WC-01"].next_task_id == "TASK-001"
    assert flights["FL-SIM102"].status.value == "delayed"
    assert flights["FL-SIM218"].status.value == "scheduled"
    assert {item.status.value for item in projection.events} == {"pending"}
    assert projection.next_boundary_at == _at(8, 0)


def test_task_and_resource_boundaries_are_left_closed_right_open() -> None:
    source = _baseline_source()

    before = project_runtime_state(source, _at(8, 7, 59), RuntimeStatus.RUNNING)
    at_boundary = project_runtime_state(source, _at(8, 8), RuntimeStatus.RUNNING)
    after = project_runtime_state(source, _at(8, 8, 1), RuntimeStatus.RUNNING)
    service_boundary = project_runtime_state(source, _at(8, 16), RuntimeStatus.RUNNING)
    completed = project_runtime_state(source, _at(8, 23), RuntimeStatus.RUNNING)

    before_tasks = _by_id(before.tasks, "task_id")
    boundary_tasks = _by_id(at_boundary.tasks, "task_id")
    after_resources = _by_id(after.resources, "resource_id")
    service_tasks = _by_id(service_boundary.tasks, "task_id")
    service_resources = _by_id(service_boundary.resources, "resource_id")
    completed_resources = _by_id(completed.resources, "resource_id")

    assert before_tasks["TASK-001"].status.value == "in_service"
    assert before.next_boundary_at == _at(8, 8)
    assert boundary_tasks["TASK-001"].status.value == "completed"
    assert boundary_tasks["TASK-004"].status.value == "en_route"
    assert _by_id(at_boundary.resources, "resource_id")["WC-01"].status.value == "moving"
    assert after_resources["WC-01"].position.progress_pct == 0.21
    assert service_tasks["TASK-004"].status.value == "in_service"
    assert service_resources["WC-01"].status.value == "serving"
    assert service_resources["WC-01"].position.from_zone_id == "TRANSFER-DESK"
    assert service_resources["WC-01"].position.to_zone_id == "GATE-W03"
    assert completed_resources["WC-01"].status.value == "idle"
    assert completed_resources["WC-01"].position.from_zone_id == "GATE-W03"


def test_zero_minute_travel_skips_moving_and_resource_availability_is_visible() -> None:
    source = _baseline_source()
    scenario = source.scenario.model_copy(deep=True)
    agent = next(item for item in scenario.resources if item.resource_id == "AGENT-02")
    agent.available_from = _at(8, 5)
    source = RuntimeProjectionSource(
        scenario=scenario,
        plan=source.plan,
        event_catalog=source.event_catalog,
    )

    at_start = project_runtime_state(source, _at(8, 0), RuntimeStatus.RUNNING)
    resources = _by_id(at_start.resources, "resource_id")
    tasks = _by_id(at_start.tasks, "task_id")

    assert tasks["TASK-001"].status.value == "in_service"
    assert tasks["TASK-002"].status.value == "in_service"
    assert tasks["TASK-003"].status.value == "waiting"
    assert resources["WC-01"].status.value == "serving"
    assert resources["AGENT-01"].status.value == "serving"
    assert resources["BUS-01"].status.value == "waiting"
    assert resources["AGENT-02"].status.value == "unavailable"
    assert resources["AGENT-02"].next_available_at == _at(8, 5)


def test_flights_and_pending_events_change_only_at_their_boundaries() -> None:
    source = _baseline_source()

    at_start = project_runtime_state(source, _at(8, 0), RuntimeStatus.RUNNING)
    at_gate_event = project_runtime_state(source, _at(8, 4), RuntimeStatus.RUNNING)
    boarding = project_runtime_state(source, _at(8, 5), RuntimeStatus.RUNNING)
    departed = project_runtime_state(source, _at(8, 35), RuntimeStatus.RUNNING)

    start_events = _by_id(at_start.events, "event_id")
    gate_events = _by_id(at_gate_event.events, "event_id")
    assert start_events["EVT-SIM102-DELAY"].status.value == "triggered"
    assert start_events["EVT-SIM218-GATE"].status.value == "pending"
    assert gate_events["EVT-SIM218-GATE"].status.value == "triggered"
    assert _by_id(boarding.flights, "flight_id")["FL-SIM102"].status.value == "boarding"
    assert _by_id(departed.flights, "flight_id")["FL-SIM102"].status.value == "departed"
    assert _by_id(at_gate_event.flights, "flight_id")["FL-SIM218"].gate_id == "GATE-W03"


def test_applied_event_uses_version_evidence_and_scenario_effects() -> None:
    imported = load_scenario(DEMO_SCENARIO_PATH)
    event = imported.events[0]
    baseline = imported.model_copy(deep=True, update={"events": []})
    revision_input = baseline.model_copy(deep=True, update={"events": [event]})
    revision = apply_events(revision_input)
    source = RuntimeProjectionSource(
        scenario=revision,
        plan=build_fifo_plan(revision),
        event_catalog=imported.events,
        applied_event_versions={event.event_id: 2},
    )

    projection = project_runtime_state(source, _at(8, 0), RuntimeStatus.READY)
    events = _by_id(projection.events, "event_id")
    flights = _by_id(projection.flights, "flight_id")

    assert events[event.event_id].status.value == "resolved"
    assert events[event.event_id].scenario_version_after == 2
    assert events[event.event_id].applied_at == event.occurred_at
    assert events["EVT-SIM218-GATE"].status.value == "pending"
    assert flights["FL-SIM102"].estimated_departure == _at(9, 0)
    assert flights["FL-SIM102"].last_event_id == event.event_id


def test_repeated_pure_projection_is_identical() -> None:
    source = _baseline_source()
    first = project_runtime_state(source, _at(8, 8, 1), RuntimeStatus.RUNNING)
    second = project_runtime_state(source, _at(8, 8, 1), RuntimeStatus.RUNNING)

    assert second == first


@dataclass
class _FakeClock:
    wall: datetime
    monotonic: float = 100.0

    def wall_now(self) -> datetime:
        return self.wall

    def monotonic_now(self) -> float:
        return self.monotonic

    def advance(self, seconds: float) -> None:
        self.wall += timedelta(seconds=seconds)
        self.monotonic += seconds


def _service_with_plan(tmp_path):
    scenario_repository = InMemoryScenarioRepository()
    imported = load_scenario(DEMO_SCENARIO_PATH)
    baseline = scenario_repository.create_scenario(imported)
    plan = ScenarioService(scenario_repository).create_plan(
        baseline.scenario_id,
        CreatePlanRequest(expected_version=1, algorithm="fifo", max_time_seconds=2),
    ).plan
    clock = _FakeClock(_at(7, 55))
    runtime_repository = SQLiteRuntimeSessionRepository(tmp_path / "runtime.sqlite3")
    service = RuntimeSessionService(
        scenario_repository,
        runtime_repository,
        wall_clock=clock.wall_now,
        monotonic_clock=clock.monotonic_now,
        session_id_factory=lambda: "RUN-PROJECTION-001",
    )
    return service, runtime_repository, clock, baseline, plan


def test_projection_reads_do_not_change_revision_or_control_audit(tmp_path) -> None:
    service, repository, clock, scenario, plan = _service_with_plan(tmp_path)
    created = service.create_session(
        CreateRuntimeSessionRequest(
            scenario_id=scenario.scenario_id,
            scenario_version=1,
            active_plan_id=plan.plan_id,
            speed=15,
        )
    )
    service.start_session(
        created.session_id,
        RuntimeRevisionRequest(expected_revision=1),
    )
    clock.advance(1)

    first = service.get_session(created.session_id)
    second = service.get_session(created.session_id)
    stored = repository.get_session(created.session_id)

    assert first.tasks == second.tasks
    assert first.resources == second.resources
    assert first.flights == second.flights
    assert first.events == second.events
    assert first.revision == second.revision == stored.revision == 4
    assert first.status is RuntimeStatus.AWAITING_CONFIRMATION
    assert len(repository.list_audit_records(created.session_id)) == 4


def test_session_source_captures_applied_event_version_from_audit(tmp_path) -> None:
    scenario_repository = InMemoryScenarioRepository()
    imported = load_scenario(DEMO_SCENARIO_PATH)
    baseline = scenario_repository.create_scenario(imported)
    event = imported.events[0]
    revision_input = baseline.model_copy(deep=True, update={"events": [event]})
    revision = apply_events(revision_input)
    scenario_repository.commit_event_revision(
        baseline.scenario_id,
        expected_version=1,
        revision=revision,
        event_ids=[event.event_id],
    )
    plan = ScenarioService(scenario_repository).create_plan(
        baseline.scenario_id,
        CreatePlanRequest(expected_version=2, algorithm="fifo", max_time_seconds=2),
    ).plan
    repository = SQLiteRuntimeSessionRepository(tmp_path / "runtime.sqlite3")
    service = RuntimeSessionService(
        scenario_repository,
        repository,
        wall_clock=lambda: _at(7, 55),
        session_id_factory=lambda: "RUN-APPLIED-EVENT",
    )

    snapshot = service.create_session(
        CreateRuntimeSessionRequest(
            scenario_id=baseline.scenario_id,
            scenario_version=2,
            active_plan_id=plan.plan_id,
        )
    )
    events = _by_id(snapshot.events, "event_id")
    stored = repository.get_session(snapshot.session_id)

    assert events[event.event_id].status.value == "resolved"
    assert events[event.event_id].scenario_version_after == 2
    assert stored.projection_source is not None
    assert stored.projection_source.applied_event_versions == {event.event_id: 2}


def test_projection_survives_restart_with_empty_scenario_repository(tmp_path) -> None:
    service, repository, _, scenario, plan = _service_with_plan(tmp_path)
    created = service.create_session(
        CreateRuntimeSessionRequest(
            scenario_id=scenario.scenario_id,
            scenario_version=1,
            active_plan_id=plan.plan_id,
        )
    )
    repository.close()

    reopened = SQLiteRuntimeSessionRepository(tmp_path / "runtime.sqlite3")
    restarted = RuntimeSessionService(
        InMemoryScenarioRepository(),
        reopened,
        wall_clock=lambda: _at(7, 56),
        recover_on_startup=False,
    )
    recovered = restarted.get_session(created.session_id)

    assert recovered.revision == 1
    assert recovered.tasks == created.tasks
    assert recovered.resources == created.resources
    assert recovered.flights == created.flights
    assert recovered.events == created.events


def test_legacy_session_hydrates_without_revision_or_audit_write(tmp_path) -> None:
    service, repository, _, scenario, plan = _service_with_plan(tmp_path)
    legacy = RuntimeSessionRecord(
        session_id="RUN-LEGACY-001",
        scenario_id=scenario.scenario_id,
        initial_scenario_version=1,
        current_scenario_version=1,
        initial_plan_id=plan.plan_id,
        active_plan_id=plan.plan_id,
        candidate_plan_id=None,
        status=RuntimeStatus.READY,
        revision=1,
        simulation_time=scenario.window_start,
        simulation_window_start=scenario.window_start,
        simulation_window_end=scenario.window_end,
        speed=SimulationSpeed.REAL_TIME,
        failure=None,
        created_at=_at(7, 55),
        updated_at=_at(7, 55),
    )
    repository.create_session(legacy)

    snapshot = service.get_session(legacy.session_id)
    stored = repository.get_session(legacy.session_id)

    assert len(snapshot.tasks) == len(scenario.tasks)
    assert stored.projection_source is not None
    assert stored.revision == 1
    assert [item.action for item in repository.list_audit_records(legacy.session_id)] == [
        "session_created"
    ]


def test_unrecoverable_legacy_session_keeps_empty_projection(tmp_path) -> None:
    repository = SQLiteRuntimeSessionRepository(tmp_path / "runtime.sqlite3")
    source = _baseline_source()
    legacy = RuntimeSessionRecord(
        session_id="RUN-LEGACY-MISSING",
        scenario_id=source.scenario.scenario_id,
        initial_scenario_version=1,
        current_scenario_version=1,
        initial_plan_id=source.plan.plan_id,
        active_plan_id=source.plan.plan_id,
        candidate_plan_id=None,
        status=RuntimeStatus.READY,
        revision=1,
        simulation_time=source.scenario.window_start,
        simulation_window_start=source.scenario.window_start,
        simulation_window_end=source.scenario.window_end,
        speed=SimulationSpeed.REAL_TIME,
        failure=None,
        created_at=_at(7, 55),
        updated_at=_at(7, 55),
    )
    repository.create_session(legacy)
    service = RuntimeSessionService(
        InMemoryScenarioRepository(),
        repository,
        wall_clock=lambda: _at(7, 56),
        recover_on_startup=False,
    )

    snapshot = service.get_session(legacy.session_id)

    assert snapshot.tasks == []
    assert snapshot.resources == []
    assert snapshot.flights == []
    assert snapshot.events == []
    assert snapshot.revision == 1
    assert len(repository.list_audit_records(legacy.session_id)) == 1


def test_repository_migrates_m41_database_without_projection_column(tmp_path) -> None:
    path = tmp_path / "runtime.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE runtime_sessions (
            session_id TEXT PRIMARY KEY,
            scenario_id TEXT NOT NULL,
            initial_scenario_version INTEGER NOT NULL,
            current_scenario_version INTEGER NOT NULL,
            initial_plan_id TEXT NOT NULL,
            active_plan_id TEXT NOT NULL,
            candidate_plan_id TEXT,
            status TEXT NOT NULL,
            revision INTEGER NOT NULL,
            simulation_time TEXT NOT NULL,
            simulation_window_start TEXT NOT NULL,
            simulation_window_end TEXT NOT NULL,
            speed INTEGER NOT NULL,
            failure_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.commit()
    connection.close()

    repository = SQLiteRuntimeSessionRepository(path)
    source = _baseline_source()
    record = RuntimeSessionRecord(
        session_id="RUN-MIGRATED-M41",
        scenario_id=source.scenario.scenario_id,
        initial_scenario_version=1,
        current_scenario_version=1,
        initial_plan_id=source.plan.plan_id,
        active_plan_id=source.plan.plan_id,
        candidate_plan_id=None,
        status=RuntimeStatus.READY,
        revision=1,
        simulation_time=source.scenario.window_start,
        simulation_window_start=source.scenario.window_start,
        simulation_window_end=source.scenario.window_end,
        speed=SimulationSpeed.REAL_TIME,
        failure=None,
        created_at=_at(7, 55),
        updated_at=_at(7, 55),
    )

    repository.create_session(record)

    assert repository.get_session(record.session_id) == record
