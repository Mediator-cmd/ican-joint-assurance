from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from backend.app.demo_export import SAFETY_NOTICE
from backend.app.models import FlightStatus
from backend.app.runtime_models import (
    CandidateDecisionRequest,
    CreateRuntimeSessionRequest,
    EventRuntimeProjection,
    FlightRuntimeProjection,
    ResetRuntimeSessionRequest,
    ResourceRuntimeProjection,
    ResourceRuntimeStatus,
    RuntimeClockSnapshot,
    RuntimeEventStatus,
    RuntimeGuidance,
    RuntimePosition,
    RuntimeRevisionRequest,
    RuntimeSessionListResponse,
    RuntimeSessionSnapshot,
    RuntimeSessionSummary,
    RuntimeStatus,
    SetRuntimeSpeedRequest,
    SimulationSpeed,
    TaskRuntimeProjection,
    TaskRuntimeStatus,
)
from backend.app.runtime_stream_models import (
    EventAppliedStreamPayload,
    HeartbeatStreamPayload,
    ReplanReadyStreamPayload,
    RuntimeStreamEvent,
    RuntimeTickStreamPayload,
    TaskTransitionStreamPayload,
)


BASE_TIME = datetime(2026, 7, 30, 8, 0, tzinfo=timezone(timedelta(hours=8)))


def _clock(*, advancing: bool = False) -> RuntimeClockSnapshot:
    return RuntimeClockSnapshot(
        simulation_time=BASE_TIME,
        server_time=BASE_TIME,
        speed=SimulationSpeed.DEMO,
        is_advancing=advancing,
        next_boundary_at=BASE_TIME + timedelta(minutes=1),
    )


def _guidance() -> RuntimeGuidance:
    return RuntimeGuidance(
        headline="运行已暂停",
        detail="请检查当前方案后继续。",
        action_required=True,
        recommended_action="确认当前状态并继续运行",
    )


def _snapshot(**updates) -> RuntimeSessionSnapshot:
    payload = {
        "session_id": "RUN-DEMO-001",
        "scenario_id": "SCN-TERMINAL-DISTURBANCE-01",
        "initial_scenario_version": 1,
        "current_scenario_version": 1,
        "initial_plan_id": "PLAN-BASELINE",
        "active_plan_id": "PLAN-BASELINE",
        "candidate_plan_id": None,
        "status": RuntimeStatus.PAUSED,
        "revision": 1,
        "clock": _clock(),
        "tasks": [],
        "resources": [],
        "flights": [],
        "events": [],
        "guidance": _guidance(),
        "failure": None,
        "created_at": BASE_TIME,
        "updated_at": BASE_TIME,
    }
    payload.update(updates)
    return RuntimeSessionSnapshot.model_validate(payload)


def test_runtime_requests_freeze_revision_and_speed_contract() -> None:
    created = CreateRuntimeSessionRequest(
        scenario_id="SCN-TERMINAL-DISTURBANCE-01",
        scenario_version=1,
        active_plan_id="PLAN-BASELINE",
        speed=15,
    )
    speed = SetRuntimeSpeedRequest(expected_revision=3, speed=5)
    decision = CandidateDecisionRequest(
        expected_revision=4,
        candidate_plan_id="PLAN-CANDIDATE",
        reason="采用新的资源安排",
    )

    assert created.speed is SimulationSpeed.DEMO
    assert speed.speed is SimulationSpeed.FAST
    assert decision.expected_revision == 4

    with pytest.raises(ValidationError):
        SetRuntimeSpeedRequest(expected_revision=1, speed=2)
    with pytest.raises(ValidationError):
        RuntimeRevisionRequest(expected_revision=0)
    with pytest.raises(ValidationError):
        ResetRuntimeSessionRequest(expected_revision=1, confirm_reset=False)


def test_runtime_session_list_supports_persisted_session_recovery() -> None:
    summary = RuntimeSessionSummary(
        session_id="RUN-DEMO-001",
        scenario_id="SCN-TERMINAL-DISTURBANCE-01",
        current_scenario_version=2,
        active_plan_id="PLAN-BASELINE",
        status="awaiting_confirmation",
        revision=4,
        simulation_time=BASE_TIME + timedelta(minutes=3),
        speed=15,
        action_required=True,
        updated_at=BASE_TIME + timedelta(minutes=3),
    )
    response = RuntimeSessionListResponse(items=[summary], total=1, offset=0, limit=20)

    assert response.items[0].status_label == "等待确认新方案"
    assert response.storage_scope == "sqlite"
    assert response.safety_notice == SAFETY_NOTICE

    with pytest.raises(ValidationError):
        RuntimeSessionListResponse(items=[summary], total=0, offset=0, limit=20)
    with pytest.raises(ValidationError):
        RuntimeSessionListResponse(items=[summary, summary], total=2, offset=0, limit=20)


