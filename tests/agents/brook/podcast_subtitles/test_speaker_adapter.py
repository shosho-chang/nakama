from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from agents.brook.podcast_subtitles.adapters.speaker import MicEnergySpeakerAttributor
from agents.brook.podcast_subtitles.hashing import canonical_json_bytes, hash_file, hash_object
from agents.brook.podcast_subtitles.ports import (
    AdapterInputError,
    AdapterIntegrityError,
    RecognitionRequest,
    SpeakerAttributionProvenance,
    SpeakerAttributionRequest,
    SpeakerAttributor,
    SpeakerTrackBinding,
    SpeakerTrackInput,
)
from shared.schemas.podcast_subtitles_v2 import (
    ArtifactDigest,
    EvidenceToken,
    RecognitionEvidence,
)
from shared.speaker_assign import ENV_SR, FRAME, FRAME_SEC, assign_word_speakers


def _base_evidence(audio: Path, raw: Path) -> RecognitionEvidence:
    return RecognitionEvidence(
        episode_id="episode",
        invocation_id="invocation",
        adapter="fixture-asr",
        model="fixture",
        language="zh",
        config_hash=hash_object({"fixture": 1}),
        raw_output=ArtifactDigest(
            uri=raw.resolve().as_uri(),
            sha256=hash_file(raw),
            size_bytes=raw.stat().st_size,
        ),
        raw_output_hash=hash_file(raw),
        normalized_audio_hash=hash_file(audio),
        tokens=(
            EvidenceToken(
                id="base-1",
                text="安",
                start_ms=0,
                end_ms=100,
                confidence=0.9,
                evidence_refs=("raw:base",),
            ),
            EvidenceToken(
                id="base-2",
                text="吉",
                start_ms=100,
                end_ms=200,
                confidence=0.8,
                evidence_refs=("raw:base",),
            ),
        ),
    )


def _recognition_request(audio: Path, raw_dir: Path) -> RecognitionRequest:
    return RecognitionRequest(
        episode_id="episode",
        invocation_id="invocation",
        normalized_audio=audio,
        expected_normalized_audio_hash=hash_file(audio),
        raw_output_dir=raw_dir,
        language_hint="zh",
    )


def _binding(path: Path, label: str) -> SpeakerTrackBinding:
    return SpeakerTrackBinding(
        path=path,
        speaker_label=label,
        sha256=hash_file(path),
        size_bytes=path.stat().st_size,
    )


def _fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path, RecognitionEvidence, SpeakerAttributionRequest]:
    audio = tmp_path / "normalized.wav"
    raw = tmp_path / "words.json"
    left = tmp_path / "left.wav"
    right = tmp_path / "right.wav"
    audio.write_bytes(b"normalized")
    raw.write_bytes(b"{}")
    left.write_bytes(b"left")
    right.write_bytes(b"right")
    base = _base_evidence(audio, raw)
    request = SpeakerAttributionRequest(
        recognition_request=_recognition_request(audio, tmp_path / "raw"),
        base_evidence=base,
        tracks=(_binding(left, "HOST"), _binding(right, "GUEST")),
    )
    return audio, left, right, base, request


def _attributor(assignments: list[int | None]) -> MicEnergySpeakerAttributor:
    return MicEnergySpeakerAttributor(
        envelope_loader=lambda _paths, _reference: object(),
        speaker_assigner=lambda _words, _envelopes: assignments,
        algorithm_config={"algorithm": "fixture", "version": 1},
    )


def test_speaker_track_input_is_transport_only_and_validated(tmp_path: Path) -> None:
    track = SpeakerTrackInput(tmp_path / "host.wav", "HOST")

    assert track.path == tmp_path / "host.wav"
    assert track.speaker_label == "HOST"
    with pytest.raises(ValueError, match="stable non-blank"):
        SpeakerTrackInput(track.path, " HOST ")


def test_speaker_attribution_is_new_content_addressed_evidence(tmp_path: Path) -> None:
    audio, left, right, base, request = _fixture(tmp_path)

    def load_envelopes(paths: list[Path], reference: Path | None) -> object:
        assert paths == [left, right]
        assert reference == audio
        return object()

    def assign(words: list[dict[str, object]], _envelopes: object) -> list[int | None]:
        assert [word["word"] for word in words] == ["安", "吉"]
        return [0, 1]

    attributor = MicEnergySpeakerAttributor(
        envelope_loader=load_envelopes,
        speaker_assigner=assign,
        algorithm_config={"algorithm": "fixture", "version": 1},
    )
    assert isinstance(attributor, SpeakerAttributor)
    assert attributor.adapter_name == "mic-energy-speaker-attribution"
    assert attributor.adapter_version == "1"
    assert len(attributor.adapter_config_hash) == 64
    result = attributor.attribute(request)

    assert base.tokens[0].speaker is None
    assert result.adapter == "mic-energy-speaker-attribution"
    assert [token.speaker for token in result.tokens] == ["HOST", "GUEST"]
    artifact = next((tmp_path / "raw").glob("speaker-attribution-*.json"))
    assert hash_file(artifact) == result.raw_output_hash
    assert all(
        any(ref.startswith("recognition:") for ref in token.evidence_refs)
        for token in result.tokens
    )
    provenance = attributor.verify(
        result,
        base_evidence=base,
        raw_output=artifact.read_bytes(),
    )
    assert [track.speaker_label for track in provenance.tracks] == ["HOST", "GUEST"]
    assert [track.sha256 for track in provenance.tracks] == [hash_file(left), hash_file(right)]
    assert (
        SpeakerAttributionProvenance.from_payload(json.loads(canonical_json_bytes(provenance)))
        == provenance
    )


