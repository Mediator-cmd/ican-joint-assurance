"""Validate model extraction against authoritative M5 event context."""

from __future__ import annotations

import re
import unicodedata

from .ai_event_parser import (
    AuthoritativeEventContext,
    AuthoritativeFlightRef,
    ParsedEventDraft,
    build_clarification_question,
    detect_deterministic_event_types,
    extract_event_time,
    find_referenced_flight_ids,
    find_referenced_gate_ids,
)
from .ai_models import (
    EventDraftField,
    EventDraftStatus,
    EvidenceOrigin,
    ExtractedFieldEvidence,
)
from .ai_provider import AIProviderInvalidOutputError, ModelEventExtraction
from .models import FlightEvent, FlightEventType


def parse_model_extraction(
    extraction: ModelEventExtraction,
    text: str,
    context: AuthoritativeEventContext,
    *,
    event_id: str,
) -> ParsedEventDraft:
    _validate_quotes(extraction, text)
    detected_event_types = detect_deterministic_event_types(text)
    if len(detected_event_types) > 1:
        raise AIProviderInvalidOutputError()
    if extraction.event_type is None:
        if detected_event_types:
            raise AIProviderInvalidOutputError()
        return ParsedEventDraft(
            status=EventDraftStatus.UNSUPPORTED,
            event=None,
            evidence=[],
            missing_fields=[],
            clarification_questions=[],
            warnings=["目前仅支持航班延误和登机口变更事件，请换一种描述。"],
        )
    if detected_event_types and extraction.event_type not in detected_event_types:
        raise AIProviderInvalidOutputError()

    assert extraction.event_type_quote is not None
    evidence = [
        ExtractedFieldEvidence(
            field=EventDraftField.EVENT_TYPE,
            normalized_value=extraction.event_type.value,
            origin=EvidenceOrigin.USER_TEXT,
            source_quote=extraction.event_type_quote,
        )
    ]
    known_flights = {item.flight_id: item for item in context.flights}
    if (
        len(find_referenced_flight_ids(text, context)) > 1
        and extraction.flight_id is not None
    ):
        raise AIProviderInvalidOutputError()
    if extraction.flight_id is not None:
        flight = known_flights.get(extraction.flight_id)
        if flight is None or not _quote_matches_flight(extraction.flight_quote, flight):
            raise AIProviderInvalidOutputError()
        evidence.append(
            ExtractedFieldEvidence(
                field=EventDraftField.FLIGHT_ID,
                normalized_value=flight.flight_id,
                origin=EvidenceOrigin.USER_TEXT,
                source_quote=extraction.flight_quote,
            )
        )
    else:
        flight = None

    extracted_time = None
    time_reason = None
    if extraction.occurred_at_quote is not None:
        extracted_time = extract_event_time(extraction.occurred_at_quote, context)
        if extracted_time.value is None and extracted_time.invalid_reason is None:
            raise AIProviderInvalidOutputError()
        if extracted_time.value is not None:
            assert extracted_time.origin is not None
            evidence.append(
                ExtractedFieldEvidence(
                    field=EventDraftField.OCCURRED_AT,
                    normalized_value=extracted_time.value.isoformat(),
                    origin=extracted_time.origin,
                    source_quote=extraction.occurred_at_quote,
                )
            )
        else:
            time_reason = extracted_time.invalid_reason

    known_gates = {item.gate_id for item in context.gates}
    new_gate_id = extraction.new_gate_id
    if new_gate_id is not None and new_gate_id not in known_gates:
        raise AIProviderInvalidOutputError()
    if new_gate_id is not None:
        quoted_gates = find_referenced_gate_ids(
            extraction.new_gate_quote or "",
            context,
        )
        if quoted_gates and quoted_gates != {new_gate_id}:
            raise AIProviderInvalidOutputError()
    if (
        extraction.event_type is FlightEventType.GATE_CHANGE
        and flight is not None
    ):
        evidence.append(
            ExtractedFieldEvidence(
                field=EventDraftField.PREVIOUS_GATE_ID,
                normalized_value=flight.gate_id,
                origin=EvidenceOrigin.AUTHORITATIVE_CONTEXT,
            )
        )
    if new_gate_id is not None and (flight is None or new_gate_id != flight.gate_id):
        evidence.append(
            ExtractedFieldEvidence(
                field=EventDraftField.NEW_GATE_ID,
                normalized_value=new_gate_id,
                origin=EvidenceOrigin.USER_TEXT,
                source_quote=extraction.new_gate_quote,
            )
        )

    if extraction.delay_minutes is not None:
        quoted_numbers = {
            int(value)
            for value in re.findall(r"\d{1,4}", extraction.delay_minutes_quote or "")
        }
        if quoted_numbers and extraction.delay_minutes not in quoted_numbers:
            raise AIProviderInvalidOutputError()
        evidence.append(
            ExtractedFieldEvidence(
                field=EventDraftField.DELAY_MINUTES,
                normalized_value=str(extraction.delay_minutes),
                origin=EvidenceOrigin.USER_TEXT,
                source_quote=extraction.delay_minutes_quote,
            )
        )

    missing_fields: list[EventDraftField] = []
    reasons: dict[EventDraftField, str] = {}
    if flight is None:
        missing_fields.append(EventDraftField.FLIGHT_ID)
    if extracted_time is None or extracted_time.value is None:
        missing_fields.append(EventDraftField.OCCURRED_AT)
        if time_reason is not None:
            reasons[EventDraftField.OCCURRED_AT] = time_reason
    if (
        extraction.event_type is FlightEventType.DELAY
        and extraction.delay_minutes is None
    ):
        missing_fields.append(EventDraftField.DELAY_MINUTES)
    if extraction.event_type is FlightEventType.GATE_CHANGE and (
        new_gate_id is None or (flight is not None and new_gate_id == flight.gate_id)
    ):
        missing_fields.append(EventDraftField.NEW_GATE_ID)
        if flight is not None and new_gate_id == flight.gate_id:
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
                build_clarification_question(
                    field,
                    context,
                    current_gate_id=flight.gate_id if flight is not None else None,
                    reason=reasons.get(field),
                )
                for field in missing_fields
            ],
            warnings=[],
        )

    assert flight is not None
    assert extracted_time is not None and extracted_time.value is not None
    event = FlightEvent(
        event_id=event_id,
        event_type=extraction.event_type,
        flight_id=flight.flight_id,
        occurred_at=extracted_time.value,
        delay_minutes=extraction.delay_minutes,
        previous_gate_id=(
            flight.gate_id
            if extraction.event_type is FlightEventType.GATE_CHANGE
            else None
        ),
        new_gate_id=(
            new_gate_id
            if extraction.event_type is FlightEventType.GATE_CHANGE
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


def _validate_quotes(extraction: ModelEventExtraction, text: str) -> None:
    normalized_text = unicodedata.normalize("NFKC", text)
    quotes = (
        extraction.event_type_quote,
        extraction.flight_quote,
        extraction.occurred_at_quote,
        extraction.delay_minutes_quote,
        extraction.new_gate_quote,
    )
    if any(
        quote is not None
        and unicodedata.normalize("NFKC", quote) not in normalized_text
        for quote in quotes
    ):
        raise AIProviderInvalidOutputError()


def _quote_matches_flight(
    quote: str | None,
    flight: AuthoritativeFlightRef,
) -> bool:
    if quote is None:
        return False
    normalized = unicodedata.normalize("NFKC", quote).upper()
    return flight.flight_id.upper() in normalized or flight.display_code.upper() in normalized
