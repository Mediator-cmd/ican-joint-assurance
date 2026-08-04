from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.api_models import CreatePlanRequest
from backend.app.constraints import validate_plan
from backend.app.demo_export import DEMO_SCENARIO_PATH
from backend.app.optimizer import OptimizationError
from backend.app.planning_models import Plan
from backend.app.repository import InMemoryScenarioRepository
from backend.app.runtime_models import (
    CandidateDecisionRequest,
    CreateRuntimeSessionRequest,
    ReplanRuntimeSessionRequest,
    ResetRuntimeSessionRequest,
    RuntimeRevisionRequest,
    RuntimeStatus,
)
from backend.app.runtime_repository import (
    RuntimeRevisionConflictError,
    SQLiteRuntimeSessionRepository,
)
from backend.app.runtime_services import (
    RuntimeCandidateMismatchError,
    RuntimeSessionService,
)
from backend.app.scenario_loader import load_scenario
from backend.app.services import ScenarioService


LOCAL_TIMEZONE = timezone(timedelta(hours=8))


def _at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 8, 1, hour, minute, second, tzinfo=LOCAL_TIMEZONE)


@dataclass
class FakeClock:
    wall: datetime = _at(7, 55)
    monotonic: float = 100.0

    def wall_now(self) -> datetime:
        return self.wall

    def monotonic_now(self) -> float:
        return self.monotonic

    def advance(self, seconds: float) -> None:
        self.wall += timedelta(seconds=seconds)
        self.monotonic += seconds


def _service(
    tmp_path,
    *,
    session_id: str = "RUN-M43-001",
    imported=None,
    rolling_planner=None,
):
    scenario_repository = InMemoryScenarioRepository()
    source = imported or load_scenario(DEMO_SCENARIO_PATH)
    baseline = scenario_repository.create_scenario(source)
    plan = ScenarioService(scenario_repository).create_plan(
        baseline.scenario_id,
        CreatePlanRequest(expected_version=1, algorithm="fifo", max_time_seconds=2),
    ).plan
    runtime_repository = SQLiteRuntimeSessionRepository(tmp_path / "runtime.sqlite3")
    clock = FakeClock()
    service = RuntimeSessionService(
        scenario_repository,
        runtime_repository,
        wall_clock=clock.wall_now,
        monotonic_clock=clock.monotonic_now,
        session_id_factory=lambda: session_id,
        rolling_planner=rolling_planner,
    )
    return service, runtime_repository, clock, baseline, plan


def _create(service, scenario, plan):
    return service.create_session(
        CreateRuntimeSessionRequest(
            scenario_id=scenario.scenario_id,
            scenario_version=scenario.version,
            active_plan_id=plan.plan_id,
            speed=15,
        )
    )


def _assignment_map(plan):
    return {assignment.task_id: assignment for assignment in plan.assignments}


def test_event_boundary_freezes_execution_and_creates_candidate(tmp_path) -> None:
    service, repository, _, scenario, initial_plan = _service(tmp_path)
    created = _create(service, scenario, initial_plan)

    awaiting = service.start_session(
        created.session_id,
        RuntimeRevisionRequest(expected_revision=1),
    )
    stored = repository.get_session(created.session_id)
    source = stored.projection_source
    assert source is not None
    assert source.candidate_plan is not None

    assert awaiting.status is RuntimeStatus.AWAITING_CONFIRMATION
    assert awaiting.revision == 4
    assert awaiting.clock.simulation_time == _at(8, 0)
    assert awaiting.current_scenario_version == 2
    assert awaiting.active_plan_id == initial_plan.plan_id
    assert awaiting.candidate_plan_id == source.candidate_plan.plan_id
    assert source.plan.plan_id == initial_plan.plan_id
    assert set(source.frozen_task_ids) == {"TASK-001", "TASK-002", "TASK-003"}
    assert source.applied_event_versions == {"EVT-SIM102-DELAY": 2}

    initial_assignments = _assignment_map(initial_plan)
    candidate_assignments = _assignment_map(source.candidate_plan)
    for task_id in source.frozen_task_ids:
        assert candidate_assignments[task_id] == initial_assignments[task_id]
    assert source.candidate_plan.violations == []
    assert [event.status.value for event in awaiting.events] == [
        "awaiting_confirmation",
        "pending",
        "pending",
        "pending",
        "pending",
        "pending",
    ]
    assert [audit.action for audit in repository.list_audit_records(created.session_id)] == [
        "session_created",
        "runtime_started",
        "event_batch_applied",
        "candidate_created",
    ]


