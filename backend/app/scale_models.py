"""Strict contracts for M6 synthetic scale and performance evidence."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from math import ceil, isclose
from statistics import median
from typing import Literal

from pydantic import Field, model_validator

from .demo_export import SAFETY_NOTICE
from .models import DataClassification, ModelBase
from .planning_models import PlanStatus


class ScaleTier(str, Enum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE_AGGREGATE = "large_aggregate"


class BenchmarkExecutionPath(str, Enum):
    CP_SAT = "cp_sat"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"


class BenchmarkFallbackReason(str, Enum):
    MODEL_SIZE_GUARD = "model_size_guard"
    TIME_LIMIT = "time_limit"


class BenchmarkPlatform(str, Enum):
    WINDOWS = "windows"
    LINUX = "linux"
    MACOS = "macos"


class ScaleProfile(ModelBase):
    tier: ScaleTier
    task_count: int = Field(gt=0)
    resource_count: int = Field(gt=0)
    zone_count: int = Field(gt=0)
    target_seconds: float = Field(gt=0)
    fallback_allowed: bool = False
    data_classification: Literal[DataClassification.SYNTHETIC] = (
        DataClassification.SYNTHETIC
    )


_PROFILE_VALUES: dict[ScaleTier, dict[str, int | float | bool]] = {
    ScaleTier.SMALL: {
        "task_count": 100,
        "resource_count": 20,
        "zone_count": 5,
        "target_seconds": 2.0,
        "fallback_allowed": False,
    },
    ScaleTier.MEDIUM: {
        "task_count": 500,
        "resource_count": 50,
        "zone_count": 10,
        "target_seconds": 5.0,
        "fallback_allowed": False,
    },
    ScaleTier.LARGE_AGGREGATE: {
        "task_count": 2000,
        "resource_count": 200,
        "zone_count": 20,
        "target_seconds": 15.0,
        "fallback_allowed": True,
    },
}


def get_scale_profile(tier: ScaleTier | str) -> ScaleProfile:
    """Return a fresh canonical profile so callers cannot mutate shared state."""

    normalized = ScaleTier(tier)
    return ScaleProfile(tier=normalized, **_PROFILE_VALUES[normalized])


class BenchmarkEnvironment(ModelBase):
    platform_name: BenchmarkPlatform
    python_version: str = Field(
        min_length=3,
        max_length=40,
        pattern=r"^[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.]+)?$",
    )
    ortools_version: str = Field(
        min_length=3,
        max_length=40,
        pattern=r"^[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.]+)?$",
    )
    cpu_logical_count: int = Field(gt=0)
    solver_workers: Literal[1] = 1


class ScaleBenchmarkSample(ModelBase):
    tier: ScaleTier
    run_index: int = Field(ge=1)
    elapsed_seconds: float = Field(ge=0)
    execution_path: BenchmarkExecutionPath
    fallback_reason: BenchmarkFallbackReason | None = None
    plan_status: PlanStatus
    total_tasks: int = Field(gt=0)
    assigned_tasks: int = Field(ge=0)
    unassigned_tasks: int = Field(ge=0)
    hard_constraint_violation_count: Literal[0] = 0

    @model_validator(mode="after")
    def validate_result(self) -> ScaleBenchmarkSample:
        profile = get_scale_profile(self.tier)
        if self.total_tasks != profile.task_count:
            raise ValueError("benchmark sample task count must match its scale profile")
        if self.assigned_tasks + self.unassigned_tasks != self.total_tasks:
            raise ValueError("assigned and unassigned counts must cover every task")
        if self.plan_status is PlanStatus.INVALID:
            raise ValueError("benchmark evidence cannot accept an invalid plan")
        if self.plan_status is PlanStatus.EXECUTABLE and self.unassigned_tasks:
            raise ValueError("an executable benchmark plan cannot leave tasks unassigned")
        if self.plan_status is PlanStatus.PARTIAL and not self.unassigned_tasks:
            raise ValueError("a partial benchmark plan must identify unassigned tasks")

        if self.execution_path is BenchmarkExecutionPath.CP_SAT:
            if self.fallback_reason is not None:
                raise ValueError("CP-SAT samples cannot claim a fallback reason")
            return self

        if not profile.fallback_allowed:
            raise ValueError("deterministic fallback is accepted only for the large tier")
        if self.fallback_reason is None:
            raise ValueError("fallback samples must publish a bounded fallback reason")
        return self


class ScaleBenchmarkReport(ModelBase):
    report_id: str = Field(min_length=1, pattern=r"^BENCH-[A-Z0-9-]+$")
    generated_at: datetime
    profile: ScaleProfile
    environment: BenchmarkEnvironment
    warmup_runs: int = Field(default=1, ge=0, le=10)
    samples: list[ScaleBenchmarkSample] = Field(min_length=1, max_length=20)
    p50_seconds: float = Field(ge=0)
    p95_seconds: float = Field(ge=0)
    max_seconds: float = Field(ge=0)
    target_met: bool
    fallback_used: bool
    all_hard_constraints_valid: Literal[True] = True
    safety_notice: Literal[SAFETY_NOTICE] = SAFETY_NOTICE

    @model_validator(mode="after")
    def validate_evidence(self) -> ScaleBenchmarkReport:
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError("benchmark generated_at must include a timezone offset")
        if self.profile != get_scale_profile(self.profile.tier):
            raise ValueError("benchmark report must use the canonical scale profile")
        if any(sample.tier is not self.profile.tier for sample in self.samples):
            raise ValueError("all benchmark samples must use the report scale tier")
        expected_indices = list(range(1, len(self.samples) + 1))
        if [sample.run_index for sample in self.samples] != expected_indices:
            raise ValueError("benchmark run indices must be contiguous and ordered")

        durations = sorted(sample.elapsed_seconds for sample in self.samples)
        expected_p50 = median(durations)
        expected_p95 = durations[ceil(len(durations) * 0.95) - 1]
        expected_max = durations[-1]
        measurements = (
            (self.p50_seconds, expected_p50),
            (self.p95_seconds, expected_p95),
            (self.max_seconds, expected_max),
        )
        if any(not isclose(actual, expected, abs_tol=1e-6) for actual, expected in measurements):
            raise ValueError("benchmark summary must be derived from measured samples")

        expected_target = expected_max <= self.profile.target_seconds
        if self.target_met is not expected_target:
            raise ValueError("target_met must reflect the maximum measured duration")
        expected_fallback = any(
            sample.execution_path is BenchmarkExecutionPath.DETERMINISTIC_FALLBACK
            for sample in self.samples
        )
        if self.fallback_used is not expected_fallback:
            raise ValueError("fallback_used must reflect the measured execution paths")
        return self
