"""Immutable human gold and deterministic transcript-quality evaluation.

This module is deliberately independent from recognition, correction, and
projection production code.  An annotation packet contains only audio
bindings and a frozen protocol.  Human gold and candidate output are separate
content-addressed artifacts that both bind that packet.  References may
authorize spelling, but never establish what was spoken.

Integrity failures raise ``ValueError``.  Missing human work is not an
integrity failure: evaluation returns a typed ``not_evaluated`` result and
never manufactures a score from candidate output or reference literals.
"""

from __future__ import annotations

import importlib.metadata
import json
import platform
import re
import sys
import unicodedata
from datetime import datetime, timezone
from enum import Enum
from importlib import resources
from pathlib import Path
from typing import Any, ClassVar, Iterable, Literal, Sequence, TypeVar

from opencc import OpenCC
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from .hashing import canonical_json_bytes, hash_object, measure_regular_file

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_LEXICAL_NORMALIZATION_PROFILE = (
    "unicode-nfkc-casefold-opencc-s2tw-ignore-punctuation-separators-controls-v1"
)
_WORD_TOKEN_UNAVAILABLE_REASON = "provider_token_boundaries_not_comparable"
_OPENCC_DISTRIBUTION = "opencc-python-reimplemented"
_OPENCC_CONFIG = "config/s2tw.json"
_OPENCC_INVENTORY = (
    _OPENCC_CONFIG,
    "dictionary/STPhrases.txt",
    "dictionary/STCharacters.txt",
    "dictionary/TWVariants.txt",
    "__init__.py",
    "opencc.py",
)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _require_nonempty(label: str, value: str) -> str:
    if not value:
        raise ValueError(f"{label} must be non-empty")
    return value


def _require_sha256(label: str, value: str) -> str:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")
    return value


def _require_unique_nonempty(label: str, values: Sequence[str]) -> tuple[str, ...]:
    result = tuple(values)
    if any(not item for item in result):
        raise ValueError(f"{label} must contain only non-empty strings")
    if len(set(result)) != len(result):
        raise ValueError(f"{label} must be unique")
    return result


def _artifact_hash(model: BaseModel, *, field: str, kind: str) -> str:
    return hash_object(
        {
            "artifact_kind": kind,
            **model.model_dump(mode="json", exclude={field}),
        }
    )


def _prospective_hash(payload: dict[str, Any], *, kind: str) -> str:
    return hash_object({"artifact_kind": kind, **payload})


