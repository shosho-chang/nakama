"""Versioned policy and display profiles for Podcast Subtitle V2.

Callers select a named policy/profile.  Provider prompts, model knobs, and
fixed-width splitting heuristics remain inside the Module and its Adapters.
"""

from __future__ import annotations

from dataclasses import dataclass

from shared.schemas.podcast_subtitles_v2 import (
    AcceptancePolicySnapshot,
    ProjectionProfile,
    ReferenceRetrievalPolicySnapshot,
)

from .hashing import hash_object


@dataclass(frozen=True, slots=True)
class CorrectionAuditExecutionDefaults:
    """Provider-neutral bounds for replayable correction audit packets."""

    text_max_cells_per_packet: int = 80
    text_max_spans_per_packet: int = 6
    text_max_tokens_per_packet: int = 256
    text_max_window_duration_ms: int = 30_000
    audio_max_cells_per_packet: int = 60
    audio_max_spans_per_packet: int = 4
    audio_max_tokens_per_packet: int = 160
    audio_max_window_duration_ms: int = 24_000
    context_halo_spans_per_side: int = 1
    audio_clip_padding_ms: int = 750
    audio_max_clip_duration_ms: int = 25_500

    def __post_init__(self) -> None:
        for field_name in (
            "text_max_cells_per_packet",
            "text_max_spans_per_packet",
            "text_max_tokens_per_packet",
            "text_max_window_duration_ms",
            "audio_max_cells_per_packet",
            "audio_max_spans_per_packet",
            "audio_max_tokens_per_packet",
            "audio_max_window_duration_ms",
            "audio_max_clip_duration_ms",
        ):
            value = getattr(self, field_name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{field_name} must be positive")
        if (
            type(self.context_halo_spans_per_side) is not int
            or type(self.audio_clip_padding_ms) is not int
            or self.context_halo_spans_per_side < 0
            or self.audio_clip_padding_ms < 0
        ):
            raise ValueError("execution context and clip padding defaults must be non-negative")
        required_clip_ms = self.audio_max_window_duration_ms + 2 * self.audio_clip_padding_ms
        if self.audio_max_clip_duration_ms < required_clip_ms:
            raise ValueError("audio_max_clip_duration_ms must preserve a full window plus padding")


@dataclass(frozen=True, slots=True)
class SubtitlePolicy:
    """Versioned editorial policy identity, not a bag of provider settings."""

    id: str = "nakama-zh-hant-podcast"
    version: int = 1
    language: str = "zh-Hant-TW"
    full_audit_max_spans_per_request: int = 24
    full_audit_max_tokens_per_request: int = 256
    max_reference_results_per_span: int = 6
    max_reference_candidate_terms_per_span: int = 16
    reference_context_left_unicode_scalar_budget: int = 5
    reference_context_right_unicode_scalar_budget: int = 5
    reference_context_max_adjacent_spans_per_side: int = 5
    reference_context_max_anchor_unicode_scalars: int = 256
    reference_context_max_query_unicode_scalars: int = 266
    reference_context_stop_at_known_speaker_change: bool = True
    reference_context_max_adjacent_gap_ms: int = 2_000
    retrievable_risk_codes: tuple[str, ...] = (
        "code_switch",
        "correction_proposal",
        "recognition_disagreement",
        "suspicious_token",
    )
    permit_unresolved_low_risk: bool = True

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.language.strip():
            raise ValueError("subtitle policy requires non-blank id and language")
        if self.version < 1:
            raise ValueError("subtitle policy version must be positive")
        for field_name in (
            "full_audit_max_spans_per_request",
            "full_audit_max_tokens_per_request",
            "max_reference_results_per_span",
            "max_reference_candidate_terms_per_span",
            "reference_context_max_anchor_unicode_scalars",
            "reference_context_max_query_unicode_scalars",
        ):
            if getattr(self, field_name) < 1:
                raise ValueError(f"{field_name} must be positive")
        for field_name in (
            "reference_context_left_unicode_scalar_budget",
            "reference_context_right_unicode_scalar_budget",
            "reference_context_max_adjacent_spans_per_side",
            "reference_context_max_adjacent_gap_ms",
        ):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} must be non-negative")
        if not self.retrievable_risk_codes or any(
            not code.strip() for code in self.retrievable_risk_codes
        ):
            raise ValueError("retrievable risk codes must be non-blank")
        if len(set(self.retrievable_risk_codes)) != len(self.retrievable_risk_codes):
            raise ValueError("retrievable risk codes must be unique")

    @property
    def content_hash(self) -> str:
        return hash_object(self)

    @property
    def acceptance_snapshot(self) -> AcceptancePolicySnapshot:
        """Return the acceptance rule that must survive a fresh process."""

        return AcceptancePolicySnapshot(permit_unresolved_low_risk=self.permit_unresolved_low_risk)

    def reference_retrieval_snapshot(
        self,
        vocabulary: tuple[str, ...],
    ) -> ReferenceRetrievalPolicySnapshot:
        """Persist every input needed to rebuild per-anchor Reference queries."""

        return ReferenceRetrievalPolicySnapshot(
            left_unicode_scalar_budget=self.reference_context_left_unicode_scalar_budget,
            right_unicode_scalar_budget=self.reference_context_right_unicode_scalar_budget,
            max_adjacent_spans_per_side=(self.reference_context_max_adjacent_spans_per_side),
            max_anchor_unicode_scalars=(self.reference_context_max_anchor_unicode_scalars),
            max_query_unicode_scalars=(self.reference_context_max_query_unicode_scalars),
            stop_at_known_speaker_change=(self.reference_context_stop_at_known_speaker_change),
            max_adjacent_gap_ms=self.reference_context_max_adjacent_gap_ms,
            max_candidate_terms=self.max_reference_candidate_terms_per_span,
            max_results=self.max_reference_results_per_span,
            retrievable_codes=self.retrievable_risk_codes,
            vocabulary=vocabulary,
        )


