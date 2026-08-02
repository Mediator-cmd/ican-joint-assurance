from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from backend.app.runtime_models import (
    EventRuntimeProjection,
    RuntimeClockSnapshot,
    RuntimeGuidance,
    RuntimeSessionSnapshot,
    RuntimeStatus,
    TaskRuntimeProjection,
)
from backend.app.runtime_repository import RuntimeControlAuditRecord
from backend.app.runtime_stream import RuntimeStreamBroker, encode_sse_event


BASE_TIME = datetime(2026, 8, 1, 8, 0, tzinfo=timezone(timedelta(hours=8)))
SESSION_ID = "RUN-STREAM-001"


def _clock(
    seconds: int = 0,
    *,
    advancing: bool = False,
) -> RuntimeClockSnapshot:
    current = BASE_TIME + timedelta(seconds=seconds)
    return RuntimeClockSnapshot(
        simulation_time=current,
        server_time=current,
        speed=1,
        is_advancing=advancing,
        next_boundary_at=BASE_TIME + timedelta(minutes=5),
    )


def _guidance(status: RuntimeStatus) -> RuntimeGuidance:
    return RuntimeGuidance(
        headline=f"状态：{status.value}",
        detail="当前运行状态来自后端权威快照。",
        action_required=status is not RuntimeStatus.RUNNING,
        recommended_action="继续观察或按状态执行操作",
    )


def _snapshot(**updates) -> RuntimeSessionSnapshot:
    status = updates.get("status", RuntimeStatus.PAUSED)
    payload = {
        "session_id": SESSION_ID,
        "scenario_id": "SCN-STREAM-001",
        "initial_scenario_version": 1,
        "current_scenario_version": 1,
        "initial_plan_id": "PLAN-BASE",
        "active_plan_id": "PLAN-BASE",
        "candidate_plan_id": None,
        "status": status,
        "revision": 1,
        "clock": _clock(advancing=status is RuntimeStatus.RUNNING),
        "tasks": [],
        "resources": [],
        "flights": [],
        "events": [],
        "guidance": _guidance(status),
        "failure": None,
        "created_at": BASE_TIME,
        "updated_at": BASE_TIME,
    }
    payload.update(updates)
    return RuntimeSessionSnapshot.model_validate(payload)


def _audit(
    audit_id: int,
    action: str,
    revision_after: int,
    *,
    status_before: RuntimeStatus,
    status_after: RuntimeStatus,
) -> RuntimeControlAuditRecord:
    return RuntimeControlAuditRecord(
        audit_id=audit_id,
        session_id=SESSION_ID,
        action=action,
        revision_before=revision_after - 1,
        revision_after=revision_after,
        status_before=status_before,
        status_after=status_after,
        simulation_time=BASE_TIME,
        created_at=BASE_TIME,
        summary=action,
    )


def _broker(*, buffer_size: int = 256) -> RuntimeStreamBroker:
    return RuntimeStreamBroker(
        buffer_size=buffer_size,
        stream_id_factory=lambda _session_id: "STREAM-TEST-001",
    )


def test_first_connection_and_multiple_clients_share_one_sequence() -> None:
    broker = _broker()
    snapshot = _snapshot()

    first = broker.connect(
        snapshot,
        [],
        last_event_id=None,
        emitted_at=BASE_TIME,
        monotonic_time=0,
    )
    resumed = broker.connect(
        snapshot,
        [],
        last_event_id=first.events[0].sse_id,
        emitted_at=BASE_TIME,
        monotonic_time=0,
    )
    second_client = broker.connect(
        snapshot,
        [],
        last_event_id=None,
        emitted_at=BASE_TIME,
        monotonic_time=0,
    )

    assert first.stream_id == resumed.stream_id == second_client.stream_id
    assert first.events[0].payload.event_type == "runtime.snapshot"
    assert first.events[0].sequence == 1
    assert resumed.events == ()
    assert resumed.cursor_sequence == 1
    assert second_client.events[0].sequence == 2
    assert [item.sequence for item in broker.buffered_events(SESSION_ID)] == [1, 2]


