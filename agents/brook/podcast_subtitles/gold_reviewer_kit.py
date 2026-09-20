"""Candidate-blind human reviewer kits for :mod:`transcript_gold`.

The helpers in this module expose only an allow-listed, neutral view of a
sealed annotation packet.  They never scan an episode directory and never
accept candidate, QC, or reference files as kit inputs.  Human-editable batch
responses are compiled into the exact canonical per-clip artifacts consumed
by :class:`~agents.brook.podcast_subtitles.gold_custody.GoldCustodyWorkspace`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, ClassVar, Iterable, Literal, Sequence

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from .hashing import canonical_json_bytes, hash_object
from .transcript_gold import (
    AdjudicationProvenance,
    AudioOnlyTranscriptSubmission,
    GoldClipLabel,
    GoldMetricCoverageV1,
    GoldOmissionLabel,
    GoldSpanLabel,
    GoldToken,
    SpellingAuthoritySource,
    SpellingAuthorityUse,
    TranscriptAdjudicationRecord,
    TranscriptAnnotationPacket,
    TranscriptGoldSuite,
    load_annotation_packet,
    verify_annotation_packet_blinding,
)

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _require_sha256(label: str, value: str) -> str:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")
    return value


def _require_safe_id(label: str, value: str) -> str:
    if not _SAFE_ID_RE.fullmatch(value):
        raise ValueError(f"{label} must be a canonical lowercase opaque identifier")
    return value


def _artifact_hash(model: BaseModel, *, field: str, kind: str) -> str:
    return hash_object(
        {
            "artifact_kind": kind,
            **model.model_dump(mode="json", exclude={field}),
        }
    )


def _prospective_hash(payload: dict[str, Any], *, kind: str) -> str:
    return hash_object({"artifact_kind": kind, **payload})


class FirstPassReviewerClipV1(_StrictFrozenModel):
    """Neutral binding for one clip; source episode and clip IDs stay hidden."""

    neutral_clip_id: str
    start_ms: int
    end_ms: int
    audio_relpath: str
    clip_binding_hash: str
    clip_audio_sha256: str
    clip_audio_size_bytes: int

    @field_validator("neutral_clip_id")
    @classmethod
    def _neutral_clip_id(cls, value: str) -> str:
        return _require_safe_id("neutral_clip_id", value)

    @field_validator("clip_binding_hash", "clip_audio_sha256")
    @classmethod
    def _hashes(cls, value: str, info: Any) -> str:
        return _require_sha256(info.field_name, value)

    @model_validator(mode="after")
    def _valid(self) -> FirstPassReviewerClipV1:
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("reviewer clip interval is invalid")
        if self.clip_audio_size_bytes < 1:
            raise ValueError("reviewer clip size must be positive")
        expected = f"audio/{int(self.neutral_clip_id.removeprefix('item-')):04d}.wav"
        if self.audio_relpath != expected:
            raise ValueError("reviewer clip audio_relpath is not canonical")
        return self


class FirstPassReviewerKitManifestV1(_StrictFrozenModel):
    """Candidate-free, reference-free first-pass handoff manifest."""

    _HASH_KIND: ClassVar[str] = "first_pass_reviewer_kit_manifest"

    schema_version: Literal[1] = 1
    kit_id: str
    annotation_packet_hash: str
    reviewer_role: Literal["independent_audio_only_first_pass"] = (
        "independent_audio_only_first_pass"
    )
    candidate_outputs_hidden: Literal[True] = True
    qc_outputs_hidden: Literal[True] = True
    reference_material_hidden: Literal[True] = True
    human_identity_authority: Literal["external_collection_process"] = "external_collection_process"
    response_schema_id: Literal["audio-only-first-pass-response-batch-v1"] = (
        "audio-only-first-pass-response-batch-v1"
    )
    clips: tuple[FirstPassReviewerClipV1, ...]
    manifest_hash: str

    @field_validator("kit_id")
    @classmethod
    def _kit_id(cls, value: str) -> str:
        return _require_safe_id("kit_id", value)

    @field_validator("annotation_packet_hash", "manifest_hash")
    @classmethod
    def _hashes(cls, value: str, info: Any) -> str:
        return _require_sha256(info.field_name, value)

    @model_validator(mode="after")
    def _valid(self) -> FirstPassReviewerKitManifestV1:
        if not self.clips:
            raise ValueError("first-pass reviewer kit requires clips")
        expected_ids = tuple(f"item-{index:04d}" for index in range(1, len(self.clips) + 1))
        if tuple(item.neutral_clip_id for item in self.clips) != expected_ids:
            raise ValueError("reviewer clips must use canonical neutral order")
        if len({item.clip_binding_hash for item in self.clips}) != len(self.clips):
            raise ValueError("reviewer clips contain duplicate bindings")
        if self.manifest_hash != _artifact_hash(self, field="manifest_hash", kind=self._HASH_KIND):
            raise ValueError("reviewer kit manifest_hash mismatch")
        return self

    @classmethod
    def build(
        cls,
        *,
        kit_id: str,
        annotation_packet_hash: str,
        clips: Sequence[FirstPassReviewerClipV1],
    ) -> FirstPassReviewerKitManifestV1:
        payload = {
            "schema_version": 1,
            "kit_id": kit_id,
            "annotation_packet_hash": annotation_packet_hash,
            "reviewer_role": "independent_audio_only_first_pass",
            "candidate_outputs_hidden": True,
            "qc_outputs_hidden": True,
            "reference_material_hidden": True,
            "human_identity_authority": "external_collection_process",
            "response_schema_id": "audio-only-first-pass-response-batch-v1",
            "clips": tuple(clips),
        }
        return cls(
            **payload,
            manifest_hash=_prospective_hash(payload, kind=cls._HASH_KIND),
        )

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)


FirstPassOutcome = Literal["accepted", "needs_review"]


class FirstPassResponseAnswerV1(_StrictFrozenModel):
    neutral_clip_id: str
    outcome: FirstPassOutcome
    text: str | None

    @field_validator("neutral_clip_id")
    @classmethod
    def _neutral_clip_id(cls, value: str) -> str:
        return _require_safe_id("neutral_clip_id", value)

    @model_validator(mode="after")
    def _valid(self) -> FirstPassResponseAnswerV1:
        if self.outcome == "accepted":
            if not self.text:
                raise ValueError("accepted first-pass response requires text")
        elif self.text is not None:
            raise ValueError("needs_review first-pass response cannot assert spoken text")
        return self


class FirstPassResponseBatchV1(_StrictFrozenModel):
    """Human-editable strict batch; compilation adds per-clip content hashes."""

    schema_version: Literal[1] = 1
    reviewer_kit_manifest_hash: str
    annotator_id: str
    reviewer_attestation: Literal["human_audio_only_first_pass"] = "human_audio_only_first_pass"
    candidate_outputs_hidden: Literal[True] = True
    qc_outputs_hidden: Literal[True] = True
    reference_material_hidden: Literal[True] = True
    answers: tuple[FirstPassResponseAnswerV1, ...]

    @field_validator("reviewer_kit_manifest_hash")
    @classmethod
    def _manifest_hash(cls, value: str) -> str:
        return _require_sha256("reviewer_kit_manifest_hash", value)

    @field_validator("annotator_id")
    @classmethod
    def _annotator_id(cls, value: str) -> str:
        return _require_safe_id("annotator_id", value)


class AdjudicationFirstPassViewV1(_StrictFrozenModel):
    """Identity-redacted view of one genuine first-pass submission."""

    pass_id: Literal["pass-a", "pass-b"]
    submission_hash: str
    outcome: FirstPassOutcome
    text: str | None
    tokens: tuple[str, ...]

    @field_validator("submission_hash")
    @classmethod
    def _submission_hash(cls, value: str) -> str:
        return _require_sha256("submission_hash", value)

    @model_validator(mode="after")
    def _valid(self) -> AdjudicationFirstPassViewV1:
        if self.outcome == "accepted":
            if self.text is None or not self.tokens or "".join(self.tokens) != self.text:
                raise ValueError("accepted first-pass view requires exact text and tokens")
        elif self.text is not None or self.tokens:
            raise ValueError("needs_review first-pass view cannot assert spoken text")
        return self


class AdjudicationReviewerClipV1(_StrictFrozenModel):
    neutral_clip_id: str
    start_ms: int
    end_ms: int
    audio_relpath: str
    clip_binding_hash: str
    clip_audio_sha256: str
    clip_audio_size_bytes: int
    first_passes: tuple[AdjudicationFirstPassViewV1, AdjudicationFirstPassViewV1]

    @field_validator("neutral_clip_id")
    @classmethod
    def _neutral_clip_id(cls, value: str) -> str:
        return _require_safe_id("neutral_clip_id", value)

    @field_validator("clip_binding_hash", "clip_audio_sha256")
    @classmethod
    def _hashes(cls, value: str, info: Any) -> str:
        return _require_sha256(info.field_name, value)

    @model_validator(mode="after")
    def _valid(self) -> AdjudicationReviewerClipV1:
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("adjudication reviewer clip interval is invalid")
        if self.clip_audio_size_bytes < 1:
            raise ValueError("adjudication reviewer clip size must be positive")
        expected = f"audio/{int(self.neutral_clip_id.removeprefix('item-')):04d}.wav"
        if self.audio_relpath != expected:
            raise ValueError("adjudication reviewer clip audio_relpath is not canonical")
        if tuple(item.pass_id for item in self.first_passes) != ("pass-a", "pass-b"):
            raise ValueError("adjudication first passes must use canonical redacted order")
        if len({item.submission_hash for item in self.first_passes}) != 2:
            raise ValueError("adjudication first passes must be distinct")
        return self


class AdjudicationReviewerKitManifestV1(_StrictFrozenModel):
    """Candidate-blind third-person kit over two identity-redacted first passes."""

    _HASH_KIND: ClassVar[str] = "adjudication_reviewer_kit_manifest"

    schema_version: Literal[1] = 1
    kit_id: str
    annotation_packet_hash: str
    reviewer_role: Literal["candidate_blind_third_person_adjudicator"] = (
        "candidate_blind_third_person_adjudicator"
    )
    candidate_outputs_hidden: Literal[True] = True
    qc_outputs_hidden: Literal[True] = True
    first_pass_identities_hidden: Literal[True] = True
    reference_scope: Literal["spelling_only"] = "spelling_only"
    human_identity_authority: Literal["external_collection_process"] = "external_collection_process"
    response_schema_id: Literal["third-person-adjudication-response-batch-v1"] = (
        "third-person-adjudication-response-batch-v1"
    )
    clips: tuple[AdjudicationReviewerClipV1, ...]
    manifest_hash: str

    @field_validator("kit_id")
    @classmethod
    def _kit_id(cls, value: str) -> str:
        return _require_safe_id("kit_id", value)

    @field_validator("annotation_packet_hash", "manifest_hash")
    @classmethod
    def _hashes(cls, value: str, info: Any) -> str:
        return _require_sha256(info.field_name, value)

    @model_validator(mode="after")
    def _valid(self) -> AdjudicationReviewerKitManifestV1:
        if not self.clips:
            raise ValueError("adjudication reviewer kit requires clips")
        expected_ids = tuple(f"item-{index:04d}" for index in range(1, len(self.clips) + 1))
        if tuple(item.neutral_clip_id for item in self.clips) != expected_ids:
            raise ValueError("adjudication reviewer clips must use canonical neutral order")
        if len({item.clip_binding_hash for item in self.clips}) != len(self.clips):
            raise ValueError("adjudication reviewer clips contain duplicate bindings")
        if self.manifest_hash != _artifact_hash(self, field="manifest_hash", kind=self._HASH_KIND):
            raise ValueError("adjudication reviewer kit manifest_hash mismatch")
        return self

    @classmethod
    def build(
        cls,
        *,
        kit_id: str,
        annotation_packet_hash: str,
        clips: Sequence[AdjudicationReviewerClipV1],
    ) -> AdjudicationReviewerKitManifestV1:
        payload = {
            "schema_version": 1,
            "kit_id": kit_id,
            "annotation_packet_hash": annotation_packet_hash,
            "reviewer_role": "candidate_blind_third_person_adjudicator",
            "candidate_outputs_hidden": True,
            "qc_outputs_hidden": True,
            "first_pass_identities_hidden": True,
            "reference_scope": "spelling_only",
            "human_identity_authority": "external_collection_process",
            "response_schema_id": "third-person-adjudication-response-batch-v1",
            "clips": tuple(clips),
        }
        return cls(
            **payload,
            manifest_hash=_prospective_hash(payload, kind=cls._HASH_KIND),
        )

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)


class AdjudicationSpanMarkV1(_StrictFrozenModel):
    label_id: str
    kind: Literal["entity", "code_switch", "numeric"]
    token_start: int
    token_end: int

    @field_validator("label_id")
    @classmethod
    def _label_id(cls, value: str) -> str:
        return _require_safe_id("label_id", value)

    @model_validator(mode="after")
    def _valid(self) -> AdjudicationSpanMarkV1:
        if self.token_start < 0 or self.token_end <= self.token_start:
            raise ValueError("adjudication span token interval is invalid")
        return self


class AdjudicationOmissionMarkV1(_StrictFrozenModel):
    label_id: str
    token_start: int
    token_end: int
    severity: Literal["material", "critical"] = "material"

    @field_validator("label_id")
    @classmethod
    def _label_id(cls, value: str) -> str:
        return _require_safe_id("label_id", value)

    @model_validator(mode="after")
    def _valid(self) -> AdjudicationOmissionMarkV1:
        if self.token_start < 0 or self.token_end <= self.token_start:
            raise ValueError("adjudication omission token interval is invalid")
        return self


class AdjudicationResponseAnswerV1(_StrictFrozenModel):
    neutral_clip_id: str
    first_pass_submission_hashes: tuple[str, str]
    spelling_authority_uses: tuple[SpellingAuthorityUse, ...]
    final_expected_outcome: Literal["accepted", "needs_review"]
    final_text: str | None
    metric_coverage: GoldMetricCoverageV1 = GoldMetricCoverageV1()
    span_labels: tuple[AdjudicationSpanMarkV1, ...] = ()
    omission_labels: tuple[AdjudicationOmissionMarkV1, ...] = ()

    @field_validator("neutral_clip_id")
    @classmethod
    def _neutral_clip_id(cls, value: str) -> str:
        return _require_safe_id("neutral_clip_id", value)

    @field_validator("first_pass_submission_hashes")
    @classmethod
    def _submission_hashes(cls, value: tuple[str, str]) -> tuple[str, str]:
        for digest in value:
            _require_sha256("first_pass_submission_hash", digest)
        if len(set(value)) != 2:
            raise ValueError("adjudication response requires two distinct submissions")
        return value

    @model_validator(mode="after")
    def _valid(self) -> AdjudicationResponseAnswerV1:
        if self.final_expected_outcome == "accepted":
            if not self.final_text:
                raise ValueError("accepted adjudication response requires text")
        elif (
            self.final_text is not None
            or self.span_labels
            or self.omission_labels
            or self.spelling_authority_uses
        ):
            raise ValueError("needs_review adjudication response cannot assert spoken text")
        if self.final_expected_outcome == "needs_review" and any(
            self.metric_coverage.model_dump().values()
        ):
            raise ValueError(
                "needs_review adjudication response cannot attest text-label coverage"
            )
        label_ids = [item.label_id for item in (*self.span_labels, *self.omission_labels)]
        if len(set(label_ids)) != len(label_ids):
            raise ValueError("adjudication response label_id values must be unique")
        return self


class AdjudicationResponseBatchV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    reviewer_kit_manifest_hash: str
    adjudicator_id: str
    reviewer_attestation: Literal["human_candidate_blind_third_person_adjudication"] = (
        "human_candidate_blind_third_person_adjudication"
    )
    candidate_outputs_hidden: Literal[True] = True
    qc_outputs_hidden: Literal[True] = True
    reference_scope: Literal["spelling_only"] = "spelling_only"
    answers: tuple[AdjudicationResponseAnswerV1, ...]

    @field_validator("reviewer_kit_manifest_hash")
    @classmethod
    def _manifest_hash(cls, value: str) -> str:
        return _require_sha256("reviewer_kit_manifest_hash", value)

    @field_validator("adjudicator_id")
    @classmethod
    def _adjudicator_id(cls, value: str) -> str:
        return _require_safe_id("adjudicator_id", value)


def _read_regular_file(path: Path, *, label: str) -> bytes:
    value = Path(path)
    if value.is_symlink() or not value.is_file():
        raise ValueError(f"{label} must be an existing regular file")
    return value.read_bytes()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _load_strict_json_model(raw: bytes, model: type[BaseModel], *, label: str) -> BaseModel:
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
        if not isinstance(value, dict):
            raise ValueError("top-level JSON must be an object")
        # ``model_validate_json`` preserves strict scalar checking while
        # accepting JSON arrays for frozen tuple fields.  The separate parse
        # above exists solely to reject duplicate keys and non-finite values,
        # which JSON decoders otherwise silently normalise.
        return model.model_validate_json(raw)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and "duplicate JSON key" in str(exc):
            raise
        raise ValueError(f"invalid {label}") from exc


def _load_exact_model(raw: bytes, model: type[BaseModel], *, label: str) -> BaseModel:
    value = _load_strict_json_model(raw, model, label=label)
    if canonical_json_bytes(value) != raw:
        raise ValueError(f"{label} bytes are not exact canonical JSON")
    return value


def _assert_forbidden_hashes_absent(
    raw: bytes, forbidden_candidate_hashes: Iterable[str], *, label: str
) -> None:
    for digest in forbidden_candidate_hashes:
        _require_sha256("forbidden candidate hash", digest)
        if digest.encode("ascii") in raw:
            raise ValueError(f"{label} contains a forbidden candidate artifact hash")


def _publish_new_directory(output_dir: Path, populate: Any) -> None:
    destination = Path(output_dir)
    if destination.exists() or destination.is_symlink():
        raise ValueError("reviewer output directory must not already exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.reviewer-kit-", dir=destination.parent)
    )
    try:
        populate(temporary)
        os.rename(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def load_first_pass_reviewer_kit(
    source: bytes | bytearray | Path,
) -> FirstPassReviewerKitManifestV1:
    raw = Path(source).read_bytes() if isinstance(source, Path) else bytes(source)
    return _load_exact_model(  # type: ignore[return-value]
        raw,
        FirstPassReviewerKitManifestV1,
        label="first-pass reviewer kit manifest",
    )


def _first_pass_response_template(manifest: FirstPassReviewerKitManifestV1) -> bytes:
    return canonical_json_bytes(
        {
            "schema_version": 1,
            "reviewer_kit_manifest_hash": manifest.manifest_hash,
            "annotator_id": "",
            "reviewer_attestation": "human_audio_only_first_pass",
            "candidate_outputs_hidden": True,
            "qc_outputs_hidden": True,
            "reference_material_hidden": True,
            "answers": [
                {
                    "neutral_clip_id": item.neutral_clip_id,
                    "outcome": "needs_review",
                    "text": None,
                }
                for item in manifest.clips
            ],
        }
    )


def prepare_first_pass_reviewer_kit(
    *,
    annotation_packet_path: Path,
    clip_audio_paths: Sequence[Path],
    output_dir: Path,
    kit_id: str,
    forbidden_candidate_hashes: Iterable[str] = (),
) -> FirstPassReviewerKitManifestV1:
    """Export one neutral audio-only kit without scanning its episode root."""

    packet = load_annotation_packet(Path(annotation_packet_path))
    candidate_hashes = tuple(forbidden_candidate_hashes)
    verify_annotation_packet_blinding(packet, candidate_hashes)
    if len(clip_audio_paths) != len(packet.clips):
        raise ValueError("clip audio count differs from annotation packet")
    loaded: list[bytes] = []
    neutral: list[FirstPassReviewerClipV1] = []
    for ordinal, (clip, path) in enumerate(
        zip(packet.clips, clip_audio_paths, strict=True), start=1
    ):
        raw = _read_regular_file(Path(path), label="clip audio")
        if len(raw) != clip.clip_audio_size_bytes or hashlib.sha256(raw).hexdigest() != (
            clip.clip_audio_hash
        ):
            raise ValueError("clip audio bytes differ from annotation packet binding")
        loaded.append(raw)
        neutral.append(
            FirstPassReviewerClipV1(
                neutral_clip_id=f"item-{ordinal:04d}",
                start_ms=clip.start_ms,
                end_ms=clip.end_ms,
                audio_relpath=f"audio/{ordinal:04d}.wav",
                clip_binding_hash=clip.binding_hash,
                clip_audio_sha256=clip.clip_audio_hash,
                clip_audio_size_bytes=clip.clip_audio_size_bytes,
            )
        )
    manifest = FirstPassReviewerKitManifestV1.build(
        kit_id=kit_id,
        annotation_packet_hash=packet.packet_hash,
        clips=neutral,
    )
    manifest_raw = manifest.canonical_bytes()
    template_raw = _first_pass_response_template(manifest)
    _assert_forbidden_hashes_absent(manifest_raw, candidate_hashes, label="reviewer manifest")
    _assert_forbidden_hashes_absent(template_raw, candidate_hashes, label="response template")

    def populate(root: Path) -> None:
        audio_root = root / "audio"
        audio_root.mkdir()
        (root / "reviewer-kit.v1.json").write_bytes(manifest_raw)
        (root / "first-pass.response.template.v1.json").write_bytes(template_raw)
        for item, raw in zip(manifest.clips, loaded, strict=True):
            (root / item.audio_relpath).write_bytes(raw)

    _publish_new_directory(Path(output_dir), populate)
    return manifest


def compile_first_pass_responses(
    *,
    annotation_packet_path: Path,
    reviewer_kit_manifest_path: Path,
    response_path: Path,
    output_dir: Path,
) -> tuple[AudioOnlyTranscriptSubmission, ...]:
    """Compile a human batch response into strict custody import artifacts."""

    packet = load_annotation_packet(Path(annotation_packet_path))
    manifest = load_first_pass_reviewer_kit(Path(reviewer_kit_manifest_path))
    if manifest.annotation_packet_hash != packet.packet_hash:
        raise ValueError("reviewer kit differs from annotation packet")
    response_raw = _read_regular_file(Path(response_path), label="first-pass response")
    response = _load_strict_json_model(
        response_raw,
        FirstPassResponseBatchV1,
        label="first-pass response batch",
    )
    assert isinstance(response, FirstPassResponseBatchV1)
    if response.reviewer_kit_manifest_hash != manifest.manifest_hash:
        raise ValueError("first-pass response differs from reviewer kit")
    expected_neutral_ids = tuple(item.neutral_clip_id for item in manifest.clips)
    observed_neutral_ids = tuple(item.neutral_clip_id for item in response.answers)
    if observed_neutral_ids != expected_neutral_ids:
        raise ValueError("first-pass responses must cover every reviewer clip in order")
    packet_by_binding = {item.binding_hash: item for item in packet.clips}
    if len(packet_by_binding) != len(packet.clips):
        raise ValueError("annotation packet contains duplicate clip bindings")
    submissions: list[AudioOnlyTranscriptSubmission] = []
    for reviewer_clip, answer in zip(manifest.clips, response.answers, strict=True):
        clip = packet_by_binding.get(reviewer_clip.clip_binding_hash)
        if (
            clip is None
            or clip.start_ms != reviewer_clip.start_ms
            or clip.end_ms != reviewer_clip.end_ms
            or clip.clip_audio_hash != reviewer_clip.clip_audio_sha256
            or clip.clip_audio_size_bytes != reviewer_clip.clip_audio_size_bytes
        ):
            raise ValueError("reviewer clip binding differs from annotation packet")
        submissions.append(
            AudioOnlyTranscriptSubmission.build(
                submission_id=(
                    f"{manifest.kit_id}-{response.annotator_id}-{reviewer_clip.neutral_clip_id}"
                ),
                annotation_packet_hash=packet.packet_hash,
                clip=clip,
                annotator_id=response.annotator_id,
                outcome=answer.outcome,
                text=answer.text,
                tokens=() if answer.text is None else tuple(answer.text),
            )
        )

    def populate(root: Path) -> None:
        for ordinal, value in enumerate(submissions, start=1):
            path = root / (f"{ordinal:04d}.{value.submission_hash}.audio-only-submission.json")
            path.write_bytes(canonical_json_bytes(value))

    _publish_new_directory(Path(output_dir), populate)
    return tuple(submissions)


def _load_audio_only_submission(path: Path) -> AudioOnlyTranscriptSubmission:
    raw = _read_regular_file(Path(path), label="audio-only submission")
    return _load_exact_model(  # type: ignore[return-value]
        raw,
        AudioOnlyTranscriptSubmission,
        label="audio-only submission",
    )


def _group_complete_first_passes(
    packet: TranscriptAnnotationPacket,
    submission_paths: Sequence[Path],
) -> tuple[
    dict[str, tuple[AudioOnlyTranscriptSubmission, AudioOnlyTranscriptSubmission]],
    tuple[str, str],
]:
    if len(submission_paths) != len(packet.clips) * 2:
        raise ValueError("adjudication requires exactly two submissions for every clip")
    loaded = tuple(_load_audio_only_submission(Path(path)) for path in submission_paths)
    if len({item.submission_hash for item in loaded}) != len(loaded):
        raise ValueError("adjudication submission input contains duplicate artifacts")
    clip_by_id = {item.clip_id: item for item in packet.clips}
    grouped: dict[str, list[AudioOnlyTranscriptSubmission]] = {}
    for item in loaded:
        expected = clip_by_id.get(item.clip.clip_id)
        if item.annotation_packet_hash != packet.packet_hash or item.clip != expected:
            raise ValueError("audio-only submission differs from annotation packet")
        grouped.setdefault(item.clip.clip_id, []).append(item)
    participant_ids = tuple(sorted({item.annotator_id for item in loaded}))
    if len(participant_ids) != 2:
        raise ValueError("formal first passes require exactly two distinct annotators")
    result: dict[str, tuple[AudioOnlyTranscriptSubmission, AudioOnlyTranscriptSubmission]] = {}
    for clip in packet.clips:
        values = sorted(grouped.get(clip.clip_id, ()), key=lambda item: item.annotator_id)
        if len(values) != 2 or tuple(item.annotator_id for item in values) != participant_ids:
            raise ValueError("every clip requires the same two distinct first-pass annotators")
        result[clip.clip_id] = (values[0], values[1])
    return result, (participant_ids[0], participant_ids[1])


def load_adjudication_reviewer_kit(
    source: bytes | bytearray | Path,
) -> AdjudicationReviewerKitManifestV1:
    raw = Path(source).read_bytes() if isinstance(source, Path) else bytes(source)
    return _load_exact_model(  # type: ignore[return-value]
        raw,
        AdjudicationReviewerKitManifestV1,
        label="adjudication reviewer kit manifest",
    )


def _adjudication_response_template(manifest: AdjudicationReviewerKitManifestV1) -> bytes:
    return canonical_json_bytes(
        {
            "schema_version": 1,
            "reviewer_kit_manifest_hash": manifest.manifest_hash,
            "adjudicator_id": "",
            "reviewer_attestation": "human_candidate_blind_third_person_adjudication",
            "candidate_outputs_hidden": True,
            "qc_outputs_hidden": True,
            "reference_scope": "spelling_only",
            "answers": [
                {
                    "neutral_clip_id": item.neutral_clip_id,
                    "first_pass_submission_hashes": [
                        value.submission_hash for value in item.first_passes
                    ],
                    "spelling_authority_uses": [],
                    "final_expected_outcome": "needs_review",
                    "final_text": None,
                    "metric_coverage": GoldMetricCoverageV1().model_dump(mode="json"),
                    "span_labels": [],
                    "omission_labels": [],
                }
                for item in manifest.clips
            ],
        }
    )


def prepare_adjudication_reviewer_kit(
    *,
    annotation_packet_path: Path,
    clip_audio_paths: Sequence[Path],
    submission_paths: Sequence[Path],
    output_dir: Path,
    kit_id: str,
    forbidden_candidate_hashes: Iterable[str] = (),
) -> AdjudicationReviewerKitManifestV1:
    """Export audio plus two identity-redacted human passes for a third person."""

    packet = load_annotation_packet(Path(annotation_packet_path))
    candidate_hashes = tuple(forbidden_candidate_hashes)
    verify_annotation_packet_blinding(packet, candidate_hashes)
    grouped, _ = _group_complete_first_passes(packet, submission_paths)
    if len(clip_audio_paths) != len(packet.clips):
        raise ValueError("clip audio count differs from annotation packet")
    loaded_audio: list[bytes] = []
    reviewer_clips: list[AdjudicationReviewerClipV1] = []
    for ordinal, (clip, audio_path) in enumerate(
        zip(packet.clips, clip_audio_paths, strict=True), start=1
    ):
        raw = _read_regular_file(Path(audio_path), label="clip audio")
        if len(raw) != clip.clip_audio_size_bytes or hashlib.sha256(raw).hexdigest() != (
            clip.clip_audio_hash
        ):
            raise ValueError("clip audio bytes differ from annotation packet binding")
        loaded_audio.append(raw)
        passes = grouped[clip.clip_id]
        reviewer_clips.append(
            AdjudicationReviewerClipV1(
                neutral_clip_id=f"item-{ordinal:04d}",
                start_ms=clip.start_ms,
                end_ms=clip.end_ms,
                audio_relpath=f"audio/{ordinal:04d}.wav",
                clip_binding_hash=clip.binding_hash,
                clip_audio_sha256=clip.clip_audio_hash,
                clip_audio_size_bytes=clip.clip_audio_size_bytes,
                first_passes=tuple(
                    AdjudicationFirstPassViewV1(
                        pass_id="pass-a" if index == 0 else "pass-b",
                        submission_hash=item.submission_hash,
                        outcome=item.outcome,
                        text=item.text,
                        tokens=item.tokens,
                    )
                    for index, item in enumerate(passes)
                ),  # type: ignore[arg-type]
            )
        )
    manifest = AdjudicationReviewerKitManifestV1.build(
        kit_id=kit_id,
        annotation_packet_hash=packet.packet_hash,
        clips=reviewer_clips,
    )
    manifest_raw = manifest.canonical_bytes()
    template_raw = _adjudication_response_template(manifest)
    _assert_forbidden_hashes_absent(
        manifest_raw, candidate_hashes, label="adjudication reviewer manifest"
    )
    _assert_forbidden_hashes_absent(
        template_raw, candidate_hashes, label="adjudication response template"
    )

    def populate(root: Path) -> None:
        audio_root = root / "audio"
        audio_root.mkdir()
        (root / "reviewer-kit.v1.json").write_bytes(manifest_raw)
        (root / "adjudication.response.template.v1.json").write_bytes(template_raw)
        for item, raw in zip(manifest.clips, loaded_audio, strict=True):
            (root / item.audio_relpath).write_bytes(raw)

    _publish_new_directory(Path(output_dir), populate)
    return manifest


def _gold_tokens(neutral_clip_id: str, text: str) -> tuple[GoldToken, ...]:
    """Pinned reviewer tokenisation: one Unicode scalar, exact order, no loss."""

    return tuple(
        GoldToken(token_id=f"{neutral_clip_id}-token-{index:04d}", text=character)
        for index, character in enumerate(text, start=1)
    )


def _span_token_ids(
    tokens: Sequence[GoldToken], *, start: int, end: int, label: str
) -> tuple[str, ...]:
    if start < 0 or end <= start or end > len(tokens):
        raise ValueError(f"{label} token interval escapes adjudicated text")
    return tuple(item.token_id for item in tokens[start:end])


def compile_adjudication_responses(
    *,
    annotation_packet_path: Path,
    reviewer_kit_manifest_path: Path,
    submission_paths: Sequence[Path],
    response_path: Path,
    output_dir: Path,
) -> tuple[TranscriptAdjudicationRecord, ...]:
    """Compile a third human's batch into exact custody adjudication artifacts."""

    packet = load_annotation_packet(Path(annotation_packet_path))
    manifest = load_adjudication_reviewer_kit(Path(reviewer_kit_manifest_path))
    if manifest.annotation_packet_hash != packet.packet_hash:
        raise ValueError("adjudication reviewer kit differs from annotation packet")
    grouped, annotator_ids = _group_complete_first_passes(packet, submission_paths)
    response = _load_strict_json_model(
        _read_regular_file(Path(response_path), label="adjudication response"),
        AdjudicationResponseBatchV1,
        label="adjudication response batch",
    )
    assert isinstance(response, AdjudicationResponseBatchV1)
    if response.reviewer_kit_manifest_hash != manifest.manifest_hash:
        raise ValueError("adjudication response differs from reviewer kit")
    if response.adjudicator_id in annotator_ids:
        raise ValueError("third adjudicator must differ from first-pass annotators")
    expected_ids = tuple(item.neutral_clip_id for item in manifest.clips)
    if tuple(item.neutral_clip_id for item in response.answers) != expected_ids:
        raise ValueError("adjudication responses must cover every reviewer clip in order")
    packet_by_binding = {item.binding_hash: item for item in packet.clips}
    records: list[TranscriptAdjudicationRecord] = []
    for reviewer_clip, answer in zip(manifest.clips, response.answers, strict=True):
        clip = packet_by_binding.get(reviewer_clip.clip_binding_hash)
        if (
            clip is None
            or clip.start_ms != reviewer_clip.start_ms
            or clip.end_ms != reviewer_clip.end_ms
            or clip.clip_audio_hash != reviewer_clip.clip_audio_sha256
            or clip.clip_audio_size_bytes != reviewer_clip.clip_audio_size_bytes
        ):
            raise ValueError("adjudication reviewer clip differs from annotation packet")
        passes = grouped[clip.clip_id]
        expected_views = tuple(
            AdjudicationFirstPassViewV1(
                pass_id="pass-a" if index == 0 else "pass-b",
                submission_hash=item.submission_hash,
                outcome=item.outcome,
                text=item.text,
                tokens=item.tokens,
            )
            for index, item in enumerate(passes)
        )
        if reviewer_clip.first_passes != expected_views:
            raise ValueError("adjudication reviewer kit differs from imported first passes")
        expected_hashes = tuple(item.submission_hash for item in passes)
        if answer.first_pass_submission_hashes != expected_hashes:
            raise ValueError("adjudication response does not bind the exact two first passes")
        tokens = (
            ()
            if answer.final_text is None
            else _gold_tokens(reviewer_clip.neutral_clip_id, answer.final_text)
        )
        span_labels = tuple(
            GoldSpanLabel(
                label_id=item.label_id,
                kind=item.kind,
                token_ids=_span_token_ids(
                    tokens,
                    start=item.token_start,
                    end=item.token_end,
                    label="span label",
                ),
                expected_text="".join(
                    token.text for token in tokens[item.token_start : item.token_end]
                ),
            )
            for item in answer.span_labels
        )
        omission_labels = tuple(
            GoldOmissionLabel(
                label_id=item.label_id,
                token_ids=_span_token_ids(
                    tokens,
                    start=item.token_start,
                    end=item.token_end,
                    label="omission label",
                ),
                expected_text="".join(
                    token.text for token in tokens[item.token_start : item.token_end]
                ),
                severity=item.severity,
            )
            for item in answer.omission_labels
        )
        records.append(
            TranscriptAdjudicationRecord.build(
                record_id=(
                    f"{manifest.kit_id}-{response.adjudicator_id}-{reviewer_clip.neutral_clip_id}"
                ),
                annotation_packet_hash=packet.packet_hash,
                clip=clip,
                first_pass_submission_hashes=expected_hashes,  # type: ignore[arg-type]
                adjudicator_id=response.adjudicator_id,
                spelling_authority_uses=answer.spelling_authority_uses,
                metric_coverage=answer.metric_coverage,
                final_expected_outcome=answer.final_expected_outcome,
                final_text=answer.final_text,
                final_tokens=tokens,
                final_span_labels=span_labels,
                final_omission_labels=omission_labels,
            )
        )

    def populate(root: Path) -> None:
        for ordinal, value in enumerate(records, start=1):
            path = root / (f"{ordinal:04d}.{value.record_hash}.transcript-adjudication.json")
            path.write_bytes(canonical_json_bytes(value))

    _publish_new_directory(Path(output_dir), populate)
    return tuple(records)


