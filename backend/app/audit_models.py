"""Validated audit records for scenario and planning state transitions."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field, field_validator, model_validator

from .models import ModelBase


class AuditAction(str, Enum):
    SCENARIO_IMPORTED = "scenario_imported"
    EVENTS_APPLIED = "events_applied"
    PLAN_CREATED = "plan_created"
    COMPARISON_CREATED = "comparison_created"


class AuditRecord(ModelBase):
    audit_id: str = Field(pattern=r"^AUDIT-[0-9]{6,}$")
    scenario_id: str = Field(min_length=1)
    action: AuditAction
    occurred_at: datetime
    version_before: int | None = Field(default=None, ge=1)
    version_after: int | None = Field(default=None, ge=1)
    related_entity_ids: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=1, max_length=240)

    @field_validator("related_entity_ids")
    @classmethod
    def validate_related_entity_ids(cls, value: list[str]) -> list[str]:
        if any(not entity_id for entity_id in value):
            raise ValueError("related entity IDs must be non-empty")
        if len(value) != len(set(value)):
            raise ValueError("related entity IDs must be unique")
        return value

    @model_validator(mode="after")
    def validate_transition(self) -> AuditRecord:
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone offset")
        if self.version_before is None and self.version_after is None:
            raise ValueError("an audit record must reference at least one scenario version")
        if (
            self.version_before is not None
            and self.version_after is not None
            and self.version_after < self.version_before
        ):
            raise ValueError("version_after must not be less than version_before")
        return self
