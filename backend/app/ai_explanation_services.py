"""Read-only fact construction and wording for M5 plan explanations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from uuid import uuid4

from pydantic import ValidationError

from .ai_explanation_provider import ModelPlanExplanation, PlanExplanationProvider
from .ai_models import (
    AssistanceFallbackReason,
    AssistanceSource,
    AssistanceTrace,
    ExplanationClaim,
    ExplanationEvidence,
    ExplanationFactKind,
    ExplanationFocus,
    PlanExplanationRequest,
    PlanExplanationResponse,
    RequestedAssistanceMode,
    RuntimePlanExplanationContext,
    ScenarioPlanExplanationContext,
)
from .ai_provider import (
    AIProviderInvalidOutputError,
    AIProviderRequestError,
    AIProviderTimeoutError,
)
from .ai_services import (
    AssistantContextNotFoundError,
    AssistantPlanContextMismatchError,
    AssistantRevisionConflictError,
)
from .models import FlightEvent, FlightEventType, Scenario
from .planning_models import Assignment, Plan, PlanStatus
from .planning_objectives import OBJECTIVE_DISPLAY_NAMES
from .repository import (
    InMemoryScenarioRepository,
    PlanNotFoundError,
    ScenarioNotFoundError,
    ScenarioVersionNotFoundError,
)
from .runtime_models import EventRuntimeProjection, RuntimeSessionSnapshot
from .runtime_repository import (
    RuntimeRevisionConflictError,
    RuntimeSessionNotFoundError,
)
from .runtime_services import RuntimeSessionService


@dataclass(frozen=True, slots=True)
class _ResolvedPlanContext:
    scenario: Scenario
    primary_plan: Plan
    baseline_plan: Plan | None
    scenario_events: tuple[FlightEvent, ...] = ()
    runtime_snapshot: RuntimeSessionSnapshot | None = None


class PlanExplanationService:
    """Explain only plans that belong to the requested authoritative context."""

    def __init__(
        self,
        scenario_repository: InMemoryScenarioRepository,
        runtime_service: RuntimeSessionService,
        *,
        provider: PlanExplanationProvider | None = None,
        explanation_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.scenario_repository = scenario_repository
        self.runtime_service = runtime_service
        self._provider = provider
        self._explanation_id_factory = explanation_id_factory or (
            lambda: f"EXPL-{uuid4().hex[:16].upper()}"
        )

    def explain_plan(self, request: PlanExplanationRequest) -> PlanExplanationResponse:
        resolved = self._resolve_context(request.context)
        evidence = build_explanation_evidence(resolved)

        if request.assistance_mode is RequestedAssistanceMode.DETERMINISTIC_ONLY:
            return self._deterministic_response(
                request,
                resolved,
                evidence,
                AssistanceTrace(source=AssistanceSource.DETERMINISTIC_RULES),
            )
        if self._provider is None:
            return self._deterministic_response(
                request,
                resolved,
                evidence,
                AssistanceTrace(
                    source=AssistanceSource.DETERMINISTIC_RULES,
                    fallback_reason=AssistanceFallbackReason.MODEL_NOT_CONFIGURED,
                ),
            )

        try:
            output = self._provider.explain(
                evidence,
                focus=request.focus,
                question=request.question,
            )
            return self._model_response(request, evidence, output)
        except AIProviderTimeoutError:
            fallback = AssistanceFallbackReason.MODEL_TIMEOUT
        except AIProviderRequestError:
            fallback = AssistanceFallbackReason.PROVIDER_ERROR
        except (AIProviderInvalidOutputError, ValidationError):
            fallback = AssistanceFallbackReason.INVALID_MODEL_OUTPUT
        return self._deterministic_response(
            request,
            resolved,
            evidence,
            AssistanceTrace(
                source=AssistanceSource.DETERMINISTIC_RULES,
                provider_attempted=True,
                fallback_reason=fallback,
            ),
        )

    def _resolve_context(
        self,
        context: ScenarioPlanExplanationContext | RuntimePlanExplanationContext,
    ) -> _ResolvedPlanContext:
        if isinstance(context, ScenarioPlanExplanationContext):
            return self._resolve_scenario_context(context)
        return self._resolve_runtime_context(context)

    def _resolve_scenario_context(
        self,
        context: ScenarioPlanExplanationContext,
    ) -> _ResolvedPlanContext:
        try:
            scenario = self.scenario_repository.get_scenario(
                context.scenario_id,
                context.scenario_version,
            )
            primary = self.scenario_repository.get_plan(context.plan_id)
            baseline = (
                self.scenario_repository.get_plan(context.baseline_plan_id)
                if context.baseline_plan_id is not None
                else None
            )
        except (
            ScenarioNotFoundError,
            ScenarioVersionNotFoundError,
            PlanNotFoundError,
        ) as error:
            raise AssistantContextNotFoundError(
                "assistant scenario plan context was not found"
            ) from error

        self._require_plan_context(
            primary,
            context.scenario_id,
            context.scenario_version,
        )
        if baseline is not None:
            self._require_plan_context(
                baseline,
                context.scenario_id,
                context.scenario_version,
            )
        return _ResolvedPlanContext(
            scenario=scenario,
            primary_plan=primary,
            baseline_plan=baseline,
            scenario_events=tuple(scenario.events),
        )

    def _resolve_runtime_context(
        self,
        context: RuntimePlanExplanationContext,
    ) -> _ResolvedPlanContext:
        try:
            snapshot, source = self.runtime_service.get_explanation_context(
                context.session_id,
                context.revision,
            )
        except RuntimeSessionNotFoundError as error:
            raise AssistantContextNotFoundError(
                f"assistant runtime session {context.session_id} was not found"
            ) from error
        except RuntimeRevisionConflictError as error:
            raise AssistantRevisionConflictError(
                error.expected_revision,
                error.current_revision,
            ) from error

        available = {
            snapshot.active_plan_id: snapshot.active_plan_detail,
        }
        if snapshot.candidate_plan_id is not None:
            available[snapshot.candidate_plan_id] = snapshot.candidate_plan_detail
        primary = available.get(context.plan_id)
        if primary is None:
            raise AssistantPlanContextMismatchError(context.plan_id)
        baseline = None
        if context.baseline_plan_id is not None:
            baseline = available.get(context.baseline_plan_id)
            if baseline is None:
                raise AssistantPlanContextMismatchError(context.baseline_plan_id)

        self._require_runtime_plan_context(primary, snapshot)
        if baseline is not None:
            self._require_runtime_plan_context(baseline, snapshot)
        return _ResolvedPlanContext(
            scenario=source.scenario,
            primary_plan=primary,
            baseline_plan=baseline,
            runtime_snapshot=snapshot,
        )

    @staticmethod
    def _require_plan_context(plan: Plan, scenario_id: str, version: int) -> None:
        if plan.scenario_id != scenario_id or plan.scenario_version != version:
            raise AssistantPlanContextMismatchError(plan.plan_id)

    @staticmethod
    def _require_runtime_plan_context(
        plan: Plan,
        snapshot: RuntimeSessionSnapshot,
    ) -> None:
        if (
            plan.scenario_id != snapshot.scenario_id
            or plan.scenario_version > snapshot.current_scenario_version
        ):
            raise AssistantPlanContextMismatchError(plan.plan_id)
        if (
            snapshot.candidate_plan_id == plan.plan_id
            and plan.scenario_version != snapshot.current_scenario_version
        ):
            raise AssistantPlanContextMismatchError(plan.plan_id)

    def _deterministic_response(
        self,
        request: PlanExplanationRequest,
        resolved: _ResolvedPlanContext,
        evidence: list[ExplanationEvidence],
        trace: AssistanceTrace,
    ) -> PlanExplanationResponse:
        summary, tradeoffs, next_step, unresolved = render_deterministic_explanation(
            resolved,
            evidence,
            request.focus,
        )
        return PlanExplanationResponse(
            explanation_id=self._explanation_id_factory(),
            context=request.context,
            trace=trace,
            summary=summary,
            tradeoffs=tradeoffs,
            recommended_next_step=next_step,
            evidence=evidence,
            unresolved_questions=unresolved,
        )

    def _model_response(
        self,
        request: PlanExplanationRequest,
        evidence: list[ExplanationEvidence],
        output: ModelPlanExplanation,
    ) -> PlanExplanationResponse:
        assert self._provider is not None
        return PlanExplanationResponse(
            explanation_id=self._explanation_id_factory(),
            context=request.context,
            trace=AssistanceTrace(
                source=AssistanceSource.LANGUAGE_MODEL,
                provider_attempted=True,
                model_label=self._provider.model_label,
            ),
            summary=output.summary.model_dump(mode="python"),
            tradeoffs=[item.model_dump(mode="python") for item in output.tradeoffs],
            recommended_next_step=output.recommended_next_step.model_dump(mode="python"),
            evidence=evidence,
            unresolved_questions=output.unresolved_questions,
        )


def build_explanation_evidence(
    resolved: _ResolvedPlanContext,
) -> list[ExplanationEvidence]:
    evidence = _plan_evidence(
        resolved.primary_plan,
        resolved.scenario,
        role="PRIMARY",
        include_task_details=True,
    )
    if resolved.baseline_plan is not None:
        evidence.extend(
            _plan_evidence(
                resolved.baseline_plan,
                resolved.scenario,
                role="BASELINE",
                include_task_details=False,
            )
        )
        evidence.extend(
            _plan_change_evidence(
                resolved.primary_plan,
                resolved.baseline_plan,
            )
        )
    else:
        evidence.append(
            ExplanationEvidence(
                evidence_id="FACT-CONTEXT-BASELINE",
                kind=ExplanationFactKind.PLAN_CHANGE,
                entity_ids=[resolved.primary_plan.plan_id],
                field="baseline_plan_id",
                value="not_provided",
                statement="本次解释未提供基线方案，只能说明当前方案事实。",
            )
        )

    runtime = resolved.runtime_snapshot
    if runtime is not None:
        for event in sorted(runtime.events, key=lambda item: (item.occurred_at, item.event_id))[:12]:
            evidence.append(_runtime_event_evidence(event))
    else:
        for event in sorted(
            resolved.scenario_events,
            key=lambda item: (item.occurred_at, item.event_id),
        )[:12]:
            evidence.append(_scenario_event_evidence(event))
    if len(evidence) > 100:
        raise ValueError("explanation evidence exceeded the contract limit")
    return evidence


def _plan_evidence(
    plan: Plan,
    scenario: Scenario,
    *,
    role: str,
    include_task_details: bool,
) -> list[ExplanationEvidence]:
    metrics = plan.metrics
    prefix = f"FACT-{role}"
    status_label = {
        PlanStatus.EXECUTABLE: "全部任务可执行",
        PlanStatus.PARTIAL: "部分任务需要人工处理",
        PlanStatus.INVALID: "存在硬约束冲突",
    }[plan.status]
    facts = [
        _fact(
            f"{prefix}-STATUS",
            ExplanationFactKind.CONSTRAINT,
            [plan.plan_id],
            "status",
            plan.status.value,
            f"方案 {plan.plan_id} 状态为“{status_label}”。",
        ),
        _fact(
            f"{prefix}-OBJECTIVE",
            ExplanationFactKind.OBJECTIVE,
            [plan.plan_id],
            "objective_profile",
            plan.objective_profile.value,
            f"方案使用“{OBJECTIVE_DISPLAY_NAMES[plan.objective_profile]}”确定性目标。",
        ),
        _metric_fact(prefix, plan, "TOTAL", "total_tasks", metrics.total_tasks, "任务总数"),
        _metric_fact(prefix, plan, "ASSIGNED", "assigned_tasks", metrics.assigned_tasks, "已安排任务数"),
        _metric_fact(prefix, plan, "UNASSIGNED", "unassigned_tasks", metrics.unassigned_tasks, "未安排任务数"),
        _metric_fact(
            prefix,
            plan,
            "CRITICAL",
            "critical_task_completion_rate_pct",
            _format_number(metrics.critical_task_completion_rate_pct),
            "P1 关键任务完成率",
            suffix="%",
        ),
        _metric_fact(
            prefix,
            plan,
            "AVERAGE-WAIT",
            "average_wait_minutes",
            _format_number(metrics.average_wait_minutes),
            "平均等待",
            suffix=" 分钟",
        ),
        _metric_fact(
            prefix,
            plan,
            "UTILIZATION",
            "overall_resource_utilization_pct",
            _format_number(metrics.overall_resource_utilization_pct),
            "总体资源利用率",
            suffix="%",
        ),
        _fact(
            f"{prefix}-CONSTRAINTS",
            ExplanationFactKind.CONSTRAINT,
            [plan.plan_id],
            "violations.count",
            str(len(plan.violations)),
            f"方案独立硬约束违规数为 {len(plan.violations)}。",
        ),
    ]
    if not include_task_details:
        return facts

    tasks = {item.task_id: item for item in scenario.tasks}
    for assignment in sorted(plan.assignments, key=lambda item: item.task_id)[:20]:
        task = tasks.get(assignment.task_id)
        facts.append(_assignment_fact(plan, assignment, task.flight_id if task else None))
    for item in sorted(plan.unassigned_tasks, key=lambda task: task.task_id)[:20]:
        task = tasks.get(item.task_id)
        entity_ids = [plan.plan_id, item.task_id]
        if task is not None:
            entity_ids.append(task.flight_id)
        facts.append(
            _fact(
                f"{prefix}-UNASSIGNED-{item.task_id}",
                ExplanationFactKind.UNASSIGNED_TASK,
                entity_ids,
                "unassigned_reason",
                item.reason.value,
                f"任务 {item.task_id} 未安排：{item.detail}。",
            )
        )
    return facts


def _metric_fact(
    prefix: str,
    plan: Plan,
    suffix_id: str,
    field: str,
    value: int | str,
    label: str,
    *,
    suffix: str = "",
) -> ExplanationEvidence:
    return _fact(
        f"{prefix}-{suffix_id}",
        ExplanationFactKind.PLAN_METRIC,
        [plan.plan_id],
        f"metrics.{field}",
        str(value),
        f"方案{label}为 {value}{suffix}。",
    )


def _assignment_fact(
    plan: Plan,
    assignment: Assignment,
    flight_id: str | None,
) -> ExplanationEvidence:
    entity_ids = [plan.plan_id, assignment.task_id, assignment.resource_id]
    flight_text = ""
    if flight_id is not None:
        entity_ids.append(flight_id)
        flight_text = f"（航班 {flight_id}）"
    statement = (
        f"任务 {assignment.task_id}{flight_text} 由 {assignment.resource_id} 执行，"
        f"{assignment.origin_zone_id} 至 {assignment.destination_zone_id}，"
        f"服务 {_short_time(assignment.service_started_at)}-"
        f"{_short_time(assignment.service_ended_at)}，等待 {assignment.wait_minutes} 分钟。"
    )
    return _fact(
        f"FACT-PRIMARY-ASSIGNMENT-{assignment.task_id}",
        ExplanationFactKind.ASSIGNMENT,
        entity_ids,
        "assignment",
        assignment.assignment_id,
        statement,
    )


def _plan_change_evidence(primary: Plan, baseline: Plan) -> list[ExplanationEvidence]:
    assigned_delta = primary.metrics.assigned_tasks - baseline.metrics.assigned_tasks
    wait_delta = round(
        primary.metrics.average_wait_minutes - baseline.metrics.average_wait_minutes,
        2,
    )
    facts = [
        _fact(
            "FACT-CHANGE-SUMMARY",
            ExplanationFactKind.PLAN_CHANGE,
            [primary.plan_id, baseline.plan_id],
            "metrics.delta",
            f"assigned={assigned_delta};average_wait={wait_delta}",
            (
                f"相较基线 {baseline.plan_id}，被解释方案已安排任务变化 "
                f"{assigned_delta:+d} 项，平均等待变化 {wait_delta:+.2f} 分钟。"
            ),
        )
    ]
    primary_assignments = {item.task_id: item for item in primary.assignments}
    baseline_assignments = {item.task_id: item for item in baseline.assignments}
    for task_id in sorted(set(primary_assignments) | set(baseline_assignments)):
        current = primary_assignments.get(task_id)
        previous = baseline_assignments.get(task_id)
        if current == previous:
            continue
        if previous is None and current is not None:
            statement = f"任务 {task_id} 在被解释方案中新增由 {current.resource_id} 安排。"
            value = "added"
        elif current is None and previous is not None:
            statement = f"任务 {task_id} 相较基线不再由 {previous.resource_id} 安排。"
            value = "removed"
        else:
            assert current is not None and previous is not None
            changes: list[str] = []
            if current.resource_id != previous.resource_id:
                changes.append(f"资源 {previous.resource_id} -> {current.resource_id}")
            if current.service_started_at != previous.service_started_at:
                changes.append(
                    f"开始时间 {_short_time(previous.service_started_at)} -> "
                    f"{_short_time(current.service_started_at)}"
                )
            if current.origin_zone_id != previous.origin_zone_id or current.destination_zone_id != previous.destination_zone_id:
                changes.append(
                    f"路线 {previous.origin_zone_id}-{previous.destination_zone_id} -> "
                    f"{current.origin_zone_id}-{current.destination_zone_id}"
                )
            if not changes:
                continue
            statement = f"任务 {task_id} 调整：{'；'.join(changes)}。"
            value = "changed"
        facts.append(
            _fact(
                f"FACT-CHANGE-{task_id}",
                ExplanationFactKind.PLAN_CHANGE,
                [primary.plan_id, baseline.plan_id, task_id],
                "assignment.change",
                value,
                statement,
            )
        )
        if len(facts) >= 20:
            break
    return facts


def _scenario_event_evidence(event: FlightEvent) -> ExplanationEvidence:
    if event.event_type is FlightEventType.DELAY:
        detail = f"航班 {event.flight_id} 延误 {event.delay_minutes} 分钟"
    else:
        detail = (
            f"航班 {event.flight_id} 登机口由 {event.previous_gate_id} "
            f"调整至 {event.new_gate_id}"
        )
    return _fact(
        f"FACT-EVENT-{event.event_id}",
        ExplanationFactKind.EVENT,
        [event.event_id, event.flight_id],
        "event",
        event.event_type.value,
        f"{_short_time(event.occurred_at)} 发生{detail}。",
    )


def _runtime_event_evidence(event: EventRuntimeProjection) -> ExplanationEvidence:
    return _fact(
        f"FACT-EVENT-{event.event_id}",
        ExplanationFactKind.EVENT,
        [event.event_id, event.flight_id],
        "runtime_event_status",
        event.status.value,
        f"{_short_time(event.occurred_at)} {event.detail}，当前状态为“{event.status_label}”。",
    )


def render_deterministic_explanation(
    resolved: _ResolvedPlanContext,
    evidence: list[ExplanationEvidence],
    focus: ExplanationFocus,
) -> tuple[ExplanationClaim, list[ExplanationClaim], ExplanationClaim, list[str]]:
    plan = resolved.primary_plan
    metrics = plan.metrics
    ids = {item.evidence_id for item in evidence}
    summary_ids = [
        "FACT-PRIMARY-ASSIGNED",
        "FACT-PRIMARY-TOTAL",
        "FACT-PRIMARY-CRITICAL",
        "FACT-PRIMARY-CONSTRAINTS",
    ]
    if plan.violations:
        summary_text = (
            f"方案存在 {len(plan.violations)} 个硬约束违规，当前不能作为可执行方案。"
        )
    elif metrics.unassigned_tasks:
        summary_text = (
            f"方案已安排 {metrics.assigned_tasks}/{metrics.total_tasks} 项任务，"
            f"P1 关键任务完成率 {_format_number(metrics.critical_task_completion_rate_pct)}%，"
            f"仍有 {metrics.unassigned_tasks} 项需要人工处理。"
        )
    else:
        summary_text = (
            f"方案已安排全部 {metrics.total_tasks} 项任务，P1 关键任务完成率 "
            f"{_format_number(metrics.critical_task_completion_rate_pct)}%，硬约束违规为 0。"
        )
    summary = ExplanationClaim(
        statement=summary_text,
        evidence_ids=[item for item in summary_ids if item in ids],
    )

    tradeoffs: list[ExplanationClaim] = []
    if focus in {ExplanationFocus.SUMMARY, ExplanationFocus.TRADEOFFS}:
        tradeoffs.append(
            ExplanationClaim(
                statement=(
                    f"当前采用“{OBJECTIVE_DISPLAY_NAMES[plan.objective_profile]}”目标，"
                    f"平均等待 {_format_number(metrics.average_wait_minutes)} 分钟，"
                    f"总体资源利用率 {_format_number(metrics.overall_resource_utilization_pct)}%。"
                ),
                evidence_ids=[
                    "FACT-PRIMARY-OBJECTIVE",
                    "FACT-PRIMARY-AVERAGE-WAIT",
                    "FACT-PRIMARY-UTILIZATION",
                ],
            )
        )
    if resolved.baseline_plan is not None:
        change_ids = sorted(item for item in ids if item.startswith("FACT-CHANGE-"))
        tradeoffs.append(
            ExplanationClaim(
                statement=(
                    "与基线相比，量化变化和具体任务调整均来自后端计划差异；"
                    "需要结合当前执行事实人工复核。"
                ),
                evidence_ids=change_ids[:20],
            )
        )
    elif focus is ExplanationFocus.TASK_CHANGES:
        assignment_ids = sorted(
            item for item in ids if item.startswith("FACT-PRIMARY-ASSIGNMENT-")
        )
        tradeoffs.append(
            ExplanationClaim(
                statement=(
                    "请求未提供基线方案，不能判断调整前后变化；"
                    "当前只能列出被解释方案的任务安排。"
                ),
                evidence_ids=["FACT-CONTEXT-BASELINE", *assignment_ids[:5]],
            )
        )
    if focus is ExplanationFocus.MANUAL_HANDLING:
        manual_ids = sorted(
            item for item in ids if item.startswith("FACT-PRIMARY-UNASSIGNED-")
        )
        tradeoffs.append(
            ExplanationClaim(
                statement=(
                    "人工处置应优先核对未安排任务和硬约束结果，"
                    "不得用解释文本替代重新规划。"
                ),
                evidence_ids=["FACT-PRIMARY-CONSTRAINTS", *manual_ids[:19]],
            )
        )

    if plan.violations:
        next_text = "不要采用该方案；请先处理硬约束冲突并重新规划。"
        next_ids = ["FACT-PRIMARY-CONSTRAINTS"]
    elif metrics.unassigned_tasks:
        next_text = "请复核未安排任务原因，决定补充资源、调整时间或保留人工处置。"
        next_ids = ["FACT-PRIMARY-UNASSIGNED"]
        next_ids.extend(
            sorted(item for item in ids if item.startswith("FACT-PRIMARY-UNASSIGNED-"))[:5]
        )
    elif (
        resolved.runtime_snapshot is not None
        and resolved.runtime_snapshot.candidate_plan_id == plan.plan_id
    ):
        next_text = "请核对候选任务书及其与当前方案的差异，再由人员决定采用或保留。"
        next_ids = ["FACT-PRIMARY-CONSTRAINTS", "FACT-PRIMARY-ASSIGNED"]
        if "FACT-CHANGE-SUMMARY" in ids:
            next_ids.append("FACT-CHANGE-SUMMARY")
    else:
        next_text = "请核对具体任务、资源和服务时间后，再由人员确认后续操作。"
        next_ids = ["FACT-PRIMARY-CONSTRAINTS", "FACT-PRIMARY-ASSIGNED"]
    next_step = ExplanationClaim(statement=next_text, evidence_ids=next_ids)
    unresolved = (
        ["未提供基线方案，无法给出调整前后任务差异。"]
        if focus is ExplanationFocus.TASK_CHANGES and resolved.baseline_plan is None
        else []
    )
    return summary, tradeoffs, next_step, unresolved


def _fact(
    evidence_id: str,
    kind: ExplanationFactKind,
    entity_ids: list[str],
    field: str,
    value: str,
    statement: str,
) -> ExplanationEvidence:
    return ExplanationEvidence(
        evidence_id=evidence_id,
        kind=kind,
        entity_ids=entity_ids,
        field=field,
        value=value,
        statement=statement,
    )


def _format_number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _short_time(value: datetime) -> str:
    return value.strftime("%H:%M")
