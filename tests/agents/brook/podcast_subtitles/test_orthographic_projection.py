from __future__ import annotations

import json

import pytest

from agents.brook.podcast_subtitles.canonical import reconcile_canonical
from agents.brook.podcast_subtitles.hashing import canonical_json_bytes, sha256_bytes
from agents.brook.podcast_subtitles.orthographic_projection import (
    OrthographicProjectionError,
    OrthographicProjectionEvidenceV1,
    measure_opencc_s2tw_identity,
    orthographic_projection_evidence_set_hash,
    orthographic_projection_raw_output_bytes,
    project_full_text_exact_length,
    project_recognition_evidence,
    project_recognition_evidence_set,
    verify_orthographic_projection,
)
from shared.schemas.podcast_subtitles_v2 import (
    ArtifactDigest,
    EvidenceToken,
    RecognitionEvidence,
    recognition_evidence_content_hash,
    recognition_evidence_set_hash,
)

H_AUDIO = "a" * 64
H_CONFIG = "b" * 64
H_SOURCE = "c" * 64
H_RECEIPT = "d" * 64
H_POLICY = "e" * 64


def _recognition(*texts: str) -> RecognitionEvidence:
    raw_bytes = canonical_json_bytes({"provider_tokens": texts})
    raw_hash = sha256_bytes(raw_bytes)
    return RecognitionEvidence(
        episode_id="episode-orthography",
        invocation_id="recognition-1",
        adapter="fixture-qwen",
        model="fixture-model",
        language="zh",
        config_hash=H_CONFIG,
        raw_output=ArtifactDigest(
            uri=f"fixture://recognition/{raw_hash}",
            sha256=raw_hash,
            size_bytes=len(raw_bytes),
        ),
        raw_output_hash=raw_hash,
        normalized_audio_hash=H_AUDIO,
        tokens=tuple(
            EvidenceToken(
                id=f"raw-{index}",
                text=text,
                start_ms=index * 100,
                end_ms=(index + 1) * 100,
                confidence=0.99,
                speaker="guest",
            )
            for index, text in enumerate(texts)
        ),
    )


def test_s2tw_projects_full_text_across_token_boundaries_without_mutating_raw() -> None:
    raw = _recognition("头", "发", "类型", " Linux 2026", "類型")
    before = canonical_json_bytes(raw)

    projected = project_recognition_evidence(
        raw,
        source_evidence_set_hash=recognition_evidence_set_hash((raw,)),
    )

    assert tuple(item.projected_text for item in projected.tokens) == (
        "頭",
        "髮",
        "類型",
        " Linux 2026",
        "類型",
    )
    assert projected.conversion.name == "s2tw"
    assert projected.source_recognition_evidence_hash == recognition_evidence_content_hash(raw)
    assert canonical_json_bytes(raw) == before
    verify_orthographic_projection(
        projected,
        (raw,),
        raw_output=orthographic_projection_raw_output_bytes(projected),
    )


def test_length_changing_or_literal_changing_projection_fails_closed() -> None:
    with pytest.raises(OrthographicProjectionError, match="Unicode scalar length"):
        project_full_text_exact_length(("一", "二"), "壹")
    with pytest.raises(OrthographicProjectionError, match="ASCII/Latin/numeric"):
        project_full_text_exact_length(("Linux 2026",), "Linux 2027")


def test_multi_option_dictionary_selection_is_measured_and_requires_full_audit() -> None:
    raw = _recognition("不", "准", "个")

    first = project_recognition_evidence(raw)
    second = project_recognition_evidence(raw)

    assert first == second
    assert tuple(item.projected_text for item in first.tokens) == ("不", "準", "個")
    assert tuple(
        (item.source_entry, item.selected_candidate, item.candidates, item.source_token_ids)
        for item in first.ambiguities
    ) == (
        ("不准", "不準", ("不準", "不准"), ("raw-0", "raw-1")),
        ("个", "個", ("個", "箇"), ("raw-2",)),
    )
    assert all(item.classification == "requires_full_audit" for item in first.ambiguities)
    identity = measure_opencc_s2tw_identity()
    assert identity.ambiguous_entry_count == 314
    assert tuple(item.entry_count for item in identity.ambiguous_dictionaries) == (75, 239, 0)


def test_projection_tamper_and_wrong_raw_output_fail_fresh_replay() -> None:
    raw = _recognition("类型", "约会对象", "竞争的对象")
    projected = project_recognition_evidence(raw)
    payload = json.loads(canonical_json_bytes(projected))
    payload["tokens"][0]["projected_text"] = "型別"

    with pytest.raises(ValueError, match="projected transcript hash mismatch"):
        OrthographicProjectionEvidenceV1.model_validate_json(canonical_json_bytes(payload))
    with pytest.raises(OrthographicProjectionError, match="persisted orthographic raw output"):
        verify_orthographic_projection(projected, (raw,), raw_output=b"tampered")


def test_s2twp_regressions_remain_unlocalized_under_fixed_s2tw() -> None:
    raw = _recognition("类型", "约会对象", "竞争的对象")

    projected = project_recognition_evidence(raw)

    assert tuple(item.projected_text for item in projected.tokens) == (
        "類型",
        "約會對象",
        "競爭的對象",
    )
    assert "型別" not in "".join(item.projected_text for item in projected.tokens)
    assert "物件" not in "".join(item.projected_text for item in projected.tokens)


def test_canonical_consumes_verified_projection_but_keeps_raw_token_lineage() -> None:
    raw = _recognition("类型", "约会对象", "競爭的對象")
    projected = project_recognition_evidence_set((raw,))

    result = reconcile_canonical(
        primary=raw,
        source_audio_hash=H_SOURCE,
        normalization_receipt_hash=H_RECEIPT,
        policy_hash=H_POLICY,
        orthographic_projections=projected,
    )

    assert tuple(token.text for token in result.transcript.tokens) == (
        "類型",
        "約會對象",
        "競爭的對象",
    )
    assert result.transcript.evidence_hash == recognition_evidence_set_hash((raw,))
    assert result.transcript.orthographic_projection_evidence_hash == (
        orthographic_projection_evidence_set_hash(projected)
    )
    raw_hash = recognition_evidence_content_hash(raw)
    assert tuple(token.evidence_ids for token in result.transcript.tokens) == tuple(
        (f"evidence:{raw_hash}:{source.id}",) for source in raw.tokens
    )


def test_canonical_multi_option_projection_is_blocking_until_explicit_resolution() -> None:
    raw = _recognition("不", "准", "个")
    projected = project_recognition_evidence_set((raw,))

    result = reconcile_canonical(
        primary=raw,
        source_audio_hash=H_SOURCE,
        normalization_receipt_hash=H_RECEIPT,
        policy_hash=H_POLICY,
        orthographic_projections=projected,
    )

    issues = tuple(
        issue
        for issue in result.transcript.review_issues
        if issue.code == "orthographic_multi_option_ambiguity"
    )
    assert result.transcript.status == "draft"
    assert len(issues) == 2
    assert all(issue.severity == "medium" and issue.status == "unresolved" for issue in issues)
