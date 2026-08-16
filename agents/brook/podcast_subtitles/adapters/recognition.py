"""Replayable Recognition Adapters and strict foreign word-Evidence import.

V1 silently skipped unaligned tokens.  V2 deliberately rejects missing,
non-finite, zero-length, overlapping, or schema-drifted word records so Evidence
cannot look complete when the source artifact was not.  Every exported
Recognizer either exposes a measured executable identity plus raw-byte replay,
or fails closed before provider work.
"""

from __future__ import annotations

import importlib.metadata
import io
import json
import math
import os
import platform
import tempfile
import unicodedata
import wave
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from shared.schemas.podcast_subtitles_v2 import (
    ArtifactDigest,
    EvidenceToken,
    RecognitionEvidence,
)

from ..hashing import canonical_json_bytes, hash_file, hash_object, sha256_bytes
from ..ports import (
    AdapterInputError,
    AdapterIntegrityError,
    AdapterUnavailableError,
    RecognitionModelIdentity,
    RecognitionRequest,
)
from ..recognition_run import (
    RecognitionRunFinalizationV1,
    RecognitionRunIntegrityError,
    RecognitionRunPlanV1,
    RecognitionRunRepository,
    build_recognition_audio_binding,
    build_recognition_chunk_plan,
    build_recognition_chunk_planner,
    build_recognition_request_binding,
    build_recognition_run_failure,
    build_recognition_run_plan,
    snapshot_recognition_model_identity,
)
from .snapshot_identity import SnapshotIdentityError, measure_huggingface_snapshot
from .timing_grouping import (
    FORCED_ALIGNMENT_GROUPING_POLICY,
    TimedObservation,
    TimedObservationGroupingReceipt,
    build_timed_observation_grouping,
)

TimestampUnit = Literal["seconds", "milliseconds"]
WhisperXRunner = Callable[[Path, RecognitionRequest], Mapping[str, object]]
Qwen3ASRRunner = Callable[[Path, RecognitionRequest], Mapping[str, object]]

_TOP_LEVEL_FIELDS = frozenset({"audio", "model", "language", "words", "segments"})
_SEGMENT_FIELDS = frozenset({"id", "start", "end", "text", "speaker", "words", "chars"})
_WORD_FIELDS = frozenset(
    {
        "word",
        "text",
        "start",
        "end",
        "confidence",
        "score",
        "probability",
        "speaker",
    }
)
_IMPORT_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "normalized_audio_sha256",
        "normalized_audio_size_bytes",
        "normalized_audio_duration_ms",
        "asr_adapter",
        "asr_model",
        "asr_config_hash",
        "words_artifact_sha256",
        "timestamp_unit",
    }
)
_HEX_DIGITS = frozenset("0123456789abcdef")


def _sha256(value: object, *, location: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _HEX_DIGITS for character in value)
    ):
        raise AdapterInputError(f"{location} must be a lowercase SHA-256 digest")
    return value


