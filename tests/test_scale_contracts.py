from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from backend.app.demo_export import SAFETY_NOTICE
from backend.app.scale_models import (
    CANONICAL_SCALE_SCENARIO_FINGERPRINTS,
    CANONICAL_SCALE_SOLVER_LIMIT_SECONDS,
    BenchmarkEnvironment,
    ScaleBenchmarkReport,
    ScaleBenchmarkSample,
    ScalePlanComparison,
    ScaleTier,
    get_scale_profile,
)


TZ = timezone(timedelta(hours=8))


def _environment() -> BenchmarkEnvironment:
    return BenchmarkEnvironment(
        platform_name="windows",
        python_version="3.13.9",
        ortools_version="9.15.6755",
        cpu_logical_count=16,
    )


def _sample(
    *,
    tier: ScaleTier = ScaleTier.SMALL,
    run_index: int = 1,
    elapsed_seconds: float = 0.8,
    execution_path: str = "cp_sat",
    fallback_reason: str | None = None,
    assigned_tasks: int | None = None,
    average_wait_minutes: float = 10.0,
    max_wait_minutes: int = 20,
) -> ScaleBenchmarkSample:
    profile = get_scale_profile(tier)
    assigned = profile.task_count if assigned_tasks is None else assigned_tasks
    algorithms = {
        "fifo_baseline": "fifo_baseline_v1",
        "cp_sat": "bounded_scale_cp_sat_v2",
        "deterministic_fallback": "deterministic_scale_fallback_v1",
    }
    return ScaleBenchmarkSample(
        tier=tier,
        run_index=run_index,
        elapsed_seconds=elapsed_seconds,
        execution_path=execution_path,
        algorithm=algorithms[execution_path],
        fallback_reason=fallback_reason,
        plan_status="executable" if assigned == profile.task_count else "partial",
        total_tasks=profile.task_count,
        assigned_tasks=assigned,
        unassigned_tasks=profile.task_count - assigned,
        average_wait_minutes=average_wait_minutes,
        max_wait_minutes=max_wait_minutes,
        task_completion_rate_pct=round(assigned / profile.task_count * 100, 2),
        critical_task_completion_rate_pct=100.0,
        overall_resource_utilization_pct=12.5,
    )


def _comparison(
    fifo: ScaleBenchmarkSample,
    bounded: ScaleBenchmarkSample,
) -> ScalePlanComparison:
    return ScalePlanComparison(
        fifo_assigned_tasks=fifo.assigned_tasks,
        bounded_assigned_tasks=bounded.assigned_tasks,
        assigned_task_delta=bounded.assigned_tasks - fifo.assigned_tasks,
        fifo_average_wait_minutes=fifo.average_wait_minutes,
        bounded_average_wait_minutes=bounded.average_wait_minutes,
        average_wait_delta_minutes=round(
            bounded.average_wait_minutes - fifo.average_wait_minutes,
            2,
        ),
        fifo_max_wait_minutes=fifo.max_wait_minutes,
        bounded_max_wait_minutes=bounded.max_wait_minutes,
        max_wait_delta_minutes=bounded.max_wait_minutes - fifo.max_wait_minutes,
        fifo_task_completion_rate_pct=fifo.task_completion_rate_pct,
        bounded_task_completion_rate_pct=bounded.task_completion_rate_pct,
        task_completion_delta_points=round(
            bounded.task_completion_rate_pct - fifo.task_completion_rate_pct,
            2,
        ),
        fifo_critical_completion_rate_pct=(
            fifo.critical_task_completion_rate_pct
        ),
        bounded_critical_completion_rate_pct=(
            bounded.critical_task_completion_rate_pct
        ),
        critical_completion_delta_points=round(
            bounded.critical_task_completion_rate_pct
            - fifo.critical_task_completion_rate_pct,
            2,
        ),
        fifo_resource_utilization_pct=fifo.overall_resource_utilization_pct,
        bounded_resource_utilization_pct=(
            bounded.overall_resource_utilization_pct
        ),
        resource_utilization_delta_points=round(
            bounded.overall_resource_utilization_pct
            - fifo.overall_resource_utilization_pct,
            2,
        ),
    )


