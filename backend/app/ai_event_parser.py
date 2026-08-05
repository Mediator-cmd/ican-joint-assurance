"""Deterministic Chinese parsing for review-only M5 event drafts."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta

from .ai_models import (
    ClarificationOption,
    ClarificationQuestion,
    EventDraftField,
    EventDraftStatus,
    EvidenceOrigin,
    ExtractedFieldEvidence,
)
from .models import FlightEvent, FlightEventType


@dataclass(frozen=True, slots=True)
class AuthoritativeFlightRef:
    flight_id: str
    display_code: str
    gate_id: str


@dataclass(frozen=True, slots=True)
class AuthoritativeGateRef:
    gate_id: str
    display_name: str


@dataclass(frozen=True, slots=True)
class AuthoritativeEventContext:
    reference_time: datetime
    window_start: datetime
    window_end: datetime
    flights: tuple[AuthoritativeFlightRef, ...]
    gates: tuple[AuthoritativeGateRef, ...]

    def __post_init__(self) -> None:
        timestamps = (self.reference_time, self.window_start, self.window_end)
        if any(value.tzinfo is None or value.utcoffset() is None for value in timestamps):
            raise ValueError("authoritative event times must include timezone offsets")
        if self.window_start >= self.window_end:
            raise ValueError("authoritative event window must have a positive duration")
        flight_ids = [item.flight_id for item in self.flights]
        gate_ids = [item.gate_id for item in self.gates]
        if len(flight_ids) != len(set(flight_ids)):
            raise ValueError("authoritative flight IDs must be unique")
        if len(gate_ids) != len(set(gate_ids)):
            raise ValueError("authoritative gate IDs must be unique")


@dataclass(frozen=True, slots=True)
class ParsedEventDraft:
    status: EventDraftStatus
    event: FlightEvent | None
    evidence: list[ExtractedFieldEvidence]
    missing_fields: list[EventDraftField]
    clarification_questions: list[ClarificationQuestion]
    warnings: list[str]


@dataclass(frozen=True, slots=True)
class _Mention:
    normalized_value: str
    quote: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class _ExtractedTime:
    value: datetime | None
    quote: str | None
    origin: EvidenceOrigin | None
    invalid_reason: str | None = None


_DELAY_PATTERN = re.compile(r"延误|晚点|推迟|顺延", re.IGNORECASE)
_GATE_CHANGE_PATTERN = re.compile(
    r"更换\s*登机口|变更\s*登机口|调整\s*登机口|"
    r"改到|改至|改为|调整到|调整至|变更为|换到|换至|转到|转至",
    re.IGNORECASE,
)
_GATE_TARGET_PATTERN = re.compile(
    r"改到|改至|改为|调整到|调整至|变更为|换到|换至|转到|转至",
    re.IGNORECASE,
)
_DELAY_MINUTES_PATTERNS = (
    re.compile(
        r"(?:延误|晚点|推迟|顺延)(?:了|约|大约)?\s*(\d{1,4})\s*(?:分钟|分)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(\d{1,4})\s*(?:分钟|分)(?:的)?\s*(?:延误|晚点|推迟|顺延)",
        re.IGNORECASE,
    ),
)


def parse_event_text(
    text: str,
    context: AuthoritativeEventContext,
    *,
    event_id: str,
) -> ParsedEventDraft:
    normalized = unicodedata.normalize("NFKC", text).strip()
    delay_match = _DELAY_PATTERN.search(normalized)
    gate_match = _GATE_CHANGE_PATTERN.search(normalized)
    if delay_match and gate_match:
        return _unsupported(
            "一条草稿只能描述一种事件；请将延误与登机口变更分开提交。"
        )
    if delay_match is None and gate_match is None:
        return _unsupported(
            "目前仅支持航班延误和登机口变更事件，请换一种描述。"
        )

    event_type = (
        FlightEventType.DELAY if delay_match is not None else FlightEventType.GATE_CHANGE
    )
    intent_match = delay_match or gate_match
    assert intent_match is not None
    evidence = [
        ExtractedFieldEvidence(
            field=EventDraftField.EVENT_TYPE,
            normalized_value=event_type.value,
            origin=EvidenceOrigin.USER_TEXT,
            source_quote=intent_match.group(0),
        )
    ]

    flight = _extract_flight(normalized, context)
    if flight is not None:
        flight_ref, flight_quote = flight
        evidence.append(
            ExtractedFieldEvidence(
                field=EventDraftField.FLIGHT_ID,
                normalized_value=flight_ref.flight_id,
                origin=EvidenceOrigin.USER_TEXT,
                source_quote=flight_quote,
            )
        )
    else:
        flight_ref = None

    extracted_time = _extract_occurred_at(normalized, context)
    if extracted_time.value is not None:
        assert extracted_time.origin is not None
        evidence.append(
            ExtractedFieldEvidence(
                field=EventDraftField.OCCURRED_AT,
                normalized_value=extracted_time.value.isoformat(),
                origin=extracted_time.origin,
                source_quote=extracted_time.quote,
            )
        )

    delay_minutes: int | None = None
    new_gate: _Mention | None = None
    if event_type is FlightEventType.DELAY:
        delay_minutes, delay_quote = _extract_delay_minutes(normalized)
        if delay_minutes is not None:
            evidence.append(
                ExtractedFieldEvidence(
                    field=EventDraftField.DELAY_MINUTES,
                    normalized_value=str(delay_minutes),
                    origin=EvidenceOrigin.USER_TEXT,
                    source_quote=delay_quote,
                )
            )
    else:
        current_gate_id = flight_ref.gate_id if flight_ref is not None else None
        if current_gate_id is not None:
            evidence.append(
                ExtractedFieldEvidence(
                    field=EventDraftField.PREVIOUS_GATE_ID,
                    normalized_value=current_gate_id,
                    origin=EvidenceOrigin.AUTHORITATIVE_CONTEXT,
                )
            )
        new_gate = _extract_new_gate(normalized, context)
        if new_gate is not None and new_gate.normalized_value != current_gate_id:
            evidence.append(
                ExtractedFieldEvidence(
                    field=EventDraftField.NEW_GATE_ID,
                    normalized_value=new_gate.normalized_value,
                    origin=EvidenceOrigin.USER_TEXT,
                    source_quote=new_gate.quote,
                )
            )
        elif new_gate is not None:
            new_gate = None

    missing_fields: list[EventDraftField] = []
    reasons: dict[EventDraftField, str] = {}
    if flight_ref is None:
        missing_fields.append(EventDraftField.FLIGHT_ID)
    if extracted_time.value is None:
        missing_fields.append(EventDraftField.OCCURRED_AT)
        if extracted_time.invalid_reason is not None:
            reasons[EventDraftField.OCCURRED_AT] = extracted_time.invalid_reason
    if event_type is FlightEventType.DELAY and delay_minutes is None:
        missing_fields.append(EventDraftField.DELAY_MINUTES)
    if event_type is FlightEventType.GATE_CHANGE and new_gate is None:
        missing_fields.append(EventDraftField.NEW_GATE_ID)
        if flight_ref is not None and _mentions_current_gate(normalized, context, flight_ref):
            reasons[EventDraftField.NEW_GATE_ID] = (
                "新登机口与当前权威登机口相同，不能形成变更事件。"
            )

    if missing_fields:
        return ParsedEventDraft(
            status=EventDraftStatus.NEEDS_CLARIFICATION,
            event=None,
            evidence=evidence,
            missing_fields=missing_fields,
            clarification_questions=[
                _clarification_question(
                    field,
                    context,
                    current_gate_id=flight_ref.gate_id if flight_ref is not None else None,
                    reason=reasons.get(field),
                )
                for field in missing_fields
            ],
            warnings=[],
        )

    assert flight_ref is not None
    assert extracted_time.value is not None
    event = FlightEvent(
        event_id=event_id,
        event_type=event_type,
        flight_id=flight_ref.flight_id,
        occurred_at=extracted_time.value,
        delay_minutes=delay_minutes,
        previous_gate_id=(
            flight_ref.gate_id if event_type is FlightEventType.GATE_CHANGE else None
        ),
        new_gate_id=(
            new_gate.normalized_value
            if event_type is FlightEventType.GATE_CHANGE and new_gate is not None
            else None
        ),
    )
    return ParsedEventDraft(
        status=EventDraftStatus.READY_FOR_REVIEW,
        event=event,
        evidence=evidence,
        missing_fields=[],
        clarification_questions=[],
        warnings=[],
    )


def _unsupported(warning: str) -> ParsedEventDraft:
    return ParsedEventDraft(
        status=EventDraftStatus.UNSUPPORTED,
        event=None,
        evidence=[],
        missing_fields=[],
        clarification_questions=[],
        warnings=[warning],
    )


def _extract_flight(
    text: str,
    context: AuthoritativeEventContext,
) -> tuple[AuthoritativeFlightRef, str] | None:
    matches: dict[str, tuple[AuthoritativeFlightRef, str]] = {}
    for flight in context.flights:
        aliases = sorted({flight.flight_id, flight.display_code}, key=len, reverse=True)
        for alias in aliases:
            match = re.search(re.escape(alias), text, re.IGNORECASE)
            if match is not None:
                current = matches.get(flight.flight_id)
                if current is None or len(match.group(0)) > len(current[1]):
                    matches[flight.flight_id] = (flight, match.group(0))
    if len(matches) != 1:
        return None
    return next(iter(matches.values()))


def _extract_delay_minutes(text: str) -> tuple[int | None, str | None]:
    for pattern in _DELAY_MINUTES_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            return int(match.group(1)), match.group(0)
    return None, None


def _extract_occurred_at(
    text: str,
    context: AuthoritativeEventContext,
) -> _ExtractedTime:
    relative = re.search(r"(\d{1,4})\s*分钟\s*(前|之前|后|以后)", text)
    if relative is not None:
        minutes = int(relative.group(1))
        direction = -1 if relative.group(2) in {"前", "之前"} else 1
        value = context.reference_time + timedelta(minutes=direction * minutes)
        return _validate_time(value, relative.group(0), EvidenceOrigin.DETERMINISTIC_DERIVATION, context)

    current = re.search(r"刚刚|刚才|刚确认|现在|此刻", text)
    if current is not None:
        return _validate_time(
            context.reference_time,
            current.group(0),
            EvidenceOrigin.DETERMINISTIC_DERIVATION,
            context,
        )

    explicit_date = re.search(
        r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})[ T](\d{1,2}):(\d{2})",
        text,
    )
    if explicit_date is not None:
        try:
            value = datetime(
                int(explicit_date.group(1)),
                int(explicit_date.group(2)),
                int(explicit_date.group(3)),
                int(explicit_date.group(4)),
                int(explicit_date.group(5)),
                tzinfo=context.reference_time.tzinfo,
            )
        except ValueError:
            return _invalid_time(explicit_date.group(0))
        return _validate_time(value, explicit_date.group(0), EvidenceOrigin.USER_TEXT, context)

    clock = re.search(r"(?<![\d:-])(\d{1,2}):(\d{2})(?!\d)", text)
    if clock is not None:
        try:
            value = context.reference_time.replace(
                hour=int(clock.group(1)),
                minute=int(clock.group(2)),
                second=0,
                microsecond=0,
            )
        except ValueError:
            return _invalid_time(clock.group(0))
        return _validate_time(value, clock.group(0), EvidenceOrigin.USER_TEXT, context)

    chinese_clock = re.search(r"(?<!\d)(\d{1,2})\s*点(?:\s*(\d{1,2})\s*分?)?", text)
    if chinese_clock is not None:
        try:
            value = context.reference_time.replace(
                hour=int(chinese_clock.group(1)),
                minute=int(chinese_clock.group(2) or 0),
                second=0,
                microsecond=0,
            )
        except ValueError:
            return _invalid_time(chinese_clock.group(0))
        return _validate_time(
            value,
            chinese_clock.group(0),
            EvidenceOrigin.USER_TEXT,
            context,
        )
    return _ExtractedTime(None, None, None)


def _invalid_time(quote: str) -> _ExtractedTime:
    return _ExtractedTime(
        None,
        quote,
        None,
        "给出的时间格式无效，请使用当前教学场景内的明确时间。",
    )


def _validate_time(
    value: datetime,
    quote: str,
    origin: EvidenceOrigin,
    context: AuthoritativeEventContext,
) -> _ExtractedTime:
    if value < context.window_start or value > context.window_end:
        return _ExtractedTime(
            None,
            quote,
            None,
            "给出的时间不在当前教学场景时间窗内。",
        )
    return _ExtractedTime(value, quote, origin)


def _extract_new_gate(
    text: str,
    context: AuthoritativeEventContext,
) -> _Mention | None:
    mentions = _find_gate_mentions(text, context)
    marker = _GATE_TARGET_PATTERN.search(text)
    if marker is not None:
        mentions = [item for item in mentions if item.start >= marker.end()]
    gate_ids = {item.normalized_value for item in mentions}
    if len(gate_ids) != 1:
        return None
    gate_id = next(iter(gate_ids))
    candidates = [item for item in mentions if item.normalized_value == gate_id]
    return max(candidates, key=lambda item: (item.end - item.start, -item.start))


def _find_gate_mentions(
    text: str,
    context: AuthoritativeEventContext,
) -> list[_Mention]:
    mentions: list[_Mention] = []
    for gate in context.gates:
        suffix = gate.gate_id.removeprefix("GATE-")
        aliases = {gate.gate_id, suffix, gate.display_name}
        for alias in sorted(aliases, key=len, reverse=True):
            flexible = r"\s*".join(re.escape(char) for char in alias if not char.isspace())
            for match in re.finditer(flexible, text, re.IGNORECASE):
                mentions.append(
                    _Mention(gate.gate_id, match.group(0), match.start(), match.end())
                )
    return mentions


def _mentions_current_gate(
    text: str,
    context: AuthoritativeEventContext,
    flight: AuthoritativeFlightRef,
) -> bool:
    target = _extract_new_gate(text, context)
    return target is not None and target.normalized_value == flight.gate_id


def _clarification_question(
    field: EventDraftField,
    context: AuthoritativeEventContext,
    *,
    current_gate_id: str | None,
    reason: str | None,
) -> ClarificationQuestion:
    if field is EventDraftField.FLIGHT_ID:
        return ClarificationQuestion(
            field=field,
            question="请确认这条事件对应哪个航班。",
            reason=reason or "未找到唯一可核对的航班编号。",
            options=[
                ClarificationOption(
                    value=item.flight_id,
                    label=f"{item.display_code}（{item.flight_id}）",
                )
                for item in context.flights
            ],
        )
    if field is EventDraftField.OCCURRED_AT:
        return ClarificationQuestion(
            field=field,
            question="请补充事件实际发生时间，例如“08:12”或“5 分钟前”。",
            reason=reason or "事件发生时间不能由航班计划时间代替。",
        )
    if field is EventDraftField.DELAY_MINUTES:
        return ClarificationQuestion(
            field=field,
            question="请补充该航班延误的分钟数。",
            reason=reason or "延误事件必须包含明确的延误分钟数。",
        )
    if field is EventDraftField.NEW_GATE_ID:
        return ClarificationQuestion(
            field=field,
            question="请确认航班要调整到哪个新登机口。",
            reason=reason or "登机口变更必须包含与当前登机口不同的新登机口。",
            options=[
                ClarificationOption(value=item.gate_id, label=item.display_name)
                for item in context.gates
                if item.gate_id != current_gate_id
            ],
        )
    raise ValueError(f"unsupported clarification field: {field.value}")

