"""Read-only fact construction and wording for M5 plan explanations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
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
    QuestionAnswer,
    QuestionAnswerStatus,
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


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _ResolvedPlanContext:
    scenario: Scenario
    primary_plan: Plan
    baseline_plan: Plan | None
    scenario_events: tuple[FlightEvent, ...] = ()
    runtime_snapshot: RuntimeSessionSnapshot | None = None


@dataclass(frozen=True, slots=True)
class _QuestionGrounding:
    status: QuestionAnswerStatus
    topics: tuple[str, ...] = ()
    matched_entity_ids: tuple[str, ...] = ()
    relevant_evidence_ids: tuple[str, ...] = ()
    matched_evidence_ids: tuple[str, ...] = ()
    unsupported_spatial: bool = False


@dataclass(frozen=True, slots=True)
class _ExplanationSections:
    question_answer: QuestionAnswer
    summary: ExplanationClaim
    tradeoffs: list[ExplanationClaim]
    task_changes: list[ExplanationClaim]
    manual_handling: list[ExplanationClaim]
    recommended_next_step: ExplanationClaim
    unresolved_questions: list[str]


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
        evidence = build_explanation_evidence(resolved, request.question)
        grounding = ground_explanation_question(request.question, evidence)
        focus_evidence_ids = focus_explanation_evidence_ids(
            request.focus,
            grounding,
            evidence,
        )

        if request.assistance_mode is RequestedAssistanceMode.DETERMINISTIC_ONLY:
            return self._deterministic_response(
                request,
                resolved,
                evidence,
                grounding,
                AssistanceTrace(source=AssistanceSource.DETERMINISTIC_RULES),
            )
        if grounding.status is QuestionAnswerStatus.INSUFFICIENT_EVIDENCE:
            return self._deterministic_response(
                request,
                resolved,
                evidence,
                grounding,
                AssistanceTrace(
                    source=AssistanceSource.DETERMINISTIC_RULES,
                    fallback_reason=AssistanceFallbackReason.QUESTION_NOT_GROUNDED,
                ),
            )
        if self._provider is None:
            return self._deterministic_response(
                request,
                resolved,
                evidence,
                grounding,
                AssistanceTrace(
                    source=AssistanceSource.DETERMINISTIC_RULES,
                    fallback_reason=AssistanceFallbackReason.MODEL_NOT_CONFIGURED,
                ),
            )

        try:
            model_evidence = select_model_explanation_evidence(evidence, grounding)
            output = self._provider.explain(
                model_evidence,
                focus=request.focus,
                question=request.question,
                question_status=grounding.status,
                relevant_evidence_ids=list(grounding.relevant_evidence_ids),
                matched_entity_ids=list(grounding.matched_entity_ids),
                focus_evidence_ids=focus_evidence_ids,
            )
            return self._model_response(
                request,
                evidence,
                grounding,
                focus_evidence_ids,
                output,
            )
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
            grounding,
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
        grounding: _QuestionGrounding,
        trace: AssistanceTrace,
    ) -> PlanExplanationResponse:
        sections = render_deterministic_explanation(
            resolved,
            evidence,
            request.focus,
            request.question,
            grounding,
        )
        return PlanExplanationResponse(
            explanation_id=self._explanation_id_factory(),
            context=request.context,
            trace=trace,
            focus=request.focus,
            question=request.question,
            question_answer=sections.question_answer,
            summary=sections.summary,
            tradeoffs=sections.tradeoffs,
            task_changes=sections.task_changes,
            manual_handling=sections.manual_handling,
            recommended_next_step=sections.recommended_next_step,
            evidence=evidence,
            unresolved_questions=sections.unresolved_questions,
        )

    def _model_response(
        self,
        request: PlanExplanationRequest,
        evidence: list[ExplanationEvidence],
        grounding: _QuestionGrounding,
        focus_evidence_ids: list[str],
        output: ModelPlanExplanation,
    ) -> PlanExplanationResponse:
        assert self._provider is not None
        _validate_model_explanation(
            output,
            request.focus,
            grounding,
            focus_evidence_ids,
            evidence,
        )
        return PlanExplanationResponse(
            explanation_id=self._explanation_id_factory(),
            context=request.context,
            trace=AssistanceTrace(
                source=AssistanceSource.LANGUAGE_MODEL,
                provider_attempted=True,
                model_label=self._provider.model_label,
            ),
            focus=request.focus,
            question=request.question,
            question_answer=output.question_answer.model_dump(mode="python"),
            summary=output.summary.model_dump(mode="python"),
            tradeoffs=[item.model_dump(mode="python") for item in output.tradeoffs],
            task_changes=[item.model_dump(mode="python") for item in output.task_changes],
            manual_handling=[
                item.model_dump(mode="python") for item in output.manual_handling
            ],
            recommended_next_step=output.recommended_next_step.model_dump(mode="python"),
            evidence=evidence,
            unresolved_questions=output.unresolved_questions,
        )


def build_explanation_evidence(
    resolved: _ResolvedPlanContext,
    question: str | None = None,
) -> list[ExplanationEvidence]:
    candidates = _plan_evidence(
        resolved.primary_plan,
        resolved.scenario,
        role="PRIMARY",
        include_task_details=True,
    )
    if resolved.baseline_plan is not None:
        candidates.extend(
            _plan_evidence(
                resolved.baseline_plan,
                resolved.scenario,
                role="BASELINE",
                include_task_details=False,
            )
        )
        candidates.extend(
            _plan_change_evidence(
                resolved.primary_plan,
                resolved.baseline_plan,
            )
        )
    else:
        candidates.append(
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
        for event in sorted(runtime.events, key=lambda item: (item.occurred_at, item.event_id)):
            candidates.append(_runtime_event_evidence(event))
    else:
        for event in sorted(
            resolved.scenario_events,
            key=lambda item: (item.occurred_at, item.event_id),
        ):
            candidates.append(_scenario_event_evidence(event))

    unique_candidates = list({item.evidence_id: item for item in candidates}.values())
    if len(unique_candidates) <= 100:
        return unique_candidates

    preliminary = ground_explanation_question(question, unique_candidates)
    required_ids = {
        item.evidence_id
        for item in unique_candidates
        if (
            item.evidence_id.startswith("FACT-PRIMARY-")
            and item.kind
            in {
                ExplanationFactKind.PLAN_METRIC,
                ExplanationFactKind.CONSTRAINT,
                ExplanationFactKind.OBJECTIVE,
            }
        )
        or item.evidence_id in {"FACT-CONTEXT-BASELINE", "FACT-CHANGE-SUMMARY"}
    }
    priority_ids = [
        *preliminary.relevant_evidence_ids,
        *preliminary.matched_evidence_ids,
    ]
    selected: list[ExplanationEvidence] = []
    selected_ids: set[str] = set()

    def append_by_id(evidence_id: str) -> None:
        if len(selected) >= 100 or evidence_id in selected_ids:
            return
        fact = next(
            (item for item in unique_candidates if item.evidence_id == evidence_id),
            None,
        )
        if fact is not None:
            selected.append(fact)
            selected_ids.add(evidence_id)

    for fact in unique_candidates:
        if fact.evidence_id in required_ids:
            append_by_id(fact.evidence_id)
    for evidence_id in priority_ids:
        append_by_id(evidence_id)
    for fact in unique_candidates:
        append_by_id(fact.evidence_id)
    return selected


_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "utilization": ("利用率", "利用情况", "负载", "忙闲", "占用率"),
    "time": ("等待", "时间", "几点", "何时", "多久", "服务时段", "开始时间", "结束时间"),
    "route": ("路线", "路径", "起点", "终点", "从哪里", "到哪里", "哪个区域"),
    "changes": ("变化", "变更", "调整", "改变", "不同", "对比", "相比", "基线"),
    "manual": ("人工", "协调", "复核", "处理", "决定", "确认", "干预"),
    "resource": ("资源", "车辆", "设备", "谁执行", "哪个资源", "分配给"),
    "task": ("任务", "作业"),
    "event": ("事件", "延误", "登机口", "突发", "扰动", "航班"),
    "constraint": ("约束", "冲突", "违规", "可执行", "风险", "不可执行"),
    "tradeoff": ("取舍", "目标", "优点", "缺点", "代价", "收益", "效率", "为什么选择"),
    "summary": ("摘要", "总体", "整体", "怎么样", "情况", "状态", "完成", "方案"),
}

_UNSUPPORTED_SPATIAL_KEYWORDS = (
    "平面图",
    "地图",
    "坐标",
    "经纬度",
    "实时位置",
    "当前位置",
    "现场位置",
    "定位",
    "哪个机位",
    "哪个跑道",
    "航站楼哪",
    "现场画面",
)


def ground_explanation_question(
    question: str | None,
    evidence: list[ExplanationEvidence],
) -> _QuestionGrounding:
    normalized = (question or "").strip().casefold()
    if not normalized:
        return _QuestionGrounding(status=QuestionAnswerStatus.NOT_ASKED)

    topics = tuple(
        topic
        for topic, keywords in _TOPIC_KEYWORDS.items()
        if any(keyword.casefold() in normalized for keyword in keywords)
    )
    if "utilization" in topics:
        topics = ("utilization",)
    elif "manual" in topics:
        topics = ("manual",)
    elif "changes" in topics:
        topics = tuple(
            topic for topic in topics if topic not in {"summary", "task"}
        )
    elif len(topics) > 1:
        topics = tuple(
            topic for topic in topics if topic not in {"summary", "task"}
        ) or topics
    unsupported_spatial = any(
        keyword.casefold() in normalized for keyword in _UNSUPPORTED_SPATIAL_KEYWORDS
    )
    entity_ids: list[str] = []
    for fact in evidence:
        for entity_id in fact.entity_ids:
            if entity_id.casefold() in normalized and entity_id not in entity_ids:
                entity_ids.append(entity_id)
    entity_ids = sorted(entity_ids, key=lambda item: (-len(item), item))[:20]
    entity_set = set(entity_ids)
    matched_facts = [
        fact
        for fact in evidence
        if entity_set.intersection(fact.entity_ids)
    ]
    if unsupported_spatial:
        return _QuestionGrounding(
            status=QuestionAnswerStatus.INSUFFICIENT_EVIDENCE,
            topics=topics,
            matched_entity_ids=tuple(entity_ids),
            matched_evidence_ids=tuple(
                item.evidence_id for item in matched_facts[:20]
            ),
            unsupported_spatial=True,
        )

    topic_facts = [fact for fact in evidence if _fact_matches_topics(fact, topics)]
    if entity_ids:
        entity_topic_facts = [
            fact for fact in matched_facts if _fact_matches_topics(fact, topics)
        ]
        if entity_topic_facts:
            relevant = entity_topic_facts
        else:
            relevant = list(matched_facts)
            for fact in topic_facts:
                if fact not in relevant:
                    relevant.append(fact)
    else:
        relevant = topic_facts
    relevant = relevant[:20]
    return _QuestionGrounding(
        status=(
            QuestionAnswerStatus.ANSWERED
            if relevant
            else QuestionAnswerStatus.INSUFFICIENT_EVIDENCE
        ),
        topics=topics,
        matched_entity_ids=tuple(entity_ids),
        relevant_evidence_ids=tuple(item.evidence_id for item in relevant),
        matched_evidence_ids=tuple(item.evidence_id for item in matched_facts[:20]),
    )


def _fact_matches_topics(
    fact: ExplanationEvidence,
    topics: tuple[str, ...],
) -> bool:
    if not topics:
        return False
    field = fact.field
    kind = fact.kind
    return any(
        (
            topic == "utilization"
            and field == "metrics.overall_resource_utilization_pct"
        )
        or (
            topic == "time"
            and ("wait" in field or kind in {ExplanationFactKind.ASSIGNMENT, ExplanationFactKind.EVENT})
        )
        or (topic == "route" and kind in {ExplanationFactKind.ASSIGNMENT, ExplanationFactKind.PLAN_CHANGE})
        or (topic == "changes" and kind is ExplanationFactKind.PLAN_CHANGE)
        or (
            topic == "manual"
            and (
                kind in {ExplanationFactKind.UNASSIGNED_TASK, ExplanationFactKind.CONSTRAINT}
                or field == "metrics.unassigned_tasks"
            )
        )
        or (
            topic == "resource"
            and (
                kind is ExplanationFactKind.ASSIGNMENT
                or field == "metrics.overall_resource_utilization_pct"
            )
        )
        or (topic == "task" and kind in {ExplanationFactKind.ASSIGNMENT, ExplanationFactKind.UNASSIGNED_TASK, ExplanationFactKind.PLAN_CHANGE})
        or (topic == "event" and kind is ExplanationFactKind.EVENT)
        or (topic == "constraint" and kind is ExplanationFactKind.CONSTRAINT)
        or (
            topic == "tradeoff"
            and (
                kind is ExplanationFactKind.OBJECTIVE
                or field
                in {
                    "metrics.average_wait_minutes",
                    "metrics.overall_resource_utilization_pct",
                    "metrics.critical_task_completion_rate_pct",
                }
            )
        )
        or (
            topic == "summary"
            and (
                kind in {ExplanationFactKind.PLAN_METRIC, ExplanationFactKind.CONSTRAINT}
                and fact.evidence_id.startswith("FACT-PRIMARY-")
            )
        )
        for topic in topics
    )


def focus_explanation_evidence_ids(
    focus: ExplanationFocus,
    grounding: _QuestionGrounding,
    evidence: list[ExplanationEvidence],
) -> list[str]:
    facts = {item.evidence_id: item for item in evidence}
    relevant = [
        facts[evidence_id]
        for evidence_id in grounding.relevant_evidence_ids
        if evidence_id in facts
    ]
    if focus is ExplanationFocus.SUMMARY:
        preferred = [
            item
            for item in relevant
            if item.evidence_id.startswith("FACT-PRIMARY-")
            and item.kind in {ExplanationFactKind.PLAN_METRIC, ExplanationFactKind.CONSTRAINT}
        ]
    elif focus is ExplanationFocus.TRADEOFFS:
        preferred = [
            item
            for item in relevant
            if item.kind in {ExplanationFactKind.PLAN_METRIC, ExplanationFactKind.OBJECTIVE}
        ]
    elif focus is ExplanationFocus.TASK_CHANGES:
        preferred = [
            item
            for item in relevant
            if item.kind in {
                ExplanationFactKind.ASSIGNMENT,
                ExplanationFactKind.PLAN_CHANGE,
                ExplanationFactKind.UNASSIGNED_TASK,
            }
        ]
    else:
        preferred = [
            item
            for item in relevant
            if item.kind in {
                ExplanationFactKind.UNASSIGNED_TASK,
                ExplanationFactKind.CONSTRAINT,
            }
        ]
    preferred_ids = [item.evidence_id for item in preferred]
    return preferred_ids or [item.evidence_id for item in relevant]


def select_model_explanation_evidence(
    evidence: list[ExplanationEvidence],
    grounding: _QuestionGrounding,
) -> list[ExplanationEvidence]:
    matched = set(grounding.matched_entity_ids)
    if not matched:
        return evidence

    matched_tasks = {item for item in matched if item.startswith("TASK-")}
    matched_flights = {item for item in matched if item.startswith("FL-")}
    matched_events = {item for item in matched if item.startswith("EVT-")}
    matched_plans = {item for item in matched if item.startswith("PLAN-")}
    matched_other = matched - matched_tasks - matched_flights - matched_events - matched_plans
    related_flights = set(matched_flights)
    for fact in evidence:
        entities = set(fact.entity_ids)
        if entities.intersection(matched_tasks | matched_events):
            related_flights.update(
                entity_id
                for entity_id in entities
                if entity_id.startswith("FL-")
            )

    relevant_ids = set(grounding.relevant_evidence_ids)
    selected: list[ExplanationEvidence] = []
    for fact in evidence:
        entities = set(fact.entity_ids)
        is_global = fact.kind in {
            ExplanationFactKind.PLAN_METRIC,
            ExplanationFactKind.CONSTRAINT,
            ExplanationFactKind.OBJECTIVE,
        } or fact.evidence_id in {"FACT-CONTEXT-BASELINE", "FACT-CHANGE-SUMMARY"}
        task_scope = matched_tasks | matched_flights | matched_other
        if matched_events:
            task_scope |= related_flights
        is_task_fact = (
            fact.kind
            in {
                ExplanationFactKind.ASSIGNMENT,
                ExplanationFactKind.UNASSIGNED_TASK,
                ExplanationFactKind.PLAN_CHANGE,
            }
            and bool(entities.intersection(task_scope))
        )
        is_event_fact = (
            fact.kind is ExplanationFactKind.EVENT
            and bool(entities.intersection(matched_events | related_flights))
        )
        if is_global or is_task_fact or is_event_fact or fact.evidence_id in relevant_ids:
            selected.append(fact)
    return selected


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
    for assignment in sorted(plan.assignments, key=lambda item: item.task_id):
        task = tasks.get(assignment.task_id)
        facts.append(_assignment_fact(plan, assignment, task.flight_id if task else None))
    for item in sorted(plan.unassigned_tasks, key=lambda task: task.task_id):
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
    question: str | None,
    grounding: _QuestionGrounding,
) -> _ExplanationSections:
    plan = resolved.primary_plan
    metrics = plan.metrics
    facts = {item.evidence_id: item for item in evidence}
    ids = set(facts)
    question_answer = _render_question_answer(question, grounding, facts)

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
            f"仍有 {metrics.unassigned_tasks} 项未安排。"
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

    tradeoffs = [
        ExplanationClaim(
            statement=(
                f"确定性目标为“{OBJECTIVE_DISPLAY_NAMES[plan.objective_profile]}”；"
                f"当前平均等待 {_format_number(metrics.average_wait_minutes)} 分钟、"
                f"总体资源利用率 {_format_number(metrics.overall_resource_utilization_pct)}%。"
                "这三项用于说明目标与效率取舍，不能单独替代人员对单项任务的判断。"
            ),
            evidence_ids=[
                "FACT-PRIMARY-OBJECTIVE",
                "FACT-PRIMARY-AVERAGE-WAIT",
                "FACT-PRIMARY-UTILIZATION",
            ],
        )
    ]
    if resolved.baseline_plan is not None and "FACT-CHANGE-SUMMARY" in ids:
        tradeoffs.append(
            ExplanationClaim(
                statement=(
                    f"相对基线的覆盖与等待代价为：{facts['FACT-CHANGE-SUMMARY'].statement}"
                    "是否接受该代价仍由人员结合现场教学情境决定。"
                ),
                evidence_ids=["FACT-CHANGE-SUMMARY"],
            )
        )

    task_changes: list[ExplanationClaim] = []
    if resolved.baseline_plan is not None:
        change_facts = [
            item
            for item in evidence
            if item.evidence_id.startswith("FACT-CHANGE-")
        ]
        if change_facts:
            task_changes.append(
                ExplanationClaim(
                    statement=change_facts[0].statement,
                    evidence_ids=[change_facts[0].evidence_id],
                )
            )
            for fact in change_facts[1:4]:
                task_changes.append(
                    ExplanationClaim(
                        statement=fact.statement,
                        evidence_ids=[fact.evidence_id],
                    )
                )
    else:
        related_assignment_ids = set(grounding.relevant_evidence_ids)
        current_assignment = next(
            (
                item
                for item in evidence
                if item.evidence_id.startswith("FACT-PRIMARY-ASSIGNMENT-")
                and item.evidence_id in related_assignment_ids
            ),
            None,
        )
        if current_assignment is None:
            current_assignment = next(
                (
                    item
                    for item in evidence
                    if item.evidence_id.startswith("FACT-PRIMARY-ASSIGNMENT-")
                ),
                None,
            )
        task_change_ids = ["FACT-CONTEXT-BASELINE"]
        task_change_text = "未提供基线方案，不能声称任务已经发生前后变化。"
        if current_assignment is not None:
            task_change_ids.append(current_assignment.evidence_id)
            task_change_text += f" 当前安排示例：{current_assignment.statement}"
        task_changes.append(
            ExplanationClaim(
                statement=task_change_text,
                evidence_ids=task_change_ids,
            )
        )

    manual_facts = [
        item
        for item in evidence
        if item.kind is ExplanationFactKind.UNASSIGNED_TASK
        and item.evidence_id.startswith("FACT-PRIMARY-")
    ]
    if plan.violations:
        manual_handling = [
            ExplanationClaim(
                statement=(
                    f"人员必须先处理 {len(plan.violations)} 个硬约束违规并重新规划；"
                    "在违规清零前不得把解释文字当成可执行安排。"
                ),
                evidence_ids=["FACT-PRIMARY-CONSTRAINTS", "FACT-PRIMARY-STATUS"],
            )
        ]
    elif manual_facts:
        manual_handling = [
            ExplanationClaim(
                statement=(
                    f"需要人员协调 {metrics.unassigned_tasks} 项未安排任务；"
                    "应逐项决定补充资源、调整时间或保留人工处置。"
                ),
                evidence_ids=[
                    "FACT-PRIMARY-UNASSIGNED",
                    *[item.evidence_id for item in manual_facts[:5]],
                ],
            )
        ]
    else:
        manual_handling = [
            ExplanationClaim(
                statement=(
                    "当前权威计划没有未安排任务或硬约束违规；人工职责仍是核对任务书，"
                    "并决定是否采用候选、保留当前方案或触发重新规划。"
                ),
                evidence_ids=["FACT-PRIMARY-UNASSIGNED", "FACT-PRIMARY-CONSTRAINTS"],
            )
        ]

    if plan.violations:
        next_text = "不要采用该方案；请先处理硬约束冲突并重新规划。"
        next_ids = ["FACT-PRIMARY-CONSTRAINTS"]
    elif metrics.unassigned_tasks:
        next_text = "请逐项复核未安排原因，再由人员决定补充资源、调整时间或保留人工处置。"
        next_ids = ["FACT-PRIMARY-UNASSIGNED"]
        next_ids.extend(item.evidence_id for item in manual_facts[:5])
    elif (
        resolved.runtime_snapshot is not None
        and resolved.runtime_snapshot.candidate_plan_id == plan.plan_id
    ):
        next_text = "请核对候选任务书及其与当前方案的差异，再由人员决定采用或保留。"
        next_ids = ["FACT-PRIMARY-CONSTRAINTS", "FACT-PRIMARY-ASSIGNED"]
        if "FACT-CHANGE-SUMMARY" in ids:
            next_ids.append("FACT-CHANGE-SUMMARY")
    else:
        next_text = "请核对具体任务、资源、服务时间和路线后，再由人员确认后续操作。"
        next_ids = ["FACT-PRIMARY-CONSTRAINTS", "FACT-PRIMARY-ASSIGNED"]
    next_step = ExplanationClaim(statement=next_text, evidence_ids=next_ids)

    if grounding.status is QuestionAnswerStatus.ANSWERED:
        related_id = grounding.relevant_evidence_ids[0]
        related = facts[related_id]
        if focus is ExplanationFocus.SUMMARY:
            summary = _append_focus_fact(summary, related, "本次问题关联事实")
        elif focus is ExplanationFocus.TRADEOFFS:
            tradeoffs[0] = _append_focus_fact(
                tradeoffs[0],
                related,
                "本次问题对应的取舍依据",
            )
        elif focus is ExplanationFocus.TASK_CHANGES:
            task_changes[0] = _append_focus_fact(
                task_changes[0],
                related,
                "本次问题对应的任务依据",
            )
        else:
            manual_handling[0] = _append_focus_fact(
                manual_handling[0],
                related,
                "本次问题需要人工核对的依据",
            )

    unresolved: list[str] = []
    if focus is ExplanationFocus.TASK_CHANGES and resolved.baseline_plan is None:
        unresolved.append("未提供基线方案，无法判断调整前后任务差异。")
    if grounding.status is QuestionAnswerStatus.INSUFFICIENT_EVIDENCE:
        unresolved.append(
            "当前 revision 没有足以回答该问题的权威事实；需要先补充经过后端校验的数据。"
        )
    return _ExplanationSections(
        question_answer=question_answer,
        summary=summary,
        tradeoffs=tradeoffs,
        task_changes=task_changes,
        manual_handling=manual_handling,
        recommended_next_step=next_step,
        unresolved_questions=unresolved,
    )


def _render_question_answer(
    question: str | None,
    grounding: _QuestionGrounding,
    facts: dict[str, ExplanationEvidence],
) -> QuestionAnswer:
    if grounding.status is QuestionAnswerStatus.NOT_ASKED:
        return QuestionAnswer(
            status=QuestionAnswerStatus.NOT_ASKED,
            statement="未提出补充问题；以下内容按所选解释重点展开。",
        )

    matched_facts = [
        facts[evidence_id]
        for evidence_id in grounding.matched_evidence_ids
        if evidence_id in facts
    ]
    if grounding.status is QuestionAnswerStatus.INSUFFICIENT_EVIDENCE:
        if grounding.unsupported_spatial:
            known = f" 当前只能确认：{matched_facts[0].statement}" if matched_facts else ""
            statement = (
                "当前 revision 尚未提供平面图坐标、实时位置或空间路径权威事实，"
                f"因此不能准确回答“{(question or '').strip()}”。{known}"
            )
        else:
            statement = (
                f"当前 revision 的权威事实不足以回答“{(question or '').strip()}”；"
                "系统没有让模型猜测不存在的实体或状态。"
            )
        return QuestionAnswer(
            status=QuestionAnswerStatus.INSUFFICIENT_EVIDENCE,
            statement=statement,
            evidence_ids=[item.evidence_id for item in matched_facts[:3]],
            matched_entity_ids=list(grounding.matched_entity_ids),
        )

    relevant = [
        facts[evidence_id]
        for evidence_id in grounding.relevant_evidence_ids
        if evidence_id in facts
    ]
    topics = set(grounding.topics)
    selected: list[ExplanationEvidence]
    if "utilization" in topics:
        selected = [
            item
            for item in relevant
            if item.field == "metrics.overall_resource_utilization_pct"
        ][:1]
        statement = (
            f"{selected[0].statement} 该值表示当前方案的总体资源占用程度；"
            "它不能单独证明某个具体资源过载，还需结合具体任务分配核对。"
        )
    elif "manual" in topics:
        selected = [
            item
            for item in relevant
            if item.kind is ExplanationFactKind.UNASSIGNED_TASK
        ][:5]
        if selected:
            statement = "需要人工协调的未安排任务如下：" + " ".join(
                item.statement for item in selected
            )
        else:
            selected = [
                item
                for item in relevant
                if item.evidence_id
                in {"FACT-PRIMARY-UNASSIGNED", "FACT-PRIMARY-CONSTRAINTS"}
            ]
            if not selected:
                selected = relevant[:2]
            statement = (
                "当前事实没有显示必须人工补位的未安排任务；"
                "候选采用、保留或重新规划仍必须由人员确认。 "
                + " ".join(item.statement for item in selected)
            )
    else:
        selected = [
            item
            for item in relevant
            if item.kind
            in {
                ExplanationFactKind.ASSIGNMENT,
                ExplanationFactKind.UNASSIGNED_TASK,
                ExplanationFactKind.PLAN_CHANGE,
                ExplanationFactKind.EVENT,
            }
        ][:3]
        if not selected:
            selected = relevant[:3]
        statement = "根据当前 revision 的权威事实：" + " ".join(
            item.statement for item in selected
        )
    return QuestionAnswer(
        status=QuestionAnswerStatus.ANSWERED,
        statement=statement,
        evidence_ids=[item.evidence_id for item in selected],
        matched_entity_ids=list(grounding.matched_entity_ids),
    )


def _append_focus_fact(
    claim: ExplanationClaim,
    fact: ExplanationEvidence,
    label: str,
) -> ExplanationClaim:
    evidence_ids = list(dict.fromkeys([*claim.evidence_ids, fact.evidence_id]))[:20]
    return ExplanationClaim(
        statement=f"{claim.statement} {label}：{fact.statement}",
        evidence_ids=evidence_ids,
    )


def _validate_model_explanation(
    output: ModelPlanExplanation,
    focus: ExplanationFocus,
    grounding: _QuestionGrounding,
    focus_evidence_ids: list[str],
    evidence: list[ExplanationEvidence],
) -> None:
    valid_ids = {item.evidence_id for item in evidence}
    answer = output.question_answer
    if answer.status is not grounding.status:
        _reject_model_explanation("question_status_mismatch")
    if answer.matched_entity_ids != list(grounding.matched_entity_ids):
        _reject_model_explanation("matched_entities_mismatch")
    claims = [
        output.summary,
        *output.tradeoffs,
        *output.task_changes,
        *output.manual_handling,
        output.recommended_next_step,
    ]
    referenced = {
        evidence_id
        for claim in claims
        for evidence_id in claim.evidence_ids
    }
    referenced.update(answer.evidence_ids)
    if not referenced <= valid_ids:
        _reject_model_explanation("unknown_evidence_reference")
    if grounding.status is QuestionAnswerStatus.ANSWERED:
        relevant_ids = set(grounding.relevant_evidence_ids)
        focus_ids = set(focus_evidence_ids)
        if not relevant_ids.intersection(answer.evidence_ids):
            _reject_model_explanation("question_answer_not_relevant")
        focus_claims = {
            ExplanationFocus.SUMMARY: [output.summary],
            ExplanationFocus.TRADEOFFS: output.tradeoffs,
            ExplanationFocus.TASK_CHANGES: output.task_changes,
            ExplanationFocus.MANUAL_HANDLING: output.manual_handling,
        }[focus]
        if not any(
            focus_ids.intersection(claim.evidence_ids) for claim in focus_claims
        ):
            logger.warning(
                "Plan explanation model focus refs rejected relevant_ids=%s focus_refs=%s answer_refs=%s",
                sorted(focus_ids),
                [claim.evidence_ids for claim in focus_claims],
                answer.evidence_ids,
            )
            _reject_model_explanation("focus_section_not_relevant")
    section_text = [
        output.summary.statement.strip().casefold(),
        " ".join(item.statement for item in output.tradeoffs).strip().casefold(),
        " ".join(item.statement for item in output.task_changes).strip().casefold(),
        " ".join(item.statement for item in output.manual_handling).strip().casefold(),
    ]
    if len(section_text) != len(set(section_text)):
        _reject_model_explanation("duplicate_section_text")


def _reject_model_explanation(reason: str) -> None:
    logger.warning("Plan explanation model grounding rejected reason=%s", reason)
    raise AIProviderInvalidOutputError()


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
