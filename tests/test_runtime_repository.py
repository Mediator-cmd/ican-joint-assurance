from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.runtime_models import RuntimeStatus, SimulationSpeed
from backend.app.runtime_repository import (
    RuntimeRevisionConflictError,
    RuntimeSessionRecord,
    SQLiteRuntimeSessionRepository,
)


BASE_TIME = datetime(2026, 7, 30, 8, 0, tzinfo=timezone(timedelta(hours=8)))


def _record(session_id: str = "RUN-REPOSITORY-001") -> RuntimeSessionRecord:
    return RuntimeSessionRecord(
        session_id=session_id,
        scenario_id="SCN-TERMINAL-DISTURBANCE-01",
        initial_scenario_version=1,
        current_scenario_version=1,
        initial_plan_id="PLAN-BASELINE",
        active_plan_id="PLAN-BASELINE",
        candidate_plan_id=None,
        status=RuntimeStatus.READY,
        revision=1,
        simulation_time=BASE_TIME,
        simulation_window_start=BASE_TIME,
        simulation_window_end=BASE_TIME + timedelta(hours=2),
        speed=SimulationSpeed.REAL_TIME,
        failure=None,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )


def test_repository_persists_lists_filters_and_audits(tmp_path) -> None:
    path = tmp_path / "runtime.sqlite3"
    repository = SQLiteRuntimeSessionRepository(path)
    repository.create_session(_record())
    repository.create_session(_record("RUN-REPOSITORY-002"))
    repository.close()

    reopened = SQLiteRuntimeSessionRepository(path)
    stored = reopened.get_session("RUN-REPOSITORY-001")
    items, total = reopened.list_sessions(
        scenario_id=stored.scenario_id,
        status=RuntimeStatus.READY,
        offset=1,
        limit=1,
    )
    audit = reopened.list_audit_records(stored.session_id)

    assert stored == _record()
    assert total == 2
    assert len(items) == 1
    assert audit[0].action == "session_created"
    assert audit[0].revision_before is None
    assert audit[0].revision_after == 1


def test_repository_cas_allows_exactly_one_concurrent_update(tmp_path) -> None:
    path = tmp_path / "runtime.sqlite3"
    first = SQLiteRuntimeSessionRepository(path)
    second = SQLiteRuntimeSessionRepository(path)
    initial = first.create_session(_record())
    updated = first.replace_record(
        initial,
        status=RuntimeStatus.RUNNING,
        revision=2,
        updated_at=BASE_TIME + timedelta(seconds=1),
    )

    def attempt(repository: SQLiteRuntimeSessionRepository) -> str:
        try:
            repository.update_session(
                updated,
                expected_revision=1,
                action="runtime_started",
                summary="运行已开始",
            )
        except RuntimeRevisionConflictError:
            return "conflict"
        return "updated"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, (first, second)))

    assert sorted(results) == ["conflict", "updated"]
    assert first.get_session(initial.session_id).revision == 2
    assert len(first.list_audit_records(initial.session_id)) == 2


def test_repository_recovers_running_session_once_and_keeps_confirmation(tmp_path) -> None:
    path = tmp_path / "runtime.sqlite3"
    repository = SQLiteRuntimeSessionRepository(path)
    ready = repository.create_session(_record("RUN-RECOVER-RUNNING"))
    running = repository.replace_record(
        ready,
        status=RuntimeStatus.RUNNING,
        revision=2,
        updated_at=BASE_TIME + timedelta(seconds=1),
    )
    repository.update_session(
        running,
        expected_revision=1,
        action="runtime_started",
        summary="运行已开始",
    )

    awaiting = repository.create_session(_record("RUN-RECOVER-AWAITING"))
    awaiting = repository.replace_record(
        awaiting,
        status=RuntimeStatus.AWAITING_CONFIRMATION,
        candidate_plan_id="PLAN-CANDIDATE",
        revision=2,
        updated_at=BASE_TIME + timedelta(seconds=1),
    )
    repository.update_session(
        awaiting,
        expected_revision=1,
        action="candidate_created",
        summary="候选方案待确认",
    )

    recovered_at = BASE_TIME + timedelta(minutes=1)
    assert repository.recover_interrupted_sessions(recovered_at) == ("RUN-RECOVER-RUNNING",)
    assert repository.recover_interrupted_sessions(recovered_at) == ()

    recovered = repository.get_session("RUN-RECOVER-RUNNING")
    preserved = repository.get_session("RUN-RECOVER-AWAITING")
    assert recovered.status is RuntimeStatus.PAUSED
    assert recovered.revision == 3
    assert recovered.simulation_time == BASE_TIME
    assert [item.action for item in repository.list_audit_records(recovered.session_id)] == [
        "session_created",
        "runtime_started",
        "service_restarted",
    ]
    assert preserved.status is RuntimeStatus.AWAITING_CONFIRMATION
    assert preserved.candidate_plan_id == "PLAN-CANDIDATE"
    assert preserved.revision == 2


def test_repository_rejects_naive_persistence_timestamps(tmp_path) -> None:
    repository = SQLiteRuntimeSessionRepository(tmp_path / "runtime.sqlite3")
    invalid = repository.replace_record(_record(), updated_at=BASE_TIME.replace(tzinfo=None))

    with pytest.raises(ValueError, match="timezone-aware"):
        repository.create_session(invalid)

    assert repository.create_session(_record()).session_id == "RUN-REPOSITORY-001"
