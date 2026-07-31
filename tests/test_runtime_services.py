from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.api_models import CreatePlanRequest
from backend.app.demo_export import DEMO_SCENARIO_PATH
from backend.app.repository import InMemoryScenarioRepository
from backend.app.runtime_models import (
    CreateRuntimeSessionRequest,
    ResetRuntimeSessionRequest,
    RuntimeRevisionRequest,
    RuntimeStatus,
    SetRuntimeSpeedRequest,
)
from backend.app.runtime_repository import (
    RuntimeRevisionConflictError,
    SQLiteRuntimeSessionRepository,
)
from backend.app.runtime_services import RuntimeInvalidTransitionError, RuntimeSessionService
from backend.app.scenario_loader import load_scenario
from backend.app.services import ScenarioService


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

    def adjust_wall(self, seconds: float) -> None:
        self.wall += timedelta(seconds=seconds)


def _build_service(tmp_path, *, clock: FakeClock | None = None):
    scenario_repository = InMemoryScenarioRepository()
    scenario = load_scenario(DEMO_SCENARIO_PATH)
    scenario_repository.create_scenario(scenario)
    plan = ScenarioService(scenario_repository).create_plan(
        scenario.scenario_id,
        CreatePlanRequest(expected_version=1, algorithm="fifo", max_time_seconds=2),
    ).plan
    fake_clock = clock or FakeClock(
        datetime(2026, 7, 30, 7, 55, tzinfo=timezone(timedelta(hours=8)))
    )
    runtime_repository = SQLiteRuntimeSessionRepository(tmp_path / "runtime.sqlite3")
    service = RuntimeSessionService(
        scenario_repository,
        runtime_repository,
        wall_clock=fake_clock.wall_now,
        monotonic_clock=fake_clock.monotonic_now,
        session_id_factory=lambda: "RUN-SERVICE-001",
    )
    return service, runtime_repository, fake_clock, scenario, plan


def test_authoritative_clock_start_pause_resume_speed_and_reset(tmp_path) -> None:
    service, repository, clock, scenario, plan = _build_service(tmp_path)
    created = service.create_session(
        CreateRuntimeSessionRequest(
            scenario_id=scenario.scenario_id,
            scenario_version=1,
            active_plan_id=plan.plan_id,
            speed=15,
        )
    )
    started = service.start_session(
        created.session_id,
        RuntimeRevisionRequest(expected_revision=1),
    )
    clock.advance(2)
    clock.adjust_wall(-300)
    advancing = service.get_session(created.session_id)
    paused = service.pause_session(
        created.session_id,
        RuntimeRevisionRequest(expected_revision=2),
    )
    clock.advance(30)
    still_paused = service.get_session(created.session_id)
    resumed = service.start_session(
        created.session_id,
        RuntimeRevisionRequest(expected_revision=3),
    )
    clock.advance(2)
    faster = service.set_speed(
        created.session_id,
        SetRuntimeSpeedRequest(expected_revision=4, speed=5),
    )
    clock.advance(2)
    after_speed = service.get_session(created.session_id)
    paused_again = service.pause_session(
        created.session_id,
        RuntimeRevisionRequest(expected_revision=5),
    )
    reset = service.reset_session(
        created.session_id,
        ResetRuntimeSessionRequest(expected_revision=6, confirm_reset=True),
    )

    assert created.status is RuntimeStatus.READY
    assert created.clock.simulation_time == scenario.window_start
    assert started.status is RuntimeStatus.RUNNING
    assert advancing.clock.simulation_time == scenario.window_start + timedelta(seconds=30)
    assert paused.clock.simulation_time == advancing.clock.simulation_time
    assert still_paused.clock.simulation_time == paused.clock.simulation_time
    assert resumed.revision == 4
    assert faster.clock.simulation_time == paused.clock.simulation_time + timedelta(seconds=30)
    assert after_speed.clock.simulation_time == faster.clock.simulation_time + timedelta(seconds=10)
    assert paused_again.revision == 6
    assert reset.status is RuntimeStatus.READY
    assert reset.revision == 7
    assert reset.clock.simulation_time == scenario.window_start
    assert reset.active_plan_id == plan.plan_id
    assert [item.action for item in repository.list_audit_records(created.session_id)] == [
        "session_created",
        "runtime_started",
        "runtime_paused",
        "runtime_started",
        "runtime_speed_changed",
        "runtime_paused",
        "runtime_reset",
    ]