def test_runtime_clock_requires_timezone_and_forward_boundary() -> None:
    with pytest.raises(ValidationError):
        RuntimeClockSnapshot(
            simulation_time=BASE_TIME.replace(tzinfo=None),
            server_time=BASE_TIME,
            speed=1,
            is_advancing=False,
        )
    with pytest.raises(ValidationError):
        RuntimeClockSnapshot(
            simulation_time=BASE_TIME,
            server_time=BASE_TIME,
            speed=1,
            is_advancing=False,
            next_boundary_at=BASE_TIME - timedelta(seconds=1),
        )


def test_task_projection_exposes_stable_labels_and_lock_semantics() -> None:
    pending = TaskRuntimeProjection(
        task_id="TASK-001",
        status="pending",
        assignment_id="ASG-001",
        resource_id="WC-01",
        current_zone_id="GATE-W03",
        next_transition_at=BASE_TIME + timedelta(minutes=1),
    )
    active = pending.model_copy(update={"status": TaskRuntimeStatus.EN_ROUTE})

    assert pending.status_label == "待出发"
    assert pending.is_locked is False
    assert active.status_label == "前往服务点"
    assert active.is_locked is True


def test_task_projection_rejects_ambiguous_assignment_states() -> None:
    with pytest.raises(ValidationError):
        TaskRuntimeProjection(task_id="TASK-001", status="in_service")
    with pytest.raises(ValidationError):
        TaskRuntimeProjection(
            task_id="TASK-001",
            status="unassigned",
            assignment_id="ASG-001",
            resource_id="WC-01",
        )
    with pytest.raises(ValidationError):
        TaskRuntimeProjection(task_id="TASK-001", status="affected")
    with pytest.raises(ValidationError):
        TaskRuntimeProjection(
            task_id="TASK-001",
            status="affected",
            affected_by_event_ids=["EVT-SIM102-DELAY"],
        )


def test_resource_projection_distinguishes_stationary_and_moving_positions() -> None:
    moving = ResourceRuntimeProjection(
        resource_id="WC-01",
        status="moving",
        position=RuntimePosition(
            from_zone_id="SERVICE-CENTER",
            to_zone_id="GATE-W03",
            progress_pct=50,
        ),
        current_task_id="TASK-001",
        next_available_at=BASE_TIME + timedelta(minutes=10),
    )

    assert moving.status_label == "前往任务点"
    assert moving.position.progress_pct == 50

    with pytest.raises(ValidationError):
        ResourceRuntimeProjection(
            resource_id="WC-01",
            status="moving",
            position=RuntimePosition(from_zone_id="SERVICE-CENTER"),
            current_task_id="TASK-001",
        )
    with pytest.raises(ValidationError):
        RuntimePosition(from_zone_id="SERVICE-CENTER", progress_pct=1)


def test_event_projection_requires_evidence_for_terminal_states() -> None:
    event = EventRuntimeProjection(
        event_id="EVT-SIM102-DELAY",
        status="awaiting_confirmation",
        occurred_at=BASE_TIME,
        applied_at=BASE_TIME,
        scenario_version_after=2,
        candidate_plan_id="PLAN-CANDIDATE",
    )

    assert event.status_label == "方案待确认"

    with pytest.raises(ValidationError):
        EventRuntimeProjection(
            event_id="EVT-SIM102-DELAY",
            status=RuntimeEventStatus.AWAITING_CONFIRMATION,
            occurred_at=BASE_TIME,
            applied_at=BASE_TIME,
            scenario_version_after=2,
        )
    with pytest.raises(ValidationError):
        EventRuntimeProjection(
            event_id="EVT-SIM102-DELAY",
            status=RuntimeEventStatus.FAILED,
            occurred_at=BASE_TIME,
        )
    with pytest.raises(ValidationError):
        EventRuntimeProjection(
            event_id="EVT-SIM102-DELAY",
            status=RuntimeEventStatus.PENDING,
            occurred_at=BASE_TIME,
            candidate_plan_id="PLAN-CANDIDATE",
        )
    with pytest.raises(ValidationError):
        EventRuntimeProjection(
            event_id="EVT-SIM102-DELAY",
            status=RuntimeEventStatus.PENDING,
            occurred_at=BASE_TIME,
            failure_code="unexpected_failure",
        )


