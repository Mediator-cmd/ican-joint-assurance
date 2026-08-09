from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.scale_benchmark import (
    build_benchmark_environment,
    run_scale_benchmark_suite,
    run_scale_tier_benchmark,
)
from backend.app.scale_models import (
    BenchmarkExecutionPath,
    BenchmarkFallbackReason,
    ScaleBenchmarkSuite,
    ScaleTier,
)


REPORT_PATH = Path(__file__).resolve().parents[1] / "docs" / "m6-benchmark-report.json"


def test_benchmark_environment_contains_only_finite_non_sensitive_fields() -> None:
    environment = build_benchmark_environment()
    payload = environment.model_dump(mode="json")

    assert payload["platform_name"] in {"windows", "linux", "macos"}
    assert payload["solver_workers"] == 1
    assert payload["cpu_logical_count"] > 0
    assert set(payload) == {
        "platform_name",
        "python_version",
        "ortools_version",
        "cpu_logical_count",
        "solver_workers",
    }
    serialized = json.dumps(payload)
    assert "hostname" not in serialized.lower()
    assert "api_key" not in serialized.lower()


def test_small_tier_runner_produces_paired_ordered_measured_evidence() -> None:
    report = run_scale_tier_benchmark(
        ScaleTier.SMALL,
        warmup_runs=1,
        measured_runs=3,
    )

    assert [sample.run_index for sample in report.fifo_samples] == [1, 2, 3]
    assert [sample.run_index for sample in report.samples] == [1, 2, 3]
    assert all(
        sample.execution_path is BenchmarkExecutionPath.FIFO_BASELINE
        for sample in report.fifo_samples
    )
    assert all(
        sample.execution_path is BenchmarkExecutionPath.CP_SAT
        for sample in report.samples
    )
    assert report.fallback_used is False
    assert report.comparison.fifo_assigned_tasks == 100
    assert report.comparison.bounded_assigned_tasks == 100
    assert report.all_hard_constraints_valid is True
    assert report.p50_seconds <= report.p95_seconds <= report.max_seconds
    assert (
        report.fifo_p50_seconds
        <= report.fifo_p95_seconds
        <= report.fifo_max_seconds
    )


def test_complete_suite_binds_all_tiers_paths_and_round_trips() -> None:
    suite = run_scale_benchmark_suite(warmup_runs=0, measured_runs=1)

    assert [report.profile.tier for report in suite.reports] == list(ScaleTier)
    assert suite.all_targets_met is all(
        report.target_met for report in suite.reports
    )
    for report in suite.reports:
        assert report.environment == suite.environment
        assert report.generated_at == suite.generated_at
        assert report.samples[0].hard_constraint_violation_count == 0
        assert report.fifo_samples[0].hard_constraint_violation_count == 0

    small, medium, large = suite.reports
    assert small.samples[0].execution_path is BenchmarkExecutionPath.CP_SAT
    assert medium.samples[0].execution_path is BenchmarkExecutionPath.CP_SAT
    assert (
        large.samples[0].execution_path
        is BenchmarkExecutionPath.DETERMINISTIC_FALLBACK
    )
    assert (
        large.samples[0].fallback_reason
        is BenchmarkFallbackReason.MODEL_SIZE_GUARD
    )
    assert large.fallback_used is True

    restored = ScaleBenchmarkSuite.model_validate(
        suite.model_dump(mode="json")
    )
    assert restored == suite
    serialized = json.dumps(suite.model_dump(mode="json"), ensure_ascii=False)
    assert "hostname" not in serialized.lower()
    assert "E:\\" not in serialized


def test_committed_formal_report_matches_the_strict_m6_3_contract() -> None:
    suite = ScaleBenchmarkSuite.model_validate_json(
        REPORT_PATH.read_text(encoding="utf-8")
    )

    assert suite.warmup_runs == 1
    assert suite.measured_runs == 5
    assert suite.environment.solver_workers == 1
    assert suite.all_targets_met is True
    assert suite.all_hard_constraints_valid is True
    assert all(report.target_met for report in suite.reports)
    assert all(
        sample.hard_constraint_violation_count == 0
        for report in suite.reports
        for sample in [*report.fifo_samples, *report.samples]
    )
    assert all(
        sample.execution_path is BenchmarkExecutionPath.CP_SAT
        for report in suite.reports[:2]
        for sample in report.samples
    )
    assert all(
        sample.fallback_reason is BenchmarkFallbackReason.MODEL_SIZE_GUARD
        for sample in suite.reports[2].samples
    )

    serialized = REPORT_PATH.read_text(encoding="utf-8")
    for forbidden in ("hostname", "api_key", "secret", "E:\\", "C:\\Users"):
        assert forbidden.lower() not in serialized.lower()


@pytest.mark.parametrize(
    ("warmup_runs", "measured_runs"),
    [(-1, 1), (11, 1), (0, 0), (0, 21), (True, 1), (0, False)],
)
def test_runner_rejects_invalid_sample_counts(
    warmup_runs: int,
    measured_runs: int,
) -> None:
    with pytest.raises(ValueError):
        run_scale_tier_benchmark(
            ScaleTier.SMALL,
            warmup_runs=warmup_runs,
            measured_runs=measured_runs,
        )
