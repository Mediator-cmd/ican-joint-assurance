"""Single-worker production entrypoint for the provider-neutral container."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import uvicorn

from .deployment import load_deployment_settings


def build_uvicorn_configuration(
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    settings = load_deployment_settings(environ)
    if settings.frontend_dist_path is None:
        raise ValueError("APP_FRONTEND_DIST_PATH is required for production startup")
    return {
        "host": "0.0.0.0",
        "port": settings.port,
        "workers": 1,
        "server_header": False,
        "timeout_keep_alive": 5,
    }


def main() -> None:
    uvicorn.run("backend.app.main:app", **build_uvicorn_configuration())


if __name__ == "__main__":
    main()
