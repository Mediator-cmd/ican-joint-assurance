"""Authoritative M4 runtime clock and session-control state machine."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Callable
from uuid import uuid4

from .models import DataClassification
from .repository import InMemoryScenarioRepository, PlanNotFoundError
from .runtime_models import (
    CreateRuntimeSessionRequest,
    ResetRuntimeSessionRequest,
    RuntimeClockSnapshot,
    RuntimeGuidance,
    RuntimeRevisionRequest,
    RuntimeSessionListResponse,
    RuntimeSessionSnapshot,
    RuntimeSessionSummary,
    RuntimeStatus,
    SetRuntimeSpeedRequest,
)
from .runtime_repository import (
    RuntimeRevisionConflictError,
    RuntimeSessionAlreadyExistsError,
    RuntimeSessionRecord,
    SQLiteRuntimeSessionRepository,
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
        recover_on_startup: bool = True,
    ) -> None:
        self.scenario_repository = scenario_repository
        self.runtime_repository = runtime_repository
        self._wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        self._monotonic_clock = monotonic_clock or time.monotonic
        self._session_id_factory = session_id_factory or (
            lambda: f"RUN-{uuid4().hex[:16].upper()}"
        )
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
        self._materialize_elapsed_window_ends()
        records, total = self.runtime_repository.list_sessions(
            scenario_id=scenario_id,
            status=status,
            offset=offset,
            limit=limit,
        )
        summaries: list[RuntimeSessionSummary] = []
        for record in records:
            snapshot = self._build_snapshot(self._materialize_window_end(record))
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

    def _materialize_elapsed_window_ends(self) -> None:
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
            self._materialize_window_end(record)

    def get_session(self, session_id: str) -> RuntimeSessionSnapshot:
        record = self.runtime_repository.get_session(session_id)
        return self._build_snapshot(self._materialize_window_end(record))

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
        return self._build_snapshot(stored)

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

    def _load_for_control(
        self,
        session_id: str,
        expected_revision: int,
    ) -> RuntimeSessionRecord:
        record = self._materialize_window_end(
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

    def _materialize_window_end(
        self,
        record: RuntimeSessionRecord,
    ) -> RuntimeSessionRecord:
        if record.status is not RuntimeStatus.RUNNING:
            return record
        if self._current_simulation_time(record) < record.simulation_window_end:
            return record
        try:
            return self._complete_at_window_end(record, self._now())
        except RuntimeRevisionConflictError:
            return self.runtime_repository.get_session(record.session_id)

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
        simulation_time = self._current_simulation_time(record)
        next_boundary_at = (
            record.simulation_window_end
            if simulation_time < record.simulation_window_end
            else None
        )
        return RuntimeSessionSnapshot(
            session_id=record.session_id,
            scenario_id=record.scenario_id,
            initial_scenario_version=record.initial_scenario_version,
            current_scenario_version=record.current_scenario_version,
            initial_plan_id=record.initial_plan_id,
            active_plan_id=record.active_plan_id,
            candidate_plan_id=record.candidate_plan_id,
            status=record.status,
            revision=record.revision,
            clock=RuntimeClockSnapshot(
                simulation_time=simulation_time,
                server_time=self._now(),
                speed=record.speed,
                is_advancing=record.status is RuntimeStatus.RUNNING,
                next_boundary_at=next_boundary_at,
            ),
            tasks=[],
            resources=[],
            flights=[],
            events=[],
            guidance=self._guidance(record),
            failure=record.failure,
            created_at=record.created_at,
            updated_at=record.updated_at,
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