def test_tick_and_heartbeat_are_throttled_without_changing_revision() -> None:
    running_broker = _broker()
    running = _snapshot(
        status=RuntimeStatus.RUNNING,
        clock=_clock(advancing=True),
        guidance=_guidance(RuntimeStatus.RUNNING),
    )
    running_broker.connect(
        running,
        [],
        last_event_id=None,
        emitted_at=BASE_TIME,
        monotonic_time=0,
    )

    early = running_broker.observe(
        running.model_copy(update={"clock": _clock(1, advancing=True)}),
        [],
        emitted_at=BASE_TIME + timedelta(seconds=1),
        monotonic_time=0.9,
    )
    tick = running_broker.observe(
        running.model_copy(update={"clock": _clock(2, advancing=True)}),
        [],
        emitted_at=BASE_TIME + timedelta(seconds=2),
        monotonic_time=1.0,
    )

    assert early == ()
    assert [item.payload.event_type for item in tick] == ["runtime.tick"]
    assert tick[0].revision == running.revision

    paused_broker = _broker()
    paused = _snapshot()
    paused_broker.connect(
        paused,
        [],
        last_event_id=None,
        emitted_at=BASE_TIME,
        monotonic_time=0,
    )
    assert paused_broker.observe(
        paused,
        [],
        emitted_at=BASE_TIME + timedelta(seconds=14),
        monotonic_time=14,
    ) == ()
    heartbeat = paused_broker.observe(
        paused,
        [],
        emitted_at=BASE_TIME + timedelta(seconds=15),
        monotonic_time=15,
    )
    assert [item.payload.event_type for item in heartbeat] == ["heartbeat"]
    assert heartbeat[0].revision == paused.revision


def test_task_transition_precedes_snapshot_and_tick() -> None:
    broker = _broker()
    pending = TaskRuntimeProjection(
        task_id="TASK-001",
        status="pending",
        assignment_id="ASG-001",
        resource_id="WC-01",
        current_zone_id="SERVICE-CENTER",
        next_transition_at=BASE_TIME + timedelta(seconds=1),
    )
    initial = _snapshot(
        status=RuntimeStatus.RUNNING,
        clock=_clock(advancing=True),
        tasks=[pending],
        guidance=_guidance(RuntimeStatus.RUNNING),
    )
    broker.connect(
        initial,
        [],
        last_event_id=None,
        emitted_at=BASE_TIME,
        monotonic_time=0,
    )
    en_route = TaskRuntimeProjection(
        task_id="TASK-001",
        status="en_route",
        assignment_id="ASG-001",
        resource_id="WC-01",
        current_zone_id="SERVICE-CENTER",
        next_transition_at=BASE_TIME + timedelta(minutes=1),
    )
    current = _snapshot(
        status=RuntimeStatus.RUNNING,
        clock=_clock(1, advancing=True),
        tasks=[en_route],
        guidance=_guidance(RuntimeStatus.RUNNING),
    )

    messages = broker.observe(
        current,
        [],
        emitted_at=BASE_TIME + timedelta(seconds=1),
        monotonic_time=1,
    )

    assert [item.payload.event_type for item in messages] == [
        "task.transition",
        "runtime.snapshot",
        "runtime.tick",
    ]
    transition = messages[0].payload
    assert transition.previous_status.value == "pending"
    assert transition.current_status.value == "en_route"
    assert transition.transition_at == BASE_TIME + timedelta(seconds=1)