def test_canonical_scale_profiles_match_project_targets_and_are_isolated() -> None:
    small = get_scale_profile("small")
    medium = get_scale_profile("medium")
    large = get_scale_profile("large_aggregate")

    assert (small.task_count, small.resource_count, small.zone_count) == (100, 20, 5)
    assert (medium.task_count, medium.resource_count, medium.zone_count) == (500, 50, 10)
    assert (large.task_count, large.resource_count, large.zone_count) == (2000, 200, 20)
    assert (small.target_seconds, medium.target_seconds, large.target_seconds) == (
        2.0,
        5.0,
        15.0,
    )
    assert small.fallback_allowed is medium.fallback_allowed is False
    assert large.fallback_allowed is True
    assert all(
        profile.data_classification.value == "synthetic"
        for profile in (small, medium, large)
    )

    small.task_count = 1
    assert get_scale_profile("small").task_count == 100


def test_benchmark_sample_requires_complete_safe_task_accounting() -> None:
    sample = _sample(assigned_tasks=96)

    assert sample.plan_status.value == "partial"
    assert sample.unassigned_tasks == 4
    assert sample.hard_constraint_violation_count == 0

    with pytest.raises(ValidationError, match="task count"):
        ScaleBenchmarkSample(
            **{
                **sample.model_dump(mode="python"),
                "total_tasks": 99,
                "unassigned_tasks": 3,
            }
        )
    with pytest.raises(ValidationError):
        ScaleBenchmarkSample(
            **{
                **sample.model_dump(mode="python"),
                "hard_constraint_violation_count": 1,
            }
        )
    with pytest.raises(ValidationError, match="invalid plan"):
        ScaleBenchmarkSample(
            **{
                **sample.model_dump(mode="python"),
                "plan_status": "invalid",
            }
        )
    with pytest.raises(ValidationError, match="completion rate"):
        ScaleBenchmarkSample(
            **{
                **sample.model_dump(mode="python"),
                "task_completion_rate_pct": 100,
            }
        )


def test_fallback_is_explicit_and_only_accepted_for_large_aggregate() -> None:
    large = _sample(
        tier=ScaleTier.LARGE_AGGREGATE,
        execution_path="deterministic_fallback",
        fallback_reason="model_size_guard",
        assigned_tasks=1980,
    )

    assert large.execution_path.value == "deterministic_fallback"
    assert large.fallback_reason is not None

    with pytest.raises(ValidationError, match="only for the large tier"):
        _sample(
            tier=ScaleTier.MEDIUM,
            execution_path="deterministic_fallback",
            fallback_reason="time_limit",
        )
    with pytest.raises(ValidationError, match="publish a bounded fallback reason"):
        _sample(
            tier=ScaleTier.LARGE_AGGREGATE,
            execution_path="deterministic_fallback",
        )
    with pytest.raises(ValidationError, match="cannot claim a fallback reason"):
        _sample(execution_path="cp_sat", fallback_reason="time_limit")
    fifo = _sample(execution_path="fifo_baseline")
    assert fifo.fallback_reason is None
    with pytest.raises(ValidationError, match="FIFO samples"):
        _sample(
            execution_path="fifo_baseline",
            fallback_reason="model_size_guard",
        )