def test_controls_reject_stale_revision_and_invalid_transition(tmp_path) -> None:
    service, _, _, scenario, plan = _build_service(tmp_path)
    created = service.create_session(
        CreateRuntimeSessionRequest(
            scenario_id=scenario.scenario_id,
            scenario_version=1,
            active_plan_id=plan.plan_id,
        )
    )
    service.start_session(
        created.session_id,
        RuntimeRevisionRequest(expected_revision=1),
    )

    with pytest.raises(RuntimeRevisionConflictError) as conflict:
        service.pause_session(
            created.session_id,
            RuntimeRevisionRequest(expected_revision=1),
        )
    assert conflict.value.current_revision == 2

    with pytest.raises(RuntimeInvalidTransitionError):
        service.start_session(
            created.session_id,
            RuntimeRevisionRequest(expected_revision=2),
        )


def test_window_end_is_persisted_once_as_completed_boundary(tmp_path) -> None:
    service, repository, clock, scenario, plan = _build_service(tmp_path)
    created = service.create_session(
        CreateRuntimeSessionRequest(
            scenario_id=scenario.scenario_id,
            scenario_version=1,
            active_plan_id=plan.plan_id,
            speed=15,
        )
    )
    service.start_session(created.session_id, RuntimeRevisionRequest(expected_revision=1))
    elapsed = (scenario.window_end - scenario.window_start).total_seconds() / 15
    clock.advance(elapsed + 1)

    completed = service.get_session(created.session_id)
    again = service.get_session(created.session_id)

    assert completed.status is RuntimeStatus.COMPLETED
    assert completed.revision == 3
    assert completed.clock.simulation_time == scenario.window_end
    assert completed.clock.is_advancing is False
    assert again == completed.model_copy(update={"clock": again.clock})
    assert [item.action for item in repository.list_audit_records(created.session_id)].count(
        "runtime_completed"
    ) == 1


def test_list_status_filter_materializes_elapsed_window_end(tmp_path) -> None:
    service, _, clock, scenario, plan = _build_service(tmp_path)
    created = service.create_session(
        CreateRuntimeSessionRequest(
            scenario_id=scenario.scenario_id,
            scenario_version=1,
            active_plan_id=plan.plan_id,
            speed=15,
        )
    )
    service.start_session(created.session_id, RuntimeRevisionRequest(expected_revision=1))
    elapsed = (scenario.window_end - scenario.window_start).total_seconds() / 15
    clock.advance(elapsed + 1)

    completed = service.list_sessions(
        scenario_id=None,
        status=RuntimeStatus.COMPLETED,
        offset=0,
        limit=20,
    )
    running = service.list_sessions(
        scenario_id=None,
        status=RuntimeStatus.RUNNING,
        offset=0,
        limit=20,
    )

    assert completed.total == 1
    assert completed.items[0].status is RuntimeStatus.COMPLETED
    assert running.total == 0


def test_restart_pauses_at_last_persisted_boundary_and_list_finds_session(tmp_path) -> None:
    path = tmp_path / "runtime.sqlite3"
    service, repository, clock, scenario, plan = _build_service(tmp_path, clock=FakeClock(
        datetime(2026, 7, 30, 7, 55, tzinfo=timezone(timedelta(hours=8)))
    ))
    created = service.create_session(
        CreateRuntimeSessionRequest(
            scenario_id=scenario.scenario_id,
            scenario_version=1,
            active_plan_id=plan.plan_id,
            speed=15,
        )
    )
    service.start_session(created.session_id, RuntimeRevisionRequest(expected_revision=1))
    clock.advance(10)
    assert service.get_session(created.session_id).clock.simulation_time > scenario.window_start
    repository.close()

    reopened_repository = SQLiteRuntimeSessionRepository(path)
    restarted = RuntimeSessionService(
        service.scenario_repository,
        reopened_repository,
        wall_clock=clock.wall_now,
        monotonic_clock=clock.monotonic_now,
    )
    recovered = restarted.get_session(created.session_id)
    listed = restarted.list_sessions(
        scenario_id=scenario.scenario_id,
        status=RuntimeStatus.PAUSED,
        offset=0,
        limit=20,
    )

    assert recovered.status is RuntimeStatus.PAUSED
    assert recovered.revision == 3
    assert recovered.clock.simulation_time == scenario.window_start
    assert listed.total == 1
    assert listed.items[0].session_id == created.session_id
    assert listed.items[0].action_required is True