DEFAULT_SUBTITLE_POLICY = SubtitlePolicy()
DEFAULT_CORRECTION_AUDIT_EXECUTION = CorrectionAuditExecutionDefaults()

HORIZONTAL_16X9 = ProjectionProfile(
    id="nakama-zh-hant-16x9",
    profile_version=6,
    language="zh-Hant-TW",
    max_lines=1,
    # Horizontal podcast subtitles are rendered as one physical line.  Keep
    # the former two-line cue capacity (2 * 44 display columns) so changing the
    # layout does not create new cue boundaries or strand a protected unit.
    # The line target follows the independent cue target below; in one-line
    # mode it is metadata rather than a cue-partition objective.
    target_line_display_columns=54,
    hard_line_display_columns=88,
    # About 27 CJK characters per cue.  This is a soft density target; the
    # single physical line retains the former cue cap of about 44 CJK chars.
    target_cue_display_columns=54,
    # Roughly 18--36 CJK characters are all normal cue densities.  The former
    # single-point quadratic target pulled legal long sentences apart merely
    # to approach 27 characters; the band leaves those decisions to semantics.
    minimum_preferred_cue_display_columns=36,
    maximum_preferred_cue_display_columns=72,
    cue_density_center_tiebreak_penalty=0.20,
    min_cue_duration_ms=800,
    max_cue_duration_ms=7_000,
    max_reading_units_per_second=15.0,
    max_intercue_gap_ms=10_000,
    pause_reward_saturation_ms=700,
    semantic_break_penalty=18.0,
    short_cue_penalty=12.0,
    long_cue_penalty=12.0,
    cue_boundary_penalty=0.5,
    second_line_penalty=0.5,
    verified_pause_reward=1.0,
)

VERTICAL_9X16 = ProjectionProfile(
    id="nakama-zh-hant-9x16",
    profile_version=4,
    language="zh-Hant-TW",
    max_lines=2,
    target_line_display_columns=18,
    hard_line_display_columns=26,
    min_cue_duration_ms=800,
    max_cue_duration_ms=6_000,
    max_reading_units_per_second=13.0,
    max_intercue_gap_ms=10_000,
    pause_reward_saturation_ms=700,
    semantic_break_penalty=22.0,
    short_cue_penalty=14.0,
    long_cue_penalty=14.0,
    cue_boundary_penalty=0.5,
    second_line_penalty=0.5,
    verified_pause_reward=1.0,
)

_PROFILES = {profile.id: profile for profile in (HORIZONTAL_16X9, VERTICAL_9X16)}


def profile_by_id(profile_id: str) -> ProjectionProfile:
    try:
        return _PROFILES[profile_id]
    except KeyError as exc:
        raise ValueError(f"unknown subtitle projection profile: {profile_id!r}") from exc


__all__ = [
    "CorrectionAuditExecutionDefaults",
    "DEFAULT_CORRECTION_AUDIT_EXECUTION",
    "DEFAULT_SUBTITLE_POLICY",
    "HORIZONTAL_16X9",
    "SubtitlePolicy",
    "VERTICAL_9X16",
    "profile_by_id",
]