def test_report_summary_must_come_from_ordered_measured_samples() -> None:
    fifo_samples = [
        _sample(
            run_index=1,
            elapsed_seconds=0.2,
            execution_path="fifo_baseline",
            average_wait_minutes=12,
            max_wait_minutes=24,
        ),
        _sample(
            run_index=2,
            elapsed_seconds=0.3,
            execution_path="fifo_baseline",
            average_wait_minutes=12,
            max_wait_minutes=24,
        ),
        _sample(
            run_index=3,
            elapsed_seconds=0.4,
            execution_path="fifo_baseline",
            average_wait_minutes=12,
            max_wait_minutes=24,
        ),
    ]
    bounded_samples = [
        _sample(run_index=1, elapsed_seconds=0.7),
        _sample(run_index=2, elapsed_seconds=0.8),
        _sample(run_index=3, elapsed_seconds=0.9),
    ]
    report = ScaleBenchmarkReport(
        report_id="BENCH-SMALL-001",
        generated_at=datetime(2026, 8, 8, 12, 0, tzinfo=TZ),
        profile=get_scale_profile("small"),
        scenario_id="SCN-SCALE-SMALL-01",
        scenario_fingerprint=CANONICAL_SCALE_SCENARIO_FINGERPRINTS[
            ScaleTier.SMALL
        ],
        environment=_environment(),
        solver_time_limit_seconds=CANONICAL_SCALE_SOLVER_LIMIT_SECONDS[
            ScaleTier.SMALL
        ],
        fifo_samples=fifo_samples,
        samples=bounded_samples,
        fifo_p50_seconds=0.3,
        fifo_p95_seconds=0.4,
        fifo_max_seconds=0.4,
        p50_seconds=0.8,
        p95_seconds=0.9,
        max_seconds=0.9,
        comparison=_comparison(fifo_samples[0], bounded_samples[0]),
        target_met=True,
        fallback_used=False,
    )

    assert report.safety_notice == SAFETY_NOTICE
    assert report.all_hard_constraints_valid is True

    payload = report.model_dump(mode="python")
    with pytest.raises(ValidationError, match="derived from measured samples"):
        ScaleBenchmarkReport(**{**payload, "p95_seconds": 0.8})
    with pytest.raises(ValidationError, match="target_met"):
        ScaleBenchmarkReport(**{**payload, "target_met": False})
    with pytest.raises(ValidationError, match="fallback_used"):
        ScaleBenchmarkReport(**{**payload, "fallback_used": True})
    with pytest.raises(ValidationError, match="contiguous and ordered"):
        out_of_order = [bounded_samples[1], bounded_samples[0], bounded_samples[2]]
        ScaleBenchmarkReport(**{**payload, "samples": out_of_order})
    with pytest.raises(ValidationError, match="frozen scenario fingerprint"):
        ScaleBenchmarkReport(**{**payload, "scenario_fingerprint": "0" * 64})
    with pytest.raises(ValidationError, match="canonical solver limit"):
        ScaleBenchmarkReport(**{**payload, "solver_time_limit_seconds": 2.0})
    with pytest.raises(ValidationError, match="FIFO sample group"):
        ScaleBenchmarkReport(**{**payload, "fifo_samples": bounded_samples})


def test_report_rejects_noncanonical_profiles_and_safety_overrides() -> None:
    fifo_sample = _sample(execution_path="fifo_baseline")
    sample = _sample()
    base = {
        "report_id": "BENCH-SMALL-002",
        "generated_at": datetime(2026, 8, 8, 12, 0, tzinfo=TZ),
        "profile": get_scale_profile("small"),
        "scenario_id": "SCN-SCALE-SMALL-01",
        "scenario_fingerprint": CANONICAL_SCALE_SCENARIO_FINGERPRINTS[
            ScaleTier.SMALL
        ],
        "environment": _environment(),
        "solver_time_limit_seconds": CANONICAL_SCALE_SOLVER_LIMIT_SECONDS[
            ScaleTier.SMALL
        ],
        "fifo_samples": [fifo_sample],
        "samples": [sample],
        "fifo_p50_seconds": 0.8,
        "fifo_p95_seconds": 0.8,
        "fifo_max_seconds": 0.8,
        "p50_seconds": 0.8,
        "p95_seconds": 0.8,
        "max_seconds": 0.8,
        "comparison": _comparison(fifo_sample, sample),
        "target_met": True,
        "fallback_used": False,
    }

    altered = get_scale_profile("small")
    altered.task_count = 101
    with pytest.raises(ValidationError, match="canonical scale profile"):
        ScaleBenchmarkReport(**{**base, "profile": altered})
    with pytest.raises(ValidationError):
        ScaleBenchmarkReport(
            **{
                **base,
                "safety_notice": "可直接用于真实机场生产调度。",
            }
        )
    with pytest.raises(ValidationError):
        ScaleBenchmarkReport(**{**base, "hostname": "private-machine"})
    with pytest.raises(ValidationError):
        BenchmarkEnvironment(
            platform_name="private-machine",
            python_version="E:\\private\\python.exe",
            ortools_version="9.15.6755",
            cpu_logical_count=16,
        )
