from __future__ import annotations

import json
from pathlib import Path

import pytest

import agents.brook.podcast_subtitles.accurate_punctuation as subject
from agents.brook.podcast_subtitles.accurate_correction import CorrectedTimedToken
from agents.brook.podcast_subtitles.accurate_recognition import (
    QwenOwnedRangeRepair,
    QwenOwnedWordSnapshot,
)
from agents.brook.podcast_subtitles.accurate_segmentation import (
    segment_accurate_subtitles,
)
from agents.brook.podcast_subtitles.adapters import recognition as qwen_recognition
from agents.brook.podcast_subtitles.hashing import (
    canonical_json_bytes,
    hash_object,
    sha256_bytes,
)
from shared.schemas.podcast_subtitles_v2 import (
    ArtifactDigest,
    EvidenceToken,
    RecognitionEvidence,
)


def _raw_envelope(
    path: Path,
    *,
    transcript: str = "大家好，歡迎你。下一句",
    aligned_texts: tuple[str, ...] = ("大家好", "歡迎你", "下一句"),
    audio_hash: str = "a" * 64,
) -> tuple[bytes, tuple[dict[str, object], ...]]:
    projection, separators = qwen_recognition._project_qwen_transcript(
        transcript,
        aligned_texts,
    )
    chunk_id = "qwen-chunk-0000"
    alignment_items: list[dict[str, object]] = []
    merged_words: list[dict[str, object]] = []
    token_rows: list[dict[str, object]] = []
    for index, item in enumerate(projection):
        start_ms = index * 300 + 100
        end_ms = start_ms + 200
        item_id = f"item-{index:04d}"
        alignment_items.append(
            {
                "id": item_id,
                "aligned_text": item["aligned_text"],
                "text": item["evidence_text"],
                "transcript_kept_indices": item["transcript_kept_indices"],
                "local_start_seconds": start_ms / 1_000,
                "local_end_seconds": end_ms / 1_000,
                "local_start_sample": start_ms,
                "local_end_sample": end_ms,
                "global_start_sample": start_ms,
                "global_end_sample": end_ms,
            }
        )
        merged_words.append(
            {
                "word": item["evidence_text"],
                "start_ms": start_ms,
                "end_ms": end_ms,
                "source_chunk_id": chunk_id,
                "source_item_id": item_id,
                "ownership": "alignment_midpoint_in_half_open_owned_interval_v1",
            }
        )
        token_rows.append(
            {
                "id": f"primary-{index}",
                "text": item["evidence_text"],
                "start_ms": start_ms,
                "end_ms": end_ms,
            }
        )

    payload = {
        "schema_version": 3,
        "kind": "qwen3_asr_bounded_overlap_evidence",
        "identity_hash": "b" * 64,
        "language": "zh",
        "normalized_audio": {
            "sha256": audio_hash,
            "size_bytes": 2_044,
            "sample_rate_hz": 1_000,
            "channels": 1,
            "sample_width_bytes": 2,
            "frame_count": 1_000,
            "duration_ms": 1_000,
            "compression_type": "NONE",
        },
        "planner": {
            "version": 1,
            "derivation": "pcm_wav_frame_slice_v1",
            "ownership": "alignment_midpoint_in_half_open_owned_interval_v1",
            "seam_comparison": "normalized_alignment_text_in_shared_context_v1",
            "owned_chunk_samples": 1_000,
            "seam_context_samples": 100,
        },
        "chunks": [
            {
                "id": chunk_id,
                "index": 0,
                "count": 1,
                "inference_start_sample": 0,
                "inference_end_sample": 1_000,
                "owned_start_sample": 0,
                "owned_end_sample": 1_000,
                "derived_audio_sha256": "c" * 64,
                "derived_audio_size_bytes": 2_044,
                "language": "zh",
                "transcript_text": transcript,
                "transcript_sha256": sha256_bytes(transcript.encode("utf-8")),
                "transcript_projection_rule": (
                    "qwen_forced_aligner_scalar_separator_classification_v1"
                ),
                "provider_output": {},
                "alignment_items": alignment_items,
                "separators": separators,
            }
        ],
        "seams": [],
        "merged_words": merged_words,
    }
    raw = canonical_json_bytes(payload)
    path.write_bytes(raw)
    return raw, tuple(token_rows)


