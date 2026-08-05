"""Application services that turn repository state into usable responses."""

from __future__ import annotations

from pydantic import ValidationError

from .api_models import (
    ApplyEventsRequest,
    ComparePlansRequest,
    CreatePlanRequest,
    PlanAlgorithm,
    PlanComparison,
    PlanComparisonDelta,
    PlanComparisonMetrics,
    PlanGuidance,
    PlanListResponse,
    PlanRecord,
    PlanSummary,
    ScenarioListResponse,
    ScenarioOperationalSummary,
    ScenarioRecord,
    ScenarioSummary,
)
from .audit_models import AuditRecord
from .constraints import validate_plan
from .demo_export import SAFETY_NOTICE
from .events import EventApplicationError, apply_events
from .fifo_scheduler import build_fifo_plan
from .models import ResourceStatus, Scenario
from .optimizer import build_optimized_plan
from .planning_models import Plan, PlanStatus
from .planning_objectives import (
    OBJECTIVE_DISPLAY_NAMES,
    PlanningObjectiveProfile,
    cp_sat_algorithm_name,
)
from .repository import (
    EventAlreadyAppliedError,
    EventAlreadyRegisteredError,
    InMemoryScenarioRepository,
    InvalidPlanComparisonError,
    InvalidRevisionError,
    PlanAlreadyExistsError,
    ScenarioStateSnapshot,
    VersionConflictError,
)


_ALGORITHM_STORAGE_NAMES = {
    PlanAlgorithm.FIFO: "fifo_baseline_v1",
    PlanAlgorithm.CP_SAT: "cp_sat_priority_v1",
}


class ScenarioNotReadyForPlanningError(ValueError):
    def __init__(self, warnings: list[str]) -> None:
        super().__init__("scenario is not ready for planning")
        self.warnings = list(warnings)


class PlanningObjectiveNotSupportedError(ValueError):
    pass


def _plan_algorithm(plan: Plan) -> PlanAlgorithm:
    if plan.algorithm == _ALGORITHM_STORAGE_NAMES[PlanAlgorithm.FIFO]:
        return PlanAlgorithm.FIFO
    if plan.algorithm.startswith("cp_sat_"):
        return PlanAlgorithm.CP_SAT
    raise ValueError(f"unsupported stored planning algorithm: {plan.algorithm}")


def _plan_guidance(plan: Plan) -> PlanGuidance:
    algorithm = _plan_algorithm(plan)
    metrics = plan.metrics
    conflict_count = len(plan.violations)

    if plan.status is PlanStatus.INVALID:
        status_label = "存在冲突，暂不可采用"
    elif plan.status is PlanStatus.PARTIAL:
        status_label = "部分任务需要人工处理"
    else:
        status_label = "全部任务均可执行"

    if conflict_count:
        result_summary = f"发现 {conflict_count} 个资源或时间冲突，这套方案暂不可执行"
        recommended_action = "不要采用当前方案；先处理冲突并重新计算"
    elif metrics.unassigned_tasks == 0:
        result_summary = (
            f"全部 {metrics.total_tasks} 项任务已安排，"
            f"紧急任务按时保障率为 {metrics.critical_task_completion_rate_pct:.0f}%"
        )
        recommended_action = "核对具体任务、执行资源和服务时间后进入人工确认"
    elif metrics.critical_task_completion_rate_pct < 100:
        result_summary = (
            f"已安排 {metrics.assigned_tasks}/{metrics.total_tasks} 项任务，"
            "仍有紧急任务未能按时保障"
        )
        recommended_action = "优先查看未分配的紧急任务，并补充资源或调整服务时间"
    else:
        result_summary = (
            f"紧急任务均已安排，另有 {metrics.unassigned_tasks} 项任务需要人工处理"
        )
        recommended_action = "查看未分配原因，决定补充资源、调整时间或保留人工处置"

    if algorithm is PlanAlgorithm.CP_SAT:
        objective_name = OBJECTIVE_DISPLAY_NAMES[plan.objective_profile]
        display_name = (
            "系统优化建议"
            if plan.objective_profile is PlanningObjectiveProfile.BALANCED
            else f"系统优化建议（{objective_name}）"
        )
        objective_basis = {
            PlanningObjectiveProfile.BALANCED: "先保护紧急任务，再兼顾任务覆盖、优先级与等待",
            PlanningObjectiveProfile.CRITICAL_FIRST: "先保护紧急任务与高优先级任务，再扩大任务覆盖",
            PlanningObjectiveProfile.MINIMUM_WAIT: "在紧急任务和任务覆盖不降低的前提下优先减少等待",
            PlanningObjectiveProfile.MINIMUM_CHANGE: "在紧急任务和任务覆盖不降低的前提下优先保留资源安排",
        }[plan.objective_profile]
        tradeoff_summary = (
            f"{objective_basis}；当前平均等待 {metrics.average_wait_minutes:.1f} 分钟"
        )
    else:
        display_name = "原规则方案"
        tradeoff_summary = (
            "按任务出现的先后顺序安排，用于对照人工常用规则；"
            f"当前平均等待 {metrics.average_wait_minutes:.1f} 分钟"
        )

    return PlanGuidance(
        display_name=display_name,
        status_label=status_label,
        result_summary=result_summary,
        tradeoff_summary=tradeoff_summary,
        recommended_action=recommended_action,
        calculation_basis=[
            "具体人员或车辆",
            "任务地点和移动时间",
            "服务截止时间",
            "资源容量和重复占用检查",
        ],
    )