def _identity_text(value: object, *, location: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise AdapterInputError(f"{location} must be a non-blank trimmed string")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise AdapterInputError(f"{location} must not contain control characters")
    return value


def _integer(value: object, *, location: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        comparator = "positive" if minimum == 1 else f">= {minimum}"
        raise AdapterInputError(f"{location} must be an integer that is {comparator}")
    return value


@dataclass(frozen=True, slots=True)
class WordsJsonImportManifest:
    """Exact provenance required before foreign word timings become Evidence."""

    schema_version: int
    normalized_audio_sha256: str
    normalized_audio_size_bytes: int
    normalized_audio_duration_ms: int
    asr_adapter: str
    asr_model: str
    asr_config_hash: str
    words_artifact_sha256: str
    timestamp_unit: TimestampUnit

    @classmethod
    def from_payload(cls, payload: object) -> WordsJsonImportManifest:
        if not isinstance(payload, Mapping):
            raise AdapterInputError("words import manifest must be a JSON object")
        _ensure_known_fields(payload, _IMPORT_MANIFEST_FIELDS, location="words import manifest")
        missing = _IMPORT_MANIFEST_FIELDS - set(payload)
        if missing:
            raise AdapterInputError(
                f"words import manifest is missing required fields: {sorted(missing)!r}"
            )
        schema_version = _integer(
            payload["schema_version"], location="manifest.schema_version", minimum=1
        )
        if schema_version != 1:
            raise AdapterInputError(
                f"unsupported words import manifest schema_version: {schema_version!r}"
            )
        timestamp_unit = payload["timestamp_unit"]
        if timestamp_unit not in {"seconds", "milliseconds"}:
            raise AdapterInputError("manifest.timestamp_unit must be 'seconds' or 'milliseconds'")
        return cls(
            schema_version=schema_version,
            normalized_audio_sha256=_sha256(
                payload["normalized_audio_sha256"],
                location="manifest.normalized_audio_sha256",
            ),
            normalized_audio_size_bytes=_integer(
                payload["normalized_audio_size_bytes"],
                location="manifest.normalized_audio_size_bytes",
                minimum=1,
            ),
            normalized_audio_duration_ms=_integer(
                payload["normalized_audio_duration_ms"],
                location="manifest.normalized_audio_duration_ms",
                minimum=1,
            ),
            asr_adapter=_identity_text(payload["asr_adapter"], location="manifest.asr_adapter"),
            asr_model=_identity_text(payload["asr_model"], location="manifest.asr_model"),
            asr_config_hash=_sha256(
                payload["asr_config_hash"], location="manifest.asr_config_hash"
            ),
            words_artifact_sha256=_sha256(
                payload["words_artifact_sha256"],
                location="manifest.words_artifact_sha256",
            ),
            timestamp_unit=timestamp_unit,
        )

    def semantic_identity(self) -> dict[str, object]:
        """Return the portable fields that are folded into Evidence config identity."""

        return {
            "schema_version": self.schema_version,
            "normalized_audio_sha256": self.normalized_audio_sha256,
            "normalized_audio_size_bytes": self.normalized_audio_size_bytes,
            "normalized_audio_duration_ms": self.normalized_audio_duration_ms,
            "asr_adapter": self.asr_adapter,
            "asr_model": self.asr_model,
            "asr_config_hash": self.asr_config_hash,
            "words_artifact_sha256": self.words_artifact_sha256,
            "timestamp_unit": self.timestamp_unit,
        }


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is forbidden: {key!r}")
        result[key] = value
    return result


def _read_strict_json(
    path: Path, *, artifact_label: str = "recognition artifact"
) -> tuple[object, ArtifactDigest]:
    if not path.is_file():
        raise AdapterInputError(f"{artifact_label} is not a file: {path}")
    raw_bytes = path.read_bytes()
    payload = _parse_strict_json_bytes(raw_bytes, artifact_label=artifact_label)
    resolved = path.resolve()
    return payload, ArtifactDigest(
        uri=resolved.as_uri(),
        sha256=sha256_bytes(raw_bytes),
        size_bytes=len(raw_bytes),
    )


def _parse_strict_json_bytes(raw_bytes: bytes, *, artifact_label: str) -> object:
    """Parse stored bytes without accepting duplicate keys or non-finite values."""

    try:
        text = raw_bytes.decode("utf-8-sig")
        return json.loads(
            text,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_strict_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise AdapterInputError(f"malformed {artifact_label} JSON: {exc}") from exc


def _ensure_known_fields(
    value: Mapping[str, object], allowed: frozenset[str], *, location: str
) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise AdapterInputError(f"{location} has unsupported fields: {sorted(unknown)!r}")


def _flatten_words(payload: object) -> tuple[list[Mapping[str, object]], dict[str, object]]:
    if isinstance(payload, list):
        raw_words = payload
        metadata: dict[str, object] = {}
    elif isinstance(payload, Mapping):
        _ensure_known_fields(payload, _TOP_LEVEL_FIELDS, location="recognition envelope")
        has_words = "words" in payload
        has_segments = "segments" in payload
        if has_words == has_segments:
            raise AdapterInputError(
                "recognition envelope must contain exactly one of 'words' or 'segments'"
            )
        metadata = {
            key: value for key, value in payload.items() if key in {"audio", "model", "language"}
        }
        if has_words:
            raw_words = payload["words"]
        else:
            segments = payload["segments"]
            if not isinstance(segments, list):
                raise AdapterInputError("recognition 'segments' must be a list")
            raw_words = []
            for segment_index, segment in enumerate(segments):
                if not isinstance(segment, Mapping):
                    raise AdapterInputError(
                        f"segments[{segment_index}] must be an object, got {type(segment).__name__}"
                    )
                _ensure_known_fields(
                    segment,
                    _SEGMENT_FIELDS,
                    location=f"segments[{segment_index}]",
                )
                words = segment.get("words")
                if not isinstance(words, list):
                    raise AdapterInputError(f"segments[{segment_index}].words must be a list")
                raw_words.extend(words)
    else:
        raise AdapterInputError(
            "recognition JSON must be a list or an object containing words/segments"
        )

    if not isinstance(raw_words, list) or not raw_words:
        raise AdapterInputError("recognition artifact requires at least one word")
    typed_words: list[Mapping[str, object]] = []
    for index, word in enumerate(raw_words):
        if not isinstance(word, Mapping):
            raise AdapterInputError(f"words[{index}] must be an object")
        _ensure_known_fields(word, _WORD_FIELDS, location=f"words[{index}]")
        typed_words.append(word)
    return typed_words, metadata


def _finite_number(value: object, *, location: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdapterInputError(f"{location} must be a number")
    converted = float(value)
    if not math.isfinite(converted):
        raise AdapterInputError(f"{location} must be finite")
    return converted


def _milliseconds(value: object, *, location: str, unit: TimestampUnit) -> int:
    number = _finite_number(value, location=location)
    factor = 1000 if unit == "seconds" else 1
    return round(number * factor)


def _word_text(word: Mapping[str, object], *, index: int) -> str:
    candidates = [word[key] for key in ("word", "text") if key in word]
    if not candidates:
        raise AdapterInputError(f"words[{index}] requires 'word' or 'text'")
    if any(not isinstance(value, str) for value in candidates):
        raise AdapterInputError(f"words[{index}] text must be a string")
    if len(set(candidates)) != 1:
        raise AdapterInputError(f"words[{index}] has conflicting word/text values")
    text = candidates[0]
    if not text.strip():
        raise AdapterInputError(f"words[{index}] text must not be blank")
    return text


def _word_confidence(word: Mapping[str, object], *, index: int) -> float | None:
    present = [word[key] for key in ("confidence", "score", "probability") if key in word]
    if not present or all(value is None for value in present):
        return None
    values = [
        _finite_number(value, location=f"words[{index}].confidence")
        for value in present
        if value is not None
    ]
    if len({round(value, 12) for value in values}) != 1:
        raise AdapterInputError(f"words[{index}] has conflicting confidence fields")
    confidence = values[0]
    if not 0.0 <= confidence <= 1.0:
        raise AdapterInputError(f"words[{index}] confidence must be between 0 and 1")
    return confidence


def _normalized_audio_hash(request: RecognitionRequest) -> str:
    audio = Path(request.normalized_audio)
    if not audio.is_file():
        raise AdapterInputError(f"normalized audio is not a file: {audio}")
    actual = hash_file(audio)
    if (
        request.expected_normalized_audio_hash is not None
        and request.expected_normalized_audio_hash != actual
    ):
        raise AdapterIntegrityError(
            "normalized audio hash does not match RecognitionRequest: "
            f"expected {request.expected_normalized_audio_hash}, got {actual}"
        )
    return actual


def _persist_raw_output(
    *,
    request: RecognitionRequest,
    adapter_stem: str,
    raw: Mapping[str, object],
) -> ArtifactDigest:
    """Atomically persist exact canonical provider output in generation storage."""

    try:
        raw_bytes = canonical_json_bytes(raw)
    except (TypeError, ValueError) as exc:
        raise AdapterInputError(f"{adapter_stem} output is not content-addressable: {exc}") from exc
    if request.raw_output_dir is None:
        raise AdapterInputError(
            f"{adapter_stem} RecognitionRequest requires generation-owned raw_output_dir"
        )
    raw_output_dir = Path(request.raw_output_dir)
    raw_output_dir.mkdir(parents=True, exist_ok=True)
    raw_hash = sha256_bytes(raw_bytes)
    raw_path = raw_output_dir / f"{adapter_stem.casefold()}-{raw_hash}.json"
    if not raw_path.exists():
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=raw_output_dir,
                prefix=f".{raw_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                temporary.write(raw_bytes)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, raw_path)
            temporary_name = None
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)
    persisted = raw_path.read_bytes()
    if persisted != raw_bytes or sha256_bytes(persisted) != raw_hash:
        raise AdapterIntegrityError(
            f"raw Evidence artifact collision at {raw_path}: "
            f"{sha256_bytes(persisted)} != {raw_hash}"
        )
    return ArtifactDigest(
        uri=raw_path.resolve().as_uri(),
        sha256=raw_hash,
        size_bytes=len(raw_bytes),
    )


def _evidence_from_payload(
    payload: object,
    *,
    request: RecognitionRequest,
    raw_output: ArtifactDigest,
    adapter: str,
    model: str,
    config: Mapping[str, object],
    timestamp_unit: TimestampUnit,
    normalized_audio_duration_ms: int | None = None,
    expected_normalized_audio_size_bytes: int | None = None,
    expected_normalized_audio_hash: str | None = None,
) -> RecognitionEvidence:
    words, metadata = _flatten_words(payload)
    raw_language = metadata.get("language")
    if raw_language is not None and not isinstance(raw_language, str):
        raise AdapterInputError("recognition envelope language must be a string")
    language = raw_language or request.language_hint
    if not language or not language.strip():
        raise AdapterInputError(
            "Recognition Evidence requires an explicit provider language or language_hint"
        )
    raw_output_hash = raw_output.sha256
    normalized_hash = _normalized_audio_hash(request)
    if (
        expected_normalized_audio_hash is not None
        and normalized_hash != expected_normalized_audio_hash
    ):
        raise AdapterIntegrityError(
            "normalized audio does not match words import manifest: "
            f"expected {expected_normalized_audio_hash}, got {normalized_hash}"
        )
    if expected_normalized_audio_size_bytes is not None:
        actual_audio_size = Path(request.normalized_audio).stat().st_size
        if actual_audio_size != expected_normalized_audio_size_bytes:
            raise AdapterIntegrityError(
                "normalized audio size does not match words import manifest: "
                f"expected {expected_normalized_audio_size_bytes}, got {actual_audio_size}"
            )
    collisions: Counter[tuple[object, ...]] = Counter()
    tokens: list[EvidenceToken] = []
    for index, word in enumerate(words):
        if "start" not in word or "end" not in word:
            raise AdapterInputError(
                f"words[{index}] requires both start and end; V2 never skips unaligned tokens"
            )
        text = _word_text(word, index=index)
        start_ms = _milliseconds(
            word["start"], location=f"words[{index}].start", unit=timestamp_unit
        )
        end_ms = _milliseconds(word["end"], location=f"words[{index}].end", unit=timestamp_unit)
        if start_ms < 0 or end_ms <= start_ms:
            raise AdapterInputError(
                f"words[{index}] has invalid rounded range {start_ms}..{end_ms} ms"
            )
        if normalized_audio_duration_ms is not None and end_ms > normalized_audio_duration_ms:
            raise AdapterIntegrityError(
                f"words[{index}] ends at {end_ms}ms beyond manifest normalized audio "
                f"duration {normalized_audio_duration_ms}ms"
            )
        speaker_value = word.get("speaker")
        if speaker_value is not None and not isinstance(speaker_value, (str, int)):
            raise AdapterInputError(f"words[{index}].speaker must be a string or integer")
        speaker = str(speaker_value) if speaker_value is not None else None
        confidence = _word_confidence(word, index=index)
        identity = (normalized_hash, adapter, start_ms, end_ms, text, speaker)
        ordinal = collisions[identity]
        collisions[identity] += 1
        token_id = (
            "ev_"
            + hash_object(
                {
                    "normalized_audio_hash": normalized_hash,
                    "adapter": adapter,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "text": text,
                    "speaker": speaker,
                    "collision_ordinal": ordinal,
                }
            )[:32]
        )
        tokens.append(
            EvidenceToken(
                id=token_id,
                text=text,
                start_ms=start_ms,
                end_ms=end_ms,
                confidence=confidence,
                speaker=speaker,
                evidence_refs=(f"raw:{raw_output_hash}",),
            )
        )
    try:
        return RecognitionEvidence(
            episode_id=request.episode_id,
            invocation_id=request.invocation_id,
            adapter=adapter,
            model=model,
            language=language,
            config_hash=hash_object(config),
            raw_output=raw_output,
            raw_output_hash=raw_output_hash,
            normalized_audio_hash=normalized_hash,
            tokens=tuple(tokens),
        )
    except ValidationError as exc:
        raise AdapterInputError(f"recognition evidence violates V2 invariants: {exc}") from exc


class WordsJsonRecognizerAdapter:
    """Replayable import of foreign word timings through an immutable manifest.

    The manifest attests the imported bytes and upstream labels; it does *not*
    prove an immutable upstream model or aligner snapshot.  The executable
    identity therefore says so explicitly instead of upgrading those labels
    into production provenance.
    """

    ADAPTER_NAME = "words-json-v1-import"
    ADAPTER_VERSION = 2
    PARSER_SCHEMA_VERSION = 2

    def __init__(
        self,
        words_json: str | Path,
        import_manifest: str | Path,
        *,
        expected_model: str | None = None,
        timestamp_unit: TimestampUnit | None = None,
    ) -> None:
        if timestamp_unit is not None and timestamp_unit not in {"seconds", "milliseconds"}:
            raise ValueError(f"unsupported timestamp unit: {timestamp_unit!r}")
        if expected_model is not None and (
            not isinstance(expected_model, str)
            or not expected_model
            or expected_model != expected_model.strip()
        ):
            raise ValueError("expected_model must be a non-blank trimmed string")
        self._path = Path(words_json)
        self._manifest_path = Path(import_manifest)
        self._expected_model = expected_model
        self._timestamp_unit = timestamp_unit
        self._source_hash = hash_file(Path(__file__))
        self._identity_cache: RecognitionModelIdentity | None = None

    def _load_manifest(
        self,
    ) -> tuple[WordsJsonImportManifest, ArtifactDigest]:
        payload, digest = _read_strict_json(
            self._manifest_path,
            artifact_label="words import manifest",
        )
        manifest = WordsJsonImportManifest.from_payload(payload)
        if self._expected_model is not None and self._expected_model != manifest.asr_model:
            raise AdapterIntegrityError(
                "recognition model does not match words import manifest: "
                f"expected {self._expected_model!r}, got {manifest.asr_model!r}"
            )
        if self._timestamp_unit is not None and self._timestamp_unit != manifest.timestamp_unit:
            raise AdapterIntegrityError(
                "timestamp unit does not match words import manifest: "
                f"expected {self._timestamp_unit!r}, got {manifest.timestamp_unit!r}"
            )
        return manifest, digest

    def _runtime_components(
        self,
        *,
        manifest: WordsJsonImportManifest,
        manifest_digest: ArtifactDigest,
    ) -> tuple[tuple[str, str], ...]:
        return tuple(
            sorted(
                {
                    "import_manifest_sha256": manifest_digest.sha256,
                    "import_parser": "strict-json-v2",
                    "python_implementation": platform.python_implementation(),
                    "python_version": platform.python_version(),
                    "upstream_adapter_claim": manifest.asr_adapter,
                    "upstream_config_claim": manifest.asr_config_hash,
                    "words_artifact_sha256": manifest.words_artifact_sha256,
                }.items()
            )
        )

    def _config(
        self,
        *,
        manifest: WordsJsonImportManifest,
        manifest_digest: ArtifactDigest,
        runtime_components: tuple[tuple[str, str], ...],
    ) -> dict[str, object]:
        return {
            "adapter": self.ADAPTER_NAME,
            "adapter_version": self.ADAPTER_VERSION,
            "adapter_source_hash": self._source_hash,
            "parser_schema": self.PARSER_SCHEMA_VERSION,
            "expected_model": self._expected_model,
            "expected_timestamp_unit": self._timestamp_unit,
            "import_manifest": manifest.semantic_identity(),
            "import_manifest_sha256": manifest_digest.sha256,
            "runtime_components": runtime_components,
            "upstream_model_snapshot_attested": False,
            "upstream_aligner_snapshot_attested": False,
        }

    def _measure_identity(
        self,
    ) -> tuple[
        WordsJsonImportManifest,
        ArtifactDigest,
        RecognitionModelIdentity,
        dict[str, object],
    ]:
        if hash_file(Path(__file__)) != self._source_hash:
            raise AdapterIntegrityError("Words JSON Adapter source changed after initialization")
        manifest, manifest_digest = self._load_manifest()
        runtime_components = self._runtime_components(
            manifest=manifest,
            manifest_digest=manifest_digest,
        )
        config = self._config(
            manifest=manifest,
            manifest_digest=manifest_digest,
            runtime_components=runtime_components,
        )
        identity = RecognitionModelIdentity(
            adapter_name=self.ADAPTER_NAME,
            adapter_version=str(self.ADAPTER_VERSION),
            model=manifest.asr_model,
            model_version="unverified-import-manifest-v1",
            aligner="upstream-word-timing-import",
            aligner_version="unverified-import-manifest-v1",
            runtime_components=runtime_components,
            runtime_hash=hash_object({"runtime_components": runtime_components}),
            adapter_code_hash=self._source_hash,
            config_hash=hash_object(config),
            execution_mode="import",
        )
        if self._identity_cache is not None and identity != self._identity_cache:
            raise AdapterIntegrityError(
                "Words JSON import manifest or executable identity changed after initialization"
            )
        self._identity_cache = identity
        return manifest, manifest_digest, identity, config

    @property
    def identity(self) -> RecognitionModelIdentity:
        """Return the exact importer identity, rechecking manifest bytes for drift."""

        return self._measure_identity()[2]

    @staticmethod
    def _validate_envelope_model(
        payload: object,
        *,
        manifest: WordsJsonImportManifest,
    ) -> None:
        _, metadata = _flatten_words(payload)
        raw_model = metadata.get("model")
        if raw_model is not None and not isinstance(raw_model, str):
            raise AdapterInputError("recognition envelope model must be a string")
        if raw_model is not None and raw_model != manifest.asr_model:
            raise AdapterIntegrityError(
                "recognition envelope model does not match words import manifest: "
                f"expected {manifest.asr_model!r}, got {raw_model!r}"
            )

    def _evidence_from_import(
        self,
        payload: object,
        *,
        request: RecognitionRequest,
        raw_output: ArtifactDigest,
        manifest: WordsJsonImportManifest,
        config: Mapping[str, object],
    ) -> RecognitionEvidence:
        self._validate_envelope_model(payload, manifest=manifest)
        return _evidence_from_payload(
            payload,
            request=request,
            raw_output=raw_output,
            adapter=self.ADAPTER_NAME,
            model=manifest.asr_model,
            config=config,
            timestamp_unit=manifest.timestamp_unit,
            normalized_audio_duration_ms=manifest.normalized_audio_duration_ms,
            expected_normalized_audio_size_bytes=manifest.normalized_audio_size_bytes,
            expected_normalized_audio_hash=manifest.normalized_audio_sha256,
        )

    def recognize(self, request: RecognitionRequest) -> RecognitionEvidence:
        payload, raw_output = _read_strict_json(self._path)
        manifest, _manifest_digest, _identity, config = self._measure_identity()
        if raw_output.sha256 != manifest.words_artifact_sha256:
            raise AdapterIntegrityError(
                "words artifact does not match import manifest: "
                f"expected {manifest.words_artifact_sha256}, got {raw_output.sha256}"
            )
        return self._evidence_from_import(
            payload,
            request=request,
            raw_output=raw_output,
            manifest=manifest,
            config=config,
        )

    def verify(
        self,
        evidence: RecognitionEvidence,
        *,
        request: RecognitionRequest,
        raw_output: bytes,
    ) -> RecognitionEvidence:
        manifest, _manifest_digest, _identity, config = self._measure_identity()
        actual_digest = sha256_bytes(raw_output)
        if (
            evidence.raw_output.sha256 != actual_digest
            or evidence.raw_output_hash != actual_digest
            or evidence.raw_output.size_bytes != len(raw_output)
        ):
            raise AdapterIntegrityError("stored Words JSON raw output digest mismatch")
        if actual_digest != manifest.words_artifact_sha256:
            raise AdapterIntegrityError(
                "stored Words JSON raw output does not match the import manifest"
            )
        payload = _parse_strict_json_bytes(
            raw_output,
            artifact_label="stored Words JSON recognition artifact",
        )
        replayed = self._evidence_from_import(
            payload,
            request=request,
            raw_output=evidence.raw_output,
            manifest=manifest,
            config=config,
        )
        if replayed != evidence:
            raise AdapterIntegrityError("stored Words JSON Recognition Evidence does not re-derive")
        return replayed


class _WhisperXAudioBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)


class _WhisperXRecognitionEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1]
    kind: Literal["whisperx_custom_runner_evidence"]
    identity_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    language_hint: str | None
    normalized_audio: _WhisperXAudioBinding
    provider_output: dict[str, object]


class WhisperXRecognizerAdapter:
    """Replayable WhisperX custom-runner Adapter.

    The built-in WhisperX loader accepts mutable model labels and chooses an
    aligner from the detected language.  That path cannot currently measure an
    immutable model/aligner snapshot, so V2 rejects it during identity preflight.
    A trusted custom runner is usable only when its model, aligner, runtime,
    code, and execution identities are all explicit.
    """

    ADAPTER_NAME = "whisperx"
    ADAPTER_VERSION = 2
    ENVELOPE_SCHEMA_VERSION = 1

    def __init__(
        self,
        *,
        model: str = "large-v3",
        device: str = "cuda",
        compute_type: str = "float16",
        batch_size: int = 16,
        runner: WhisperXRunner | None = None,
        model_version: str | None = None,
        aligner: str | None = None,
        aligner_version: str | None = None,
        runner_runtime_components: Mapping[str, str] | None = None,
        runner_code_hash: str | None = None,
        runner_execution_mode: Literal["fixture", "local", "other"] | None = None,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        for label, value in (
            ("model", model),
            ("device", device),
            ("compute_type", compute_type),
        ):
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError(f"WhisperX {label} must be non-blank and trimmed")
        custom_identity = (
            model_version,
            aligner,
            aligner_version,
            runner_runtime_components,
            runner_code_hash,
            runner_execution_mode,
        )
        if runner is None and any(value is not None for value in custom_identity):
            raise ValueError("custom WhisperX identity fields require a custom runner")
        if runner is not None and any(value is None for value in custom_identity):
            raise ValueError(
                "custom WhisperX runner requires explicit model, aligner, runtime, "
                "code, and execution identity"
            )
        self._model = model
        self._device = device
        self._compute_type = compute_type
        self._batch_size = batch_size
        self._runner = runner
        self._source_hash = hash_file(Path(__file__))
        self._identity_cache: RecognitionModelIdentity | None = None
        self._model_version = model_version
        self._aligner = aligner
        self._aligner_version = aligner_version
        self._runner_code_hash = runner_code_hash
        self._runner_execution_mode = runner_execution_mode
        self._runner_runtime_components: tuple[tuple[str, str], ...] | None = None
        if runner is not None:
            assert model_version is not None
            assert aligner is not None
            assert aligner_version is not None
            assert runner_runtime_components is not None
            assert runner_code_hash is not None
            assert runner_execution_mode is not None
            for label, value in (
                ("model_version", model_version),
                ("aligner", aligner),
                ("aligner_version", aligner_version),
            ):
                if not isinstance(value, str) or not value or value != value.strip():
                    raise ValueError(f"custom WhisperX {label} must be non-blank and trimmed")
            if (
                not isinstance(runner_code_hash, str)
                or len(runner_code_hash) != 64
                or any(character not in _HEX_DIGITS for character in runner_code_hash)
            ):
                raise ValueError("custom WhisperX runner_code_hash must be lowercase SHA-256")
            if runner_execution_mode not in {"fixture", "local", "other"}:
                raise ValueError("unsupported custom WhisperX runner execution mode")
            if (
                not isinstance(runner_runtime_components, Mapping)
                or not runner_runtime_components
                or any(
                    not isinstance(name, str)
                    or not name
                    or name != name.strip()
                    or not isinstance(value, str)
                    or not value
                    or value != value.strip()
                    for name, value in runner_runtime_components.items()
                )
            ):
                raise ValueError(
                    "custom WhisperX runtime components require non-blank trimmed strings"
                )
            runtime_components = tuple(sorted(runner_runtime_components.items()))
            code_hash = hash_object(
                {
                    "adapter_source_hash": self._source_hash,
                    "custom_runner_code_hash": runner_code_hash,
                }
            )
            self._runner_runtime_components = runtime_components
            config = self._config(
                runtime_components=runtime_components,
                runner_code_hash=runner_code_hash,
                execution_mode=runner_execution_mode,
            )
            self._identity_cache = RecognitionModelIdentity(
                adapter_name=self.ADAPTER_NAME,
                adapter_version=str(self.ADAPTER_VERSION),
                model=self._model,
                model_version=model_version,
                aligner=aligner,
                aligner_version=aligner_version,
                runtime_components=runtime_components,
                runtime_hash=hash_object({"runtime_components": runtime_components}),
                adapter_code_hash=code_hash,
                config_hash=hash_object(config),
                execution_mode=runner_execution_mode,
            )

    def _assert_source_identity(self) -> None:
        if hash_file(Path(__file__)) != self._source_hash:
            raise AdapterIntegrityError("WhisperX Adapter source changed after initialization")

    def _config(
        self,
        *,
        runtime_components: tuple[tuple[str, str], ...],
        runner_code_hash: str,
        execution_mode: Literal["fixture", "local", "other"],
    ) -> dict[str, object]:
        return {
            "adapter": self.ADAPTER_NAME,
            "adapter_version": self.ADAPTER_VERSION,
            "adapter_source_hash": self._source_hash,
            "model": self._model,
            "model_version": self._model_version,
            "aligner": self._aligner,
            "aligner_version": self._aligner_version,
            "device": self._device,
            "compute_type": self._compute_type,
            "batch_size": self._batch_size,
            "asr_options": {
                "condition_on_previous_text": False,
                "compression_ratio_threshold": 2.4,
                "no_speech_threshold": 0.6,
            },
            "timestamp_semantics": "whisperx_aligned_words_seconds_v1",
            "envelope_schema_version": self.ENVELOPE_SCHEMA_VERSION,
            "runtime_components": runtime_components,
            "runner_code_hash": runner_code_hash,
            "runner_execution_mode": execution_mode,
        }

    @property
    def adapter_config_hash(self) -> str:
        identity = self.identity
        return identity.config_hash

    @property
    def identity(self) -> RecognitionModelIdentity:
        self._assert_source_identity()
        if self._identity_cache is None:
            raise AdapterUnavailableError(
                "WhisperX production identity is unavailable: the built-in loader cannot "
                "prove immutable model and aligner snapshots; configure a custom runner "
                "with explicit executable identity"
            )
        return self._identity_cache

    def _run_whisperx(self, audio_path: Path, request: RecognitionRequest) -> Mapping[str, object]:
        """Legacy loader retained for a future measured-snapshot implementation.

        ``recognize`` deliberately cannot reach this method until a production
        identity implementation can pin and measure both snapshots.
        """

        try:
            import whisperx
        except ImportError as exc:  # pragma: no cover - optional production dependency
            raise AdapterUnavailableError(
                "WhisperXRecognizerAdapter requires the optional 'transcription' runtime"
            ) from exc

        asr_options: dict[str, object] = {
            "condition_on_previous_text": False,
            "compression_ratio_threshold": 2.4,
            "no_speech_threshold": 0.6,
        }
        model = whisperx.load_model(
            self._model,
            device=self._device,
            compute_type=self._compute_type,
            language=request.language_hint,
            asr_options=asr_options,
        )
        audio = whisperx.load_audio(str(audio_path))
        transcription = model.transcribe(
            audio,
            batch_size=self._batch_size,
            language=request.language_hint,
        )
        segments = transcription.get("segments") or []
        if not segments:
            raise AdapterInputError("WhisperX returned no recognition segments")
        language = transcription.get("language") or request.language_hint
        if not language:
            raise AdapterInputError("WhisperX did not return a language for alignment")
        align_model, align_metadata = whisperx.load_align_model(
            language_code=language,
            device=self._device,
        )
        aligned = whisperx.align(
            segments,
            align_model,
            align_metadata,
            audio,
            self._device,
            return_char_alignments=False,
        )
        return {
            "language": language,
            "segments": aligned.get("segments") or [],
        }

    def _build_envelope(
        self,
        *,
        request: RecognitionRequest,
        provider_output: Mapping[str, object],
    ) -> dict[str, object]:
        audio_path = Path(request.normalized_audio)
        return {
            "schema_version": self.ENVELOPE_SCHEMA_VERSION,
            "kind": "whisperx_custom_runner_evidence",
            "identity_hash": self.identity.content_hash,
            "language_hint": request.language_hint,
            "normalized_audio": {
                "sha256": _normalized_audio_hash(request),
                "size_bytes": audio_path.stat().st_size,
            },
            "provider_output": dict(provider_output),
        }

    @staticmethod
    def _parse_envelope(raw_output: bytes) -> _WhisperXRecognitionEnvelope:
        payload = _parse_strict_json_bytes(
            raw_output,
            artifact_label="stored WhisperX Evidence envelope",
        )
        try:
            return _WhisperXRecognitionEnvelope.model_validate(payload)
        except ValidationError as exc:
            raise AdapterInputError(f"invalid WhisperX Evidence envelope: {exc}") from exc

    def _validate_envelope(
        self,
        envelope: _WhisperXRecognitionEnvelope,
        *,
        request: RecognitionRequest,
    ) -> None:
        audio_path = Path(request.normalized_audio)
        actual_hash = _normalized_audio_hash(request)
        if (
            envelope.normalized_audio.sha256 != actual_hash
            or envelope.normalized_audio.size_bytes != audio_path.stat().st_size
        ):
            raise AdapterIntegrityError("WhisperX envelope normalized audio mismatch")
        if envelope.language_hint != request.language_hint:
            raise AdapterIntegrityError("WhisperX envelope language hint mismatch")
        if envelope.identity_hash != self.identity.content_hash:
            raise AdapterIntegrityError("WhisperX envelope executable identity mismatch")

    def _evidence_from_envelope(
        self,
        envelope: _WhisperXRecognitionEnvelope,
        *,
        request: RecognitionRequest,
        raw_output: ArtifactDigest,
    ) -> RecognitionEvidence:
        provider = envelope.provider_output
        slim: dict[str, object] = {"segments": provider.get("segments")}
        if "language" in provider:
            slim["language"] = provider["language"]
        assert self._model_version is not None
        assert self._runner_runtime_components is not None
        assert self._runner_code_hash is not None
        assert self._runner_execution_mode is not None
        config = self._config(
            runtime_components=self._runner_runtime_components,
            runner_code_hash=self._runner_code_hash,
            execution_mode=self._runner_execution_mode,
        )
        return _evidence_from_payload(
            slim,
            request=request,
            raw_output=raw_output,
            adapter=self.ADAPTER_NAME,
            model=f"{self._model}@{self._model_version}",
            config=config,
            timestamp_unit="seconds",
            expected_normalized_audio_size_bytes=envelope.normalized_audio.size_bytes,
            expected_normalized_audio_hash=envelope.normalized_audio.sha256,
        )

    def recognize(self, request: RecognitionRequest) -> RecognitionEvidence:
        # Hash verification happens before an injected or production runner can
        # spend GPU time on a stale generation.
        _normalized_audio_hash(request)
        self.identity
        assert self._runner is not None
        try:
            provider_output = self._runner(Path(request.normalized_audio), request)
        except (AdapterInputError, AdapterIntegrityError, AdapterUnavailableError):
            raise
        except Exception as exc:
            raise AdapterErrorFromProvider("WhisperX recognition failed") from exc
        if not isinstance(provider_output, Mapping):
            raise AdapterInputError("WhisperX runner must return a mapping")
        raw = self._build_envelope(
            request=request,
            provider_output=provider_output,
        )
        raw_output = _persist_raw_output(
            request=request,
            adapter_stem="whisperx",
            raw=raw,
        )
        raw_bytes = canonical_json_bytes(raw)
        envelope = self._parse_envelope(raw_bytes)
        self._validate_envelope(envelope, request=request)
        return self._evidence_from_envelope(
            envelope,
            request=request,
            raw_output=raw_output,
        )

    def verify(
        self,
        evidence: RecognitionEvidence,
        *,
        request: RecognitionRequest,
        raw_output: bytes,
    ) -> RecognitionEvidence:
        self.identity
        actual_digest = sha256_bytes(raw_output)
        if (
            evidence.raw_output.sha256 != actual_digest
            or evidence.raw_output_hash != actual_digest
            or evidence.raw_output.size_bytes != len(raw_output)
        ):
            raise AdapterIntegrityError("stored WhisperX raw envelope digest mismatch")
        envelope = self._parse_envelope(raw_output)
        self._validate_envelope(envelope, request=request)
        replayed = self._evidence_from_envelope(
            envelope,
            request=request,
            raw_output=evidence.raw_output,
        )
        if replayed != evidence:
            raise AdapterIntegrityError("stored WhisperX Recognition Evidence does not re-derive")
        return replayed


class _EnvelopeContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


_QWEN_ALIGNMENT_GROUPING_METHOD = FORCED_ALIGNMENT_GROUPING_POLICY.method
_QwenAlignmentGroupingReceipt = TimedObservationGroupingReceipt


def _build_qwen_alignment_grouping(
    observations: Sequence[Mapping[str, object]],
) -> _QwenAlignmentGroupingReceipt:
    try:
        typed = tuple(TimedObservation.model_validate(item, strict=True) for item in observations)
        return build_timed_observation_grouping(
            typed,
            policy=FORCED_ALIGNMENT_GROUPING_POLICY,
        )
    except (ValidationError, ValueError) as exc:
        raise AdapterIntegrityError(
            f"invalid Qwen forced-alignment observation sequence: {exc}"
        ) from exc


class _QwenAudioTopology(_EnvelopeContract):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)
    sample_rate_hz: int = Field(gt=0)
    channels: int = Field(gt=0)
    sample_width_bytes: int = Field(gt=0)
    frame_count: int = Field(gt=0)
    duration_ms: int = Field(gt=0)
    compression_type: str


class _QwenPlanner(_EnvelopeContract):
    version: Literal[1]
    derivation: Literal["pcm_wav_frame_slice_v1"]
    ownership: Literal["alignment_midpoint_in_half_open_owned_interval_v1"]
    seam_comparison: Literal["normalized_alignment_text_in_shared_context_v1"]
    owned_chunk_samples: int = Field(gt=0)
    seam_context_samples: int = Field(gt=0)


class _QwenAlignmentItem(_EnvelopeContract):
    id: str
    aligned_text: str
    text: str
    transcript_kept_indices: tuple[int, ...]
    local_start_seconds: float = Field(ge=0, allow_inf_nan=False)
    local_end_seconds: float = Field(gt=0, allow_inf_nan=False)
    local_start_sample: int = Field(ge=0)
    local_end_sample: int = Field(gt=0)
    global_start_sample: int = Field(ge=0)
    global_end_sample: int = Field(gt=0)

    @model_validator(mode="after")
    def _positive_ranges(self) -> _QwenAlignmentItem:
        if not self.id.strip() or not self.aligned_text or not self.text.strip():
            raise ValueError("Qwen alignment item requires non-blank ID and text")
        if self.aligned_text != _qwen_aligner_text(self.aligned_text):
            raise ValueError("Qwen raw alignment text violates the forced-aligner alphabet")
        if _qwen_aligner_text(self.text) != self.aligned_text:
            raise ValueError("Qwen projected text changes aligned lexical text")
        if any(
            not (character.isspace() or _qwen_aligner_keeps(character)) for character in self.text
        ):
            raise ValueError("Qwen Evidence text may retain whitespace but not punctuation")
        if (
            len(self.transcript_kept_indices) != len(self.aligned_text)
            or tuple(sorted(set(self.transcript_kept_indices))) != self.transcript_kept_indices
        ):
            raise ValueError("Qwen transcript kept indices must map each aligned scalar once")
        if self.local_end_seconds <= self.local_start_seconds:
            raise ValueError("Qwen alignment seconds must be positive")
        if self.local_end_sample <= self.local_start_sample:
            raise ValueError("Qwen alignment samples must be positive")
        if self.global_end_sample <= self.global_start_sample:
            raise ValueError("Qwen global alignment samples must be positive")
        return self


class _QwenSeparatorReceipt(_EnvelopeContract):
    text: str
    transcript_start_index: int = Field(ge=0)
    transcript_end_index: int = Field(gt=0)
    classification: Literal[
        "retained_orthographic_whitespace",
        "provider_only_separator",
    ]
    attached_alignment_index: int | None = Field(default=None, ge=0)
    left_alignment_index: int | None = Field(default=None, ge=0)
    right_alignment_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _valid_separator(self) -> _QwenSeparatorReceipt:
        if not self.text or self.transcript_end_index - self.transcript_start_index != len(
            self.text
        ):
            raise ValueError("Qwen separator range must exactly cover non-empty text")
        if any(_qwen_aligner_keeps(character) for character in self.text):
            raise ValueError("Qwen separator cannot contain aligner-kept characters")
        if self.classification == "retained_orthographic_whitespace":
            if not self.text.isspace() or self.attached_alignment_index is None:
                raise ValueError(
                    "retained Qwen orthographic whitespace requires a real left anchor"
                )
        elif self.attached_alignment_index is not None:
            raise ValueError("provider-only Qwen separators cannot receive timing anchors")
        return self


class _QwenChunkReceipt(_EnvelopeContract):
    id: str
    index: int = Field(ge=0)
    count: int = Field(gt=0)
    inference_start_sample: int = Field(ge=0)
    inference_end_sample: int = Field(gt=0)
    owned_start_sample: int = Field(ge=0)
    owned_end_sample: int = Field(gt=0)
    derived_audio_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    derived_audio_size_bytes: int = Field(gt=0)
    language: str
    transcript_text: str
    transcript_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    transcript_projection_rule: Literal["qwen_forced_aligner_scalar_separator_classification_v1"]
    provider_output: dict[str, object]
    alignment_items: tuple[_QwenAlignmentItem, ...]
    separators: tuple[_QwenSeparatorReceipt, ...]

    @model_validator(mode="after")
    def _ranges(self) -> _QwenChunkReceipt:
        if not self.id.strip() or not self.language.strip():
            raise ValueError("Qwen chunk requires non-blank ID and language")
        if not 0 <= self.index < self.count:
            raise ValueError("Qwen chunk index is outside its declared count")
        if self.inference_end_sample <= self.inference_start_sample:
            raise ValueError("Qwen inference chunk range must be positive")
        if self.owned_end_sample <= self.owned_start_sample:
            raise ValueError("Qwen owned chunk range must be positive")
        if not (
            self.inference_start_sample
            <= self.owned_start_sample
            < self.owned_end_sample
            <= self.inference_end_sample
        ):
            raise ValueError("Qwen inference chunk must contain its owned range")
        if len({item.id for item in self.alignment_items}) != len(self.alignment_items):
            raise ValueError("Qwen chunk alignment item IDs must be unique")
        if self.transcript_sha256 != sha256_bytes(self.transcript_text.encode("utf-8")):
            raise ValueError("Qwen chunk transcript digest mismatch")
        expected_items, expected_separators = _project_qwen_transcript(
            self.transcript_text,
            tuple(item.aligned_text for item in self.alignment_items),
        )
        for item, expected in zip(self.alignment_items, expected_items):
            if (
                item.text != expected["evidence_text"]
                or item.transcript_kept_indices != expected["transcript_kept_indices"]
            ):
                raise ValueError("Qwen transcript-to-alignment projection does not replay")
        if tuple(separator.model_dump(mode="json") for separator in self.separators) != (
            expected_separators
        ):
            raise ValueError("Qwen separator classification does not replay")
        return self


class _QwenSeamReceipt(_EnvelopeContract):
    id: str
    seam_sample: int = Field(gt=0)
    left_chunk_id: str
    right_chunk_id: str
    comparison_start_sample: int = Field(ge=0)
    comparison_end_sample: int = Field(gt=0)
    left_text: str
    right_text: str
    left_normalized: str
    right_normalized: str
    status: Literal["matched", "conflict"]


class _QwenMergedWord(_EnvelopeContract):
    word: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    source_chunk_id: str
    source_item_id: str
    ownership: Literal["alignment_midpoint_in_half_open_owned_interval_v1"]

    @model_validator(mode="after")
    def _positive_time(self) -> _QwenMergedWord:
        if not self.word.strip() or self.end_ms <= self.start_ms:
            raise ValueError("Qwen merged word requires non-blank text and positive time")
        return self


class _QwenRecognitionEnvelope(_EnvelopeContract):
    schema_version: Literal[3]
    kind: Literal["qwen3_asr_bounded_overlap_evidence"]
    identity_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    language: str
    normalized_audio: _QwenAudioTopology
    planner: _QwenPlanner
    chunks: tuple[_QwenChunkReceipt, ...]
    seams: tuple[_QwenSeamReceipt, ...]
    merged_words: tuple[_QwenMergedWord, ...]


@dataclass(frozen=True, slots=True)
class _WavTopology:
    sample_rate_hz: int
    channels: int
    sample_width_bytes: int
    frame_count: int
    compression_type: str


@dataclass(frozen=True, slots=True)
class _ChunkPlan:
    index: int
    count: int
    inference_start_sample: int
    inference_end_sample: int
    owned_start_sample: int
    owned_end_sample: int

    @property
    def id(self) -> str:
        return (
            f"qwen-chunk-{self.index:04d}-"
            f"{self.inference_start_sample}-{self.inference_end_sample}-"
            f"own-{self.owned_start_sample}-{self.owned_end_sample}"
        )


def _read_pcm_wav_topology(path: Path) -> _WavTopology:
    try:
        with wave.open(str(path), "rb") as reader:
            topology = _WavTopology(
                sample_rate_hz=reader.getframerate(),
                channels=reader.getnchannels(),
                sample_width_bytes=reader.getsampwidth(),
                frame_count=reader.getnframes(),
                compression_type=reader.getcomptype(),
            )
    except (EOFError, wave.Error) as exc:
        raise AdapterInputError(
            "Qwen3-ASR bounded Evidence requires an uncompressed PCM WAV normalized audio"
        ) from exc
    if (
        topology.compression_type != "NONE"
        or topology.sample_rate_hz <= 0
        or topology.channels <= 0
        or topology.sample_width_bytes <= 0
        or topology.frame_count <= 0
    ):
        raise AdapterInputError(
            "Qwen3-ASR bounded Evidence requires a positive uncompressed PCM WAV topology"
        )
    return topology


def _derive_pcm_wav_chunk(
    path: Path,
    *,
    topology: _WavTopology,
    start_sample: int,
    end_sample: int,
) -> bytes:
    if not 0 <= start_sample < end_sample <= topology.frame_count:
        raise AdapterIntegrityError("Qwen chunk derivation range exceeds normalized audio")
    try:
        with wave.open(str(path), "rb") as reader:
            reader.setpos(start_sample)
            frames = reader.readframes(end_sample - start_sample)
    except (EOFError, wave.Error) as exc:
        raise AdapterIntegrityError(
            "Qwen chunk derivation could not replay PCM WAV frames"
        ) from exc
    expected_size = (end_sample - start_sample) * topology.channels * topology.sample_width_bytes
    if len(frames) != expected_size:
        raise AdapterIntegrityError("Qwen chunk derivation returned incomplete PCM frames")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(topology.channels)
        writer.setsampwidth(topology.sample_width_bytes)
        writer.setframerate(topology.sample_rate_hz)
        writer.setcomptype(topology.compression_type, "not compressed")
        writer.writeframes(frames)
    return buffer.getvalue()


def _seam_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(
        character
        for character in normalized
        if not unicodedata.category(character).startswith(("P", "Z", "C"))
    )


_QWEN_TRANSCRIPT_PROJECTION_RULE = "qwen_forced_aligner_scalar_separator_classification_v1"


def _qwen_aligner_keeps(character: str) -> bool:
    """Mirror Qwen3ForceAlignProcessor.is_kept_char at the audited revision."""

    category = unicodedata.category(character)
    return character == "'" or category.startswith(("L", "N"))


def _qwen_aligner_text(text: str) -> str:
    return "".join(character for character in text if _qwen_aligner_keeps(character))


def _qwen_orthographic_alnum(character: str) -> bool:
    category = unicodedata.category(character)
    return category.startswith("N") or (
        category.startswith("L") and "LATIN" in unicodedata.name(character, "")
    )


def _project_qwen_transcript(
    transcript_text: str,
    aligned_texts: Sequence[str],
) -> tuple[tuple[dict[str, object], ...], tuple[dict[str, object], ...]]:
    """Project exact provider text onto aligner anchors without making new timing.

    The audited Qwen forced aligner removes every character except Unicode Letter,
    Number, and apostrophe before it emits timestamp items.  The provider transcript
    remains immutable provider evidence.  Only orthographic whitespace between
    Latin letters/numbers is retained in Recognition text; punctuation and every
    other stripped scalar stay provider-only.  Every scalar retains an exact
    mapping, and retained whitespace attaches to a real left alignment span without
    receiving a fabricated time range of its own.
    """

    if not transcript_text or not transcript_text.strip():
        raise AdapterInputError("bounded Qwen transcript_text must be non-blank")
    if not aligned_texts:
        raise AdapterInputError("bounded Qwen alignment requires at least one item")
    for index, aligned_text in enumerate(aligned_texts):
        if not isinstance(aligned_text, str) or not aligned_text:
            raise AdapterInputError(f"bounded Qwen alignment items[{index}] text must be non-empty")
        if aligned_text != _qwen_aligner_text(aligned_text):
            raise AdapterIntegrityError(
                "bounded Qwen alignment item contains characters the official forced aligner strips"
            )

    lexical_transcript = _qwen_aligner_text(transcript_text)
    lexical_alignment = "".join(aligned_texts)
    if lexical_alignment != lexical_transcript:
        raise AdapterIntegrityError(
            "bounded Qwen alignment has a lexical mismatch with transcript_text"
        )
    kept_indices = tuple(
        index for index, character in enumerate(transcript_text) if _qwen_aligner_keeps(character)
    )
    if not kept_indices:
        raise AdapterIntegrityError(
            "bounded Qwen transcript contains no alignable lexical characters"
        )

    lexical_owner: dict[int, int] = {}
    projection: list[dict[str, object]] = []
    lexical_cursor = 0
    for index, aligned_text in enumerate(aligned_texts):
        next_lexical_cursor = lexical_cursor + len(aligned_text)
        item_indices = kept_indices[lexical_cursor:next_lexical_cursor]
        for transcript_index in item_indices:
            lexical_owner[transcript_index] = index
        projection.append(
            {
                "alignment_index": index,
                "aligned_text": aligned_text,
                "transcript_kept_indices": item_indices,
            }
        )
        lexical_cursor = next_lexical_cursor

    nearest_left: list[int | None] = []
    left: int | None = None
    for index, character in enumerate(transcript_text):
        if _qwen_aligner_keeps(character):
            left = index
        nearest_left.append(left)
    nearest_right: list[int | None] = [None] * len(transcript_text)
    right: int | None = None
    for index in range(len(transcript_text) - 1, -1, -1):
        if _qwen_aligner_keeps(transcript_text[index]):
            right = index
        nearest_right[index] = right

    scalar_receipts: list[dict[str, object]] = []
    retained_by_alignment: dict[int, list[tuple[int, str]]] = {
        index: [] for index in range(len(aligned_texts))
    }
    for index, character in enumerate(transcript_text):
        if _qwen_aligner_keeps(character):
            continue
        left_index = nearest_left[index]
        right_index = nearest_right[index]
        left_owner = lexical_owner.get(left_index) if left_index is not None else None
        right_owner = lexical_owner.get(right_index) if right_index is not None else None
        retained = (
            character.isspace()
            and left_index is not None
            and right_index is not None
            and _qwen_orthographic_alnum(transcript_text[left_index])
            and _qwen_orthographic_alnum(transcript_text[right_index])
        )
        attached = left_owner if retained else None
        if attached is not None:
            retained_by_alignment[attached].append((index, character))
        scalar_receipts.append(
            {
                "text": character,
                "transcript_start_index": index,
                "transcript_end_index": index + 1,
                "classification": (
                    "retained_orthographic_whitespace" if retained else "provider_only_separator"
                ),
                "attached_alignment_index": attached,
                "left_alignment_index": left_owner,
                "right_alignment_index": right_owner,
            }
        )

    separators: list[dict[str, object]] = []
    for scalar in scalar_receipts:
        if (
            separators
            and separators[-1]["classification"] == scalar["classification"]
            and separators[-1]["attached_alignment_index"] == scalar["attached_alignment_index"]
            and separators[-1]["left_alignment_index"] == scalar["left_alignment_index"]
            and separators[-1]["right_alignment_index"] == scalar["right_alignment_index"]
            and separators[-1]["transcript_end_index"] == scalar["transcript_start_index"]
        ):
            separators[-1]["text"] = str(separators[-1]["text"]) + str(scalar["text"])
            separators[-1]["transcript_end_index"] = scalar["transcript_end_index"]
        else:
            separators.append(dict(scalar))

    for item in projection:
        alignment_index = int(item["alignment_index"])
        kept_positions = tuple(int(value) for value in item["transcript_kept_indices"])
        evidence_scalars = [(position, transcript_text[position]) for position in kept_positions]
        evidence_scalars.extend(retained_by_alignment[alignment_index])
        evidence_scalars.sort(key=lambda pair: pair[0])
        item["evidence_text"] = "".join(character for _, character in evidence_scalars)
        if _qwen_aligner_text(str(item["evidence_text"])) != item["aligned_text"]:
            raise AdapterIntegrityError(
                "bounded Qwen orthographic projection changed aligned lexical text"
            )
    return tuple(projection), tuple(separators)


class _QwenDeterministicRejection(AdapterIntegrityError):
    """Typed terminal reason found only while replaying a complete prefix."""

    def __init__(
        self,
        code: Literal["seam_conflict", "empty_owned_output", "owned_output_overlap"],
        message: str,
    ) -> None:
        self.failure_code = code
        super().__init__(message)


class Qwen3ASRRecognizerAdapter:
    """Replayable bounded Qwen3-ASR + ForcedAligner Adapter.

    Qwen's public long-audio call returns only a merged result, so its internal
    180-second seams cannot be audited after the fact.  V2 instead makes
    bounded calls whose inference windows overlap while their ownership ranges
    form one exact partition of the normalized PCM clock.  Both observations
    around every seam must agree before Evidence is emitted.
    """

    ADAPTER_NAME = "qwen3-asr-forced-alignment"
    ADAPTER_VERSION = 3
    ENVELOPE_SCHEMA_VERSION = 3
    RECOGNITION_RUN_NAMESPACE = "podcast-subtitle-v2/qwen3-asr-bounded-overlap-envelope/v3"

    def __init__(
        self,
        *,
        model: str = "Qwen/Qwen3-ASR-1.7B",
        model_revision: str,
        forced_aligner: str = "Qwen/Qwen3-ForcedAligner-0.6B",
        forced_aligner_revision: str,
        device: str = "cuda:0",
        dtype: str = "bfloat16",
        max_inference_batch_size: int = 8,
        max_new_tokens: int = 4096,
        local_files_only: bool = True,
        owned_chunk_seconds: int = 176,
        seam_context_ms: int = 2_000,
        runner: Qwen3ASRRunner | None = None,
        runner_runtime_components: Mapping[str, str] | None = None,
        runner_code_hash: str | None = None,
        runner_execution_mode: Literal["fixture", "local", "other"] | None = None,
        recognition_run_repository: RecognitionRunRepository | None = None,
        logical_namespace: str | None = None,
    ) -> None:
        identities = {
            "model": model,
            "model_revision": model_revision,
            "forced_aligner": forced_aligner,
            "forced_aligner_revision": forced_aligner_revision,
            "device": device,
            "dtype": dtype,
        }
        if any(not value or value != value.strip() for value in identities.values()):
            raise ValueError("Qwen3-ASR requires explicit non-blank model/runtime identities")
        if max_inference_batch_size < 1 or max_new_tokens < 1:
            raise ValueError("Qwen3-ASR batch size and max_new_tokens must be positive")
        if owned_chunk_seconds < 1 or seam_context_ms < 1:
            raise ValueError("Qwen3-ASR chunk and seam context must be positive")
        if owned_chunk_seconds * 1_000 + 2 * seam_context_ms > 180_000:
            raise ValueError(
                "Qwen bounded inference windows must not exceed the provider 180-second seam"
            )
        custom_identity_fields = (
            runner_runtime_components,
            runner_code_hash,
            runner_execution_mode,
        )
        if runner is None and any(value is not None for value in custom_identity_fields):
            raise ValueError("runner identity fields require a custom Qwen runner")
        if runner is not None and any(value is None for value in custom_identity_fields):
            raise ValueError(
                "custom Qwen runner requires explicit runtime, code, and execution identity"
            )
        if (recognition_run_repository is None) != (logical_namespace is None):
            raise ValueError(
                "recognition_run_repository and logical_namespace must be provided together"
            )
        if logical_namespace is not None and (
            not logical_namespace.strip() or logical_namespace != logical_namespace.strip()
        ):
            raise ValueError("Qwen Recognition logical_namespace must be non-blank and trimmed")
        self._model = model
        self._model_revision = model_revision
        self._forced_aligner = forced_aligner
        self._forced_aligner_revision = forced_aligner_revision
        self._device = device
        self._dtype = dtype
        self._max_inference_batch_size = max_inference_batch_size
        self._max_new_tokens = max_new_tokens
        self._local_files_only = local_files_only
        self._owned_chunk_seconds = owned_chunk_seconds
        self._seam_context_ms = seam_context_ms
        self._runner = runner
        self._recognition_run_repository = recognition_run_repository
        self._logical_namespace = logical_namespace
        self._source_hash = hash_file(Path(__file__))
        self._snapshot_identity_source_hash = hash_file(
            Path(__file__).with_name("snapshot_identity.py")
        )
        self._timing_grouping_source_hash = hash_file(
            Path(__file__).with_name("timing_grouping.py")
        )
        self._hashing_source_hash = hash_file(Path(__file__).parents[1] / "hashing.py")
        self._adapter_code_hash = hash_object(
            {
                "adapter_source_hash": self._source_hash,
                "hashing_source_hash": self._hashing_source_hash,
                "snapshot_identity_source_hash": self._snapshot_identity_source_hash,
                "timing_grouping_source_hash": self._timing_grouping_source_hash,
            }
        )
        self._identity_cache: RecognitionModelIdentity | None = None
        self._model_snapshot_path: Path | None = None
        self._forced_aligner_snapshot_path: Path | None = None
        self._model_snapshot_inventory_hash: str | None = None
        self._forced_aligner_snapshot_inventory_hash: str | None = None
        if runner is not None:
            assert runner_runtime_components is not None
            assert runner_code_hash is not None
            assert runner_execution_mode is not None
            runtime_components = tuple(sorted(runner_runtime_components.items()))
            self._identity_cache = self._make_identity(
                runtime_components=runtime_components,
                code_hash=hash_object(
                    {
                        "adapter_code_hash": self._adapter_code_hash,
                        "custom_runner_code_hash": runner_code_hash,
                    }
                ),
                execution_mode=runner_execution_mode,
            )

    @property
    def adapter_name(self) -> str:
        return self.ADAPTER_NAME

    @property
    def adapter_version(self) -> int:
        return self.ADAPTER_VERSION

    @property
    def adapter_config_hash(self) -> str:
        return hash_object(self._config())

    @property
    def identity(self) -> RecognitionModelIdentity:
        self._assert_source_identity()
        if self._identity_cache is None:
            self._identity_cache = self._measure_production_identity()
        return self._identity_cache

    def _assert_source_identity(self) -> None:
        current = {
            "adapter_source_hash": hash_file(Path(__file__)),
            "hashing_source_hash": hash_file(Path(__file__).parents[1] / "hashing.py"),
            "snapshot_identity_source_hash": hash_file(
                Path(__file__).with_name("snapshot_identity.py")
            ),
            "timing_grouping_source_hash": hash_file(
                Path(__file__).with_name("timing_grouping.py")
            ),
        }
        expected = {
            "adapter_source_hash": self._source_hash,
            "hashing_source_hash": self._hashing_source_hash,
            "snapshot_identity_source_hash": self._snapshot_identity_source_hash,
            "timing_grouping_source_hash": self._timing_grouping_source_hash,
        }
        if current != expected:
            raise AdapterIntegrityError(
                "Qwen3-ASR Adapter or an identity-critical dependency changed after initialization"
            )

    def _config(self) -> dict[str, object]:
        return {
            "adapter": self.ADAPTER_NAME,
            "adapter_version": self.ADAPTER_VERSION,
            "adapter_source_hash": self._source_hash,
            "hashing_source_hash": self._hashing_source_hash,
            "snapshot_identity_source_hash": self._snapshot_identity_source_hash,
            "timing_grouping_source_hash": self._timing_grouping_source_hash,
            "adapter_code_hash": self._adapter_code_hash,
            "model": self._model,
            "model_revision": self._model_revision,
            "forced_aligner": self._forced_aligner,
            "forced_aligner_revision": self._forced_aligner_revision,
            "device": self._device,
            "dtype": self._dtype,
            "max_inference_batch_size": self._max_inference_batch_size,
            "max_new_tokens": self._max_new_tokens,
            "local_files_only": self._local_files_only,
            "owned_chunk_seconds": self._owned_chunk_seconds,
            "seam_context_ms": self._seam_context_ms,
            "chunk_derivation": "pcm_wav_frame_slice_v1",
            "ownership": "alignment_midpoint_in_half_open_owned_interval_v1",
            "seam_comparison": "normalized_alignment_text_in_shared_context_v1",
            "timestamp_semantics": ("forced_aligner_exact_or_zero_duration_bounded_group_v1"),
            "envelope_schema_version": self.ENVELOPE_SCHEMA_VERSION,
        }

    def _make_identity(
        self,
        *,
        runtime_components: tuple[tuple[str, str], ...],
        code_hash: str,
        execution_mode: Literal["fixture", "local", "other"],
    ) -> RecognitionModelIdentity:
        return RecognitionModelIdentity(
            adapter_name=self.ADAPTER_NAME,
            adapter_version=str(self.ADAPTER_VERSION),
            model=self._model,
            model_version=self._model_revision,
            aligner=self._forced_aligner,
            aligner_version=self._forced_aligner_revision,
            runtime_components=runtime_components,
            runtime_hash=hash_object({"runtime_components": runtime_components}),
            adapter_code_hash=code_hash,
            config_hash=self.adapter_config_hash,
            execution_mode=execution_mode,
        )

    @staticmethod
    def _measure_local_snapshot(
        *,
        repo_id: str,
        revision: str,
        label: str,
    ) -> tuple[Path, str, str]:
        """Resolve one pinned Hugging Face snapshot without network or weight loading."""

        if len(revision) != 40 or any(character not in _HEX_DIGITS for character in revision):
            raise AdapterUnavailableError(
                f"Qwen3-ASR production {label} revision must be an immutable "
                "40-character lowercase commit SHA"
            )
        try:
            from huggingface_hub import snapshot_download

            snapshot = Path(
                snapshot_download(
                    repo_id=repo_id,
                    revision=revision,
                    local_files_only=True,
                )
            )
        except (ImportError, OSError, ValueError) as exc:
            raise AdapterUnavailableError(
                f"Qwen3-ASR pinned local {label} snapshot is unavailable: {repo_id}@{revision}"
            ) from exc
        if not snapshot.is_dir() or not (snapshot / "config.json").is_file():
            raise AdapterUnavailableError(
                f"Qwen3-ASR pinned local {label} snapshot is incomplete: "
                f"{repo_id}@{revision} has no config.json"
            )
        weight_files = tuple(
            sorted(
                (
                    *snapshot.rglob("*.safetensors"),
                    *snapshot.rglob("pytorch_model*.bin"),
                ),
                key=lambda path: path.as_posix(),
            )
        )
        if not weight_files or any(
            not path.is_file() or path.stat().st_size <= 0 for path in weight_files
        ):
            raise AdapterUnavailableError(
                f"Qwen3-ASR pinned local {label} snapshot is incomplete: "
                f"{repo_id}@{revision} has no readable model weights"
            )
        try:
            resolved_snapshot, _inventory, inventory_hash = measure_huggingface_snapshot(snapshot)
        except SnapshotIdentityError as exc:
            raise AdapterUnavailableError(
                f"Qwen3-ASR pinned local {label} snapshot identity failed: {exc}"
            ) from exc
        resolved_revision = snapshot.name if snapshot.parent.name == "snapshots" else revision
        if resolved_revision != revision:
            raise AdapterUnavailableError(
                f"Qwen3-ASR pinned local {label} snapshot resolved to an unexpected "
                f"revision: requested {revision}, found {resolved_revision}"
            )
        return resolved_snapshot, resolved_revision, inventory_hash

    def _measure_production_identity(self) -> RecognitionModelIdentity:
        if not self._local_files_only:
            raise AdapterUnavailableError(
                "Qwen3-ASR production Evidence requires local_files_only=True so "
                "preflight cannot perform network downloads"
            )
        try:
            import qwen_asr
            import torch
        except ImportError as exc:  # pragma: no cover - optional production dependency
            raise AdapterUnavailableError(
                "Qwen3ASRRecognizerAdapter requires the optional qwen-asr GPU runtime"
            ) from exc
        try:
            qwen_version = importlib.metadata.version("qwen-asr")
        except importlib.metadata.PackageNotFoundError:  # pragma: no cover - package anomaly
            qwen_version = str(getattr(qwen_asr, "__version__", "unknown"))
        torch_version = str(getattr(torch, "__version__", "unknown"))
        cuda_version = str(getattr(getattr(torch, "version", None), "cuda", None) or "none")
        components: dict[str, str] = {
            "cuda_runtime": cuda_version,
            "device_request": self._device,
            "platform": platform.platform(),
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "qwen_asr": qwen_version,
            "torch": torch_version,
        }
        model_snapshot_path, model_snapshot, model_inventory = self._measure_local_snapshot(
            repo_id=self._model,
            revision=self._model_revision,
            label="model",
        )
        aligner_snapshot_path, aligner_snapshot, aligner_inventory = self._measure_local_snapshot(
            repo_id=self._forced_aligner,
            revision=self._forced_aligner_revision,
            label="forced-aligner",
        )
        components.update(
            {
                "aligner_snapshot_inventory": aligner_inventory,
                "aligner_snapshot_revision": aligner_snapshot,
                "model_snapshot_inventory": model_inventory,
                "model_snapshot_revision": model_snapshot,
            }
        )
        if self._device.startswith("cuda"):
            cuda = getattr(torch, "cuda", None)
            if cuda is None or not cuda.is_available():
                raise AdapterUnavailableError(
                    f"Qwen3-ASR configured {self._device!r}, but CUDA is unavailable"
                )
            try:
                device_index = int(self._device.partition(":")[2] or "0")
                components["cuda_device_name"] = str(cuda.get_device_name(device_index))
                components["cuda_capability"] = ".".join(
                    str(part) for part in cuda.get_device_capability(device_index)
                )
            except (ValueError, RuntimeError) as exc:
                raise AdapterUnavailableError(
                    f"Qwen3-ASR cannot measure configured CUDA device {self._device!r}"
                ) from exc
        identity = self._make_identity(
            runtime_components=tuple(sorted(components.items())),
            code_hash=self._adapter_code_hash,
            execution_mode="local",
        )
        self._model_snapshot_path = model_snapshot_path
        self._forced_aligner_snapshot_path = aligner_snapshot_path
        self._model_snapshot_inventory_hash = model_inventory
        self._forced_aligner_snapshot_inventory_hash = aligner_inventory
        return identity

    @staticmethod
    def _provider_language(language_hint: str | None) -> str | None:
        if language_hint is None:
            return None
        normalized = language_hint.strip().casefold().replace("_", "-")
        if normalized in {"zh", "zh-cn", "zh-tw", "zh-hant", "zh-hant-tw"}:
            return "Chinese"
        if normalized in {"en", "en-us", "en-gb", "english"}:
            return "English"
        return language_hint

    def _load_qwen_model(self) -> object:
        try:
            import torch
            from qwen_asr import Qwen3ASRModel
        except ImportError as exc:  # pragma: no cover - optional production dependency
            raise AdapterUnavailableError(
                "Qwen3ASRRecognizerAdapter requires the optional qwen-asr GPU runtime"
            ) from exc
        try:
            torch_dtype = getattr(torch, self._dtype)
        except AttributeError as exc:  # pragma: no cover - configuration guard
            raise AdapterInputError(f"unsupported torch dtype: {self._dtype!r}") from exc
        _ = self.identity
        if self._model_snapshot_path is None or self._forced_aligner_snapshot_path is None:
            raise AdapterUnavailableError(
                "Qwen3-ASR local snapshot paths were not established by identity preflight"
            )
        try:
            _, _, model_inventory = measure_huggingface_snapshot(self._model_snapshot_path)
            _, _, aligner_inventory = measure_huggingface_snapshot(
                self._forced_aligner_snapshot_path
            )
        except SnapshotIdentityError as exc:
            raise AdapterIntegrityError("Qwen3-ASR local snapshot identity failed") from exc
        if (
            model_inventory != self._model_snapshot_inventory_hash
            or aligner_inventory != self._forced_aligner_snapshot_inventory_hash
        ):
            raise AdapterIntegrityError(
                "Qwen3-ASR local snapshot bytes changed after identity preflight"
            )
        model = Qwen3ASRModel.from_pretrained(
            str(self._model_snapshot_path),
            revision=self._model_revision,
            dtype=torch_dtype,
            device_map=self._device,
            forced_aligner=str(self._forced_aligner_snapshot_path),
            forced_aligner_kwargs={
                "revision": self._forced_aligner_revision,
                "dtype": torch_dtype,
                "device_map": self._device,
                "local_files_only": self._local_files_only,
            },
            max_inference_batch_size=self._max_inference_batch_size,
            max_new_tokens=self._max_new_tokens,
            local_files_only=self._local_files_only,
        )
        try:
            _, _, model_inventory_after = measure_huggingface_snapshot(self._model_snapshot_path)
            _, _, aligner_inventory_after = measure_huggingface_snapshot(
                self._forced_aligner_snapshot_path
            )
        except SnapshotIdentityError as exc:
            del model
            raise AdapterIntegrityError(
                "Qwen3-ASR local snapshot changed during model construction"
            ) from exc
        if (
            model_inventory_after != self._model_snapshot_inventory_hash
            or aligner_inventory_after != self._forced_aligner_snapshot_inventory_hash
        ):
            del model
            raise AdapterIntegrityError(
                "Qwen3-ASR local snapshot changed during model construction"
            )
        return model

    def _transcribe_qwen_chunk(
        self,
        model: object,
        audio_path: Path,
        request: RecognitionRequest,
    ) -> Mapping[str, object]:
        results = model.transcribe(  # type: ignore[attr-defined]
            audio=str(audio_path),
            context=request.context_policy.context_bytes.decode("utf-8"),
            language=self._provider_language(request.language_hint),
            return_time_stamps=True,
        )
        if not results:
            raise AdapterInputError("Qwen3-ASR returned no recognition result for a bounded chunk")
        result = results[0]
        forced_alignment = getattr(result, "time_stamps", None)
        raw_items = getattr(forced_alignment, "items", None)
        if not raw_items:
            raise AdapterInputError(
                "Qwen3-ASR returned no forced-alignment timestamps for a bounded chunk"
            )
        transcript_text = getattr(result, "text", None)
        if not isinstance(transcript_text, str) or not transcript_text:
            raise AdapterInputError("Qwen3-ASR returned no transcript text for a bounded chunk")
        observations: list[dict[str, object]] = []
        for index, segment in enumerate(raw_items):
            text = getattr(segment, "text", None)
            start = getattr(segment, "start_time", None)
            end = getattr(segment, "end_time", None)
            if not isinstance(text, str) or not text:
                raise AdapterInputError(f"Qwen3-ASR forced-alignment items[{index}] has no text")
            start_number = _finite_number(
                start, location=f"Qwen alignment items[{index}].start_time"
            )
            end_number = _finite_number(end, location=f"Qwen alignment items[{index}].end_time")
            if start_number < 0 or end_number < start_number:
                raise AdapterIntegrityError(
                    "Qwen3-ASR forced-alignment item has a negative or backwards range"
                )
            observations.append(
                {
                    "index": index,
                    "text": text,
                    "start_seconds": start_number,
                    "end_seconds": end_number,
                }
            )
        grouping = _build_qwen_alignment_grouping(observations)
        _project_qwen_transcript(
            transcript_text,
            tuple(group.text for group in grouping.groups),
        )
        segments = [
            {
                "id": group.index,
                "start": group.start_seconds,
                "end": group.end_seconds,
                "text": group.text,
                "words": [
                    {
                        "word": group.text,
                        "start": group.start_seconds,
                        "end": group.end_seconds,
                    }
                ],
            }
            for group in grouping.groups
        ]
        return {
            "language": getattr(result, "language", None) or request.language_hint,
            "transcript_text": transcript_text,
            "segments": segments,
            "alignment_grouping": grouping.model_dump(mode="json"),
        }

    def _run_qwen(
        self,
        audio_path: Path,
        request: RecognitionRequest,
    ) -> Mapping[str, object]:
        """One bounded provider call retained as a focused production contract seam."""

        return self._transcribe_qwen_chunk(self._load_qwen_model(), audio_path, request)

    def _plan_chunks(self, topology: _WavTopology) -> tuple[_ChunkPlan, ...]:
        owned_samples = self._owned_chunk_seconds * topology.sample_rate_hz
        context_samples = round(self._seam_context_ms * topology.sample_rate_hz / 1_000)
        count = math.ceil(topology.frame_count / owned_samples)
        return tuple(
            _ChunkPlan(
                index=index,
                count=count,
                inference_start_sample=max(0, index * owned_samples - context_samples),
                inference_end_sample=min(
                    topology.frame_count,
                    min(topology.frame_count, (index + 1) * owned_samples) + context_samples,
                ),
                owned_start_sample=index * owned_samples,
                owned_end_sample=min(topology.frame_count, (index + 1) * owned_samples),
            )
            for index in range(count)
        )

    @staticmethod
    def _iter_chunk_bytes(
        audio_path: Path,
        *,
        topology: _WavTopology,
        plans: Sequence[_ChunkPlan],
    ) -> Iterable[bytes]:
        """Derive bounded WAV slices lazily; never retain the episode-sized set."""

        for plan in plans:
            yield _derive_pcm_wav_chunk(
                audio_path,
                topology=topology,
                start_sample=plan.inference_start_sample,
                end_sample=plan.inference_end_sample,
            )

    @staticmethod
    def _provider_words(
        raw: Mapping[str, object],
    ) -> tuple[
        list[Mapping[str, object]],
        str,
        tuple[dict[str, object], ...],
        tuple[dict[str, object], ...],
    ]:
        slim: dict[str, object] = {}
        if "segments" in raw:
            slim["segments"] = raw["segments"]
        if "words" in raw:
            slim["words"] = raw["words"]
        if "language" in raw:
            slim["language"] = raw["language"]
        words, _metadata = _flatten_words(slim)
        grouping_payload = raw.get("alignment_grouping")
        if grouping_payload is not None:
            try:
                grouping = _QwenAlignmentGroupingReceipt.model_validate_json(
                    canonical_json_bytes(grouping_payload)
                )
            except ValidationError as exc:
                raise AdapterInputError(f"invalid Qwen alignment grouping receipt: {exc}") from exc
            if len(words) != len(grouping.groups):
                raise AdapterIntegrityError(
                    "Qwen provider words differ from the bounded alignment group partition"
                )
            for index, (word, group) in enumerate(zip(words, grouping.groups)):
                start = _finite_number(word.get("start"), location=f"words[{index}].start")
                end = _finite_number(word.get("end"), location=f"words[{index}].end")
                if (
                    _word_text(word, index=index) != group.text
                    or start != group.start_seconds
                    or end != group.end_seconds
                ):
                    raise AdapterIntegrityError(
                        "Qwen provider word does not match its bounded alignment group"
                    )
        transcript_text = raw.get("transcript_text")
        if not isinstance(transcript_text, str) or not transcript_text:
            raise AdapterInputError(
                "bounded Qwen runner must return explicit non-blank transcript_text"
            )
        aligned_texts = tuple(_word_text(word, index=index) for index, word in enumerate(words))
        projection, separators = _project_qwen_transcript(transcript_text, aligned_texts)
        return words, transcript_text, projection, separators

    def _chunk_receipt(
        self,
        *,
        plan: _ChunkPlan,
        topology: _WavTopology,
        chunk_bytes: bytes,
        provider_output: Mapping[str, object],
        language_hint: str | None,
    ) -> dict[str, object]:
        words, transcript_text, projection, separators = self._provider_words(provider_output)
        raw_language = provider_output.get("language") or language_hint
        if not isinstance(raw_language, str) or not raw_language.strip():
            raise AdapterInputError("bounded Qwen chunk requires provider or requested language")
        items: list[dict[str, object]] = []
        chunk_length = plan.inference_end_sample - plan.inference_start_sample
        for index, (word, projected) in enumerate(zip(words, projection)):
            if "start" not in word or "end" not in word:
                raise AdapterInputError(f"bounded Qwen words[{index}] requires exact start and end")
            start_seconds = _finite_number(word["start"], location=f"words[{index}].start")
            end_seconds = _finite_number(word["end"], location=f"words[{index}].end")
            if end_seconds <= start_seconds:
                raise AdapterIntegrityError(
                    f"bounded Qwen words[{index}] has zero or negative duration"
                )
            local_start = round(start_seconds * topology.sample_rate_hz)
            local_end = round(end_seconds * topology.sample_rate_hz)
            if not 0 <= local_start < local_end <= chunk_length:
                raise AdapterIntegrityError(
                    f"bounded Qwen words[{index}] exceeds its exact derived chunk"
                )
            aligned_text = _word_text(word, index=index)
            projected_text = str(projected["evidence_text"])
            item_id = (
                "qwen-item-"
                + hash_object(
                    {
                        "chunk_id": plan.id,
                        "index": index,
                        "aligned_text": aligned_text,
                        "evidence_text": projected_text,
                        "transcript_kept_indices": projected["transcript_kept_indices"],
                        "local_start_sample": local_start,
                        "local_end_sample": local_end,
                    }
                )[:32]
            )
            items.append(
                {
                    "id": item_id,
                    "aligned_text": aligned_text,
                    "text": projected_text,
                    "transcript_kept_indices": projected["transcript_kept_indices"],
                    "local_start_seconds": start_seconds,
                    "local_end_seconds": end_seconds,
                    "local_start_sample": local_start,
                    "local_end_sample": local_end,
                    "global_start_sample": plan.inference_start_sample + local_start,
                    "global_end_sample": plan.inference_start_sample + local_end,
                }
            )
        return {
            "id": plan.id,
            "index": plan.index,
            "count": plan.count,
            "inference_start_sample": plan.inference_start_sample,
            "inference_end_sample": plan.inference_end_sample,
            "owned_start_sample": plan.owned_start_sample,
            "owned_end_sample": plan.owned_end_sample,
            "derived_audio_sha256": sha256_bytes(chunk_bytes),
            "derived_audio_size_bytes": len(chunk_bytes),
            "language": raw_language,
            "transcript_text": transcript_text,
            "transcript_sha256": sha256_bytes(transcript_text.encode("utf-8")),
            "transcript_projection_rule": _QWEN_TRANSCRIPT_PROJECTION_RULE,
            "provider_output": dict(provider_output),
            "alignment_items": items,
            "separators": separators,
        }

    @staticmethod
    def _items_in_range(
        chunk: Mapping[str, object],
        *,
        start_sample: int,
        end_sample: int,
    ) -> tuple[Mapping[str, object], ...]:
        items = chunk["alignment_items"]
        assert isinstance(items, Sequence)
        return tuple(
            item
            for item in items
            if isinstance(item, Mapping)
            and start_sample
            <= (int(item["global_start_sample"]) + int(item["global_end_sample"])) // 2
            < end_sample
        )

    def _build_envelope(
        self,
        *,
        audio_path: Path,
        request: RecognitionRequest,
        topology: _WavTopology,
        plans: tuple[_ChunkPlan, ...],
        provider_outputs: Sequence[Mapping[str, object]],
        chunk_bytes_set: Iterable[bytes],
    ) -> dict[str, object]:
        if len(provider_outputs) != len(plans):
            raise AdapterIntegrityError("Qwen bounded execution returned an incomplete chunk set")
        chunk_iterator = iter(chunk_bytes_set)
        chunks: list[dict[str, object]] = []
        try:
            for plan, provider_output in zip(plans, provider_outputs):
                chunk_bytes = next(chunk_iterator)
                chunks.append(
                    self._chunk_receipt(
                        plan=plan,
                        topology=topology,
                        chunk_bytes=chunk_bytes,
                        provider_output=provider_output,
                        language_hint=request.language_hint,
                    )
                )
                del chunk_bytes
        except StopIteration as exc:
            raise AdapterIntegrityError(
                "Qwen bounded execution returned an incomplete derived chunk set"
            ) from exc
        sentinel = object()
        if next(chunk_iterator, sentinel) is not sentinel:
            raise AdapterIntegrityError("Qwen bounded execution returned excess derived chunks")
        languages = {str(chunk["language"]) for chunk in chunks}
        if len(languages) != 1:
            raise AdapterIntegrityError("Qwen bounded chunks disagree on provider language")
        seams: list[dict[str, object]] = []
        for left, right in zip(chunks, chunks[1:]):
            comparison_start = max(
                int(left["inference_start_sample"]),
                int(right["inference_start_sample"]),
            )
            comparison_end = min(
                int(left["inference_end_sample"]),
                int(right["inference_end_sample"]),
            )
            left_items = self._items_in_range(
                left, start_sample=comparison_start, end_sample=comparison_end
            )
            right_items = self._items_in_range(
                right, start_sample=comparison_start, end_sample=comparison_end
            )
            left_text = "".join(str(item["text"]) for item in left_items)
            right_text = "".join(str(item["text"]) for item in right_items)
            left_normalized = _seam_text(left_text)
            right_normalized = _seam_text(right_text)
            seam_sample = int(left["owned_end_sample"])
            seams.append(
                {
                    "id": f"qwen-seam-{seam_sample}",
                    "seam_sample": seam_sample,
                    "left_chunk_id": left["id"],
                    "right_chunk_id": right["id"],
                    "comparison_start_sample": comparison_start,
                    "comparison_end_sample": comparison_end,
                    "left_text": left_text,
                    "right_text": right_text,
                    "left_normalized": left_normalized,
                    "right_normalized": right_normalized,
                    "status": ("matched" if left_normalized == right_normalized else "conflict"),
                }
            )
        merged: list[dict[str, object]] = []
        for chunk in chunks:
            for item in self._items_in_range(
                chunk,
                start_sample=int(chunk["owned_start_sample"]),
                end_sample=int(chunk["owned_end_sample"]),
            ):
                start_ms = round(int(item["global_start_sample"]) * 1_000 / topology.sample_rate_hz)
                end_ms = round(int(item["global_end_sample"]) * 1_000 / topology.sample_rate_hz)
                merged.append(
                    {
                        "word": item["text"],
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                        "source_chunk_id": chunk["id"],
                        "source_item_id": item["id"],
                        "ownership": ("alignment_midpoint_in_half_open_owned_interval_v1"),
                    }
                )
        return {
            "schema_version": self.ENVELOPE_SCHEMA_VERSION,
            "kind": "qwen3_asr_bounded_overlap_evidence",
            "identity_hash": self.identity.content_hash,
            "language": next(iter(languages)),
            "normalized_audio": {
                "sha256": hash_file(audio_path),
                "size_bytes": audio_path.stat().st_size,
                "sample_rate_hz": topology.sample_rate_hz,
                "channels": topology.channels,
                "sample_width_bytes": topology.sample_width_bytes,
                "frame_count": topology.frame_count,
                "duration_ms": round(topology.frame_count * 1_000 / topology.sample_rate_hz),
                "compression_type": topology.compression_type,
            },
            "planner": {
                "version": 1,
                "derivation": "pcm_wav_frame_slice_v1",
                "ownership": "alignment_midpoint_in_half_open_owned_interval_v1",
                "seam_comparison": "normalized_alignment_text_in_shared_context_v1",
                "owned_chunk_samples": (self._owned_chunk_seconds * topology.sample_rate_hz),
                "seam_context_samples": round(
                    self._seam_context_ms * topology.sample_rate_hz / 1_000
                ),
            },
            "chunks": chunks,
            "seams": seams,
            "merged_words": merged,
        }

    def _parse_envelope(self, raw_output: bytes) -> _QwenRecognitionEnvelope:
        try:
            payload = json.loads(
                raw_output.decode("utf-8"),
                parse_constant=_reject_json_constant,
                object_pairs_hook=_strict_object,
            )
            return _QwenRecognitionEnvelope.model_validate(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise AdapterInputError(f"invalid bounded Qwen Evidence envelope: {exc}") from exc

    def _validate_envelope(
        self,
        envelope: _QwenRecognitionEnvelope,
        *,
        request: RecognitionRequest,
    ) -> None:
        audio_path = Path(request.normalized_audio)
        topology = _read_pcm_wav_topology(audio_path)
        actual_hash = _normalized_audio_hash(request)
        audio = envelope.normalized_audio
        if (
            audio.sha256 != actual_hash
            or audio.size_bytes != audio_path.stat().st_size
            or audio.sample_rate_hz != topology.sample_rate_hz
            or audio.channels != topology.channels
            or audio.sample_width_bytes != topology.sample_width_bytes
            or audio.frame_count != topology.frame_count
            or audio.duration_ms != round(topology.frame_count * 1_000 / topology.sample_rate_hz)
            or audio.compression_type != topology.compression_type
        ):
            raise AdapterIntegrityError("bounded Qwen envelope audio topology mismatch")
        if envelope.identity_hash != self.identity.content_hash:
            raise AdapterIntegrityError("bounded Qwen envelope executable identity mismatch")
        plans = self._plan_chunks(topology)
        if len(envelope.chunks) != len(plans):
            raise AdapterIntegrityError("bounded Qwen envelope chunk set is incomplete")
        for chunk, plan in zip(envelope.chunks, plans):
            if (
                chunk.id != plan.id
                or chunk.index != plan.index
                or chunk.count != plan.count
                or chunk.inference_start_sample != plan.inference_start_sample
                or chunk.inference_end_sample != plan.inference_end_sample
                or chunk.owned_start_sample != plan.owned_start_sample
                or chunk.owned_end_sample != plan.owned_end_sample
            ):
                raise AdapterIntegrityError(
                    "bounded Qwen chunks are reordered, duplicated, gapped, or overlapping"
                )
            derived = _derive_pcm_wav_chunk(
                audio_path,
                topology=topology,
                start_sample=plan.inference_start_sample,
                end_sample=plan.inference_end_sample,
            )
            if chunk.derived_audio_sha256 != sha256_bytes(
                derived
            ) or chunk.derived_audio_size_bytes != len(derived):
                raise AdapterIntegrityError("bounded Qwen derived chunk digest mismatch")
            provider_words, provider_text, projection, separators = self._provider_words(
                chunk.provider_output
            )
            if (
                provider_text != chunk.transcript_text
                or len(provider_words) != len(chunk.alignment_items)
                or tuple(separator.model_dump(mode="json") for separator in chunk.separators)
                != (separators)
            ):
                raise AdapterIntegrityError("bounded Qwen chunk derivation changed provider output")
            for index, (word, item, projected) in enumerate(
                zip(provider_words, chunk.alignment_items, projection)
            ):
                expected_aligned_text = _word_text(word, index=index)
                expected_text = str(projected["evidence_text"])
                start_seconds = _finite_number(
                    word["start"], location=f"chunks[{chunk.index}].words[{index}].start"
                )
                end_seconds = _finite_number(
                    word["end"], location=f"chunks[{chunk.index}].words[{index}].end"
                )
                expected_local_start = round(start_seconds * topology.sample_rate_hz)
                expected_local_end = round(end_seconds * topology.sample_rate_hz)
                if (
                    item.aligned_text != expected_aligned_text
                    or item.text != expected_text
                    or item.transcript_kept_indices != projected["transcript_kept_indices"]
                    or item.local_start_seconds != start_seconds
                    or item.local_end_seconds != end_seconds
                    or item.local_start_sample != expected_local_start
                    or item.local_end_sample != expected_local_end
                    or item.global_start_sample
                    != plan.inference_start_sample + expected_local_start
                    or item.global_end_sample != plan.inference_start_sample + expected_local_end
                ):
                    raise AdapterIntegrityError("bounded Qwen alignment derivation does not replay")
        expected_mapping = self._build_envelope(
            audio_path=audio_path,
            request=request,
            topology=topology,
            plans=plans,
            provider_outputs=tuple(chunk.provider_output for chunk in envelope.chunks),
            chunk_bytes_set=self._iter_chunk_bytes(
                audio_path,
                topology=topology,
                plans=plans,
            ),
        )
        expected = _QwenRecognitionEnvelope.model_validate(expected_mapping)
        if envelope != expected:
            raise AdapterIntegrityError("bounded Qwen Evidence envelope does not replay exactly")
        conflicts = tuple(seam.id for seam in envelope.seams if seam.status != "matched")
        if conflicts:
            raise _QwenDeterministicRejection(
                "seam_conflict",
                f"bounded Qwen seam hypotheses conflict: {conflicts!r}",
            )
        if not envelope.merged_words:
            raise _QwenDeterministicRejection(
                "empty_owned_output",
                "bounded Qwen execution produced no owned words",
            )
        previous_end = 0
        for word in envelope.merged_words:
            if word.start_ms < previous_end:
                raise _QwenDeterministicRejection(
                    "owned_output_overlap",
                    "bounded Qwen ownership produced overlapping merged words",
                )
            previous_end = word.end_ms

    def _evidence_from_envelope(
        self,
        envelope: _QwenRecognitionEnvelope,
        *,
        request: RecognitionRequest,
        raw_output: ArtifactDigest,
    ) -> RecognitionEvidence:
        slim = {
            "language": envelope.language,
            "words": [
                {
                    "word": word.word,
                    "start": word.start_ms,
                    "end": word.end_ms,
                }
                for word in envelope.merged_words
            ],
        }
        return _evidence_from_payload(
            slim,
            request=request,
            raw_output=raw_output,
            adapter=self.ADAPTER_NAME,
            model=f"{self._model}@{self._model_revision}",
            config=self._config(),
            timestamp_unit="milliseconds",
            normalized_audio_duration_ms=envelope.normalized_audio.duration_ms,
            expected_normalized_audio_size_bytes=envelope.normalized_audio.size_bytes,
            expected_normalized_audio_hash=envelope.normalized_audio.sha256,
        )

    def _build_recognition_run_plan(
        self,
        *,
        audio_path: Path,
        request: RecognitionRequest,
        topology: _WavTopology,
        plans: tuple[_ChunkPlan, ...],
    ) -> RecognitionRunPlanV1:
        logical_namespace = self._logical_namespace
        if logical_namespace is None:
            raise AdapterIntegrityError(
                "Qwen checkpoint execution requires an explicit logical namespace"
            )
        audio = build_recognition_audio_binding(audio_path)
        if (
            audio.sample_rate_hz != topology.sample_rate_hz
            or audio.channels != topology.channels
            or audio.sample_width_bytes != topology.sample_width_bytes
            or audio.frame_count != topology.frame_count
            or audio.compression_type != topology.compression_type
        ):
            raise AdapterIntegrityError(
                "Qwen Recognition run audio binding crossed its measured topology"
            )
        request_binding = build_recognition_request_binding(
            episode_id=request.episode_id,
            invocation_id=request.invocation_id,
            logical_namespace=logical_namespace,
            language_hint=request.language_hint,
            context_policy_id=request.context_policy.id,
            context_bytes=request.context_policy.context_bytes,
            audio=audio,
        )
        identity = snapshot_recognition_model_identity(self.identity)
        planner = build_recognition_chunk_planner(
            planner_name="qwen3-asr-bounded-overlap",
            planner_version=str(self.ADAPTER_VERSION),
            owned_chunk_samples=self._owned_chunk_seconds * topology.sample_rate_hz,
            seam_context_samples=round(self._seam_context_ms * topology.sample_rate_hz / 1_000),
        )
        chunks = tuple(
            build_recognition_chunk_plan(
                index=plan.index,
                count=plan.count,
                inference_start_sample=plan.inference_start_sample,
                inference_end_sample=plan.inference_end_sample,
                owned_start_sample=plan.owned_start_sample,
                owned_end_sample=plan.owned_end_sample,
                normalized_audio=audio_path,
                audio=audio,
            )
            for plan in plans
        )
        return build_recognition_run_plan(
            request=request_binding,
            audio=audio,
            adapter_identity=identity,
            planner=planner,
            chunks=chunks,
        )

    @staticmethod
    def _raise_terminal_rejection(finalization: RecognitionRunFinalizationV1) -> None:
        failure = finalization.failure
        code = failure.code if failure is not None else "missing_typed_failure"
        raise AdapterIntegrityError(f"bounded Qwen Recognition run is terminally rejected: {code}")

    @staticmethod
    def _repair_then_prepare(
        repository: RecognitionRunRepository,
        *,
        plan: RecognitionRunPlanV1,
        normalized_audio: Path,
    ) -> None:
        """Prepare, repairing only a fully authenticated publication tail.

        Both repair methods validate the complete repository before mutation.
        Trying them after an integrity failure therefore cannot regenerate past
        corrupt state or turn a different terminal outcome into a verified one.
        """

        try:
            repository.prepare(plan, normalized_audio=normalized_audio)
            return
        except RecognitionRunIntegrityError as original:
            repairers = (
                repository.repair_published_tail,
                repository.repair_published_finalization,
            )
            for repair in repairers:
                try:
                    repair(plan=plan)
                except RecognitionRunIntegrityError:
                    continue
                repository.prepare(plan, normalized_audio=normalized_audio)
                return
            raise original

    def _assemble_checkpoint_evidence(
        self,
        *,
        audio_path: Path,
        request: RecognitionRequest,
        topology: _WavTopology,
        plans: tuple[_ChunkPlan, ...],
        provider_outputs: Sequence[Mapping[str, object]],
    ) -> RecognitionEvidence:
        raw = self._build_envelope(
            audio_path=audio_path,
            request=request,
            topology=topology,
            plans=plans,
            provider_outputs=provider_outputs,
            chunk_bytes_set=self._iter_chunk_bytes(
                audio_path,
                topology=topology,
                plans=plans,
            ),
        )
        raw_output = _persist_raw_output(
            request=request,
            adapter_stem="qwen3-asr",
            raw=raw,
        )
        raw_bytes = Path(_file_uri_path_for_adapter(raw_output.uri)).read_bytes()
        envelope = self._parse_envelope(raw_bytes)
        self._validate_envelope(envelope, request=request)
        return self._evidence_from_envelope(
            envelope,
            request=request,
            raw_output=raw_output,
        )

    @staticmethod
    def _deterministic_failure_code(
        failure: AdapterInputError | AdapterIntegrityError,
    ) -> Literal[
        "seam_conflict",
        "empty_owned_output",
        "owned_output_overlap",
        "recognition_evidence_rejected",
        "deterministic_postprocessing_failure",
    ]:
        if isinstance(failure, _QwenDeterministicRejection):
            return failure.failure_code
        if isinstance(failure, AdapterIntegrityError):
            return "recognition_evidence_rejected"
        return "deterministic_postprocessing_failure"

    def _recognize_with_checkpoint(
        self,
        *,
        request: RecognitionRequest,
        audio_path: Path,
        topology: _WavTopology,
        plans: tuple[_ChunkPlan, ...],
    ) -> RecognitionEvidence:
        repository = self._recognition_run_repository
        if repository is None:
            raise AdapterIntegrityError("Qwen checkpoint repository disappeared")
        plan = self._build_recognition_run_plan(
            audio_path=audio_path,
            request=request,
            topology=topology,
            plans=plans,
        )
        with repository.execution_lease(plan.run_key):
            self._repair_then_prepare(
                repository,
                plan=plan,
                normalized_audio=audio_path,
            )
            finalization = repository.load_finalization(plan=plan)
            if finalization is not None and finalization.status == "rejected":
                self._raise_terminal_rejection(finalization)

            replay = repository.replay_prefix(plan=plan)
            provider_outputs: list[Mapping[str, object]] = [
                item.adapter_observation for item in replay
            ]
            completed_prefix = len(provider_outputs)
            if completed_prefix < len(plans):
                try:
                    with tempfile.TemporaryDirectory(prefix="qwen3-asr-bounded-") as temporary:
                        model = None if self._runner is not None else self._load_qwen_model()
                        for index in range(completed_prefix, len(plans)):
                            qwen_plan = plans[index]
                            chunk_bytes = _derive_pcm_wav_chunk(
                                audio_path,
                                topology=topology,
                                start_sample=qwen_plan.inference_start_sample,
                                end_sample=qwen_plan.inference_end_sample,
                            )
                            chunk_path = Path(temporary) / f"{qwen_plan.id}.wav"
                            chunk_path.write_bytes(chunk_bytes)
                            raw = (
                                self._runner(chunk_path, request)
                                if self._runner is not None
                                else self._transcribe_qwen_chunk(model, chunk_path, request)
                            )
                            if not isinstance(raw, Mapping):
                                raise AdapterInputError("Qwen3-ASR runner must return a mapping")
                            committed = repository.commit_chunk(
                                plan=plan,
                                chunk_index=index,
                                derived_chunk_bytes=chunk_bytes,
                                adapter_observation=raw,
                            )
                            provider_outputs.append(committed.adapter_observation)
                            chunk_path.unlink(missing_ok=True)
                            del chunk_bytes
                except (AdapterInputError, AdapterIntegrityError, AdapterUnavailableError):
                    raise
                except RecognitionRunIntegrityError:
                    raise
                except Exception as exc:
                    raise AdapterErrorFromProvider("Qwen3-ASR recognition failed") from exc

            replay = repository.replay_prefix(plan=plan)
            if len(replay) != len(plans):
                raise RecognitionRunIntegrityError(
                    "Qwen Recognition run did not produce its complete durable prefix"
                )
            provider_outputs = [item.adapter_observation for item in replay]
            try:
                evidence = self._assemble_checkpoint_evidence(
                    audio_path=audio_path,
                    request=request,
                    topology=topology,
                    plans=plans,
                    provider_outputs=provider_outputs,
                )
            except (AdapterInputError, AdapterIntegrityError) as exc:
                if finalization is not None:
                    raise
                failure = build_recognition_run_failure(
                    code=self._deterministic_failure_code(exc),
                    affected_chunk_ids=tuple(chunk.id for chunk in plan.chunks),
                    diagnostics={
                        "failure_class": (
                            "AdapterIntegrityError"
                            if isinstance(exc, AdapterIntegrityError)
                            else "AdapterInputError"
                        ),
                        "stage": "qwen_envelope_v3_replay",
                    },
                )
                repository.commit_finalization(
                    plan=plan,
                    status="rejected",
                    failure=failure,
                )
                raise

            evidence_bytes = canonical_json_bytes(evidence)
            if finalization is None:
                repository.commit_finalization(
                    plan=plan,
                    status="verified",
                    recognition_evidence_bytes=evidence_bytes,
                )
            elif (
                finalization.status != "verified"
                or finalization.recognition_evidence_hash != sha256_bytes(evidence_bytes)
            ):
                raise RecognitionRunIntegrityError(
                    "Qwen replayed Evidence differs from its terminal finalization"
                )
            return evidence

    def recognize(self, request: RecognitionRequest) -> RecognitionEvidence:
        _normalized_audio_hash(request)
        self._assert_source_identity()
        audio_path = Path(request.normalized_audio)
        topology = _read_pcm_wav_topology(audio_path)
        plans = self._plan_chunks(topology)
        if self._recognition_run_repository is not None:
            return self._recognize_with_checkpoint(
                request=request,
                audio_path=audio_path,
                topology=topology,
                plans=plans,
            )
        provider_outputs: list[Mapping[str, object]] = []
        try:
            with tempfile.TemporaryDirectory(prefix="qwen3-asr-bounded-") as temporary:
                model = None if self._runner is not None else self._load_qwen_model()
                for plan in plans:
                    chunk_bytes = _derive_pcm_wav_chunk(
                        audio_path,
                        topology=topology,
                        start_sample=plan.inference_start_sample,
                        end_sample=plan.inference_end_sample,
                    )
                    chunk_path = Path(temporary) / f"{plan.id}.wav"
                    chunk_path.write_bytes(chunk_bytes)
                    raw = (
                        self._runner(chunk_path, request)
                        if self._runner is not None
                        else self._transcribe_qwen_chunk(model, chunk_path, request)
                    )
                    if not isinstance(raw, Mapping):
                        raise AdapterInputError("Qwen3-ASR runner must return a mapping")
                    provider_outputs.append(raw)
                    chunk_path.unlink(missing_ok=True)
                    del chunk_bytes
        except (AdapterInputError, AdapterIntegrityError, AdapterUnavailableError):
            raise
        except Exception as exc:
            raise AdapterErrorFromProvider("Qwen3-ASR recognition failed") from exc
        raw = self._build_envelope(
            audio_path=audio_path,
            request=request,
            topology=topology,
            plans=plans,
            provider_outputs=provider_outputs,
            chunk_bytes_set=self._iter_chunk_bytes(
                audio_path,
                topology=topology,
                plans=plans,
            ),
        )
        raw_output = _persist_raw_output(
            request=request,
            adapter_stem="qwen3-asr",
            raw=raw,
        )
        raw_bytes = Path(_file_uri_path_for_adapter(raw_output.uri)).read_bytes()
        envelope = self._parse_envelope(raw_bytes)
        self._validate_envelope(envelope, request=request)
        return self._evidence_from_envelope(
            envelope,
            request=request,
            raw_output=raw_output,
        )

    def verify(
        self,
        evidence: RecognitionEvidence,
        *,
        request: RecognitionRequest,
        raw_output: bytes,
    ) -> RecognitionEvidence:
        self._assert_source_identity()
        actual_digest = sha256_bytes(raw_output)
        if (
            evidence.raw_output.sha256 != actual_digest
            or evidence.raw_output_hash != actual_digest
            or evidence.raw_output.size_bytes != len(raw_output)
        ):
            raise AdapterIntegrityError("stored Qwen raw envelope digest mismatch")
        envelope = self._parse_envelope(raw_output)
        self._validate_envelope(envelope, request=request)
        replayed = self._evidence_from_envelope(
            envelope,
            request=request,
            raw_output=evidence.raw_output,
        )
        if replayed != evidence:
            raise AdapterIntegrityError("stored Qwen Recognition Evidence does not re-derive")
        return replayed


def _file_uri_path_for_adapter(uri: str) -> Path:
    """Resolve the file URI emitted by this Adapter without importing Module code."""

    from urllib.parse import unquote, urlparse
    from urllib.request import url2pathname

    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise AdapterIntegrityError("new Qwen raw output must be a local file URI")
    raw = url2pathname(unquote(parsed.path))
    if os.name == "nt" and len(raw) >= 3 and raw[0] in "/\\" and raw[2] == ":":
        raw = raw[1:]
    return Path(raw)


class AdapterErrorFromProvider(AdapterUnavailableError):
    """A configured production provider failed instead of returning Evidence."""


__all__ = [
    "AdapterErrorFromProvider",
    "TimestampUnit",
    "Qwen3ASRRecognizerAdapter",
    "Qwen3ASRRunner",
    "WhisperXRecognizerAdapter",
    "WhisperXRunner",
    "WordsJsonImportManifest",
    "WordsJsonRecognizerAdapter",
]