def _qwen_fixture(
    tmp_path: Path,
) -> tuple[Path, RecognitionEvidence, tuple[CorrectedTimedToken, ...]]:
    raw_path = tmp_path / "qwen.json"
    raw, rows = _raw_envelope(raw_path)
    raw_hash = sha256_bytes(raw)
    evidence_tokens = tuple(
        EvidenceToken(
            **row,
            confidence=None,
            evidence_refs=(f"raw:{raw_hash}",),
        )
        for row in rows
    )
    evidence = RecognitionEvidence(
        episode_id="episode",
        invocation_id="invocation",
        adapter=qwen_recognition.Qwen3ASRRecognizerAdapter.ADAPTER_NAME,
        model="Qwen/Qwen3-ASR-1.7B@fixture",
        language="zh",
        config_hash=hash_object({"fixture": True}),
        raw_output=ArtifactDigest(
            uri=raw_path.resolve().as_uri(),
            sha256=raw_hash,
            size_bytes=len(raw),
        ),
        raw_output_hash=raw_hash,
        normalized_audio_hash="a" * 64,
        tokens=evidence_tokens,
    )
    corrected = tuple(
        CorrectedTimedToken(
            id=f"corrected-{index}",
            text=token.text,
            start_ms=token.start_ms,
            end_ms=token.end_ms,
            confidence=token.confidence,
            speaker=token.speaker,
            source_primary_token_ids=(token.id,),
            recognition_refs=token.evidence_refs,
        )
        for index, token in enumerate(evidence_tokens)
    )
    return raw_path, evidence, corrected


def _repair_receipt() -> QwenOwnedRangeRepair:
    left = QwenOwnedWordSnapshot(
        source_item_id="left",
        source_chunk_id="chunk-left",
        text="甲",
        start_ms=100,
        end_ms=250,
    )
    right = QwenOwnedWordSnapshot(
        source_item_id="right",
        source_chunk_id="chunk-right",
        text="甲",
        start_ms=200,
        end_ms=300,
    )
    return QwenOwnedRangeRepair(
        id="qwen-owned-range-repair-fixture",
        seam_id="seam-fixture",
        reason="drop_right_duplicate_left_suffix",
        overlap_ms=50,
        before_tokens=(left, right),
        after_tokens=(left,),
    )


def _rendered_text(result: object) -> str:
    projection = getattr(result, "projection")
    return "".join(line for cue in projection.cues for line in cue.lines)


def test_verified_qwen_punctuation_becomes_boundary_hints_not_subtitle_text(
    tmp_path: Path,
) -> None:
    _raw_path, evidence, corrected = _qwen_fixture(tmp_path)

    projection = subject.derive_qwen_sentence_hints(
        primary=evidence,
        primary_owned_range_repairs=(),
        corrected_tokens=corrected,
    )

    assert projection.status == "verified"
    assert projection.count == 2
    assert [(hint.after_token_id, hint.strength) for hint in projection.sentence_hints] == [
        ("corrected-0", 0.75),
        ("corrected-1", 1.0),
    ]
    segmented = segment_accurate_subtitles(
        corrected,
        episode_id="episode",
        generation_id="provider-punctuation",
        sentence_hints=projection.sentence_hints,
        audio_end_ms=corrected[-1].end_ms,
    )
    assert segmented.boundary_decisions[0].cue_relation == "preferred"
    assert segmented.boundary_decisions[1].cue_relation == "preferred"
    assert "sentence_boundary_hint" in segmented.boundary_decisions[0].reasons
    assert _rendered_text(segmented) == "大家好歡迎你下一句"
    assert not {"，", "。"}.intersection(segmented.srt_text)


def test_qwen_punctuation_projects_to_faster_based_tokens_on_shared_audio_clock(
    tmp_path: Path,
) -> None:
    _raw_path, evidence, _qwen_corrected = _qwen_fixture(tmp_path)
    faster_corrected = tuple(
        CorrectedTimedToken(
            id=f"faster-corrected-{index}",
            text=token.text,
            start_ms=token.start_ms + 20,
            end_ms=token.end_ms + 20,
            confidence=token.confidence,
            speaker=token.speaker,
            source_primary_token_ids=(f"faster-token-{index}",),
            recognition_refs=(),
        )
        for index, token in enumerate(evidence.tokens)
    )

    projection = subject.derive_qwen_sentence_hints(
        primary=evidence,
        primary_owned_range_repairs=(),
        corrected_tokens=faster_corrected,
    )

    assert projection.status == "verified"
    assert [(hint.after_token_id, hint.strength) for hint in projection.sentence_hints] == [
        ("faster-corrected-0", 0.75),
        ("faster-corrected-1", 1.0),
    ]