def _normalise_text(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _parse_utc(label: str, value: str) -> datetime:
    if not value.endswith("Z"):
        raise ValueError(f"{label} must be canonical UTC ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{label} must be a valid UTC timestamp") from exc
    if parsed.tzinfo != timezone.utc or parsed.isoformat().replace("+00:00", "Z") != value:
        raise ValueError(f"{label} must be canonical UTC")
    return parsed


def _lexical_script_converter() -> OpenCC:
    """Return a fresh pinned Simplified-to-Taiwan-Traditional converter.

    Comparison uses one common Han orthography only to avoid charging a
    recognizer for its output script.  It does not rewrite Gold or candidate
    evidence.  The dependency is exact-pinned by the project lock and the
    policy identity is persisted in every metrics report.  A fresh instance
    avoids scoring against a stale in-memory dictionary after bytes drift.
    """

    return OpenCC("s2tw")


class LexicalEvaluatorInventoryItemV1(_StrictFrozenModel):
    path: str
    sha256: str
    size_bytes: int

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        return _require_nonempty("lexical evaluator inventory path", value)

    @field_validator("sha256")
    @classmethod
    def _hash(cls, value: str) -> str:
        return _require_sha256("lexical evaluator inventory sha256", value)

    @field_validator("size_bytes")
    @classmethod
    def _size(cls, value: int) -> int:
        if value < 1:
            raise ValueError("lexical evaluator inventory file must be non-empty")
        return value


class LexicalEvaluatorIdentityV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    normalization_profile: Literal[
        "unicode-nfkc-casefold-opencc-s2tw-ignore-punctuation-separators-controls-v1"
    ]
    implementation: Literal["opencc-python-reimplemented"]
    implementation_version: str
    conversion_config: Literal["s2tw"]
    python_implementation: str
    python_version: str
    python_cache_tag: str
    unicode_database_version: str
    inventory: tuple[LexicalEvaluatorInventoryItemV1, ...]
    inventory_hash: str
    evaluator_code_hash: str
    identity_hash: str

    @field_validator(
        "implementation_version",
        "python_implementation",
        "python_version",
        "python_cache_tag",
        "unicode_database_version",
    )
    @classmethod
    def _version(cls, value: str) -> str:
        return _require_nonempty("lexical evaluator implementation_version", value)

    @field_validator("inventory_hash", "evaluator_code_hash", "identity_hash")
    @classmethod
    def _hashes(cls, value: str, info: Any) -> str:
        return _require_sha256(info.field_name, value)

    @model_validator(mode="after")
    def _closed(self) -> LexicalEvaluatorIdentityV1:
        if tuple(item.path for item in self.inventory) != _OPENCC_INVENTORY:
            raise ValueError("lexical evaluator inventory is incomplete or reordered")
        if self.inventory_hash != hash_object(self.inventory):
            raise ValueError("lexical evaluator inventory_hash mismatch")
        if self.identity_hash != _artifact_hash(
            self, field="identity_hash", kind="lexical_evaluator_identity"
        ):
            raise ValueError("lexical evaluator identity_hash mismatch")
        return self


def measure_lexical_evaluator_identity() -> LexicalEvaluatorIdentityV1:
    """Measure every local byte that can change lexical recognition scoring."""

    package_root = Path(str(resources.files("opencc"))).resolve()
    if not package_root.is_dir():
        raise ValueError("OpenCC resources must be filesystem-backed")
    try:
        config = json.loads((package_root / _OPENCC_CONFIG).read_text(encoding="utf-8"))
        segmentation_file = config["segmentation"]["dict"]["file"]
        conversion_chain = config["conversion_chain"]
        first_group = tuple(
            item["file"] for item in conversion_chain[0]["dict"]["dicts"]
        )
        second_file = conversion_chain[1]["dict"]["file"]
    except (KeyError, TypeError, IndexError, json.JSONDecodeError) as exc:
        raise ValueError("OpenCC s2tw config is malformed") from exc
    if (
        segmentation_file != "STPhrases.txt"
        or first_group != ("STPhrases.txt", "STCharacters.txt")
        or second_file != "TWVariants.txt"
    ):
        raise ValueError("OpenCC s2tw config dictionary chain drifted")
    inventory_values: list[LexicalEvaluatorInventoryItemV1] = []
    for relative in _OPENCC_INVENTORY:
        digest, size_bytes = measure_regular_file(package_root / relative)
        inventory_values.append(
            LexicalEvaluatorInventoryItemV1(
                path=relative,
                sha256=digest,
                size_bytes=size_bytes,
            )
        )
    inventory = tuple(inventory_values)
    evaluator_code_hash, _ = measure_regular_file(__file__)
    payload = {
        "schema_version": 1,
        "normalization_profile": _LEXICAL_NORMALIZATION_PROFILE,
        "implementation": _OPENCC_DISTRIBUTION,
        "implementation_version": importlib.metadata.version(_OPENCC_DISTRIBUTION),
        "conversion_config": "s2tw",
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "python_cache_tag": sys.implementation.cache_tag,
        "unicode_database_version": unicodedata.unidata_version,
        "inventory": inventory,
        "inventory_hash": hash_object(inventory),
        "evaluator_code_hash": evaluator_code_hash,
    }
    return LexicalEvaluatorIdentityV1(
        **payload,
        identity_hash=_prospective_hash(payload, kind="lexical_evaluator_identity"),
    )


def _lexical_character_units(value: str) -> tuple[str, ...]:
    """Deterministic presentation-insensitive units for content accuracy.

    NFKC and case-folding cover compatibility width and case.  OpenCC maps
    Traditional and Simplified Han to a shared comparison script.  Unicode
    punctuation, separators, and controls are presentation-only for this
    metric; combining marks and symbols remain content-bearing.
    """

    normalized = unicodedata.normalize("NFKC", value).casefold()
    script_neutral = _lexical_script_converter().convert(normalized)
    normalized = unicodedata.normalize("NFKC", script_neutral).casefold()
    return tuple(
        character
        for character in normalized
        if not unicodedata.category(character).startswith(("P", "Z", "C"))
    )


def _semantic_search_text(value: str) -> str:
    """Normalize width/script/case while preserving semantic punctuation.

    This is deliberately stricter than lexical CER.  Decimal points, signs,
    apostrophes, hyphens, and symbols can change a numeric or code-switched
    literal, so label/critical-omission matching must not erase them.
    """

    normalized = unicodedata.normalize("NFKC", value).casefold()
    script_neutral = _lexical_script_converter().convert(normalized)
    normalized = unicodedata.normalize("NFKC", script_neutral).casefold()
    return "".join(
        " " if unicodedata.category(character).startswith("Z") else character
        for character in normalized
        if not unicodedata.category(character).startswith("C")
    )


def _lexical_label_search_text(value: str) -> str:
    """Normalize entity labels without joining across punctuation boundaries."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    script_neutral = _lexical_script_converter().convert(normalized)
    normalized = unicodedata.normalize("NFKC", script_neutral).casefold()
    return "".join(
        " "
        if unicodedata.category(character).startswith(("P", "Z", "C"))
        else character
        for character in normalized
    )


def _ordered_clip_bindings(
    clips: Sequence[AudioClipBinding],
    *,
    label: str,
) -> tuple[AudioClipBinding, ...]:
    result = tuple(clips)
    identifiers = [clip.clip_id for clip in result]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError(f"{label} contains duplicate clip_id")
    expected = tuple(sorted(result, key=lambda item: (item.start_ms, item.end_ms, item.clip_id)))
    if result != expected:
        raise ValueError(f"{label} clips must be in chronological canonical order")
    for left, right in zip(result, result[1:]):
        if left.end_ms > right.start_ms:
            raise ValueError(f"{label} clips must not overlap")
    return result


class AudioClipBinding(_StrictFrozenModel):
    """Exact byte and interval identity for one independently sampled clip."""

    schema_version: Literal[1] = 1
    clip_id: str
    start_ms: int
    end_ms: int
    clip_audio_hash: str
    clip_audio_size_bytes: int
    normalized_audio_hash: str
    normalized_audio_size_bytes: int
    binding_hash: str

    @field_validator("clip_id")
    @classmethod
    def _clip_id(cls, value: str) -> str:
        return _require_nonempty("clip_id", value)

    @field_validator("clip_audio_hash", "normalized_audio_hash", "binding_hash")
    @classmethod
    def _hashes(cls, value: str, info: Any) -> str:
        return _require_sha256(info.field_name, value)

    @model_validator(mode="after")
    def _valid(self) -> AudioClipBinding:
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("audio clip binding has invalid interval")
        if self.clip_audio_size_bytes < 1 or self.normalized_audio_size_bytes < 1:
            raise ValueError("audio clip binding sizes must be positive")
        if self.binding_hash != _artifact_hash(
            self, field="binding_hash", kind="transcript_audio_clip_binding"
        ):
            raise ValueError("audio clip binding_hash mismatch")
        return self

    @classmethod
    def build(
        cls,
        *,
        clip_id: str,
        start_ms: int,
        end_ms: int,
        clip_audio_hash: str,
        clip_audio_size_bytes: int,
        normalized_audio_hash: str,
        normalized_audio_size_bytes: int,
    ) -> AudioClipBinding:
        payload = {
            "schema_version": 1,
            "clip_id": clip_id,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "clip_audio_hash": clip_audio_hash,
            "clip_audio_size_bytes": clip_audio_size_bytes,
            "normalized_audio_hash": normalized_audio_hash,
            "normalized_audio_size_bytes": normalized_audio_size_bytes,
        }
        return cls(
            **payload,
            binding_hash=_prospective_hash(payload, kind="transcript_audio_clip_binding"),
        )


class TranscriptAnnotationProtocol(_StrictFrozenModel):
    """Frozen rules for collecting gold without exposing system output."""

    schema_version: Literal[1] = 1
    protocol_id: str
    first_pass_annotator_count: Literal[2] = 2
    first_pass_audio_only: Literal[True] = True
    independent_first_passes: Literal[True] = True
    third_person_adjudication_required: Literal[True] = True
    candidate_outputs_hidden: Literal[True] = True
    qc_outputs_hidden: Literal[True] = True
    glossary_hidden_during_first_pass: Literal[True] = True
    adjudication_reference_scope: Literal["spelling_only"] = "spelling_only"
    unresolved_audio_policy: Literal["needs_review"] = "needs_review"

    @field_validator("protocol_id")
    @classmethod
    def _protocol_id(cls, value: str) -> str:
        return _require_nonempty("protocol_id", value)


class TranscriptAnnotationPacket(_StrictFrozenModel):
    """Candidate-free audio packet issued to human annotators."""

    _HASH_KIND: ClassVar[str] = "transcript_annotation_packet"

    schema_version: Literal[1] = 1
    packet_id: str
    episode_id: str
    normalized_audio_hash: str
    normalized_audio_size_bytes: int
    sampling_declaration_hash: str | None
    instruction_profile_id: str
    protocol: TranscriptAnnotationProtocol
    clips: tuple[AudioClipBinding, ...]
    packet_hash: str

    @field_validator("packet_id", "episode_id", "instruction_profile_id")
    @classmethod
    def _identifiers(cls, value: str, info: Any) -> str:
        return _require_nonempty(info.field_name, value)

    @field_validator("normalized_audio_hash", "packet_hash")
    @classmethod
    def _hashes(cls, value: str, info: Any) -> str:
        return _require_sha256(info.field_name, value)

    @field_validator("sampling_declaration_hash")
    @classmethod
    def _sampling_declaration_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_sha256("sampling_declaration_hash", value)

    @model_validator(mode="after")
    def _valid(self) -> TranscriptAnnotationPacket:
        if self.normalized_audio_size_bytes < 1:
            raise ValueError("normalized_audio_size_bytes must be positive")
        clips = _ordered_clip_bindings(self.clips, label="annotation packet")
        if not clips:
            raise ValueError("annotation packet requires at least one clip")
        for clip in clips:
            if (
                clip.normalized_audio_hash != self.normalized_audio_hash
                or clip.normalized_audio_size_bytes != self.normalized_audio_size_bytes
            ):
                raise ValueError("annotation packet clip normalized-audio lineage mismatch")
        if self.packet_hash != _artifact_hash(
            self, field="packet_hash", kind=self._HASH_KIND
        ):
            raise ValueError("annotation packet_hash mismatch")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)

    @classmethod
    def build(
        cls,
        *,
        packet_id: str,
        episode_id: str,
        normalized_audio_hash: str,
        normalized_audio_size_bytes: int,
        sampling_declaration_hash: str | None = None,
        instruction_profile_id: str,
        protocol: TranscriptAnnotationProtocol,
        clips: Sequence[AudioClipBinding],
        forbidden_candidate_hashes: Iterable[str] = (),
    ) -> TranscriptAnnotationPacket:
        payload = {
            "schema_version": 1,
            "packet_id": packet_id,
            "episode_id": episode_id,
            "normalized_audio_hash": normalized_audio_hash,
            "normalized_audio_size_bytes": normalized_audio_size_bytes,
            "sampling_declaration_hash": sampling_declaration_hash,
            "instruction_profile_id": instruction_profile_id,
            "protocol": protocol,
            "clips": tuple(clips),
        }
        _assert_no_candidate_leakage(
            payload,
            forbidden_candidate_hashes,
            label="annotation packet",
        )
        return cls(
            **payload,
            packet_hash=_prospective_hash(payload, kind=cls._HASH_KIND),
        )


class SpellingAuthoritySource(_StrictFrozenModel):
    """Reference provenance usable only to settle spelling after listening."""

    source_id: str
    artifact_hash: str
    authority_scope: Literal["spelling_only"] = "spelling_only"

    @field_validator("source_id")
    @classmethod
    def _source_id(cls, value: str) -> str:
        return _require_nonempty("source_id", value)

    @field_validator("artifact_hash")
    @classmethod
    def _artifact_hash(cls, value: str) -> str:
        return _require_sha256("artifact_hash", value)


AudioOnlyOutcome = Literal["accepted", "needs_review"]


class AudioOnlyTranscriptSubmission(_StrictFrozenModel):
    """One exact first-pass result; its schema has no reference/system fields."""

    _HASH_KIND: ClassVar[str] = "audio_only_transcript_submission"

    schema_version: Literal[1] = 1
    submission_id: str
    annotation_packet_hash: str
    clip: AudioClipBinding
    annotator_id: str
    outcome: AudioOnlyOutcome
    text: str | None
    tokens: tuple[str, ...]
    audio_only: Literal[True] = True
    candidate_outputs_hidden: Literal[True] = True
    qc_outputs_hidden: Literal[True] = True
    reference_material_hidden: Literal[True] = True
    submission_hash: str

    @field_validator("submission_id", "annotator_id")
    @classmethod
    def _identifiers(cls, value: str, info: Any) -> str:
        return _require_nonempty(info.field_name, value)

    @field_validator("annotation_packet_hash", "submission_hash")
    @classmethod
    def _hashes(cls, value: str, info: Any) -> str:
        return _require_sha256(info.field_name, value)

    @field_validator("tokens")
    @classmethod
    def _tokens(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item for item in values):
            raise ValueError("audio-only submission tokens must be non-empty")
        return values

    @model_validator(mode="after")
    def _valid(self) -> AudioOnlyTranscriptSubmission:
        if self.outcome == "accepted":
            if self.text is None or not self.tokens:
                raise ValueError("accepted audio-only submission requires text and tokens")
            if "".join(self.tokens) != self.text:
                raise ValueError("audio-only submission text drifts from tokens")
        elif self.text is not None or self.tokens:
            raise ValueError("needs_review audio-only submission cannot assert spoken text")
        if self.submission_hash != _artifact_hash(
            self, field="submission_hash", kind=self._HASH_KIND
        ):
            raise ValueError("audio-only submission_hash mismatch")
        return self

    @classmethod
    def build(
        cls,
        *,
        submission_id: str,
        annotation_packet_hash: str,
        clip: AudioClipBinding,
        annotator_id: str,
        outcome: AudioOnlyOutcome,
        text: str | None,
        tokens: Sequence[str],
    ) -> AudioOnlyTranscriptSubmission:
        payload = {
            "schema_version": 1,
            "submission_id": submission_id,
            "annotation_packet_hash": annotation_packet_hash,
            "clip": clip,
            "annotator_id": annotator_id,
            "outcome": outcome,
            "text": text,
            "tokens": tuple(tokens),
            "audio_only": True,
            "candidate_outputs_hidden": True,
            "qc_outputs_hidden": True,
            "reference_material_hidden": True,
        }
        return cls(
            **payload,
            submission_hash=_prospective_hash(payload, kind=cls._HASH_KIND),
        )


class GoldToken(_StrictFrozenModel):
    token_id: str
    text: str

    @field_validator("token_id", "text")
    @classmethod
    def _nonempty(cls, value: str, info: Any) -> str:
        return _require_nonempty(info.field_name, value)


GoldSpanKind = Literal["entity", "code_switch", "numeric"]


class GoldSpanLabel(_StrictFrozenModel):
    label_id: str
    kind: GoldSpanKind
    token_ids: tuple[str, ...]
    expected_text: str

    @field_validator("label_id", "expected_text")
    @classmethod
    def _nonempty(cls, value: str, info: Any) -> str:
        return _require_nonempty(info.field_name, value)

    @field_validator("token_ids")
    @classmethod
    def _token_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        result = _require_unique_nonempty("gold span token_ids", values)
        if not result:
            raise ValueError("gold span requires token_ids")
        return result


class GoldOmissionLabel(_StrictFrozenModel):
    label_id: str
    token_ids: tuple[str, ...]
    expected_text: str
    severity: Literal["material", "critical"] = "material"

    @field_validator("label_id", "expected_text")
    @classmethod
    def _nonempty(cls, value: str, info: Any) -> str:
        return _require_nonempty(info.field_name, value)

    @field_validator("token_ids")
    @classmethod
    def _token_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        result = _require_unique_nonempty("omission label token_ids", values)
        if not result:
            raise ValueError("omission label requires token_ids")
        return result


CorrectionGoldOutcome = Literal["accepted", "needs_review"]


class CorrectionGoldLabel(_StrictFrozenModel):
    """Human truth for a preselected audio subspan.

    ``label_id`` belongs only to human gold.  A candidate never receives it;
    matching is by the exact ``(target_start_ms, target_end_ms)`` binding.
    """

    label_id: str
    target_start_ms: int
    target_end_ms: int
    token_ids: tuple[str, ...]
    expected_outcome: CorrectionGoldOutcome
    expected_text: str | None
    authorized_spelling_source_ids: tuple[str, ...] = ()

    @field_validator("label_id")
    @classmethod
    def _nonempty(cls, value: str, info: Any) -> str:
        return _require_nonempty(info.field_name, value)

    @field_validator("token_ids")
    @classmethod
    def _token_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _require_unique_nonempty("correction gold token_ids", values)

    @field_validator("authorized_spelling_source_ids")
    @classmethod
    def _source_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _require_unique_nonempty("authorized_spelling_source_ids", values)

    @model_validator(mode="after")
    def _valid(self) -> CorrectionGoldLabel:
        if self.target_start_ms < 0 or self.target_end_ms <= self.target_start_ms:
            raise ValueError("correction gold target has invalid interval")
        if self.expected_outcome == "accepted":
            if not self.expected_text or not self.token_ids:
                raise ValueError("accepted correction gold requires text and token_ids")
        elif self.expected_text is not None or self.token_ids:
            raise ValueError("needs_review correction gold cannot assert spoken text")
        return self


GoldClipOutcome = Literal["accepted", "needs_review"]


class SpellingAuthorityUse(_StrictFrozenModel):
    source_id: str
    locator: str

    @field_validator("source_id", "locator")
    @classmethod
    def _nonempty(cls, value: str, info: Any) -> str:
        return _require_nonempty(info.field_name, value)


class GoldMetricCoverageV1(_StrictFrozenModel):
    """Third-person attestation that a clip was exhaustively labelled by kind."""

    entity_labels_exhaustive: bool = False
    code_switch_labels_exhaustive: bool = False
    numeric_labels_exhaustive: bool = False
    critical_omission_labels_exhaustive: bool = False


class TranscriptAdjudicationRecord(_StrictFrozenModel):
    """Third-person adjudication over two exact audio-only submissions."""

    _HASH_KIND: ClassVar[str] = "transcript_adjudication_record"

    schema_version: Literal[1] = 1
    record_id: str
    annotation_packet_hash: str
    clip: AudioClipBinding
    first_pass_submission_hashes: tuple[str, str]
    adjudicator_id: str
    spelling_authority_uses: tuple[SpellingAuthorityUse, ...]
    candidate_outputs_hidden: Literal[True] = True
    qc_outputs_hidden: Literal[True] = True
    reference_scope: Literal["spelling_only"] = "spelling_only"
    metric_coverage: GoldMetricCoverageV1 = GoldMetricCoverageV1()
    final_expected_outcome: GoldClipOutcome
    final_text: str | None
    final_tokens: tuple[GoldToken, ...]
    final_span_labels: tuple[GoldSpanLabel, ...] = ()
    final_omission_labels: tuple[GoldOmissionLabel, ...] = ()
    final_correction_labels: tuple[CorrectionGoldLabel, ...] = ()
    record_hash: str

    @field_validator("record_id", "adjudicator_id")
    @classmethod
    def _identifiers(cls, value: str, info: Any) -> str:
        return _require_nonempty(info.field_name, value)

    @field_validator("annotation_packet_hash", "record_hash")
    @classmethod
    def _hashes(cls, value: str, info: Any) -> str:
        return _require_sha256(info.field_name, value)

    @field_validator("first_pass_submission_hashes")
    @classmethod
    def _submission_hashes(cls, values: tuple[str, str]) -> tuple[str, str]:
        for value in values:
            _require_sha256("first_pass_submission_hash", value)
        if len(set(values)) != 2:
            raise ValueError("adjudication requires two distinct first-pass submissions")
        return values

    @model_validator(mode="after")
    def _valid(self) -> TranscriptAdjudicationRecord:
        if self.final_expected_outcome == "accepted":
            if self.final_text is None or not self.final_tokens:
                raise ValueError("accepted adjudication requires final text and tokens")
            if "".join(token.text for token in self.final_tokens) != self.final_text:
                raise ValueError("adjudication final_text drifts from final_tokens")
        elif (
            self.final_text is not None
            or self.final_tokens
            or self.final_span_labels
            or self.final_omission_labels
        ):
            raise ValueError("needs_review adjudication cannot assert spoken text")
        if self.final_expected_outcome == "needs_review" and any(
            self.metric_coverage.model_dump().values()
        ):
            raise ValueError("needs_review adjudication cannot attest text-label coverage")
        if self.record_hash != _artifact_hash(self, field="record_hash", kind=self._HASH_KIND):
            raise ValueError("transcript adjudication record_hash mismatch")
        return self

    @classmethod
    def build(
        cls,
        *,
        record_id: str,
        annotation_packet_hash: str,
        clip: AudioClipBinding,
        first_pass_submission_hashes: tuple[str, str],
        adjudicator_id: str,
        spelling_authority_uses: Sequence[SpellingAuthorityUse],
        metric_coverage: GoldMetricCoverageV1 | None = None,
        final_expected_outcome: GoldClipOutcome,
        final_text: str | None,
        final_tokens: Sequence[GoldToken],
        final_span_labels: Sequence[GoldSpanLabel] = (),
        final_omission_labels: Sequence[GoldOmissionLabel] = (),
        final_correction_labels: Sequence[CorrectionGoldLabel] = (),
    ) -> TranscriptAdjudicationRecord:
        payload = {
            "schema_version": 1,
            "record_id": record_id,
            "annotation_packet_hash": annotation_packet_hash,
            "clip": clip,
            "first_pass_submission_hashes": first_pass_submission_hashes,
            "adjudicator_id": adjudicator_id,
            "spelling_authority_uses": tuple(spelling_authority_uses),
            "candidate_outputs_hidden": True,
            "qc_outputs_hidden": True,
            "reference_scope": "spelling_only",
            "metric_coverage": metric_coverage or GoldMetricCoverageV1(),
            "final_expected_outcome": final_expected_outcome,
            "final_text": final_text,
            "final_tokens": tuple(final_tokens),
            "final_span_labels": tuple(final_span_labels),
            "final_omission_labels": tuple(final_omission_labels),
            "final_correction_labels": tuple(final_correction_labels),
        }
        return cls(
            **payload,
            record_hash=_prospective_hash(payload, kind=cls._HASH_KIND),
        )


class AdjudicationProvenance(_StrictFrozenModel):
    """Verifiable exact artifacts, never a caller's self-attested hash list."""

    first_pass_submissions: tuple[
        AudioOnlyTranscriptSubmission, AudioOnlyTranscriptSubmission
    ]
    adjudication_record: TranscriptAdjudicationRecord | None

    @model_validator(mode="after")
    def _valid(self) -> AdjudicationProvenance:
        submissions = self.first_pass_submissions
        annotators = tuple(item.annotator_id for item in submissions)
        if len(set(annotators)) != 2:
            raise ValueError("exact audio-only submission artifacts need distinct annotators")
        if annotators != tuple(sorted(annotators)):
            raise ValueError("audio-only submissions must use canonical annotator order")
        if submissions[0].submission_hash == submissions[1].submission_hash:
            raise ValueError("exact audio-only submission artifacts must be distinct")
        if (
            submissions[0].annotation_packet_hash != submissions[1].annotation_packet_hash
            or submissions[0].clip != submissions[1].clip
        ):
            raise ValueError("audio-only submissions differ in packet or clip binding")
        record = self.adjudication_record
        if record is not None:
            if record.adjudicator_id in annotators:
                raise ValueError("third adjudicator must differ from first-pass annotators")
            if (
                record.annotation_packet_hash != submissions[0].annotation_packet_hash
                or record.clip != submissions[0].clip
                or record.first_pass_submission_hashes
                != tuple(item.submission_hash for item in submissions)
            ):
                raise ValueError("adjudication record does not bind exact first-pass artifacts")
        return self

    @property
    def completed(self) -> bool:
        return self.adjudication_record is not None

    @property
    def first_pass_annotator_ids(self) -> tuple[str, str]:
        return tuple(item.annotator_id for item in self.first_pass_submissions)  # type: ignore[return-value]

    @property
    def first_pass_submission_hashes(self) -> tuple[str, str]:
        return tuple(item.submission_hash for item in self.first_pass_submissions)  # type: ignore[return-value]

    @property
    def adjudicator_id(self) -> str | None:
        record = self.adjudication_record
        return None if record is None else record.adjudicator_id

    @property
    def adjudication_record_hash(self) -> str | None:
        record = self.adjudication_record
        return None if record is None else record.record_hash


class GoldClipLabel(_StrictFrozenModel):
    clip_id: str
    expected_outcome: GoldClipOutcome
    text: str | None
    tokens: tuple[GoldToken, ...]
    span_labels: tuple[GoldSpanLabel, ...] = ()
    omission_labels: tuple[GoldOmissionLabel, ...] = ()
    correction_labels: tuple[CorrectionGoldLabel, ...] = ()
    provenance: AdjudicationProvenance

    @field_validator("clip_id")
    @classmethod
    def _clip_id(cls, value: str) -> str:
        return _require_nonempty("clip_id", value)

    @model_validator(mode="after")
    def _valid(self) -> GoldClipLabel:
        if self.expected_outcome == "accepted" and self.text is None:
            raise ValueError("accepted gold clip requires adjudicated text")
        if self.text is None:
            if self.tokens or self.span_labels or self.omission_labels:
                raise ValueError("gold without adjudicated text cannot carry text-derived labels")
        else:
            if not self.tokens:
                raise ValueError("adjudicated gold text requires token labels")
            token_ids = [token.token_id for token in self.tokens]
            if len(set(token_ids)) != len(token_ids):
                raise ValueError("gold token_id must be unique within a clip")
            if "".join(token.text for token in self.tokens) != self.text:
                raise ValueError("gold text must equal the exact concatenation of gold tokens")
            token_by_id = {token.token_id: token for token in self.tokens}
            order = {token.token_id: index for index, token in enumerate(self.tokens)}
            labels: tuple[GoldSpanLabel | GoldOmissionLabel, ...] = (
                *self.span_labels,
                *self.omission_labels,
            )
            label_ids = [label.label_id for label in labels]
            if len(set(label_ids)) != len(label_ids):
                raise ValueError("gold text-derived label_id must be unique within a clip")
            for label in labels:
                unknown = set(label.token_ids) - set(token_by_id)
                if unknown:
                    raise ValueError(f"gold label cites unknown token_ids: {sorted(unknown)}")
                positions = [order[token_id] for token_id in label.token_ids]
                if positions != list(range(positions[0], positions[-1] + 1)):
                    raise ValueError("gold label token_ids must form one ordered contiguous span")
                expected = "".join(token_by_id[token_id].text for token_id in label.token_ids)
                if expected != label.expected_text:
                    raise ValueError("gold label expected_text drifts from cited gold tokens")
            for correction in self.correction_labels:
                if correction.expected_outcome != "accepted":
                    continue
                unknown = set(correction.token_ids) - set(token_by_id)
                if unknown:
                    raise ValueError(
                        f"correction gold cites unknown token_ids: {sorted(unknown)}"
                    )
                positions = [order[token_id] for token_id in correction.token_ids]
                if positions != list(range(positions[0], positions[-1] + 1)):
                    raise ValueError(
                        "correction gold token_ids must form one ordered contiguous span"
                    )
                expected = "".join(
                    token_by_id[token_id].text for token_id in correction.token_ids
                )
                if expected != correction.expected_text:
                    raise ValueError(
                        "correction gold expected_text drifts from cited gold tokens"
                    )
        correction_ids = [item.label_id for item in self.correction_labels]
        if len(set(correction_ids)) != len(correction_ids):
            raise ValueError("gold correction label_id must be unique within a clip")
        correction_targets = [
            (item.target_start_ms, item.target_end_ms) for item in self.correction_labels
        ]
        if len(set(correction_targets)) != len(correction_targets):
            raise ValueError("gold correction target interval must be unique within a clip")
        ordered_targets = sorted(correction_targets)
        for left, right in zip(ordered_targets, ordered_targets[1:]):
            if left[1] > right[0]:
                raise ValueError("gold correction target intervals must not overlap")
        record = self.provenance.adjudication_record
        if record is not None and (
            record.final_expected_outcome != self.expected_outcome
            or record.final_text != self.text
            or record.final_tokens != self.tokens
            or record.final_span_labels != self.span_labels
            or record.final_omission_labels != self.omission_labels
            or record.final_correction_labels != self.correction_labels
        ):
            raise ValueError("gold label differs from exact adjudication final label")
        return self


class TranscriptGoldSuite(_StrictFrozenModel):
    """Closed or in-progress human gold, never derived from system output.

    ``correction_targets_revealed_at_utc`` is content-addressed ordering
    metadata.  By itself it is not proof of external custody or a trusted
    clock; release-grade use needs an immutable pre-reveal candidate seal.
    """

    _HASH_KIND: ClassVar[str] = "transcript_gold_suite"

    schema_version: Literal[1] = 1
    suite_id: str
    episode_id: str
    normalized_audio_hash: str
    normalized_audio_size_bytes: int
    annotation_packet_hash: str
    expected_clip_count: int
    complete: bool
    protocol: TranscriptAnnotationProtocol
    clips: tuple[AudioClipBinding, ...]
    spelling_sources: tuple[SpellingAuthoritySource, ...]
    labels: tuple[GoldClipLabel, ...]
    correction_targets_revealed_at_utc: str | None
    suite_hash: str

    @field_validator("suite_id", "episode_id")
    @classmethod
    def _identifiers(cls, value: str, info: Any) -> str:
        return _require_nonempty(info.field_name, value)

    @field_validator("normalized_audio_hash", "annotation_packet_hash", "suite_hash")
    @classmethod
    def _hashes(cls, value: str, info: Any) -> str:
        return _require_sha256(info.field_name, value)

    @model_validator(mode="after")
    def _valid(self) -> TranscriptGoldSuite:
        if self.normalized_audio_size_bytes < 1:
            raise ValueError("normalized_audio_size_bytes must be positive")
        clips = _ordered_clip_bindings(self.clips, label="gold suite")
        if self.expected_clip_count != len(clips) or self.expected_clip_count < 1:
            raise ValueError("gold expected_clip_count must equal the planned clip count")
        for clip in clips:
            if (
                clip.normalized_audio_hash != self.normalized_audio_hash
                or clip.normalized_audio_size_bytes != self.normalized_audio_size_bytes
            ):
                raise ValueError("gold clip normalized-audio lineage mismatch")
        source_ids = [source.source_id for source in self.spelling_sources]
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("gold spelling source_id must be unique")
        clip_order = {clip.clip_id: index for index, clip in enumerate(clips)}
        label_ids = [label.clip_id for label in self.labels]
        if len(set(label_ids)) != len(label_ids):
            raise ValueError("gold suite contains duplicate clip labels")
        unknown = set(label_ids) - set(clip_order)
        if unknown:
            raise ValueError(f"gold labels cite unknown clips: {sorted(unknown)}")
        label_positions = [clip_order[item] for item in label_ids]
        if label_positions != sorted(label_positions):
            raise ValueError("gold labels must follow planned clip order")
        known_sources = set(source_ids)
        for label in self.labels:
            clip = clips[clip_order[label.clip_id]]
            for submission in label.provenance.first_pass_submissions:
                if submission.annotation_packet_hash != self.annotation_packet_hash:
                    raise ValueError("first-pass submission annotation packet mismatch")
                if submission.clip != clip:
                    raise ValueError("first-pass submission clip binding mismatch")
            record = label.provenance.adjudication_record
            if record is not None:
                if record.annotation_packet_hash != self.annotation_packet_hash:
                    raise ValueError("adjudication record annotation packet mismatch")
                if record.clip != clip:
                    raise ValueError("adjudication record clip binding mismatch")
                unknown_uses = {
                    use.source_id for use in record.spelling_authority_uses
                } - known_sources
                if unknown_uses:
                    raise ValueError(
                        "adjudication cites unknown spelling sources: "
                        f"{sorted(unknown_uses)}"
                    )
            for correction in label.correction_labels:
                if (
                    correction.target_start_ms < clip.start_ms
                    or correction.target_end_ms > clip.end_ms
                ):
                    raise ValueError("gold correction target escapes its audio clip")
                unknown_sources = set(correction.authorized_spelling_source_ids) - known_sources
                if unknown_sources:
                    raise ValueError(
                        "gold correction cites unknown spelling sources: "
                        f"{sorted(unknown_sources)}"
                    )
        if self.complete:
            if len(self.labels) != self.expected_clip_count:
                raise ValueError("complete gold must label every planned clip")
            if any(not label.provenance.completed for label in self.labels):
                raise ValueError("complete gold requires completed third-person adjudication")
        if self.correction_targets_revealed_at_utc is not None:
            _parse_utc(
                "correction_targets_revealed_at_utc",
                self.correction_targets_revealed_at_utc,
            )
        if self.suite_hash != _artifact_hash(self, field="suite_hash", kind=self._HASH_KIND):
            raise ValueError("transcript gold suite_hash mismatch")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)

    @classmethod
    def build(
        cls,
        *,
        suite_id: str,
        annotation_packet: TranscriptAnnotationPacket,
        complete: bool,
        spelling_sources: Sequence[SpellingAuthoritySource] = (),
        labels: Sequence[GoldClipLabel] = (),
        correction_targets_revealed_at_utc: str | None = None,
        forbidden_candidate_hashes: Iterable[str] = (),
    ) -> TranscriptGoldSuite:
        verify_annotation_packet_blinding(annotation_packet, forbidden_candidate_hashes)
        payload = {
            "schema_version": 1,
            "suite_id": suite_id,
            "episode_id": annotation_packet.episode_id,
            "normalized_audio_hash": annotation_packet.normalized_audio_hash,
            "normalized_audio_size_bytes": annotation_packet.normalized_audio_size_bytes,
            "annotation_packet_hash": annotation_packet.packet_hash,
            "expected_clip_count": len(annotation_packet.clips),
            "complete": complete,
            "protocol": annotation_packet.protocol,
            "clips": annotation_packet.clips,
            "spelling_sources": tuple(spelling_sources),
            "labels": tuple(labels),
            "correction_targets_revealed_at_utc": correction_targets_revealed_at_utc,
        }
        _assert_no_candidate_leakage(payload, forbidden_candidate_hashes, label="gold suite")
        return cls(
            **payload,
            suite_hash=_prospective_hash(payload, kind=cls._HASH_KIND),
        )


