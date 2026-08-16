"""Content-addressed multi-microphone speaker attribution.

This Adapter enriches an existing Recognition Evidence stream with speaker
labels derived from isolated microphone tracks.  It deliberately produces a
new immutable Evidence artifact instead of mutating the ASR output in place.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from shared.schemas.podcast_subtitles_v2 import (
    ArtifactDigest,
    EvidenceToken,
    RecognitionEvidence,
    recognition_evidence_content_hash,
)

from ..hashing import canonical_json_bytes, hash_file, hash_object, sha256_bytes
from ..ports import (
    AdapterInputError,
    AdapterIntegrityError,
    SpeakerAttributionProvenance,
    SpeakerAttributionRequest,
    SpeakerTrackProvenance,
)

EnvelopeLoader = Callable[[list[Path], Path | None], Any]
SpeakerAssigner = Callable[[list[dict[str, object]], Any], list[int | None]]

_ADAPTER = "mic-energy-speaker-attribution"
_ADAPTER_VERSION = 1


def _production_algorithm() -> tuple[EnvelopeLoader, SpeakerAssigner, Mapping[str, object]]:
    """Load the legacy DSP implementation lazily and freeze all material knobs."""

    from shared import speaker_assign

    config: dict[str, object] = {
        "algorithm": "two-track-rms-viterbi",
        "algorithm_version": speaker_assign.ALGORITHM_VERSION,
        "env_sample_rate": speaker_assign.ENV_SR,
        "frame_samples": speaker_assign.FRAME,
        "evidence_clamp_db": speaker_assign.EVIDENCE_CLAMP_DB,
        "silence_floor_db": speaker_assign.SILENCE_FLOOR_DB,
        "switch_penalty": speaker_assign.SWITCH_PENALTY,
        "max_word_seconds": speaker_assign.MAX_WORD_SEC,
        "gap_discount": speaker_assign.GAP_DISCOUNT,
        "minimum_switch_penalty": speaker_assign.MIN_SWITCH_PENALTY,
        "minimum_assignment_margin": speaker_assign.MIN_ASSIGNMENT_MARGIN,
    }
    return speaker_assign.load_envelopes, speaker_assign.assign_word_speakers, config


def _write_content_addressed(
    output_dir: Path,
    *,
    stem: str,
    payload: Mapping[str, object],
) -> ArtifactDigest:
    raw = canonical_json_bytes(payload)
    digest = sha256_bytes(raw)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{stem}-{digest}.json"
    if target.exists():
        actual = hash_file(target)
        if actual != digest:
            raise AdapterIntegrityError(
                f"speaker attribution artifact collision at {target}: {actual} != {digest}"
            )
    else:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{stem}-{digest}.",
            suffix=".tmp",
            dir=output_dir,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
    return ArtifactDigest(
        uri=target.resolve().as_uri(),
        sha256=digest,
        size_bytes=len(raw),
    )


def _attribution_config_hash(
    *,
    base_hash: str,
    track_records: list[dict[str, object]],
    algorithm: Mapping[str, object],
) -> str:
    return hash_object(
        {
            "adapter": _ADAPTER,
            "adapter_version": _ADAPTER_VERSION,
            "base_recognition_evidence_hash": base_hash,
            "track_slots": track_records,
            "algorithm": dict(algorithm),
        }
    )


def _strict_payload(raw_output: bytes) -> dict[str, object]:
    duplicates: list[str] = []

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                duplicates.append(key)
            result[key] = value
        return result

    try:
        payload = json.loads(raw_output, object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterIntegrityError("speaker attribution raw output is not valid JSON") from exc
    if duplicates:
        raise AdapterIntegrityError(
            f"speaker attribution raw output has duplicate keys: {sorted(set(duplicates))!r}"
        )
    if not isinstance(payload, dict) or canonical_json_bytes(payload) != raw_output:
        raise AdapterIntegrityError("speaker attribution raw output is not canonical JSON")
    return payload


class MicEnergySpeakerAttributor:
    """Attribute one existing Evidence stream from exactly two mic tracks.

    The mic paths are operator-selected inputs.  Their local paths are never
    part of logical Evidence identity; their byte digests and stable slot order
    are.  Hashes are checked again after DSP execution so a concurrently
    modified track cannot be accepted under stale provenance.
    """

    def __init__(
        self,
        *,
        envelope_loader: EnvelopeLoader | None = None,
        speaker_assigner: SpeakerAssigner | None = None,
        algorithm_config: Mapping[str, object] | None = None,
    ) -> None:
        custom_runtime = envelope_loader is not None or speaker_assigner is not None
        if custom_runtime:
            if envelope_loader is None or speaker_assigner is None:
                raise ValueError("custom speaker runtime requires both loader and assigner")
            if not algorithm_config:
                raise ValueError("custom speaker runtime requires explicit algorithm_config")
            loader = envelope_loader
            assigner = speaker_assigner
            config = dict(algorithm_config)
        else:
            loader, assigner, production_config = _production_algorithm()
            config = dict(production_config)
            if algorithm_config is not None:
                raise ValueError("algorithm_config may only accompany a custom runtime")

        self._loader = loader
        self._assigner = assigner
        self._algorithm_config = config

    @property
    def adapter_name(self) -> str:
        return _ADAPTER

    @property
    def adapter_version(self) -> str:
        return str(_ADAPTER_VERSION)

    @property
    def adapter_config_hash(self) -> str:
        """Portable identity of the configured DSP implementation and knobs."""

        return hash_object(
            {
                "adapter": _ADAPTER,
                "adapter_version": _ADAPTER_VERSION,
                "algorithm": self._algorithm_config,
            }
        )

    def attribute(
        self,
        request: SpeakerAttributionRequest,
    ) -> RecognitionEvidence:
        recognition = request.recognition_request
        base = request.base_evidence
        tracks = request.tracks
        normalized_audio = Path(recognition.normalized_audio)
        if not normalized_audio.is_file():
            raise AdapterInputError(f"normalized audio is not a file: {normalized_audio}")
        normalized_hash = hash_file(normalized_audio)
        if normalized_hash != recognition.expected_normalized_audio_hash:
            raise AdapterIntegrityError(
                "normalized audio hash differs from speaker attribution request"
            )

        track_records: list[dict[str, object]] = []
        before_hashes: list[str] = []
        for slot, binding in enumerate(tracks):
            track = binding.path
            if not track.is_file():
                raise AdapterInputError(f"microphone track is not a file: {track}")
            digest = hash_file(track)
            size_bytes = track.stat().st_size
            if digest != binding.sha256 or size_bytes != binding.size_bytes:
                raise AdapterIntegrityError(
                    f"microphone track {slot} bytes differ from its Module binding"
                )
            before_hashes.append(digest)
            track_records.append(
                {
                    "slot": slot,
                    "speaker_label": binding.speaker_label,
                    "sha256": digest,
                    "size_bytes": size_bytes,
                }
            )

        if (
            base.episode_id != recognition.episode_id
            or base.invocation_id != recognition.invocation_id
        ):
            raise AdapterIntegrityError("base Recognition Evidence belongs to another request")
        if base.normalized_audio_hash != normalized_hash:
            raise AdapterIntegrityError(
                "base Recognition Evidence uses another normalized-audio clock"
            )

        words = [
            {
                "word": token.text,
                "start": token.start_ms / 1000.0,
                "end": token.end_ms / 1000.0,
            }
            for token in base.tokens
        ]
        try:
            envelopes = self._loader([binding.path for binding in tracks], normalized_audio)
            assignments = self._assigner(words, envelopes)
        except (AdapterInputError, AdapterIntegrityError):
            raise
        except Exception as exc:
            raise AdapterInputError("microphone speaker attribution failed") from exc

        if len(assignments) != len(base.tokens):
            raise AdapterInputError(
                "speaker assigner returned a different number of labels than ASR tokens"
            )
        invalid = sorted(
            {
                repr(assignment)
                for assignment in assignments
                if assignment is not None
                and (
                    not isinstance(assignment, int)
                    or isinstance(assignment, bool)
                    or assignment not in range(len(tracks))
                )
            }
        )
        if invalid:
            raise AdapterInputError(f"speaker assigner returned invalid track slots: {invalid!r}")
        if any(assignment is None for assignment in assignments):
            raise AdapterInputError("speaker attribution left one or more tokens unresolved")

        after_hashes = [hash_file(binding.path) for binding in tracks]
        if after_hashes != before_hashes:
            raise AdapterIntegrityError("microphone track changed during speaker attribution")
        if hash_file(normalized_audio) != normalized_hash:
            raise AdapterIntegrityError("normalized audio changed during speaker attribution")

        base_hash = recognition_evidence_content_hash(base)
        payload: dict[str, object] = {
            "schema_version": 1,
            "adapter": _ADAPTER,
            "adapter_version": _ADAPTER_VERSION,
            "episode_id": recognition.episode_id,
            "invocation_id": recognition.invocation_id,
            "base_recognition_evidence_hash": base_hash,
            "normalized_audio_hash": normalized_hash,
            "tracks": track_records,
            "algorithm": self._algorithm_config,
            "assignments": [
                {
                    "base_token_id": token.id,
                    "speaker": (
                        tracks[assignment].speaker_label if assignment is not None else None
                    ),
                }
                for token, assignment in zip(base.tokens, assignments)
            ],
        }
        raw_output = _write_content_addressed(
            Path(recognition.raw_output_dir),
            stem="speaker-attribution",
            payload=payload,
        )
        config_hash = _attribution_config_hash(
            base_hash=base_hash,
            track_records=track_records,
            algorithm=self._algorithm_config,
        )
        tokens = tuple(
            EvidenceToken(
                id="ev_"
                + hash_object(
                    {
                        "adapter": _ADAPTER,
                        "base_token_id": token.id,
                        "speaker": (
                            tracks[assignment].speaker_label
                            if assignment is not None
                            else None
                        ),
                        "raw_output_hash": raw_output.sha256,
                    }
                )[:32],
                text=token.text,
                start_ms=token.start_ms,
                end_ms=token.end_ms,
                confidence=token.confidence,
                speaker=(
                    tracks[assignment].speaker_label if assignment is not None else None
                ),
                evidence_refs=(
                    f"recognition:{base_hash}#{token.id}",
                    f"raw:{raw_output.sha256}",
                ),
            )
            for token, assignment in zip(base.tokens, assignments)
        )
        return RecognitionEvidence(
            episode_id=base.episode_id,
            invocation_id=base.invocation_id,
            adapter=_ADAPTER,
            model=(
                f"{base.adapter}/{base.model}+two-track-rms-viterbi-"
                f"v{self._algorithm_config.get('algorithm_version', 'custom')}"
            ),
            language=base.language,
            config_hash=config_hash,
            raw_output=raw_output,
            raw_output_hash=raw_output.sha256,
            normalized_audio_hash=normalized_hash,
            tokens=tokens,
        )

    def verify(
        self,
        attributed: RecognitionEvidence,
        *,
        base_evidence: RecognitionEvidence,
        raw_output: bytes,
    ) -> SpeakerAttributionProvenance:
        """Revalidate a persisted attribution without reading original mic paths."""

        if (
            sha256_bytes(raw_output) != attributed.raw_output.sha256
            or len(raw_output) != attributed.raw_output.size_bytes
        ):
            raise AdapterIntegrityError(
                "stored speaker attribution bytes differ from Recognition Evidence"
            )
        payload = _strict_payload(raw_output)
        required_keys = {
            "schema_version",
            "adapter",
            "adapter_version",
            "episode_id",
            "invocation_id",
            "base_recognition_evidence_hash",
            "normalized_audio_hash",
            "tracks",
            "algorithm",
            "assignments",
        }
        if set(payload) != required_keys:
            raise AdapterIntegrityError("speaker attribution raw output has an invalid contract")

        base_hash = recognition_evidence_content_hash(base_evidence)
        if (
            payload["schema_version"] != 1
            or payload["adapter"] != _ADAPTER
            or payload["adapter_version"] != _ADAPTER_VERSION
            or payload["episode_id"] != base_evidence.episode_id
            or payload["invocation_id"] != base_evidence.invocation_id
            or payload["base_recognition_evidence_hash"] != base_hash
            or payload["normalized_audio_hash"] != base_evidence.normalized_audio_hash
            or payload["algorithm"] != self._algorithm_config
        ):
            raise AdapterIntegrityError(
                "speaker attribution raw output crossed its base Evidence or Adapter identity"
            )
        if (
            attributed.episode_id != base_evidence.episode_id
            or attributed.invocation_id != base_evidence.invocation_id
            or attributed.normalized_audio_hash != base_evidence.normalized_audio_hash
            or attributed.adapter != _ADAPTER
        ):
            raise AdapterIntegrityError("attributed Evidence crossed base Recognition lineage")

        raw_tracks = payload["tracks"]
        if not isinstance(raw_tracks, list) or len(raw_tracks) != 2:
            raise AdapterIntegrityError("speaker attribution requires two stored track identities")
        tracks: list[SpeakerTrackProvenance] = []
        track_records: list[dict[str, object]] = []
        try:
            for expected_slot, item in enumerate(raw_tracks):
                if not isinstance(item, dict) or set(item) != {
                    "slot",
                    "speaker_label",
                    "sha256",
                    "size_bytes",
                }:
                    raise ValueError("invalid track record")
                provenance = SpeakerTrackProvenance(
                    slot=item["slot"],
                    speaker_label=item["speaker_label"],
                    sha256=item["sha256"],
                    size_bytes=item["size_bytes"],
                )
                if provenance.slot != expected_slot:
                    raise ValueError("track slots are not ordered")
                tracks.append(provenance)
                track_records.append(dict(item))
        except (TypeError, ValueError) as exc:
            raise AdapterIntegrityError("invalid stored speaker track provenance") from exc
        if len({track.speaker_label for track in tracks}) != len(tracks) or len(
            {track.sha256 for track in tracks}
        ) != len(tracks):
            raise AdapterIntegrityError("stored speaker track identities are not distinct")

        raw_assignments = payload["assignments"]
        if not isinstance(raw_assignments, list) or len(raw_assignments) != len(
            base_evidence.tokens
        ):
            raise AdapterIntegrityError("stored speaker assignments do not cover base tokens")
        assignments: list[tuple[str, str]] = []
        allowed_labels = {track.speaker_label for track in tracks}
        for item in raw_assignments:
            if not isinstance(item, dict) or set(item) != {"base_token_id", "speaker"}:
                raise AdapterIntegrityError("stored speaker assignment has an invalid contract")
            base_token_id = item["base_token_id"]
            speaker = item["speaker"]
            if not isinstance(base_token_id, str) or not isinstance(speaker, str):
                raise AdapterIntegrityError("stored speaker assignment is not typed")
            if speaker not in allowed_labels:
                raise AdapterIntegrityError("stored speaker assignment has an unknown speaker")
            assignments.append((base_token_id, speaker))

        if [item[0] for item in assignments] != [token.id for token in base_evidence.tokens]:
            raise AdapterIntegrityError("stored speaker assignments crossed base token identity")
        config_hash = _attribution_config_hash(
            base_hash=base_hash,
            track_records=track_records,
            algorithm=self._algorithm_config,
        )
        expected_tokens = tuple(
            EvidenceToken(
                id="ev_"
                + hash_object(
                    {
                        "adapter": _ADAPTER,
                        "base_token_id": token.id,
                        "speaker": speaker,
                        "raw_output_hash": attributed.raw_output.sha256,
                    }
                )[:32],
                text=token.text,
                start_ms=token.start_ms,
                end_ms=token.end_ms,
                confidence=token.confidence,
                speaker=speaker,
                evidence_refs=(
                    f"recognition:{base_hash}#{token.id}",
                    f"raw:{attributed.raw_output.sha256}",
                ),
            )
            for token, (_, speaker) in zip(base_evidence.tokens, assignments)
        )
        expected = RecognitionEvidence(
            episode_id=base_evidence.episode_id,
            invocation_id=base_evidence.invocation_id,
            adapter=_ADAPTER,
            model=(
                f"{base_evidence.adapter}/{base_evidence.model}+two-track-rms-viterbi-"
                f"v{self._algorithm_config.get('algorithm_version', 'custom')}"
            ),
            language=base_evidence.language,
            config_hash=config_hash,
            raw_output=attributed.raw_output,
            raw_output_hash=attributed.raw_output_hash,
            normalized_audio_hash=base_evidence.normalized_audio_hash,
            tokens=expected_tokens,
        )
        if attributed != expected:
            raise AdapterIntegrityError(
                "attributed Recognition Evidence differs from its stored proof"
            )
        return SpeakerAttributionProvenance(
            adapter=_ADAPTER,
            adapter_version=_ADAPTER_VERSION,
            base_recognition_evidence_hash=base_hash,
            config_hash=config_hash,
            tracks=tuple(tracks),
        )
__all__ = ["MicEnergySpeakerAttributor"]
