"""HTTP response models shared by the versioned API surface."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .models import ModelBase


class HealthResponse(ModelBase):
    service: Literal["joint-assurance-api"] = "joint-assurance-api"
    version: str = Field(min_length=1)
    api_version: Literal["v1"] = "v1"
    status: Literal["ok"] = "ok"


class ApiErrorDetail(ModelBase):
    location: list[str | int] = Field(default_factory=list)
    message: str = Field(min_length=1)
    type: str = Field(min_length=1)


class ApiErrorBody(ModelBase):
    code: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
    message: str = Field(min_length=1)
    details: list[ApiErrorDetail] = Field(default_factory=list)
    request_id: str = Field(pattern=r"^REQ-[A-F0-9]{12}$")


class ApiErrorResponse(ModelBase):
    error: ApiErrorBody
