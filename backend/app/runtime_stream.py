"""Shared in-memory SSE sequencing and replay for runtime sessions."""

from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from typing import Callable, Iterable
from uuid import uuid4

from .runtime_models import ReplanTrigger, RuntimeSessionSnapshot, RuntimeStatus
from .runtime_repository import RuntimeControlAuditRecord
from .runtime_stream_models import (
    EventAppliedStreamPayload,
    HeartbeatStreamPayload,
    PlanAcceptedStreamPayload,
    PlanRejectedStreamPayload,
    ReplanFailedStreamPayload,
    ReplanReadyStreamPayload,
    ReplanStartedStreamPayload,
    RuntimeCompletedStreamPayload,
    RuntimeSnapshotStreamPayload,
    RuntimeStreamEvent,
    RuntimeStreamPayload,
    RuntimeTickStreamPayload,
    TaskTransitionStreamPayload,
)


_SSE_ID_PATTERN = re.compile(r"^(STREAM-[A-Z0-9-]+):([1-9][0-9]*)$")


@dataclass(frozen=True, slots=True)
class RuntimeStreamRead:
    """One ordered read from a shared runtime stream."""

    stream_id: str
    cursor_sequence: int
    events: tuple[RuntimeStreamEvent, ...]
    snapshot_fallback: bool = False


@dataclass(slots=True)
class _RuntimeStreamState:
    stream_id: str
    buffer_size: int
    next_sequence: int = 1
    buffer: deque[RuntimeStreamEvent] = field(init=False)
    last_snapshot: RuntimeSessionSnapshot | None = None
    last_audit_id: int = 0
    last_tick_monotonic: float | None = None
    last_heartbeat_monotonic: float | None = None

    def __post_init__(self) -> None:
        self.buffer = deque(maxlen=self.buffer_size)

    @property
    def latest_sequence(self) -> int:
        return self.next_sequence - 1


