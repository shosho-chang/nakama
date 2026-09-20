from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.adapters.diarization_speaker import (
    ContentAddressedDiarizationAttributor,
)
from agents.brook.podcast_subtitles.hashing import canonical_json_bytes, hash_file, hash_object
from agents.brook.podcast_subtitles.ports import (
    AdapterInputError,
    AdapterIntegrityError,
    DiarizationModelIdentity,
    DiarizationPolicyV1,
    DiarizationRequest,
    SpeakerDiarizer,
)
from shared.schemas.podcast_subtitles_v2 import (
    ArtifactDigest,
    EvidenceToken,
    RecognitionEvidence,
)


def _base_evidence(audio: Path, raw: Path) -> RecognitionEvidence:
    return RecognitionEvidence(
        episode_id="episode",
        invocation_id="invocation",
        adapter="fixture-asr",
        model="fixture-asr-model",
        language="zh-Hant-TW",
        config_hash=hash_object({"fixture": "asr"}),
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
                start_ms=100,
                end_ms=200,
                confidence=0.95,
                evidence_refs=("raw:base",),
            ),
            EvidenceToken(
                id="base-2",
                text="吉",
                start_ms=300,
                end_ms=400,
                confidence=0.90,
                evidence_refs=("raw:base",),
            ),
        ),
    )


def _fixture(
    tmp_path: Path,
) -> tuple[
    ContentAddressedDiarizationAttributor,
    DiarizationRequest,
    RecognitionEvidence,
]:
    audio = tmp_path / "normalized.wav"
    audio.write_bytes(b"normalized-audio-evidence")
    raw = tmp_path / "recognition.json"
    raw.write_bytes(b"{}")
    base = _base_evidence(audio, raw)
    policy = DiarizationPolicyV1(
        minimum_segment_confidence=0.80,
        minimum_assignment_margin=0.10,
    )
    runtime_components = (("diarizer", "1.2.3"), ("python", "3.12.10"))
    identity = DiarizationModelIdentity(
        adapter_name="fixture-diarization-import",
        adapter_version="1.0.0",
        model="fixture-diarizer",
        model_revision="a" * 40,
        runtime_components=runtime_components,
        runtime_hash=hash_object({"runtime_components": runtime_components}),
        adapter_code_hash="b" * 64,
        config_hash=policy.content_hash,
        execution_mode="import",
    )
    adapter = ContentAddressedDiarizationAttributor(identity=identity, policy=policy)
    request = DiarizationRequest(
        episode_id="episode",
        invocation_id="invocation",
        normalized_audio=audio,
        expected_normalized_audio_hash=hash_file(audio),
        expected_normalized_audio_size_bytes=audio.stat().st_size,
        normalized_audio_duration_ms=1_000,
        base_evidence=base,
        raw_output_dir=tmp_path / "diarization",
    )
    return adapter, request, base


def _response(
    adapter: ContentAddressedDiarizationAttributor,
    request: DiarizationRequest,
) -> bytes:
    packet = json.loads(adapter.prepare(request))
    return canonical_json_bytes(
        {
            "schema_version": 1,
            "work_packet_id": packet["work_packet_id"],
            "adapter_identity_hash": adapter.identity.content_hash,
            "normalized_audio_hash": request.expected_normalized_audio_hash,
            "normalized_audio_duration_ms": request.normalized_audio_duration_ms,
            "status": "completed",
            "speaker_labels": ["speaker_0000", "speaker_0001"],
            "segments": [
                {
                    "start_ms": 50,
                    "end_ms": 250,
                    "scores": [
                        {"speaker": "speaker_0000", "confidence": 0.95},
                        {"speaker": "speaker_0001", "confidence": 0.05},
                    ],
                },
                {
                    "start_ms": 250,
                    "end_ms": 450,
                    "scores": [
                        {"speaker": "speaker_0000", "confidence": 0.10},
                        {"speaker": "speaker_0001", "confidence": 0.90},
                    ],
                },
            ],
        }
    )


def _mutate_response(raw: bytes, update: object) -> bytes:
    payload = json.loads(raw)
    update(payload)
    return canonical_json_bytes(payload)


def test_complete_audio_bound_diarization_materializes_and_replays_new_evidence(
    tmp_path: Path,
) -> None:
    adapter, request, base = _fixture(tmp_path)

    assert isinstance(adapter, SpeakerDiarizer)
    response_bytes = _response(adapter, request)
    result = adapter.materialize(request, response_bytes=response_bytes)

    assert all(token.speaker is None for token in base.tokens)
    assert tuple(token.speaker for token in result.evidence.tokens) == (
        "speaker_0000",
        "speaker_0001",
    )
    assert tuple(token.text for token in result.evidence.tokens) == ("安", "吉")
    assert result.receipt.normalized_audio.sha256 == hash_file(request.normalized_audio)
    assert result.receipt.materialized_recognition_evidence_hash == result.evidence_hash
    assert result.request_bytes == adapter.prepare(request)
    assert result.response_bytes == response_bytes

    assert adapter.verify(request, result=result) == result