def _comparison_metrics(plan: Plan) -> PlanComparisonMetrics:
    metrics = plan.metrics
    return PlanComparisonMetrics(
        plan_id=plan.plan_id,
        scenario_version=plan.scenario_version,
        algorithm=_plan_algorithm(plan),
        assigned_tasks=metrics.assigned_tasks,
        unassigned_tasks=metrics.unassigned_tasks,
        total_tasks=metrics.total_tasks,
        task_completion_rate_pct=metrics.task_completion_rate_pct,
        critical_task_completion_rate_pct=metrics.critical_task_completion_rate_pct,
        average_wait_minutes=metrics.average_wait_minutes,
        max_wait_minutes=metrics.max_wait_minutes,
        overall_resource_utilization_pct=metrics.overall_resource_utilization_pct,
        violation_count=len(plan.violations),
    )


def _comparison_delta(
    baseline: PlanComparisonMetrics,
    candidate: PlanComparisonMetrics,
) -> PlanComparisonDelta:
    return PlanComparisonDelta(
        assigned_tasks=candidate.assigned_tasks - baseline.assigned_tasks,
        unassigned_tasks=candidate.unassigned_tasks - baseline.unassigned_tasks,
        total_tasks=candidate.total_tasks - baseline.total_tasks,
        task_completion_rate_pct=round(
            candidate.task_completion_rate_pct - baseline.task_completion_rate_pct,
            2,
        ),
        critical_task_completion_rate_pct=round(
            candidate.critical_task_completion_rate_pct
            - baseline.critical_task_completion_rate_pct,
            2,
        ),
        average_wait_minutes=round(
            candidate.average_wait_minutes - baseline.average_wait_minutes,
            2,
        ),
        max_wait_minutes=candidate.max_wait_minutes - baseline.max_wait_minutes,
        overall_resource_utilization_pct=round(
            candidate.overall_resource_utilization_pct
            - baseline.overall_resource_utilization_pct,
            2,
        ),
        violation_count=candidate.violation_count - baseline.violation_count,
    )


