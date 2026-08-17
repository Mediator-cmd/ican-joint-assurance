"""Fact-bound spatial question grounding, fallback wording and model validation."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable
import unicodedata
from uuid import uuid4

from pydantic import ValidationError

from .ai_models import (
    AssistanceFallbackReason,
    AssistanceSource,
    AssistanceTrace,
    RequestedAssistanceMode,
)
from .ai_provider import (
    AIProviderInvalidOutputError,
    AIProviderRequestError,
    AIProviderTimeoutError,
)
from .ai_services import AssistantContextNotFoundError, AssistantRevisionConflictError
from .ai_spatial_models import (
    SpatialAnswerStatus,
    SpatialMapFocus,
    SpatialQuestionAnswer,
    SpatialQuestionBasis,
    SpatialQuestionRequest,
    SpatialQuestionResponse,
    SpatialQuestionSelection,
)
from .ai_spatial_provider import ModelSpatialAnswer, SpatialQuestionProvider
from .spatial_models import (
    RuntimeSpatialView,
    SpatialFact,
    SpatialFactCategory,
    SpatialTaskRoute,
)
from .spatial_services import RuntimeSpatialService
from .runtime_repository import RuntimeRevisionConflictError, RuntimeSessionNotFoundError


class AssistantSpatialSelectionMismatchError(Exception):
    def __init__(self, entity_id: str) -> None:
        super().__init__(f"spatial selection {entity_id} is not in the current overlay")
        self.entity_id = entity_id


@dataclass(frozen=True, slots=True)
class SpatialQuestionGrounding:
    status: SpatialAnswerStatus
    topics: tuple[str, ...] = ()
    matched_entity_ids: tuple[str, ...] = ()
    relevant_fact_ids: tuple[str, ...] = ()
    reason: str | None = None


class SpatialQuestionService:
    def __init__(
        self,
        spatial_service: RuntimeSpatialService,
        *,
        provider: SpatialQuestionProvider | None = None,
        question_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.spatial_service = spatial_service
        self._provider = provider
        self._question_id_factory = question_id_factory or (
            lambda: f"SPATIAL-Q-{uuid4().hex[:16].upper()}"
        )

    def answer_question(self, request: SpatialQuestionRequest) -> SpatialQuestionResponse:
        try:
            view = self.spatial_service.get_view(
                request.context.session_id,
                request.context.revision,
            )
        except RuntimeSessionNotFoundError as error:
            raise AssistantContextNotFoundError(
                f"assistant runtime session {request.context.session_id} was not found"
            ) from error
        except RuntimeRevisionConflictError as error:
            raise AssistantRevisionConflictError(
                error.expected_revision,
                error.current_revision,
            ) from error
        _validate_selection(request.selection, view)
        grounding = ground_spatial_question(request.question, request.selection, view)
        facts_by_id = {fact.fact_id: fact for fact in view.facts}
        relevant_facts = [
            facts_by_id[fact_id]
            for fact_id in grounding.relevant_fact_ids
            if fact_id in facts_by_id
        ]
        model_facts = _model_safe_facts(relevant_facts)
        model_relevant_fact_ids = [fact.fact_id for fact in model_facts]
        allowed_focus = _focus_for_facts(model_facts, view)

        if request.assistance_mode is RequestedAssistanceMode.DETERMINISTIC_ONLY:
            return self._deterministic_response(
                request,
                view,
                grounding,
                AssistanceTrace(source=AssistanceSource.DETERMINISTIC_RULES),
            )
        if grounding.status is SpatialAnswerStatus.INSUFFICIENT_EVIDENCE:
            return self._deterministic_response(
                request,
                view,
                grounding,
                AssistanceTrace(
                    source=AssistanceSource.DETERMINISTIC_RULES,
                    fallback_reason=AssistanceFallbackReason.QUESTION_NOT_GROUNDED,
                ),
            )
        if self._provider is None:
            return self._deterministic_response(
                request,
                view,
                grounding,
                AssistanceTrace(
                    source=AssistanceSource.DETERMINISTIC_RULES,
                    fallback_reason=AssistanceFallbackReason.MODEL_NOT_CONFIGURED,
                ),
            )
        if not model_facts:
            return self._deterministic_response(
                request,
                view,
                grounding,
                AssistanceTrace(source=AssistanceSource.DETERMINISTIC_RULES),
            )

        try:
            output = self._provider.answer(
                model_facts,
                question=request.question,
                selection=request.selection,
                status=grounding.status,
                relevant_fact_ids=model_relevant_fact_ids,
                matched_entity_ids=list(grounding.matched_entity_ids),
                allowed_focus=allowed_focus,
            )
            _validate_model_answer(
                output,
                grounding.status,
                model_relevant_fact_ids,
                model_facts,
                allowed_focus,
            )
            cited_facts = _bounded_deterministic_facts(
                [fact for fact in model_facts if fact.fact_id in output.fact_ids]
            )
            return self._response(
                request,
                view,
                answer=SpatialQuestionAnswer(
                    status=output.status,
                    statement=_deterministic_statement(cited_facts, grounding, view),
                    fact_ids=[fact.fact_id for fact in cited_facts],
                    matched_entity_ids=list(grounding.matched_entity_ids),
                ),
                focus=output.focus,
                unresolved_questions=_unresolved_for_facts(cited_facts),
                trace=AssistanceTrace(
                    source=AssistanceSource.LANGUAGE_MODEL,
                    provider_attempted=True,
                    model_label=self._provider.model_label,
                ),
            )
        except AIProviderTimeoutError:
            fallback = AssistanceFallbackReason.MODEL_TIMEOUT
        except AIProviderRequestError:
            fallback = AssistanceFallbackReason.PROVIDER_ERROR
        except (AIProviderInvalidOutputError, ValidationError, ValueError):
            fallback = AssistanceFallbackReason.INVALID_MODEL_OUTPUT
        return self._deterministic_response(
            request,
            view,
            grounding,
            AssistanceTrace(
                source=AssistanceSource.DETERMINISTIC_RULES,
                provider_attempted=True,
                fallback_reason=fallback,
            ),
        )

    def _deterministic_response(
        self,
        request: SpatialQuestionRequest,
        view: RuntimeSpatialView,
        grounding: SpatialQuestionGrounding,
        trace: AssistanceTrace,
    ) -> SpatialQuestionResponse:
        facts_by_id = {fact.fact_id: fact for fact in view.facts}
        relevant = [
            facts_by_id[fact_id]
            for fact_id in grounding.relevant_fact_ids
            if fact_id in facts_by_id
        ]
        if grounding.status is SpatialAnswerStatus.INSUFFICIENT_EVIDENCE:
            answer = SpatialQuestionAnswer(
                status=grounding.status,
                statement=(
                    grounding.reason
                    or "当前匿名空间事实不足以回答该问题，系统不会猜测位置或运行状态。"
                ),
                fact_ids=[],
                matched_entity_ids=list(grounding.matched_entity_ids),
            )
            focus = SpatialMapFocus()
            unresolved = ["请改问当前地图中已登记的任务、资源、事件或匿名区域。"]
        else:
            answer_facts = _bounded_deterministic_facts(relevant)
            answer = SpatialQuestionAnswer(
                status=grounding.status,
                statement=_deterministic_statement(answer_facts, grounding, view),
                fact_ids=[fact.fact_id for fact in answer_facts],
                matched_entity_ids=list(grounding.matched_entity_ids),
            )
            focus = _focus_for_facts(answer_facts, view)
            unresolved = _unresolved_for_facts(answer_facts)
        return self._response(
            request,
            view,
            answer=answer,
            focus=focus,
            unresolved_questions=unresolved,
            trace=trace,
        )

    def _response(
        self,
        request: SpatialQuestionRequest,
        view: RuntimeSpatialView,
        *,
        answer: SpatialQuestionAnswer,
        focus: SpatialMapFocus,
        unresolved_questions: list[str],
        trace: AssistanceTrace,
    ) -> SpatialQuestionResponse:
        return SpatialQuestionResponse(
            question_id=self._question_id_factory(),
            basis=SpatialQuestionBasis(
                session_id=view.overlay.session_id,
                revision=view.overlay.revision,
                simulation_time=view.overlay.simulation_time,
                layout_id=view.layout.layout_id,
            ),
            question=request.question,
            selection=request.selection,
            trace=trace,
            answer=answer,
            focus=focus,
            facts=view.facts,
            unresolved_questions=unresolved_questions,
        )


_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "overview": ("总体", "整体", "现在", "当前", "态势", "什么情况", "概况"),
    "location": ("位置", "地图", "哪里", "在哪", "区域", "登机口"),
    "route": ("路线", "路径", "下一段", "怎么走", "途经"),
    "event": ("事件", "突发", "延误", "扰动", "航班", "登机口变更"),
    "resource": ("资源", "车辆", "设备", "谁执行", "哪个资源"),
    "assignment": ("谁执行", "哪个资源", "由谁", "分配给", "负责"),
    "progress": ("进度", "走到", "到哪", "移动", "保障中"),
    "task": ("任务", "作业", "task", "执行路线"),
    "candidate": ("候选", "虚线", "变化", "调整", "采用前", "方案"),
    "impact": ("影响", "关联", "波及"),
    "manual": ("人工", "确认", "协调", "怎么处理", "下一步", "应对"),
    "status": ("状态", "完成", "等待", "处理到", "正在做"),
}

_FORBIDDEN_OPERATION_PHRASES = (
    "直接采用",
    "替我采用",
    "自动采用",
    "直接拒绝",
    "替我拒绝",
    "应用事件",
    "推进时间",
    "修改revision",
    "修改 revision",
    "控制车辆",
    "发出指令",
    "执行控制",
)

_UNKNOWN_GEOMETRY_PHRASES = (
    "经纬度",
    "精确坐标",
    "真实坐标",
    "坐标是多少",
    "真实机场",
    "北京大兴",
    "内部运行图",
    "安防区域",
)


def ground_spatial_question(
    question: str,
    selection: SpatialQuestionSelection,
    view: RuntimeSpatialView,
) -> SpatialQuestionGrounding:
    normalized = _normalize(question)
    topics = tuple(
        topic
        for topic, keywords in _TOPIC_KEYWORDS.items()
        if any(_normalize(keyword) in normalized for keyword in keywords)
    )
    topics = _prioritize_topics(topics)
    question_entities = _match_spatial_entities(normalized, view)
    selected_ids = _selected_ids_for_topics(selection, topics)
    matched_entities = list(dict.fromkeys(question_entities or selected_ids))[:20]

    if any(_normalize(phrase) in normalized for phrase in _FORBIDDEN_OPERATION_PHRASES):
        return SpatialQuestionGrounding(
            status=SpatialAnswerStatus.INSUFFICIENT_EVIDENCE,
            topics=topics,
            matched_entity_ids=tuple(matched_entities),
            reason=(
                "空间 AI 只能解释当前事实，不能应用事件、采用或拒绝候选、"
                "推进时钟或控制车辆。请在既有人工确认流程中操作。"
            ),
        )
    if any(_normalize(phrase) in normalized for phrase in _UNKNOWN_GEOMETRY_PHRASES):
        return SpatialQuestionGrounding(
            status=SpatialAnswerStatus.INSUFFICIENT_EVIDENCE,
            topics=topics,
            matched_entity_ids=tuple(matched_entities),
            reason=(
                "当前只提供原创匿名空间示意与登记区域，不包含真实机场坐标、"
                "内部运行图或未授权位置，系统不会猜测。"
            ),
        )
    if _contains_unmatched_entity_reference(normalized) and not question_entities:
        return SpatialQuestionGrounding(
            status=SpatialAnswerStatus.INSUFFICIENT_EVIDENCE,
            topics=topics,
            matched_entity_ids=tuple(selected_ids),
            reason="问题中的对象不属于当前地图 revision，系统不会用相近编号代替。",
        )
    if topics == ("location",) and not matched_entities:
        return SpatialQuestionGrounding(
            status=SpatialAnswerStatus.INSUFFICIENT_EVIDENCE,
            topics=topics,
            reason="请指出当前地图中的任务、资源、事件或匿名区域；未登记位置不能推断。",
        )

    matched_set = set(matched_entities)
    matched_facts = [
        fact for fact in view.facts if matched_set.intersection(fact.entity_ids)
    ]
    categories = _categories_for_topics(topics)
    direct_facts = _direct_entity_facts(matched_facts, matched_set, topics, view)
    if direct_facts:
        relevant = direct_facts
    elif matched_facts:
        narrowed = [fact for fact in matched_facts if fact.category in categories]
        relevant = narrowed or matched_facts
    elif categories:
        relevant = [fact for fact in view.facts if fact.category in categories]
    else:
        relevant = []

    relevant = _sort_relevant_facts(relevant, topics)[:20]
    if not relevant:
        return SpatialQuestionGrounding(
            status=SpatialAnswerStatus.INSUFFICIENT_EVIDENCE,
            topics=topics,
            matched_entity_ids=tuple(matched_entities),
            reason="当前 revision 的 `SPATIAL-FACT-*` 未提供足以回答该问题的事实，系统不会补造。",
        )
    return SpatialQuestionGrounding(
        status=SpatialAnswerStatus.ANSWERED,
        topics=topics,
        matched_entity_ids=tuple(matched_entities),
        relevant_fact_ids=tuple(fact.fact_id for fact in relevant),
    )


def _direct_entity_facts(
    facts: list[SpatialFact],
    matched_entities: set[str],
    topics: tuple[str, ...],
    view: RuntimeSpatialView,
) -> list[SpatialFact]:
    if set(topics).intersection({"candidate", "impact", "manual"}):
        return []
    entity_categories = (
        (
            {route.task_id for route in view.overlay.task_routes},
            SpatialFactCategory.TASK,
        ),
        (
            {marker.resource_id for marker in view.overlay.resource_markers},
            SpatialFactCategory.RESOURCE,
        ),
        (
            {marker.event_id for marker in view.overlay.event_markers},
            SpatialFactCategory.EVENT,
        ),
    )
    matched_categories = {
        category
        for entity_ids, category in entity_categories
        if matched_entities.intersection(entity_ids)
    }
    if len(matched_categories) != 1:
        return []
    category = next(iter(matched_categories))
    return [fact for fact in facts if fact.category is category]


def _categories_for_topics(topics: tuple[str, ...]) -> set[SpatialFactCategory]:
    categories: set[SpatialFactCategory] = set()
    for topic in topics:
        if topic == "overview":
            categories.update(
                {SpatialFactCategory.CONTEXT, SpatialFactCategory.COVERAGE}
            )
        elif topic == "event":
            categories.update(
                {
                    SpatialFactCategory.EVENT,
                    SpatialFactCategory.TASK,
                    SpatialFactCategory.PLAN,
                }
            )
        elif topic in {"resource", "progress", "assignment"}:
            categories.add(SpatialFactCategory.RESOURCE)
        elif topic in {"task", "location", "route", "status", "impact"}:
            categories.update(
                {
                    SpatialFactCategory.TASK,
                    SpatialFactCategory.RESOURCE,
                    SpatialFactCategory.EVENT,
                    SpatialFactCategory.PLAN,
                }
            )
        elif topic in {"candidate", "manual"}:
            categories.update(
                {
                    SpatialFactCategory.PLAN,
                    SpatialFactCategory.COVERAGE,
                    SpatialFactCategory.EVENT,
                    SpatialFactCategory.TASK,
                }
            )
    return categories


def _sort_relevant_facts(
    facts: list[SpatialFact],
    topics: tuple[str, ...],
) -> list[SpatialFact]:
    topic_set = set(topics)
    if "event" in topic_set:
        order = {
            SpatialFactCategory.EVENT: 0,
            SpatialFactCategory.PLAN: 1,
            SpatialFactCategory.TASK: 2,
        }
    elif "candidate" in topic_set:
        order = {
            SpatialFactCategory.PLAN: 0,
            SpatialFactCategory.COVERAGE: 1,
            SpatialFactCategory.TASK: 2,
            SpatialFactCategory.EVENT: 3,
        }
    elif "task" in topic_set:
        order = {
            SpatialFactCategory.TASK: 0,
            SpatialFactCategory.PLAN: 1,
            SpatialFactCategory.RESOURCE: 2,
            SpatialFactCategory.EVENT: 3,
        }
    else:
        order = {}
    return [
        fact
        for _, fact in sorted(
            enumerate(facts),
            key=lambda item: (order.get(item[1].category, 10), item[0]),
        )
    ]


def _selected_ids_for_topics(
    selection: SpatialQuestionSelection,
    topics: tuple[str, ...],
) -> list[str]:
    topic_set = set(topics)
    if "task" in topic_set:
        return [selection.task_id] if selection.task_id else []
    if topic_set.intersection({"resource", "progress", "assignment"}):
        return [selection.resource_id] if selection.resource_id else []
    if "event" in topic_set:
        return [selection.event_id] if selection.event_id else []
    if topic_set.intersection({"overview", "candidate", "manual"}):
        return []
    return [
        entity_id
        for entity_id in (
            selection.task_id,
            selection.resource_id,
            selection.event_id,
        )
        if entity_id is not None
    ]


def _prioritize_topics(topics: tuple[str, ...]) -> tuple[str, ...]:
    unique = tuple(dict.fromkeys(topics))
    if len(unique) > 1:
        unique = tuple(topic for topic in unique if topic != "overview")
    priority = {
        "event": 0,
        "candidate": 1,
        "task": 2,
        "resource": 3,
        "assignment": 4,
        "location": 5,
        "route": 6,
        "status": 7,
        "progress": 8,
        "impact": 9,
        "manual": 10,
        "overview": 11,
    }
    return tuple(sorted(unique, key=lambda topic: priority.get(topic, 99)))


def _deterministic_statement(
    facts: list[SpatialFact],
    grounding: SpatialQuestionGrounding,
    view: RuntimeSpatialView,
) -> str:
    if not facts:
        return "当前空间事实不足，系统不会猜测。"
    cited_fact_ids = {fact.fact_id for fact in facts}
    topic_set = set(grounding.topics)
    fact_categories = {fact.category for fact in facts}
    task_routes = [
        route
        for route in view.overlay.task_routes
        if f"SPATIAL-FACT-{route.task_id}-{route.route_kind.value.upper()}"
        in cited_fact_ids
    ]
    task_statements = (
        _task_statements(task_routes, topic_set)
        if fact_categories == {SpatialFactCategory.TASK}
        else []
    )
    if task_statements:
        return _join_statements(task_statements)
    resource_statements = (
        _resource_statements(facts, topic_set, view)
        if fact_categories == {SpatialFactCategory.RESOURCE}
        else []
    )
    if resource_statements:
        return _join_statements(resource_statements)
    event_statements = (
        _event_statements(facts, topic_set, view)
        if fact_categories == {SpatialFactCategory.EVENT}
        else []
    )
    if event_statements:
        return _join_statements(event_statements)
    return _join_statements([fact.claim for fact in facts])


def _join_statements(
    statements: list[str],
    max_characters: int = 1450,
) -> str:
    selected: list[str] = []
    length = 0
    for statement in statements:
        separator = 1 if selected else 0
        if selected and length + separator + len(statement) > max_characters:
            break
        selected.append(statement)
        length += separator + len(statement)
    return " ".join(selected or statements[:1])


def _task_statements(
    task_routes: list[SpatialTaskRoute],
    topic_set: set[str],
) -> list[str]:
    statements: list[str] = []
    for route in task_routes:
        if route.route_kind.value == "candidate":
            statements.append(
                f"任务 {route.task_id} 的待确认候选路线从 {route.origin_zone_id} "
                f"到 {route.destination_zone_id}；采用前不替换当前路线。"
            )
            continue
        details: list[str] = []
        if "location" in topic_set:
            details.append(
                f"任务 {route.task_id} 的当前空间范围为 {route.origin_zone_id} "
                f"至 {route.destination_zone_id}。"
            )
        if topic_set.intersection({"resource", "assignment"}):
            details.append(
                f"任务 {route.task_id} 当前由资源 {route.resource_id} 执行。"
                if route.resource_id is not None
                else f"任务 {route.task_id} 当前未分配资源，仍待人工协调。"
            )
        if "status" in topic_set:
            details.append(
                f"任务 {route.task_id} 当前运行状态为 {route.task_status.value}。"
            )
        if "route" in topic_set:
            details.append(
                f"任务 {route.task_id} 当前路线从 {route.origin_zone_id} "
                f"到 {route.destination_zone_id}。"
            )
        if not details:
            assignment = (
                f"由资源 {route.resource_id} 执行"
                if route.resource_id is not None
                else "当前未分配资源，仍待人工协调"
            )
            details.append(
                f"任务 {route.task_id} 当前路线从 {route.origin_zone_id} 到 "
                f"{route.destination_zone_id}，{assignment}，运行状态为 "
                f"{route.task_status.value}。"
            )
        statements.extend(details)
    return statements


def _resource_statements(
    facts: list[SpatialFact],
    topic_set: set[str],
    view: RuntimeSpatialView,
) -> list[str]:
    cited_fact_ids = {fact.fact_id for fact in facts}
    markers = [
        marker
        for marker in view.overlay.resource_markers
        if f"SPATIAL-FACT-{marker.resource_id}" in cited_fact_ids
    ]
    statements: list[str] = []
    for marker in markers:
        details: list[str] = []
        if "location" in topic_set:
            location = marker.from_zone_id
            if marker.to_zone_id is not None:
                location = f"{marker.from_zone_id} 至 {marker.to_zone_id} 的移动路径"
            details.append(f"资源 {marker.resource_id} 当前位于 {location}。")
        if "progress" in topic_set:
            details.append(
                f"资源 {marker.resource_id} 当前移动进度为 {marker.progress_pct:.2f}%。"
                if marker.to_zone_id is not None
                else f"资源 {marker.resource_id} 当前未在路径上移动。"
            )
        if "status" in topic_set:
            details.append(
                f"资源 {marker.resource_id} 当前状态为 {marker.status.value}。"
            )
        if not details:
            details.append(
                next(
                    fact.claim
                    for fact in facts
                    if fact.fact_id == f"SPATIAL-FACT-{marker.resource_id}"
                )
            )
        statements.extend(details)
    return statements


def _event_statements(
    facts: list[SpatialFact],
    topic_set: set[str],
    view: RuntimeSpatialView,
) -> list[str]:
    cited_facts = {fact.fact_id: fact for fact in facts}
    markers = [
        marker
        for marker in view.overlay.event_markers
        if f"SPATIAL-FACT-{marker.event_id}" in cited_facts
    ]
    statements: list[str] = []
    task_ids = {route.task_id for route in view.overlay.task_routes}
    for marker in markers:
        details: list[str] = []
        if "location" in topic_set:
            details.append(
                f"事件 {marker.event_id} 当前定位于 {marker.primary_zone_id}。"
            )
        if "status" in topic_set:
            details.append(
                f"事件 {marker.event_id} 当前事件状态为 {marker.status.value}。"
            )
        if "impact" in topic_set:
            fact = cited_facts[f"SPATIAL-FACT-{marker.event_id}"]
            affected = sorted(set(fact.entity_ids).intersection(task_ids))
            details.append(
                f"事件 {marker.event_id} 当前关联任务为 {', '.join(affected)}。"
                if affected
                else f"事件 {marker.event_id} 当前没有登记关联任务。"
            )
        if not details:
            details.append(cited_facts[f"SPATIAL-FACT-{marker.event_id}"].claim)
        statements.extend(details)
    return statements


def _unresolved_for_facts(facts: list[SpatialFact]) -> list[str]:
    return (
        ["候选路线是否采用仍需人工确认，回答不会替代现行实线方案。"]
        if any(fact.category is SpatialFactCategory.PLAN for fact in facts)
        else []
    )


def _bounded_deterministic_facts(
    facts: list[SpatialFact],
    max_characters: int = 1450,
) -> list[SpatialFact]:
    selected: list[SpatialFact] = []
    length = 0
    for fact in facts:
        separator = 1 if selected else 0
        if selected and length + separator + len(fact.claim) > max_characters:
            break
        selected.append(fact)
        length += separator + len(fact.claim)
    return selected or facts[:1]


def _validate_selection(
    selection: SpatialQuestionSelection,
    view: RuntimeSpatialView,
) -> None:
    available = (
        {route.task_id for route in view.overlay.task_routes},
        {marker.resource_id for marker in view.overlay.resource_markers},
        {marker.event_id for marker in view.overlay.event_markers},
    )
    for entity_id, allowed in zip(
        (selection.task_id, selection.resource_id, selection.event_id),
        available,
        strict=True,
    ):
        if entity_id is not None and entity_id not in allowed:
            raise AssistantSpatialSelectionMismatchError(entity_id)


def _focus_for_facts(
    facts: list[SpatialFact],
    view: RuntimeSpatialView,
) -> SpatialMapFocus:
    entities = {
        entity_id for fact in facts for entity_id in fact.entity_ids
    }
    task_ids = {route.task_id for route in view.overlay.task_routes}
    resource_ids = {marker.resource_id for marker in view.overlay.resource_markers}
    event_ids = {marker.event_id for marker in view.overlay.event_markers}
    zone_ids = {zone.zone_id for zone in view.layout.zones}
    return SpatialMapFocus(
        task_ids=sorted(entities.intersection(task_ids)),
        resource_ids=sorted(entities.intersection(resource_ids)),
        event_ids=sorted(entities.intersection(event_ids)),
        zone_ids=sorted(entities.intersection(zone_ids)),
    )


def _model_safe_facts(facts: list[SpatialFact]) -> list[SpatialFact]:
    return [
        fact.model_copy(
            update={
                "entity_ids": [
                    entity_id
                    for entity_id in fact.entity_ids
                    if not entity_id.startswith("RUN-")
                ]
            }
        )
        for fact in facts
        if fact.category is not SpatialFactCategory.CONTEXT
    ]


def _validate_model_answer(
    output: ModelSpatialAnswer,
    expected_status: SpatialAnswerStatus,
    relevant_fact_ids: list[str],
    facts: list[SpatialFact],
    allowed_focus: SpatialMapFocus,
) -> None:
    if output.status is not expected_status:
        raise ValueError("model spatial status does not match server grounding")
    relevant_ids = set(relevant_fact_ids)
    if output.status is SpatialAnswerStatus.ANSWERED and not output.fact_ids:
        raise ValueError("model spatial answer omitted citations")
    if not set(output.fact_ids) <= relevant_ids:
        raise ValueError("model spatial answer cited a non-relevant fact")
    allowed = {
        "task_ids": set(allowed_focus.task_ids),
        "resource_ids": set(allowed_focus.resource_ids),
        "event_ids": set(allowed_focus.event_ids),
        "zone_ids": set(allowed_focus.zone_ids),
    }
    for field, values in output.focus.model_dump(mode="python").items():
        if not set(values) <= allowed[field]:
            raise ValueError("model spatial focus escaped the current overlay")
    cited_entities = {
        entity_id
        for fact in facts
        if fact.fact_id in output.fact_ids
        for entity_id in fact.entity_ids
    }
    focused_entities = {
        *output.focus.task_ids,
        *output.focus.resource_ids,
        *output.focus.event_ids,
        *output.focus.zone_ids,
    }
    if not focused_entities <= cited_entities:
        raise ValueError("model spatial focus is not supported by cited facts")


_ALIAS_SEPARATOR = r"[-_\s]*"


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _alias_spans(text: str, pattern: str) -> tuple[tuple[int, int], ...]:
    return tuple(
        match.span()
        for match in re.finditer(
            rf"(?<![a-z0-9])(?:{pattern})(?![a-z0-9])",
            text,
        )
    )


def _entity_alias_patterns(entity_id: str) -> tuple[tuple[int, str], ...]:
    parts = tuple(part for part in re.split(r"[-_\s]+", entity_id) if part)
    if len(parts) < 2:
        return ()
    aliases: list[tuple[int, str]] = [
        (1, _ALIAS_SEPARATOR.join(re.escape(part) for part in parts))
    ]
    if parts[-1].isdigit():
        number = str(int(parts[-1]))
        number_pattern = r"0+" if number == "0" else rf"0*{re.escape(number)}"
        prefix = _ALIAS_SEPARATOR.join(re.escape(part) for part in parts[:-1])
        aliases.append((2, f"{prefix}{_ALIAS_SEPARATOR}{number_pattern}"))
        if parts[:-1] == ("task",):
            aliases.append((2, rf"任务{_ALIAS_SEPARATOR}{number_pattern}"))
    return tuple(aliases)


def _match_spatial_entities(
    normalized_question: str,
    view: RuntimeSpatialView,
) -> list[str]:
    available = list(
        dict.fromkeys(
            entity_id for fact in view.facts for entity_id in fact.entity_ids
        )
    )
    exact_matches: set[str] = set()
    exact_spans: set[tuple[int, int]] = set()
    alias_matches: dict[tuple[int, int], dict[int, set[str]]] = {}
    for entity_id in available:
        normalized_id = _normalize(entity_id)
        spans = _alias_spans(normalized_question, re.escape(normalized_id))
        if spans:
            exact_matches.add(entity_id)
            exact_spans.update(spans)
    for entity_id in available:
        for priority, pattern in _entity_alias_patterns(_normalize(entity_id)):
            for span in _alias_spans(normalized_question, pattern):
                if any(start <= span[0] and span[1] <= end for start, end in exact_spans):
                    continue
                alias_matches.setdefault(span, {}).setdefault(priority, set()).add(entity_id)
    alias_results: set[str] = set()
    for priorities in alias_matches.values():
        best = priorities[min(priorities)]
        if len(best) == 1:
            alias_results.add(next(iter(best)))

    for index, marker in enumerate(view.overlay.event_markers, start=1):
        event_pattern = rf"(?:e|事件){_ALIAS_SEPARATOR}0*{index}"
        if _alias_spans(normalized_question, event_pattern):
            alias_results.add(marker.event_id)
    return sorted(exact_matches | alias_results, key=lambda item: (-len(item), item))[:20]


def _contains_unmatched_entity_reference(normalized_question: str) -> bool:
    return bool(
        re.search(
            r"(?<![a-z0-9])(?:task|任务|wc|evt|事件|e)[-_\s]*[a-z0-9]+(?![a-z0-9])",
            normalized_question,
        )
    )