@pytest.mark.parametrize(
    ("segment_index", "field_name", "value"),
    ((0, "start_ms", 150), (1, "end_ms", 350)),
    ids=("leading-token-gap", "trailing-token-gap"),
)
def test_token_in_leading_or_trailing_diarization_gap_remains_unresolved(
    tmp_path: Path,
    segment_index: int,
    field_name: str,
    value: int,
) -> None:
    adapter, request, _base = _fixture(tmp_path)
    response = _mutate_response(
        _response(adapter, request),
        lambda payload: payload["segments"][segment_index].__setitem__(field_name, value),
    )

    with pytest.raises(AdapterInputError, match="lacks complete diarization coverage"):
        adapter.materialize(request, response_bytes=response)


def test_overlapping_diarization_segments_are_rejected(tmp_path: Path) -> None:
    adapter, request, _base = _fixture(tmp_path)
    response = _mutate_response(
        _response(adapter, request),
        lambda payload: payload["segments"][1].__setitem__("start_ms", 200),
    )

    with pytest.raises(AdapterInputError, match="overlap or are reordered"):
        adapter.materialize(request, response_bytes=response)


def test_unknown_or_identity_bearing_speaker_label_is_rejected(tmp_path: Path) -> None:
    adapter, request, _base = _fixture(tmp_path)
    unknown_score = _mutate_response(
        _response(adapter, request),
        lambda payload: payload["segments"][0]["scores"][0].__setitem__(
            "speaker", "HOST"
        ),
    )

    with pytest.raises(AdapterInputError, match="unknown or reordered"):
        adapter.materialize(request, response_bytes=unknown_score)

    named_roster = _mutate_response(
        _response(adapter, request),
        lambda payload: payload.__setitem__("speaker_labels", ["HOST", "GUEST"]),
    )
    with pytest.raises(AdapterInputError, match="canonical opaque labels"):
        adapter.materialize(request, response_bytes=named_roster)


def test_opaque_speaker_labels_are_canonicalized_by_first_appearance(
    tmp_path: Path,
) -> None:
    adapter, request, _base = _fixture(tmp_path)

    def reverse_winners(payload: dict[str, object]) -> None:
        for segment in payload["segments"]:
            scores = segment["scores"]
            scores[0]["confidence"], scores[1]["confidence"] = (
                scores[1]["confidence"],
                scores[0]["confidence"],
            )

    response = _mutate_response(_response(adapter, request), reverse_winners)
    with pytest.raises(AdapterInputError, match="first-appearance order"):
        adapter.materialize(request, response_bytes=response)


def test_token_crossing_two_speaker_segments_is_unresolved(tmp_path: Path) -> None:
    adapter, request, base = _fixture(tmp_path)
    crossing_tokens = (
        base.tokens[0],
        base.tokens[1].model_copy(update={"start_ms": 200, "end_ms": 300}),
    )
    crossing_request = DiarizationRequest(
        episode_id=request.episode_id,
        invocation_id=request.invocation_id,
        normalized_audio=request.normalized_audio,
        expected_normalized_audio_hash=request.expected_normalized_audio_hash,
        expected_normalized_audio_size_bytes=request.expected_normalized_audio_size_bytes,
        normalized_audio_duration_ms=request.normalized_audio_duration_ms,
        base_evidence=base.model_copy(update={"tokens": crossing_tokens}),
        raw_output_dir=request.raw_output_dir,
    )
    response = _mutate_response(
        _response(adapter, crossing_request),
        lambda payload: (
            payload["segments"][0].__setitem__("end_ms", 250),
            payload["segments"][1].__setitem__("start_ms", 250),
        ),
    )

    with pytest.raises(AdapterInputError, match="crosses a diarization speaker boundary"):
        adapter.materialize(crossing_request, response_bytes=response)


def test_diarization_does_not_auto_accept_whole_token_stream_as_one_speaker(
    tmp_path: Path,
) -> None:
    adapter, request, _base = _fixture(tmp_path)

    def move_second_observation_after_tokens(payload: dict[str, object]) -> None:
        first, second = payload["segments"]
        first["end_ms"] = 450
        second["start_ms"] = 450
        second["end_ms"] = 500

    response = _mutate_response(
        _response(adapter, request), move_second_observation_after_tokens
    )
    with pytest.raises(AdapterInputError, match="whole token stream as one speaker"):
        adapter.materialize(request, response_bytes=response)


