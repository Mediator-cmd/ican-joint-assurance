from __future__ import annotations

import pytest
from pydantic import ValidationError

import backend.app.scale_planner as scale_planner
from backend.app.constraints import validate_plan
from backend.app.demo_export import SAFETY_NOTICE
from backend.app.scale_models import (
    BenchmarkExecutionPath,
    BenchmarkFallbackReason,
    ScaleTier,
)
from backend.app.scale_planner import (
    ScaleModelEstimate,
    ScalePlanningError,
    ScalePlanningResult,
    ScalePlanningTimeout,
    build_bounded_scale_plan,
    estimate_scale_model,
)
from backend.app.scale_scenario_factory import build_scale_scenario


EXPECTED_ESTIMATES = {
    ScaleTier.SMALL: (750, 300, 15_250, 6, False),
    ScaleTier.MEDIUM: (9_375, 1_500, 971_875, 15, False),
    ScaleTier.LARGE_AGGREGATE: (
        150_000,
        6_000,
        62_425_000,
        30,
        True,
    ),
}


@pytest.mark.parametrize("tier", list(ScaleTier))
def test_scale_model_estimate_is_exact_and_guard_is_derived(tier: ScaleTier) -> None:
    estimate = estimate_scale_model(build_scale_scenario(tier))
    expected = EXPECTED_ESTIMATES[tier]

    assert (
        estimate.compatible_pair_count,
        estimate.bounded_candidate_pair_count,
        estimate.legacy_ordering_pair_count,
        estimate.max_transition_minutes,
        estimate.guard_triggered,
    ) == expected
    assert estimate.bounded_candidate_pair_count <= 3 * estimate.task_count

    payload = estimate.model_dump(mode="python")
    with pytest.raises(ValidationError, match="guard must be derived"):
        ScaleModelEstimate.model_validate(
            {**payload, "guard_triggered": not estimate.guard_triggered}
        )


@pytest.mark.parametrize(
    ("tier", "solver_limit"),
    [(ScaleTier.SMALL, 0.25), (ScaleTier.MEDIUM, 1.0)],
)
def test_small_and_medium_use_bounded_cp_sat_with_complete_safe_plans(
    tier: ScaleTier,
    solver_limit: float,
) -> None:
    artifact = build_scale_scenario(tier)
    result = build_bounded_scale_plan(
        artifact,
        max_time_seconds=solver_limit,
    )

    assert result.execution_path is BenchmarkExecutionPath.CP_SAT
    assert result.fallback_reason is None
    assert result.model_estimate.guard_triggered is False
    assert result.plan.algorithm == "bounded_scale_cp_sat_v2"
    assert result.plan.status.value == "executable"
    assert result.plan.metrics.assigned_tasks == artifact.profile.task_count
    assert result.plan.metrics.unassigned_tasks == 0
    assert result.plan.violations == []
    assert validate_plan(
        artifact.scenario,
        result.plan.assignments,
        result.plan.unassigned_tasks,
    ) == []
    tasks_by_id = {task.task_id: task for task in artifact.scenario.tasks}
    assert all(
        assignment.service_started_at
        == max(
            tasks_by_id[assignment.task_id].release_at,
            assignment.travel_ended_at,
        )
        for assignment in result.plan.assignments
    )
    assert result.requires_human_confirmation is True
    assert result.safety_notice == SAFETY_NOTICE


def test_bounded_cp_sat_is_deterministic_for_the_same_small_artifact() -> None:
    artifact = build_scale_scenario(ScaleTier.SMALL)

    first = build_bounded_scale_plan(artifact, max_time_seconds=0.25)
    second = build_bounded_scale_plan(artifact, max_time_seconds=0.25)

    assert first == second


def test_large_guard_returns_explicit_fallback_without_building_cp_sat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_cp_sat(*args, **kwargs):
        raise AssertionError("large guard must run before CP-SAT model construction")

    monkeypatch.setattr(
        scale_planner,
        "_build_bounded_cp_sat_plan",
        forbidden_cp_sat,
    )
    artifact = build_scale_scenario(ScaleTier.LARGE_AGGREGATE)
    result = build_bounded_scale_plan(artifact)

    assert result.execution_path is BenchmarkExecutionPath.DETERMINISTIC_FALLBACK
    assert result.fallback_reason is BenchmarkFallbackReason.MODEL_SIZE_GUARD
    assert result.model_estimate.guard_triggered is True
    assert result.plan.algorithm == "deterministic_scale_fallback_v1"
    assert result.plan.status.value == "executable"
    assert result.plan.metrics.assigned_tasks == 2000
    assert result.plan.metrics.unassigned_tasks == 0
    assert result.plan.violations == []
    assert validate_plan(
        artifact.scenario,
        result.plan.assignments,
        result.plan.unassigned_tasks,
    ) == []
    payload = result.model_dump(mode="python")
    with pytest.raises(ValidationError, match="cannot bypass"):
        ScalePlanningResult.model_validate(
            {**payload, "fallback_reason": "time_limit"}
        )


@pytest.mark.parametrize("tier", [ScaleTier.SMALL, ScaleTier.MEDIUM])
def test_small_and_medium_never_silently_fall_back(
    tier: ScaleTier,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed_optimizer(*args, **kwargs):
        raise ScalePlanningTimeout("test timeout")

    def forbidden_fallback(*args, **kwargs):
        raise AssertionError("fallback must not run for small or medium")

    monkeypatch.setattr(
        scale_planner,
        "_build_bounded_cp_sat_plan",
        failed_optimizer,
    )
    monkeypatch.setattr(scale_planner, "_build_fallback_plan", forbidden_fallback)

    with pytest.raises(ScalePlanningError, match="did not return a safe plan"):
        build_bounded_scale_plan(build_scale_scenario(tier))


def test_scale_planning_contract_rejects_hidden_fallback_and_invalid_limits() -> None:
    artifact = build_scale_scenario(ScaleTier.SMALL)
    result = build_bounded_scale_plan(artifact, max_time_seconds=0.25)
    payload = result.model_dump(mode="python")

    with pytest.raises(ValidationError, match="only for large_aggregate"):
        ScalePlanningResult.model_validate(
            {
                **payload,
                "execution_path": "deterministic_fallback",
                "fallback_reason": "model_size_guard",
            }
        )
    with pytest.raises(ValidationError):
        ScalePlanningResult.model_validate(
            {**payload, "requires_human_confirmation": False}
        )
    with pytest.raises(ValidationError, match="fingerprint must match"):
        ScalePlanningResult.model_validate(
            {**payload, "scenario_fingerprint": "0" * 64}
        )
    altered_plan = result.plan.model_copy(update={"scenario_id": "SCN-SCALE-OTHER-01"})
    with pytest.raises(ValidationError, match="scenario identity"):
        ScalePlanningResult.model_validate({**payload, "plan": altered_plan})
    for invalid_limit in (0, -1, float("inf"), 2.01):
        with pytest.raises(ValueError, match="within the tier target"):
            build_bounded_scale_plan(
                artifact,
                max_time_seconds=invalid_limit,
            )
