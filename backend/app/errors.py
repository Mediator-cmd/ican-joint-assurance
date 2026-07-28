"""Application errors that can be translated into safe HTTP responses."""

from __future__ import annotations

from collections.abc import Sequence

from .api_models import ApiErrorDetail


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Sequence[ApiErrorDetail] = (),
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = list(details)