def test_event_batch_and_candidate_audits_publish_typed_messages() -> None:
    broker = _broker()
    initial_event = EventRuntimeProjection(
        event_id="EVT-001",
        event_type="delay",
        flight_id="FL-SIM102",
        detail="SIM102 预计离港时间顺延 25 分钟",
        status="pending",
        occurred_at=BASE_TIME,
    )
    initial = _snapshot(
        status=RuntimeStatus.RUNNING,
        clock=_clock(advancing=True),
        events=[initial_event],
        guidance=_guidance(RuntimeStatus.RUNNING),
    )
    start_audit = _audit(
        1,
        "runtime_started",
        2,
        status_before=RuntimeStatus.READY,
        status_after=RuntimeStatus.RUNNING,
    )
    broker.connect(
        initial,
        [start_audit],
        last_event_id=None,
        emitted_at=BASE_TIME,
        monotonic_time=0,
    )

    applied_event = EventRuntimeProjection(
        event_id="EVT-001",
        event_type="delay",
        flight_id="FL-SIM102",
        detail="SIM102 预计离港时间顺延 25 分钟",
        status="awaiting_confirmation",
        occurred_at=BASE_TIME,
        applied_at=BASE_TIME,
        scenario_version_after=2,
        candidate_plan_id="PLAN-CANDIDATE",
    )
    awaiting = _snapshot(
        current_scenario_version=2,
        status=RuntimeStatus.AWAITING_CONFIRMATION,
        revision=4,
        candidate_plan_id="PLAN-CANDIDATE",
        clock=_clock(),
        events=[applied_event],
        guidance=_guidance(RuntimeStatus.AWAITING_CONFIRMATION),
        updated_at=BASE_TIME + timedelta(seconds=1),
    )
    audits = [
        start_audit,
        _audit(
            2,
            "event_batch_applied",
            3,
            status_before=RuntimeStatus.RUNNING,
            status_after=RuntimeStatus.REPLANNING,
        ),
        _audit(
            3,
            "candidate_created",
            4,
            status_before=RuntimeStatus.REPLANNING,
            status_after=RuntimeStatus.AWAITING_CONFIRMATION,
        ),
    ]

    messages = broker.observe(
        awaiting,
        audits,
        emitted_at=BASE_TIME + timedelta(seconds=1),
        monotonic_time=1,
    )

    assert [item.payload.event_type for item in messages] == [
        "event.applied",
        "replan.started",
        "replan.ready",
        "runtime.snapshot",
    ]
    assert [item.revision for item in messages[:3]] == [3, 3, 4]
    assert messages[0].payload.event_ids == ["EVT-001"]
    assert messages[1].payload.trigger.value == "automatic_event"
    assert messages[2].payload.candidate_plan_id == "PLAN-CANDIDATE"


def test_candidate_decisions_publish_accept_and_reject_messages() -> None:
    awaiting = _snapshot(
        status=RuntimeStatus.AWAITING_CONFIRMATION,
        revision=3,
        candidate_plan_id="PLAN-CANDIDATE",
        guidance=_guidance(RuntimeStatus.AWAITING_CONFIRMATION),
    )
    prior_audits = [
        _audit(
            1,
            "candidate_created",
            3,
            status_before=RuntimeStatus.REPLANNING,
            status_after=RuntimeStatus.AWAITING_CONFIRMATION,
        )
    ]

    accepted_broker = _broker()
    accepted_broker.connect(
        awaiting,
        prior_audits,
        last_event_id=None,
        emitted_at=BASE_TIME,
        monotonic_time=0,
    )
    accepted = _snapshot(
        active_plan_id="PLAN-CANDIDATE",
        status=RuntimeStatus.PAUSED,
        revision=4,
        guidance=_guidance(RuntimeStatus.PAUSED),
        updated_at=BASE_TIME + timedelta(seconds=1),
    )
    accepted_messages = accepted_broker.observe(
        accepted,
        [
            *prior_audits,
            _audit(
                2,
                "candidate_accepted",
                4,
                status_before=RuntimeStatus.AWAITING_CONFIRMATION,
                status_after=RuntimeStatus.PAUSED,
            ),
        ],
        emitted_at=BASE_TIME + timedelta(seconds=1),
        monotonic_time=1,
    )
    assert [item.payload.event_type for item in accepted_messages] == [
        "plan.accepted",
        "runtime.snapshot",
    ]

    rejected_broker = _broker()
    rejected_broker.connect(
        awaiting,
        prior_audits,
        last_event_id=None,
        emitted_at=BASE_TIME,
        monotonic_time=0,
    )
    rejected = _snapshot(
        status=RuntimeStatus.PAUSED,
        revision=4,
        guidance=_guidance(RuntimeStatus.PAUSED),
        updated_at=BASE_TIME + timedelta(seconds=1),
    )
    rejected_messages = rejected_broker.observe(
        rejected,
        [
            *prior_audits,
            _audit(
                2,
                "candidate_rejected",
                4,
                status_before=RuntimeStatus.AWAITING_CONFIRMATION,
                status_after=RuntimeStatus.PAUSED,
            ),
        ],
        emitted_at=BASE_TIME + timedelta(seconds=1),
        monotonic_time=1,
    )
    assert [item.payload.event_type for item in rejected_messages] == [
        "plan.rejected",
        "runtime.snapshot",
    ]


