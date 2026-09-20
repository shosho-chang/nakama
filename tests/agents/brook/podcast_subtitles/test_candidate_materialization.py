from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from agents.brook.podcast_subtitles.candidate_materialization import (
    CANDIDATE_MATERIALIZER_ID,
    CANDIDATE_MATERIALIZER_VERSION,
    SourceBoundTranscriptCandidateArtifactV3,
    build_source_bound_transcript_candidate,
    candidate_materializer_code_hash,
    verify_source_bound_transcript_candidate,
)
from agents.brook.podcast_subtitles.hashing import canonical_json_bytes, hash_object
from agents.brook.podcast_subtitles.recognition_request import RecognitionRequestArtifactV1
from agents.brook.podcast_subtitles.transcript_gold import (
    AdjudicationProvenance,
    AudioClipBinding,
    AudioOnlyTranscriptSubmission,
    GoldClipLabel,
    GoldToken,
    TranscriptAdjudicationRecord,
    TranscriptAnnotationPacket,
    TranscriptAnnotationProtocol,
    TranscriptEvaluationStatus,
    TranscriptGoldSuite,
    evaluate_transcript_candidate,
    load_transcript_candidate,
)
from shared.schemas.podcast_subtitles_v2 import ArtifactDigest, EvidenceToken, RecognitionEvidence


def _h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _sources(tokens: tuple[EvidenceToken, ...] | None = None):
    clip = AudioClipBinding.build(
        clip_id="clip-1",
        start_ms=2_000,
        end_ms=3_000,
        clip_audio_hash=_h("clip"),
        clip_audio_size_bytes=1_000,
        normalized_audio_hash=_h("episode"),
        normalized_audio_size_bytes=9_000,
    )
    packet = TranscriptAnnotationPacket.build(
        packet_id="packet",
        episode_id="episode",
        normalized_audio_hash=clip.normalized_audio_hash,
        normalized_audio_size_bytes=clip.normalized_audio_size_bytes,
        instruction_profile_id="audio-only",
        protocol=TranscriptAnnotationProtocol(protocol_id="protocol"),
        clips=(clip,),
    )
    request_payload = {
        "schema_version": 1,
        "episode_id": "episode",
        "invocation_id": "invoke",
        "normalized_audio_sha256": clip.clip_audio_hash,
        "normalized_audio_size_bytes": clip.clip_audio_size_bytes,
        "language_hint": "zh-TW",
        "context_policy_id": "nakama-verbatim-no-lexical-context-v1",
        "context_sha256": _h(""),
        "context_size_bytes": 0,
    }
    request = RecognitionRequestArtifactV1(
        **request_payload, content_hash=hash_object(request_payload)
    )
    selected = tokens or (
        EvidenceToken(id="t1", text="安", start_ms=0, end_ms=400),
        EvidenceToken(id="t2", text="吉", start_ms=600, end_ms=800),
    )
    evidence = RecognitionEvidence(
        episode_id="episode",
        invocation_id="invoke",
        adapter="fixture",
        model="fixture-v1",
        language="zh-TW",
        config_hash=_h("config"),
        raw_output=ArtifactDigest(uri="digest://raw", sha256=_h("raw"), size_bytes=3),
        raw_output_hash=_h("raw"),
        normalized_audio_hash=clip.clip_audio_hash,
        tokens=selected,
    )
    return packet, request, evidence


def _candidate(tokens: tuple[EvidenceToken, ...] | None = None):
    packet, request, evidence = _sources(tokens)
    candidate = build_source_bound_transcript_candidate(
        candidate_id="candidate", system_id="recognizer", annotation_packet=packet,
        requests=(request,), evidence=(evidence,),
    )
    return packet, candidate


def _updated_request(
    request: RecognitionRequestArtifactV1, updates: dict[str, object]
) -> RecognitionRequestArtifactV1:
    payload = request.model_dump(mode="json", exclude={"content_hash"})
    payload.update(updates)
    return RecognitionRequestArtifactV1(**payload, content_hash=hash_object(payload))