def test_finite_event_sequence_creates_six_unique_candidates_then_completes(tmp_path) -> None:
    service, repository, clock, scenario, initial_plan = _service(
        tmp_path,
        session_id="RUN-M45A-SEQUENCE",
    )
    created = _create(service, scenario, initial_plan)
    event_minutes = [0, 4, 16, 28, 40, 52]
    candidate_ids: list[str] = []

    awaiting = service.start_session(
        created.session_id,
        RuntimeRevisionRequest(expected_revision=created.revision),
    )
    for index, minute in enumerate(event_minutes):
        assert awaiting.status is RuntimeStatus.AWAITING_CONFIRMATION
        assert awaiting.clock.simulation_time == _at(8, minute)
        assert awaiting.current_scenario_version == index + 2
        assert awaiting.candidate_plan_id is not None
        assert awaiting.candidate_plan_detail is not None
        assert awaiting.candidate_plan_detail.violations == []
        candidate_ids.append(awaiting.candidate_plan_id)

        accepted = service.accept_candidate(
            created.session_id,
            CandidateDecisionRequest(
                expected_revision=awaiting.revision,
                candidate_plan_id=awaiting.candidate_plan_id,
            ),
        )
        if index == len(event_minutes) - 1:
            break
        running = service.start_session(
            created.session_id,
            RuntimeRevisionRequest(expected_revision=accepted.revision),
        )
        next_minute = event_minutes[index + 1]
        clock.advance((next_minute - minute) * 60 / 15)
        awaiting = service.get_session(running.session_id)

    assert len(set(candidate_ids)) == len(event_minutes)
    running = service.start_session(
        created.session_id,
        RuntimeRevisionRequest(expected_revision=accepted.revision),
    )
    clock.advance((90 - event_minutes[-1]) * 60 / 15)
    completed = service.get_session(running.session_id)
    source = repository.get_session(created.session_id).projection_source

    assert completed.status is RuntimeStatus.COMPLETED
    assert completed.clock.simulation_time == _at(9, 30)
    assert source is not None
    assert len(source.applied_event_versions) == 6
    assert set(source.resolved_event_ids) == set(source.applied_event_versions)


def test_accept_then_reset_restores_initial_runtime_facts(tmp_path) -> None:
    service, repository, _, scenario, initial_plan = _service(tmp_path)
    created = _create(service, scenario, initial_plan)
    awaiting = service.start_session(
        created.session_id,
        RuntimeRevisionRequest(expected_revision=1),
    )
    candidate_id = awaiting.candidate_plan_id
    assert candidate_id is not None

    accepted = service.accept_candidate(
        created.session_id,
        CandidateDecisionRequest(
            expected_revision=awaiting.revision,
            candidate_plan_id=candidate_id,
            reason="确认教学演示候选",
        ),
    )
    reset = service.reset_session(
        created.session_id,
        ResetRuntimeSessionRequest(
            expected_revision=accepted.revision,
            confirm_reset=True,
        ),
    )

    assert accepted.status is RuntimeStatus.PAUSED
    assert accepted.active_plan_id == candidate_id
    assert accepted.candidate_plan_id is None
    assert accepted.events[0].status.value == "resolved"
    assert reset.status is RuntimeStatus.READY
    assert reset.current_scenario_version == scenario.version
    assert reset.active_plan_id == initial_plan.plan_id
    assert reset.clock.simulation_time == scenario.window_start
    assert {event.status.value for event in reset.events} == {"pending"}
    restored = repository.get_session(created.session_id).projection_source
    assert restored is not None
    assert restored.plan == initial_plan
    assert restored.candidate_plan is None
    assert restored.frozen_task_ids == []
    assert restored.applied_event_versions == {}