def test_snapshot_freezes_candidate_clock_and_safety_invariants() -> None:
    awaiting = _snapshot(
        current_scenario_version=2,
        status=RuntimeStatus.AWAITING_CONFIRMATION,
        candidate_plan_id="PLAN-CANDIDATE",
        revision=4,
    )

    assert awaiting.status_label == "等待确认新方案"
    assert awaiting.safety_notice == SAFETY_NOTICE
    assert awaiting.model_dump(mode="json")["storage_scope"] == "sqlite"

    with pytest.raises(ValidationError):
        _snapshot(status=RuntimeStatus.AWAITING_CONFIRMATION)
    with pytest.raises(ValidationError):
        _snapshot(status=RuntimeStatus.PAUSED, candidate_plan_id="PLAN-CANDIDATE")
    with pytest.raises(ValidationError):
        _snapshot(status=RuntimeStatus.RUNNING, clock=_clock(advancing=False))


def test_snapshot_rejects_duplicate_projection_ids() -> None:
    task = TaskRuntimeProjection(
        task_id="TASK-001",
        status="unassigned",
    )
    with pytest.raises(ValidationError):
        _snapshot(tasks=[task, task])


def test_stream_sequence_is_independent_from_durable_revision() -> None:
    tick = RuntimeStreamEvent(
        stream_id="STREAM-DEMO-001",
        sequence=10,
        session_id="RUN-DEMO-001",
        revision=3,
        emitted_at=BASE_TIME,
        payload=RuntimeTickStreamPayload(clock=_clock(advancing=True)),
    )
    heartbeat = RuntimeStreamEvent(
        stream_id="STREAM-DEMO-001",
        sequence=11,
        session_id="RUN-DEMO-001",
        revision=3,
        emitted_at=BASE_TIME + timedelta(seconds=1),
        payload=HeartbeatStreamPayload(server_time=BASE_TIME + timedelta(seconds=1)),
    )

    assert tick.revision == heartbeat.revision == 3
    assert tick.sse_id == "STREAM-DEMO-001:10"
    assert heartbeat.sse_id == "STREAM-DEMO-001:11"


def test_stream_payload_uses_discriminator_and_rejects_noop_transition() -> None:
    transition = RuntimeStreamEvent.model_validate(
        {
            "stream_id": "STREAM-DEMO-001",
            "sequence": 1,
            "session_id": "RUN-DEMO-001",
            "revision": 2,
            "emitted_at": BASE_TIME,
            "payload": {
                "event_type": "task.transition",
                "task_id": "TASK-001",
                "previous_status": "pending",
                "current_status": "en_route",
                "transition_at": BASE_TIME,
            },
        }
    )

    assert isinstance(transition.payload, TaskTransitionStreamPayload)

    with pytest.raises(ValidationError):
        TaskTransitionStreamPayload(
            task_id="TASK-001",
            previous_status="pending",
            current_status="pending",
            transition_at=BASE_TIME,
        )


def test_event_batch_and_candidate_contracts_reject_invalid_results() -> None:
    applied = EventAppliedStreamPayload(
        event_ids=["EVT-001", "EVT-002"],
        scenario_version_before=1,
        scenario_version_after=2,
    )
    ready = ReplanReadyStreamPayload(
        active_plan_id="PLAN-BASELINE",
        candidate_plan_id="PLAN-CANDIDATE",
        affected_task_ids=["TASK-001"],
    )

    assert applied.scenario_version_after == 2
    assert ready.violation_count == 0

    with pytest.raises(ValidationError):
        EventAppliedStreamPayload(
            event_ids=["EVT-001"],
            scenario_version_before=1,
            scenario_version_after=3,
        )
    with pytest.raises(ValidationError):
        ReplanReadyStreamPayload(
            active_plan_id="PLAN-SAME",
            candidate_plan_id="PLAN-SAME",
        )


def test_runtime_snapshot_schema_contains_frontend_contract_fields() -> None:
    schema = RuntimeSessionSnapshot.model_json_schema(mode="serialization")
    properties = schema["properties"]

    assert {
        "session_id",
        "current_scenario_version",
        "active_plan_id",
        "candidate_plan_id",
        "status",
        "revision",
        "clock",
        "tasks",
        "resources",
        "flights",
        "events",
        "guidance",
        "status_label",
        "safety_notice",
    } <= set(properties)


def test_flight_projection_requires_aware_estimated_departure() -> None:
    flight = FlightRuntimeProjection(
        flight_id="FL-SIM102",
        status=FlightStatus.DELAYED,
        estimated_departure=BASE_TIME + timedelta(hours=1),
        gate_id="GATE-W03",
        last_event_id="EVT-SIM102-DELAY",
    )
    assert flight.status is FlightStatus.DELAYED

    with pytest.raises(ValidationError):
        FlightRuntimeProjection(
            flight_id="FL-SIM102",
            status=FlightStatus.DELAYED,
            estimated_departure=BASE_TIME.replace(tzinfo=None),
            gate_id="GATE-W03",
        )