def _rehash_candidate(data: dict[str, object]) -> None:
    receipt = data["materialization_receipt"]
    assert isinstance(receipt, dict)
    receipt["receipt_hash"] = hash_object({
        "artifact_kind": "candidate_materialization_receipt_v3",
        **{key: value for key, value in receipt.items() if key != "receipt_hash"},
    })
    data["generation_id"] = receipt["receipt_hash"]
    data["artifact_hash"] = hash_object({
        "artifact_kind": "transcript_candidate_artifact",
        **{key: value for key, value in data.items() if key != "artifact_hash"},
    })


def _rehash_materialization(data: dict[str, object]) -> None:
    materials = data["source_materializations"]
    receipt = data["materialization_receipt"]
    assert isinstance(materials, list) and isinstance(receipt, dict)
    material = materials[0]
    assert isinstance(material, dict)
    material["materialization_hash"] = hash_object({
        "artifact_kind": "source_bound_candidate_clip_v3",
        **{key: value for key, value in material.items() if key != "materialization_hash"},
    })
    receipt["materialization_hashes"] = [material["materialization_hash"]]
    _rehash_candidate(data)


def test_offsets_tokens_and_types_internal_and_edge_no_output_gaps() -> None:
    packet, candidate = _candidate()
    spans = candidate.source_materializations[0].spans
    assert [(s.source_kind, s.start_ms, s.end_ms, s.text) for s in spans] == [
        ("recognition_token", 2_000, 2_400, "安"),
        ("no_text_emitted_by_recognizer", 2_400, 2_600, ""),
        ("recognition_token", 2_600, 2_800, "吉"),
        ("no_text_emitted_by_recognizer", 2_800, 3_000, ""),
    ]
    assert candidate.clips[0].text == "安吉"
    assert candidate.generation_id == candidate.materialization_receipt.receipt_hash
    assert verify_source_bound_transcript_candidate(candidate, packet) is candidate
    assert load_transcript_candidate(candidate.canonical_bytes()) == candidate


def test_adjacent_tokens_have_only_trailing_no_output_gap() -> None:
    _, candidate = _candidate((
        EvidenceToken(id="a", text="安", start_ms=0, end_ms=400),
        EvidenceToken(id="b", text="吉", start_ms=400, end_ms=800),
    ))
    assert [s.source_kind for s in candidate.source_materializations[0].spans] == [
        "recognition_token", "recognition_token", "no_text_emitted_by_recognizer"
    ]


def test_leading_gap_is_typed_no_output_not_silence() -> None:
    _, candidate = _candidate((
        EvidenceToken(id="a", text="安", start_ms=200, end_ms=800),
    ))
    first = candidate.source_materializations[0].spans[0]
    assert (first.source_kind, first.start_ms, first.end_ms, first.text) == (
        "no_text_emitted_by_recognizer", 2_000, 2_200, ""
    )
    assert "silence" not in canonical_json_bytes(candidate).decode("utf-8")


@pytest.mark.parametrize(
    ("request_update", "evidence_update", "message"),
    [
        ({"normalized_audio_sha256": _h("wrong")}, {}, "exact clip bytes"),
        ({"normalized_audio_size_bytes": 999}, {}, "exact clip bytes"),
        ({"episode_id": "other"}, {}, "episode mismatch"),
        ({}, {"episode_id": "other"}, "episode mismatch"),
        ({"invocation_id": "other"}, {}, "invocation mismatch"),
        ({}, {"invocation_id": "other"}, "invocation mismatch"),
    ],
)
def test_request_and_evidence_source_binding_mismatch_fails(
    request_update: dict[str, object],
    evidence_update: dict[str, object],
    message: str,
) -> None:
    packet, request, evidence = _sources()
    with pytest.raises(ValueError, match=message):
        build_source_bound_transcript_candidate(
            candidate_id="candidate",
            system_id="system",
            annotation_packet=packet,
            requests=(_updated_request(request, request_update),),
            evidence=(evidence.model_copy(update=evidence_update),),
        )