def test_reject_keeps_older_active_plan_with_newer_event_scenario(tmp_path) -> None:
    service, repository, clock, scenario, initial_plan = _service(tmp_path)
    created = _create(service, scenario, initial_plan)
    first = service.start_session(
        created.session_id,
        RuntimeRevisionRequest(expected_revision=1),
    )
    accepted = service.accept_candidate(
        created.session_id,
        CandidateDecisionRequest(
            expected_revision=first.revision,
            candidate_plan_id=first.candidate_plan_id or "",
        ),
    )
    running = service.start_session(
        created.session_id,
        RuntimeRevisionRequest(expected_revision=accepted.revision),
    )
    clock.advance(16)
    second = service.get_session(created.session_id)
    assert running.status is RuntimeStatus.RUNNING
    assert second.status is RuntimeStatus.AWAITING_CONFIRMATION
    assert second.clock.simulation_time == _at(8, 4)
    assert second.current_scenario_version == 3
    active_before_reject = second.active_plan_id

    rejected = service.reject_candidate(
        created.session_id,
        CandidateDecisionRequest(
            expected_revision=second.revision,
            candidate_plan_id=second.candidate_plan_id or "",
            reason="保留原执行安排",
        ),
    )
    source = repository.get_session(created.session_id).projection_source
    assert source is not None

    assert rejected.status is RuntimeStatus.PAUSED
    assert rejected.current_scenario_version == 3
    assert rejected.active_plan_id == active_before_reject
    assert rejected.candidate_plan_id is None
    assert source.scenario.version == 3
    assert source.plan.scenario_version == 2
    assert source.candidate_plan is None
    assert {event.status.value for event in rejected.events} == {"resolved", "pending"}


def test_reject_then_later_event_still_creates_safe_candidate(tmp_path) -> None:
    service, repository, clock, scenario, initial_plan = _service(
        tmp_path,
        session_id="RUN-M46-REJECT-CONTINUE",
    )
    created = _create(service, scenario, initial_plan)
    first = service.start_session(
        created.session_id,
        RuntimeRevisionRequest(expected_revision=created.revision),
    )
    first_accepted = service.accept_candidate(
        created.session_id,
        CandidateDecisionRequest(
            expected_revision=first.revision,
            candidate_plan_id=first.candidate_plan_id or "",
        ),
    )
    service.start_session(
        created.session_id,
        RuntimeRevisionRequest(expected_revision=first_accepted.revision),
    )
    clock.advance(16)
    second = service.get_session(created.session_id)
    second_rejected = service.reject_candidate(
        created.session_id,
        CandidateDecisionRequest(
            expected_revision=second.revision,
            candidate_plan_id=second.candidate_plan_id or "",
            reason="保留当前执行方案后继续运行",
        ),
    )

    service.start_session(
        created.session_id,
        RuntimeRevisionRequest(expected_revision=second_rejected.revision),
    )
    clock.advance(48)
    third = service.get_session(created.session_id)
    source = repository.get_session(created.session_id).projection_source
    assert source is not None
    assert source.candidate_plan is not None

    assert third.status is RuntimeStatus.AWAITING_CONFIRMATION
    assert third.clock.simulation_time == _at(8, 16)
    assert third.current_scenario_version == 4
    assert third.candidate_plan_id == source.candidate_plan.plan_id
    assert source.failed_event_codes == {}

    active_assignments = _assignment_map(source.plan)
    candidate_assignments = _assignment_map(source.candidate_plan)
    scenario_tasks = {task.task_id: task for task in source.scenario.tasks}
    for task_id in source.frozen_task_ids:
        assert candidate_assignments[task_id] == active_assignments[task_id]
        assert scenario_tasks[task_id].origin_zone_id == active_assignments[task_id].origin_zone_id
        assert (
            scenario_tasks[task_id].destination_zone_id
            == active_assignments[task_id].destination_zone_id
        )
    assert validate_plan(
        source.scenario,
        source.candidate_plan.assignments,
        source.candidate_plan.unassigned_tasks,
    ) == []
    assert repository.list_audit_records(created.session_id)[-1].action == "candidate_created"


