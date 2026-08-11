from __future__ import annotations

import logging
from typing import Annotated

import pytest
from fastapi import Query
from fastapi.testclient import TestClient

from backend.app.demo_export import SAFETY_NOTICE
from backend.app.errors import ApiError
from backend.app.main import create_app


def test_health_reports_versioned_service_status() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "service": "joint-assurance-api",
        "version": "0.1.0",
        "api_version": "v1",
        "status": "ok",
    }
    assert response.headers["X-Request-ID"].startswith("REQ-")


def test_openapi_document_contains_health_route() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/openapi.json")

    assert response.status_code == 200
    document = response.json()
    assert document["info"]["title"] == "联保智调业务 API"
    assert SAFETY_NOTICE in document["info"]["description"]
    assert {tag["name"] for tag in document["tags"]} == {
        "system",
        "scenarios",
        "events",
        "plans",
        "audit",
        "runtime",
        "assistant",
        "spatial",
    }
    assert "/api/v1/health" in document["paths"]
    assert "/api/v1/assistant/event-drafts" in document["paths"]
    assert (
        document["paths"]["/api/v1/health"]["get"]["summary"]
        == "检查业务 API 是否就绪"
    )
    validation_schema = document["paths"]["/api/v1/health"]["get"]["responses"]["422"]
    assert validation_schema["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ApiErrorResponse"
    }


def test_unknown_route_uses_unified_error_response() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/missing")

    payload = response.json()
    assert response.status_code == 404
    assert payload["error"]["code"] == "not_found"
    assert payload["error"]["details"] == []
    assert payload["error"]["request_id"] == response.headers["X-Request-ID"]


def test_request_validation_uses_unified_error_response() -> None:
    app = create_app()

    @app.get("/_test/validation")
    def validation_probe(limit: Annotated[int, Query(ge=1)]) -> dict[str, int]:
        return {"limit": limit}

    with TestClient(app) as client:
        response = client.get("/_test/validation", params={"limit": 0})

    payload = response.json()
    assert response.status_code == 422
    assert payload["error"]["code"] == "validation_error"
    assert len(payload["error"]["details"]) == 1
    detail = payload["error"]["details"][0]
    assert detail["location"] == ["query", "limit"]
    assert detail["type"] == "greater_than_equal"
    assert detail["message"]
    assert payload["error"]["request_id"] == response.headers["X-Request-ID"]


def test_application_error_preserves_safe_business_code() -> None:
    app = create_app()

    @app.get("/_test/conflict")
    def conflict_probe() -> None:
        raise ApiError(409, "version_conflict", "场景版本冲突")

    with TestClient(app) as client:
        response = client.get("/_test/conflict")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "version_conflict"
    assert response.json()["error"]["message"] == "场景版本冲突"


def test_unexpected_error_is_redacted(caplog: pytest.LogCaptureFixture) -> None:
    app = create_app()
    caplog.set_level(logging.ERROR, logger="backend.app.main")

    @app.get("/_test/failure")
    def failure_probe() -> None:
        raise RuntimeError("E:/private-path/secret.txt")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/_test/failure")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "private-path" not in response.text
    assert "private-path" not in caplog.text
    assert "exception_type=RuntimeError" in caplog.text
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
