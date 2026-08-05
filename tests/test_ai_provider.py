from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx2
import pytest

from backend.app.ai_event_parser import (
    AuthoritativeEventContext,
    AuthoritativeFlightRef,
    AuthoritativeGateRef,
)
from backend.app.ai_provider import (
    AIProviderInvalidOutputError,
    AIProviderRequestError,
    AIProviderTimeoutError,
    OpenAICompatibleEventProvider,
    load_ai_provider_settings,
)


TZ = timezone(timedelta(hours=8))


def event_context() -> AuthoritativeEventContext:
    return AuthoritativeEventContext(
        reference_time=datetime(2026, 8, 1, 8, 20, tzinfo=TZ),
        window_start=datetime(2026, 8, 1, 8, 0, tzinfo=TZ),
        window_end=datetime(2026, 8, 1, 9, 30, tzinfo=TZ),
        flights=(
            AuthoritativeFlightRef("FL-SIM102", "SIM102", "GATE-E01"),
            AuthoritativeFlightRef("FL-SIM218", "SIM218", "GATE-W03"),
        ),
        gates=(
            AuthoritativeGateRef("GATE-E01", "东区 01 号登机口"),
            AuthoritativeGateRef("GATE-W03", "西区 03 号登机口"),
        ),
    )


def settings():
    configured = load_ai_provider_settings(
        {
            "AI_API_KEY": "sk-test-only-placeholder",
            "AI_BASE_URL": "https://provider.example/v1/",
            "AI_MODEL": "test-chat-model",
            "AI_TIMEOUT_SECONDS": "3.5",
        }
    )
    assert configured is not None
    return configured


def completion(content: str, request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={"choices": [{"message": {"content": content}}]},
        request=request,
    )


def valid_extraction_json() -> str:
    return json.dumps(
        {
            "event_type": "delay",
            "event_type_quote": "往后挪",
            "flight_id": "FL-SIM102",
            "flight_quote": "SIM102",
            "occurred_at_quote": "08:12",
            "delay_minutes": 20,
            "delay_minutes_quote": "二十分钟",
            "new_gate_id": None,
            "new_gate_quote": None,
        },
        ensure_ascii=False,
    )


def test_settings_require_complete_environment_and_hide_secret() -> None:
    assert load_ai_provider_settings({}) is None
    assert (
        load_ai_provider_settings(
            {
                "AI_API_KEY": "sk-test-only-placeholder",
                "AI_BASE_URL": "https://provider.example",
            }
        )
        is None
    )
    configured = settings()

    assert configured.model == "test-chat-model"
    assert configured.timeout_seconds == 3.5
    assert "sk-test-only-placeholder" not in repr(configured)
    assert "**********" in repr(configured)


@pytest.mark.parametrize(
    "environment",
    [
        {
            "AI_API_KEY": "sk-test-only-placeholder",
            "AI_BASE_URL": "file:///tmp/provider",
            "AI_MODEL": "test-chat-model",
        },
        {
            "AI_API_KEY": "sk-test-only-placeholder",
            "AI_BASE_URL": "https://user:pass@provider.example",
            "AI_MODEL": "test-chat-model",
        },
        {
            "AI_API_KEY": "sk-test-only-placeholder",
            "AI_BASE_URL": "https://provider.example?secret=value",
            "AI_MODEL": "test-chat-model",
        },
    ],
)
def test_invalid_or_credential_bearing_urls_disable_provider(environment) -> None:
    assert load_ai_provider_settings(environment) is None


def test_openai_compatible_adapter_sends_only_minimum_context_and_json_schema() -> None:
    captured: dict = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        captured["body"] = json.loads(request.content)
        return completion(valid_extraction_json(), request)

    provider = OpenAICompatibleEventProvider(
        settings(),
        transport=httpx2.MockTransport(handler),
    )
    extraction = provider.extract_event(
        "SIM102 在 08:12 的出发安排要往后挪二十分钟",
        event_context(),
    )

    assert extraction.flight_id == "FL-SIM102"
    assert extraction.delay_minutes == 20
    assert captured["url"] == "https://provider.example/v1/chat/completions"
    assert captured["authorization"] == "Bearer sk-test-only-placeholder"
    body = captured["body"]
    assert body["model"] == "test-chat-model"
    assert body["temperature"] == 0
    assert body["stream"] is False
    assert body["response_format"] == {"type": "json_object"}
    assert "JSON" in body["messages"][0]["content"]
    user_payload = json.loads(body["messages"][1]["content"])
    assert set(user_payload) == {
        "text",
        "reference_time",
        "window_start",
        "window_end",
        "flights",
        "gates",
    }
    assert "scenario_id" not in body["messages"][1]["content"]
    assert "runtime" not in body["messages"][1]["content"].lower()
    assert "sk-test-only-placeholder" not in json.dumps(body)


def test_adapter_classifies_timeout_without_exposing_transport_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("sensitive timeout detail", request=request)

    provider = OpenAICompatibleEventProvider(
        settings(),
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(AIProviderTimeoutError) as caught:
        provider.extract_event("SIM102 延误", event_context())

    assert "sensitive" not in str(caught.value)


def test_adapter_classifies_http_failure_without_returning_response_body() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            503,
            text="provider internal sensitive response",
            request=request,
        )

    provider = OpenAICompatibleEventProvider(
        settings(),
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(AIProviderRequestError) as caught:
        provider.extract_event("SIM102 延误", event_context())

    assert "sensitive" not in str(caught.value)
    assert "503" not in str(caught.value)


@pytest.mark.parametrize(
    "response_content",
    [
        "not-json",
        json.dumps({"event_type": "delay", "unknown": "field"}),
        json.dumps(
            {
                "event_type": "delay",
                "event_type_quote": None,
                "flight_id": None,
                "flight_quote": None,
                "occurred_at_quote": None,
                "delay_minutes": None,
                "delay_minutes_quote": None,
                "new_gate_id": None,
                "new_gate_quote": None,
            }
        ),
    ],
)
def test_adapter_rejects_non_json_or_contract_invalid_output(response_content) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return completion(response_content, request)

    provider = OpenAICompatibleEventProvider(
        settings(),
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(AIProviderInvalidOutputError):
        provider.extract_event("SIM102 延误", event_context())

