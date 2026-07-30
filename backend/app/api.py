"""Versioned HTTP routes for the joint-assurance service."""

from __future__ import annotations

from typing import Annotated, Any, Never

from fastapi import APIRouter, Depends, Query, Request, status

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
from .services import ScenarioNotReadyForPlanningError, ScenarioService


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


def _raise_domain_error(error: Exception) -> Never:
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
    raise error


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