CandidateCorrectionAction = Literal[
    "apply", "keep_original", "needs_review", "not_detected"
]


class CandidateCorrectionDecision(_StrictFrozenModel):
    """Candidate-owned before/after trace for one exact audio subspan."""

    decision_id: str
    target_start_ms: int
    target_end_ms: int
    action: CandidateCorrectionAction
    recognition_text: str
    final_text: str
    source_artifact_ids: tuple[str, ...] = ()

    @field_validator("decision_id")
    @classmethod
    def _nonempty(cls, value: str, info: Any) -> str:
        return _require_nonempty(info.field_name, value)

    @field_validator("source_artifact_ids")
    @classmethod
    def _source_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _require_unique_nonempty("candidate source_artifact_ids", values)

    @model_validator(mode="after")
    def _valid(self) -> CandidateCorrectionDecision:
        if self.target_start_ms < 0 or self.target_end_ms <= self.target_start_ms:
            raise ValueError("candidate correction target has invalid interval")
        if not self.recognition_text and not self.final_text:
            raise ValueError("candidate correction trace cannot have two empty text states")
        if self.action == "apply":
            if self.final_text == self.recognition_text:
                raise ValueError("candidate apply requires final_text to differ from recognition")
        elif self.final_text != self.recognition_text:
            raise ValueError("non-apply candidate trace must preserve recognition_text")
        if self.action == "not_detected" and self.source_artifact_ids:
            raise ValueError("not_detected trace cannot cite correction sources")
        return self


