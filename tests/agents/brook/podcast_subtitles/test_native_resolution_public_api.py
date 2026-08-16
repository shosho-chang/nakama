"""Public operator surface for typed native resolution."""

from __future__ import annotations

from agents.brook.podcast_subtitles import (
    HumanOriginalConfirmationReceiptV2,
    NativeCorrectionDecisionV2,
    NativeResolveCheckpointV2,
    OriginalConfirmationAuthorizationV2,
    OriginalConfirmationPolicyV2,
    ResolveNativeRequest,
    build_native_correction_decision,
    build_original_confirmation_authorization,
    default_original_confirmation_policy,
    human_original_confirmation_receipt_bytes,
    native_correction_decision_bytes,
    original_confirmation_authorization_bytes,
    original_confirmation_policy_bytes,
    verify_human_original_confirmation_receipt,
    verify_native_correction_decision,
    verify_original_confirmation_authorization,
    verify_original_confirmation_policy,
)
from agents.brook.podcast_subtitles.facade import PodcastSubtitleFacade


def test_native_resolution_contracts_are_exported() -> None:
    assert all(
        value is not None
        for value in (
            HumanOriginalConfirmationReceiptV2,
            NativeCorrectionDecisionV2,
            NativeResolveCheckpointV2,
            OriginalConfirmationAuthorizationV2,
            OriginalConfirmationPolicyV2,
            ResolveNativeRequest,
            build_native_correction_decision,
            build_original_confirmation_authorization,
            default_original_confirmation_policy,
            human_original_confirmation_receipt_bytes,
            native_correction_decision_bytes,
            original_confirmation_authorization_bytes,
            original_confirmation_policy_bytes,
            verify_human_original_confirmation_receipt,
            verify_native_correction_decision,
            verify_original_confirmation_authorization,
            verify_original_confirmation_policy,
        )
    )


def test_facade_decide_native_delegates_the_exact_request() -> None:
    request = ResolveNativeRequest(
        generation_id="generation-" + "1" * 64,
        correction_acceptance_verdict=b"verdict",
        correction_acceptance_policy=b"policy",
    )
    sentinel = object()

    class StubModule:
        seen = None

        def resolve_native(self, supplied):
            self.seen = supplied
            return sentinel

    module = StubModule()
    facade = PodcastSubtitleFacade(module)  # type: ignore[arg-type]

    assert facade.decide_native(request) is sentinel
    assert module.seen is request