def test_token_outside_or_overlap_fails_closed() -> None:
    packet, request, evidence = _sources()
    outside = evidence.model_copy(update={"tokens": (
        EvidenceToken(id="x", text="x", start_ms=900, end_ms=1_001),
    )})
    with pytest.raises(ValueError, match="escapes exact clip"):
        build_source_bound_transcript_candidate(
            candidate_id="c", system_id="s", annotation_packet=packet,
            requests=(request,), evidence=(outside,),
        )
    overlap = evidence.model_dump(mode="json")
    overlap["tokens"] = [
        EvidenceToken(id="a", text="a", start_ms=0, end_ms=600).model_dump(mode="json"),
        EvidenceToken(id="b", text="b", start_ms=500, end_ms=800).model_dump(mode="json"),
    ]
    with pytest.raises(ValidationError, match="monotonic and non-overlapping"):
        RecognitionEvidence.model_validate(overlap)


@pytest.mark.parametrize("field", ["text", "start_ms", "end_ms", "span_id", "source_kind"])
def test_tampered_derived_span_rejects_even_with_outer_hash_recomputed(field: str) -> None:
    _, candidate = _candidate()
    data = candidate.model_dump(mode="json")
    span = data["source_materializations"][0]["spans"][0]
    span[field] = {
        "text": "改", "start_ms": 2_001, "end_ms": 2_399,
        "span_id": "tampered", "source_kind": "no_text_emitted_by_recognizer",
    }[field]
    material = data["source_materializations"][0]
    material["coverage_hash"] = hash_object(material["spans"])
    material["materialization_hash"] = hash_object({
        "artifact_kind": "source_bound_candidate_clip_v3",
        **{k: v for k, v in material.items() if k != "materialization_hash"},
    })
    receipt = data["materialization_receipt"]
    receipt["coverage_hashes"] = [material["coverage_hash"]]
    receipt["materialization_hashes"] = [material["materialization_hash"]]
    receipt["receipt_hash"] = hash_object({
        "artifact_kind": "candidate_materialization_receipt_v3",
        **{k: v for k, v in receipt.items() if k != "receipt_hash"},
    })
    data["generation_id"] = receipt["receipt_hash"]
    data["artifact_hash"] = hash_object({
        "artifact_kind": "transcript_candidate_artifact",
        **{k: v for k, v in data.items() if k != "artifact_hash"},
    })
    with pytest.raises((ValidationError, ValueError)):
        SourceBoundTranscriptCandidateArtifactV3.model_validate_json(
            canonical_json_bytes(data)
        )


@pytest.mark.parametrize(
    ("field", "receipt_field"),
    [
        ("request_canonical_hash", "request_canonical_hashes"),
        ("request_content_hash", "request_content_hashes"),
        ("evidence_canonical_hash", "evidence_canonical_hashes"),
        ("evidence_content_hash", "evidence_content_hashes"),
    ],
)
def test_declared_source_hash_tamper_rejects_after_outer_rehash(
    field: str, receipt_field: str
) -> None:
    _, candidate = _candidate()
    data = candidate.model_dump(mode="json")
    material = data["source_materializations"][0]
    material[field] = _h(f"tampered:{field}")
    receipt = data["materialization_receipt"]
    receipt[receipt_field] = [material[field]]
    _rehash_materialization(data)
    with pytest.raises((ValidationError, ValueError)):
        SourceBoundTranscriptCandidateArtifactV3.model_validate_json(
            canonical_json_bytes(data)
        )


@pytest.mark.parametrize("token_field", ["text", "start_ms", "end_ms"])
def test_embedded_token_tamper_rejects_with_frozen_source_hash_and_outer_rehash(
    token_field: str,
) -> None:
    _, candidate = _candidate()
    data = candidate.model_dump(mode="json")
    token = data["source_materializations"][0]["recognition_evidence"]["tokens"][0]
    token[token_field] = {"text": "改", "start_ms": 1, "end_ms": 399}[token_field]
    _rehash_materialization(data)
    with pytest.raises((ValidationError, ValueError)):
        SourceBoundTranscriptCandidateArtifactV3.model_validate_json(
            canonical_json_bytes(data)
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("materializer_id", "other-materializer"),
        ("materializer_version", "999.0.0"),
        ("materializer_code_hash", _h("other-code")),
    ],
)
def test_materializer_identity_tamper_rejects_after_all_dependent_rehash(
    field: str, value: str
) -> None:
    _, candidate = _candidate()
    data = candidate.model_dump(mode="json")
    data["materialization_receipt"][field] = value
    _rehash_candidate(data)
    with pytest.raises((ValidationError, ValueError), match="executable identity"):
        SourceBoundTranscriptCandidateArtifactV3.model_validate_json(
            canonical_json_bytes(data)
        )


