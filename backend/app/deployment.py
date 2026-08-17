"""Provider-neutral deployment settings and single-origin HTTP helpers."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .runtime_paths import project_root

PROJECT_ROOT = project_root()
DEFAULT_RUNTIME_DATABASE_PATH = PROJECT_ROOT / ".runtime" / "runtime-sessions.sqlite3"
DEFAULT_MAX_REQUEST_BODY_BYTES = 2 * 1024 * 1024
MIN_MAX_REQUEST_BODY_BYTES = 64 * 1024
MAX_MAX_REQUEST_BODY_BYTES = 16 * 1024 * 1024
DEFAULT_PORT = 8000


@dataclass(frozen=True, slots=True)
class DeploymentSettings:
    runtime_database_path: Path
    frontend_dist_path: Path | None
    max_request_body_bytes: int
    port: int


def _configured_path(
    source: Mapping[str, str],
    name: str,
    *,
    project_root: Path,
    default: Path | None,
) -> Path | None:
    raw_value = source.get(name)
    if raw_value is None:
        return default
    value = raw_value.strip()
    if not value:
        raise ValueError(f"{name} cannot be empty when configured")
    path = Path(value)
    return path if path.is_absolute() else project_root / path


def _bounded_integer(
    source: Mapping[str, str],
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw_value = source.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value.strip())
    except (AttributeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def load_deployment_settings(
    environ: Mapping[str, str] | None = None,
    *,
    project_root: str | Path | None = None,
) -> DeploymentSettings:
    """Load non-secret application settings without reading a dotenv file."""

    source = os.environ if environ is None else environ
    root = Path(project_root).resolve() if project_root is not None else PROJECT_ROOT
    runtime_database_path = _configured_path(
        source,
        "APP_RUNTIME_DATABASE_PATH",
        project_root=root,
        default=root / ".runtime" / "runtime-sessions.sqlite3",
    )
    frontend_dist_path = _configured_path(
        source,
        "APP_FRONTEND_DIST_PATH",
        project_root=root,
        default=None,
    )
    return DeploymentSettings(
        runtime_database_path=runtime_database_path or DEFAULT_RUNTIME_DATABASE_PATH,
        frontend_dist_path=frontend_dist_path,
        max_request_body_bytes=_bounded_integer(
            source,
            "APP_MAX_REQUEST_BODY_BYTES",
            default=DEFAULT_MAX_REQUEST_BODY_BYTES,
            minimum=MIN_MAX_REQUEST_BODY_BYTES,
            maximum=MAX_MAX_REQUEST_BODY_BYTES,
        ),
        port=_bounded_integer(
            source,
            "PORT",
            default=DEFAULT_PORT,
            minimum=1,
            maximum=65_535,
        ),
    )


def _new_request_id() -> str:
    return f"REQ-{uuid4().hex[:12].upper()}"


def _request_id_from_scope(scope: Scope) -> str:
    state = scope.setdefault("state", {})
    request_id = state.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        request_id = _new_request_id()
        state["request_id"] = request_id
    return request_id


class RequestBodyLimitMiddleware:
    """Bound API mutation bodies before request parsing, including chunked input."""

    _BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        if max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be positive")
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = str(scope.get("path", ""))
        method = str(scope.get("method", "GET")).upper()
        if (
            scope.get("type") != "http"
            or method not in self._BODY_METHODS
            or (path != "/api/v1" and not path.startswith("/api/v1/"))
        ):
            await self.app(scope, receive, send)
            return

        content_length = self._content_length(scope)
        if content_length is not None and content_length > self.max_body_bytes:
            await self._send_too_large(scope, receive, send)
            return

        buffered_messages: list[Message] = []
        received_bytes = 0
        while True:
            message = await receive()
            buffered_messages.append(message)
            if message["type"] == "http.disconnect":
                break
            if message["type"] != "http.request":
                continue
            received_bytes += len(message.get("body", b""))
            if received_bytes > self.max_body_bytes:
                await self._send_too_large(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        message_index = 0

        async def replay_receive() -> Message:
            nonlocal message_index
            if message_index < len(buffered_messages):
                message = buffered_messages[message_index]
                message_index += 1
                return message
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay_receive, send)

    @staticmethod
    def _content_length(scope: Scope) -> int | None:
        for name, value in scope.get("headers", []):
            if name.lower() != b"content-length":
                continue
            try:
                parsed = int(value.decode("ascii"))
            except (UnicodeDecodeError, ValueError):
                return None
            return max(parsed, 0)
        return None

    async def _send_too_large(self, scope: Scope, receive: Receive, send: Send) -> None:
        request_id = _request_id_from_scope(scope)
        response = JSONResponse(
            status_code=413,
            content={
                "error": {
                    "code": "request_too_large",
                    "message": "请求体超过部署允许的大小上限",
                    "details": [],
                    "request_id": request_id,
                }
            },
            headers={
                "X-Request-ID": request_id,
                "Connection": "close",
            },
        )
        await response(scope, receive, send)


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _static_response(file_path: Path, relative_path: str) -> FileResponse:
    if relative_path == "index.html" or relative_path == "demo-output.json":
        cache_control = "no-store"
    elif relative_path.startswith("assets/"):
        cache_control = "public, max-age=31536000, immutable"
    else:
        cache_control = "public, max-age=3600"
    return FileResponse(
        file_path,
        headers={
            "Cache-Control": cache_control,
            "X-Content-Type-Options": "nosniff",
        },
    )


def register_single_origin_frontend(
    application: FastAPI,
    frontend_dist_path: str | Path,
) -> None:
    """Serve one Vite build after API routes without masking API errors."""

    root = Path(frontend_dist_path).resolve()
    index_path = root / "index.html"
    if not index_path.is_file():
        raise RuntimeError("configured frontend build does not contain index.html")

    @application.api_route(
        "/{full_path:path}",
        methods=["GET", "HEAD"],
        include_in_schema=False,
        name="single_origin_frontend",
    )
    async def serve_frontend(full_path: str) -> FileResponse:
        normalized = full_path.replace("\\", "/").lstrip("/")
        if normalized == "api" or normalized.startswith("api/"):
            raise StarletteHTTPException(status_code=404)

        candidate = (root / normalized).resolve()
        if normalized and _is_within(candidate, root) and candidate.is_file():
            relative_path = candidate.relative_to(root).as_posix()
            return _static_response(candidate, relative_path)

        if normalized and Path(normalized).suffix:
            raise StarletteHTTPException(status_code=404)
        return _static_response(index_path, "index.html")