def _load_adjudication_record(path: Path) -> TranscriptAdjudicationRecord:
    raw = _read_regular_file(Path(path), label="transcript adjudication")
    return _load_exact_model(  # type: ignore[return-value]
        raw,
        TranscriptAdjudicationRecord,
        label="transcript adjudication",
    )


def _write_new_file(path: Path, payload: bytes) -> None:
    destination = Path(path)
    if destination.exists() or destination.is_symlink():
        raise ValueError("output file must not already exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # No-replace publication: another writer winning after the existence
        # check must make this operation fail rather than overwrite immutable
        # Gold bytes.
        os.rename(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def assemble_transcript_gold_suite(
    *,
    annotation_packet_path: Path,
    submission_paths: Sequence[Path],
    adjudication_paths: Sequence[Path],
    output_path: Path,
    suite_id: str,
    spelling_sources: Sequence[SpellingAuthoritySource] = (),
    forbidden_candidate_hashes: Iterable[str] = (),
) -> TranscriptGoldSuite:
    """Assemble complete 2+1 artifacts without deriving any human label."""

    packet = load_annotation_packet(Path(annotation_packet_path))
    candidate_hashes = tuple(forbidden_candidate_hashes)
    verify_annotation_packet_blinding(packet, candidate_hashes)
    grouped, annotator_ids = _group_complete_first_passes(packet, submission_paths)
    if len(adjudication_paths) != len(packet.clips):
        raise ValueError("gold assembly requires one adjudication for every clip")
    records = tuple(_load_adjudication_record(Path(path)) for path in adjudication_paths)
    if len({item.record_hash for item in records}) != len(records):
        raise ValueError("gold assembly contains duplicate adjudication artifacts")
    record_by_clip: dict[str, TranscriptAdjudicationRecord] = {}
    for record in records:
        expected_clip = next(
            (clip for clip in packet.clips if clip.clip_id == record.clip.clip_id), None
        )
        if (
            record.annotation_packet_hash != packet.packet_hash
            or record.clip != expected_clip
            or record.clip.clip_id in record_by_clip
        ):
            raise ValueError("adjudication differs from annotation packet or is duplicated")
        record_by_clip[record.clip.clip_id] = record
    adjudicator_ids = {item.adjudicator_id for item in records}
    if len(adjudicator_ids) != 1 or not adjudicator_ids.isdisjoint(annotator_ids):
        raise ValueError("formal gold requires one third adjudicator distinct from annotators")
    labels: list[GoldClipLabel] = []
    for clip in packet.clips:
        passes = grouped[clip.clip_id]
        record = record_by_clip.get(clip.clip_id)
        if record is None:
            raise ValueError("gold assembly is missing a clip adjudication")
        expected_hashes = tuple(item.submission_hash for item in passes)
        if record.first_pass_submission_hashes != expected_hashes:
            raise ValueError("adjudication does not bind exact first-pass submissions")
        labels.append(
            GoldClipLabel(
                clip_id=clip.clip_id,
                expected_outcome=record.final_expected_outcome,
                text=record.final_text,
                tokens=record.final_tokens,
                span_labels=record.final_span_labels,
                omission_labels=record.final_omission_labels,
                correction_labels=record.final_correction_labels,
                provenance=AdjudicationProvenance(
                    first_pass_submissions=passes,
                    adjudication_record=record,
                ),
            )
        )
    suite = TranscriptGoldSuite.build(
        suite_id=suite_id,
        annotation_packet=packet,
        complete=True,
        spelling_sources=spelling_sources,
        labels=labels,
        forbidden_candidate_hashes=candidate_hashes,
    )
    _write_new_file(Path(output_path), suite.canonical_bytes())
    return suite


ReviewerKitKind = Literal["first-pass", "adjudication"]


def verify_reviewer_kit_directory(
    root: Path,
    *,
    kind: ReviewerKitKind,
    forbidden_candidate_hashes: Iterable[str] = (),
) -> FirstPassReviewerKitManifestV1 | AdjudicationReviewerKitManifestV1:
    """Freshly replay exact kit metadata, allow-list, audio bytes, and leakage scan."""

    kit_root = Path(root)
    if kit_root.is_symlink() or not kit_root.is_dir():
        raise ValueError("reviewer kit root must be an existing regular directory")
    if kind == "first-pass":
        manifest: FirstPassReviewerKitManifestV1 | AdjudicationReviewerKitManifestV1 = (
            load_first_pass_reviewer_kit(kit_root / "reviewer-kit.v1.json")
        )
        template_name = "first-pass.response.template.v1.json"
        expected_template = _first_pass_response_template(manifest)
    elif kind == "adjudication":
        manifest = load_adjudication_reviewer_kit(kit_root / "reviewer-kit.v1.json")
        template_name = "adjudication.response.template.v1.json"
        expected_template = _adjudication_response_template(manifest)
    else:
        raise ValueError(f"unsupported reviewer kit kind: {kind!r}")
    expected_files = {
        "reviewer-kit.v1.json",
        template_name,
        *(item.audio_relpath for item in manifest.clips),
    }
    observed_files: set[str] = set()
    for path in kit_root.rglob("*"):
        if path.is_symlink():
            raise ValueError("reviewer kit cannot contain symbolic links")
        if path.is_file():
            observed_files.add(path.relative_to(kit_root).as_posix())
    if observed_files != expected_files:
        raise ValueError("reviewer kit contains missing or unexpected files")
    template_raw = _read_regular_file(kit_root / template_name, label="response template")
    if template_raw != expected_template:
        raise ValueError("reviewer response template bytes differ from manifest")
    candidate_hashes = tuple(forbidden_candidate_hashes)
    _assert_forbidden_hashes_absent(
        manifest.canonical_bytes(), candidate_hashes, label="reviewer manifest"
    )
    _assert_forbidden_hashes_absent(
        template_raw, candidate_hashes, label="reviewer response template"
    )
    for item in manifest.clips:
        raw = _read_regular_file(kit_root / item.audio_relpath, label="reviewer clip audio")
        if len(raw) != item.clip_audio_size_bytes or hashlib.sha256(raw).hexdigest() != (
            item.clip_audio_sha256
        ):
            raise ValueError("reviewer clip audio bytes differ from manifest")
    return manifest


__all__ = [
    "AdjudicationFirstPassViewV1",
    "AdjudicationOmissionMarkV1",
    "AdjudicationResponseAnswerV1",
    "AdjudicationResponseBatchV1",
    "AdjudicationReviewerClipV1",
    "AdjudicationReviewerKitManifestV1",
    "AdjudicationSpanMarkV1",
    "FirstPassResponseAnswerV1",
    "FirstPassResponseBatchV1",
    "FirstPassReviewerClipV1",
    "FirstPassReviewerKitManifestV1",
    "assemble_transcript_gold_suite",
    "compile_adjudication_responses",
    "compile_first_pass_responses",
    "load_adjudication_reviewer_kit",
    "load_first_pass_reviewer_kit",
    "prepare_adjudication_reviewer_kit",
    "prepare_first_pass_reviewer_kit",
    "verify_reviewer_kit_directory",
]