def test_manual_replan_and_recoverable_failure_publish_typed_messages() -> None:
    manual_broker = _broker()
    paused = _snapshot()
    manual_broker.connect(
        paused,
        [],
        last_event_id=None,
        emitted_at=BASE_TIME,
        monotonic_time=0,
    )
    awaiting = _snapshot(
        status=RuntimeStatus.AWAITING_CONFIRMATION,
        revision=3,
        candidate_plan_id="PLAN-MANUAL",
        guidance=_guidance(RuntimeStatus.AWAITING_CONFIRMATION),
        updated_at=BASE_TIME + timedelta(seconds=1),
    )
    manual_messages = manual_broker.observe(
        awaiting,
        [
            _audit(
                1,
                "replan_started",
                2,
                status_before=RuntimeStatus.PAUSED,
                status_after=RuntimeStatus.REPLANNING,
            ),
            _audit(
                2,
                "candidate_created",
                3,
                status_before=RuntimeStatus.REPLANNING,
                status_after=RuntimeStatus.AWAITING_CONFIRMATION,
            ),
        ],
        emitted_at=BASE_TIME + timedelta(seconds=1),
        monotonic_time=1,
    )
    assert [item.payload.event_type for item in manual_messages] == [
        "replan.started",
        "replan.ready",
        "runtime.snapshot",
    ]
    assert manual_messages[0].payload.trigger.value == "manual"

    failed_broker = _broker()
    failed_broker.connect(
        paused,
        [],
        last_event_id=None,
        emitted_at=BASE_TIME,
        monotonic_time=0,
    )
    failed_event = EventRuntimeProjection(
        event_id="EVT-FAILED",
        event_type="delay",
        flight_id="FL-SIM102",
        detail="SIM102 预计离港时间顺延 25 分钟",
        status="failed",
        occurred_at=BASE_TIME,
        failure_code="replan_failed",
    )
    failed = _snapshot(
        current_scenario_version=2,
        revision=2,
        events=[failed_event],
        updated_at=BASE_TIME + timedelta(seconds=1),
    )
    failed_messages = failed_broker.observe(
        failed,
        [
            _audit(
                1,
                "replan_failed",
                2,
                status_before=RuntimeStatus.REPLANNING,
                status_after=RuntimeStatus.PAUSED,
            )
        ],
        emitted_at=BASE_TIME + timedelta(seconds=1),
        monotonic_time=1,
    )
    assert [item.payload.event_type for item in failed_messages] == [
        "replan.failed",
        "runtime.snapshot",
    ]
    assert failed_messages[0].payload.error_code == "replan_failed"
    assert failed_messages[0].payload.fallback_available is True


