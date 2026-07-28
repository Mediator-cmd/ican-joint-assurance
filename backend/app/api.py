"""Versioned HTTP routes for the joint-assurance service."""

from __future__ import annotations

from typing import Annotated, Never

from fastapi import APIRouter, Depends, Query, Request, status

from .api_models import (
    ApiErrorDetail,
    ApiErrorResponse,
    HealthResponse,
    ScenarioListResponse,
    ScenarioRecord,
)
from .errors import ApiError
from .models import Scenario
from .repository import (
    ScenarioAlreadyExistsError,
    ScenarioNotFoundError,
    ScenarioVersionNotFoundError,
)
from .services import ScenarioService


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
    summary="Check whether the API process is ready",
)
def get_health() -> HealthResponse:
    return HealthResponse(version=APP_VERSION)


def get_scenario_service(request: Request) -> ScenarioService:
    return request.app.state.scenario_service


def _raise_scenario_error(error: Exception) -> Never:
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
        _raise_scenario_error(error)


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
        _raise_scenario_error(error)