def _comparison_guidance(delta: PlanComparisonDelta) -> tuple[str, str]:
    if delta.violation_count < 0:
        preferred = "candidate"
    elif delta.violation_count > 0:
        preferred = "baseline"
    elif delta.critical_task_completion_rate_pct > 0:
        preferred = "candidate"
    elif delta.critical_task_completion_rate_pct < 0:
        preferred = "baseline"
    elif delta.task_completion_rate_pct > 0:
        preferred = "candidate"
    elif delta.task_completion_rate_pct < 0:
        preferred = "baseline"
    elif delta.average_wait_minutes < 0:
        preferred = "candidate"
    elif delta.average_wait_minutes > 0:
        preferred = "baseline"
    elif delta.max_wait_minutes < 0:
        preferred = "candidate"
    elif delta.max_wait_minutes > 0:
        preferred = "baseline"
    else:
        preferred = "equivalent"

    tradeoffs: list[str] = []
    if delta.assigned_tasks:
        direction = "增加" if delta.assigned_tasks > 0 else "减少"
        tradeoffs.append(f"已安排任务{direction} {abs(delta.assigned_tasks)} 项")
    if delta.critical_task_completion_rate_pct:
        direction = "提高" if delta.critical_task_completion_rate_pct > 0 else "降低"
        tradeoffs.append(
            f"紧急任务保障率{direction} "
            f"{abs(delta.critical_task_completion_rate_pct):.0f} 个百分点"
        )
    if delta.average_wait_minutes:
        direction = "增加" if delta.average_wait_minutes > 0 else "减少"
        tradeoffs.append(
            f"平均等待{direction} {abs(delta.average_wait_minutes):.2f} 分钟"
        )
    if delta.violation_count:
        direction = "增加" if delta.violation_count > 0 else "减少"
        tradeoffs.append(f"约束冲突{direction} {abs(delta.violation_count)} 个")

    detail = "，".join(tradeoffs) if tradeoffs else "核心指标没有变化"
    if preferred == "candidate":
        return (
            f"候选方案综合优先；相较基线，{detail}。",
            "建议优先复核候选方案，并确认等待时间、资源负载和具体任务安排后由人员决定是否采用。",
        )
    if preferred == "baseline":
        return (
            f"基线方案综合优先；候选方案相较基线，{detail}。",
            "建议保留基线方案，并检查候选方案的保障率、冲突或等待代价后再重新规划。",
        )
    return (
        "两套方案核心指标相当，没有形成明确的量化优势。",
        "请结合具体任务、资源和执行时段进行人工复核后选择方案。",
    )