CandidateClipOutcome = Literal["accepted", "needs_review", "rejected"]


class CandidateClipOutput(_StrictFrozenModel):
    clip: AudioClipBinding
    outcome: CandidateClipOutcome
    text: str | None
    tokens: tuple[str, ...]
    corrections: tuple[CandidateCorrectionDecision, ...] = ()
    transcript_text_hash: str

    @field_validator("transcript_text_hash")
    @classmethod
    def _text_hash(cls, value: str) -> str:
        return _require_sha256("transcript_text_hash", value)

    @field_validator("tokens")
    @classmethod
    def _tokens(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value for value in values):
            raise ValueError("candidate tokens must be non-empty strings")
        return values

    @model_validator(mode="after")
    def _valid(self) -> CandidateClipOutput:
        if self.outcome == "accepted" and self.text is None:
            raise ValueError("accepted candidate clip requires text")
        if self.text is None:
            if self.tokens:
                raise ValueError("candidate without text must not carry tokens")
        elif "".join(self.tokens) != self.text:
            raise ValueError("candidate text drifts from exact token concatenation")
        correction_ids = [item.decision_id for item in self.corrections]
        if len(set(correction_ids)) != len(correction_ids):
            raise ValueError("candidate decision_id must be unique within a clip")
        correction_targets = [
            (item.target_start_ms, item.target_end_ms) for item in self.corrections
        ]
        if len(set(correction_targets)) != len(correction_targets):
            raise ValueError("candidate correction target interval must be unique within a clip")
        ordered_targets = sorted(correction_targets)
        for left, right in zip(ordered_targets, ordered_targets[1:]):
            if left[1] > right[0]:
                raise ValueError("candidate correction target intervals must not overlap")
        if any(
            item.target_start_ms < self.clip.start_ms
            or item.target_end_ms > self.clip.end_ms
            for item in self.corrections
        ):
            raise ValueError("candidate correction target escapes its audio clip")
        expected_hash = hash_object({"text": self.text})
        if self.transcript_text_hash != expected_hash:
            raise ValueError("candidate transcript_text_hash mismatch")
        return self

    @classmethod
    def build(
        cls,
        *,
        clip: AudioClipBinding,
        outcome: CandidateClipOutcome,
        text: str | None,
        tokens: Sequence[str],
        corrections: Sequence[CandidateCorrectionDecision] = (),
    ) -> CandidateClipOutput:
        return cls(
            clip=clip,
            outcome=outcome,
            text=text,
            tokens=tuple(tokens),
            corrections=tuple(corrections),
            transcript_text_hash=hash_object({"text": text}),
        )


class TranscriptCandidateArtifactV1(_StrictFrozenModel):
    """Legacy sparse-decision candidate retained for exact replay only.

    The packet carries no evaluation target spans.  ``generated_at_utc`` is a
    content-addressed claim, not proof that a production pipeline was denied
    access to targets; a later custody workflow must establish that fact.

    V1 has no complete timed recognition-before/final-after evidence.  It may
    still be replayed for transcript metrics, but correction metrics must stay
    typed ``not_evaluated``.
    """

    _HASH_KIND: ClassVar[str] = "transcript_candidate_artifact"

    schema_version: Literal[1] = 1
    candidate_id: str
    system_id: str
    generation_id: str
    episode_id: str
    normalized_audio_hash: str
    normalized_audio_size_bytes: int
    annotation_packet_hash: str
    generated_at_utc: str
    expected_clip_count: int
    complete: bool
    clips: tuple[CandidateClipOutput, ...]
    artifact_hash: str

    @field_validator(
        "candidate_id", "system_id", "generation_id", "episode_id", "generated_at_utc"
    )
    @classmethod
    def _identifiers(cls, value: str, info: Any) -> str:
        return _require_nonempty(info.field_name, value)

    @field_validator("normalized_audio_hash", "annotation_packet_hash", "artifact_hash")
    @classmethod
    def _hashes(cls, value: str, info: Any) -> str:
        return _require_sha256(info.field_name, value)

    @model_validator(mode="after")
    def _valid(self) -> TranscriptCandidateArtifactV1:
        _parse_utc("generated_at_utc", self.generated_at_utc)
        if self.normalized_audio_size_bytes < 1 or self.expected_clip_count < 1:
            raise ValueError("candidate audio size and expected_clip_count must be positive")
        bindings = _ordered_clip_bindings(
            tuple(item.clip for item in self.clips), label="candidate artifact"
        )
        for clip in bindings:
            if (
                clip.normalized_audio_hash != self.normalized_audio_hash
                or clip.normalized_audio_size_bytes != self.normalized_audio_size_bytes
            ):
                raise ValueError("candidate clip normalized-audio lineage mismatch")
        if len(self.clips) > self.expected_clip_count:
            raise ValueError("candidate output exceeds expected_clip_count")
        if self.complete and len(self.clips) != self.expected_clip_count:
            raise ValueError("complete candidate must cover every planned clip")
        if self.artifact_hash != _artifact_hash(
            self, field="artifact_hash", kind=self._HASH_KIND
        ):
            raise ValueError("transcript candidate artifact_hash mismatch")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)

    @classmethod
    def build(
        cls,
        *,
        candidate_id: str,
        system_id: str,
        generation_id: str,
        annotation_packet: TranscriptAnnotationPacket,
        generated_at_utc: str = "1970-01-01T00:00:00Z",
        complete: bool,
        clips: Sequence[CandidateClipOutput],
    ) -> TranscriptCandidateArtifactV1:
        payload = {
            "schema_version": 1,
            "candidate_id": candidate_id,
            "system_id": system_id,
            "generation_id": generation_id,
            "episode_id": annotation_packet.episode_id,
            "normalized_audio_hash": annotation_packet.normalized_audio_hash,
            "normalized_audio_size_bytes": annotation_packet.normalized_audio_size_bytes,
            "annotation_packet_hash": annotation_packet.packet_hash,
            "generated_at_utc": generated_at_utc,
            "expected_clip_count": len(annotation_packet.clips),
            "complete": complete,
            "clips": tuple(clips),
        }
        candidate = cls(
            **payload,
            artifact_hash=_prospective_hash(payload, kind=cls._HASH_KIND),
        )
        _validate_candidate_bindings(annotation_packet.clips, candidate.clips)
        return candidate


