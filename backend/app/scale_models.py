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
    FIFO_BASELINE = "fifo_baseline"
    CP_SAT = "cp_sat"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"


class BenchmarkFallbackReason(str, Enum):
    MODEL_SIZE_GUARD = "model_size_guard"
    TIME_LIMIT = "time_limit"


class BenchmarkPlatform(str, Enum):
    WINDOWS = "windows"
    LINUX = "linux"
    MACOS = "macos"


CANONICAL_SCALE_SCENARIO_FINGERPRINTS = {
    ScaleTier.SMALL: "7dc3827de6a1d702f73a2383473008f04170ba3ed1491e1817bcab73bb0bcf11",
    ScaleTier.MEDIUM: "5775f0c58d2d64e47cca1a8879ae14077ea65236a66f18a15816ce244a73d649",
    ScaleTier.LARGE_AGGREGATE: (
        "a5e0bae31aa7a91b70568df58326fe93a850ce26d3701234a39e14e620c9c70e"
    ),
}

CANONICAL_SCALE_SOLVER_LIMIT_SECONDS = {
    ScaleTier.SMALL: 1.0,
    ScaleTier.MEDIUM: 3.0,
    ScaleTier.LARGE_AGGREGATE: 9.0,
}


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
    algorithm: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
    fallback_reason: BenchmarkFallbackReason | None = None
    plan_status: PlanStatus
    total_tasks: int = Field(gt=0)
    assigned_tasks: int = Field(ge=0)
    unassigned_tasks: int = Field(ge=0)
    average_wait_minutes: float = Field(ge=0)
    max_wait_minutes: int = Field(ge=0)
    task_completion_rate_pct: float = Field(ge=0, le=100)
    critical_task_completion_rate_pct: float = Field(ge=0, le=100)
    overall_resource_utilization_pct: float = Field(ge=0, le=100)
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
        expected_completion_rate = round(
            self.assigned_tasks / self.total_tasks * 100,
            2,
        )
        if not isclose(
            self.task_completion_rate_pct,
            expected_completion_rate,
            abs_tol=1e-6,
        ):
            raise ValueError("benchmark completion rate must match task accounting")

        if self.execution_path is BenchmarkExecutionPath.FIFO_BASELINE:
            if self.fallback_reason is not None:
                raise ValueError("FIFO samples cannot claim a fallback reason")
            if self.algorithm != "fifo_baseline_v1":
                raise ValueError("FIFO samples must publish the baseline algorithm")
            return self

        if self.execution_path is BenchmarkExecutionPath.CP_SAT:
            if self.fallback_reason is not None:
                raise ValueError("CP-SAT samples cannot claim a fallback reason")
            if self.algorithm != "bounded_scale_cp_sat_v2":
                raise ValueError("CP-SAT samples must publish the bounded algorithm")
            return self

        if not profile.fallback_allowed:
            raise ValueError("deterministic fallback is accepted only for the large tier")
        if self.fallback_reason is None:
            raise ValueError("fallback samples must publish a bounded fallback reason")
        if self.algorithm != "deterministic_scale_fallback_v1":
            raise ValueError("fallback samples must publish the fallback algorithm")
        return self


class ScalePlanComparison(ModelBase):
    fifo_assigned_tasks: int = Field(ge=0)
    bounded_assigned_tasks: int = Field(ge=0)
    assigned_task_delta: int
    fifo_average_wait_minutes: float = Field(ge=0)
    bounded_average_wait_minutes: float = Field(ge=0)
    average_wait_delta_minutes: float
    fifo_max_wait_minutes: int = Field(ge=0)
    bounded_max_wait_minutes: int = Field(ge=0)
    max_wait_delta_minutes: int
    fifo_task_completion_rate_pct: float = Field(ge=0, le=100)
    bounded_task_completion_rate_pct: float = Field(ge=0, le=100)
    task_completion_delta_points: float
    fifo_critical_completion_rate_pct: float = Field(ge=0, le=100)
    bounded_critical_completion_rate_pct: float = Field(ge=0, le=100)
    critical_completion_delta_points: float
    fifo_resource_utilization_pct: float = Field(ge=0, le=100)
    bounded_resource_utilization_pct: float = Field(ge=0, le=100)
    resource_utilization_delta_points: float

    @model_validator(mode="after")
    def validate_deltas(self) -> ScalePlanComparison:
        exact_deltas = (
            (
                self.assigned_task_delta,
                self.bounded_assigned_tasks - self.fifo_assigned_tasks,
            ),
            (
                self.max_wait_delta_minutes,
                self.bounded_max_wait_minutes - self.fifo_max_wait_minutes,
            ),
        )
        if any(actual != expected for actual, expected in exact_deltas):
            raise ValueError("benchmark comparison integer deltas must be derived")
        rounded_deltas = (
            (
                self.average_wait_delta_minutes,
                self.bounded_average_wait_minutes - self.fifo_average_wait_minutes,
            ),
            (
                self.task_completion_delta_points,
                self.bounded_task_completion_rate_pct
                - self.fifo_task_completion_rate_pct,
            ),
            (
                self.critical_completion_delta_points,
                self.bounded_critical_completion_rate_pct
                - self.fifo_critical_completion_rate_pct,
            ),
            (
                self.resource_utilization_delta_points,
                self.bounded_resource_utilization_pct
                - self.fifo_resource_utilization_pct,
            ),
        )
        if any(
            not isclose(actual, round(expected, 2), abs_tol=1e-6)
            for actual, expected in rounded_deltas
        ):
            raise ValueError("benchmark comparison percentage deltas must be derived")
        return self