def test_speaker_attribution_rejects_unresolved_or_invalid_slots(tmp_path: Path) -> None:
    *_, request = _fixture(tmp_path)

    with pytest.raises(AdapterInputError, match="unresolved"):
        _attributor([0, None]).attribute(request)
    with pytest.raises(AdapterInputError, match="invalid track slots"):
        _attributor([0, 4]).attribute(request)


def test_production_assigner_rejects_token_beyond_shorter_mic_coverage(tmp_path: Path) -> None:
    *_, request = _fixture(tmp_path)
    frame_count = int(0.1 / FRAME_SEC)
    envelopes = np.full((2, frame_count), 1e-6, dtype=np.float32)
    envelopes[0, :] = 0.1
    attributor = MicEnergySpeakerAttributor(
        envelope_loader=lambda _paths, _reference: envelopes,
        speaker_assigner=assign_word_speakers,
        algorithm_config={
            "algorithm": "two-track-rms-viterbi",
            "algorithm_version": 2,
            "env_sample_rate": ENV_SR,
            "frame_samples": FRAME,
        },
    )

    with pytest.raises(AdapterInputError, match="unresolved"):
        attributor.attribute(request)


def test_speaker_attribution_rejects_track_bytes_changed_before_dsp(tmp_path: Path) -> None:
    _, left, _, _, request = _fixture(tmp_path)
    left.write_bytes(b"different")

    with pytest.raises(AdapterIntegrityError, match="Module binding"):
        _attributor([0, 1]).attribute(request)


def test_speaker_attribution_detects_track_mutation_during_dsp(tmp_path: Path) -> None:
    _, left, _, _, request = _fixture(tmp_path)

    def mutate_track(_paths: list[Path], _reference: Path | None) -> object:
        left.write_bytes(b"changed")
        return object()

    attributor = MicEnergySpeakerAttributor(
        envelope_loader=mutate_track,
        speaker_assigner=lambda _words, _envelopes: [0, 1],
        algorithm_config={"algorithm": "fixture", "version": 1},
    )
    with pytest.raises(AdapterIntegrityError, match="changed"):
        attributor.attribute(request)


def test_attribution_request_rejects_cross_invocation_and_lineage(tmp_path: Path) -> None:
    audio, _, _, base, request = _fixture(tmp_path)
    other_invocation = request.recognition_request.__class__(
        episode_id="episode",
        invocation_id="other",
        normalized_audio=audio,
        expected_normalized_audio_hash=hash_file(audio),
        raw_output_dir=tmp_path / "raw",
    )
    with pytest.raises(ValueError, match="another invocation"):
        SpeakerAttributionRequest(other_invocation, base, request.tracks)

    other_hash = "0" * 64
    other_lineage = request.recognition_request.__class__(
        episode_id="episode",
        invocation_id="invocation",
        normalized_audio=audio,
        expected_normalized_audio_hash=other_hash,
        raw_output_dir=tmp_path / "raw",
    )
    with pytest.raises(ValueError, match="another normalized-audio clock"):
        SpeakerAttributionRequest(other_lineage, base, request.tracks)


def test_attribution_request_rejects_label_or_track_aliases(tmp_path: Path) -> None:
    *_, request = _fixture(tmp_path)
    first, second = request.tracks
    with pytest.raises(ValueError, match="labels must be unique"):
        SpeakerAttributionRequest(
            request.recognition_request,
            request.base_evidence,
            (first, _binding(second.path, first.speaker_label)),
        )
    with pytest.raises(ValueError, match="byte identities must be distinct"):
        SpeakerAttributionRequest(
            request.recognition_request,
            request.base_evidence,
            (
                first,
                SpeakerTrackBinding(
                    path=second.path,
                    speaker_label=second.speaker_label,
                    sha256=first.sha256,
                    size_bytes=first.size_bytes,
                ),
            ),
        )


def test_restart_verifier_rejects_tampered_speaker_or_raw_proof(tmp_path: Path) -> None:
    *_, base, request = _fixture(tmp_path)
    attributor = _attributor([0, 1])
    attributed = attributor.attribute(request)
    artifact = next((tmp_path / "raw").glob("speaker-attribution-*.json"))
    raw_output = artifact.read_bytes()

    tampered_tokens = (
        attributed.tokens[0].model_copy(update={"speaker": "GUEST"}),
        attributed.tokens[1],
    )
    tampered = attributed.model_copy(update={"tokens": tampered_tokens})
    with pytest.raises(AdapterIntegrityError, match="differs from its stored proof"):
        attributor.verify(tampered, base_evidence=base, raw_output=raw_output)

    with pytest.raises(AdapterIntegrityError, match="bytes differ"):
        attributor.verify(
            attributed,
            base_evidence=base,
            raw_output=raw_output.replace(b'"HOST"', b'"GUEST"', 1),
        )


def test_restart_verifier_rejects_cross_base_evidence(tmp_path: Path) -> None:
    audio, _, _, base, request = _fixture(tmp_path)
    attributor = _attributor([0, 1])
    attributed = attributor.attribute(request)
    artifact = next((tmp_path / "raw").glob("speaker-attribution-*.json"))
    other_raw = tmp_path / "other.json"
    other_raw.write_bytes(b'{"other":true}')
    cross_base = base.model_copy(
        update={
            "raw_output": ArtifactDigest(
                uri=other_raw.resolve().as_uri(),
                sha256=hash_file(other_raw),
                size_bytes=other_raw.stat().st_size,
            ),
            "raw_output_hash": hash_file(other_raw),
            "normalized_audio_hash": hash_file(audio),
        }
    )

    with pytest.raises(AdapterIntegrityError, match="base Evidence"):
        attributor.verify(
            attributed,
            base_evidence=cross_base,
            raw_output=artifact.read_bytes(),
        )