def test_cross_asr_punctuation_requires_both_lexical_anchors() -> None:
    qwen_tokens = {
        "left": EvidenceToken(id="left", text="安吉", start_ms=0, end_ms=400),
        "right": EvidenceToken(id="right", text="他", start_ms=500, end_ms=650),
    }
    signal = subject.VerifiedProviderBoundarySignal(
        left_primary_token_id="left",
        right_primary_token_id="right",
        strength=0.75,
        signal_kind="soft",
        source_separator_id="chunk:separator:1",
    )
    corrected = (
        CorrectedTimedToken("c0", "安", 0, 200, None, None, (), ()),
        CorrectedTimedToken("c1", "吉", 200, 400, None, None, (), ()),
        CorrectedTimedToken("c2", "他", 400, 480, None, None, (), ()),
        # The wrong edge after ``他`` is closer to Qwen's target time.  Pure
        # nearest-time matching selected it and yielded ``他／的``.
        CorrectedTimedToken("c3", "的", 480, 650, None, None, (), ()),
    )

    assert subject._temporal_edge_index(
        signal,
        qwen_tokens=qwen_tokens,
        corrected_tokens=corrected,
    ) == 1


def test_full_stop_prefers_the_edge_between_two_short_sentences(tmp_path: Path) -> None:
    raw_path = tmp_path / "sentences.json"
    raw, rows = _raw_envelope(
        raw_path,
        transcript="一句話。下一句",
        aligned_texts=("一句話", "下一句"),
    )
    raw_hash = sha256_bytes(raw)
    tokens = tuple(
        EvidenceToken(
            **row,
            confidence=None,
            evidence_refs=(f"raw:{raw_hash}",),
        )
        for row in rows
    )
    evidence = RecognitionEvidence(
        episode_id="episode",
        invocation_id="invocation",
        adapter=qwen_recognition.Qwen3ASRRecognizerAdapter.ADAPTER_NAME,
        model="qwen@fixture",
        language="zh",
        config_hash="d" * 64,
        raw_output=ArtifactDigest(
            uri=raw_path.resolve().as_uri(), sha256=raw_hash, size_bytes=len(raw)
        ),
        raw_output_hash=raw_hash,
        normalized_audio_hash="a" * 64,
        tokens=tokens,
    )
    corrected = tuple(
        CorrectedTimedToken(
            id=f"c-{index}",
            text=token.text,
            start_ms=token.start_ms,
            end_ms=token.end_ms,
            confidence=None,
            speaker=None,
            source_primary_token_ids=(token.id,),
            recognition_refs=token.evidence_refs,
        )
        for index, token in enumerate(tokens)
    )

    projection = subject.derive_qwen_sentence_hints(
        primary=evidence,
        primary_owned_range_repairs=(),
        corrected_tokens=corrected,
    )

    assert [(item.after_token_id, item.strength) for item in projection.sentence_hints] == [
        ("c-0", 1.0)
    ]


