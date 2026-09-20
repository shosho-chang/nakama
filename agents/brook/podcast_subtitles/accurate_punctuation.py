"""Replay Qwen provider punctuation as segmentation-only boundary evidence.

The forced aligner intentionally removes provider punctuation from Recognition
Evidence text.  This module authenticates the immutable Qwen envelope again,
replays the same owned-range repair used by recognition, and projects only
unambiguous punctuation edges onto corrected token IDs.  It never adds a
provider character to corrected or rendered subtitle text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Sequence

from opencc import OpenCC
from pydantic import ValidationError

from shared.schemas.podcast_subtitles_v2 import EvidenceToken, RecognitionEvidence

from .accurate_correction import CorrectedTimedToken
from .accurate_recognition import (
    QwenOwnedRangeRepair,
    _repair_qwen_owned_range_overlaps,
)
from .accurate_segmentation import SentenceBoundaryHint
from .adapters import recognition as qwen_recognition
from .hashing import canonical_json_bytes, hash_object, measure_regular_file, sha256_bytes
from .ports import AdapterInputError, AdapterIntegrityError

PROVIDER_PUNCTUATION_BOUNDARY_ALGORITHM = (
    "qwen-provider-punctuation-lexically-anchored-cross-asr-edge-v3"
)
_BOUNDARY_HASH_NAMESPACE = "podcast-subtitle-v2/provider-sentence-boundary-set/v3"
_HARD_BOUNDARY_CHARACTERS = frozenset("。！？!?；;")
_SOFT_BOUNDARY_CHARACTERS = frozenset("，,、：:\n\r")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_CROSS_ASR_EDGE_MAX_DISTANCE_MS = 450
_CROSS_ASR_ENDPOINT_MAX_DISTANCE_MS = 800
_PUNCTUATION_MATCH_OPENCC = OpenCC("t2s")

BoundarySignalKind = Literal["hard", "soft"]
ProjectionStatus = Literal["not_applicable", "verified"]


class AccuratePunctuationIntegrityError(RuntimeError):
    """A production punctuation projection could not replay exact Evidence."""


@dataclass(frozen=True, slots=True)
class VerifiedProviderBoundarySignal:
    """One raw separator already bound to adjacent primary Evidence tokens."""

    left_primary_token_id: str
    right_primary_token_id: str
    strength: float
    signal_kind: BoundarySignalKind
    source_separator_id: str

    def __post_init__(self) -> None:
        for label, value in (
            ("left_primary_token_id", self.left_primary_token_id),
            ("right_primary_token_id", self.right_primary_token_id),
            ("source_separator_id", self.source_separator_id),
        ):
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(f"{label} must be non-blank and trimmed")
        if self.left_primary_token_id == self.right_primary_token_id:
            raise ValueError("provider boundary signal requires two distinct token IDs")
        expected_strength = 1.0 if self.signal_kind == "hard" else 0.75
        if self.strength != expected_strength:
            raise ValueError(
                f"{self.signal_kind} provider boundary strength must be {expected_strength}"
            )


@dataclass(frozen=True, slots=True)
class ProviderSentenceBoundary:
    """One deduplicated corrected-token edge and its recognition provenance."""

    after_token_id: str
    strength: float
    source_primary_token_edges: tuple[tuple[str, str], ...]
    signal_kinds: tuple[BoundarySignalKind, ...]
    source_separator_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        SentenceBoundaryHint(after_token_id=self.after_token_id, strength=self.strength)
        if not self.source_primary_token_edges or not self.signal_kinds:
            raise ValueError("provider boundary requires primary-edge and signal provenance")
        if not self.source_separator_ids:
            raise ValueError("provider boundary requires separator provenance")
        if self.source_primary_token_edges != tuple(sorted(set(self.source_primary_token_edges))):
            raise ValueError("provider boundary primary edges must be sorted and unique")
        if self.signal_kinds != tuple(sorted(set(self.signal_kinds))):
            raise ValueError("provider boundary signal kinds must be sorted and unique")
        if self.source_separator_ids != tuple(sorted(set(self.source_separator_ids))):
            raise ValueError("provider boundary separator IDs must be sorted and unique")


@dataclass(frozen=True, slots=True)
class ProviderPunctuationProjection:
    """Deterministic boundary set consumed by semantic segmentation."""

    status: ProjectionStatus
    raw_output_sha256: str | None
    boundaries: tuple[ProviderSentenceBoundary, ...]
    boundary_hash: str
    count: int

    def __post_init__(self) -> None:
        if self.status == "verified":
            if self.raw_output_sha256 is None or not _SHA256_RE.fullmatch(
                self.raw_output_sha256
            ):
                raise ValueError("verified provider punctuation requires a raw SHA-256")
        elif self.raw_output_sha256 is not None:
            raise ValueError("not-applicable provider punctuation cannot claim raw Evidence")
        if self.count != len(self.boundaries):
            raise ValueError("provider punctuation count differs from its boundary set")
        expected_hash = _boundary_set_hash(self.boundaries)
        if self.boundary_hash != expected_hash:
            raise ValueError("provider punctuation boundary hash does not cover its set")
        boundary_ids = tuple(boundary.after_token_id for boundary in self.boundaries)
        if len(set(boundary_ids)) != len(boundary_ids):
            raise ValueError("provider punctuation boundary IDs must be unique")

    @property
    def sentence_hints(self) -> tuple[SentenceBoundaryHint, ...]:
        return tuple(
            SentenceBoundaryHint(
                after_token_id=boundary.after_token_id,
                strength=boundary.strength,
            )
            for boundary in self.boundaries
        )

    @property
    def identity(self) -> dict[str, object]:
        """Portable identity embedded in config and generation addressing."""

        return {
            "algorithm": PROVIDER_PUNCTUATION_BOUNDARY_ALGORITHM,
            "status": self.status,
            "boundary_hash": self.boundary_hash,
            "boundary_count": self.count,
        }

    def recognition_receipt(self) -> dict[str, object]:
        return {
            "type": "qwen_provider_punctuation_sentence_boundary",
            "algorithm": PROVIDER_PUNCTUATION_BOUNDARY_ALGORITHM,
            "status": self.status,
            "boundary_hash": self.boundary_hash,
            "count": self.count,
            "raw_output_sha256": self.raw_output_sha256,
            "items": self.boundaries,
        }


def _boundary_set_hash(boundaries: Sequence[ProviderSentenceBoundary]) -> str:
    return hash_object(
        {
            "namespace": _BOUNDARY_HASH_NAMESPACE,
            "items": tuple(
                {
                    "after_token_id": boundary.after_token_id,
                    "strength": boundary.strength,
                }
                for boundary in boundaries
            ),
        }
    )


def _projection(
    *,
    status: ProjectionStatus,
    raw_output_sha256: str | None,
    boundaries: Sequence[ProviderSentenceBoundary],
) -> ProviderPunctuationProjection:
    canonical_boundaries = tuple(boundaries)
    return ProviderPunctuationProjection(
        status=status,
        raw_output_sha256=raw_output_sha256,
        boundaries=canonical_boundaries,
        boundary_hash=_boundary_set_hash(canonical_boundaries),
        count=len(canonical_boundaries),
    )


def _contiguous(indices: Sequence[int]) -> bool:
    ordered = tuple(sorted(set(indices)))
    return bool(ordered) and ordered == tuple(range(ordered[0], ordered[-1] + 1))


def project_verified_provider_boundaries(
    signals: Sequence[VerifiedProviderBoundarySignal],
    *,
    corrected_tokens: Sequence[CorrectedTimedToken],
    raw_output_sha256: str,
) -> ProviderPunctuationProjection:
    """Map verified primary-token edges onto unambiguous corrected-token edges.

    Correction may atomize one primary token into several display tokens; in
    that case the edge is projected after the last atom.  If correction merged
    both sides into one token, or otherwise made the edge ambiguous, it is
    deliberately omitted rather than guessed.
    """

    if not _SHA256_RE.fullmatch(raw_output_sha256):
        raise ValueError("provider punctuation raw_output_sha256 must be lowercase SHA-256")
    corrected = tuple(corrected_tokens)
    corrected_ids = tuple(token.id for token in corrected)
    if any(not token_id.strip() for token_id in corrected_ids) or len(
        set(corrected_ids)
    ) != len(corrected_ids):
        raise AccuratePunctuationIntegrityError(
            "corrected tokens require unique non-blank IDs for punctuation projection"
        )

    source_positions: dict[str, list[int]] = {}
    for index, token in enumerate(corrected):
        for source_id in set(token.source_primary_token_ids):
            source_positions.setdefault(source_id, []).append(index)

    projected: list[tuple[VerifiedProviderBoundarySignal, int]] = []
    for signal in sorted(
        signals,
        key=lambda item: (
            item.left_primary_token_id,
            item.right_primary_token_id,
            item.source_separator_id,
            item.signal_kind,
        ),
    ):
        left_positions = source_positions.get(signal.left_primary_token_id, [])
        right_positions = source_positions.get(signal.right_primary_token_id, [])
        if not _contiguous(left_positions) or not _contiguous(right_positions):
            continue
        left_index = max(left_positions)
        right_index = min(right_positions)
        if left_index + 1 != right_index:
            # This includes the fail-closed case where both primary tokens were
            # folded into the same corrected token.
            continue
        projected.append((signal, left_index))

    return _projection_from_signal_edges(
        projected,
        corrected_tokens=corrected,
        raw_output_sha256=raw_output_sha256,
    )


def _projection_from_signal_edges(
    projected: Sequence[tuple[VerifiedProviderBoundarySignal, int]],
    *,
    corrected_tokens: Sequence[CorrectedTimedToken],
    raw_output_sha256: str,
) -> ProviderPunctuationProjection:
    corrected = tuple(corrected_tokens)
    aggregated: dict[int, dict[str, object]] = {}
    for signal, left_index in projected:
        item = aggregated.setdefault(
            left_index,
            {
                "strength": signal.strength,
                "primary_edges": set(),
                "signal_kinds": set(),
                "separator_ids": set(),
            },
        )
        item["strength"] = max(float(item["strength"]), signal.strength)
        primary_edges = item["primary_edges"]
        signal_kinds = item["signal_kinds"]
        separator_ids = item["separator_ids"]
        assert isinstance(primary_edges, set)
        assert isinstance(signal_kinds, set)
        assert isinstance(separator_ids, set)
        primary_edges.add(
            (signal.left_primary_token_id, signal.right_primary_token_id)
        )
        signal_kinds.add(signal.signal_kind)
        separator_ids.add(signal.source_separator_id)

    boundaries: list[ProviderSentenceBoundary] = []
    for left_index, item in sorted(aggregated.items()):
        primary_edges = item["primary_edges"]
        signal_kinds = item["signal_kinds"]
        separator_ids = item["separator_ids"]
        assert isinstance(primary_edges, set)
        assert isinstance(signal_kinds, set)
        assert isinstance(separator_ids, set)
        boundaries.append(
            ProviderSentenceBoundary(
                after_token_id=corrected[left_index].id,
                strength=float(item["strength"]),
                source_primary_token_edges=tuple(sorted(primary_edges)),
                signal_kinds=tuple(sorted(signal_kinds)),
                source_separator_ids=tuple(sorted(separator_ids)),
            )
        )
    return _projection(
        status="verified",
        raw_output_sha256=raw_output_sha256,
        boundaries=boundaries,
    )


def _temporal_edge_index(
    signal: VerifiedProviderBoundarySignal,
    *,
    qwen_tokens: dict[str, EvidenceToken],
    corrected_tokens: Sequence[CorrectedTimedToken],
) -> int | None:
    """Map one Qwen edge to a unique, lexically anchored nearby edge.

    Nearest-time projection alone is unsafe across recognizers: their word
    timestamps can make the edge *after* the right-hand word closer than the
    actual punctuation edge before it.  Requiring both lexical anchors prevents
    visible off-by-one cuts such as ``他／的``.
    """

    left_qwen = qwen_tokens.get(signal.left_primary_token_id)
    right_qwen = qwen_tokens.get(signal.right_primary_token_id)
    if left_qwen is None or right_qwen is None:
        return None
    target_ms = (left_qwen.end_ms + right_qwen.start_ms) // 2
    left_key = _lexical_key(left_qwen.text)
    right_key = _lexical_key(right_qwen.text)
    if not left_key or not right_key:
        return None
    candidates: list[tuple[int, int, int]] = []
    corrected = tuple(corrected_tokens)
    for index, (left, right) in enumerate(zip(corrected, corrected[1:])):
        edge_ms = (left.end_ms + right.start_ms) // 2
        edge_distance = abs(edge_ms - target_ms)
        left_distance = abs(left.end_ms - left_qwen.end_ms)
        right_distance = abs(right.start_ms - right_qwen.start_ms)
        if edge_distance > _CROSS_ASR_EDGE_MAX_DISTANCE_MS:
            continue
        if max(left_distance, right_distance) > _CROSS_ASR_ENDPOINT_MAX_DISTANCE_MS:
            continue
        left_context = _lexical_key(
            "".join(token.text for token in corrected[max(0, index - 15) : index + 1])
        )
        right_context = _lexical_key(
            "".join(token.text for token in corrected[index + 1 : index + 17])
        )
        if not left_context.endswith(left_key) or not right_context.startswith(right_key):
            continue
        candidates.append((edge_distance, left_distance + right_distance, index))
    if not candidates:
        return None
    candidates.sort()
    best = candidates[0]
    if len(candidates) > 1 and candidates[1][:2] == best[:2]:
        # Equal acoustic-clock support is not enough to choose between two
        # different corrected-token edges.
        return None
    return best[2]


def _lexical_key(value: str) -> str:
    converted = _PUNCTUATION_MATCH_OPENCC.convert(value).casefold()
    return "".join(character for character in converted if character.isalnum())


def _project_qwen_boundaries_across_asr(
    signals: Sequence[VerifiedProviderBoundarySignal],
    *,
    qwen_tokens: Sequence[EvidenceToken],
    corrected_tokens: Sequence[CorrectedTimedToken],
    raw_output_sha256: str,
) -> ProviderPunctuationProjection:
    """Prefer exact lineage, then conservatively use the shared audio clock."""

    corrected = tuple(corrected_tokens)
    source_positions: dict[str, list[int]] = {}
    for index, token in enumerate(corrected):
        for source_id in set(token.source_primary_token_ids):
            source_positions.setdefault(source_id, []).append(index)
    qwen_by_id = {token.id: token for token in qwen_tokens}
    projected: list[tuple[VerifiedProviderBoundarySignal, int]] = []
    for signal in sorted(
        signals,
        key=lambda item: (
            item.left_primary_token_id,
            item.right_primary_token_id,
            item.source_separator_id,
            item.signal_kind,
        ),
    ):
        left_positions = source_positions.get(signal.left_primary_token_id, [])
        right_positions = source_positions.get(signal.right_primary_token_id, [])
        edge_index: int | None = None
        if _contiguous(left_positions) and _contiguous(right_positions):
            direct_left = max(left_positions)
            if direct_left + 1 == min(right_positions):
                edge_index = direct_left
        if edge_index is None:
            edge_index = _temporal_edge_index(
                signal,
                qwen_tokens=qwen_by_id,
                corrected_tokens=corrected,
            )
        if edge_index is not None:
            projected.append((signal, edge_index))
    return _projection_from_signal_edges(
        projected,
        corrected_tokens=corrected,
        raw_output_sha256=raw_output_sha256,
    )


def _separator_signal(text: str) -> tuple[float, BoundarySignalKind] | None:
    if any(character in _HARD_BOUNDARY_CHARACTERS for character in text):
        return 1.0, "hard"
    if any(character in _SOFT_BOUNDARY_CHARACTERS for character in text):
        return 0.75, "soft"
    # Spaces, tabs, quotes, brackets, and book-title marks are not sentence
    # evidence.  Newline is handled explicitly in the soft set above.
    return None


def _read_verified_qwen_envelope(
    primary: RecognitionEvidence,
    *,
    primary_owned_range_repairs: Sequence[QwenOwnedRangeRepair],
) -> qwen_recognition._QwenRecognitionEnvelope:
    try:
        raw_path = qwen_recognition._file_uri_path_for_adapter(primary.raw_output.uri)
        measured_hash, measured_size = measure_regular_file(raw_path)
        if (measured_hash, measured_size) != (
            primary.raw_output.sha256,
            primary.raw_output.size_bytes,
        ):
            raise AccuratePunctuationIntegrityError(
                "Qwen raw artifact hash/size differs from Recognition Evidence"
            )
        raw_bytes = raw_path.read_bytes()
        if (
            len(raw_bytes) != measured_size
            or sha256_bytes(raw_bytes) != measured_hash
        ):
            raise AccuratePunctuationIntegrityError(
                "Qwen raw artifact changed while punctuation was replayed"
            )
        parsed = qwen_recognition._parse_strict_json_bytes(
            raw_bytes,
            artifact_label="Qwen provider punctuation artifact",
        )
        if canonical_json_bytes(parsed) != raw_bytes:
            raise AccuratePunctuationIntegrityError(
                "Qwen provider punctuation artifact is not canonical JSON"
            )
        envelope = qwen_recognition._QwenRecognitionEnvelope.model_validate(parsed)
        if envelope.normalized_audio.sha256 != primary.normalized_audio_hash:
            raise AccuratePunctuationIntegrityError(
                "Qwen provider punctuation crossed the normalized-audio clock"
            )
        repaired, replayed_repairs = _repair_qwen_owned_range_overlaps(envelope)
        if replayed_repairs != tuple(primary_owned_range_repairs):
            raise AccuratePunctuationIntegrityError(
                "Qwen owned-range repairs do not replay Recognition result"
            )
        _validate_repaired_words_against_evidence(repaired.merged_words, primary.tokens)
        return repaired
    except AccuratePunctuationIntegrityError:
        raise
    except (
        AdapterInputError,
        AdapterIntegrityError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ) as exc:
        raise AccuratePunctuationIntegrityError(
            f"Qwen provider punctuation Evidence could not be replayed: {exc}"
        ) from exc


def _validate_repaired_words_against_evidence(
    words: Sequence[qwen_recognition._QwenMergedWord],
    tokens: Sequence[EvidenceToken],
) -> None:
    if len(words) != len(tokens):
        raise AccuratePunctuationIntegrityError(
            "repaired Qwen merged words differ from primary Evidence token count"
        )
    for index, (word, token) in enumerate(zip(words, tokens)):
        if (word.word, word.start_ms, word.end_ms) != (
            token.text,
            token.start_ms,
            token.end_ms,
        ):
            raise AccuratePunctuationIntegrityError(
                f"repaired Qwen merged word {index} differs from primary Evidence"
            )


def _verified_separator_signals(
    envelope: qwen_recognition._QwenRecognitionEnvelope,
    tokens: Sequence[EvidenceToken],
) -> tuple[VerifiedProviderBoundarySignal, ...]:
    owned_positions: dict[tuple[str, str], int] = {}
    for position, word in enumerate(envelope.merged_words):
        key = (word.source_chunk_id, word.source_item_id)
        if key in owned_positions:
            raise AccuratePunctuationIntegrityError(
                "repaired Qwen merged words have duplicate source ownership"
            )
        owned_positions[key] = position

    signals: list[VerifiedProviderBoundarySignal] = []
    for chunk in envelope.chunks:
        for separator_index, separator in enumerate(chunk.separators):
            classified = _separator_signal(separator.text)
            if classified is None:
                continue
            left_index = separator.left_alignment_index
            right_index = separator.right_alignment_index
            if (
                left_index is None
                or right_index is None
                or right_index != left_index + 1
                or left_index >= len(chunk.alignment_items)
                or right_index >= len(chunk.alignment_items)
            ):
                continue
            left_item = chunk.alignment_items[left_index]
            right_item = chunk.alignment_items[right_index]
            left_position = owned_positions.get((chunk.id, left_item.id))
            right_position = owned_positions.get((chunk.id, right_item.id))
            if (
                left_position is None
                or right_position is None
                or right_position != left_position + 1
            ):
                continue
            strength, signal_kind = classified
            signals.append(
                VerifiedProviderBoundarySignal(
                    left_primary_token_id=tokens[left_position].id,
                    right_primary_token_id=tokens[right_position].id,
                    strength=strength,
                    signal_kind=signal_kind,
                    source_separator_id=(
                        f"{chunk.id}:separator:{separator_index:06d}"
                    ),
                )
            )
    return tuple(signals)


def derive_qwen_sentence_hints(
    *,
    primary: RecognitionEvidence,
    primary_owned_range_repairs: Sequence[QwenOwnedRangeRepair],
    corrected_tokens: Sequence[CorrectedTimedToken],
) -> ProviderPunctuationProjection:
    """Authenticate and project production Qwen punctuation, otherwise no-op."""

    if primary.adapter != qwen_recognition.Qwen3ASRRecognizerAdapter.ADAPTER_NAME:
        return _projection(
            status="not_applicable",
            raw_output_sha256=None,
            boundaries=(),
        )
    envelope = _read_verified_qwen_envelope(
        primary,
        primary_owned_range_repairs=primary_owned_range_repairs,
    )
    expected_raw_ref = (f"raw:{primary.raw_output.sha256}",)
    if any(token.evidence_refs != expected_raw_ref for token in primary.tokens):
        raise AccuratePunctuationIntegrityError(
            "primary Qwen tokens do not point exactly to the replayed raw artifact"
        )
    signals = _verified_separator_signals(envelope, primary.tokens)
    return _project_qwen_boundaries_across_asr(
        signals,
        qwen_tokens=primary.tokens,
        corrected_tokens=corrected_tokens,
        raw_output_sha256=primary.raw_output.sha256,
    )


__all__ = [
    "AccuratePunctuationIntegrityError",
    "PROVIDER_PUNCTUATION_BOUNDARY_ALGORITHM",
    "ProviderPunctuationProjection",
    "ProviderSentenceBoundary",
    "VerifiedProviderBoundarySignal",
    "derive_qwen_sentence_hints",
    "project_verified_provider_boundaries",
]
