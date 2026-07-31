"""FastAPI application factory and shared HTTP error handling."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable
from uuid import uuid4
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api import APP_VERSION, router
from .api_models import ApiErrorBody, ApiErrorDetail, ApiErrorResponse
from .demo_export import DEMO_SCENARIO_PATH, SAFETY_NOTICE
from .errors import ApiError
from .repository import InMemoryScenarioRepository
from .runtime_repository import SQLiteRuntimeSessionRepository
from .runtime_services import RuntimeSessionService
from .scenario_loader import load_scenario
from .services import ScenarioService


logger = logging.getLogger(__name__)


OPENAPI_DESCRIPTION = f"""
联保智调的版本化业务接口，用于匿名化仿真场景的导入、事件应用、确定性规划、方案比较与审计查询。

建议按以下顺序操作：

1. 查询或导入场景；
2. 在当前版本生成 FIFO 原规则方案；
3. 携带 `expected_version` 应用结构化事件；
4. 在新版本生成 CP-SAT 系统优化建议；
5. 比较两套已保存方案并查询审计记录。

事件应用和计划创建受期望版本与重复提交检查保护；方案比较会校验两套方案均已保存、互不相同且属于请求场景。M4-1 运行会话控制状态保存在 SQLite，服务重启后可从列表找回并以安全暂停状态继续；场景与普通方案仍沿用 M3 进程内仓库。

**安全边界：{SAFETY_NOTICE}**
""".strip()

OPENAPI_TAGS = [
    {
        "name": "system",
        "description": "健康检查与不修改运行状态的确定性演示数据。",
    },
    {
        "name": "scenarios",
        "description": "导入、列出并查询匿名化仿真场景及其历史版本。",
    },
    {
        "name": "events",
        "description": "按期望版本应用结构化扰动，并生成新的不可变场景版本。",
    },
    {
        "name": "plans",
        "description": "生成、查询和比较 FIFO 原规则方案与 CP-SAT 系统优化建议。",
    },
    {
        "name": "audit",
        "description": "查询不含个人信息、原始请求和本机路径的场景审计时间线。",
    },
    {
        "name": "runtime",
        "description": "创建、找回并控制具有后端权威仿真时钟的持久运行会话。",
    },
]


DEFAULT_RUNTIME_DATABASE_PATH = (
    Path(__file__).resolve().parents[2] / ".runtime" / "runtime-sessions.sqlite3"
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
    runtime_repository: SQLiteRuntimeSessionRepository | None = None,
    runtime_database_path: str | Path | None = None,
    wall_clock: Callable[[], datetime] | None = None,
    monotonic_clock: Callable[[], float] | None = None,
    runtime_session_id_factory: Callable[[], str] | None = None,
    recover_runtime_sessions: bool = True,
) -> FastAPI:
    scenario_repository = repository or InMemoryScenarioRepository()
    if seed_demo:
        demo_scenario = load_scenario(DEMO_SCENARIO_PATH)
        if demo_scenario.scenario_id not in scenario_repository.list_scenario_ids():
            scenario_repository.create_scenario(demo_scenario)

    durable_runtime_repository = runtime_repository or SQLiteRuntimeSessionRepository(
        runtime_database_path or DEFAULT_RUNTIME_DATABASE_PATH
    )
    runtime_service = RuntimeSessionService(
        scenario_repository,
        durable_runtime_repository,
        wall_clock=wall_clock,
        monotonic_clock=monotonic_clock,
        session_id_factory=runtime_session_id_factory,
        recover_on_startup=recover_runtime_sessions,
    )

    application = FastAPI(
        title="联保智调业务 API",
        description=OPENAPI_DESCRIPTION,
        version=APP_VERSION,
        docs_url="/docs",
        openapi_url="/api/v1/openapi.json",
        redoc_url=None,
        openapi_tags=OPENAPI_TAGS,
    )
    application.state.scenario_repository = scenario_repository
    application.state.scenario_service = ScenarioService(scenario_repository)
    application.state.runtime_repository = durable_runtime_repository
    application.state.runtime_service = runtime_service

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
