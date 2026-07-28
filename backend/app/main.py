"""FastAPI application factory and shared HTTP error handling."""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api import APP_VERSION, router
from .api_models import ApiErrorBody, ApiErrorDetail, ApiErrorResponse
from .errors import ApiError
from .repository import InMemoryScenarioRepository
from .scenario_loader import load_scenario
from .services import ScenarioService


logger = logging.getLogger(__name__)
DEMO_SCENARIO_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "scenarios"
    / "terminal-disturbance-demo.json"
)


def _new_request_id() -> str:
    return f"REQ-{uuid4().hex[:12].upper()}"


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or _new_request_id()


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: list[ApiErrorDetail] | None = None,
) -> JSONResponse:
    payload = ApiErrorResponse(
        error=ApiErrorBody(
            code=code,
            message=message,
            details=details or [],
            request_id=_request_id(request),
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
        headers={"X-Request-ID": payload.error.request_id},
    )


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return _error_response(
            request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        details = [
            ApiErrorDetail(
                location=[part for part in error["loc"] if isinstance(part, (str, int))],
                message=error["msg"],
                type=error["type"],
            )
            for error in exc.errors()
        ]
        return _error_response(
            request,
            status_code=422,
            code="validation_error",
            message="请求数据校验失败",
            details=details,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        if exc.status_code == 404:
            return _error_response(
                request,
                status_code=404,
                code="not_found",
                message="请求的接口不存在",
            )
        return _error_response(
            request,
            status_code=exc.status_code,
            code=f"http_{exc.status_code}",
            message="请求无法完成",
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = _request_id(request)
        logger.error(
            "Unhandled API error request_id=%s exception_type=%s",
            request_id,
            type(exc).__name__,
        )
        return _error_response(
            request,
            status_code=500,
            code="internal_error",
            message="服务暂时无法完成请求",
        )


def create_app(
    repository: InMemoryScenarioRepository | None = None,
    seed_demo: bool = True,
) -> FastAPI:
    scenario_repository = repository or InMemoryScenarioRepository()
    if seed_demo:
        demo_scenario = load_scenario(DEMO_SCENARIO_PATH)
        if demo_scenario.scenario_id not in scenario_repository.list_scenario_ids():
            scenario_repository.create_scenario(demo_scenario)

    application = FastAPI(
        title="联保智调业务 API",
        description="教学仿真与辅助决策接口，不构成真实机场运行指令。",
        version=APP_VERSION,
        docs_url="/docs",
        openapi_url="/api/v1/openapi.json",
        redoc_url=None,
    )
    application.state.scenario_repository = scenario_repository
    application.state.scenario_service = ScenarioService(scenario_repository)

    @application.middleware("http")
    async def add_request_id(request: Request, call_next):
        request.state.request_id = _new_request_id()
        response = await call_next(request)
        response.headers.setdefault("X-Request-ID", request.state.request_id)
        return response

    _register_exception_handlers(application)
    application.include_router(router)
    return application


app = create_app()