class CandidateTimedTextSpanV2(_StrictFrozenModel):
    """One target-independent atomic time span with immutable before/after text."""

    schema_version: Literal[2] = 2
    span_id: str
    start_ms: int
    end_ms: int
    recognition_text: str
    final_text: str

    @field_validator("span_id")
    @classmethod
    def _span_id(cls, value: str) -> str:
        return _require_nonempty("span_id", value)

    @model_validator(mode="after")
    def _valid(self) -> CandidateTimedTextSpanV2:
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("candidate timed span has invalid interval")
        return self


class CandidateTimedCoverageV2(_StrictFrozenModel):
    """Complete target-independent timed custody of one candidate clip.

    Spans use half-open intervals and must tile the clip exactly.  Empty text is
    valid for silence, deletion, or insertion evidence; the aggregate strings
    and content hash prevent either side of the trace from being rewritten.
    """

    _HASH_KIND: ClassVar[str] = "candidate_timed_coverage_v2"

    schema_version: Literal[2] = 2
    clip: AudioClipBinding
    coverage_scope: Literal["full_clip_target_independent"] = (
        "full_clip_target_independent"
    )
    complete: Literal[True] = True
    spans: tuple[CandidateTimedTextSpanV2, ...]
    recognition_text: str
    final_text: str
    coverage_hash: str

    @field_validator("coverage_hash")
    @classmethod
    def _coverage_hash(cls, value: str) -> str:
        return _require_sha256("coverage_hash", value)

    @model_validator(mode="after")
    def _valid(self) -> CandidateTimedCoverageV2:
        if not self.spans:
            raise ValueError("candidate timed coverage requires at least one span")
        identifiers = [span.span_id for span in self.spans]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("candidate timed span_id must be unique within a clip")
        expected_order = tuple(
            sorted(self.spans, key=lambda span: (span.start_ms, span.end_ms, span.span_id))
        )
        if self.spans != expected_order:
            raise ValueError("candidate timed spans must use canonical chronological order")
        cursor = self.clip.start_ms
        for span in self.spans:
            if span.start_ms != cursor:
                if span.start_ms < cursor:
                    raise ValueError("candidate timed coverage spans must not overlap")
                raise ValueError("candidate timed coverage must not contain gaps")
            if span.end_ms > self.clip.end_ms:
                raise ValueError("candidate timed coverage escapes its audio clip")
            cursor = span.end_ms
        if cursor != self.clip.end_ms:
            raise ValueError("candidate timed coverage must tile the full audio clip")
        if self.recognition_text != "".join(
            span.recognition_text for span in self.spans
        ):
            raise ValueError("candidate timed recognition_text drifts from its spans")
        if self.final_text != "".join(span.final_text for span in self.spans):
            raise ValueError("candidate timed final_text drifts from its spans")
        if self.coverage_hash != _artifact_hash(
            self, field="coverage_hash", kind=self._HASH_KIND
        ):
            raise ValueError("candidate timed coverage_hash mismatch")
        return self

    @classmethod
    def build(
        cls,
        *,
        clip: AudioClipBinding,
        spans: Sequence[CandidateTimedTextSpanV2],
    ) -> CandidateTimedCoverageV2:
        selected = tuple(spans)
        payload = {
            "schema_version": 2,
            "clip": clip,
            "coverage_scope": "full_clip_target_independent",
            "complete": True,
            "spans": selected,
            "recognition_text": "".join(span.recognition_text for span in selected),
            "final_text": "".join(span.final_text for span in selected),
        }
        return cls(
            **payload,
            coverage_hash=_prospective_hash(payload, kind=cls._HASH_KIND),
        )


def _project_timed_text(
    coverage: CandidateTimedCoverageV2,
    *,
    start_ms: int,
    end_ms: int,
) -> tuple[str, str, tuple[CandidateTimedTextSpanV2, ...]]:
    """Project one half-open interval by deterministic atomic-span overlap."""

    selected = tuple(
        span
        for span in coverage.spans
        if span.start_ms < end_ms and span.end_ms > start_ms
    )
    return (
        "".join(span.recognition_text for span in selected),
        "".join(span.final_text for span in selected),
        selected,
    )


class TranscriptCandidateArtifact(_StrictFrozenModel):
    """V2 candidate with optional, but integrity-checked, full timed evidence.

    Omitting timed coverage remains legal so callers can migrate transcript
    scoring independently.  Correction metrics then fail closed rather than
    inferring recognition state from sparse correction decisions.
    """

    _HASH_KIND: ClassVar[str] = "transcript_candidate_artifact"

    schema_version: Literal[2] = 2
    candidate_id: str
    system_id: str
    generation_id: str
    episode_id: str
    normalized_audio_hash: str
    normalized_audio_size_bytes: int
    annotation_packet_hash: str
    generated_at_utc: str
    expected_clip_count: int
    complete: bool
    clips: tuple[CandidateClipOutput, ...]
    timed_coverages: tuple[CandidateTimedCoverageV2, ...] = ()
    artifact_hash: str

    @field_validator(
        "candidate_id", "system_id", "generation_id", "episode_id", "generated_at_utc"
    )
    @classmethod
    def _identifiers(cls, value: str, info: Any) -> str:
        return _require_nonempty(info.field_name, value)

    @field_validator("normalized_audio_hash", "annotation_packet_hash", "artifact_hash")
    @classmethod
    def _hashes(cls, value: str, info: Any) -> str:
        return _require_sha256(info.field_name, value)

    @model_validator(mode="after")
    def _valid(self) -> TranscriptCandidateArtifact:
        _parse_utc("generated_at_utc", self.generated_at_utc)
        if self.normalized_audio_size_bytes < 1 or self.expected_clip_count < 1:
            raise ValueError("candidate audio size and expected_clip_count must be positive")
        bindings = _ordered_clip_bindings(
            tuple(item.clip for item in self.clips), label="candidate artifact"
        )
        for clip in bindings:
            if (
                clip.normalized_audio_hash != self.normalized_audio_hash
                or clip.normalized_audio_size_bytes != self.normalized_audio_size_bytes
            ):
                raise ValueError("candidate clip normalized-audio lineage mismatch")
        if len(self.clips) > self.expected_clip_count:
            raise ValueError("candidate output exceeds expected_clip_count")
        if self.complete and len(self.clips) != self.expected_clip_count:
            raise ValueError("complete candidate must cover every planned clip")

        output_by_id = {output.clip.clip_id: output for output in self.clips}
        output_order = {output.clip.clip_id: index for index, output in enumerate(self.clips)}
        coverage_ids = [coverage.clip.clip_id for coverage in self.timed_coverages]
        if len(set(coverage_ids)) != len(coverage_ids):
            raise ValueError("candidate timed coverage contains duplicate clip_id")
        unknown = set(coverage_ids) - set(output_by_id)
        if unknown:
            raise ValueError(f"candidate timed coverage cites unknown clips: {sorted(unknown)}")
        if [output_order[item] for item in coverage_ids] != sorted(
            output_order[item] for item in coverage_ids
        ):
            raise ValueError("candidate timed coverage must follow candidate clip order")

        for coverage in self.timed_coverages:
            output = output_by_id[coverage.clip.clip_id]
            if coverage.clip != output.clip:
                raise ValueError("candidate timed coverage clip binding drift")
            if output.text is None:
                raise ValueError("candidate timed coverage requires candidate output text")
            if coverage.final_text != output.text:
                raise ValueError("candidate timed final_text differs from candidate output text")
            self._validate_decision_projection(output, coverage)

        if self.artifact_hash != _artifact_hash(
            self, field="artifact_hash", kind=self._HASH_KIND
        ):
            raise ValueError("transcript candidate artifact_hash mismatch")
        return self

    @staticmethod
    def _validate_decision_projection(
        output: CandidateClipOutput,
        coverage: CandidateTimedCoverageV2,
    ) -> None:
        apply_by_span: dict[str, int] = {span.span_id: 0 for span in coverage.spans}
        for decision in output.corrections:
            recognition, final, selected = _project_timed_text(
                coverage,
                start_ms=decision.target_start_ms,
                end_ms=decision.target_end_ms,
            )
            if not selected:
                raise ValueError("candidate correction cannot project to timed coverage")
            if (
                selected[0].start_ms != decision.target_start_ms
                or selected[-1].end_ms != decision.target_end_ms
            ):
                raise ValueError("candidate correction target must align to timed span boundaries")
            if recognition != decision.recognition_text or final != decision.final_text:
                raise ValueError("candidate correction trace differs from timed coverage")
            if decision.action == "apply":
                changed = [
                    span for span in selected if span.recognition_text != span.final_text
                ]
                if not changed:
                    raise ValueError("candidate apply must cover a changed timed span")
                for span in changed:
                    apply_by_span[span.span_id] += 1

        for span in coverage.spans:
            if span.recognition_text == span.final_text:
                continue
            if apply_by_span[span.span_id] != 1:
                raise ValueError(
                    "every changed timed span requires exactly one candidate apply decision"
                )

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)

    @classmethod
    def build(
        cls,
        *,
        candidate_id: str,
        system_id: str,
        generation_id: str,
        annotation_packet: TranscriptAnnotationPacket,
        generated_at_utc: str = "1970-01-01T00:00:00Z",
        complete: bool,
        clips: Sequence[CandidateClipOutput],
        timed_coverages: Sequence[CandidateTimedCoverageV2] = (),
    ) -> TranscriptCandidateArtifact:
        payload = {
            "schema_version": 2,
            "candidate_id": candidate_id,
            "system_id": system_id,
            "generation_id": generation_id,
            "episode_id": annotation_packet.episode_id,
            "normalized_audio_hash": annotation_packet.normalized_audio_hash,
            "normalized_audio_size_bytes": annotation_packet.normalized_audio_size_bytes,
            "annotation_packet_hash": annotation_packet.packet_hash,
            "generated_at_utc": generated_at_utc,
            "expected_clip_count": len(annotation_packet.clips),
            "complete": complete,
            "clips": tuple(clips),
            "timed_coverages": tuple(timed_coverages),
        }
        candidate = cls(
            **payload,
            artifact_hash=_prospective_hash(payload, kind=cls._HASH_KIND),
        )
        _validate_candidate_bindings(annotation_packet.clips, candidate.clips)
        return candidate


TranscriptCandidateArtifactAny = TranscriptCandidateArtifactV1 | TranscriptCandidateArtifact


class MetricRate(_StrictFrozenModel):
    numerator: int
    denominator: int
    value: float | None

    @model_validator(mode="after")
    def _valid(self) -> MetricRate:
        if self.numerator < 0 or self.denominator < 0:
            raise ValueError("metric counts must be non-negative")
        expected = None if self.denominator == 0 else self.numerator / self.denominator
        if self.value != expected:
            raise ValueError("metric value does not match numerator/denominator")
        return self

    @classmethod
    def of(cls, numerator: int, denominator: int) -> MetricRate:
        return cls(
            numerator=numerator,
            denominator=denominator,
            value=None if denominator == 0 else numerator / denominator,
        )


