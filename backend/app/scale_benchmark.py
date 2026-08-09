"""Offline M6 benchmark runner for paired FIFO and bounded planning evidence."""

from __future__ import annotations

import os
import platform
from datetime import datetime, timezone
from math import ceil
from statistics import median
from time import perf_counter

import ortools

from .constraints import validate_plan
from .fifo_scheduler import build_fifo_plan
from .planning_models import Plan, PlanStatus
from .scale_models import (
    CANONICAL_SCALE_SOLVER_LIMIT_SECONDS,
    BenchmarkEnvironment,
    BenchmarkExecutionPath,
    BenchmarkFallbackReason,
    BenchmarkPlatform,
    ScaleBenchmarkReport,
    ScaleBenchmarkSample,
    ScaleBenchmarkSuite,
    ScalePlanComparison,
    ScaleTier,
)
from .scale_planner import build_bounded_scale_plan
from .scale_scenario_factory import ScaleScenarioArtifact, build_scale_scenario


DEFAULT_WARMUP_RUNS = 1
DEFAULT_MEASURED_RUNS = 5


class ScaleBenchmarkError(RuntimeError):
    """Raised when measured evidence cannot satisfy the benchmark contract."""


def build_benchmark_environment() -> BenchmarkEnvironment:
    """Return the finite, non-sensitive environment fields allowed in reports."""

    platform_names = {
        "Windows": BenchmarkPlatform.WINDOWS,
        "Linux": BenchmarkPlatform.LINUX,
        "Darwin": BenchmarkPlatform.MACOS,
    }
    system_name = platform.system()
    try:
        platform_name = platform_names[system_name]
    except KeyError as error:
        raise ScaleBenchmarkError("unsupported benchmark platform") from error
    return BenchmarkEnvironment(
        platform_name=platform_name,
        python_version=platform.python_version(),
        ortools_version=ortools.__version__,
        cpu_logical_count=os.cpu_count() or 1,
    )


def _validate_run_counts(warmup_runs: int, measured_runs: int) -> None:
    if (
        isinstance(warmup_runs, bool)
        or not isinstance(warmup_runs, int)
        or not 0 <= warmup_runs <= 10
    ):
        raise ValueError("warmup_runs must be an integer between 0 and 10")
    if (
        isinstance(measured_runs, bool)
        or not isinstance(measured_runs, int)
        or not 1 <= measured_runs <= 20
    ):
        raise ValueError("measured_runs must be an integer between 1 and 20")


def _validate_measured_plan(
    artifact: ScaleScenarioArtifact,
    plan: Plan,
) -> None:
    violations = validate_plan(
        artifact.scenario,
        plan.assignments,
        plan.unassigned_tasks,
    )
    if violations or plan.violations != violations:
        raise ScaleBenchmarkError(
            "benchmark plan failed independent hard-constraint validation"
        )
    if plan.status is PlanStatus.INVALID:
        raise ScaleBenchmarkError("benchmark plan cannot be invalid")
    if plan.scenario_id != artifact.scenario.scenario_id:
        raise ScaleBenchmarkError("benchmark plan scenario id does not match its input")
    if plan.scenario_version != artifact.scenario.version:
        raise ScaleBenchmarkError(
            "benchmark plan scenario version does not match its input"
        )
    if plan.metrics.total_tasks != artifact.profile.task_count:
        raise ScaleBenchmarkError("benchmark plan metrics do not cover the scale tier")
    if (
        plan.metrics.assigned_tasks + plan.metrics.unassigned_tasks
        != artifact.profile.task_count
    ):
        raise ScaleBenchmarkError("benchmark plan does not account for every task")


