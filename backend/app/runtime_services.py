"""Authoritative M4 runtime clock and session-control state machine."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Callable
from uuid import uuid4

from .audit_models import AuditAction
from .constraints import validate_plan
from .models import DataClassification, FlightEvent, Scenario
from .planning_models import Plan
from .planning_objectives import PlanningObjectiveProfile
from .optimizer import OptimizationError
from .repository import InMemoryScenarioRepository, PlanNotFoundError, RepositoryError
from .runtime_models import (
    CreateRuntimeSessionRequest,
    CandidateDecisionRequest,
    ReplanRuntimeSessionRequest,
    ResetRuntimeSessionRequest,
    RuntimeClockSnapshot,
    RuntimeFailure,
    RuntimeGuidance,
    RuntimeRevisionRequest,
    RuntimeSessionListResponse,
    RuntimeSessionSnapshot,
    RuntimeSessionSummary,
    RuntimeStatus,
    SetRuntimeSpeedRequest,
)
from .runtime_repository import (
    RuntimePersistenceError,
    RuntimeRevisionConflictError,
    RuntimeSessionAlreadyExistsError,
    RuntimeSessionRecord,
    SQLiteRuntimeSessionRepository,
)
from .runtime_projection import RuntimeProjectionSource, project_runtime_state
from .runtime_planning import (
    RuntimePlanningError,
    apply_runtime_event_batch,
    build_rolling_candidate,
    reset_runtime_source,
    source_after_candidate_decision,
    source_after_replan_failure,
    source_with_objective,
    source_with_candidate,
    source_with_registered_event,
)


class RuntimeServiceError(Exception):
    """Base class for runtime-session business errors."""


class RuntimePlanNotFoundError(RuntimeServiceError):
    pass


class RuntimePlanScenarioMismatchError(RuntimeServiceError):
    pass


class RuntimePlanVersionMismatchError(RuntimeServiceError):
    pass


class RuntimePlanHasViolationsError(RuntimeServiceError):
    pass


class RuntimeScenarioClassificationError(RuntimeServiceError):
    pass


class RuntimeInvalidTransitionError(RuntimeServiceError):
    def __init__(self, status: RuntimeStatus, action: str) -> None:
        super().__init__(f"runtime status {status.value} does not allow {action}")
        self.status = status
        self.action = action


class RuntimeCandidateMismatchError(RuntimeServiceError):
    def __init__(self, submitted_candidate_id: str, current_candidate_id: str | None) -> None:
        super().__init__(
            f"submitted runtime candidate {submitted_candidate_id} does not match "
            f"{current_candidate_id or 'none'}"
        )
        self.submitted_candidate_id = submitted_candidate_id
        self.current_candidate_id = current_candidate_id


class RuntimeEventAlreadyRegisteredError(RuntimeServiceError):
    def __init__(self, event_id: str) -> None:
        super().__init__(f"runtime event {event_id} is already registered")
        self.event_id = event_id


class RuntimeEventTimeConflictError(RuntimeServiceError):
    def __init__(self, occurred_at: datetime, simulation_time: datetime) -> None:
        super().__init__("runtime event occurred_at is before the frozen simulation time")
        self.occurred_at = occurred_at
        self.simulation_time = simulation_time


class RuntimeEventNotApplicableError(RuntimeServiceError):
    pass


@dataclass(frozen=True, slots=True)
class _ClockAnchor:
    simulation_time: datetime
    monotonic_time: float
    revision: int


class RuntimeSessionService:
    """Coordinate SQLite boundaries with an in-process monotonic clock anchor."""

    def __init__(
        self,
        scenario_repository: InMemoryScenarioRepository,
        runtime_repository: SQLiteRuntimeSessionRepository,
        *,
        wall_clock: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] | None = None,
        session_id_factory: Callable[[], str] | None = None,
        rolling_planner: Callable[..., Plan] | None = None,
        recover_on_startup: bool = True,
    ) -> None:
        self.scenario_repository = scenario_repository
        self.runtime_repository = runtime_repository
        self._wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        self._monotonic_clock = monotonic_clock or time.monotonic
        self._session_id_factory = session_id_factory or (
            lambda: f"RUN-{uuid4().hex[:16].upper()}"
        )
        self._rolling_planner = rolling_planner or build_rolling_candidate
        self._anchors: dict[str, _ClockAnchor] = {}
        self._anchor_lock = RLock()
        if recover_on_startup:
            self.runtime_repository.recover_interrupted_sessions(self._now())

    def create_session(
        self,
        request: CreateRuntimeSessionRequest,
    ) -> RuntimeSessionSnapshot:
        scenario = self.scenario_repository.get_scenario(
            request.scenario_id,
            request.scenario_version,
        )
        if scenario.data_classification not in {
            DataClassification.SYNTHETIC,
            DataClassification.ANONYMIZED_REPLAY,
        }:
            raise RuntimeScenarioClassificationError(
                "runtime sessions require synthetic or anonymized replay data"
            )
        try:
            plan = self.scenario_repository.get_plan(request.active_plan_id)
        except PlanNotFoundError as error:
            raise RuntimePlanNotFoundError(
                f"runtime plan {request.active_plan_id} was not found"
            ) from error
        if plan.scenario_id != scenario.scenario_id:
            raise RuntimePlanScenarioMismatchError(
                "runtime plan does not belong to the requested scenario"
            )
        if plan.scenario_version != scenario.version:
            raise RuntimePlanVersionMismatchError(
                "runtime plan version does not match the requested scenario version"
            )
        if plan.violations:
            raise RuntimePlanHasViolationsError(
                "runtime plan contains hard-constraint violations"
            )
        projection_source = self._build_projection_source(scenario, plan)

        now = self._now()
        for _ in range(5):
            record = RuntimeSessionRecord(
                session_id=self._session_id_factory(),
                scenario_id=scenario.scenario_id,
                initial_scenario_version=scenario.version,
                current_scenario_version=scenario.version,
                initial_plan_id=plan.plan_id,
                active_plan_id=plan.plan_id,
                candidate_plan_id=None,
                status=RuntimeStatus.READY,
                revision=1,
                simulation_time=scenario.window_start,
                simulation_window_start=scenario.window_start,
                simulation_window_end=scenario.window_end,
                speed=request.speed,
                failure=None,
                created_at=now,
                updated_at=now,
                projection_source=projection_source,
            )
            try:
                stored = self.runtime_repository.create_session(record)
                return self._build_snapshot(stored)
            except RuntimeSessionAlreadyExistsError:
                continue
        raise RuntimeSessionAlreadyExistsError(
            "could not allocate a unique runtime session ID"
        )

    def list_sessions(
        self,
        *,
        scenario_id: str | None,
        status: RuntimeStatus | None,
        offset: int,
        limit: int,
    ) -> RuntimeSessionListResponse:
        self._materialize_running_boundaries()
        records, total = self.runtime_repository.list_sessions(
            scenario_id=scenario_id,
            status=status,
            offset=offset,
            limit=limit,
        )
        summaries: list[RuntimeSessionSummary] = []
        for record in records:
            snapshot = self._build_snapshot(self._materialize_runtime_boundary(record))
            summaries.append(
                RuntimeSessionSummary(
                    session_id=snapshot.session_id,
                    scenario_id=snapshot.scenario_id,
                    current_scenario_version=snapshot.current_scenario_version,
                    active_plan_id=snapshot.active_plan_id,
                    status=snapshot.status,
                    revision=snapshot.revision,
                    simulation_time=snapshot.clock.simulation_time,
                    speed=snapshot.clock.speed,
                    action_required=snapshot.guidance.action_required,
                    updated_at=snapshot.updated_at,
                )
            )
        return RuntimeSessionListResponse(
            items=summaries,
            total=total,
            offset=offset,
            limit=limit,
        )

    def _materialize_running_boundaries(self) -> None:
        _, total = self.runtime_repository.list_sessions(
            status=RuntimeStatus.RUNNING,
            offset=0,
            limit=1,
        )
        if total == 0:
            return
        records, _ = self.runtime_repository.list_sessions(
            status=RuntimeStatus.RUNNING,
            offset=0,
            limit=total,
        )
        for record in records:
            self._materialize_runtime_boundary(record)

    def get_session(self, session_id: str) -> RuntimeSessionSnapshot:
        record = self.runtime_repository.get_session(session_id)
        return self._build_snapshot(self._materialize_runtime_boundary(record))

    def get_explanation_context(
        self,
        session_id: str,
        expected_revision: int,
    ) -> tuple[RuntimeSessionSnapshot, RuntimeProjectionSource]:
        """Return one revision-bound snapshot and its authoritative projection facts."""
        record = self._materialize_runtime_boundary(
            self.runtime_repository.get_session(session_id)
        )
        if record.revision != expected_revision:
            raise RuntimeRevisionConflictError(expected_revision, record.revision)
        record = self._hydrate_projection_source(record)
        if record.projection_source is None:
            raise RuntimePlanningError("runtime projection facts are unavailable")
        snapshot = self._build_snapshot(record)
        source = record.projection_source.model_copy(deep=True)
        return snapshot, source

    def start_session(
        self,
        session_id: str,
        request: RuntimeRevisionRequest,
    ) -> RuntimeSessionSnapshot:
        record = self._load_for_control(session_id, request.expected_revision)
        self._require_status(record, "start", {RuntimeStatus.READY, RuntimeStatus.PAUSED})
        now = self._now()
        monotonic_now = self._monotonic_now()
        updated = replace(
            record,
            status=RuntimeStatus.RUNNING,
            revision=record.revision + 1,
            updated_at=max(record.updated_at, now),
        )
        stored = self.runtime_repository.update_session(
            updated,
            expected_revision=request.expected_revision,
            action="runtime_started",
            summary="仿真时钟已开始推进",
        )
        with self._anchor_lock:
            self._anchors[session_id] = _ClockAnchor(
                simulation_time=stored.simulation_time,
                monotonic_time=monotonic_now,
                revision=stored.revision,
            )
        return self._build_snapshot(self._materialize_runtime_boundary(stored))

    def pause_session(
        self,
        session_id: str,
        request: RuntimeRevisionRequest,
    ) -> RuntimeSessionSnapshot:
        record = self._load_for_control(session_id, request.expected_revision)
        self._require_status(record, "pause", {RuntimeStatus.RUNNING})
        simulation_time = self._current_simulation_time(record)
        now = self._now()
        if simulation_time >= record.simulation_window_end:
            completed = self._complete_at_window_end(record, now)
            raise RuntimeRevisionConflictError(
                request.expected_revision,
                completed.revision,
            )
        updated = replace(
            record,
            status=RuntimeStatus.PAUSED,
            revision=record.revision + 1,
            simulation_time=simulation_time,
            updated_at=max(record.updated_at, now),
        )
        stored = self.runtime_repository.update_session(
            updated,
            expected_revision=request.expected_revision,
            action="runtime_paused",
            summary="仿真时钟已暂停并固定当前时间",
        )
        with self._anchor_lock:
            self._anchors.pop(session_id, None)
        return self._build_snapshot(stored)

    def set_speed(
        self,
        session_id: str,
        request: SetRuntimeSpeedRequest,
    ) -> RuntimeSessionSnapshot:
        record = self._load_for_control(session_id, request.expected_revision)
        self._require_status(
            record,
            "speed",
            {RuntimeStatus.READY, RuntimeStatus.RUNNING, RuntimeStatus.PAUSED},
        )
        monotonic_now = self._monotonic_now()
        simulation_time = self._current_simulation_time(record, monotonic_now=monotonic_now)
        now = self._now()
        if simulation_time >= record.simulation_window_end:
            completed = self._complete_at_window_end(record, now)
            raise RuntimeRevisionConflictError(
                request.expected_revision,
                completed.revision,
            )
        updated = replace(
            record,
            revision=record.revision + 1,
            simulation_time=simulation_time,
            speed=request.speed,
            updated_at=max(record.updated_at, now),
        )
        stored = self.runtime_repository.update_session(
            updated,
            expected_revision=request.expected_revision,
            action="runtime_speed_changed",
            summary=f"仿真倍速已调整为 {request.speed.value}x",
        )
        if stored.status is RuntimeStatus.RUNNING:
            with self._anchor_lock:
                self._anchors[session_id] = _ClockAnchor(
                    simulation_time=stored.simulation_time,
                    monotonic_time=monotonic_now,
                    revision=stored.revision,
                )
        return self._build_snapshot(stored)

    def reset_session(
        self,
        session_id: str,
        request: ResetRuntimeSessionRequest,
    ) -> RuntimeSessionSnapshot:
        record = self._load_for_control(session_id, request.expected_revision)
        self._require_status(
            record,
            "reset",
            {
                RuntimeStatus.PAUSED,
                RuntimeStatus.AWAITING_CONFIRMATION,
                RuntimeStatus.COMPLETED,
                RuntimeStatus.FAILED,
            },
        )
        source = record.projection_source
        reset_source = reset_runtime_source(source) if source is not None else None
        updated = replace(
            record,
            current_scenario_version=record.initial_scenario_version,
            active_plan_id=record.initial_plan_id,
            candidate_plan_id=None,
            status=RuntimeStatus.READY,
            revision=record.revision + 1,
            simulation_time=record.simulation_window_start,
            failure=None,
            updated_at=max(record.updated_at, self._now()),
            projection_source=reset_source,
        )
        stored = self.runtime_repository.update_session(
            updated,
            expected_revision=request.expected_revision,
            action="runtime_reset",
            summary="运行会话已重置到初始仿真状态",
        )
        with self._anchor_lock:
            self._anchors.pop(session_id, None)
        return self._build_snapshot(stored)

    def replan_session(
        self,
        session_id: str,
        request: ReplanRuntimeSessionRequest,
    ) -> RuntimeSessionSnapshot:
        record = self._load_for_control(session_id, request.expected_revision)
        self._require_status(record, "replan", {RuntimeStatus.PAUSED})
        if record.projection_source is None:
            raise RuntimePlanningError("runtime projection facts are unavailable")
        objective_profile = (
            request.objective_profile
            if request.objective_profile is not None
            else record.projection_source.objective_profile
        )
        objective_source = source_with_objective(
            record.projection_source,
            objective_profile,
        )
        replanning = replace(
            record,
            status=RuntimeStatus.REPLANNING,
            revision=record.revision + 1,
            candidate_plan_id=None,
            failure=None,
            updated_at=max(record.updated_at, self._now()),
            projection_source=objective_source,
        )
        stored = self.runtime_repository.update_session(
            replanning,
            expected_revision=record.revision,
            action="replan_started",
            summary=(
                f"已按人工请求启动滚动重规划：{request.reason}"
                if request.reason
                else "已按人工请求启动滚动重规划"
            ),
        )
        return self._build_snapshot(self._finish_replan(stored))

    def submit_reviewed_event(
        self,
        session_id: str,
        *,
        expected_revision: int,
        event: FlightEvent,
        objective_profile: PlanningObjectiveProfile,
    ) -> RuntimeSessionSnapshot:
        record = self._load_for_control(session_id, expected_revision)
        self._require_status(
            record,
            "event_submit",
            {RuntimeStatus.READY, RuntimeStatus.PAUSED},
        )
        source = record.projection_source
        if source is None:
            raise RuntimePlanningError("runtime projection facts are unavailable")
        if event.event_id in {item.event_id for item in source.event_catalog}:
            raise RuntimeEventAlreadyRegisteredError(event.event_id)
        if event.occurred_at < record.simulation_time:
            raise RuntimeEventTimeConflictError(
                event.occurred_at,
                record.simulation_time,
            )
        try:
            registered_source = source_with_registered_event(
                source,
                event,
                objective_profile,
            )
        except ValueError as error:
            raise RuntimeEventNotApplicableError(
                "reviewed event does not match the runtime scenario"
            ) from error

        if event.occurred_at == record.simulation_time:
            batch = sorted(
                (
                    item
                    for item in registered_source.event_catalog
                    if item.event_id not in registered_source.applied_event_versions
                    and item.occurred_at == event.occurred_at
                ),
                key=lambda item: item.event_id,
            )
            updated_record = replace(record, projection_source=registered_source)
            return self._build_snapshot(
                self._apply_event_boundary(updated_record, event.occurred_at, batch)
            )

        updated = replace(
            record,
            revision=record.revision + 1,
            failure=None,
            updated_at=max(record.updated_at, self._now()),
            projection_source=registered_source,
        )
        stored = self.runtime_repository.update_session(
            updated,
            expected_revision=record.revision,
            action="runtime_event_registered",
            summary=f"人工复核事件 {event.event_id} 已登记并等待仿真时间触发",
        )
        return self._build_snapshot(stored)

    def accept_candidate(
        self,
        session_id: str,
        request: CandidateDecisionRequest,
    ) -> RuntimeSessionSnapshot:
        return self._decide_candidate(session_id, request, accept=True)

    def reject_candidate(
        self,
        session_id: str,
        request: CandidateDecisionRequest,
    ) -> RuntimeSessionSnapshot:
        return self._decide_candidate(session_id, request, accept=False)

    def _decide_candidate(
        self,
        session_id: str,
        request: CandidateDecisionRequest,
        *,
        accept: bool,
    ) -> RuntimeSessionSnapshot:
        record = self._load_for_control(session_id, request.expected_revision)
        self._require_status(
            record,
            "candidate_accept" if accept else "candidate_reject",
            {RuntimeStatus.AWAITING_CONFIRMATION},
        )
        if record.candidate_plan_id != request.candidate_plan_id:
            raise RuntimeCandidateMismatchError(
                request.candidate_plan_id,
                record.candidate_plan_id,
            )
        source = record.projection_source
        if (
            source is None
            or source.candidate_plan is None
            or source.candidate_plan.plan_id != request.candidate_plan_id
        ):
            raise RuntimeCandidateMismatchError(
                request.candidate_plan_id,
                source.candidate_plan.plan_id
                if source is not None and source.candidate_plan is not None
                else None,
            )
        decided_source = source_after_candidate_decision(source, accept=accept)
        now = self._now()
        updated = replace(
            record,
            active_plan_id=(
                request.candidate_plan_id if accept else record.active_plan_id
            ),
            candidate_plan_id=None,
            status=RuntimeStatus.PAUSED,
            revision=record.revision + 1,
            failure=None,
            updated_at=max(record.updated_at, now),
            projection_source=decided_source,
        )
        reason_suffix = f"：{request.reason}" if request.reason else ""
        stored = self.runtime_repository.update_session(
            updated,
            expected_revision=record.revision,
            action="candidate_accepted" if accept else "candidate_rejected",
            summary=(
                f"已采用候选方案 {request.candidate_plan_id}{reason_suffix}"
                if accept
                else f"已保留当前方案并拒绝候选 {request.candidate_plan_id}{reason_suffix}"
            ),
        )
        return self._build_snapshot(stored)

    def _load_for_control(
        self,
        session_id: str,
        expected_revision: int,
    ) -> RuntimeSessionRecord:
        record = self._materialize_runtime_boundary(
            self.runtime_repository.get_session(session_id)
        )
        if record.revision != expected_revision:
            raise RuntimeRevisionConflictError(expected_revision, record.revision)
        return record

    @staticmethod
    def _require_status(
        record: RuntimeSessionRecord,
        action: str,
        allowed: set[RuntimeStatus],
    ) -> None:
        if record.status not in allowed:
            raise RuntimeInvalidTransitionError(record.status, action)

    def _materialize_runtime_boundary(
        self,
        record: RuntimeSessionRecord,
    ) -> RuntimeSessionRecord:
        if record.status is not RuntimeStatus.RUNNING:
            return record
        record = self._hydrate_projection_source(record)
        target_time = self._current_simulation_time(record)
        source = record.projection_source
        if source is not None:
            pending_events = sorted(
                (
                    event
                    for event in source.event_catalog
                    if event.event_id not in source.applied_event_versions
                    and event.occurred_at <= target_time
                ),
                key=lambda item: (item.occurred_at, item.event_id),
            )
            if pending_events:
                boundary = pending_events[0].occurred_at
                batch = [
                    event for event in pending_events if event.occurred_at == boundary
                ]
                try:
                    return self._apply_event_boundary(record, boundary, batch)
                except RuntimeRevisionConflictError:
                    return self.runtime_repository.get_session(record.session_id)
        if target_time < record.simulation_window_end:
            return record
        try:
            return self._complete_at_window_end(record, self._now())
        except RuntimeRevisionConflictError:
            return self.runtime_repository.get_session(record.session_id)

    def _apply_event_boundary(
        self,
        record: RuntimeSessionRecord,
        boundary: datetime,
        events: list[FlightEvent],
    ) -> RuntimeSessionRecord:
        source = record.projection_source
        if source is None:
            raise RuntimePlanningError("runtime projection facts are unavailable")
        projection = project_runtime_state(source, boundary, record.status)
        frozen_task_ids = set(source.frozen_task_ids)
        frozen_task_ids.update(
            task.task_id
            for task in projection.tasks
            if task.is_locked and task.assignment_id is not None
        )
        try:
            event_source = apply_runtime_event_batch(
                source,
                list(events),
                frozen_task_ids,
            )
        except (RuntimePlanningError, ValueError):
            failed_codes = dict(source.failed_event_codes)
            failed_codes.update(
                {event.event_id: "event_application_failed" for event in events}
            )
            failed_source_data = source.model_dump(mode="python")
            failed_source_data["failed_event_codes"] = failed_codes
            failed_source = RuntimeProjectionSource.model_validate(failed_source_data)
            failed = replace(
                record,
                status=RuntimeStatus.FAILED,
                revision=record.revision + 1,
                simulation_time=boundary,
                candidate_plan_id=None,
                failure=RuntimeFailure(
                    code="event_application_failed",
                    message="事件与当前运行事实不一致，已冻结会话等待复核",
                    recoverable=False,
                ),
                updated_at=max(record.updated_at, self._now()),
                projection_source=failed_source,
            )
            stored = self.runtime_repository.update_session(
                failed,
                expected_revision=record.revision,
                action="event_application_failed",
                summary="事件批次未写入场景，会话已安全冻结",
            )
            with self._anchor_lock:
                self._anchors.pop(record.session_id, None)
            return stored

        replanning = replace(
            record,
            current_scenario_version=event_source.scenario.version,
            candidate_plan_id=None,
            status=RuntimeStatus.REPLANNING,
            revision=record.revision + 1,
            simulation_time=boundary,
            failure=None,
            updated_at=max(record.updated_at, self._now()),
            projection_source=event_source,
        )
        stored = self.runtime_repository.update_session(
            replanning,
            expected_revision=record.revision,
            action="event_batch_applied",
            summary=(
                f"事件批次已应用到场景版本 {event_source.scenario.version}："
                + "、".join(event.event_id for event in events)
            ),
        )
        with self._anchor_lock:
            self._anchors.pop(record.session_id, None)
        return self._finish_replan(stored)

    def _finish_replan(self, record: RuntimeSessionRecord) -> RuntimeSessionRecord:
        source = record.projection_source
        if source is None:
            raise RuntimePlanningError("runtime projection facts are unavailable")
        try:
            candidate = self._rolling_planner(
                source.scenario,
                source.plan,
                record.simulation_time,
                set(source.frozen_task_ids),
                record.session_id,
                record.revision,
                5.0,
                objective_profile=source.objective_profile,
            )
            independent_violations = validate_plan(
                source.scenario,
                candidate.assignments,
                candidate.unassigned_tasks,
            )
            if candidate.violations or independent_violations:
                raise RuntimePlanningError("runtime candidate contains hard violations")
            candidate_source = source_with_candidate(source, candidate)
        except (OptimizationError, RuntimePlanningError):
            failed_source = source_after_replan_failure(source, "replan_failed")
            paused = replace(
                record,
                status=RuntimeStatus.PAUSED,
                revision=record.revision + 1,
                candidate_plan_id=None,
                failure=None,
                updated_at=max(record.updated_at, self._now()),
                projection_source=failed_source,
            )
            return self.runtime_repository.update_session(
                paused,
                expected_revision=record.revision,
                action="replan_failed",
                summary="滚动重规划未生成可安全采用的候选，已保留当前方案",
            )
        except ValueError:
            failed = replace(
                record,
                status=RuntimeStatus.FAILED,
                revision=record.revision + 1,
                candidate_plan_id=None,
                failure=RuntimeFailure(
                    code="runtime_state_inconsistent",
                    message="运行状态一致性复核失败，已冻结会话等待检查",
                    recoverable=False,
                ),
                updated_at=max(record.updated_at, self._now()),
            )
            return self.runtime_repository.update_session(
                failed,
                expected_revision=record.revision,
                action="runtime_state_inconsistent",
                summary="候选方案状态不一致，会话已安全冻结",
            )

        awaiting = replace(
            record,
            candidate_plan_id=candidate.plan_id,
            status=RuntimeStatus.AWAITING_CONFIRMATION,
            revision=record.revision + 1,
            failure=None,
            updated_at=max(record.updated_at, self._now()),
            projection_source=candidate_source,
        )
        return self.runtime_repository.update_session(
            awaiting,
            expected_revision=record.revision,
            action="candidate_created",
            summary=f"滚动候选方案 {candidate.plan_id} 已通过独立约束复核",
        )

    def _complete_at_window_end(
        self,
        record: RuntimeSessionRecord,
        now: datetime,
    ) -> RuntimeSessionRecord:
        completed = replace(
            record,
            status=RuntimeStatus.COMPLETED,
            revision=record.revision + 1,
            simulation_time=record.simulation_window_end,
            updated_at=max(record.updated_at, now),
        )
        stored = self.runtime_repository.update_session(
            completed,
            expected_revision=record.revision,
            action="runtime_completed",
            summary="仿真时钟已到达场景窗口终点",
        )
        with self._anchor_lock:
            self._anchors.pop(record.session_id, None)
        return stored

    def _current_simulation_time(
        self,
        record: RuntimeSessionRecord,
        *,
        monotonic_now: float | None = None,
    ) -> datetime:
        if record.status is not RuntimeStatus.RUNNING:
            return record.simulation_time
        current_monotonic = self._monotonic_now() if monotonic_now is None else monotonic_now
        with self._anchor_lock:
            anchor = self._anchors.get(record.session_id)
            if anchor is None or anchor.revision != record.revision:
                anchor = _ClockAnchor(
                    simulation_time=record.simulation_time,
                    monotonic_time=current_monotonic,
                    revision=record.revision,
                )
                self._anchors[record.session_id] = anchor
        elapsed_seconds = max(0.0, current_monotonic - anchor.monotonic_time)
        projected = anchor.simulation_time + timedelta(
            seconds=elapsed_seconds * record.speed.value
        )
        return min(projected, record.simulation_window_end)

    def _build_snapshot(self, record: RuntimeSessionRecord) -> RuntimeSessionSnapshot:
        record = self._hydrate_projection_source(record)
        self._validate_projection_record(record)
        simulation_time = self._current_simulation_time(record)
        projection = (
            project_runtime_state(
                record.projection_source,
                simulation_time,
                record.status,
            )
            if record.projection_source is not None
            else None
        )
        next_boundary_at = (
            projection.next_boundary_at
            if projection is not None
            else (
                record.simulation_window_end
                if simulation_time < record.simulation_window_end
                else None
            )
        )
        return RuntimeSessionSnapshot(
            session_id=record.session_id,
            scenario_id=record.scenario_id,
            initial_scenario_version=record.initial_scenario_version,
            current_scenario_version=record.current_scenario_version,
            initial_plan_id=record.initial_plan_id,
            active_plan_id=record.active_plan_id,
            candidate_plan_id=record.candidate_plan_id,
            active_plan_detail=(
                record.projection_source.plan
                if record.projection_source is not None
                else None
            ),
            candidate_plan_detail=(
                record.projection_source.candidate_plan
                if record.projection_source is not None
                else None
            ),
            objective_profile=(
                record.projection_source.objective_profile
                if record.projection_source is not None
                else PlanningObjectiveProfile.BALANCED
            ),
            status=record.status,
            revision=record.revision,
            clock=RuntimeClockSnapshot(
                simulation_time=simulation_time,
                server_time=self._now(),
                speed=record.speed,
                is_advancing=record.status is RuntimeStatus.RUNNING,
                next_boundary_at=next_boundary_at,
            ),
            tasks=projection.tasks if projection is not None else [],
            resources=projection.resources if projection is not None else [],
            flights=projection.flights if projection is not None else [],
            events=projection.events if projection is not None else [],
            guidance=self._guidance(record),
            failure=record.failure,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _validate_projection_record(record: RuntimeSessionRecord) -> None:
        source = record.projection_source
        if source is None:
            return
        candidate_plan_id = (
            source.candidate_plan.plan_id if source.candidate_plan is not None else None
        )
        if (
            source.scenario.scenario_id != record.scenario_id
            or source.initial_scenario is None
            or source.initial_scenario.version != record.initial_scenario_version
            or source.initial_plan is None
            or source.initial_plan.plan_id != record.initial_plan_id
            or source.scenario.version != record.current_scenario_version
            or source.plan.plan_id != record.active_plan_id
            or candidate_plan_id != record.candidate_plan_id
        ):
            raise RuntimePersistenceError(
                "runtime session columns do not match persisted projection facts"
            )

    def _build_projection_source(
        self,
        scenario: Scenario,
        plan: Plan,
    ) -> RuntimeProjectionSource:
        applied_event_ids = {event.event_id for event in scenario.events}
        applied_event_versions: dict[str, int] = {}
        for audit in self.scenario_repository.list_audit_records(scenario.scenario_id):
            if (
                audit.action is AuditAction.EVENTS_APPLIED
                and audit.version_after is not None
                and audit.version_after <= scenario.version
            ):
                for event_id in audit.related_entity_ids:
                    if event_id in applied_event_ids:
                        applied_event_versions[event_id] = audit.version_after
        return RuntimeProjectionSource(
            scenario=scenario,
            plan=plan,
            objective_profile=plan.objective_profile,
            event_catalog=list(
                self.scenario_repository.get_events(scenario.scenario_id)
            ),
            applied_event_versions=applied_event_versions,
        )

    def _hydrate_projection_source(
        self,
        record: RuntimeSessionRecord,
    ) -> RuntimeSessionRecord:
        if record.projection_source is not None:
            return record
        try:
            scenario = self.scenario_repository.get_scenario(
                record.scenario_id,
                record.current_scenario_version,
            )
            plan = self.scenario_repository.get_plan(record.active_plan_id)
            source = self._build_projection_source(scenario, plan)
        except (RepositoryError, ValueError):
            return record
        return self.runtime_repository.attach_projection_source(
            record.session_id,
            source,
        )

    @staticmethod
    def _guidance(record: RuntimeSessionRecord) -> RuntimeGuidance:
        if record.status is RuntimeStatus.READY:
            return RuntimeGuidance(
                headline="运行准备就绪",
                detail="初始方案已经通过约束复核，仿真时间尚未开始推进。",
                action_required=True,
                recommended_action="确认场景与方案后开始运行",
            )
        if record.status is RuntimeStatus.RUNNING:
            return RuntimeGuidance(
                headline="仿真正在运行",
                detail=f"仿真时间正按 {record.speed.value} 倍速推进。",
                action_required=False,
                recommended_action="继续观察运行变化，必要时暂停复核",
            )
        if record.status is RuntimeStatus.PAUSED:
            source = record.projection_source
            unresolved_events = (
                set(source.applied_event_versions)
                - set(source.resolved_event_ids)
                - set(source.failed_event_codes)
                if source is not None
                else set()
            )
            if unresolved_events:
                return RuntimeGuidance(
                    headline="重规划需要重新启动",
                    detail="事件已经生效，但候选计算在服务恢复前未完成，当前方案仍被保留。",
                    action_required=True,
                    recommended_action="复核当前状态后点击重新计算",
                )
            return RuntimeGuidance(
                headline="运行已暂停",
                detail="仿真时间已固定，继续运行前可先复核当前状态。",
                action_required=True,
                recommended_action="复核完成后继续运行或重置会话",
            )
        if record.status is RuntimeStatus.COMPLETED:
            return RuntimeGuidance(
                headline="本次运行已完成",
                detail="仿真时间已经到达场景窗口终点。",
                action_required=False,
                recommended_action="查看运行记录，或重置后再次演示",
            )
        if record.status is RuntimeStatus.AWAITING_CONFIRMATION:
            return RuntimeGuidance(
                headline="新方案等待确认",
                detail="仿真时间保持冻结，当前方案尚未被替换。",
                action_required=True,
                recommended_action="比较候选方案后明确采用或保留当前方案",
            )
        if record.status is RuntimeStatus.REPLANNING:
            return RuntimeGuidance(
                headline="系统正在重新规划",
                detail="仿真时间保持冻结，当前执行事实不会被改写。",
                action_required=False,
                recommended_action="等待候选方案完成约束复核",
            )
        return RuntimeGuidance(
            headline="运行需要人工处理",
            detail="会话无法安全继续，仿真时间已经冻结。",
            action_required=True,
            recommended_action="查看错误摘要并在确认后重置会话",
        )

    def _now(self) -> datetime:
        value = self._wall_clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("wall clock must return a timezone-aware datetime")
        return value

    def _monotonic_now(self) -> float:
        return float(self._monotonic_clock())