class MetricNotEvaluated(_StrictFrozenModel):
    """A named metric that the available evidence cannot honestly score."""

    status: Literal["not_evaluated"] = "not_evaluated"
    reason_codes: tuple[str, ...]

    @field_validator("reason_codes")
    @classmethod
    def _reasons(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        result = _require_unique_nonempty("metric reason_codes", values)
        if not result or result != tuple(sorted(result)):
            raise ValueError("metric reason_codes must be non-empty and canonically sorted")
        return result


MetricResult = MetricRate | MetricNotEvaluated


class TranscriptMetrics(_StrictFrozenModel):
    lexical_normalization_profile: Literal[
        "unicode-nfkc-casefold-opencc-s2tw-ignore-punctuation-separators-controls-v1"
    ]
    lexical_evaluator_identity: LexicalEvaluatorIdentityV1
    scored_text_clip_count: int
    lexical_character_substitutions: int
    lexical_character_deletions: int
    lexical_character_insertions: int
    lexical_character_error_rate: MetricRate
    lexical_character_accuracy: MetricRate
    word_token_accuracy: MetricNotEvaluated
    entity_recall: MetricResult
    code_switch_recall: MetricResult
    numeric_recall: MetricResult
    lexical_deletion_edit_rate: MetricRate
    critical_omission_rate: MetricResult
    lexical_insertion_edit_rate: MetricRate
    needs_review_precision: MetricRate
    needs_review_recall: MetricRate
    correction_detection_recall: MetricRate
    correction_apply_recall: MetricRate
    false_keep_original_rate: MetricRate
    harmful_apply_rate: MetricRate
    source_precision: MetricRate

    @model_validator(mode="after")
    def _valid(self) -> TranscriptMetrics:
        if (
            self.lexical_evaluator_identity.normalization_profile
            != self.lexical_normalization_profile
        ):
            raise ValueError("lexical evaluator identity differs from normalization profile")
        for field in (
            self.scored_text_clip_count,
            self.lexical_character_substitutions,
            self.lexical_character_deletions,
            self.lexical_character_insertions,
        ):
            if field < 0:
                raise ValueError("transcript metric counts must be non-negative")
        lexical_errors = (
            self.lexical_character_substitutions
            + self.lexical_character_deletions
            + self.lexical_character_insertions
        )
        if self.lexical_character_error_rate.numerator != lexical_errors:
            raise ValueError("lexical_character_error_rate numerator drifts from edit counts")
        expected_accuracy_numerator = max(
            self.lexical_character_error_rate.denominator - lexical_errors,
            0,
        )
        if (
            self.lexical_character_accuracy.denominator
            != self.lexical_character_error_rate.denominator
            or self.lexical_character_accuracy.numerator != expected_accuracy_numerator
        ):
            raise ValueError("lexical_character_accuracy drifts from lexical edit counts")
        if self.word_token_accuracy.reason_codes != (_WORD_TOKEN_UNAVAILABLE_REASON,):
            raise ValueError("word_token_accuracy must state why provider tokens are incomparable")
        if (
            self.lexical_deletion_edit_rate.numerator
            != self.lexical_character_deletions
        ):
            raise ValueError(
                "lexical_deletion_edit_rate must be driven by lexical deletions"
            )
        if (
            self.lexical_insertion_edit_rate.numerator
            != self.lexical_character_insertions
        ):
            raise ValueError(
                "lexical_insertion_edit_rate must be driven by lexical insertions"
            )
        return self


class ClipTranscriptEvaluation(_StrictFrozenModel):
    clip_id: str
    text_scored: bool
    gold_text_hash: str | None
    candidate_text_hash: str
    lexical_character_substitutions: int
    lexical_character_deletions: int
    lexical_character_insertions: int
    gold_lexical_character_count: int
    entity_correct: int
    entity_total: int
    code_switch_correct: int
    code_switch_total: int
    numeric_correct: int
    numeric_total: int
    critical_omissions: int
    critical_omission_total: int
    gold_needs_review: bool
    candidate_needs_review: bool

    @field_validator("clip_id")
    @classmethod
    def _clip_id(cls, value: str) -> str:
        return _require_nonempty("clip_id", value)

    @field_validator("gold_text_hash", "candidate_text_hash")
    @classmethod
    def _hashes(cls, value: str | None, info: Any) -> str | None:
        if value is not None:
            _require_sha256(info.field_name, value)
        return value


class TranscriptEvaluationStatus(str, Enum):
    EVALUATED = "evaluated"
    NOT_EVALUATED = "not_evaluated"


class TranscriptEvaluationResult(_StrictFrozenModel):
    _HASH_KIND: ClassVar[str] = "transcript_candidate_evaluation"

    schema_version: Literal[2] = 2
    status: TranscriptEvaluationStatus
    reason_codes: tuple[str, ...]
    gold_suite_hash: str
    candidate_artifact_hash: str
    normalized_audio_hash: str
    annotation_packet_hash: str
    metrics: TranscriptMetrics | None
    correction_metrics_status: TranscriptEvaluationStatus
    correction_metrics_reason_codes: tuple[str, ...]
    clip_results: tuple[ClipTranscriptEvaluation, ...]
    evaluation_hash: str

    @field_validator(
        "gold_suite_hash",
        "candidate_artifact_hash",
        "normalized_audio_hash",
        "annotation_packet_hash",
        "evaluation_hash",
    )
    @classmethod
    def _hashes(cls, value: str, info: Any) -> str:
        return _require_sha256(info.field_name, value)

    @field_validator("reason_codes")
    @classmethod
    def _reasons(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        result = _require_unique_nonempty("reason_codes", values)
        if result != tuple(sorted(result)):
            raise ValueError("reason_codes must be in canonical sorted order")
        return result

    @model_validator(mode="after")
    def _valid(self) -> TranscriptEvaluationResult:
        if self.status is TranscriptEvaluationStatus.EVALUATED:
            if self.reason_codes or self.metrics is None or not self.clip_results:
                raise ValueError("evaluated result requires metrics/results and no reason codes")
        elif self.metrics is not None or self.clip_results:
            raise ValueError("not_evaluated result must not carry fabricated metrics")
        if self.correction_metrics_status is TranscriptEvaluationStatus.EVALUATED:
            if self.correction_metrics_reason_codes:
                raise ValueError("evaluated correction metrics cannot carry reason codes")
        elif not self.correction_metrics_reason_codes:
            raise ValueError("not_evaluated correction metrics require reason codes")
        if self.evaluation_hash != _artifact_hash(
            self, field="evaluation_hash", kind=self._HASH_KIND
        ):
            raise ValueError("transcript evaluation_hash mismatch")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)

    @classmethod
    def build(
        cls,
        *,
        status: TranscriptEvaluationStatus,
        reason_codes: Sequence[str],
        gold_suite_hash: str,
        candidate_artifact_hash: str,
        normalized_audio_hash: str,
        annotation_packet_hash: str,
        metrics: TranscriptMetrics | None,
        correction_metrics_status: TranscriptEvaluationStatus,
        correction_metrics_reason_codes: Sequence[str],
        clip_results: Sequence[ClipTranscriptEvaluation],
    ) -> TranscriptEvaluationResult:
        payload = {
            "schema_version": 2,
            "status": status,
            "reason_codes": tuple(sorted(reason_codes)),
            "gold_suite_hash": gold_suite_hash,
            "candidate_artifact_hash": candidate_artifact_hash,
            "normalized_audio_hash": normalized_audio_hash,
            "annotation_packet_hash": annotation_packet_hash,
            "metrics": metrics,
            "correction_metrics_status": correction_metrics_status,
            "correction_metrics_reason_codes": tuple(
                sorted(set(correction_metrics_reason_codes))
            ),
            "clip_results": tuple(clip_results),
        }
        return cls(
            **payload,
            evaluation_hash=_prospective_hash(payload, kind=cls._HASH_KIND),
        )


def _assert_no_candidate_leakage(
    value: Any,
    candidate_hashes: Iterable[str],
    *,
    label: str,
) -> None:
    raw = canonical_json_bytes(value)
    for digest in candidate_hashes:
        _require_sha256("candidate artifact hash", digest)
        if digest.encode("ascii") in raw:
            raise ValueError(f"{label} contains a forbidden candidate artifact hash")


def verify_annotation_packet_blinding(
    packet: TranscriptAnnotationPacket,
    candidate_artifact_hashes: Iterable[str],
) -> None:
    """Fail if any known V1/V2 candidate identity leaked into annotation input."""

    _assert_no_candidate_leakage(
        packet.model_dump(mode="json", exclude={"packet_hash"}),
        candidate_artifact_hashes,
        label="annotation packet",
    )


def verify_gold_blinding(
    suite: TranscriptGoldSuite,
    candidate_artifact_hashes: Iterable[str],
) -> None:
    """Fail if human gold provenance contains any known candidate identity."""

    _assert_no_candidate_leakage(
        suite.model_dump(mode="json", exclude={"suite_hash"}),
        candidate_artifact_hashes,
        label="gold suite",
    )


def _validate_candidate_bindings(
    planned: Sequence[AudioClipBinding],
    outputs: Sequence[CandidateClipOutput],
) -> None:
    plan_by_id = {clip.clip_id: clip for clip in planned}
    plan_order = {clip.clip_id: index for index, clip in enumerate(planned)}
    output_ids = [output.clip.clip_id for output in outputs]
    unknown = set(output_ids) - set(plan_by_id)
    if unknown:
        raise ValueError(f"candidate output cites unknown clips: {sorted(unknown)}")
    if [plan_order[item] for item in output_ids] != sorted(plan_order[item] for item in output_ids):
        raise ValueError("candidate outputs must follow annotation packet clip order")
    for output in outputs:
        expected = plan_by_id[output.clip.clip_id]
        if output.clip != expected:
            raise ValueError(
                f"candidate clip binding drift for {output.clip.clip_id}: interval or bytes differ"
            )


class _EditCounts(_StrictFrozenModel):
    substitutions: int
    deletions: int
    insertions: int

    @property
    def total(self) -> int:
        return self.substitutions + self.deletions + self.insertions


def _edit_counts(expected: Sequence[str], observed: Sequence[str]) -> _EditCounts:
    """Levenshtein edits with deterministic substitution/deletion/insertion ties."""

    previous = [
        _EditCounts(substitutions=0, deletions=0, insertions=index)
        for index in range(len(observed) + 1)
    ]
    for expected_index, expected_item in enumerate(expected, start=1):
        current = [_EditCounts(substitutions=0, deletions=expected_index, insertions=0)]
        for observed_index, observed_item in enumerate(observed, start=1):
            diagonal = previous[observed_index - 1]
            if expected_item == observed_item:
                current.append(diagonal)
                continue
            candidates = (
                (
                    _EditCounts(
                        substitutions=diagonal.substitutions + 1,
                        deletions=diagonal.deletions,
                        insertions=diagonal.insertions,
                    ),
                    0,
                ),
                (
                    _EditCounts(
                        substitutions=previous[observed_index].substitutions,
                        deletions=previous[observed_index].deletions + 1,
                        insertions=previous[observed_index].insertions,
                    ),
                    1,
                ),
                (
                    _EditCounts(
                        substitutions=current[observed_index - 1].substitutions,
                        deletions=current[observed_index - 1].deletions,
                        insertions=current[observed_index - 1].insertions + 1,
                    ),
                    2,
                ),
            )
            current.append(min(candidates, key=lambda item: (item[0].total, item[1]))[0])
        previous = current
    return previous[-1]


def _label_recall(
    labels: Sequence[GoldSpanLabel],
    *,
    kind: GoldSpanKind,
    candidate_text: str,
) -> tuple[int, int]:
    selected = [label for label in labels if label.kind == kind]
    normalizer = _lexical_label_search_text if kind == "entity" else _semantic_search_text
    correct = _matched_literal_count(
        tuple(label.expected_text for label in selected),
        candidate_text=candidate_text,
        normalizer=normalizer,
    )
    return correct, len(selected)


def _matched_literal_count(
    expected_values: Sequence[str],
    *,
    candidate_text: str,
    normalizer: Any,
) -> int:
    searchable = normalizer(candidate_text)
    occupied: list[tuple[int, int]] = []
    correct = 0
    for value in expected_values:
        text = normalizer(value).strip()
        if not text or "  " in text:
            continue
        candidates = tuple(
            match.span()
            for match in re.finditer(re.escape(text), searchable)
            if _label_match_has_boundaries(searchable, match.start(), match.end(), text)
        )
        available = next(
            (
                interval
                for interval in candidates
                if all(interval[1] <= start or interval[0] >= end for start, end in occupied)
            ),
            None,
        )
        if available is not None:
            occupied.append(available)
            correct += 1
    return correct


def _missing_omission_count(
    labels: Sequence[GoldOmissionLabel],
    *,
    candidate_text: str,
) -> int:
    present = _matched_literal_count(
        tuple(label.expected_text for label in labels),
        candidate_text=candidate_text,
        normalizer=_semantic_search_text,
    )
    return len(labels) - present


def _label_match_has_boundaries(
    searchable: str,
    start: int,
    end: int,
    expected: str,
) -> bool:
    """Reject substring credit inside ASCII words and decimal/integer runs."""

    def ascii_word(character: str) -> bool:
        return character.isascii() and (character.isalnum() or character == "_")

    first = expected[0]
    last = expected[-1]
    left = searchable[start - 1] if start else ""
    right = searchable[end] if end < len(searchable) else ""
    if left and ascii_word(left) and ascii_word(first):
        return False
    if right and ascii_word(last) and ascii_word(right):
        return False
    if first.isdecimal() and left and left.isdecimal():
        return False
    if last.isdecimal() and right and right.isdecimal():
        return False
    return True


class _CorrectionCounts(_StrictFrozenModel):
    apply_gold: int = 0
    detected: int = 0
    exact_applied: int = 0
    false_keep_original: int = 0
    applied_total: int = 0
    harmful_applies: int = 0
    cited_total: int = 0
    cited_supported: int = 0

    def plus(self, other: _CorrectionCounts) -> _CorrectionCounts:
        return _CorrectionCounts(
            **{
                field: getattr(self, field) + getattr(other, field)
                for field in type(self).model_fields
            }
        )


def _correction_counts(
    gold: Sequence[CorrectionGoldLabel],
    candidate: Sequence[CandidateCorrectionDecision],
    coverage: CandidateTimedCoverageV2,
) -> _CorrectionCounts:
    gold_by_target = {
        (item.target_start_ms, item.target_end_ms): item for item in gold
    }
    candidate_by_target = {
        (item.target_start_ms, item.target_end_ms): item for item in candidate
    }
    apply_gold = 0
    detected = 0
    exact_applied = 0
    false_keep = 0
    applied_total = 0
    harmful = 0
    cited_total = 0
    cited_supported = 0

    for correction in gold:
        if correction.expected_outcome != "accepted":
            continue
        decision = candidate_by_target.get(
            (correction.target_start_ms, correction.target_end_ms)
        )
        recognition_text, final_text, _ = _project_timed_text(
            coverage,
            start_ms=correction.target_start_ms,
            end_ms=correction.target_end_ms,
        )
        if recognition_text == correction.expected_text:
            continue
        apply_gold += 1
        if (
            decision is not None
            and decision.action != "not_detected"
            or final_text != recognition_text
        ):
            detected += 1
        if final_text == recognition_text:
            false_keep += 1
        if final_text == correction.expected_text:
            exact_applied += 1

    for decision in candidate:
        expected = gold_by_target.get((decision.target_start_ms, decision.target_end_ms))
        if decision.action == "apply":
            applied_total += 1
            # Authority validation guarantees an exact accepted target here.
            if expected is None or expected.expected_outcome != "accepted":
                raise ValueError("candidate apply lacks accepted gold authority")
            if decision.final_text != expected.expected_text:
                harmful += 1
        cited_total += len(decision.source_artifact_ids)
        if expected is not None:
            cited_supported += sum(
                source_id in expected.authorized_spelling_source_ids
                for source_id in decision.source_artifact_ids
            )
    return _CorrectionCounts(
        apply_gold=apply_gold,
        detected=detected,
        exact_applied=exact_applied,
        false_keep_original=false_keep,
        applied_total=applied_total,
        harmful_applies=harmful,
        cited_total=cited_total,
        cited_supported=cited_supported,
    )


def _candidate_timed_coverage(
    candidate: TranscriptCandidateArtifactAny,
) -> tuple[dict[str, CandidateTimedCoverageV2], tuple[str, ...]]:
    if getattr(candidate, "evaluation_scope", None) == "recognition_only":
        return {}, ("candidate_evaluation_scope_recognition_only",)
    if isinstance(candidate, TranscriptCandidateArtifactV1):
        return {}, ("candidate_timed_coverage_unavailable",)
    coverage_by_id = {
        coverage.clip.clip_id: coverage for coverage in candidate.timed_coverages
    }
    clip_ids = {output.clip.clip_id for output in candidate.clips}
    if set(coverage_by_id) != clip_ids:
        return coverage_by_id, ("candidate_timed_coverage_incomplete",)
    return coverage_by_id, ()


def _correction_authority_reasons(
    labels: Sequence[GoldClipLabel],
    candidate_by_id: dict[str, CandidateClipOutput],
) -> tuple[str, ...]:
    """Refuse harmful-apply scoring where gold does not authorize a verdict.

    TranscriptGoldSuite V1 does not declare correction labels exhaustive.
    Therefore an apply that cannot map uniquely to one exact accepted target is
    not guessed harmful or harmless: the correction block is not evaluated.
    """

    gold_by_clip = {label.clip_id: label for label in labels}
    for label in labels:
        output = candidate_by_id[label.clip_id]
        accepted_targets = {
            (correction.target_start_ms, correction.target_end_ms)
            for correction in label.correction_labels
            if correction.expected_outcome == "accepted"
        }
        for decision in output.corrections:
            if decision.action != "apply":
                continue
            target = (decision.target_start_ms, decision.target_end_ms)
            if target not in accepted_targets:
                return ("candidate_apply_outside_gold_authority",)
    unknown_clip_ids = set(candidate_by_id) - set(gold_by_clip)
    if any(
        decision.action == "apply"
        for clip_id in unknown_clip_ids
        for decision in candidate_by_id[clip_id].corrections
    ):
        return ("candidate_apply_outside_gold_authority",)
    return ()


def _not_evaluated(
    suite: TranscriptGoldSuite,
    candidate: TranscriptCandidateArtifactAny,
    reasons: Sequence[str],
) -> TranscriptEvaluationResult:
    return TranscriptEvaluationResult.build(
        status=TranscriptEvaluationStatus.NOT_EVALUATED,
        reason_codes=tuple(sorted(set(reasons))),
        gold_suite_hash=suite.suite_hash,
        candidate_artifact_hash=candidate.artifact_hash,
        normalized_audio_hash=suite.normalized_audio_hash,
        annotation_packet_hash=suite.annotation_packet_hash,
        metrics=None,
        correction_metrics_status=TranscriptEvaluationStatus.NOT_EVALUATED,
        correction_metrics_reason_codes=("episode_transcript_not_evaluated",),
        clip_results=(),
    )


def evaluate_transcript_candidate(
    suite: TranscriptGoldSuite,
    candidate: TranscriptCandidateArtifactAny,
    *,
    all_candidate_artifact_hashes: Iterable[str] = (),
) -> TranscriptEvaluationResult:
    """Score one candidate, or honestly return typed ``not_evaluated``.

    ``all_candidate_artifact_hashes`` should contain both V1 and V2 identities
    in a paired study.  The current candidate is added automatically.  Any of
    those hashes appearing in gold provenance is treated as blinding leakage.
    """

    if suite.episode_id != candidate.episode_id:
        raise ValueError("candidate episode_id differs from gold suite")
    if (
        suite.normalized_audio_hash != candidate.normalized_audio_hash
        or suite.normalized_audio_size_bytes != candidate.normalized_audio_size_bytes
    ):
        raise ValueError("candidate normalized-audio lineage differs from gold suite")
    if suite.annotation_packet_hash != candidate.annotation_packet_hash:
        raise ValueError("candidate annotation packet lineage differs from gold suite")
    if suite.expected_clip_count != candidate.expected_clip_count:
        raise ValueError("candidate expected clip count differs from gold suite")
    _validate_candidate_bindings(suite.clips, candidate.clips)
    hashes = tuple(all_candidate_artifact_hashes) + (candidate.artifact_hash,)
    verify_gold_blinding(suite, hashes)
    lexical_evaluator_before = measure_lexical_evaluator_identity()

    reasons: list[str] = []
    if not suite.complete:
        reasons.append("gold_suite_incomplete")
    if len(suite.labels) != suite.expected_clip_count:
        reasons.append("gold_labels_missing")
    if not candidate.complete:
        reasons.append("candidate_artifact_incomplete")
    if len(candidate.clips) != candidate.expected_clip_count:
        reasons.append("candidate_outputs_missing")
    if reasons:
        return _not_evaluated(suite, candidate, reasons)

    candidate_by_id = {item.clip.clip_id: item for item in candidate.clips}
    coverage_by_id, coverage_reasons = _candidate_timed_coverage(candidate)
    authority_reasons = _correction_authority_reasons(suite.labels, candidate_by_id)
    correction_phase_order_verified = bool(
        suite.correction_targets_revealed_at_utc is not None
        and _parse_utc("candidate generated_at_utc", candidate.generated_at_utc)
        < _parse_utc(
            "correction targets revealed_at_utc",
            suite.correction_targets_revealed_at_utc,
        )
    )
    lexical_char_substitutions = 0
    lexical_char_deletions = 0
    lexical_char_insertions = 0
    gold_lexical_characters = 0
    entity_correct = 0
    entity_total = 0
    code_switch_correct = 0
    code_switch_total = 0
    numeric_correct = 0
    numeric_total = 0
    critical_omissions = 0
    critical_omission_total = 0
    entity_labels_exhaustive = True
    code_switch_labels_exhaustive = True
    numeric_labels_exhaustive = True
    critical_omission_labels_exhaustive = True
    scored_coverage_count = 0
    review_tp = 0
    review_fp = 0
    review_fn = 0
    corrections = _CorrectionCounts()
    correction_evidence_valid = not coverage_reasons and not authority_reasons
    results: list[ClipTranscriptEvaluation] = []

    for gold in suite.labels:
        observed = candidate_by_id[gold.clip_id]
        gold_needs_review = gold.expected_outcome == "needs_review"
        candidate_needs_review = observed.outcome == "needs_review"
        review_tp += int(gold_needs_review and candidate_needs_review)
        review_fp += int(not gold_needs_review and candidate_needs_review)
        review_fn += int(gold_needs_review and not candidate_needs_review)

        text_scored = not gold_needs_review and gold.text is not None
        edits = _EditCounts(substitutions=0, deletions=0, insertions=0)
        clip_gold_lexical_chars = 0
        clip_entity_correct = 0
        clip_entity_total = 0
        clip_code_correct = 0
        clip_code_total = 0
        clip_numeric_correct = 0
        clip_numeric_total = 0
        clip_omissions = 0
        clip_omission_total = 0
        if text_scored:
            scored_coverage_count += 1
            adjudication = gold.provenance.adjudication_record
            if adjudication is None:
                raise ValueError("complete Gold unexpectedly lacks adjudication coverage")
            coverage = adjudication.metric_coverage
            entity_labels_exhaustive &= coverage.entity_labels_exhaustive
            code_switch_labels_exhaustive &= coverage.code_switch_labels_exhaustive
            numeric_labels_exhaustive &= coverage.numeric_labels_exhaustive
            critical_omission_labels_exhaustive &= (
                coverage.critical_omission_labels_exhaustive
            )
            expected_text = gold.text or ""
            candidate_text = observed.text or ""
            expected_characters = _lexical_character_units(expected_text)
            observed_characters = _lexical_character_units(candidate_text)
            edits = _edit_counts(expected_characters, observed_characters)
            clip_gold_lexical_chars = len(expected_characters)
            clip_entity_correct, clip_entity_total = _label_recall(
                gold.span_labels, kind="entity", candidate_text=candidate_text
            )
            clip_code_correct, clip_code_total = _label_recall(
                gold.span_labels, kind="code_switch", candidate_text=candidate_text
            )
            clip_numeric_correct, clip_numeric_total = _label_recall(
                gold.span_labels, kind="numeric", candidate_text=candidate_text
            )
            critical_labels = [
                label for label in gold.omission_labels if label.severity == "critical"
            ]
            clip_omission_total = len(critical_labels)
            clip_omissions = _missing_omission_count(
                critical_labels,
                candidate_text=candidate_text,
            )

            lexical_char_substitutions += edits.substitutions
            lexical_char_deletions += edits.deletions
            lexical_char_insertions += edits.insertions
            gold_lexical_characters += clip_gold_lexical_chars
            entity_correct += clip_entity_correct
            entity_total += clip_entity_total
            code_switch_correct += clip_code_correct
            code_switch_total += clip_code_total
            numeric_correct += clip_numeric_correct
            numeric_total += clip_numeric_total
            critical_omissions += clip_omissions
            critical_omission_total += clip_omission_total

        if correction_evidence_valid:
            corrections = corrections.plus(
                _correction_counts(
                    gold.correction_labels,
                    observed.corrections,
                    coverage_by_id[gold.clip_id],
                )
            )
        results.append(
            ClipTranscriptEvaluation(
                clip_id=gold.clip_id,
                text_scored=text_scored,
                gold_text_hash=None if gold.text is None else hash_object({"text": gold.text}),
                candidate_text_hash=observed.transcript_text_hash,
                lexical_character_substitutions=edits.substitutions,
                lexical_character_deletions=edits.deletions,
                lexical_character_insertions=edits.insertions,
                gold_lexical_character_count=clip_gold_lexical_chars,
                entity_correct=clip_entity_correct,
                entity_total=clip_entity_total,
                code_switch_correct=clip_code_correct,
                code_switch_total=clip_code_total,
                numeric_correct=clip_numeric_correct,
                numeric_total=clip_numeric_total,
                critical_omissions=clip_omissions,
                critical_omission_total=clip_omission_total,
                gold_needs_review=gold_needs_review,
                candidate_needs_review=candidate_needs_review,
            )
        )

    lexical_char_errors = (
        lexical_char_substitutions + lexical_char_deletions + lexical_char_insertions
    )
    entity_labels_exhaustive &= scored_coverage_count > 0
    code_switch_labels_exhaustive &= scored_coverage_count > 0
    numeric_labels_exhaustive &= scored_coverage_count > 0
    critical_omission_labels_exhaustive &= scored_coverage_count > 0
    if getattr(candidate, "evaluation_scope", None) == "recognition_only":
        correction_reasons = ("candidate_evaluation_scope_recognition_only",)
    else:
        correction_reasons = tuple(
            sorted(
                {
                    *coverage_reasons,
                    *authority_reasons,
                    *(
                        ()
                        if correction_phase_order_verified
                        else ("candidate_generated_before_target_reveal_unverified",)
                    ),
                }
            )
        )
    if not correction_reasons:
        detection_metric = MetricRate.of(corrections.detected, corrections.apply_gold)
        apply_metric = MetricRate.of(corrections.exact_applied, corrections.apply_gold)
        false_keep_metric = MetricRate.of(
            corrections.false_keep_original, corrections.apply_gold
        )
        harmful_metric = MetricRate.of(
            corrections.harmful_applies, corrections.applied_total
        )
        source_metric = MetricRate.of(corrections.cited_supported, corrections.cited_total)
        correction_status = TranscriptEvaluationStatus.EVALUATED
        correction_reasons = ()
    else:
        detection_metric = MetricRate.of(0, 0)
        apply_metric = MetricRate.of(0, 0)
        false_keep_metric = MetricRate.of(0, 0)
        harmful_metric = MetricRate.of(0, 0)
        source_metric = MetricRate.of(0, 0)
        correction_status = TranscriptEvaluationStatus.NOT_EVALUATED

    lexical_evaluator_after = measure_lexical_evaluator_identity()
    if lexical_evaluator_after != lexical_evaluator_before:
        raise ValueError("lexical evaluator changed while recognition metrics were scored")
    metrics = TranscriptMetrics(
        lexical_normalization_profile=_LEXICAL_NORMALIZATION_PROFILE,
        lexical_evaluator_identity=lexical_evaluator_before,
        scored_text_clip_count=sum(result.text_scored for result in results),
        lexical_character_substitutions=lexical_char_substitutions,
        lexical_character_deletions=lexical_char_deletions,
        lexical_character_insertions=lexical_char_insertions,
        lexical_character_error_rate=MetricRate.of(
            lexical_char_errors, gold_lexical_characters
        ),
        lexical_character_accuracy=MetricRate.of(
            max(gold_lexical_characters - lexical_char_errors, 0),
            gold_lexical_characters,
        ),
        word_token_accuracy=MetricNotEvaluated(
            reason_codes=(_WORD_TOKEN_UNAVAILABLE_REASON,)
        ),
        entity_recall=(
            MetricRate.of(entity_correct, entity_total)
            if entity_labels_exhaustive
            else MetricNotEvaluated(reason_codes=("entity_labels_not_exhaustive",))
        ),
        code_switch_recall=(
            MetricRate.of(code_switch_correct, code_switch_total)
            if code_switch_labels_exhaustive
            else MetricNotEvaluated(reason_codes=("code_switch_labels_not_exhaustive",))
        ),
        numeric_recall=(
            MetricRate.of(numeric_correct, numeric_total)
            if numeric_labels_exhaustive
            else MetricNotEvaluated(reason_codes=("numeric_labels_not_exhaustive",))
        ),
        lexical_deletion_edit_rate=MetricRate.of(
            lexical_char_deletions, gold_lexical_characters
        ),
        critical_omission_rate=(
            MetricRate.of(critical_omissions, critical_omission_total)
            if critical_omission_labels_exhaustive
            else MetricNotEvaluated(reason_codes=("critical_omission_labels_not_exhaustive",))
        ),
        lexical_insertion_edit_rate=MetricRate.of(
            lexical_char_insertions, gold_lexical_characters
        ),
        needs_review_precision=MetricRate.of(review_tp, review_tp + review_fp),
        needs_review_recall=MetricRate.of(review_tp, review_tp + review_fn),
        correction_detection_recall=detection_metric,
        correction_apply_recall=apply_metric,
        false_keep_original_rate=false_keep_metric,
        harmful_apply_rate=harmful_metric,
        source_precision=source_metric,
    )
    return TranscriptEvaluationResult.build(
        status=TranscriptEvaluationStatus.EVALUATED,
        reason_codes=(),
        gold_suite_hash=suite.suite_hash,
        candidate_artifact_hash=candidate.artifact_hash,
        normalized_audio_hash=suite.normalized_audio_hash,
        annotation_packet_hash=suite.annotation_packet_hash,
        metrics=metrics,
        correction_metrics_status=correction_status,
        correction_metrics_reason_codes=correction_reasons,
        clip_results=results,
    )


_ModelT = TypeVar("_ModelT", bound=BaseModel)


def _load_exact_canonical(source: bytes | bytearray | Path, model: type[_ModelT]) -> _ModelT:
    raw = Path(source).read_bytes() if isinstance(source, Path) else bytes(source)
    try:
        untyped = json.loads(raw)
        if not isinstance(untyped, dict):
            raise ValueError("top-level artifact must be an object")
        expected_fields = set(model.model_fields)
        if set(untyped) != expected_fields:
            missing = sorted(expected_fields - set(untyped))
            unknown = sorted(set(untyped) - expected_fields)
            raise ValueError(
                f"top-level fields are not exact; missing={missing}, unknown={unknown}"
            )
        value = model.model_validate_json(raw)
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise ValueError(f"invalid {model.__name__} JSON") from exc
    if canonical_json_bytes(value) != raw:
        raise ValueError(f"{model.__name__} bytes are not exact canonical JSON")
    return value


def load_annotation_packet(source: bytes | bytearray | Path) -> TranscriptAnnotationPacket:
    return _load_exact_canonical(source, TranscriptAnnotationPacket)


def load_transcript_gold_suite(source: bytes | bytearray | Path) -> TranscriptGoldSuite:
    return _load_exact_canonical(source, TranscriptGoldSuite)


def load_transcript_candidate(
    source: bytes | bytearray | Path,
) -> TranscriptCandidateArtifactAny:
    raw = Path(source).read_bytes() if isinstance(source, Path) else bytes(source)
    try:
        untyped = json.loads(raw)
        if not isinstance(untyped, dict):
            raise ValueError("top-level artifact must be an object")
        schema_version = untyped.get("schema_version")
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise ValueError("invalid transcript candidate JSON") from exc
    if schema_version == 1:
        return _load_exact_canonical(raw, TranscriptCandidateArtifactV1)
    if schema_version == 2:
        return _load_exact_canonical(raw, TranscriptCandidateArtifact)
    if schema_version == 3:
        from .candidate_materialization import SourceBoundTranscriptCandidateArtifactV3

        return _load_exact_canonical(raw, SourceBoundTranscriptCandidateArtifactV3)  # type: ignore[return-value]
    raise ValueError(f"unsupported transcript candidate schema_version: {schema_version!r}")


def load_transcript_evaluation(source: bytes | bytearray | Path) -> TranscriptEvaluationResult:
    return _load_exact_canonical(source, TranscriptEvaluationResult)


__all__ = [
    "AdjudicationProvenance",
    "AudioClipBinding",
    "AudioOnlyTranscriptSubmission",
    "CandidateClipOutput",
    "CandidateCorrectionDecision",
    "CandidateTimedCoverageV2",
    "CandidateTimedTextSpanV2",
    "CorrectionGoldLabel",
    "GoldClipLabel",
    "GoldMetricCoverageV1",
    "GoldOmissionLabel",
    "GoldSpanLabel",
    "GoldToken",
    "MetricRate",
    "MetricNotEvaluated",
    "LexicalEvaluatorIdentityV1",
    "SpellingAuthoritySource",
    "SpellingAuthorityUse",
    "TranscriptAdjudicationRecord",
    "TranscriptAnnotationPacket",
    "TranscriptAnnotationProtocol",
    "TranscriptCandidateArtifact",
    "TranscriptCandidateArtifactAny",
    "TranscriptCandidateArtifactV1",
    "TranscriptEvaluationResult",
    "TranscriptEvaluationStatus",
    "TranscriptGoldSuite",
    "TranscriptMetrics",
    "evaluate_transcript_candidate",
    "load_annotation_packet",
    "load_transcript_candidate",
    "load_transcript_evaluation",
    "load_transcript_gold_suite",
    "measure_lexical_evaluator_identity",
    "verify_annotation_packet_blinding",
    "verify_gold_blinding",
]
