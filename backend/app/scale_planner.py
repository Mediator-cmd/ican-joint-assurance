"""Bounded M6 planning with explicit, independently validated fallback."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from math import isfinite
from typing import Literal

from ortools.sat.python import cp_model
from pydantic import Field, model_validator

from .constraints import validate_plan
from .demo_export import SAFETY_NOTICE
from .fifo_scheduler import build_fifo_plan, build_plan_metrics
from .models import ModelBase, Resource, ResourceStatus, Scenario, ServiceTask
from .planning_models import (
    Assignment,
    Plan,
    PlanStatus,
    UnassignedReason,
    UnassignedTask,
)
from .planning_objectives import PlanningObjectiveProfile
from .scale_models import (
    CANONICAL_SCALE_SCENARIO_FINGERPRINTS,
    CANONICAL_SCALE_SOLVER_LIMIT_SECONDS,
    BenchmarkExecutionPath,
    BenchmarkFallbackReason,
    ScaleProfile,
    ScaleTier,
    get_scale_profile,
)
from .scale_scenario_factory import ScaleScenarioArtifact
from .travel import RouteNotFoundError, shortest_travel_minutes


MAX_CP_SAT_TASKS = 500
MAX_LEGACY_ORDERING_PAIRS = 1_250_000
MAX_CANDIDATE_RESOURCES_PER_TASK = 3


class ScalePlanningError(RuntimeError):
    """Raised when a scale tier cannot return an independently safe plan."""


class ScalePlanningTimeout(ScalePlanningError):
    """Raised only when bounded CP-SAT returns UNKNOWN within its time limit."""


class ScaleModelEstimate(ModelBase):
    task_count: int = Field(gt=0)
    resource_count: int = Field(gt=0)
    compatible_pair_count: int = Field(ge=0)
    bounded_candidate_pair_count: int = Field(ge=0)
    legacy_ordering_pair_count: int = Field(ge=0)
    max_transition_minutes: int = Field(ge=0)
    task_limit: Literal[MAX_CP_SAT_TASKS] = MAX_CP_SAT_TASKS
    legacy_ordering_pair_limit: Literal[MAX_LEGACY_ORDERING_PAIRS] = (
        MAX_LEGACY_ORDERING_PAIRS
    )
    candidate_resources_per_task: Literal[MAX_CANDIDATE_RESOURCES_PER_TASK] = (
        MAX_CANDIDATE_RESOURCES_PER_TASK
    )
    guard_triggered: bool

    @model_validator(mode="after")
    def validate_estimate(self) -> ScaleModelEstimate:
        if self.bounded_candidate_pair_count > self.compatible_pair_count:
            raise ValueError("bounded candidate count cannot exceed compatible pairs")
        if self.bounded_candidate_pair_count > (
            self.task_count * self.candidate_resources_per_task
        ):
            raise ValueError("bounded candidate count exceeds its per-task cap")
        expected_guard = (
            self.task_count > self.task_limit
            or self.legacy_ordering_pair_count > self.legacy_ordering_pair_limit
        )
        if self.guard_triggered is not expected_guard:
            raise ValueError("scale model guard must be derived from the estimate")
        return self


class ScalePlanningResult(ModelBase):
    profile: ScaleProfile
    scenario_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_path: BenchmarkExecutionPath
    fallback_reason: BenchmarkFallbackReason | None = None
    model_estimate: ScaleModelEstimate
    plan: Plan
    hard_constraint_violation_count: Literal[0] = 0
    requires_human_confirmation: Literal[True] = True
    safety_notice: Literal[SAFETY_NOTICE] = SAFETY_NOTICE

    @model_validator(mode="after")
    def validate_result(self) -> ScalePlanningResult:
        if self.profile != get_scale_profile(self.profile.tier):
            raise ValueError("scale planning result must use the canonical profile")
        if self.scenario_fingerprint != CANONICAL_SCALE_SCENARIO_FINGERPRINTS[
            self.profile.tier
        ]:
            raise ValueError("scale planning result fingerprint must match its tier")
        if self.model_estimate.task_count != self.profile.task_count:
            raise ValueError("scale plan estimate must match the profile task count")
        if self.model_estimate.resource_count != self.profile.resource_count:
            raise ValueError("scale plan estimate must match the profile resource count")
        if self.plan.metrics.total_tasks != self.profile.task_count:
            raise ValueError("scale plan must cover the profile task count")
        expected_scenario_id = (
            f"SCN-SCALE-{self.profile.tier.value.upper().replace('_', '-')}-01"
        )
        if self.plan.scenario_id != expected_scenario_id or self.plan.scenario_version != 1:
            raise ValueError("scale plan scenario identity must match its tier")
        if len(self.plan.assignments) != self.plan.metrics.assigned_tasks:
            raise ValueError("scale plan assignment metrics must match its task list")
        if len(self.plan.unassigned_tasks) != self.plan.metrics.unassigned_tasks:
            raise ValueError("scale plan unassigned metrics must match its task list")
        if (
            self.plan.metrics.assigned_tasks + self.plan.metrics.unassigned_tasks
            != self.profile.task_count
        ):
            raise ValueError("scale plan must account for every task")
        if self.plan.status is PlanStatus.INVALID or self.plan.violations:
            raise ValueError("scale planning results cannot contain invalid plans")

        if self.execution_path is BenchmarkExecutionPath.CP_SAT:
            if self.fallback_reason is not None:
                raise ValueError("CP-SAT scale results cannot claim a fallback")
            if self.model_estimate.guard_triggered:
                raise ValueError("guarded scale results cannot claim a CP-SAT path")
            if self.plan.algorithm != "bounded_scale_cp_sat_v2":
                raise ValueError("CP-SAT scale results require the bounded algorithm")
            return self

        if self.execution_path is BenchmarkExecutionPath.FIFO_BASELINE:
            raise ValueError("scale planning results cannot claim the FIFO baseline path")

        if self.profile.tier is not ScaleTier.LARGE_AGGREGATE:
            raise ValueError("scale fallback is allowed only for large_aggregate")
        if self.fallback_reason is None:
            raise ValueError("scale fallback must publish a bounded reason")
        if (
            self.fallback_reason is BenchmarkFallbackReason.MODEL_SIZE_GUARD
            and not self.model_estimate.guard_triggered
        ):
            raise ValueError("model_size_guard requires a triggered model guard")
        if (
            self.fallback_reason is BenchmarkFallbackReason.TIME_LIMIT
            and self.model_estimate.guard_triggered
        ):
            raise ValueError("time_limit cannot bypass a triggered model guard")
        if self.plan.algorithm != "deterministic_scale_fallback_v1":
            raise ValueError("fallback results require the deterministic algorithm")
        return self


TravelMatrix = dict[tuple[str, str], int]
EligibleResources = dict[str, list[tuple[Resource, int]]]
GreedyHints = dict[str, tuple[str, int] | None]


def _validated_artifact(artifact: ScaleScenarioArtifact) -> ScaleScenarioArtifact:
    return ScaleScenarioArtifact.model_validate(artifact.model_dump(mode="python"))


def _build_travel_matrix(scenario: Scenario) -> TravelMatrix:
    matrix: TravelMatrix = {}
    for source in scenario.zones:
        for target in scenario.zones:
            try:
                matrix[(source.zone_id, target.zone_id)] = shortest_travel_minutes(
                    scenario,
                    source.zone_id,
                    target.zone_id,
                )
            except RouteNotFoundError:
                continue
    return matrix


def _eligible_resources(
    scenario: Scenario,
    travel_matrix: TravelMatrix,
) -> EligibleResources:
    resources = sorted(scenario.resources, key=lambda item: item.resource_id)
    eligible: EligibleResources = {}
    for task in scenario.tasks:
        candidates = [
            (
                resource,
                travel_matrix[(resource.current_zone_id, task.origin_zone_id)],
            )
            for resource in resources
            if resource.status is ResourceStatus.AVAILABLE
            and resource.resource_type is task.required_resource_type
            and resource.capacity >= task.party_size
            and (resource.current_zone_id, task.origin_zone_id) in travel_matrix
        ]
        candidates.sort(key=lambda item: (item[1], item[0].resource_id))
        eligible[task.task_id] = candidates
    return eligible


def _estimate_from(
    scenario: Scenario,
    travel_matrix: TravelMatrix,
    eligible_resources: EligibleResources,
) -> ScaleModelEstimate:
    compatible_pair_count = sum(len(items) for items in eligible_resources.values())
    bounded_candidate_pair_count = sum(
        min(len(items), MAX_CANDIDATE_RESOURCES_PER_TASK)
        for items in eligible_resources.values()
    )
    tasks_by_resource: dict[str, int] = defaultdict(int)
    for candidates in eligible_resources.values():
        for resource, _ in candidates:
            tasks_by_resource[resource.resource_id] += 1
    legacy_ordering_pair_count = sum(
        count * (count - 1) // 2 for count in tasks_by_resource.values()
    )
    max_transition_minutes = max(travel_matrix.values(), default=0)
    guard_triggered = (
        len(scenario.tasks) > MAX_CP_SAT_TASKS
        or legacy_ordering_pair_count > MAX_LEGACY_ORDERING_PAIRS
    )
    return ScaleModelEstimate(
        task_count=len(scenario.tasks),
        resource_count=len(scenario.resources),
        compatible_pair_count=compatible_pair_count,
        bounded_candidate_pair_count=bounded_candidate_pair_count,
        legacy_ordering_pair_count=legacy_ordering_pair_count,
        max_transition_minutes=max_transition_minutes,
        guard_triggered=guard_triggered,
    )


def estimate_scale_model(artifact: ScaleScenarioArtifact) -> ScaleModelEstimate:
    """Estimate model growth without constructing a CP-SAT model."""

    validated = _validated_artifact(artifact)
    travel_matrix = _build_travel_matrix(validated.scenario)
    eligible_resources = _eligible_resources(validated.scenario, travel_matrix)
    return _estimate_from(validated.scenario, travel_matrix, eligible_resources)


def _unassigned_detail(
    scenario: Scenario,
    task: ServiceTask,
    travel_matrix: TravelMatrix,
) -> UnassignedTask:
    matching = [
        resource
        for resource in scenario.resources
        if resource.resource_type is task.required_resource_type
        and resource.capacity >= task.party_size
    ]
    if not matching:
        return UnassignedTask(
            task_id=task.task_id,
            reason=UnassignedReason.NO_COMPATIBLE_RESOURCE,
            detail="No resource matches both the required type and capacity",
        )
    available = [
        resource
        for resource in matching
        if resource.status is ResourceStatus.AVAILABLE
    ]
    if not available:
        return UnassignedTask(
            task_id=task.task_id,
            reason=UnassignedReason.RESOURCE_UNAVAILABLE,
            detail="All compatible resources are unavailable",
        )
    reachable = [
        resource
        for resource in available
        if (resource.current_zone_id, task.origin_zone_id) in travel_matrix
    ]
    if not reachable:
        return UnassignedTask(
            task_id=task.task_id,
            reason=UnassignedReason.NO_ROUTE,
            detail="No compatible resource can reach the task origin",
        )
    return UnassignedTask(
        task_id=task.task_id,
        reason=UnassignedReason.PRIORITY_TRADEOFF,
        detail="Bounded optimization prioritized other tasks within shared windows",
    )


def _build_greedy_hints(
    scenario: Scenario,
    transition_buffer: int,
    eligible_resources: EligibleResources,
) -> GreedyHints:
    available_at = {
        resource.resource_id: int(
            (resource.available_from - scenario.window_start).total_seconds() // 60
        )
        for resource in scenario.resources
    }
    hints: GreedyHints = {}
    for task in sorted(
        scenario.tasks,
        key=lambda item: (item.release_at, item.priority, item.task_id),
    ):
        release = int((task.release_at - scenario.window_start).total_seconds() // 60)
        deadline = int((task.deadline_at - scenario.window_start).total_seconds() // 60)
        candidates: list[tuple[int, int, str]] = []
        for resource, _ in eligible_resources[task.task_id][
            :MAX_CANDIDATE_RESOURCES_PER_TASK
        ]:
            service_start = max(
                release,
                available_at[resource.resource_id] + transition_buffer,
            )
            service_end = service_start + task.duration_minutes
            available_to = int(
                (resource.available_to - scenario.window_start).total_seconds() // 60
            )
            if service_end <= deadline and service_end <= available_to:
                candidates.append(
                    (service_end, service_start, resource.resource_id)
                )
        if not candidates:
            hints[task.task_id] = None
            continue
        service_end, service_start, resource_id = min(candidates)
        hints[task.task_id] = (resource_id, service_start)
        available_at[resource_id] = service_end
    return hints


def _build_bounded_cp_sat_plan(
    scenario: Scenario,
    max_time_seconds: float,
    estimate: ScaleModelEstimate,
    travel_matrix: TravelMatrix,
    eligible_resources: EligibleResources,
) -> Plan:
    model = cp_model.CpModel()
    horizon = int((scenario.window_end - scenario.window_start).total_seconds() // 60)
    transition_buffer = estimate.max_transition_minutes

    assigned: dict[str, cp_model.IntVar] = {}
    starts: dict[str, cp_model.IntVar] = {}
    ends: dict[str, cp_model.IntVar] = {}
    waits: dict[str, cp_model.IntVar] = {}
    use_resource: dict[tuple[str, str], cp_model.IntVar] = {}
    slot_starts: dict[str, cp_model.IntVar] = {}
    candidates_by_task: dict[str, list[str]] = {}
    intervals_by_resource: dict[str, list[cp_model.IntervalVar]] = {
        resource.resource_id: [] for resource in scenario.resources
    }
    resource_rank_costs: list[cp_model.LinearExpr] = []

    for task in scenario.tasks:
        task_id = task.task_id
        release = int((task.release_at - scenario.window_start).total_seconds() // 60)
        deadline = int((task.deadline_at - scenario.window_start).total_seconds() // 60)
        assigned[task_id] = model.NewBoolVar(f"assigned_{task_id}")
        starts[task_id] = model.NewIntVar(0, horizon, f"start_{task_id}")
        ends[task_id] = model.NewIntVar(0, horizon, f"end_{task_id}")
        waits[task_id] = model.NewIntVar(0, horizon, f"wait_{task_id}")
        slot_start = model.NewIntVar(-transition_buffer, horizon, f"slot_{task_id}")
        slot_starts[task_id] = slot_start
        model.Add(ends[task_id] == starts[task_id] + task.duration_minutes)
        model.Add(slot_start + transition_buffer == starts[task_id])

        candidates = eligible_resources[task_id][
            :MAX_CANDIDATE_RESOURCES_PER_TASK
        ]
        candidate_ids: list[str] = []
        for rank, (resource, _) in enumerate(candidates, start=1):
            resource_id = resource.resource_id
            candidate_ids.append(resource_id)
            selected = model.NewBoolVar(f"use_{task_id}_{resource_id}")
            use_resource[(task_id, resource_id)] = selected
            resource_rank_costs.append(selected * rank)
            available_from = int(
                (resource.available_from - scenario.window_start).total_seconds() // 60
            )
            available_to = int(
                (resource.available_to - scenario.window_start).total_seconds() // 60
            )
            model.Add(slot_start >= available_from).OnlyEnforceIf(selected)
            model.Add(ends[task_id] <= available_to).OnlyEnforceIf(selected)
            interval = model.NewOptionalIntervalVar(
                slot_start,
                transition_buffer + task.duration_minutes,
                ends[task_id],
                selected,
                f"interval_{task_id}_{resource_id}",
            )
            intervals_by_resource[resource_id].append(interval)

        candidates_by_task[task_id] = candidate_ids
        model.Add(
            sum(use_resource[(task_id, resource_id)] for resource_id in candidate_ids)
            == assigned[task_id]
        )
        model.Add(starts[task_id] >= release).OnlyEnforceIf(assigned[task_id])
        model.Add(ends[task_id] <= deadline).OnlyEnforceIf(assigned[task_id])
        model.Add(waits[task_id] == starts[task_id] - release).OnlyEnforceIf(
            assigned[task_id]
        )
        model.Add(starts[task_id] == release).OnlyEnforceIf(assigned[task_id].Not())
        model.Add(waits[task_id] == 0).OnlyEnforceIf(assigned[task_id].Not())

    for intervals in intervals_by_resource.values():
        if intervals:
            model.AddNoOverlap(intervals)

    greedy_hints = _build_greedy_hints(
        scenario,
        transition_buffer,
        eligible_resources,
    )
    for task in scenario.tasks:
        task_id = task.task_id
        release = int((task.release_at - scenario.window_start).total_seconds() // 60)
        hint = greedy_hints[task_id]
        if hint is None:
            model.AddHint(assigned[task_id], 0)
            model.AddHint(starts[task_id], release)
            model.AddHint(ends[task_id], release + task.duration_minutes)
            model.AddHint(waits[task_id], 0)
            model.AddHint(slot_starts[task_id], release - transition_buffer)
            for resource_id in candidates_by_task[task_id]:
                model.AddHint(use_resource[(task_id, resource_id)], 0)
            continue
        selected_resource_id, service_start = hint
        model.AddHint(assigned[task_id], 1)
        model.AddHint(starts[task_id], service_start)
        model.AddHint(ends[task_id], service_start + task.duration_minutes)
        model.AddHint(waits[task_id], service_start - release)
        model.AddHint(slot_starts[task_id], service_start - transition_buffer)
        for resource_id in candidates_by_task[task_id]:
            model.AddHint(
                use_resource[(task_id, resource_id)],
                int(resource_id == selected_resource_id),
            )

    critical_count = sum(
        assigned[task.task_id] for task in scenario.tasks if task.priority == 1
    )
    total_assigned = sum(assigned.values())
    priority_score = sum(
        assigned[task.task_id] * (6 - task.priority) for task in scenario.tasks
    )
    total_wait = sum(waits.values())
    rank_cost = sum(resource_rank_costs)
    lower_order_max = horizon * len(scenario.tasks) + (
        MAX_CANDIDATE_RESOURCES_PER_TASK * len(scenario.tasks)
    )
    priority_coefficient = lower_order_max + 1
    total_coefficient = priority_coefficient * (5 * len(scenario.tasks) + 1)
    critical_coefficient = total_coefficient * (len(scenario.tasks) + 1)
    model.Maximize(
        critical_count * critical_coefficient
        + total_assigned * total_coefficient
        + priority_score * priority_coefficient
        - total_wait
        - rank_cost
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max_time_seconds
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    status = solver.Solve(model)
    if status == cp_model.UNKNOWN:
        raise ScalePlanningTimeout("bounded CP-SAT reached its time limit")
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise ScalePlanningError(
            "bounded CP-SAT did not return a valid planning state"
        )

    assignments_by_resource: dict[str, list[ServiceTask]] = defaultdict(list)
    unassigned_tasks: list[UnassignedTask] = []
    for task in scenario.tasks:
        if not solver.Value(assigned[task.task_id]):
            unassigned_tasks.append(
                _unassigned_detail(scenario, task, travel_matrix)
            )
            continue
        resource_id = next(
            resource_id
            for resource_id in candidates_by_task[task.task_id]
            if solver.Value(use_resource[(task.task_id, resource_id)])
        )
        assignments_by_resource[resource_id].append(task)

    resources_by_id = {
        resource.resource_id: resource for resource in scenario.resources
    }
    assignments: list[Assignment] = []
    for resource_id, resource_tasks in assignments_by_resource.items():
        resource = resources_by_id[resource_id]
        previous_end = resource.available_from
        previous_zone = resource.current_zone_id
        for task in sorted(
            resource_tasks,
            key=lambda item: (solver.Value(starts[item.task_id]), item.task_id),
        ):
            reposition_minutes = travel_matrix[
                (previous_zone, task.origin_zone_id)
            ]
            travel_started_at = previous_end
            travel_ended_at = previous_end + timedelta(minutes=reposition_minutes)
            service_started_at = max(
                task.release_at,
                travel_ended_at,
            )
            service_ended_at = service_started_at + timedelta(
                minutes=task.duration_minutes
            )
            assignments.append(
                Assignment(
                    assignment_id=f"ASG-{task.task_id.removeprefix('TASK-')}",
                    task_id=task.task_id,
                    resource_id=resource_id,
                    resource_type=resource.resource_type,
                    resource_start_zone_id=previous_zone,
                    origin_zone_id=task.origin_zone_id,
                    destination_zone_id=task.destination_zone_id,
                    travel_started_at=travel_started_at,
                    travel_ended_at=travel_ended_at,
                    service_started_at=service_started_at,
                    service_ended_at=service_ended_at,
                    reposition_minutes=reposition_minutes,
                    service_minutes=task.duration_minutes,
                    wait_minutes=int(
                        (service_started_at - task.release_at).total_seconds() // 60
                    ),
                )
            )
            previous_end = service_ended_at
            previous_zone = task.destination_zone_id

    assignments.sort(key=lambda item: (item.service_started_at, item.task_id))
    unassigned_tasks.sort(key=lambda item: item.task_id)
    violations = validate_plan(scenario, assignments, unassigned_tasks)
    if violations:
        raise ScalePlanningError(
            "bounded CP-SAT result failed independent constraint validation"
        )
    status_value = PlanStatus.PARTIAL if unassigned_tasks else PlanStatus.EXECUTABLE
    plan = Plan(
        plan_id=(
            f"PLAN-{scenario.scenario_id.removeprefix('SCN-')}-V{scenario.version}"
            "-BOUNDED-CP-SAT"
        ),
        scenario_id=scenario.scenario_id,
        scenario_version=scenario.version,
        algorithm="bounded_scale_cp_sat_v2",
        objective_profile=PlanningObjectiveProfile.BALANCED,
        generated_at=scenario.window_start,
        status=status_value,
        assignments=assignments,
        unassigned_tasks=unassigned_tasks,
        violations=[],
        metrics=build_plan_metrics(scenario, assignments, unassigned_tasks),
    )
    return Plan.model_validate(plan.model_dump(mode="python"))


def _build_fallback_plan(scenario: Scenario) -> Plan:
    fifo_plan = build_fifo_plan(scenario)
    violations = validate_plan(
        scenario,
        fifo_plan.assignments,
        fifo_plan.unassigned_tasks,
    )
    if violations or fifo_plan.violations != violations:
        raise ScalePlanningError(
            "deterministic fallback failed independent constraint validation"
        )
    fallback = fifo_plan.model_copy(
        update={
            "plan_id": (
                f"PLAN-{scenario.scenario_id.removeprefix('SCN-')}"
                f"-V{scenario.version}-DETERMINISTIC-FALLBACK"
            ),
            "algorithm": "deterministic_scale_fallback_v1",
        },
        deep=True,
    )
    return Plan.model_validate(fallback.model_dump(mode="python"))


def _model_requires_guard(estimate: ScaleModelEstimate) -> bool:
    return estimate.guard_triggered


def build_bounded_scale_plan(
    artifact: ScaleScenarioArtifact,
    *,
    max_time_seconds: float | None = None,
) -> ScalePlanningResult:
    """Return a bounded CP-SAT plan or an explicit large-tier safe fallback."""

    validated = _validated_artifact(artifact)
    profile = validated.profile
    time_limit = (
        CANONICAL_SCALE_SOLVER_LIMIT_SECONDS[profile.tier]
        if max_time_seconds is None
        else max_time_seconds
    )
    if (
        not isfinite(time_limit)
        or time_limit <= 0
        or time_limit > profile.target_seconds
    ):
        raise ValueError("scale planning time limit must be within the tier target")

    scenario = validated.scenario
    travel_matrix = _build_travel_matrix(scenario)
    eligible_resources = _eligible_resources(scenario, travel_matrix)
    estimate = _estimate_from(scenario, travel_matrix, eligible_resources)

    execution_path = BenchmarkExecutionPath.CP_SAT
    fallback_reason: BenchmarkFallbackReason | None = None
    if _model_requires_guard(estimate):
        if not profile.fallback_allowed:
            raise ScalePlanningError(
                "model size guard cannot fall back for small or medium tiers"
            )
        execution_path = BenchmarkExecutionPath.DETERMINISTIC_FALLBACK
        fallback_reason = BenchmarkFallbackReason.MODEL_SIZE_GUARD
        plan = _build_fallback_plan(scenario)
    else:
        try:
            plan = _build_bounded_cp_sat_plan(
                scenario,
                time_limit,
                estimate,
                travel_matrix,
                eligible_resources,
            )
        except ScalePlanningTimeout as error:
            if not profile.fallback_allowed:
                raise ScalePlanningError(
                    "CP-SAT did not return a safe plan within the tier limit"
                ) from error
            execution_path = BenchmarkExecutionPath.DETERMINISTIC_FALLBACK
            fallback_reason = BenchmarkFallbackReason.TIME_LIMIT
            plan = _build_fallback_plan(scenario)

    independent_violations = validate_plan(
        scenario,
        plan.assignments,
        plan.unassigned_tasks,
    )
    if independent_violations or independent_violations != plan.violations:
        raise ScalePlanningError("scale plan failed final independent validation")
    return ScalePlanningResult(
        profile=profile,
        scenario_fingerprint=validated.fingerprint_sha256,
        execution_path=execution_path,
        fallback_reason=fallback_reason,
        model_estimate=estimate,
        plan=plan,
    )
