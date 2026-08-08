from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from backend.app.demo_export import SAFETY_NOTICE
from backend.app.scale_models import (
    BenchmarkEnvironment,
    ScaleBenchmarkReport,
    ScaleBenchmarkSample,
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
) -> ScaleBenchmarkSample:
    profile = get_scale_profile(tier)
    assigned = profile.task_count if assigned_tasks is None else assigned_tasks
    return ScaleBenchmarkSample(
        tier=tier,
        run_index=run_index,
        elapsed_seconds=elapsed_seconds,
        execution_path=execution_path,
        fallback_reason=fallback_reason,
        plan_status="executable" if assigned == profile.task_count else "partial",
        total_tasks=profile.task_count,
        assigned_tasks=assigned,
        unassigned_tasks=profile.task_count - assigned,
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


def test_report_summary_must_come_from_ordered_measured_samples() -> None:
    samples = [
        _sample(run_index=1, elapsed_seconds=0.7),
        _sample(run_index=2, elapsed_seconds=0.8),
        _sample(run_index=3, elapsed_seconds=0.9),
    ]
    report = ScaleBenchmarkReport(
        report_id="BENCH-SMALL-001",
        generated_at=datetime(2026, 8, 8, 12, 0, tzinfo=TZ),
        profile=get_scale_profile("small"),
        environment=_environment(),
        samples=samples,
        p50_seconds=0.8,
        p95_seconds=0.9,
        max_seconds=0.9,
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
        out_of_order = [samples[1], samples[0], samples[2]]
        ScaleBenchmarkReport(**{**payload, "samples": out_of_order})


def test_report_rejects_noncanonical_profiles_and_safety_overrides() -> None:
    sample = _sample()
    base = {
        "report_id": "BENCH-SMALL-002",
        "generated_at": datetime(2026, 8, 8, 12, 0, tzinfo=TZ),
        "profile": get_scale_profile("small"),
        "environment": _environment(),
        "samples": [sample],
        "p50_seconds": 0.8,
        "p95_seconds": 0.8,
        "max_seconds": 0.8,
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