class ScaleBenchmarkReport(ModelBase):
    report_id: str = Field(min_length=1, pattern=r"^BENCH-[A-Z0-9-]+$")
    generated_at: datetime
    profile: ScaleProfile
    scenario_id: str = Field(min_length=1, pattern=r"^SCN-SCALE-[A-Z0-9-]+$")
    scenario_version: Literal[1] = 1
    scenario_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    environment: BenchmarkEnvironment
    solver_time_limit_seconds: float = Field(gt=0)
    warmup_runs: int = Field(default=1, ge=0, le=10)
    fifo_samples: list[ScaleBenchmarkSample] = Field(min_length=1, max_length=20)
    samples: list[ScaleBenchmarkSample] = Field(min_length=1, max_length=20)
    fifo_p50_seconds: float = Field(ge=0)
    fifo_p95_seconds: float = Field(ge=0)
    fifo_max_seconds: float = Field(ge=0)
    p50_seconds: float = Field(ge=0)
    p95_seconds: float = Field(ge=0)
    max_seconds: float = Field(ge=0)
    comparison_run_index: Literal[1] = 1
    comparison: ScalePlanComparison
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
        expected_scenario_id = (
            f"SCN-SCALE-{self.profile.tier.value.upper().replace('_', '-')}-01"
        )
        if self.scenario_id != expected_scenario_id:
            raise ValueError("benchmark report scenario identity must match its tier")
        if self.scenario_fingerprint != CANONICAL_SCALE_SCENARIO_FINGERPRINTS[
            self.profile.tier
        ]:
            raise ValueError("benchmark report must use the frozen scenario fingerprint")
        if not isclose(
            self.solver_time_limit_seconds,
            CANONICAL_SCALE_SOLVER_LIMIT_SECONDS[self.profile.tier],
            abs_tol=1e-6,
        ):
            raise ValueError("benchmark report must publish the canonical solver limit")
        all_samples = [*self.fifo_samples, *self.samples]
        if any(sample.tier is not self.profile.tier for sample in all_samples):
            raise ValueError("all benchmark samples must use the report scale tier")
        if len(self.fifo_samples) != len(self.samples):
            raise ValueError("FIFO and bounded benchmark sample counts must match")
        expected_indices = list(range(1, len(self.samples) + 1))
        for sample_group in (self.fifo_samples, self.samples):
            if [sample.run_index for sample in sample_group] != expected_indices:
                raise ValueError("benchmark run indices must be contiguous and ordered")
        if any(
            sample.execution_path is not BenchmarkExecutionPath.FIFO_BASELINE
            for sample in self.fifo_samples
        ):
            raise ValueError("FIFO sample group must use the FIFO baseline path")
        if any(
            sample.execution_path is BenchmarkExecutionPath.FIFO_BASELINE
            for sample in self.samples
        ):
            raise ValueError("bounded sample group cannot use the FIFO baseline path")
        if self.profile.tier is ScaleTier.LARGE_AGGREGATE:
            if any(
                sample.execution_path
                is not BenchmarkExecutionPath.DETERMINISTIC_FALLBACK
                or sample.fallback_reason
                is not BenchmarkFallbackReason.MODEL_SIZE_GUARD
                for sample in self.samples
            ):
                raise ValueError("large benchmark samples must publish the model guard")
        elif any(
            sample.execution_path is not BenchmarkExecutionPath.CP_SAT
            for sample in self.samples
        ):
            raise ValueError("small and medium benchmark samples must use CP-SAT")

        def summary(
            sample_group: list[ScaleBenchmarkSample],
        ) -> tuple[float, float, float]:
            durations = sorted(sample.elapsed_seconds for sample in sample_group)
            return (
                median(durations),
                durations[ceil(len(durations) * 0.95) - 1],
                durations[-1],
            )

        expected_fifo_summary = summary(self.fifo_samples)
        expected_bounded_summary = summary(self.samples)
        measurements = zip(
            (
                self.fifo_p50_seconds,
                self.fifo_p95_seconds,
                self.fifo_max_seconds,
                self.p50_seconds,
                self.p95_seconds,
                self.max_seconds,
            ),
            (*expected_fifo_summary, *expected_bounded_summary),
            strict=True,
        )
        if any(
            not isclose(actual, expected, abs_tol=1e-6)
            for actual, expected in measurements
        ):
            raise ValueError("benchmark summary must be derived from measured samples")

        expected_target = expected_bounded_summary[2] <= self.profile.target_seconds
        if self.target_met is not expected_target:
            raise ValueError("target_met must reflect the maximum measured duration")
        expected_fallback = any(
            sample.execution_path is BenchmarkExecutionPath.DETERMINISTIC_FALLBACK
            for sample in self.samples
        )
        if self.fallback_used is not expected_fallback:
            raise ValueError("fallback_used must reflect the measured execution paths")

        fifo = self.fifo_samples[0]
        bounded = self.samples[0]
        comparison_values = {
            "fifo_assigned_tasks": fifo.assigned_tasks,
            "bounded_assigned_tasks": bounded.assigned_tasks,
            "fifo_average_wait_minutes": fifo.average_wait_minutes,
            "bounded_average_wait_minutes": bounded.average_wait_minutes,
            "fifo_max_wait_minutes": fifo.max_wait_minutes,
            "bounded_max_wait_minutes": bounded.max_wait_minutes,
            "fifo_task_completion_rate_pct": fifo.task_completion_rate_pct,
            "bounded_task_completion_rate_pct": bounded.task_completion_rate_pct,
            "fifo_critical_completion_rate_pct": (
                fifo.critical_task_completion_rate_pct
            ),
            "bounded_critical_completion_rate_pct": (
                bounded.critical_task_completion_rate_pct
            ),
            "fifo_resource_utilization_pct": (
                fifo.overall_resource_utilization_pct
            ),
            "bounded_resource_utilization_pct": (
                bounded.overall_resource_utilization_pct
            ),
        }
        if any(
            getattr(self.comparison, field) != value
            for field, value in comparison_values.items()
        ):
            raise ValueError("benchmark comparison must match measured plan outcomes")
        return self