class RuntimeStreamBroker:
    """Publish one thread-safe, replayable SSE sequence per runtime session."""

    def __init__(
        self,
        *,
        buffer_size: int = 256,
        tick_interval_seconds: float = 1.0,
        heartbeat_interval_seconds: float = 15.0,
        stream_id_factory: Callable[[str], str] | None = None,
    ) -> None:
        if buffer_size < 1:
            raise ValueError("runtime stream buffer_size must be positive")
        if tick_interval_seconds <= 0 or heartbeat_interval_seconds <= 0:
            raise ValueError("runtime stream intervals must be positive")
        self.buffer_size = buffer_size
        self.tick_interval_seconds = float(tick_interval_seconds)
        self.heartbeat_interval_seconds = float(heartbeat_interval_seconds)
        self._stream_id_factory = stream_id_factory or (
            lambda _session_id: f"STREAM-{uuid4().hex[:16].upper()}"
        )
        self._states: dict[str, _RuntimeStreamState] = {}
        self._lock = RLock()

    def connect(
        self,
        snapshot: RuntimeSessionSnapshot,
        audits: Iterable[RuntimeControlAuditRecord],
        *,
        last_event_id: str | None,
        emitted_at: datetime,
        monotonic_time: float,
    ) -> RuntimeStreamRead:
        """Open or resume a stream, falling back to a full snapshot when needed."""

        self._require_aware(emitted_at)
        audit_records = tuple(audits)
        with self._lock:
            state, created = self._state_for(snapshot.session_id)
            if created:
                self._establish_baseline(
                    state,
                    snapshot,
                    audit_records,
                    monotonic_time=monotonic_time,
                )
            else:
                self._observe_locked(
                    state,
                    snapshot,
                    audit_records,
                    emitted_at=emitted_at,
                    monotonic_time=monotonic_time,
                )

            parsed = self._parse_sse_id(last_event_id)
            if parsed is not None and parsed[0] == state.stream_id:
                after_sequence = parsed[1]
                replay = self._read_buffer_locked(state, after_sequence)
                if replay is not None:
                    return RuntimeStreamRead(
                        stream_id=state.stream_id,
                        cursor_sequence=(
                            replay[-1].sequence if replay else after_sequence
                        ),
                        events=replay,
                    )

            fallback = self._publish_locked(
                state,
                snapshot.session_id,
                snapshot.revision,
                RuntimeSnapshotStreamPayload(snapshot=snapshot),
                emitted_at,
            )
            return RuntimeStreamRead(
                stream_id=state.stream_id,
                cursor_sequence=fallback.sequence,
                events=(fallback,),
                snapshot_fallback=last_event_id is not None,
            )

    def poll(
        self,
        snapshot: RuntimeSessionSnapshot,
        audits: Iterable[RuntimeControlAuditRecord],
        *,
        stream_id: str,
        after_sequence: int,
        emitted_at: datetime,
        monotonic_time: float,
    ) -> RuntimeStreamRead:
        """Observe current facts and return events after a connection cursor."""

        self._require_aware(emitted_at)
        audit_records = tuple(audits)
        with self._lock:
            state, created = self._state_for(snapshot.session_id)
            if created:
                self._establish_baseline(
                    state,
                    snapshot,
                    audit_records,
                    monotonic_time=monotonic_time,
                )
            else:
                self._observe_locked(
                    state,
                    snapshot,
                    audit_records,
                    emitted_at=emitted_at,
                    monotonic_time=monotonic_time,
                )

            if stream_id == state.stream_id:
                replay = self._read_buffer_locked(state, after_sequence)
                if replay is not None:
                    return RuntimeStreamRead(
                        stream_id=state.stream_id,
                        cursor_sequence=(
                            replay[-1].sequence if replay else after_sequence
                        ),
                        events=replay,
                    )

            fallback = self._publish_locked(
                state,
                snapshot.session_id,
                snapshot.revision,
                RuntimeSnapshotStreamPayload(snapshot=snapshot),
                emitted_at,
            )
            return RuntimeStreamRead(
                stream_id=state.stream_id,
                cursor_sequence=fallback.sequence,
                events=(fallback,),
                snapshot_fallback=True,
            )

    def observe(
        self,
        snapshot: RuntimeSessionSnapshot,
        audits: Iterable[RuntimeControlAuditRecord],
        *,
        emitted_at: datetime,
        monotonic_time: float,
    ) -> tuple[RuntimeStreamEvent, ...]:
        """Publish messages caused by one authoritative snapshot sample."""

        self._require_aware(emitted_at)
        audit_records = tuple(audits)
        with self._lock:
            state, created = self._state_for(snapshot.session_id)
            if created:
                self._establish_baseline(
                    state,
                    snapshot,
                    audit_records,
                    monotonic_time=monotonic_time,
                )
                return ()
            return self._observe_locked(
                state,
                snapshot,
                audit_records,
                emitted_at=emitted_at,
                monotonic_time=monotonic_time,
            )

    def buffered_events(self, session_id: str) -> tuple[RuntimeStreamEvent, ...]:
        with self._lock:
            state = self._states.get(session_id)
            return tuple(state.buffer) if state is not None else ()

    def _state_for(self, session_id: str) -> tuple[_RuntimeStreamState, bool]:
        state = self._states.get(session_id)
        if state is not None:
            return state, False
        stream_id = self._stream_id_factory(session_id)
        if _SSE_ID_PATTERN.fullmatch(f"{stream_id}:1") is None:
            raise ValueError("runtime stream ID factory returned an invalid ID")
        state = _RuntimeStreamState(stream_id=stream_id, buffer_size=self.buffer_size)
        self._states[session_id] = state
        return state, True

    @staticmethod
    def _establish_baseline(
        state: _RuntimeStreamState,
        snapshot: RuntimeSessionSnapshot,
        audits: tuple[RuntimeControlAuditRecord, ...],
        *,
        monotonic_time: float,
    ) -> None:
        state.last_snapshot = snapshot.model_copy(deep=True)
        state.last_audit_id = max((item.audit_id for item in audits), default=0)
        if snapshot.status is RuntimeStatus.RUNNING:
            state.last_tick_monotonic = monotonic_time
        else:
            state.last_heartbeat_monotonic = monotonic_time

    def _observe_locked(
        self,
        state: _RuntimeStreamState,
        snapshot: RuntimeSessionSnapshot,
        audits: tuple[RuntimeControlAuditRecord, ...],
        *,
        emitted_at: datetime,
        monotonic_time: float,
    ) -> tuple[RuntimeStreamEvent, ...]:
        previous = state.last_snapshot
        if previous is None:
            self._establish_baseline(
                state,
                snapshot,
                audits,
                monotonic_time=monotonic_time,
            )
            return ()

        published: list[RuntimeStreamEvent] = []
        new_audits = sorted(
            (item for item in audits if item.audit_id > state.last_audit_id),
            key=lambda item: item.audit_id,
        )
        event_groups = self._new_applied_event_groups(previous, snapshot)
        event_group_index = 0
        for audit in new_audits:
            group = (
                event_groups[event_group_index]
                if audit.action == "event_batch_applied"
                and event_group_index < len(event_groups)
                else None
            )
            if group is not None:
                event_group_index += 1
            for payload in self._payloads_for_audit(
                audit,
                previous,
                snapshot,
                applied_event_group=group,
            ):
                published.append(
                    self._publish_locked(
                        state,
                        snapshot.session_id,
                        audit.revision_after,
                        payload,
                        emitted_at,
                    )
                )

        previous_tasks = {item.task_id: item for item in previous.tasks}
        for current_task in sorted(snapshot.tasks, key=lambda item: item.task_id):
            previous_task = previous_tasks.get(current_task.task_id)
            if previous_task is None or previous_task.status == current_task.status:
                continue
            transition_at = (
                previous_task.next_transition_at
                if previous_task.next_transition_at is not None
                and previous_task.next_transition_at <= snapshot.clock.simulation_time
                else snapshot.clock.simulation_time
            )
            published.append(
                self._publish_locked(
                    state,
                    snapshot.session_id,
                    snapshot.revision,
                    TaskTransitionStreamPayload(
                        task_id=current_task.task_id,
                        previous_status=previous_task.status,
                        current_status=current_task.status,
                        transition_at=transition_at,
                    ),
                    emitted_at,
                )
            )

        if self._snapshot_signature(previous) != self._snapshot_signature(snapshot):
            published.append(
                self._publish_locked(
                    state,
                    snapshot.session_id,
                    snapshot.revision,
                    RuntimeSnapshotStreamPayload(snapshot=snapshot),
                    emitted_at,
                )
            )

        if snapshot.status is RuntimeStatus.RUNNING:
            state.last_heartbeat_monotonic = None
            if previous.status is not RuntimeStatus.RUNNING:
                state.last_tick_monotonic = monotonic_time
            elif (
                state.last_tick_monotonic is None
                or monotonic_time - state.last_tick_monotonic
                >= self.tick_interval_seconds
            ):
                published.append(
                    self._publish_locked(
                        state,
                        snapshot.session_id,
                        snapshot.revision,
                        RuntimeTickStreamPayload(clock=snapshot.clock),
                        emitted_at,
                    )
                )
                state.last_tick_monotonic = monotonic_time
        else:
            state.last_tick_monotonic = None
            if previous.status is RuntimeStatus.RUNNING:
                state.last_heartbeat_monotonic = monotonic_time
            elif (
                state.last_heartbeat_monotonic is None
                or monotonic_time - state.last_heartbeat_monotonic
                >= self.heartbeat_interval_seconds
            ):
                published.append(
                    self._publish_locked(
                        state,
                        snapshot.session_id,
                        snapshot.revision,
                        HeartbeatStreamPayload(server_time=emitted_at),
                        emitted_at,
                    )
                )
                state.last_heartbeat_monotonic = monotonic_time

        state.last_snapshot = snapshot.model_copy(deep=True)
        if new_audits:
            state.last_audit_id = new_audits[-1].audit_id
        return tuple(published)

    def _payloads_for_audit(
        self,
        audit: RuntimeControlAuditRecord,
        previous: RuntimeSessionSnapshot,
        current: RuntimeSessionSnapshot,
        *,
        applied_event_group: tuple[int, tuple[str, ...]] | None,
    ) -> tuple[RuntimeStreamPayload, ...]:
        if audit.action == "event_batch_applied" and applied_event_group is not None:
            version_after, event_ids = applied_event_group
            return (
                EventAppliedStreamPayload(
                    event_ids=list(event_ids),
                    scenario_version_before=version_after - 1,
                    scenario_version_after=version_after,
                ),
                ReplanStartedStreamPayload(
                    trigger=ReplanTrigger.AUTOMATIC_EVENT,
                    event_ids=list(event_ids),
                ),
            )
        if audit.action == "replan_started":
            return (ReplanStartedStreamPayload(trigger=ReplanTrigger.MANUAL),)
        if audit.action == "candidate_created" and current.candidate_plan_id is not None:
            affected_task_ids = sorted(
                task.task_id
                for task in current.tasks
                if task.affected_by_event_ids
            )
            return (
                ReplanReadyStreamPayload(
                    active_plan_id=current.active_plan_id,
                    candidate_plan_id=current.candidate_plan_id,
                    affected_task_ids=affected_task_ids,
                ),
            )
        if audit.action in {
            "replan_failed",
            "event_application_failed",
            "runtime_state_inconsistent",
        }:
            failure_code = (
                current.failure.code
                if current.failure is not None
                else next(
                    (
                        event.failure_code
                        for event in current.events
                        if event.failure_code is not None
                    ),
                    "replan_failed",
                )
            )
            return (
                ReplanFailedStreamPayload(
                    error_code=failure_code,
                    message=(
                        "滚动重规划未生成可安全采用的候选，已保留当前执行方案。"
                        if audit.action == "replan_failed"
                        else (
                            current.failure.message
                            if current.failure is not None
                            else current.guidance.detail
                        )
                    ),
                    fallback_available=current.status is RuntimeStatus.PAUSED,
                ),
            )
        if (
            audit.action == "candidate_accepted"
            and previous.candidate_plan_id is not None
            and current.active_plan_id != previous.active_plan_id
        ):
            return (
                PlanAcceptedStreamPayload(
                    previous_plan_id=previous.active_plan_id,
                    active_plan_id=current.active_plan_id,
                ),
            )
        if (
            audit.action == "candidate_rejected"
            and previous.candidate_plan_id is not None
        ):
            return (
                PlanRejectedStreamPayload(
                    active_plan_id=current.active_plan_id,
                    rejected_plan_id=previous.candidate_plan_id,
                ),
            )
        if audit.action == "runtime_completed":
            return (
                RuntimeCompletedStreamPayload(
                    completed_at=current.clock.simulation_time,
                    completed_task_count=sum(
                        task.status.value == "completed" for task in current.tasks
                    ),
                    unassigned_task_count=sum(
                        task.status.value == "unassigned" for task in current.tasks
                    ),
                ),
            )
        return ()

    @staticmethod
    def _new_applied_event_groups(
        previous: RuntimeSessionSnapshot,
        current: RuntimeSessionSnapshot,
    ) -> list[tuple[int, tuple[str, ...]]]:
        previous_versions = {
            item.event_id: item.scenario_version_after for item in previous.events
        }
        grouped: dict[int, list[str]] = {}
        for event in current.events:
            version = event.scenario_version_after
            if version is None or previous_versions.get(event.event_id) is not None:
                continue
            grouped.setdefault(version, []).append(event.event_id)
        return [
            (version, tuple(sorted(event_ids)))
            for version, event_ids in sorted(grouped.items())
        ]

    @staticmethod
    def _snapshot_signature(snapshot: RuntimeSessionSnapshot) -> str:
        payload = snapshot.model_dump(mode="json")
        clock = payload.get("clock", {})
        if isinstance(clock, dict):
            clock.pop("simulation_time", None)
            clock.pop("server_time", None)
        payload.pop("updated_at", None)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _parse_sse_id(value: str | None) -> tuple[str, int] | None:
        if value is None:
            return None
        matched = _SSE_ID_PATTERN.fullmatch(value.strip())
        if matched is None:
            return None
        return matched.group(1), int(matched.group(2))

    @staticmethod
    def _read_buffer_locked(
        state: _RuntimeStreamState,
        after_sequence: int,
    ) -> tuple[RuntimeStreamEvent, ...] | None:
        latest = state.latest_sequence
        if after_sequence > latest:
            return None
        if not state.buffer:
            return () if after_sequence == latest else None
        earliest = state.buffer[0].sequence
        if after_sequence < earliest - 1:
            return None
        return tuple(
            event for event in state.buffer if event.sequence > after_sequence
        )

    @staticmethod
    def _publish_locked(
        state: _RuntimeStreamState,
        session_id: str,
        revision: int,
        payload: RuntimeStreamPayload,
        emitted_at: datetime,
    ) -> RuntimeStreamEvent:
        event = RuntimeStreamEvent(
            stream_id=state.stream_id,
            sequence=state.next_sequence,
            session_id=session_id,
            revision=revision,
            emitted_at=emitted_at,
            payload=payload,
        )
        state.next_sequence += 1
        state.buffer.append(event)
        return event

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("runtime stream emitted_at must include a timezone offset")


def encode_sse_event(event: RuntimeStreamEvent) -> str:
    """Serialize one typed event as a standards-compatible SSE frame."""

    data = json.dumps(
        event.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        f"id: {event.sse_id}\n"
        f"event: {event.payload.event_type}\n"
        f"data: {data}\n\n"
    )