def _sample_from_plan(
    *,
    artifact: ScaleScenarioArtifact,
    run_index: int,
    elapsed_seconds: float,
    execution_path: BenchmarkExecutionPath,
    fallback_reason: BenchmarkFallbackReason | None,
    plan: Plan,
) -> ScaleBenchmarkSample:
    metrics = plan.metrics
    return ScaleBenchmarkSample(
        tier=artifact.profile.tier,
        run_index=run_index,
        elapsed_seconds=round(elapsed_seconds, 6),
        execution_path=execution_path,
        algorithm=plan.algorithm,
        fallback_reason=fallback_reason,
        plan_status=plan.status,
        total_tasks=metrics.total_tasks,
        assigned_tasks=metrics.assigned_tasks,
        unassigned_tasks=metrics.unassigned_tasks,
        average_wait_minutes=metrics.average_wait_minutes,
        max_wait_minutes=metrics.max_wait_minutes,
        task_completion_rate_pct=metrics.task_completion_rate_pct,
        critical_task_completion_rate_pct=(
            metrics.critical_task_completion_rate_pct
        ),
        overall_resource_utilization_pct=(
            metrics.overall_resource_utilization_pct
        ),
    )


def _run_fifo_sample(
    artifact: ScaleScenarioArtifact,
    run_index: int,
) -> ScaleBenchmarkSample:
    started = perf_counter()
    plan = build_fifo_plan(artifact.scenario)
    _validate_measured_plan(artifact, plan)
    elapsed_seconds = perf_counter() - started
    return _sample_from_plan(
        artifact=artifact,
        run_index=run_index,
        elapsed_seconds=elapsed_seconds,
        execution_path=BenchmarkExecutionPath.FIFO_BASELINE,
        fallback_reason=None,
        plan=plan,
    )


def _run_bounded_sample(
    artifact: ScaleScenarioArtifact,
    run_index: int,
) -> ScaleBenchmarkSample:
    started = perf_counter()
    result = build_bounded_scale_plan(artifact)
    _validate_measured_plan(artifact, result.plan)
    elapsed_seconds = perf_counter() - started
    return _sample_from_plan(
        artifact=artifact,
        run_index=run_index,
        elapsed_seconds=elapsed_seconds,
        execution_path=result.execution_path,
        fallback_reason=result.fallback_reason,
        plan=result.plan,
    )


def _summarize(samples: list[ScaleBenchmarkSample]) -> tuple[float, float, float]:
    durations = sorted(sample.elapsed_seconds for sample in samples)
    return (
        median(durations),
        durations[ceil(len(durations) * 0.95) - 1],
        durations[-1],
    )


def _build_comparison(
    fifo_sample: ScaleBenchmarkSample,
    bounded_sample: ScaleBenchmarkSample,
) -> ScalePlanComparison:
    return ScalePlanComparison(
        fifo_assigned_tasks=fifo_sample.assigned_tasks,
        bounded_assigned_tasks=bounded_sample.assigned_tasks,
        assigned_task_delta=(
            bounded_sample.assigned_tasks - fifo_sample.assigned_tasks
        ),
        fifo_average_wait_minutes=fifo_sample.average_wait_minutes,
        bounded_average_wait_minutes=bounded_sample.average_wait_minutes,
        average_wait_delta_minutes=round(
            bounded_sample.average_wait_minutes - fifo_sample.average_wait_minutes,
            2,
        ),
        fifo_max_wait_minutes=fifo_sample.max_wait_minutes,
        bounded_max_wait_minutes=bounded_sample.max_wait_minutes,
        max_wait_delta_minutes=(
            bounded_sample.max_wait_minutes - fifo_sample.max_wait_minutes
        ),
        fifo_task_completion_rate_pct=fifo_sample.task_completion_rate_pct,
        bounded_task_completion_rate_pct=bounded_sample.task_completion_rate_pct,
        task_completion_delta_points=round(
            bounded_sample.task_completion_rate_pct
            - fifo_sample.task_completion_rate_pct,
            2,
        ),
        fifo_critical_completion_rate_pct=(
            fifo_sample.critical_task_completion_rate_pct
        ),
        bounded_critical_completion_rate_pct=(
            bounded_sample.critical_task_completion_rate_pct
        ),
        critical_completion_delta_points=round(
            bounded_sample.critical_task_completion_rate_pct
            - fifo_sample.critical_task_completion_rate_pct,
            2,
        ),
        fifo_resource_utilization_pct=(
            fifo_sample.overall_resource_utilization_pct
        ),
        bounded_resource_utilization_pct=(
            bounded_sample.overall_resource_utilization_pct
        ),
        resource_utilization_delta_points=round(
            bounded_sample.overall_resource_utilization_pct
            - fifo_sample.overall_resource_utilization_pct,
            2,
        ),
    )


