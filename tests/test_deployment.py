from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from backend.app.deployment import (
    DEFAULT_MAX_REQUEST_BODY_BYTES,
    MAX_MAX_REQUEST_BODY_BYTES,
    MIN_MAX_REQUEST_BODY_BYTES,
    load_deployment_settings,
)
from backend.app.main import create_app
from backend.app.production import build_uvicorn_configuration


SCENARIO_ID = "SCN-TERMINAL-DISTURBANCE-01"


def test_deployment_settings_have_safe_defaults_and_resolve_relative_paths(
    tmp_path: Path,
) -> None:
    defaults = load_deployment_settings({}, project_root=tmp_path)

    assert defaults.runtime_database_path == tmp_path / ".runtime" / "runtime-sessions.sqlite3"
    assert defaults.frontend_dist_path is None
    assert defaults.max_request_body_bytes == DEFAULT_MAX_REQUEST_BODY_BYTES
    assert defaults.port == 8000

    configured = load_deployment_settings(
        {
            "APP_RUNTIME_DATABASE_PATH": "state/runtime.sqlite3",
            "APP_FRONTEND_DIST_PATH": "web/dist",
            "APP_MAX_REQUEST_BODY_BYTES": str(MIN_MAX_REQUEST_BODY_BYTES),
            "PORT": "9010",
        },
        project_root=tmp_path,
    )

    assert configured.runtime_database_path == tmp_path / "state" / "runtime.sqlite3"
    assert configured.frontend_dist_path == tmp_path / "web" / "dist"
    assert configured.max_request_body_bytes == MIN_MAX_REQUEST_BODY_BYTES
    assert configured.port == 9010


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("APP_RUNTIME_DATABASE_PATH", " "),
        ("APP_FRONTEND_DIST_PATH", ""),
        ("APP_MAX_REQUEST_BODY_BYTES", "not-a-number"),
        ("APP_MAX_REQUEST_BODY_BYTES", str(MIN_MAX_REQUEST_BODY_BYTES - 1)),
        ("APP_MAX_REQUEST_BODY_BYTES", str(MAX_MAX_REQUEST_BODY_BYTES + 1)),
        ("PORT", "0"),
        ("PORT", "65536"),
    ],
)
def test_deployment_settings_reject_invalid_values(
    tmp_path: Path,
    name: str,
    value: str,
) -> None:
    with pytest.raises(ValueError):
        load_deployment_settings({name: value}, project_root=tmp_path)


def _build_frontend_dist(tmp_path: Path) -> Path:
    frontend_dist = tmp_path / "frontend-dist"
    assets = frontend_dist / "assets"
    assets.mkdir(parents=True)
    (frontend_dist / "index.html").write_text(
        "<!doctype html><html><body><div id='root'>single-origin</div></body></html>",
        encoding="utf-8",
    )
    (frontend_dist / "favicon.svg").write_text("<svg></svg>", encoding="utf-8")
    (frontend_dist / "demo-output.json").write_text("{}", encoding="utf-8")
    (assets / "app-A1B2C3.js").write_text("export {};", encoding="utf-8")
    return frontend_dist


def test_single_origin_serves_frontend_assets_and_preserves_api_json_errors(
    tmp_path: Path,
) -> None:
    app = create_app(
        runtime_database_path=":memory:",
        frontend_dist_path=_build_frontend_dist(tmp_path),
    )

    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        home = client.get("/")
        head = client.head("/")
        frontend_route = client.get("/workspace/dispatch")
        asset = client.get("/assets/app-A1B2C3.js")
        versioned_asset = client.get("/assets/app-A1B2C3.js?v=" + "0" * 64)
        demo = client.get("/demo-output.json")
        missing_asset = client.get("/assets/missing.js")
        missing_api = client.get("/api/v1/missing")

    assert health.status_code == 200
    assert health.headers["content-type"].startswith("application/json")
    assert health.json()["status"] == "ok"

    assert home.status_code == head.status_code == frontend_route.status_code == 200
    assert "single-origin" in home.text
    assert head.content == b""
    assert frontend_route.text == home.text
    assert home.headers["cache-control"] == "no-store"

    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert asset.headers["x-content-type-options"] == "nosniff"
    assert versioned_asset.status_code == 200
    assert versioned_asset.content == asset.content
    assert versioned_asset.headers["cache-control"] == asset.headers["cache-control"]
    assert demo.headers["cache-control"] == "no-store"

    for response in (missing_asset, missing_api):
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")
        assert response.json()["error"]["code"] == "not_found"
        assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


def test_single_origin_requires_a_real_vite_index(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="index.html"):
        create_app(
            runtime_database_path=":memory:",
            frontend_dist_path=tmp_path / "missing-dist",
        )