@pytest.mark.parametrize(
    ("scores", "message"),
    (
        ((0.79, 0.21), "confidence is below policy"),
        ((0.54, 0.46), "assignment is ambiguous"),
        ((0.50, 0.50), "assignment is ambiguous"),
    ),
    ids=("low-confidence", "low-margin", "exact-tie"),
)
def test_low_confidence_or_ambiguous_segment_is_unresolved(
    tmp_path: Path,
    scores: tuple[float, float],
    message: str,
) -> None:
    adapter, request, _base = _fixture(tmp_path)

    def update(payload: dict[str, object]) -> None:
        first = payload["segments"][0]["scores"]
        first[0]["confidence"] = scores[0]
        first[1]["confidence"] = scores[1]

    response = _mutate_response(_response(adapter, request), update)
    with pytest.raises(AdapterInputError, match=message):
        adapter.materialize(request, response_bytes=response)


def test_missing_speaker_confidence_cannot_materialize_evidence(tmp_path: Path) -> None:
    adapter, request, _base = _fixture(tmp_path)
    response = _mutate_response(
        _response(adapter, request),
        lambda payload: payload["segments"][0]["scores"][0].pop("confidence"),
    )

    with pytest.raises(AdapterIntegrityError, match="invalid strict contract"):
        adapter.materialize(request, response_bytes=response)


def test_audio_hash_drift_is_rejected_before_packet_or_materialization(tmp_path: Path) -> None:
    adapter, request, _base = _fixture(tmp_path)
    response = _response(adapter, request)
    request.normalized_audio.write_bytes(b"changed-after-request-binding")

    with pytest.raises(AdapterIntegrityError, match="differs from the exact request binding"):
        adapter.prepare(request)
    with pytest.raises(AdapterIntegrityError, match="differs from the exact request binding"):
        adapter.materialize(request, response_bytes=response)


def test_missing_local_audio_cannot_create_a_diarization_packet(tmp_path: Path) -> None:
    adapter, request, _base = _fixture(tmp_path)
    missing = request.__class__(
        episode_id=request.episode_id,
        invocation_id=request.invocation_id,
        normalized_audio=tmp_path / "does-not-exist.wav",
        expected_normalized_audio_hash=request.expected_normalized_audio_hash,
        expected_normalized_audio_size_bytes=request.expected_normalized_audio_size_bytes,
        normalized_audio_duration_ms=request.normalized_audio_duration_ms,
        base_evidence=request.base_evidence,
        raw_output_dir=request.raw_output_dir,
    )

    with pytest.raises(AdapterInputError, match="normalized audio is unavailable"):
        adapter.prepare(missing)


def test_response_tamper_and_noncanonical_bytes_are_rejected(tmp_path: Path) -> None:
    adapter, request, _base = _fixture(tmp_path)
    response = _response(adapter, request)
    crossed_audio = _mutate_response(
        response,
        lambda payload: payload.__setitem__("normalized_audio_hash", "0" * 64),
    )
    with pytest.raises(AdapterIntegrityError, match="crossed request, audio"):
        adapter.materialize(request, response_bytes=crossed_audio)

    with pytest.raises(AdapterIntegrityError, match="not canonical JSON"):
        adapter.materialize(request, response_bytes=response + b"\n")

    accepted = adapter.materialize(request, response_bytes=response)
    with pytest.raises(ValueError, match="response bytes differ from receipt"):
        replace(accepted, response_bytes=response + b"\n")
    tampered_evidence = accepted.evidence.model_copy(
        update={
            "tokens": (
                accepted.evidence.tokens[0].model_copy(update={"speaker": "speaker_0001"}),
                accepted.evidence.tokens[1],
            )
        }
    )
    with pytest.raises(ValueError, match="Evidence differs from receipt"):
        replace(accepted, evidence=tampered_evidence)


def test_diarization_identity_rejects_floating_model_revision(tmp_path: Path) -> None:
    adapter, _request, _base = _fixture(tmp_path)
    with pytest.raises(ValueError, match="pinned content revision"):
        DiarizationModelIdentity(
            adapter_name=adapter.identity.adapter_name,
            adapter_version=adapter.identity.adapter_version,
            model=adapter.identity.model,
            model_revision="latest",
            runtime_components=adapter.identity.runtime_components,
            runtime_hash=adapter.identity.runtime_hash,
            adapter_code_hash=adapter.identity.adapter_code_hash,
            config_hash=adapter.identity.config_hash,
            execution_mode="import",
        )


def test_materialization_is_deterministic_across_fresh_adapter_instances(
    tmp_path: Path,
) -> None:
    adapter, request, _base = _fixture(tmp_path)
    response = _response(adapter, request)
    first = adapter.materialize(request, response_bytes=response)
    fresh = ContentAddressedDiarizationAttributor(
        identity=adapter.identity,
        policy=adapter.policy,
    )

    second = fresh.materialize(request, response_bytes=response)

    assert second == first
    assert fresh.verify(request, result=first) == first