class ScenarioService:
    def __init__(self, repository: InMemoryScenarioRepository) -> None:
        self.repository = repository

    def import_scenario(self, scenario: Scenario) -> ScenarioRecord:
        created = self.repository.create_scenario(scenario)
        return self.get_scenario(created.scenario_id)

    def list_scenarios(self, offset: int, limit: int) -> ScenarioListResponse:
        scenario_ids = self.repository.list_scenario_ids()
        selected_ids = scenario_ids[offset : offset + limit]
        items = [
            self._build_summary(self.repository.get_state_snapshot(scenario_id))
            for scenario_id in selected_ids
        ]
        return ScenarioListResponse(
            items=items,
            total=len(scenario_ids),
            offset=offset,
            limit=limit,
        )

    def get_scenario(self, scenario_id: str, version: int | None = None) -> ScenarioRecord:
        snapshot = self.repository.get_state_snapshot(scenario_id, version)
        return ScenarioRecord(
            summary=self._build_summary(snapshot),
            scenario=snapshot.scenario,
            current_version=snapshot.current_version,
            selected_version=snapshot.scenario.version,
            is_current_version=snapshot.scenario.version == snapshot.current_version,
            available_versions=list(snapshot.available_versions),
            pending_event_ids=list(snapshot.pending_event_ids),
            applied_event_ids=list(snapshot.applied_event_ids),
            safety_notice=SAFETY_NOTICE,
        )

    def apply_events(self, scenario_id: str, request: ApplyEventsRequest) -> ScenarioRecord:
        snapshot = self.repository.get_state_snapshot(scenario_id)
        if snapshot.current_version != request.expected_version:
            raise VersionConflictError(request.expected_version, snapshot.current_version)

        applied_ids = set(snapshot.applied_event_ids)
        repeated_ids = [event_id for event_id in request.event_ids if event_id in applied_ids]
        if repeated_ids:
            raise EventAlreadyAppliedError(
                f"event {repeated_ids[0]} was already applied to scenario {scenario_id}"
            )

        catalog_ids = {event.event_id for event in self.repository.get_events(scenario_id)}
        repeated_new_ids = [event.event_id for event in request.events if event.event_id in catalog_ids]
        if repeated_new_ids:
            raise EventAlreadyRegisteredError(
                f"event {repeated_new_ids[0]} is already registered for scenario {scenario_id}"
            )

        previously_applied = self.repository.get_events(
            scenario_id,
            snapshot.applied_event_ids,
        )
        registered_events = self.repository.get_events(scenario_id, request.event_ids)
        revision_input = self.repository.get_baseline(scenario_id)
        try:
            revision_input.events = [
                *previously_applied,
                *registered_events,
                *(event.model_copy(deep=True) for event in request.events),
            ]
            revision = apply_events(revision_input)
            revision.version = request.expected_version + 1
            revision = Scenario.model_validate(revision.model_dump(mode="python"))
        except (EventApplicationError, KeyError, ValidationError) as error:
            raise InvalidRevisionError(
                "the event batch cannot be applied to the immutable baseline"
            ) from error

        self.repository.commit_event_revision(
            scenario_id=scenario_id,
            expected_version=request.expected_version,
            revision=revision,
            event_ids=request.event_ids,
            new_events=request.events,
        )
        return self.get_scenario(scenario_id)

    def create_plan(self, scenario_id: str, request: CreatePlanRequest) -> PlanRecord:
        snapshot = self.repository.get_state_snapshot(scenario_id)
        if snapshot.current_version != request.expected_version:
            raise VersionConflictError(request.expected_version, snapshot.current_version)
        scenario = snapshot.scenario
        summary = self._build_summary(snapshot)
        if not summary.operational.ready_for_planning:
            raise ScenarioNotReadyForPlanningError(summary.operational.warnings)

        if (
            request.algorithm is PlanAlgorithm.CP_SAT
            and request.applied_objective is PlanningObjectiveProfile.MINIMUM_CHANGE
        ):
            raise PlanningObjectiveNotSupportedError(
                "minimum_change is only available for rolling runtime planning"
            )
        storage_algorithm = (
            _ALGORITHM_STORAGE_NAMES[PlanAlgorithm.FIFO]
            if request.algorithm is PlanAlgorithm.FIFO
            else cp_sat_algorithm_name(request.applied_objective)
        )
        existing_plans = self.repository.list_plans(
            scenario_id,
            scenario_version=request.expected_version,
            algorithm=storage_algorithm,
        )
        if existing_plans:
            raise PlanAlreadyExistsError(
                f"plan {existing_plans[0].plan_id} is already stored for scenario {scenario_id}"
            )

        if request.algorithm is PlanAlgorithm.FIFO:
            plan = build_fifo_plan(scenario)
        else:
            plan = build_optimized_plan(
                scenario,
                max_time_seconds=request.max_time_seconds,
                objective_profile=request.applied_objective,
            )

        independent_violations = validate_plan(
            scenario,
            plan.assignments,
            plan.unassigned_tasks,
        )
        if independent_violations != plan.violations:
            raise RuntimeError("planning result changed between calculation and validation")

        stored = self.repository.save_plan(
            scenario_id=scenario_id,
            expected_version=request.expected_version,
            plan=plan,
        )
        return self._build_plan_record(stored)

    def get_plan(self, plan_id: str) -> PlanRecord:
        return self._build_plan_record(self.repository.get_plan(plan_id))

    def list_plans(
        self,
        scenario_id: str,
        scenario_version: int | None = None,
        algorithm: PlanAlgorithm | None = None,
    ) -> PlanListResponse:
        if scenario_version is not None:
            self.repository.get_scenario(scenario_id, version=scenario_version)
        plans = self.repository.list_plans(
            scenario_id,
            scenario_version=scenario_version,
            algorithm=(
                _ALGORITHM_STORAGE_NAMES[PlanAlgorithm.FIFO]
                if algorithm is PlanAlgorithm.FIFO
                else None
            ),
        )
        if algorithm is PlanAlgorithm.CP_SAT:
            plans = tuple(plan for plan in plans if plan.algorithm.startswith("cp_sat_"))
        items = [self._build_plan_summary(plan) for plan in plans]
        return PlanListResponse(items=items, total=len(items))

    def compare_plans(
        self,
        scenario_id: str,
        request: ComparePlansRequest,
    ) -> PlanComparison:
        self.repository.get_scenario(scenario_id)
        baseline_plan = self.repository.get_plan(request.baseline_plan_id)
        candidate_plan = self.repository.get_plan(request.candidate_plan_id)
        if (
            baseline_plan.scenario_id != scenario_id
            or candidate_plan.scenario_id != scenario_id
        ):
            raise InvalidPlanComparisonError(
                "comparison plans must both belong to the requested scenario"
            )

        baseline_metrics = _comparison_metrics(baseline_plan)
        candidate_metrics = _comparison_metrics(candidate_plan)
        delta = _comparison_delta(baseline_metrics, candidate_metrics)
        conclusion, recommendation = _comparison_guidance(delta)
        comparison = PlanComparison(
            scenario_id=scenario_id,
            baseline_plan_id=baseline_plan.plan_id,
            candidate_plan_id=candidate_plan.plan_id,
            baseline_metrics=baseline_metrics,
            candidate_metrics=candidate_metrics,
            candidate_minus_baseline=delta,
            conclusion=conclusion,
            recommendation=recommendation,
            safety_notice=SAFETY_NOTICE,
        )
        self.repository.record_plan_comparison(
            scenario_id,
            baseline_plan.plan_id,
            candidate_plan.plan_id,
        )
        return comparison

    def list_audit_records(self, scenario_id: str, limit: int) -> list[AuditRecord]:
        records = self.repository.list_audit_records(scenario_id)
        return list(records[-limit:])

    def _build_plan_record(self, plan: Plan) -> PlanRecord:
        return PlanRecord(
            plan=plan,
            guidance=_plan_guidance(plan),
            safety_notice=SAFETY_NOTICE,
        )

    def _build_plan_summary(self, plan: Plan) -> PlanSummary:
        guidance = _plan_guidance(plan)
        return PlanSummary(
            plan_id=plan.plan_id,
            scenario_id=plan.scenario_id,
            scenario_version=plan.scenario_version,
            algorithm=_plan_algorithm(plan),
            objective_profile=plan.objective_profile,
            display_name=guidance.display_name,
            status=plan.status,
            status_label=guidance.status_label,
            assigned_tasks=plan.metrics.assigned_tasks,
            total_tasks=plan.metrics.total_tasks,
            urgent_task_completion_rate_pct=plan.metrics.critical_task_completion_rate_pct,
            average_wait_minutes=plan.metrics.average_wait_minutes,
            needs_manual_handling=plan.metrics.unassigned_tasks,
            constraint_conflicts=len(plan.violations),
            result_summary=guidance.result_summary,
            tradeoff_summary=guidance.tradeoff_summary,
            recommended_action=guidance.recommended_action,
        )

    def _build_summary(self, snapshot: ScenarioStateSnapshot) -> ScenarioSummary:
        scenario = snapshot.scenario
        available_resources = [
            resource
            for resource in scenario.resources
            if resource.status is ResourceStatus.AVAILABLE
        ]
        available_types = {resource.resource_type for resource in available_resources}
        required_types = {task.required_resource_type for task in scenario.tasks}

        warnings: list[str] = []
        if not scenario.flights:
            warnings.append("场景没有航班，无法形成航班保障闭环")
        if not scenario.tasks:
            warnings.append("场景没有保障任务，请先补充任务")
        if not scenario.resources:
            warnings.append("场景没有保障资源，请先补充资源")
        elif not available_resources:
            warnings.append("场景资源均不可用，请先恢复至少一项资源")
        missing_types = sorted(resource_type.value for resource_type in required_types - available_types)
        if missing_types:
            warnings.append(f"缺少可用任务资源类型：{', '.join(missing_types)}")

        ready_for_planning = bool(scenario.tasks and required_types & available_types)
        if not ready_for_planning:
            recommended_action = "根据警告补全任务或可用资源后再规划"
        elif snapshot.pending_event_ids:
            recommended_action = "先生成当前版本 FIFO 基线，再选择待处理事件进行重规划"
        else:
            recommended_action = "生成 FIFO 与 CP-SAT 方案并比较关键指标"

        return ScenarioSummary(
            scenario_id=scenario.scenario_id,
            name=scenario.name,
            version=scenario.version,
            run_mode=scenario.run_mode,
            data_classification=scenario.data_classification,
            operational=ScenarioOperationalSummary(
                flight_count=len(scenario.flights),
                task_count=len(scenario.tasks),
                resource_count=len(scenario.resources),
                zone_count=len(scenario.zones),
                pending_event_count=len(snapshot.pending_event_ids),
                applied_event_count=len(snapshot.applied_event_ids),
                ready_for_planning=ready_for_planning,
                warnings=warnings,
                recommended_action=recommended_action,
            ),
        )