def test_api_request_body_limit_checks_declared_and_streamed_bytes() -> None:
    limit = MIN_MAX_REQUEST_BODY_BYTES
    app = create_app(runtime_database_path=":memory:", max_request_body_bytes=limit)

    @app.post("/api/v1/_test/body-size")
    async def body_size(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    marker = b"PRIVATE-BODY-MARKER"
    within_limit = b"x" * limit
    over_limit = marker + b"x" * limit

    def streamed_body():
        yield marker
        yield b"x" * limit

    with TestClient(app) as client:
        accepted = client.post("/api/v1/_test/body-size", content=within_limit)
        declared = client.post("/api/v1/_test/body-size", content=over_limit)
        streamed = client.post(
            "/api/v1/_test/body-size",
            content=streamed_body(),
            headers={"Transfer-Encoding": "chunked"},
        )

    assert accepted.status_code == 200
    assert accepted.json() == {"size": limit}
    for response in (declared, streamed):
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "request_too_large"
        assert response.json()["error"]["details"] == []
        assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
        assert marker.decode("ascii") not in response.text


def test_configured_sqlite_path_survives_single_worker_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "persistent" / "runtime.sqlite3"
    first_app = create_app(runtime_database_path=database_path)

    with TestClient(first_app) as client:
        plan_response = client.post(
            f"/api/v1/scenarios/{SCENARIO_ID}/plans",
            json={
                "expected_version": 1,
                "algorithm": "cp_sat",
                "max_time_seconds": 5,
            },
        )
        assert plan_response.status_code == 201
        plan_id = plan_response.json()["plan"]["plan_id"]
        created = client.post(
            "/api/v1/runtime-sessions",
            json={
                "scenario_id": SCENARIO_ID,
                "scenario_version": 1,
                "active_plan_id": plan_id,
                "speed": 1,
            },
        )
        assert created.status_code == 201
        session_id = created.json()["session_id"]
        started = client.post(
            f"/api/v1/runtime-sessions/{session_id}/start",
            json={"expected_revision": created.json()["revision"]},
        )
        assert started.status_code == 200
        if started.json()["status"] == "awaiting_confirmation":
            rejected = client.post(
                f"/api/v1/runtime-sessions/{session_id}/candidate/reject",
                json={
                    "expected_revision": started.json()["revision"],
                    "candidate_plan_id": started.json()["candidate_plan_id"],
                    "reason": "继续验证单 worker 重启恢复",
                },
            )
            assert rejected.status_code == 200
            started = client.post(
                f"/api/v1/runtime-sessions/{session_id}/start",
                json={"expected_revision": rejected.json()["revision"]},
            )
            assert started.status_code == 200
        started_revision = started.json()["revision"]
        assert started.json()["status"] == "running"
    first_app.state.runtime_repository.close()

    second_app = create_app(runtime_database_path=database_path)
    with TestClient(second_app) as client:
        recovered = client.get(f"/api/v1/runtime-sessions/{session_id}")
    second_app.state.runtime_repository.close()

    assert recovered.status_code == 200
    assert recovered.json()["status"] == "paused"
    assert recovered.json()["revision"] == started_revision + 1
    assert database_path.is_file()


def test_production_entrypoint_is_fixed_to_one_worker(tmp_path: Path) -> None:
    configuration = build_uvicorn_configuration(
        {
            "APP_RUNTIME_DATABASE_PATH": str(tmp_path / "runtime.sqlite3"),
            "APP_FRONTEND_DIST_PATH": str(tmp_path / "dist"),
            "PORT": "9123",
        }
    )

    assert configuration == {
        "host": "0.0.0.0",
        "port": 9123,
        "workers": 1,
        "server_header": False,
        "timeout_keep_alive": 5,
    }
    with pytest.raises(ValueError, match="APP_FRONTEND_DIST_PATH"):
        build_uvicorn_configuration({})


def test_container_contract_is_non_root_persistent_and_secret_free() -> None:
    project_root = Path(__file__).parents[1]
    dockerfile = (project_root / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (project_root / ".dockerignore").read_text(encoding="utf-8")
    runtime_requirements = (
        project_root / "backend" / "requirements-runtime.txt"
    ).read_text(encoding="utf-8")

    assert "FROM node:22-alpine AS frontend-build" in dockerfile
    assert "FROM python:3.13-slim AS runtime" in dockerfile
    assert "USER jointassurance:jointassurance" in dockerfile
    assert 'VOLUME ["/data"]' in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert 'CMD ["python", "-m", "backend.app.production"]' in dockerfile
    assert "APP_RUNTIME_DATABASE_PATH=/data/runtime-sessions.sqlite3" in dockerfile
    assert "COPY backend/requirements-runtime.txt" in dockerfile
    assert "AI_API_KEY=" not in dockerfile
    assert "COPY ." not in dockerfile
    assert "pytest" not in runtime_requirements

    ignored = set(dockerignore.splitlines())
    for required in {
        ".git",
        ".env",
        ".env.*",
        ".runtime",
        ".venv",
        "frontend/node_modules",
        "frontend/dist",
    }:
        assert required in ignored