def test_same_time_events_share_one_scenario_version_and_replan(tmp_path) -> None:
    imported = load_scenario(DEMO_SCENARIO_PATH)
    imported.events[1].occurred_at = imported.events[0].occurred_at
    service, repository, _, scenario, plan = _service(
        tmp_path,
        session_id="RUN-M43-BATCH",
        imported=imported,
    )
    created = _create(service, scenario, plan)

    awaiting = service.start_session(
        created.session_id,
        RuntimeRevisionRequest(expected_revision=1),
    )
    source = repository.get_session(created.session_id).projection_source
    assert source is not None

    assert awaiting.current_scenario_version == 2
    assert awaiting.revision == 4
    assert source.applied_event_versions == {
        "EVT-SIM102-DELAY": 2,
        "EVT-SIM218-GATE": 2,
    }
    actions = [audit.action for audit in repository.list_audit_records(created.session_id)]
    assert actions.count("event_batch_applied") == 1
    assert actions.count("candidate_created") == 1


def test_manual_replan_generates_unique_candidates_on_same_scenario_version(tmp_path) -> None:
    service, _, _, scenario, plan = _service(tmp_path)
    created = _create(service, scenario, plan)
    awaiting = service.start_session(
        created.session_id,
        RuntimeRevisionRequest(expected_revision=1),
    )
    accepted = service.accept_candidate(
        created.session_id,
        CandidateDecisionRequest(
            expected_revision=awaiting.revision,
            candidate_plan_id=awaiting.candidate_plan_id or "",
        ),
    )
    first_manual = service.replan_session(
        created.session_id,
        ReplanRuntimeSessionRequest(
            expected_revision=accepted.revision,
            reason="复核资源安排",
        ),
    )
    rejected = service.reject_candidate(
        created.session_id,
        CandidateDecisionRequest(
            expected_revision=first_manual.revision,
            candidate_plan_id=first_manual.candidate_plan_id or "",
        ),
    )
    second_manual = service.replan_session(
        created.session_id,
        ReplanRuntimeSessionRequest(expected_revision=rejected.revision),
    )

    assert first_manual.status is RuntimeStatus.AWAITING_CONFIRMATION
    assert second_manual.status is RuntimeStatus.AWAITING_CONFIRMATION
    assert first_manual.current_scenario_version == second_manual.current_scenario_version == 2
    assert first_manual.candidate_plan_id != second_manual.candidate_plan_id
    assert f"R{accepted.revision + 1}" in (first_manual.candidate_plan_id or "")
    assert f"R{rejected.revision + 1}" in (second_manual.candidate_plan_id or "")


def test_candidate_mismatch_and_stale_revision_leave_state_unchanged(tmp_path) -> None:
    service, repository, _, scenario, plan = _service(tmp_path)
    created = _create(service, scenario, plan)
    awaiting = service.start_session(
        created.session_id,
        RuntimeRevisionRequest(expected_revision=1),
    )
    audit_count = len(repository.list_audit_records(created.session_id))

    with pytest.raises(RuntimeCandidateMismatchError):
        service.accept_candidate(
            created.session_id,
            CandidateDecisionRequest(
                expected_revision=awaiting.revision,
                candidate_plan_id="PLAN-WRONG-CANDIDATE",
            ),
        )
    with pytest.raises(RuntimeRevisionConflictError):
        service.reject_candidate(
            created.session_id,
            CandidateDecisionRequest(
                expected_revision=awaiting.revision - 1,
                candidate_plan_id=awaiting.candidate_plan_id or "",
            ),
        )

    preserved = repository.get_session(created.session_id)
    assert preserved.status is RuntimeStatus.AWAITING_CONFIRMATION
    assert preserved.revision == awaiting.revision
    assert preserved.candidate_plan_id == awaiting.candidate_plan_id
    assert len(repository.list_audit_records(created.session_id)) == audit_count


