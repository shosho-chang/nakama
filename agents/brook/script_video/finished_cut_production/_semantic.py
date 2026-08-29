"""Managed semantic seam for current Finished Cut Production requests."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from ._records import (
    ComponentProposal,
    DirectorEventProposal,
    DPEventProposal,
    EventRecord,
    StageProposal,
    StageRequest,
    VisualEventProposal,
)

_DEFAULT_PARENT = object()


SemanticDispatchState = Literal["pending", "ready", "failed", "indeterminate"]
SemanticRequestState = Literal["unclaimed", "claimed", "completed"]


@dataclass(frozen=True, slots=True)
class SemanticDispatchOutcome:
    """Private terminal/pending result of dispatching one exact StageRequest."""

    request_id: str
    state: SemanticDispatchState
    proposal: StageProposal | None = None
    reason_code: str | None = None
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if self.state == "ready":
            if self.proposal is None or self.reason_code is not None:
                raise ValueError("ready semantic dispatch requires exactly one proposal")
        elif self.proposal is not None:
            raise ValueError("non-ready semantic dispatch cannot carry a proposal")
        if self.state in {"failed", "indeterminate"} and not self.reason_code:
            raise ValueError("terminal semantic dispatch requires a reason code")


class SemanticAdapter(Protocol):
    """Dispatch one core-created current request through a managed authority seam."""

    def dispatch(self, request: StageRequest) -> SemanticDispatchOutcome: ...

    def outcome_for(self, request_id: str) -> SemanticDispatchOutcome | None: ...

    def outcome_for_request(
        self,
        request: StageRequest,
    ) -> SemanticDispatchOutcome | None: ...

    def dispatch_state(self, request_id: str) -> SemanticRequestState: ...


class SemanticDispatchLedger(Protocol):
    """Private durable claim/outcome authority used by the managed Adapter."""

    def claim(self, request: StageRequest) -> bool: ...

    def load(self, request: StageRequest) -> SemanticDispatchOutcome | None: ...

    def outcome_for(self, request_id: str) -> SemanticDispatchOutcome | None: ...

    def dispatch_state(self, request_id: str) -> SemanticRequestState: ...

    def complete(
        self,
        request: StageRequest,
        outcome: SemanticDispatchOutcome,
    ) -> None: ...


@dataclass(slots=True)
class DurableSemanticAdapter:
    """Exactly-once dispatch Module: claim first, then persist the typed outcome."""

    worker: SemanticAdapter
    ledger: SemanticDispatchLedger

    def dispatch(self, request: StageRequest) -> SemanticDispatchOutcome:
        existing = self.ledger.load(request)
        if existing is not None:
            return existing
        if not self.ledger.claim(request):
            claimed = self.ledger.load(request)
            if claimed is None:  # pragma: no cover - defensive against a broken ledger Adapter
                raise RuntimeError("semantic dispatch claim disappeared")
            return claimed
        try:
            outcome = self.worker.dispatch(request)
        except Exception as error:
            outcome = SemanticDispatchOutcome(
                request_id=request.request_id,
                state="failed",
                reason_code="semantic_dispatch_error",
                diagnostic=str(error)[:512],
            )
        if outcome.state == "pending":
            outcome = SemanticDispatchOutcome(
                request_id=request.request_id,
                state="failed",
                reason_code="semantic_dispatch_incomplete",
                diagnostic="synchronous semantic worker returned no terminal outcome",
            )
        self.ledger.complete(request, outcome)
        return outcome

    def outcome_for(self, request_id: str) -> SemanticDispatchOutcome | None:
        return self.ledger.outcome_for(request_id)

    def outcome_for_request(
        self,
        request: StageRequest,
    ) -> SemanticDispatchOutcome | None:
        return self.ledger.load(request)

    def dispatch_state(self, request_id: str) -> SemanticRequestState:
        return self.ledger.dispatch_state(request_id)


class InMemorySemanticAdapter:
    """Fixture adapter with no filesystem or static-response discovery."""

    def __init__(self) -> None:
        self._proposals: dict[str, StageProposal] = {}
        self._current_requests: dict[str, StageRequest] = {}
        self._requests_by_id: dict[str, StageRequest] = {}
        self._outcomes: dict[str, SemanticDispatchOutcome] = {}
        self._dispatched_request_ids: set[str] = set()

    def respond(
        self,
        request: StageRequest,
        *,
        events: Iterable[
            DirectorEventProposal | DPEventProposal | VisualEventProposal | EventRecord
        ],
        components: Iterable[ComponentProposal] | None = None,
        parent_acceptance_id: str | None | object = _DEFAULT_PARENT,
        **overrides: Any,
    ) -> None:
        parent = (
            request.parent_acceptance_id
            if parent_acceptance_id is _DEFAULT_PARENT
            else parent_acceptance_id
        )
        proposal = StageProposal(
            run_id=str(overrides.get("run_id", request.run_id)),
            request_id=str(overrides.get("request_id", request.request_id)),
            episode_id=str(overrides.get("episode_id", request.episode_id)),
            cut_id=str(overrides.get("cut_id", request.cut_id)),
            format=overrides.get("format", request.format),
            stage=overrides.get("stage", request.stage),
            attempt=int(overrides.get("attempt", request.attempt)),
            scope=overrides.get("scope", request.scope),
            event_id=overrides.get("event_id", request.event_id),
            parent_acceptance_id=parent if isinstance(parent, str) or parent is None else None,
            events=tuple(events),
            components=(request.components if components is None else tuple(components)),
            schema=str(overrides.get("schema", request.schema)),
        )
        self._proposals[request.request_id] = proposal
        self._outcomes.pop(request.request_id, None)

    def dispatch(self, request: StageRequest) -> SemanticDispatchOutcome:
        existing_request = self._requests_by_id.get(request.request_id)
        if existing_request is not None and existing_request != request:
            raise ValueError("semantic request ID was reused for another request")
        self._requests_by_id[request.request_id] = request
        self._dispatched_request_ids.add(request.request_id)
        self._current_requests[request.command_id] = request
        existing = self._outcomes.get(request.request_id)
        if existing is not None:
            return existing
        proposal = self._proposals.pop(request.request_id, None)
        if proposal is None:
            return SemanticDispatchOutcome(request.request_id, "pending")
        outcome = SemanticDispatchOutcome(request.request_id, "ready", proposal=proposal)
        self._outcomes[request.request_id] = outcome
        return outcome

    def outcome_for(self, request_id: str) -> SemanticDispatchOutcome | None:
        return self._outcomes.get(request_id)

    def outcome_for_request(
        self,
        request: StageRequest,
    ) -> SemanticDispatchOutcome | None:
        if self._requests_by_id.get(request.request_id) != request:
            return None
        return self._outcomes.get(request.request_id)

    def dispatch_state(self, request_id: str) -> SemanticRequestState:
        if request_id in self._outcomes:
            return "completed"
        if request_id in self._dispatched_request_ids:
            return "claimed"
        return "unclaimed"

    def proposal_for(self, request: StageRequest) -> StageProposal | None:
        """Compatibility helper used only by internal fixture tests."""

        outcome = self.dispatch(request)
        return outcome.proposal if outcome.state == "ready" else None

    def current_request(self, command_id: str) -> StageRequest:
        """Return the exact request observed by this fixture adapter."""

        return self._current_requests[command_id]