def test_materializer_identity_and_candidate_identifiers_are_exact() -> None:
    _, candidate = _candidate()
    receipt = candidate.materialization_receipt
    assert receipt.materializer_id == CANDIDATE_MATERIALIZER_ID
    assert receipt.materializer_version == CANDIDATE_MATERIALIZER_VERSION
    assert receipt.materializer_code_hash == candidate_materializer_code_hash()
    packet, request, evidence = _sources()
    for field in ("candidate_id", "system_id"):
        kwargs = {"candidate_id": "candidate", "system_id": "system"}
        kwargs[field] = " "
        with pytest.raises(ValidationError, match="non-blank"):
            build_source_bound_transcript_candidate(
                **kwargs,
                annotation_packet=packet,
                requests=(request,),
                evidence=(evidence,),
            )


def test_noncanonical_and_correction_fields_rejected() -> None:
    _, candidate = _candidate()
    assert canonical_json_bytes(candidate) == candidate.canonical_bytes()
    data = json.loads(candidate.canonical_bytes())
    data["evaluation_scope"] = "corrected"
    with pytest.raises(ValueError):
        SourceBoundTranscriptCandidateArtifactV3.model_validate_json(
            canonical_json_bytes(data)
        )


def test_v3_scores_transcript_but_never_correction_metrics() -> None:
    packet, candidate = _candidate()
    clip = packet.clips[0]
    submissions = tuple(
        AudioOnlyTranscriptSubmission.build(
            submission_id=f"submission-{suffix}",
            annotation_packet_hash=packet.packet_hash,
            clip=clip,
            annotator_id=f"annotator-{suffix}",
            outcome="accepted",
            text="安吉",
            tokens=("安", "吉"),
        )
        for suffix in ("a", "b")
    )
    gold_tokens = (
        GoldToken(token_id="gold-a", text="安"),
        GoldToken(token_id="gold-b", text="吉"),
    )
    adjudication = TranscriptAdjudicationRecord.build(
        record_id="adjudication",
        annotation_packet_hash=packet.packet_hash,
        clip=clip,
        first_pass_submission_hashes=tuple(  # type: ignore[arg-type]
            item.submission_hash for item in submissions
        ),
        adjudicator_id="adjudicator-c",
        spelling_authority_uses=(),
        final_expected_outcome="accepted",
        final_text="安吉",
        final_tokens=gold_tokens,
    )
    suite = TranscriptGoldSuite.build(
        suite_id="gold",
        annotation_packet=packet,
        complete=True,
        labels=(
            GoldClipLabel(
                clip_id=clip.clip_id,
                expected_outcome="accepted",
                text="安吉",
                tokens=gold_tokens,
                provenance=AdjudicationProvenance(
                    first_pass_submissions=submissions,  # type: ignore[arg-type]
                    adjudication_record=adjudication,
                ),
            ),
        ),
    )
    result = evaluate_transcript_candidate(suite, candidate)
    assert result.status is TranscriptEvaluationStatus.EVALUATED
    assert result.metrics is not None
    assert result.metrics.lexical_character_error_rate.value == 0.0
    assert result.correction_metrics_status is TranscriptEvaluationStatus.NOT_EVALUATED
    assert result.correction_metrics_reason_codes == (
        "candidate_evaluation_scope_recognition_only",
    )
    data = json.loads(candidate.canonical_bytes())
    data["clips"][0]["corrections"] = [{"not": "allowed"}]
    with pytest.raises(ValueError):
        SourceBoundTranscriptCandidateArtifactV3.model_validate_json(
            canonical_json_bytes(data)
        )