def test_solver_failure_pauses_and_preserves_active_plan(tmp_path) -> None:
    def fail_planner(*args, **kwargs):
        raise OptimizationError("forced timeout")

    service, repository, _, scenario, plan = _service(
        tmp_path,
        session_id="RUN-M43-FAIL",
        rolling_planner=fail_planner,
    )
    created = _create(service, scenario, plan)
    paused = service.start_session(
        created.session_id,
        RuntimeRevisionRequest(expected_revision=1),
    )
    source = repository.get_session(created.session_id).projection_source
    assert source is not None

    assert paused.status is RuntimeStatus.PAUSED
    assert paused.revision == 4
    assert paused.current_scenario_version == 2
    assert paused.active_plan_id == plan.plan_id
    assert paused.candidate_plan_id is None
    assert paused.failure is None
    assert paused.events[0].status.value == "failed"
    assert paused.events[0].failure_code == "replan_failed"
    assert source.candidate_plan is None
    assert [audit.action for audit in repository.list_audit_records(created.session_id)][-2:] == [
        "event_batch_applied",
        "replan_failed",
    ]


def test_service_independently_rejects_candidate_with_hidden_violation(tmp_path) -> None:
    def invalid_planner(scenario, active_plan, *args, **kwargs):
        payload = active_plan.model_dump(mode="python")
        payload["plan_id"] = "PLAN-M43-HIDDEN-VIOLATION"
        payload["scenario_version"] = scenario.version
        payload["assignments"][0]["destination_zone_id"] = "GATE-W03"
        return Plan.model_validate(payload)

    service, repository, _, scenario, plan = _service(
        tmp_path,
        session_id="RUN-M43-INVALID",
        rolling_planner=invalid_planner,
    )
    created = _create(service, scenario, plan)
    paused = service.start_session(
        created.session_id,
        RuntimeRevisionRequest(expected_revision=1),
    )

    assert paused.status is RuntimeStatus.PAUSED
    assert paused.candidate_plan_id is None
    assert paused.active_plan_id == plan.plan_id
    assert repository.list_audit_records(created.session_id)[-1].action == "replan_failed"


def test_awaiting_candidate_survives_restart_without_scenario_repository(tmp_path) -> None:
    service, repository, _, scenario, plan = _service(
        tmp_path,
        session_id="RUN-M43-RESTART",
    )
    created = _create(service, scenario, plan)
    awaiting = service.start_session(
        created.session_id,
        RuntimeRevisionRequest(expected_revision=1),
    )
    repository.close()

    reopened = SQLiteRuntimeSessionRepository(tmp_path / "runtime.sqlite3")
    restarted = RuntimeSessionService(
        InMemoryScenarioRepository(),
        reopened,
        wall_clock=lambda: _at(7, 56),
    )
    restored = restarted.get_session(created.session_id)

    assert restored.status is RuntimeStatus.AWAITING_CONFIRMATION
    assert restored.revision == awaiting.revision
    assert restored.candidate_plan_id == awaiting.candidate_plan_id
    assert restored.current_scenario_version == 2
    assert restored.tasks == awaiting.tasks
    assert restored.events == awaiting.events


def test_concurrent_event_materialization_applies_batch_once(tmp_path) -> None:
    service, repository, _, scenario, plan = _service(
        tmp_path,
        session_id="RUN-M43-CONCURRENT",
    )
    created = _create(service, scenario, plan)
    ready = repository.get_session(created.session_id)
    running = repository.replace_record(
        ready,
        status=RuntimeStatus.RUNNING,
        revision=2,
        updated_at=ready.updated_at + timedelta(seconds=1),
    )
    repository.update_session(
        running,
        expected_revision=1,
        action="runtime_started",
        summary="并发测试运行已开始",
    )
    source = running.projection_source
    assert source is not None
    event = next(
        item for item in source.event_catalog if item.event_id == "EVT-SIM102-DELAY"
    )

    def materialize():
        try:
            return service._apply_event_boundary(running, _at(8, 0), [event]).revision
        except RuntimeRevisionConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: materialize(), range(2)))

    assert "conflict" in results
    stored = repository.get_session(created.session_id)
    assert stored.status is RuntimeStatus.AWAITING_CONFIRMATION
    assert stored.current_scenario_version == 2
    actions = [audit.action for audit in repository.list_audit_records(created.session_id)]
    assert actions.count("event_batch_applied") == 1
    assert actions.count("candidate_created") == 1