def run_scale_tier_benchmark(
    tier: ScaleTier | str,
    *,
    environment: BenchmarkEnvironment | None = None,
    generated_at: datetime | None = None,
    warmup_runs: int = DEFAULT_WARMUP_RUNS,
    measured_runs: int = DEFAULT_MEASURED_RUNS,
) -> ScaleBenchmarkReport:
    """Run paired FIFO and bounded samples for one frozen scale scenario."""

    _validate_run_counts(warmup_runs, measured_runs)
    artifact = build_scale_scenario(tier)
    measured_environment = environment or build_benchmark_environment()
    measured_at = generated_at or datetime.now(timezone.utc)

    for _ in range(warmup_runs):
        _run_fifo_sample(artifact, 1)
        _run_bounded_sample(artifact, 1)

    fifo_samples: list[ScaleBenchmarkSample] = []
    bounded_samples: list[ScaleBenchmarkSample] = []
    for run_index in range(1, measured_runs + 1):
        fifo_samples.append(_run_fifo_sample(artifact, run_index))
        bounded_samples.append(_run_bounded_sample(artifact, run_index))

    fifo_summary = _summarize(fifo_samples)
    bounded_summary = _summarize(bounded_samples)
    tier_slug = artifact.profile.tier.value.upper().replace("_", "-")
    return ScaleBenchmarkReport(
        report_id=f"BENCH-M6-03-{tier_slug}",
        generated_at=measured_at,
        profile=artifact.profile,
        scenario_id=artifact.scenario.scenario_id,
        scenario_version=artifact.scenario.version,
        scenario_fingerprint=artifact.fingerprint_sha256,
        environment=measured_environment,
        solver_time_limit_seconds=CANONICAL_SCALE_SOLVER_LIMIT_SECONDS[
            artifact.profile.tier
        ],
        warmup_runs=warmup_runs,
        fifo_samples=fifo_samples,
        samples=bounded_samples,
        fifo_p50_seconds=fifo_summary[0],
        fifo_p95_seconds=fifo_summary[1],
        fifo_max_seconds=fifo_summary[2],
        p50_seconds=bounded_summary[0],
        p95_seconds=bounded_summary[1],
        max_seconds=bounded_summary[2],
        comparison=_build_comparison(fifo_samples[0], bounded_samples[0]),
        target_met=bounded_summary[2] <= artifact.profile.target_seconds,
        fallback_used=any(
            sample.execution_path
            is BenchmarkExecutionPath.DETERMINISTIC_FALLBACK
            for sample in bounded_samples
        ),
    )


def run_scale_benchmark_suite(
    *,
    warmup_runs: int = DEFAULT_WARMUP_RUNS,
    measured_runs: int = DEFAULT_MEASURED_RUNS,
) -> ScaleBenchmarkSuite:
    """Run the complete ordered M6-3 benchmark suite without external I/O."""

    _validate_run_counts(warmup_runs, measured_runs)
    environment = build_benchmark_environment()
    generated_at = datetime.now(timezone.utc)
    reports = [
        run_scale_tier_benchmark(
            tier,
            environment=environment,
            generated_at=generated_at,
            warmup_runs=warmup_runs,
            measured_runs=measured_runs,
        )
        for tier in ScaleTier
    ]
    return ScaleBenchmarkSuite(
        generated_at=generated_at,
        environment=environment,
        warmup_runs=warmup_runs,
        measured_runs=measured_runs,
        reports=reports,
        all_targets_met=all(report.target_met for report in reports),
    )
