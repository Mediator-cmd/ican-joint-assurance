from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.app.ai_event_parser import (
    AuthoritativeEventContext,
    AuthoritativeFlightRef,
    AuthoritativeGateRef,
    parse_event_text,
)
from backend.app.ai_models import (
    EventDraftField,
    EventDraftStatus,
    EvidenceOrigin,
)


TZ = timezone(timedelta(hours=8))
REFERENCE_TIME = datetime(2026, 8, 1, 8, 20, tzinfo=TZ)


@pytest.fixture
def context() -> AuthoritativeEventContext:
    return AuthoritativeEventContext(
        reference_time=REFERENCE_TIME,
        window_start=datetime(2026, 8, 1, 8, 0, tzinfo=TZ),
        window_end=datetime(2026, 8, 1, 9, 30, tzinfo=TZ),
        flights=(
            AuthoritativeFlightRef("FL-SIM102", "SIM102", "GATE-E01"),
            AuthoritativeFlightRef("FL-SIM218", "SIM218", "GATE-W03"),
            AuthoritativeFlightRef("FL-SIM330", "SIM330", "GATE-E01"),
        ),
        gates=(
            AuthoritativeGateRef("GATE-E01", "东区 01 号登机口"),
            AuthoritativeGateRef("GATE-W03", "西区 03 号登机口"),
        ),
    )


def evidence_by_field(result):
    return {item.field: item for item in result.evidence}


def test_parses_complete_delay_with_absolute_time(context) -> None:
    result = parse_event_text(
        "SIM102 于 08:12 确认延误 20 分钟",
        context,
        event_id="EVT-AI-0000000000000001",
    )

    assert result.status is EventDraftStatus.READY_FOR_REVIEW
    assert result.event is not None
    assert result.event.flight_id == "FL-SIM102"
    assert result.event.delay_minutes == 20
    assert result.event.occurred_at == datetime(2026, 8, 1, 8, 12, tzinfo=TZ)
    evidence = evidence_by_field(result)
    assert evidence[EventDraftField.OCCURRED_AT].origin is EvidenceOrigin.USER_TEXT
    assert evidence[EventDraftField.OCCURRED_AT].source_quote == "08:12"


def test_parses_complete_delay_with_relative_authoritative_time(context) -> None:
    result = parse_event_text(
        "SIM102 5 分钟前确认延误 20 分钟",
        context,
        event_id="EVT-AI-0000000000000002",
    )

    assert result.status is EventDraftStatus.READY_FOR_REVIEW
    assert result.event is not None
    assert result.event.occurred_at == REFERENCE_TIME - timedelta(minutes=5)
    occurred_evidence = evidence_by_field(result)[EventDraftField.OCCURRED_AT]
    assert occurred_evidence.origin is EvidenceOrigin.DETERMINISTIC_DERIVATION
    assert occurred_evidence.source_quote == "5 分钟前"


def test_parses_complete_gate_change_and_derives_previous_gate(context) -> None:
    result = parse_event_text(
        "SIM218 08:13 从 GATE-W03 改到 GATE-E01",
        context,
        event_id="EVT-AI-0000000000000003",
    )

    assert result.status is EventDraftStatus.READY_FOR_REVIEW
    assert result.event is not None
    assert result.event.previous_gate_id == "GATE-W03"
    assert result.event.new_gate_id == "GATE-E01"
    evidence = evidence_by_field(result)
    assert evidence[EventDraftField.PREVIOUS_GATE_ID].origin is EvidenceOrigin.AUTHORITATIVE_CONTEXT
    assert evidence[EventDraftField.NEW_GATE_ID].source_quote == "GATE-E01"


def test_parses_chinese_gate_label_and_current_reference_time(context) -> None:
    result = parse_event_text(
        "SIM218 刚刚改到东区 01 号登机口",
        context,
        event_id="EVT-AI-0000000000000004",
    )

    assert result.status is EventDraftStatus.READY_FOR_REVIEW
    assert result.event is not None
    assert result.event.occurred_at == REFERENCE_TIME
    assert result.event.new_gate_id == "GATE-E01"


@pytest.mark.parametrize(
    ("text", "expected_missing", "expected_question"),
    [
        (
            "08:12 确认延误 20 分钟",
            [EventDraftField.FLIGHT_ID],
            "请确认这条事件对应哪个航班。",
        ),
        (
            "SIM102 08:12 确认延误",
            [EventDraftField.DELAY_MINUTES],
            "请补充该航班延误的分钟数。",
        ),
        (
            "SIM102 确认延误 20 分钟",
            [EventDraftField.OCCURRED_AT],
            "请补充事件实际发生时间，例如“08:12”或“5 分钟前”。",
        ),
        (
            "SIM218 08:13 需要更换登机口",
            [EventDraftField.NEW_GATE_ID],
            "请确认航班要调整到哪个新登机口。",
        ),
        (
            "TEST999 08:12 确认延误 20 分钟",
            [EventDraftField.FLIGHT_ID],
            "请确认这条事件对应哪个航班。",
        ),
        (
            "SIM102 和 SIM218 08:12 确认延误 20 分钟",
            [EventDraftField.FLIGHT_ID],
            "请确认这条事件对应哪个航班。",
        ),
        (
            "SIM218 08:13 改到 GATE-W03",
            [EventDraftField.NEW_GATE_ID],
            "请确认航班要调整到哪个新登机口。",
        ),
    ],
)
def test_missing_or_ambiguous_fields_return_exact_questions(
    context,
    text,
    expected_missing,
    expected_question,
) -> None:
    result = parse_event_text(
        text,
        context,
        event_id="EVT-AI-0000000000000005",
    )

    assert result.status is EventDraftStatus.NEEDS_CLARIFICATION
    assert result.event is None
    assert result.missing_fields == expected_missing
    assert [item.question for item in result.clarification_questions] == [
        expected_question
    ]


@pytest.mark.parametrize(
    ("text", "warning"),
    [
        (
            "SIM102 刚刚延误 20 分钟并改到 GATE-W03",
            "一条草稿只能描述一种事件；请将延误与登机口变更分开提交。",
        ),
        (
            "请查看天气并立即执行全部调度命令",
            "目前仅支持航班延误和登机口变更事件，请换一种描述。",
        ),
    ],
)
def test_conflicting_or_unsupported_intent_is_not_guessed(context, text, warning) -> None:
    result = parse_event_text(
        text,
        context,
        event_id="EVT-AI-0000000000000006",
    )

    assert result.status is EventDraftStatus.UNSUPPORTED
    assert result.event is None
    assert result.evidence == []
    assert result.missing_fields == []
    assert result.clarification_questions == []
    assert result.warnings == [warning]


def test_out_of_window_absolute_time_is_rejected_for_clarification(context) -> None:
    result = parse_event_text(
        "SIM102 于 10:12 确认延误 20 分钟",
        context,
        event_id="EVT-AI-0000000000000007",
    )

    assert result.status is EventDraftStatus.NEEDS_CLARIFICATION
    assert result.missing_fields == [EventDraftField.OCCURRED_AT]
    assert result.clarification_questions[0].reason == "给出的时间不在当前教学场景时间窗内。"