def test_runtime_completion_precedes_task_transition_and_snapshot() -> None:
    broker = _broker()
    pending = TaskRuntimeProjection(
        task_id="TASK-001",
        status="pending",
        assignment_id="ASG-001",
        resource_id="WC-01",
        current_zone_id="SERVICE-CENTER",
        next_transition_at=BASE_TIME + timedelta(minutes=1),
    )
    running = _snapshot(
        status=RuntimeStatus.RUNNING,
        clock=_clock(advancing=True),
        tasks=[pending],
        guidance=_guidance(RuntimeStatus.RUNNING),
    )
    broker.connect(
        running,
        [],
        last_event_id=None,
        emitted_at=BASE_TIME,
        monotonic_time=0,
    )
    completed_task = TaskRuntimeProjection(
        task_id="TASK-001",
        status="completed",
        assignment_id="ASG-001",
        resource_id="WC-01",
        current_zone_id="GATE-A01",
    )
    completed = _snapshot(
        status=RuntimeStatus.COMPLETED,
        revision=2,
        clock=_clock(300),
        tasks=[completed_task],
        guidance=_guidance(RuntimeStatus.COMPLETED),
        updated_at=BASE_TIME + timedelta(minutes=5),
    )
    messages = broker.observe(
        completed,
        [
            _audit(
                1,
                "runtime_completed",
                2,
                status_before=RuntimeStatus.RUNNING,
                status_after=RuntimeStatus.COMPLETED,
            )
        ],
        emitted_at=BASE_TIME + timedelta(minutes=5),
        monotonic_time=300,
    )

    assert [item.payload.event_type for item in messages] == [
        "runtime.completed",
        "task.transition",
        "runtime.snapshot",
    ]
    assert messages[0].payload.completed_task_count == 1


def test_reconnect_replays_exact_messages_and_falls_back_after_eviction() -> None:
    broker = _broker(buffer_size=3)
    paused = _snapshot()
    first = broker.connect(
        paused,
        [],
        last_event_id=None,
        emitted_at=BASE_TIME,
        monotonic_time=0,
    )
    for index in range(1, 5):
        broker.observe(
            paused,
            [],
            emitted_at=BASE_TIME + timedelta(seconds=index * 15),
            monotonic_time=index * 15,
        )

    assert len(broker.buffered_events(SESSION_ID)) == 3
    replay = broker.connect(
        paused,
        [],
        last_event_id="STREAM-TEST-001:4",
        emitted_at=BASE_TIME + timedelta(seconds=61),
        monotonic_time=61,
    )
    assert [item.sequence for item in replay.events] == [5]
    assert replay.snapshot_fallback is False

    evicted = broker.connect(
        paused,
        [],
        last_event_id=first.events[0].sse_id,
        emitted_at=BASE_TIME + timedelta(seconds=62),
        monotonic_time=62,
    )
    assert evicted.snapshot_fallback is True
    assert evicted.events[0].payload.event_type == "runtime.snapshot"
    assert evicted.events[0].sequence == 6

    invalid = broker.connect(
        paused,
        [],
        last_event_id="STREAM-OLD:999",
        emitted_at=BASE_TIME + timedelta(seconds=63),
        monotonic_time=63,
    )
    assert invalid.snapshot_fallback is True
    assert invalid.events[0].sequence == 7


def test_sse_frame_contains_id_event_and_single_line_json() -> None:
    broker = _broker()
    event = broker.connect(
        _snapshot(),
        [],
        last_event_id=None,
        emitted_at=BASE_TIME,
        monotonic_time=0,
    ).events[0]

    frame = encode_sse_event(event)
    lines = frame.splitlines()
    assert lines[0] == "id: STREAM-TEST-001:1"
    assert lines[1] == "event: runtime.snapshot"
    assert lines[2].startswith("data: {")
    assert json.loads(lines[2].removeprefix("data: "))["sse_id"] == event.sse_id
    assert frame.endswith("\n\n")
