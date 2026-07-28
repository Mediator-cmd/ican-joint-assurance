"""Versioned HTTP routes for the joint-assurance service."""

from __future__ import annotations

from fastapi import APIRouter

from .api_models import ApiErrorResponse, HealthResponse


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
