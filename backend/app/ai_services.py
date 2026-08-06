"""Read-only orchestration for M5 assistant event drafts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from uuid import uuid4

from .ai_event_parser import (
    AuthoritativeEventContext,
    AuthoritativeFlightRef,
    AuthoritativeGateRef,
    ParsedEventDraft,
    parse_event_text,
)
from .ai_model_parser import parse_model_extraction
from .ai_models import (
    AssistanceFallbackReason,
    AssistanceSource,
    AssistanceTrace,
    EventDraftBasis,
    EventDraftRequest,
    EventDraftResponse,
    EventDraftSubmissionRequest,
    RequestedAssistanceMode,
    RuntimeEventDraftContext,
    ScenarioEventDraftContext,
)
from .ai_provider import (
    AIProviderInvalidOutputError,
    AIProviderRequestError,
    AIProviderTimeoutError,
    EventExtractionProvider,
)
from .models import Scenario
from .api_models import ApplyEventsRequest, ScenarioRecord
from .repository import InMemoryScenarioRepository, ScenarioNotFoundError
from .runtime_repository import RuntimeSessionNotFoundError
from .runtime_services import RuntimeSessionService
from .runtime_models import RuntimeSessionSnapshot
from .services import ScenarioService


class AssistantServiceError(Exception):
    """Base class for safe assistant context failures."""


class AssistantContextNotFoundError(AssistantServiceError):
    pass


class AssistantVersionConflictError(AssistantServiceError):
    def __init__(self, expected_version: int, current_version: int) -> None:
        super().__init__(
            f"expected assistant scenario version {expected_version}, "
            f"current version is {current_version}"
        )
        self.expected_version = expected_version
        self.current_version = current_version


class AssistantRevisionConflictError(AssistantServiceError):
    def __init__(self, expected_revision: int, current_revision: int) -> None:
        super().__init__(
            f"expected assistant runtime revision {expected_revision}, "
            f"current revision is {current_revision}"
        )
        self.expected_revision = expected_revision
        self.current_revision = current_revision


class AssistantPlanContextMismatchError(AssistantServiceError):
    def __init__(self, plan_id: str) -> None:
        super().__init__(f"assistant plan {plan_id} does not belong to the requested context")
        self.plan_id = plan_id


@dataclass(frozen=True, slots=True)
class _ResolvedEventContext:
    basis: EventDraftBasis
    parser_context: AuthoritativeEventContext


class EventAssistantService:
    """Resolve authoritative facts and return a non-persisted review draft."""

    def __init__(
        self,
        scenario_repository: InMemoryScenarioRepository,
        runtime_service: RuntimeSessionService,
        *,
        event_provider: EventExtractionProvider | None = None,
        draft_id_factory: Callable[[], str] | None = None,
        event_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.scenario_repository = scenario_repository
        self.runtime_service = runtime_service
        self._event_provider = event_provider
        self._draft_id_factory = draft_id_factory or (
            lambda: f"DRAFT-{uuid4().hex[:16].upper()}"
        )
        self._event_id_factory = event_id_factory or (
            lambda: f"EVT-AI-{uuid4().hex[:16].upper()}"
        )

    def create_event_draft(self, request: EventDraftRequest) -> EventDraftResponse:
        resolved = self._resolve_context(request.context)
        event_id = self._event_id_factory()
        if request.assistance_mode is RequestedAssistanceMode.DETERMINISTIC_ONLY:
            parsed = self._parse_deterministically(request, resolved, event_id)
            trace = AssistanceTrace(source=AssistanceSource.DETERMINISTIC_RULES)
        elif self._event_provider is None:
            parsed = self._parse_deterministically(request, resolved, event_id)
            trace = AssistanceTrace(
                source=AssistanceSource.DETERMINISTIC_RULES,
                fallback_reason=AssistanceFallbackReason.MODEL_NOT_CONFIGURED,
            )
        else:
            try:
                extraction = self._event_provider.extract_event(
                    request.text,
                    resolved.parser_context,
                )
                parsed = parse_model_extraction(
                    extraction,
                    request.text,
                    resolved.parser_context,
                    event_id=event_id,
                )
            except AIProviderTimeoutError:
                parsed = self._parse_deterministically(request, resolved, event_id)
                trace = self._fallback_trace(AssistanceFallbackReason.MODEL_TIMEOUT)
            except AIProviderRequestError:
                parsed = self._parse_deterministically(request, resolved, event_id)
                trace = self._fallback_trace(AssistanceFallbackReason.PROVIDER_ERROR)
            except AIProviderInvalidOutputError:
                parsed = self._parse_deterministically(request, resolved, event_id)
                trace = self._fallback_trace(
                    AssistanceFallbackReason.INVALID_MODEL_OUTPUT
                )
            else:
                trace = AssistanceTrace(
                    source=AssistanceSource.LANGUAGE_MODEL,
                    provider_attempted=True,
                    model_label=self._event_provider.model_label,
                )
        return EventDraftResponse(
            draft_id=self._draft_id_factory(),
            basis=resolved.basis,
            status=parsed.status,
            trace=trace,
            event=parsed.event,
            evidence=parsed.evidence,
            missing_fields=parsed.missing_fields,
            clarification_questions=parsed.clarification_questions,
            warnings=parsed.warnings,
        )

    def submit_event_draft(
        self,
        request: EventDraftSubmissionRequest,
    ) -> ScenarioRecord | RuntimeSessionSnapshot:
        event = request.draft.event
        assert event is not None
        basis = request.draft.basis
        if request.scope == "scenario":
            try:
                state = self.scenario_repository.get_state_snapshot(basis.scenario_id)
            except ScenarioNotFoundError as error:
                raise AssistantContextNotFoundError(
                    f"assistant scenario {basis.scenario_id} was not found"
                ) from error
            if state.current_version != basis.scenario_version:
                raise AssistantVersionConflictError(
                    basis.scenario_version,
                    state.current_version,
                )
            return ScenarioService(self.scenario_repository).apply_events(
                basis.scenario_id,
                ApplyEventsRequest(
                    expected_version=basis.scenario_version,
                    events=[event],
                ),
            )

        session_id = basis.runtime_session_id
        expected_revision = basis.runtime_revision
        objective_profile = request.objective_profile
        assert session_id is not None
        assert expected_revision is not None
        assert objective_profile is not None
        try:
            snapshot = self.runtime_service.get_session(session_id)
        except RuntimeSessionNotFoundError as error:
            raise AssistantContextNotFoundError(
                f"assistant runtime session {session_id} was not found"
            ) from error
        if snapshot.revision != expected_revision:
            raise AssistantRevisionConflictError(
                expected_revision,
                snapshot.revision,
            )
        if (
            snapshot.scenario_id != basis.scenario_id
            or snapshot.current_scenario_version != basis.scenario_version
        ):
            raise AssistantRevisionConflictError(
                expected_revision,
                snapshot.revision,
            )
        return self.runtime_service.submit_reviewed_event(
            session_id,
            expected_revision=expected_revision,
            event=event,
            objective_profile=objective_profile,
        )

    @staticmethod
    def _parse_deterministically(
        request: EventDraftRequest,
        resolved: _ResolvedEventContext,
        event_id: str,
    ) -> ParsedEventDraft:
        return parse_event_text(
            request.text,
            resolved.parser_context,
            event_id=event_id,
        )

    @staticmethod
    def _fallback_trace(reason: AssistanceFallbackReason) -> AssistanceTrace:
        return AssistanceTrace(
            source=AssistanceSource.DETERMINISTIC_RULES,
            provider_attempted=True,
            fallback_reason=reason,
        )

    def _resolve_context(
        self,
        context: ScenarioEventDraftContext | RuntimeEventDraftContext,
    ) -> _ResolvedEventContext:
        if isinstance(context, ScenarioEventDraftContext):
            return self._resolve_scenario_context(context)
        return self._resolve_runtime_context(context)

    def _resolve_scenario_context(
        self,
        context: ScenarioEventDraftContext,
    ) -> _ResolvedEventContext:
        try:
            state = self.scenario_repository.get_state_snapshot(context.scenario_id)
        except ScenarioNotFoundError as error:
            raise AssistantContextNotFoundError(
                f"assistant scenario {context.scenario_id} was not found"
            ) from error
        if context.expected_version != state.current_version:
            raise AssistantVersionConflictError(
                context.expected_version,
                state.current_version,
            )
        return _ResolvedEventContext(
            basis=EventDraftBasis(
                scenario_id=state.scenario.scenario_id,
                scenario_version=state.current_version,
                reference_time=context.reference_time,
            ),
            parser_context=_scenario_parser_context(
                state.scenario,
                reference_time=context.reference_time,
            ),
        )

    def _resolve_runtime_context(
        self,
        context: RuntimeEventDraftContext,
    ) -> _ResolvedEventContext:
        try:
            snapshot = self.runtime_service.get_session(context.session_id)
        except RuntimeSessionNotFoundError as error:
            raise AssistantContextNotFoundError(
                f"assistant runtime session {context.session_id} was not found"
            ) from error
        if context.expected_revision != snapshot.revision:
            raise AssistantRevisionConflictError(
                context.expected_revision,
                snapshot.revision,
            )
        try:
            baseline = self.scenario_repository.get_baseline(snapshot.scenario_id)
        except ScenarioNotFoundError as error:
            raise AssistantContextNotFoundError(
                f"assistant runtime scenario {snapshot.scenario_id} was not found"
            ) from error

        display_codes = {
            flight.flight_id: flight.display_code for flight in baseline.flights
        }
        flights = tuple(
            AuthoritativeFlightRef(
                flight_id=flight.flight_id,
                display_code=display_codes.get(flight.flight_id, flight.flight_id),
                gate_id=flight.gate_id,
            )
            for flight in snapshot.flights
        )
        return _ResolvedEventContext(
            basis=EventDraftBasis(
                scenario_id=snapshot.scenario_id,
                scenario_version=snapshot.current_scenario_version,
                reference_time=snapshot.clock.simulation_time,
                runtime_session_id=snapshot.session_id,
                runtime_revision=snapshot.revision,
            ),
            parser_context=AuthoritativeEventContext(
                reference_time=snapshot.clock.simulation_time,
                window_start=baseline.window_start,
                window_end=baseline.window_end,
                flights=flights,
                gates=_gate_refs(
                    baseline,
                    extra_gate_ids={flight.gate_id for flight in snapshot.flights},
                ),
            ),
        )


def _scenario_parser_context(
    scenario: Scenario,
    *,
    reference_time: datetime,
) -> AuthoritativeEventContext:
    return AuthoritativeEventContext(
        reference_time=reference_time,
        window_start=scenario.window_start,
        window_end=scenario.window_end,
        flights=tuple(
            AuthoritativeFlightRef(
                flight_id=flight.flight_id,
                display_code=flight.display_code,
                gate_id=flight.gate_id,
            )
            for flight in scenario.flights
        ),
        gates=_gate_refs(
            scenario,
            extra_gate_ids={flight.gate_id for flight in scenario.flights},
        ),
    )


def _gate_refs(
    scenario: Scenario,
    *,
    extra_gate_ids: set[str],
) -> tuple[AuthoritativeGateRef, ...]:
    gate_ids = {
        zone.zone_id
        for zone in scenario.zones
        if zone.zone_id.startswith("GATE-") or zone.zone_id in extra_gate_ids
    }
    zones = {zone.zone_id: zone for zone in scenario.zones}
    return tuple(
        AuthoritativeGateRef(
            gate_id=gate_id,
            display_name=zones[gate_id].name if gate_id in zones else gate_id,
        )
        for gate_id in sorted(gate_ids)
    )