@pytest.mark.parametrize("failure", ("digest", "size", "canonical", "repair", "evidence"))
def test_qwen_projection_fails_closed_on_unreplayed_lineage(
    tmp_path: Path,
    failure: str,
) -> None:
    raw_path, evidence, corrected = _qwen_fixture(tmp_path)
    repairs: tuple[QwenOwnedRangeRepair, ...] = ()
    if failure == "digest":
        raw_path.write_bytes(raw_path.read_bytes() + b" ")
    elif failure == "size":
        evidence = evidence.model_copy(
            update={
                "raw_output": ArtifactDigest(
                    uri=evidence.raw_output.uri,
                    sha256=evidence.raw_output.sha256,
                    size_bytes=evidence.raw_output.size_bytes + 1,
                )
            }
        )
    elif failure == "canonical":
        pretty = json.dumps(
            json.loads(raw_path.read_text(encoding="utf-8")),
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        raw_path.write_bytes(pretty)
        pretty_hash = sha256_bytes(pretty)
        evidence = evidence.model_copy(
            update={
                "raw_output": ArtifactDigest(
                    uri=raw_path.resolve().as_uri(),
                    sha256=pretty_hash,
                    size_bytes=len(pretty),
                ),
                "raw_output_hash": pretty_hash,
                "tokens": tuple(
                    token.model_copy(update={"evidence_refs": (f"raw:{pretty_hash}",)})
                    for token in evidence.tokens
                ),
            }
        )
    elif failure == "repair":
        repairs = (_repair_receipt(),)
    elif failure == "evidence":
        evidence = evidence.model_copy(
            update={
                "tokens": (
                    evidence.tokens[0].model_copy(update={"text": "辨識漂移"}),
                    *evidence.tokens[1:],
                )
            }
        )

    with pytest.raises(subject.AccuratePunctuationIntegrityError):
        subject.derive_qwen_sentence_hints(
            primary=evidence,
            primary_owned_range_repairs=repairs,
            corrected_tokens=corrected,
        )


def test_fixture_and_non_qwen_evidence_return_empty_hints_without_reading_raw() -> None:
    raw = b"not-a-file"
    digest = sha256_bytes(raw)
    evidence = RecognitionEvidence(
        episode_id="episode",
        invocation_id="invocation",
        adapter="qwen",
        model="fixture",
        language="zh",
        config_hash="e" * 64,
        raw_output=ArtifactDigest(uri="fixture://qwen", sha256=digest, size_bytes=len(raw)),
        raw_output_hash=digest,
        normalized_audio_hash="a" * 64,
        tokens=(EvidenceToken(id="p0", text="甲", start_ms=0, end_ms=100),),
    )
    corrected = (
        CorrectedTimedToken(
            id="c0",
            text="甲",
            start_ms=0,
            end_ms=100,
            confidence=None,
            speaker=None,
            source_primary_token_ids=("p0",),
            recognition_refs=(),
        ),
    )

    projection = subject.derive_qwen_sentence_hints(
        primary=evidence,
        primary_owned_range_repairs=(),
        corrected_tokens=corrected,
    )

    assert projection.status == "not_applicable"
    assert projection.count == 0
    assert projection.sentence_hints == ()


def test_duplicate_verified_separator_signals_dedupe_deterministically() -> None:
    corrected = (
        CorrectedTimedToken(
            id="c0",
            text="甲",
            start_ms=0,
            end_ms=100,
            confidence=None,
            speaker=None,
            source_primary_token_ids=("p0",),
            recognition_refs=(),
        ),
        CorrectedTimedToken(
            id="c1",
            text="乙",
            start_ms=100,
            end_ms=200,
            confidence=None,
            speaker=None,
            source_primary_token_ids=("p1",),
            recognition_refs=(),
        ),
    )
    signals = (
        subject.VerifiedProviderBoundarySignal(
            left_primary_token_id="p0",
            right_primary_token_id="p1",
            strength=0.75,
            signal_kind="soft",
            source_separator_id="chunk-right:separator:0",
        ),
        subject.VerifiedProviderBoundarySignal(
            left_primary_token_id="p0",
            right_primary_token_id="p1",
            strength=1.0,
            signal_kind="hard",
            source_separator_id="chunk-left:separator:9",
        ),
    )

    first = subject.project_verified_provider_boundaries(
        signals,
        corrected_tokens=corrected,
        raw_output_sha256="f" * 64,
    )
    second = subject.project_verified_provider_boundaries(
        tuple(reversed(signals)),
        corrected_tokens=corrected,
        raw_output_sha256="f" * 64,
    )

    assert first == second
    assert first.count == 1
    assert first.boundary_hash == second.boundary_hash
    assert first.sentence_hints[0].strength == 1.0


def test_boundary_inside_one_merged_corrected_token_is_omitted() -> None:
    merged = (
        CorrectedTimedToken(
            id="merged",
            text="甲乙",
            start_ms=0,
            end_ms=200,
            confidence=None,
            speaker=None,
            source_primary_token_ids=("p0", "p1"),
            recognition_refs=(),
        ),
    )
    signal = subject.VerifiedProviderBoundarySignal(
        left_primary_token_id="p0",
        right_primary_token_id="p1",
        strength=1.0,
        signal_kind="hard",
        source_separator_id="chunk:separator:0",
    )

    projection = subject.project_verified_provider_boundaries(
        (signal,),
        corrected_tokens=merged,
        raw_output_sha256="f" * 64,
    )

    assert projection.count == 0
    assert projection.sentence_hints == ()


@pytest.mark.parametrize(
    ("separator", "expected"),
    (
        ("   \t", None),
        ("「」『』《》〈〉", None),
        ("\n", (0.75, "soft")),
        ("：\u3000", (0.75, "soft")),
        ("？」", (1.0, "hard")),
    ),
)
def test_only_declared_punctuation_classes_create_boundary_signals(
    separator: str,
    expected: tuple[float, str] | None,
) -> None:
    assert subject._separator_signal(separator) == expected