class ScaleBenchmarkSuite(ModelBase):
    schema_version: Literal[1] = 1
    suite_id: Literal["BENCH-M6-03"] = "BENCH-M6-03"
    generated_at: datetime
    environment: BenchmarkEnvironment
    warmup_runs: int = Field(default=1, ge=0, le=10)
    measured_runs: int = Field(default=5, ge=1, le=20)
    reports: list[ScaleBenchmarkReport] = Field(min_length=3, max_length=3)
    all_targets_met: bool
    all_hard_constraints_valid: Literal[True] = True
    safety_notice: Literal[SAFETY_NOTICE] = SAFETY_NOTICE

    @model_validator(mode="after")
    def validate_suite(self) -> ScaleBenchmarkSuite:
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError("benchmark suite generated_at must include a timezone offset")
        if [report.profile.tier for report in self.reports] != list(ScaleTier):
            raise ValueError("benchmark suite must contain all canonical tiers in order")
        expected_report_ids = [
            f"BENCH-M6-03-{tier.value.upper().replace('_', '-')}"
            for tier in ScaleTier
        ]
        if [report.report_id for report in self.reports] != expected_report_ids:
            raise ValueError("benchmark suite report ids must match their ordered tiers")
        if any(report.generated_at != self.generated_at for report in self.reports):
            raise ValueError("benchmark report timestamps must match their suite")
        if any(report.environment != self.environment for report in self.reports):
            raise ValueError("benchmark report environments must match their suite")
        if any(report.warmup_runs != self.warmup_runs for report in self.reports):
            raise ValueError("benchmark report warmups must match their suite")
        if any(len(report.samples) != self.measured_runs for report in self.reports):
            raise ValueError("benchmark report samples must match measured_runs")
        expected_targets = all(report.target_met for report in self.reports)
        if self.all_targets_met is not expected_targets:
            raise ValueError("all_targets_met must reflect every tier report")
        return self
