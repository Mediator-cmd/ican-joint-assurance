"""Versioned HTTP routes for the joint-assurance service."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Annotated, Any, Never

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from .ai_models import (
    EventDraftRequest,
    EventDraftResponse,
    EventDraftSubmissionRequest,
)
from .ai_services import (
    AssistantContextNotFoundError,
    AssistantRevisionConflictError,
    AssistantVersionConflictError,
    EventAssistantService,
)
from .api_models import (
    ApplyEventsRequest,
    ApiErrorDetail,
    ApiErrorResponse,
    ComparePlansRequest,
    CreatePlanRequest,
    HealthResponse,
    PlanAlgorithm,
    PlanComparison,
    PlanListResponse,
    PlanRecord,
    ScenarioListResponse,
    ScenarioRecord,
)
from .audit_models import AuditRecord
from .demo_export import build_default_demo_payload
from .errors import ApiError
from .models import Scenario
from .repository import (
    EventAlreadyAppliedError,
    EventAlreadyRegisteredError,
    EventNotFoundError,
    InvalidRevisionError,
    InvalidPlanComparisonError,
    PlanAlreadyExistsError,
    PlanNotFoundError,
    ScenarioAlreadyExistsError,
    ScenarioNotFoundError,
    ScenarioVersionNotFoundError,
    VersionConflictError,
)
from .runtime_models import (
    CandidateDecisionRequest,
    CreateRuntimeSessionRequest,
    ReplanRuntimeSessionRequest,
    ResetRuntimeSessionRequest,
    RuntimeRevisionRequest,
    RuntimeSessionListResponse,
    RuntimeSessionSnapshot,
    RuntimeStatus,
    SetRuntimeSpeedRequest,
)
from .runtime_repository import (
    RuntimePersistenceError,
    RuntimeRevisionConflictError,
    RuntimeSessionAlreadyExistsError,
    RuntimeSessionNotFoundError,
)
from .runtime_services import (
    RuntimeCandidateMismatchError,
    RuntimeEventAlreadyRegisteredError,
    RuntimeEventNotApplicableError,
    RuntimeEventTimeConflictError,
    RuntimeInvalidTransitionError,
    RuntimePlanHasViolationsError,
    RuntimePlanNotFoundError,
    RuntimePlanScenarioMismatchError,
    RuntimePlanVersionMismatchError,
    RuntimeScenarioClassificationError,
    RuntimeSessionService,
)
from .runtime_planning import RuntimePlanningError
from .runtime_stream import RuntimeStreamBroker, encode_sse_event
from .services import (
    PlanningObjectiveNotSupportedError,
    ScenarioNotReadyForPlanningError,
    ScenarioService,
)


APP_VERSION = "0.1.0"

COMMON_ERROR_RESPONSES = {
    400: {"model": ApiErrorResponse, "description": "Invalid business request"},
    404: {"model": ApiErrorResponse, "description": "Resource not found"},
    409: {"model": ApiErrorResponse, "description": "State conflict"},
    422: {"model": ApiErrorResponse, "description": "Request validation failed"},
    500: {"model": ApiErrorResponse, "description": "Unexpected service error"},
}

router = APIRouter(prefix="/api/v1", responses=COMMON_ERROR_RESPONSES)


@router.get(
    "/health",
    response_model=HealthResponse,
    tags=["system"],
    summary="检查业务 API 是否就绪",
)
def get_health() -> HealthResponse:
    return HealthResponse(version=APP_VERSION)


@router.get(
    "/demo",
    tags=["system"],
    summary="提供与离线网站兼容的确定性演示数据",
)
def get_demo() -> dict[str, Any]:
    return build_default_demo_payload()


def get_scenario_service(request: Request) -> ScenarioService:
    return request.app.state.scenario_service


def get_runtime_service(request: Request) -> RuntimeSessionService:
    return request.app.state.runtime_service


def get_runtime_stream_broker(request: Request) -> RuntimeStreamBroker:
    return request.app.state.runtime_stream_broker


def get_event_assistant_service(request: Request) -> EventAssistantService:
    return request.app.state.event_assistant_service


def _raise_domain_error(error: Exception) -> Never:
    if isinstance(error, AssistantContextNotFoundError):
        raise ApiError(
            404,
            "assistant_context_not_found",
            "未找到绑定的场景或运行会话，请刷新上下文后重试",
            [
                ApiErrorDetail(
                    location=["body", "context"],
                    message="辅助请求上下文不存在",
                    type="missing_assistant_context",
                )
            ],
        ) from error
    if isinstance(error, AssistantVersionConflictError):
        raise ApiError(
            409,
            "assistant_version_conflict",
            "场景版本已变化，请刷新场景后重新生成事件草稿",
            [
                ApiErrorDetail(
                    location=["body", "context", "expected_version"],
                    message=(
                        f"提交版本 {error.expected_version}，"
                        f"当前版本 {error.current_version}"
                    ),
                    type="stale_assistant_scenario_version",
                )
            ],
        ) from error
    if isinstance(error, AssistantRevisionConflictError):
        raise ApiError(
            409,
            "assistant_revision_conflict",
            "运行状态已变化，请刷新权威快照后重新生成事件草稿",
            [
                ApiErrorDetail(
                    location=["body", "context", "expected_revision"],
                    message=(
                        f"提交修订 {error.expected_revision}，"
                        f"当前修订 {error.current_revision}"
                    ),
                    type="stale_assistant_runtime_revision",
                )
            ],
        ) from error
    if isinstance(error, ScenarioAlreadyExistsError):
        raise ApiError(
            409,
            "scenario_already_exists",
            "场景 ID 已存在，请查询现有场景或使用新的 ID",
            [
                ApiErrorDetail(
                    location=["body", "scenario_id"],
                    message="场景 ID 必须唯一",
                    type="duplicate_scenario_id",
                )
            ],
        ) from error
    if isinstance(error, ScenarioVersionNotFoundError):
        raise ApiError(
            404,
            "scenario_version_not_found",
            "未找到指定场景版本，请查看 available_versions 后重试",
            [
                ApiErrorDetail(
                    location=["query", "version"],
                    message="请求的版本不存在",
                    type="missing_scenario_version",
                )
            ],
        ) from error
    if isinstance(error, ScenarioNotFoundError):
        raise ApiError(
            404,
            "scenario_not_found",
            "未找到该场景，请先查询场景列表确认可用 ID",
            [
                ApiErrorDetail(
                    location=["path", "scenario_id"],
                    message="请求的场景不存在",
                    type="missing_scenario",
                )
            ],
        ) from error
    if isinstance(error, VersionConflictError):
        raise ApiError(
            409,
            "version_conflict",
            (
                f"场景已更新到版本 {error.current_version}；"
                "请刷新场景后，使用最新版本重新操作"
            ),
            [
                ApiErrorDetail(
                    location=["body", "expected_version"],
                    message=(
                        f"提交版本 {error.expected_version}，"
                        f"当前版本 {error.current_version}"
                    ),
                    type="stale_scenario_version",
                )
            ],
        ) from error
    if isinstance(error, EventNotFoundError):
        raise ApiError(
            404,
            "event_not_found",
            "未找到待应用事件，请刷新场景并从 pending_event_ids 中重新选择",
            [
                ApiErrorDetail(
                    location=["body", "event_ids"],
                    message="至少一个事件 ID 不属于当前场景",
                    type="missing_event",
                )
            ],
        ) from error
    if isinstance(error, EventAlreadyAppliedError):
        raise ApiError(
            409,
            "event_already_applied",
            "该事件已经计入场景，不能重复应用；请查看当前版本",
            [
                ApiErrorDetail(
                    location=["body", "event_ids"],
                    message="事件已应用",
                    type="duplicate_event_application",
                )
            ],
        ) from error
    if isinstance(error, EventAlreadyRegisteredError):
        raise ApiError(
            409,
            "event_already_registered",
            "新增事件 ID 已存在；请选择待处理事件或使用新的事件 ID",
            [
                ApiErrorDetail(
                    location=["body", "events"],
                    message="新增事件 ID 与场景事件目录重复",
                    type="duplicate_event_id",
                )
            ],
        ) from error
    if isinstance(error, InvalidRevisionError):
        raise ApiError(
            400,
            "event_batch_not_applicable",
            "这些事件无法一起应用到当前基线，请检查航班、登机口和时间范围",
            [
                ApiErrorDetail(
                    location=["body"],
                    message="事件批次与当前场景状态不一致",
                    type="invalid_event_batch",
                )
            ],
        ) from error
    if isinstance(error, PlanAlreadyExistsError):
        raise ApiError(
            409,
            "plan_already_exists",
            "当前场景版本已经生成过这种方案，请直接查询已有方案",
            [
                ApiErrorDetail(
                    location=["body", "algorithm"],
                    message="同版本同算法方案已存在",
                    type="duplicate_plan",
                )
            ],
        ) from error
    if isinstance(error, PlanNotFoundError):
        raise ApiError(
            404,
            "plan_not_found",
            "未找到该方案，请先查询场景的方案列表确认可用 ID",
            [
                ApiErrorDetail(
                    location=["path", "plan_id"],
                    message="请求的方案不存在",
                    type="missing_plan",
                )
            ],
        ) from error
    if isinstance(error, InvalidPlanComparisonError):
        raise ApiError(
            400,
            "plan_comparison_not_allowed",
            "只能比较当前场景下两套不同的已保存方案",
            [
                ApiErrorDetail(
                    location=["body"],
                    message="基线方案和候选方案必须不同且属于请求场景",
                    type="invalid_plan_comparison",
                )
            ],
        ) from error
    if isinstance(error, ScenarioNotReadyForPlanningError):
        warning_text = "；".join(error.warnings) if error.warnings else "场景缺少可规划任务或资源"
        raise ApiError(
            400,
            "scenario_not_ready_for_planning",
            f"场景暂不能生成方案：{warning_text}",
            [
                ApiErrorDetail(
                    location=["body"],
                    message="请先根据场景 warnings 补全任务和可用资源",
                    type="planning_prerequisite_missing",
                )
            ],
        ) from error
    if isinstance(error, PlanningObjectiveNotSupportedError):
        raise ApiError(
            400,
            "planning_objective_not_supported",
            "该目标不适用于当前规划上下文；最小变更只用于有当前方案的滚动重规划",
            [
                ApiErrorDetail(
                    location=["body", "objective_profile"],
                    message="请选择均衡、关键任务优先或最小等待",
                    type="unsupported_planning_objective",
                )
            ],
        ) from error
    if isinstance(error, RuntimeSessionNotFoundError):
        raise ApiError(
            404,
            "runtime_session_not_found",
            "未找到该运行会话，请从运行会话列表中重新选择",
            [
                ApiErrorDetail(
                    location=["path", "session_id"],
                    message="运行会话不存在",
                    type="missing_runtime_session",
                )
            ],
        ) from error
    if isinstance(error, RuntimePlanNotFoundError):
        raise ApiError(
            404,
            "runtime_plan_not_found",
            "未找到用于创建运行会话的方案，请先生成并保存方案",
            [
                ApiErrorDetail(
                    location=["body", "active_plan_id"],
                    message="初始方案不存在",
                    type="missing_runtime_plan",
                )
            ],
        ) from error
    if isinstance(error, RuntimePlanScenarioMismatchError):
        raise ApiError(
            400,
            "runtime_plan_scenario_mismatch",
            "初始方案不属于请求的仿真场景，请重新选择方案",
            [
                ApiErrorDetail(
                    location=["body", "active_plan_id"],
                    message="方案与场景不匹配",
                    type="runtime_plan_scenario_mismatch",
                )
            ],
        ) from error
    if isinstance(error, RuntimePlanHasViolationsError):
        raise ApiError(
            400,
            "runtime_plan_has_violations",
            "初始方案存在硬约束冲突，不能用于运行仿真",
            [
                ApiErrorDetail(
                    location=["body", "active_plan_id"],
                    message="方案硬约束违规数必须为 0",
                    type="runtime_plan_has_violations",
                )
            ],
        ) from error
    if isinstance(error, RuntimePlanVersionMismatchError):
        raise ApiError(
            409,
            "runtime_plan_version_mismatch",
            "初始方案与请求的场景版本不一致，请使用同一版本的方案",
            [
                ApiErrorDetail(
                    location=["body", "active_plan_id"],
                    message="方案版本与场景版本不一致",
                    type="runtime_plan_version_mismatch",
                )
            ],
        ) from error
    if isinstance(error, RuntimeRevisionConflictError):
        raise ApiError(
            409,
            "runtime_revision_conflict",
            (
                f"运行状态已更新到修订 {error.current_revision}；"
                "请刷新当前会话后重试"
            ),
            [
                ApiErrorDetail(
                    location=["body", "expected_revision"],
                    message=(
                        f"提交修订 {error.expected_revision}，"
                        f"当前修订 {error.current_revision}"
                    ),
                    type="stale_runtime_revision",
                )
            ],
        ) from error
    if isinstance(error, RuntimeInvalidTransitionError):
        raise ApiError(
            409,
            "runtime_invalid_transition",
            "当前运行状态不允许执行该操作，请刷新会话并查看建议操作",
            [
                ApiErrorDetail(
                    location=["path", "session_id"],
                    message=f"状态 {error.status.value} 不允许操作 {error.action}",
                    type="runtime_invalid_transition",
                )
            ],
        ) from error
    if isinstance(error, RuntimeCandidateMismatchError):
        raise ApiError(
            409,
            "runtime_candidate_mismatch",
            "待确认方案已经变化，请刷新会话后再操作",
            [
                ApiErrorDetail(
                    location=["body", "candidate_plan_id"],
                    message=(
                        f"提交候选 {error.submitted_candidate_id}，"
                        f"当前候选 {error.current_candidate_id or '无'}"
                    ),
                    type="stale_runtime_candidate",
                )
            ],
        ) from error
    if isinstance(error, RuntimeEventAlreadyRegisteredError):
        raise ApiError(
            409,
            "runtime_event_already_registered",
            "该事件已经登记到运行会话，不能重复提交",
            [
                ApiErrorDetail(
                    location=["body", "draft", "event", "event_id"],
                    message=f"事件 {error.event_id} 已存在",
                    type="duplicate_runtime_event",
                )
            ],
        ) from error
    if isinstance(error, RuntimeEventTimeConflictError):
        raise ApiError(
            409,
            "runtime_event_time_conflict",
            "事件时间早于当前冻结的仿真时间，请刷新会话后重新生成草稿",
            [
                ApiErrorDetail(
                    location=["body", "draft", "event", "occurred_at"],
                    message="事件不能让权威仿真时钟回退",
                    type="stale_runtime_event_time",
                )
            ],
        ) from error
    if isinstance(error, RuntimeEventNotApplicableError):
        raise ApiError(
            400,
            "runtime_event_not_applicable",
            "复核事件与当前运行场景不一致，请重新生成草稿",
            [
                ApiErrorDetail(
                    location=["body", "draft", "event"],
                    message="航班、登机口或时间不属于当前运行事实",
                    type="invalid_runtime_event",
                )
            ],
        ) from error
    if isinstance(error, RuntimeScenarioClassificationError):
        raise ApiError(
            400,
            "runtime_scenario_not_supported",
            "运行会话只接受合成数据或匿名化回放数据",
            [
                ApiErrorDetail(
                    location=["body", "scenario_id"],
                    message="场景数据分类不适用于教学仿真",
                    type="runtime_scenario_not_supported",
                )
            ],
        ) from error
    if isinstance(
        error,
        (
            RuntimePersistenceError,
            RuntimeSessionAlreadyExistsError,
            RuntimePlanningError,
        ),
    ):
        raise ApiError(
            500,
            "runtime_persistence_error",
            "运行状态暂时无法安全保存，请稍后重试并保留当前页面",
        ) from error
    raise error


@router.post(
    "/assistant/event-drafts",
    response_model=EventDraftResponse,
    tags=["assistant"],
    summary="把中文航班变化解析为待人工复核的事件草稿",
)
def create_event_draft(
    payload: EventDraftRequest,
    service: Annotated[EventAssistantService, Depends(get_event_assistant_service)],
) -> EventDraftResponse:
    try:
        return service.create_event_draft(payload)
    except (
        AssistantContextNotFoundError,
        AssistantVersionConflictError,
        AssistantRevisionConflictError,
        RuntimePersistenceError,
    ) as error:
        _raise_domain_error(error)


@router.post(
    "/assistant/event-drafts/submit",
    response_model=ScenarioRecord | RuntimeSessionSnapshot,
    tags=["assistant"],
    summary="人工确认后把事件草稿提交到权威事件链",
)
def submit_event_draft(
    payload: EventDraftSubmissionRequest,
    service: Annotated[EventAssistantService, Depends(get_event_assistant_service)],
) -> ScenarioRecord | RuntimeSessionSnapshot:
    try:
        return service.submit_event_draft(payload)
    except (
        AssistantContextNotFoundError,
        AssistantVersionConflictError,
        AssistantRevisionConflictError,
        VersionConflictError,
        EventAlreadyAppliedError,
        EventAlreadyRegisteredError,
        InvalidRevisionError,
        RuntimeSessionNotFoundError,
        RuntimeRevisionConflictError,
        RuntimeInvalidTransitionError,
        RuntimeEventAlreadyRegisteredError,
        RuntimeEventTimeConflictError,
        RuntimeEventNotApplicableError,
        RuntimePersistenceError,
        RuntimePlanningError,
    ) as error:
        _raise_domain_error(error)


@router.post(
    "/runtime-sessions",
    response_model=RuntimeSessionSnapshot,
    status_code=status.HTTP_201_CREATED,
    tags=["runtime"],
    summary="创建独立的教学仿真运行会话",
)
def create_runtime_session(
    payload: CreateRuntimeSessionRequest,
    service: Annotated[RuntimeSessionService, Depends(get_runtime_service)],
) -> RuntimeSessionSnapshot:
    try:
        return service.create_session(payload)
    except (
        ScenarioNotFoundError,
        ScenarioVersionNotFoundError,
        RuntimePlanNotFoundError,
        RuntimePlanScenarioMismatchError,
        RuntimePlanVersionMismatchError,
        RuntimePlanHasViolationsError,
        RuntimeScenarioClassificationError,
        RuntimeSessionAlreadyExistsError,
        RuntimePersistenceError,
    ) as error:
        _raise_domain_error(error)


@router.get(
    "/runtime-sessions",
    response_model=RuntimeSessionListResponse,
    tags=["runtime"],
    summary="分页查找已持久化的运行会话",
)
def list_runtime_sessions(
    service: Annotated[RuntimeSessionService, Depends(get_runtime_service)],
    scenario_id: Annotated[str | None, Query(min_length=1)] = None,
    runtime_status: Annotated[RuntimeStatus | None, Query(alias="status")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> RuntimeSessionListResponse:
    try:
        return service.list_sessions(
            scenario_id=scenario_id,
            status=runtime_status,
            offset=offset,
            limit=limit,
        )
    except RuntimePersistenceError as error:
        _raise_domain_error(error)


@router.get(
    "/runtime-sessions/{session_id}",
    response_model=RuntimeSessionSnapshot,
    tags=["runtime"],
    summary="查询权威运行快照",
)
def get_runtime_session(
    session_id: str,
    service: Annotated[RuntimeSessionService, Depends(get_runtime_service)],
) -> RuntimeSessionSnapshot:
    try:
        return service.get_session(session_id)
    except (RuntimeSessionNotFoundError, RuntimePersistenceError) as error:
        _raise_domain_error(error)


@router.get(
    "/runtime-sessions/{session_id}/stream",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "类型化运行事件流",
            "content": {"text/event-stream": {}},
        }
    },
    tags=["runtime"],
    summary="订阅可补发的权威运行事件流",
)
async def stream_runtime_session(
    session_id: str,
    request: Request,
    service: Annotated[RuntimeSessionService, Depends(get_runtime_service)],
    broker: Annotated[RuntimeStreamBroker, Depends(get_runtime_stream_broker)],
) -> StreamingResponse:
    try:
        snapshot = await run_in_threadpool(service.get_session, session_id)
        audits = await run_in_threadpool(
            service.runtime_repository.list_audit_records,
            session_id,
        )
    except (RuntimeSessionNotFoundError, RuntimePersistenceError) as error:
        _raise_domain_error(error)

    initial = broker.connect(
        snapshot,
        audits,
        last_event_id=request.headers.get("last-event-id"),
        emitted_at=datetime.now(timezone.utc),
        monotonic_time=time.monotonic(),
    )

    async def event_source():
        stream_id = initial.stream_id
        cursor = initial.cursor_sequence
        for event in initial.events:
            yield encode_sse_event(event)

        while not await request.is_disconnected():
            await asyncio.sleep(min(1.0, broker.tick_interval_seconds))
            try:
                current = await run_in_threadpool(service.get_session, session_id)
                current_audits = await run_in_threadpool(
                    service.runtime_repository.list_audit_records,
                    session_id,
                )
            except (RuntimeSessionNotFoundError, RuntimePersistenceError):
                return
            batch = broker.poll(
                current,
                current_audits,
                stream_id=stream_id,
                after_sequence=cursor,
                emitted_at=datetime.now(timezone.utc),
                monotonic_time=time.monotonic(),
            )
            stream_id = batch.stream_id
            cursor = batch.cursor_sequence
            for event in batch.events:
                yield encode_sse_event(event)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/runtime-sessions/{session_id}/start",
    response_model=RuntimeSessionSnapshot,
    tags=["runtime"],
    summary="开始或继续推进仿真时钟",
)
def start_runtime_session(
    session_id: str,
    payload: RuntimeRevisionRequest,
    service: Annotated[RuntimeSessionService, Depends(get_runtime_service)],
) -> RuntimeSessionSnapshot:
    try:
        return service.start_session(session_id, payload)
    except (
        RuntimeSessionNotFoundError,
        RuntimeRevisionConflictError,
        RuntimeInvalidTransitionError,
        RuntimePersistenceError,
    ) as error:
        _raise_domain_error(error)


@router.post(
    "/runtime-sessions/{session_id}/pause",
    response_model=RuntimeSessionSnapshot,
    tags=["runtime"],
    summary="暂停并固定当前仿真时间",
)
def pause_runtime_session(
    session_id: str,
    payload: RuntimeRevisionRequest,
    service: Annotated[RuntimeSessionService, Depends(get_runtime_service)],
) -> RuntimeSessionSnapshot:
    try:
        return service.pause_session(session_id, payload)
    except (
        RuntimeSessionNotFoundError,
        RuntimeRevisionConflictError,
        RuntimeInvalidTransitionError,
        RuntimePersistenceError,
    ) as error:
        _raise_domain_error(error)


@router.post(
    "/runtime-sessions/{session_id}/speed",
    response_model=RuntimeSessionSnapshot,
    tags=["runtime"],
    summary="切换仿真时间推进倍速",
)
def set_runtime_session_speed(
    session_id: str,
    payload: SetRuntimeSpeedRequest,
    service: Annotated[RuntimeSessionService, Depends(get_runtime_service)],
) -> RuntimeSessionSnapshot:
    try:
        return service.set_speed(session_id, payload)
    except (
        RuntimeSessionNotFoundError,
        RuntimeRevisionConflictError,
        RuntimeInvalidTransitionError,
        RuntimePersistenceError,
    ) as error:
        _raise_domain_error(error)


@router.post(
    "/runtime-sessions/{session_id}/reset",
    response_model=RuntimeSessionSnapshot,
    tags=["runtime"],
    summary="确认后重置到初始仿真状态",
)
def reset_runtime_session(
    session_id: str,
    payload: ResetRuntimeSessionRequest,
    service: Annotated[RuntimeSessionService, Depends(get_runtime_service)],
) -> RuntimeSessionSnapshot:
    try:
        return service.reset_session(session_id, payload)
    except (
        RuntimeSessionNotFoundError,
        RuntimeRevisionConflictError,
        RuntimeInvalidTransitionError,
        RuntimePersistenceError,
    ) as error:
        _raise_domain_error(error)


@router.post(
    "/runtime-sessions/{session_id}/replan",
    response_model=RuntimeSessionSnapshot,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["runtime"],
    summary="在冻结时刻重新计算未来保障任务",
)
def replan_runtime_session(
    session_id: str,
    payload: ReplanRuntimeSessionRequest,
    service: Annotated[RuntimeSessionService, Depends(get_runtime_service)],
) -> RuntimeSessionSnapshot:
    try:
        return service.replan_session(session_id, payload)
    except (
        RuntimeSessionNotFoundError,
        RuntimeRevisionConflictError,
        RuntimeInvalidTransitionError,
        RuntimePersistenceError,
        RuntimePlanningError,
    ) as error:
        _raise_domain_error(error)


@router.post(
    "/runtime-sessions/{session_id}/candidate/accept",
    response_model=RuntimeSessionSnapshot,
    tags=["runtime"],
    summary="采用当前待确认的滚动候选方案",
)
def accept_runtime_candidate(
    session_id: str,
    payload: CandidateDecisionRequest,
    service: Annotated[RuntimeSessionService, Depends(get_runtime_service)],
) -> RuntimeSessionSnapshot:
    try:
        return service.accept_candidate(session_id, payload)
    except (
        RuntimeSessionNotFoundError,
        RuntimeRevisionConflictError,
        RuntimeInvalidTransitionError,
        RuntimeCandidateMismatchError,
        RuntimePersistenceError,
        RuntimePlanningError,
    ) as error:
        _raise_domain_error(error)


@router.post(
    "/runtime-sessions/{session_id}/candidate/reject",
    response_model=RuntimeSessionSnapshot,
    tags=["runtime"],
    summary="拒绝候选并保留当前执行方案",
)
def reject_runtime_candidate(
    session_id: str,
    payload: CandidateDecisionRequest,
    service: Annotated[RuntimeSessionService, Depends(get_runtime_service)],
) -> RuntimeSessionSnapshot:
    try:
        return service.reject_candidate(session_id, payload)
    except (
        RuntimeSessionNotFoundError,
        RuntimeRevisionConflictError,
        RuntimeInvalidTransitionError,
        RuntimeCandidateMismatchError,
        RuntimePersistenceError,
        RuntimePlanningError,
    ) as error:
        _raise_domain_error(error)


@router.post(
    "/scenarios",
    response_model=ScenarioRecord,
    status_code=status.HTTP_201_CREATED,
    tags=["scenarios"],
    summary="导入并校验一个结构化仿真场景",
)
def import_scenario(
    scenario: Scenario,
    service: Annotated[ScenarioService, Depends(get_scenario_service)],
) -> ScenarioRecord:
    try:
        return service.import_scenario(scenario)
    except ScenarioAlreadyExistsError as error:
        _raise_domain_error(error)


@router.get(
    "/scenarios",
    response_model=ScenarioListResponse,
    tags=["scenarios"],
    summary="列出场景及其可规划状态",
)
def list_scenarios(
    service: Annotated[ScenarioService, Depends(get_scenario_service)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ScenarioListResponse:
    return service.list_scenarios(offset=offset, limit=limit)


@router.get(
    "/scenarios/{scenario_id}",
    response_model=ScenarioRecord,
    tags=["scenarios"],
    summary="查询场景当前或历史版本",
)
def get_scenario(
    scenario_id: str,
    service: Annotated[ScenarioService, Depends(get_scenario_service)],
    version: Annotated[int | None, Query(ge=1)] = None,
) -> ScenarioRecord:
    try:
        return service.get_scenario(scenario_id, version)
    except (ScenarioNotFoundError, ScenarioVersionNotFoundError) as error:
        _raise_domain_error(error)


@router.post(
    "/scenarios/{scenario_id}/events/apply",
    response_model=ScenarioRecord,
    tags=["events"],
    summary="把结构化航班变化计入场景并生成新版本",
)
def apply_scenario_events(
    scenario_id: str,
    payload: ApplyEventsRequest,
    service: Annotated[ScenarioService, Depends(get_scenario_service)],
) -> ScenarioRecord:
    try:
        return service.apply_events(scenario_id, payload)
    except (
        ScenarioNotFoundError,
        VersionConflictError,
        EventNotFoundError,
        EventAlreadyAppliedError,
        EventAlreadyRegisteredError,
        InvalidRevisionError,
    ) as error:
        _raise_domain_error(error)


@router.post(
    "/scenarios/{scenario_id}/plans",
    response_model=PlanRecord,
    status_code=status.HTTP_201_CREATED,
    tags=["plans"],
    summary="生成原规则方案或系统优化建议",
)
def create_plan(
    scenario_id: str,
    payload: CreatePlanRequest,
    service: Annotated[ScenarioService, Depends(get_scenario_service)],
) -> PlanRecord:
    try:
        return service.create_plan(scenario_id, payload)
    except (
        ScenarioNotFoundError,
        VersionConflictError,
        PlanAlreadyExistsError,
        ScenarioNotReadyForPlanningError,
        PlanningObjectiveNotSupportedError,
    ) as error:
        _raise_domain_error(error)


@router.get(
    "/scenarios/{scenario_id}/plans",
    response_model=PlanListResponse,
    tags=["plans"],
    summary="查询场景已有方案及普通语言结论",
)
def list_plans(
    scenario_id: str,
    service: Annotated[ScenarioService, Depends(get_scenario_service)],
    version: Annotated[int | None, Query(ge=1)] = None,
    algorithm: Annotated[PlanAlgorithm | None, Query()] = None,
) -> PlanListResponse:
    try:
        return service.list_plans(
            scenario_id,
            scenario_version=version,
            algorithm=algorithm,
        )
    except (ScenarioNotFoundError, ScenarioVersionNotFoundError) as error:
        _raise_domain_error(error)


@router.get(
    "/plans/{plan_id}",
    response_model=PlanRecord,
    tags=["plans"],
    summary="查询完整方案、解释和人工确认提示",
)
def get_plan(
    plan_id: str,
    service: Annotated[ScenarioService, Depends(get_scenario_service)],
) -> PlanRecord:
    try:
        return service.get_plan(plan_id)
    except PlanNotFoundError as error:
        _raise_domain_error(error)


@router.post(
    "/scenarios/{scenario_id}/comparisons",
    response_model=PlanComparison,
    status_code=status.HTTP_201_CREATED,
    tags=["plans"],
    summary="比较两套已保存方案的核心指标和取舍",
)
def compare_plans(
    scenario_id: str,
    payload: ComparePlansRequest,
    service: Annotated[ScenarioService, Depends(get_scenario_service)],
) -> PlanComparison:
    try:
        return service.compare_plans(scenario_id, payload)
    except (
        ScenarioNotFoundError,
        PlanNotFoundError,
        InvalidPlanComparisonError,
    ) as error:
        _raise_domain_error(error)


@router.get(
    "/scenarios/{scenario_id}/audit-records",
    response_model=list[AuditRecord],
    tags=["audit"],
    summary="查询不含个人信息和原始请求的场景审计时间线",
)
def list_audit_records(
    scenario_id: str,
    service: Annotated[ScenarioService, Depends(get_scenario_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[AuditRecord]:
    try:
        return service.list_audit_records(scenario_id, limit)
    except ScenarioNotFoundError as error:
        _raise_domain_error(error)
