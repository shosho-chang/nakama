from __future__ import annotations

import json
import wave
from dataclasses import replace
from pathlib import Path

import pytest

import agents.brook.podcast_subtitles.accurate_pipeline as subject
import agents.brook.podcast_subtitles.accurate_punctuation as punctuation
from agents.brook.podcast_subtitles.accurate_correction import (
    CorrectedTimedToken,
    CorrectionReviewSelection,
)
from agents.brook.podcast_subtitles.accurate_pipeline import (
    AccurateSubtitlePipelineResult,
)
from agents.brook.podcast_subtitles.accurate_recognition import (
    AccurateRecognitionResult,
    FasterOwnedRangeRepair,
    FasterOwnedWordSnapshot,
    FasterSeamConflict,
    FasterSegmentBoundaryRepair,
    QwenOwnedRangeRepair,
    QwenOwnedWordSnapshot,
    QwenSeamConflict,
)
from agents.brook.podcast_subtitles.accurate_segmentation import BoundaryReviewItem
from agents.brook.podcast_subtitles.hashing import (
    canonical_json_bytes,
    hash_file,
    hash_object,
    sha256_bytes,
)
from shared.schemas.podcast_subtitles_v2 import (
    ArtifactDigest,
    EvidenceToken,
    RecognitionEvidence,
)


def _write_pcm_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16_000)
        writer.writeframes(b"\x00\x00" * 16_000)


def _evidence(
    *,
    episode_id: str,
    invocation_id: str,
    audio_hash: str,
    adapter: str,
    text: str,
) -> RecognitionEvidence:
    raw = f"{adapter}:{text}".encode("utf-8")
    raw_hash = sha256_bytes(raw)
    return RecognitionEvidence(
        episode_id=episode_id,
        invocation_id=invocation_id,
        adapter=adapter,
        model=f"{adapter}-fixture@v1",
        language="zh-TW",
        config_hash=hash_object({"adapter": adapter}),
        raw_output=ArtifactDigest(
            uri=f"fixture://{adapter}",
            sha256=raw_hash,
            size_bytes=len(raw),
        ),
        raw_output_hash=raw_hash,
        normalized_audio_hash=audio_hash,
        tokens=(
            EvidenceToken(
                id=f"{adapter}-token-0",
                text=text,
                start_ms=100,
                end_ms=900,
                confidence=0.95,
            ),
        ),
    )


def _seam_conflict() -> QwenSeamConflict:
    return QwenSeamConflict(
        id="qwen-seam-001",
        seam_sample=16_000,
        seam_ms=1_000,
        comparison_start_sample=15_000,
        comparison_end_sample=17_000,
        comparison_start_ms=938,
        comparison_end_ms=1_062,
        left_chunk_id="chunk-left",
        right_chunk_id="chunk-right",
        left_text="今天下雨",
        right_text="今天放晴",
        left_normalized="今天下雨",
        right_normalized="今天放晴",
    )


def _owned_range_repair() -> QwenOwnedRangeRepair:
    left = QwenOwnedWordSnapshot(
        source_item_id="left-item",
        source_chunk_id="chunk-left",
        text="製造",
        start_ms=900,
        end_ms=1_020,
    )
    right = QwenOwnedWordSnapshot(
        source_item_id="right-item",
        source_chunk_id="chunk-right",
        text="製造",
        start_ms=1_000,
        end_ms=1_100,
    )
    return QwenOwnedRangeRepair(
        id="qwen-owned-repair-001",
        seam_id="qwen-seam-001",
        reason="drop_right_duplicate_left_suffix",
        overlap_ms=20,
        before_tokens=(left, right),
        after_tokens=(left,),
    )


def _segment_boundary_repair() -> FasterSegmentBoundaryRepair:
    return FasterSegmentBoundaryRepair(
        id="faster-segment-boundary-repair-001",
        chunk_id="faster-chunk-000001",
        chunk_index=1,
        segment_id=7,
        word_id="faster-word-000042",
        word_index=0,
        word_text="ä¹™",
        provider_observation_sha256="e" * 64,
        before_segment_start_seconds=0.2,
        after_segment_start_seconds=0.1,
        delta_ms=100,
    )


def _faster_seam_conflict() -> FasterSeamConflict:
    return FasterSeamConflict(
        id="faster-seam-conflict-001",
        seam_sample=64_000,
        seam_ms=4_000,
        comparison_start_sample=48_000,
        comparison_end_sample=80_000,
        comparison_start_ms=3_000,
        comparison_end_ms=5_000,
        left_chunk_id="faster-chunk-left",
        right_chunk_id="faster-chunk-right",
        left_text="甲",
        right_text="乙",
        left_normalized="甲",
        right_normalized="乙",
    )


def _faster_owned_range_repair() -> FasterOwnedRangeRepair:
    left = FasterOwnedWordSnapshot(
        source_chunk_id="faster-chunk-left",
        source_chunk_index=0,
        source_word_index=7,
        text="甲",
        start_sample=63_040,
        end_sample=64_320,
        start_ms=3_940,
        end_ms=4_020,
    )
    right = FasterOwnedWordSnapshot(
        source_chunk_id="faster-chunk-right",
        source_chunk_index=1,
        source_word_index=2,
        text="乙",
        start_sample=63_680,
        end_sample=64_960,
        start_ms=3_980,
        end_ms=4_060,
    )
    clamped = replace(left, end_sample=63_680, end_ms=3_980)
    return FasterOwnedRangeRepair(
        id="faster-owned-range-repair-001",
        seam_id="faster-whisper-seam-64000",
        reason="clamp_left_end_cross_chunk_timestamp_spill",
        overlap_samples=640,
        overlap_ms=40,
        before_tokens=(left, right),
        after_tokens=(clamped, right),
    )


def _fake_recognition_runner(
    *,
    with_seam: bool = True,
    with_repair: bool = False,
    with_segment_boundary_repair: bool = False,
    with_faster_seam_conflict: bool = False,
    with_faster_owned_range_repair: bool = False,
    same_text: bool = False,
    primary_text: str = "今天下雨",
    corroborating_text: str = "今天放晴",
):
    def run(
        *,
        audio: Path,
        output_dir: Path,
        episode_id: str,
        invocation_id: str | None = None,
    ) -> AccurateRecognitionResult:
        audio_hash = hash_file(audio)
        effective_invocation = invocation_id or "fixture-stable-invocation"
        primary = _evidence(
            episode_id=episode_id,
            invocation_id=effective_invocation,
            audio_hash=audio_hash,
            adapter="qwen",
            text=primary_text,
        )
        corroborating = _evidence(
            episode_id=episode_id,
            invocation_id=effective_invocation,
            audio_hash=audio_hash,
            adapter="faster",
            text=primary_text if same_text else corroborating_text,
        )
        evidence_dir = output_dir / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        primary_payload = canonical_json_bytes(primary)
        corroborating_payload = canonical_json_bytes(corroborating)
        primary_path = evidence_dir / f"primary-{sha256_bytes(primary_payload)}.json"
        corroborating_path = (
            evidence_dir / f"corroborating-{sha256_bytes(corroborating_payload)}.json"
        )
        primary_path.write_bytes(primary_payload)
        corroborating_path.write_bytes(corroborating_payload)
        return AccurateRecognitionResult(
            episode_id=episode_id,
            invocation_id=effective_invocation,
            audio_sha256=audio_hash,
            primary_evidence=primary,
            corroborating_evidence=corroborating,
            primary_evidence_path=primary_path,
            corroborating_evidence_path=corroborating_path,
            primary_seam_conflicts=(_seam_conflict(),) if with_seam else (),
            primary_owned_range_repairs=(_owned_range_repair(),) if with_repair else (),
            corroborating_segment_boundary_repairs=(
                (_segment_boundary_repair(),) if with_segment_boundary_repair else ()
            ),
            corroborating_seam_conflicts=(
                (_faster_seam_conflict(),) if with_faster_seam_conflict else ()
            ),
            corroborating_owned_range_repairs=(
                (_faster_owned_range_repair(),)
                if with_faster_owned_range_repair
                else ()
            ),
        )

    return run


def test_pipeline_publishes_exact_srt_review_and_hashed_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio)
    book = tmp_path / "book.txt"
    book.write_text("這是作者書本的背景資料。", encoding="utf-8")
    outline = tmp_path / "outline.txt"
    outline.write_text("安吉訪綱的訪談問題。", encoding="utf-8")
    monkeypatch.setattr(subject, "run_accurate_recognition", _fake_recognition_runner())

    original_segment = subject.segment_accurate_subtitles

    def segment_with_review(*args: object, **kwargs: object):
        segmented = original_segment(*args, **kwargs)
        first = segmented.projection.token_ids[0]
        last = segmented.projection.token_ids[-1]
        return replace(
            segmented,
            boundary_reviews=(
                BoundaryReviewItem(
                    code="forced_low_confidence_boundary",
                    channel="cue",
                    edge_index=1,
                    left_token_id=first,
                    right_token_id=last,
                    pause_ms=0,
                    relation="neutral",
                    context="今天下雨",
                ),
            ),
        )

    monkeypatch.setattr(subject, "segment_accurate_subtitles", segment_with_review)
    output = tmp_path / "output"
    result = subject.run_accurate_subtitle_pipeline(
        audio=audio,
        output_dir=output,
        episode_id="episode-anji",
        book_paths=(book,),
        outline_paths=(outline,),
        glossary_terms=("不正常人類研究所", "修修", "修修"),
    )

    assert result.status == "completed_with_review"
    assert result.srt_path.read_text(encoding="utf-8").endswith("今天放晴\n")
    correction = json.loads(result.correction_path.read_text(encoding="utf-8"))
    rendered_correction = "".join(token["text"] for token in correction["tokens"])
    rendered_srt = "".join(
        line
        for line in result.srt_path.read_text(encoding="utf-8").splitlines()
        if line and not line.isdigit() and " --> " not in line
    )
    assert rendered_srt == rendered_correction == "今天放晴"

    review = json.loads(result.review_path.read_text(encoding="utf-8"))
    assert review["counts"] == {
        "boundary_reviews": 1,
        "faster_owned_range_repairs": 0,
        "faster_seam_conflicts": 0,
        "faster_segment_boundary_repairs": 0,
        "qwen_owned_range_repairs": 0,
        "qwen_seam_conflicts": 1,
        "unresolved_corrections": 1,
    }
    assert review["qwen_seam_conflicts"][0]["id"] == "qwen-seam-001"
    assert review["unresolved_corrections"][0]["category"] == "recognition_disagreement"
    assert review["correction_review_packets"][0]["current"] == "放晴"
    assert review["correction_review_packets"][0]["candidates"] == ["下雨"]
    assert review["correction_review_packets"][0]["window_current"] == "今天放晴"
    assert review["boundary_reviews"][0]["context"] == "今天下雨"

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["input"]["normalized_audio_sha256"] == hash_file(audio)
    assert manifest["recognition_roles"]["execution_order"] == [
        "qwen",
        "faster_whisper",
    ]
    assert manifest["recognition_roles"]["correction_base"]["adapter"] == "faster"
    assert manifest["recognition_roles"]["corroborator"]["adapter"] == "qwen"
    assert manifest["recognition_roles"]["punctuation_source"]["adapter"] == "qwen"
    assert [item["kind"] for item in manifest["references"]] == [
        "book",
        "outline",
        "glossary",
    ]
    assert manifest["glossary_terms"] == ["不正常人類研究所", "修修"]
    for artifact in manifest["artifacts"].values():
        artifact_path = Path(artifact["uri"].removeprefix("file:///"))
        if not artifact_path.is_file():
            artifact_path = Path("/" + artifact["uri"].removeprefix("file://"))
        assert artifact["sha256"] == hash_file(artifact_path)
        assert artifact["size_bytes"] == artifact_path.stat().st_size
    assert all(
        Path(item["snapshot_uri"].removeprefix("file:///")).is_file()
        for item in manifest["references"]
    )


@pytest.mark.parametrize(
    ("choice", "candidate_index", "expected_text", "unresolved_count", "category"),
    [
        ("current", None, "今天放晴", 0, "bounded_review_primary_selection"),
        ("candidate", 0, "今天下雨", 0, "bounded_review_candidate_selection"),
        ("defer", None, "今天放晴", 1, None),
    ],
)
def test_closed_vocabulary_review_selection_is_applied_before_final_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    choice: str,
    candidate_index: int | None,
    expected_text: str,
    unresolved_count: int,
    category: str | None,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio)
    monkeypatch.setattr(
        subject,
        "run_accurate_recognition",
        _fake_recognition_runner(with_seam=False),
    )
    baseline = subject.run_accurate_subtitle_pipeline(
        audio=audio,
        output_dir=tmp_path / "baseline",
        episode_id="episode-reviewed",
    )
    baseline_review = json.loads(baseline.review_path.read_text(encoding="utf-8"))
    decision_id = baseline_review["correction_review_packets"][0]["decision_id"]

    reviewed = subject.run_accurate_subtitle_pipeline(
        audio=audio,
        output_dir=tmp_path / choice,
        episode_id="episode-reviewed",
        review_selections=(
            CorrectionReviewSelection(
                decision_id=decision_id,
                choice=choice,  # type: ignore[arg-type]
                candidate_index=candidate_index,
            ),
        ),
    )

    correction = json.loads(reviewed.correction_path.read_text(encoding="utf-8"))
    review = json.loads(reviewed.review_path.read_text(encoding="utf-8"))
    manifest = json.loads(reviewed.manifest_path.read_text(encoding="utf-8"))
    rendered_correction = "".join(token["text"] for token in correction["tokens"])
    rendered_srt = "".join(
        line
        for line in reviewed.srt_path.read_text(encoding="utf-8").splitlines()
        if line and not line.isdigit() and " --> " not in line
    )
    receipt = correction["review_selection_receipt"]

    assert rendered_srt == rendered_correction == expected_text
    assert reviewed.unresolved_correction_count == unresolved_count
    assert review["review_selection_receipt"] == receipt
    assert manifest["review_selection_receipt"] == receipt
    assert receipt["selection_count"] == 1
    assert len(receipt["selection_sha256"]) == 64
    assert manifest["config"]["review_selection_identity"] == receipt["identity"]
    assert manifest["generation_id"] != json.loads(
        baseline.manifest_path.read_text(encoding="utf-8")
    )["generation_id"]
    if category is None:
        assert review["applied_review_decisions"] == []
        assert review["unresolved_corrections"][0]["id"] == decision_id
    else:
        assert review["applied_review_decisions"][0]["id"] == decision_id
        assert review["applied_review_decisions"][0]["category"] == category
        assert correction["applied"][-1]["category"] == category
        assert review["unresolved_corrections"] == []


def test_review_selection_rejects_unknown_decision_and_unknown_choice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio)
    monkeypatch.setattr(
        subject,
        "run_accurate_recognition",
        _fake_recognition_runner(with_seam=False),
    )

    with pytest.raises(ValueError, match="unknown decision"):
        subject.run_accurate_subtitle_pipeline(
            audio=audio,
            output_dir=tmp_path / "unknown-decision",
            episode_id="episode-review-rejection",
            review_selections=(
                CorrectionReviewSelection(
                    decision_id="not-a-stored-decision",
                    choice="current",
                ),
            ),
        )
    with pytest.raises(ValueError, match="unsupported choice"):
        subject.run_accurate_subtitle_pipeline(
            audio=audio,
            output_dir=tmp_path / "unknown-choice",
            episode_id="episode-review-rejection",
            review_selections=(
                CorrectionReviewSelection(
                    decision_id="decision-00000001",
                    choice="invented",  # type: ignore[arg-type]
                ),
            ),
        )

    assert not (tmp_path / "unknown-decision" / "final.srt").exists()
    assert not (tmp_path / "unknown-choice" / "final.srt").exists()


def test_review_selection_cannot_insert_coverage_candidate_without_audio_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio)
    monkeypatch.setattr(
        subject,
        "run_accurate_recognition",
            _fake_recognition_runner(
                with_seam=False,
                primary_text="我不知道",
                corroborating_text="我知道",
            ),
    )
    baseline = subject.run_accurate_subtitle_pipeline(
        audio=audio,
        output_dir=tmp_path / "baseline-coverage",
        episode_id="episode-coverage",
    )
    review = json.loads(baseline.review_path.read_text(encoding="utf-8"))
    packet = review["correction_review_packets"][0]
    assert review["unresolved_corrections"][0]["category"] == "recognition_coverage_gap"

    with pytest.raises(ValueError, match="audio confirmation"):
        subject.run_accurate_subtitle_pipeline(
            audio=audio,
            output_dir=tmp_path / "rejected-coverage",
            episode_id="episode-coverage",
            review_selections=(
                CorrectionReviewSelection(
                    decision_id=packet["decision_id"],
                    choice="candidate",
                    candidate_index=0,
                ),
            ),
        )
    assert not (tmp_path / "rejected-coverage" / "final.srt").exists()


def test_reference_kinds_and_glossary_are_forwarded_to_correction_and_segmentation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio)
    books = (tmp_path / "book-a.txt", tmp_path / "book-b.txt")
    for index, path in enumerate(books):
        path.write_text(f"作者書籍資料{index}", encoding="utf-8")
    outline = tmp_path / "outline.txt"
    outline.write_text("訪綱資料", encoding="utf-8")
    monkeypatch.setattr(
        subject,
        "run_accurate_recognition",
        _fake_recognition_runner(with_seam=False),
    )
    captured: dict[str, object] = {}
    original_correct = subject.correct_recognition
    original_punctuation = subject.derive_qwen_sentence_hints
    original_segment = subject.segment_accurate_subtitles

    def capture_correction(**kwargs: object):
        captured["references"] = kwargs["references"]
        captured["correction_primary"] = kwargs["primary"]
        captured["correction_corroborating"] = kwargs["corroborating"]
        return original_correct(**kwargs)

    def capture_punctuation(**kwargs: object):
        captured["punctuation_primary"] = kwargs["primary"]
        return original_punctuation(**kwargs)

    def capture_segmentation(*args: object, **kwargs: object):
        captured["protected_terms"] = kwargs["protected_terms"]
        return original_segment(*args, **kwargs)

    monkeypatch.setattr(subject, "correct_recognition", capture_correction)
    monkeypatch.setattr(subject, "derive_qwen_sentence_hints", capture_punctuation)
    monkeypatch.setattr(subject, "segment_accurate_subtitles", capture_segmentation)

    subject.run_accurate_subtitle_pipeline(
        audio=audio,
        output_dir=tmp_path / "output",
        episode_id="episode-anji",
        book_paths=books,
        outline_paths=(outline,),
        glossary_terms=("臺灣製造", "安吉"),
    )

    references = captured["references"]
    assert [reference.kind for reference in references] == [
        "book",
        "book",
        "outline",
        "glossary",
    ]
    assert [reference.source_id for reference in references] == list(
        dict.fromkeys(reference.source_id for reference in references)
    )
    assert captured["correction_primary"].adapter == "faster"
    assert captured["correction_corroborating"].adapter == "qwen"
    assert captured["punctuation_primary"].adapter == "qwen"
    assert captured["protected_terms"] == ("臺灣製造", "安吉")


def test_provider_boundary_set_is_passed_to_segmentation_and_generation_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio)
    monkeypatch.setattr(
        subject,
        "run_accurate_recognition",
        _fake_recognition_runner(with_seam=False, same_text=True),
    )
    projection_tokens = (
        CorrectedTimedToken(
            id="projection-left",
            text="甲",
            start_ms=0,
            end_ms=100,
            confidence=None,
            speaker=None,
            source_primary_token_ids=("primary-left",),
            recognition_refs=(),
        ),
        CorrectedTimedToken(
            id="projection-right",
            text="乙",
            start_ms=100,
            end_ms=200,
            confidence=None,
            speaker=None,
            source_primary_token_ids=("primary-right",),
            recognition_refs=(),
        ),
    )
    signal = punctuation.VerifiedProviderBoundarySignal(
        left_primary_token_id="primary-left",
        right_primary_token_id="primary-right",
        strength=1.0,
        signal_kind="hard",
        source_separator_id="chunk:separator:0",
    )
    with_boundary = punctuation.project_verified_provider_boundaries(
        (signal,),
        corrected_tokens=projection_tokens,
        raw_output_sha256="f" * 64,
    )
    without_boundary = punctuation.project_verified_provider_boundaries(
        (),
        corrected_tokens=projection_tokens,
        raw_output_sha256="f" * 64,
    )
    captured: dict[str, object] = {}
    original_segment = subject.segment_accurate_subtitles

    def capture_segmentation(*args: object, **kwargs: object):
        captured["sentence_hints"] = kwargs.get("sentence_hints")
        # The explicit injected seam uses fixture-only corrected IDs.  The
        # production derivation is separately tested against corrected lineage.
        return original_segment(*args, **{**kwargs, "sentence_hints": ()})

    monkeypatch.setattr(subject, "segment_accurate_subtitles", capture_segmentation)
    monkeypatch.setattr(
        subject,
        "derive_qwen_sentence_hints",
        lambda **_kwargs: with_boundary,
        raising=False,
    )
    first = subject.run_accurate_subtitle_pipeline(
        audio=audio,
        output_dir=tmp_path / "with-boundary",
        episode_id="episode-provider-boundary",
    )
    first_manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    first_recognition = json.loads(first.recognition_path.read_text(encoding="utf-8"))

    monkeypatch.setattr(
        subject,
        "derive_qwen_sentence_hints",
        lambda **_kwargs: without_boundary,
        raising=False,
    )
    second = subject.run_accurate_subtitle_pipeline(
        audio=audio,
        output_dir=tmp_path / "without-boundary",
        episode_id="episode-provider-boundary",
    )
    second_manifest = json.loads(second.manifest_path.read_text(encoding="utf-8"))

    assert captured["sentence_hints"] == without_boundary.sentence_hints
    receipt = first_recognition["qwen_provider_sentence_boundaries"]
    assert receipt["count"] == 1
    assert receipt["boundary_hash"] == with_boundary.boundary_hash
    assert first_manifest["config"]["provider_sentence_boundaries"] == (
        with_boundary.identity
    )
    assert first_manifest["generation_id"] != second_manifest["generation_id"]


def test_owned_range_repair_is_preserved_as_non_blocking_review_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio)
    monkeypatch.setattr(
        subject,
        "run_accurate_recognition",
        _fake_recognition_runner(
            with_seam=False,
            with_repair=True,
            same_text=True,
        ),
    )

    result = subject.run_accurate_subtitle_pipeline(
        audio=audio,
        output_dir=tmp_path / "output",
        episode_id="episode-owned-repair",
    )

    assert result.status == "completed_with_review"
    assert result.owned_range_repair_count == 1
    assert result.seam_conflict_count == 0
    assert result.unresolved_correction_count == 0
    recognition = json.loads(result.recognition_path.read_text(encoding="utf-8"))
    assert recognition["qwen_owned_range_repairs"][0]["id"] == ("qwen-owned-repair-001")
    review = json.loads(result.review_path.read_text(encoding="utf-8"))
    assert review["status"] == "review_recommended"
    assert review["counts"]["qwen_owned_range_repairs"] == 1
    assert review["qwen_owned_range_repairs"][0]["reason"] == (
        "drop_right_duplicate_left_suffix"
    )
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "completed_with_review"
    assert manifest["review_counts"]["qwen_owned_range_repairs"] == 1


def test_faster_segment_boundary_repair_is_public_review_lineage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio)
    monkeypatch.setattr(
        subject,
        "run_accurate_recognition",
        _fake_recognition_runner(
            with_seam=False,
            same_text=True,
            with_segment_boundary_repair=True,
        ),
    )

    result = subject.run_accurate_subtitle_pipeline(
        audio=audio,
        output_dir=tmp_path / "output",
        episode_id="episode-faster-segment-boundary-repair",
    )

    assert result.status == "completed_with_review"
    assert result.corroborating_segment_boundary_repair_count == 1
    recognition = json.loads(result.recognition_path.read_text(encoding="utf-8"))
    assert recognition["faster_segment_boundary_repairs"] == {
        "type": "faster_segment_boundary_repair",
        "status": "review_recommended",
        "count": 1,
        "items": [
            {
                "id": "faster-segment-boundary-repair-001",
                "chunk_id": "faster-chunk-000001",
                "chunk_index": 1,
                "segment_id": 7,
                "word_id": "faster-word-000042",
                "word_index": 0,
                "word_text": "ä¹™",
                "provider_observation_sha256": "e" * 64,
                "before_segment_start_seconds": 0.2,
                "after_segment_start_seconds": 0.1,
                "delta_ms": 100,
            }
        ],
    }
    review = json.loads(result.review_path.read_text(encoding="utf-8"))
    assert review["status"] == "review_recommended"
    assert review["counts"]["faster_segment_boundary_repairs"] == 1
    assert (
        review["faster_segment_boundary_repairs"]
        == recognition["faster_segment_boundary_repairs"]
    )
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "completed_with_review"
    assert manifest["review_counts"]["faster_segment_boundary_repairs"] == 1


def test_faster_secondary_conflicts_and_range_repairs_are_public_lineage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio)
    monkeypatch.setattr(
        subject,
        "run_accurate_recognition",
        _fake_recognition_runner(
            with_seam=False,
            same_text=True,
            with_faster_seam_conflict=True,
            with_faster_owned_range_repair=True,
        ),
    )

    result = subject.run_accurate_subtitle_pipeline(
        audio=audio,
        output_dir=tmp_path / "output",
        episode_id="episode-faster-secondary-lineage",
    )

    assert result.status == "completed_with_review"
    assert result.corroborating_seam_conflict_count == 1
    assert result.corroborating_owned_range_repair_count == 1
    recognition = json.loads(result.recognition_path.read_text(encoding="utf-8"))
    review = json.loads(result.review_path.read_text(encoding="utf-8"))
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert recognition["faster_seam_conflicts"][0]["id"] == (
        "faster-seam-conflict-001"
    )
    assert recognition["faster_owned_range_repairs"][0]["id"] == (
        "faster-owned-range-repair-001"
    )
    assert review["status"] == "review_recommended"
    assert review["counts"]["faster_seam_conflicts"] == 1
    assert review["counts"]["faster_owned_range_repairs"] == 1
    assert (
        review["faster_seam_conflicts"]
        == recognition["faster_seam_conflicts"]
    )
    assert (
        review["faster_owned_range_repairs"]
        == recognition["faster_owned_range_repairs"]
    )
    assert manifest["status"] == "completed_with_review"
    assert manifest["review_counts"]["faster_seam_conflicts"] == 1
    assert manifest["review_counts"]["faster_owned_range_repairs"] == 1


def test_zero_faster_segment_boundary_repairs_remain_clear_and_deterministic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio)
    monkeypatch.setattr(
        subject,
        "run_accurate_recognition",
        _fake_recognition_runner(with_seam=False, same_text=True),
    )
    output = tmp_path / "output"

    first = subject.run_accurate_subtitle_pipeline(
        audio=audio,
        output_dir=output,
        episode_id="episode-no-segment-boundary-repairs",
    )
    before = {
        name: (output / name).read_bytes()
        for name in ("recognition.json", "review.json", "manifest.json")
    }
    second = subject.run_accurate_subtitle_pipeline(
        audio=audio,
        output_dir=output,
        episode_id="episode-no-segment-boundary-repairs",
    )

    assert first == second
    assert first.status == "completed"
    assert first.corroborating_segment_boundary_repair_count == 0
    assert first.corroborating_seam_conflict_count == 0
    assert first.corroborating_owned_range_repair_count == 0
    assert before == {name: (output / name).read_bytes() for name in before}
    expected = {
        "type": "faster_segment_boundary_repair",
        "status": "clear",
        "count": 0,
        "items": [],
    }
    recognition = json.loads(first.recognition_path.read_text(encoding="utf-8"))
    review = json.loads(first.review_path.read_text(encoding="utf-8"))
    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    assert recognition["faster_segment_boundary_repairs"] == expected
    assert review["status"] == "clear"
    assert review["faster_segment_boundary_repairs"] == expected
    assert review["counts"]["faster_segment_boundary_repairs"] == 0
    assert recognition["faster_seam_conflicts"] == []
    assert recognition["faster_owned_range_repairs"] == []
    assert review["faster_seam_conflicts"] == []
    assert review["faster_owned_range_repairs"] == []
    assert review["counts"]["faster_seam_conflicts"] == 0
    assert review["counts"]["faster_owned_range_repairs"] == 0
    assert manifest["status"] == "completed"
    assert manifest["review_counts"]["faster_segment_boundary_repairs"] == 0
    assert manifest["review_counts"]["faster_seam_conflicts"] == 0
    assert manifest["review_counts"]["faster_owned_range_repairs"] == 0


def test_same_inputs_replay_to_identical_named_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio)
    monkeypatch.setattr(
        subject,
        "run_accurate_recognition",
        _fake_recognition_runner(with_seam=False),
    )
    output = tmp_path / "output"

    first = subject.run_accurate_subtitle_pipeline(
        audio=audio,
        output_dir=output,
        episode_id="episode-replay",
    )
    before = {
        path.name: path.read_bytes()
        for path in (
            first.recognition_path,
            first.correction_path,
            first.review_path,
            first.srt_path,
            first.manifest_path,
        )
    }
    second = subject.run_accurate_subtitle_pipeline(
        audio=audio,
        output_dir=output,
        episode_id="episode-replay",
    )

    assert first == second
    assert before == {name: (output / name).read_bytes() for name in before}
    for name in ("correction.json", "review.json", "manifest.json"):
        payload = json.loads((output / name).read_text(encoding="utf-8"))
        receipt = payload["review_selection_receipt"]
        assert receipt["selection_count"] == 0
        assert receipt["selections"] == []
        assert len(receipt["identity"]) > 64


def test_injected_recognition_skips_models_and_matches_wrapper_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio)
    book = tmp_path / "book.txt"
    book.write_text("作者談臺灣製造。", encoding="utf-8")
    fixture_runner = _fake_recognition_runner(
        with_seam=True,
        with_segment_boundary_repair=True,
    )
    direct_root = tmp_path / "direct"
    recognition = fixture_runner(
        audio=audio.resolve(),
        output_dir=direct_root / ".subtitle-v2",
        episode_id="episode-injected",
        invocation_id="immutable-completed-recognition",
    )

    def forbidden(**_kwargs: object):
        raise AssertionError("injected recognition must trigger zero recognizer calls")

    monkeypatch.setattr(subject, "run_accurate_recognition", forbidden)
    direct = subject.publish_accurate_subtitle_artifacts(
        recognition=recognition,
        audio=audio,
        output_dir=direct_root,
        episode_id="episode-injected",
        book_paths=(book,),
        glossary_terms=("臺灣製造",),
    )

    assert direct.corroborating_segment_boundary_repair_count == 1

    wrapper_root = tmp_path / "wrapper"
    wrapper_recognition = replace(
        recognition,
        primary_evidence_path=(
            wrapper_root / ".subtitle-v2" / "evidence" / recognition.primary_evidence_path.name
        ),
        corroborating_evidence_path=(
            wrapper_root
            / ".subtitle-v2"
            / "evidence"
            / recognition.corroborating_evidence_path.name
        ),
    )

    def one_recognition_call(**_kwargs: object) -> AccurateRecognitionResult:
        wrapper_recognition.primary_evidence_path.parent.mkdir(parents=True, exist_ok=True)
        wrapper_recognition.primary_evidence_path.write_bytes(
            canonical_json_bytes(wrapper_recognition.primary_evidence)
        )
        wrapper_recognition.corroborating_evidence_path.write_bytes(
            canonical_json_bytes(wrapper_recognition.corroborating_evidence)
        )
        return wrapper_recognition

    monkeypatch.setattr(subject, "run_accurate_recognition", one_recognition_call)
    wrapper = subject.run_accurate_subtitle_pipeline(
        audio=audio,
        output_dir=wrapper_root,
        episode_id="episode-injected",
        book_paths=(book,),
        glossary_terms=("臺灣製造",),
        invocation_id="immutable-completed-recognition",
    )

    # URI fields intentionally reflect each output root.  Every semantic and
    # text-bearing artifact remains otherwise byte-identical between paths.
    for name in ("recognition.json", "correction.json", "review.json", "final.srt"):
        assert (direct_root / name).read_bytes() == (wrapper_root / name).read_bytes()
    direct_manifest = json.loads(direct.manifest_path.read_text(encoding="utf-8"))
    wrapper_manifest = json.loads(wrapper.manifest_path.read_text(encoding="utf-8"))
    for manifest in (direct_manifest, wrapper_manifest):
        for artifact in manifest["artifacts"].values():
            artifact.pop("uri")
        for reference in manifest["references"]:
            reference.pop("snapshot_uri")
    assert direct_manifest == wrapper_manifest


@pytest.mark.parametrize("defect", ["episode", "audio", "evidence_path"])
def test_injected_recognition_is_authenticated_before_publication(
    tmp_path: Path,
    defect: str,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio)
    fixture = _fake_recognition_runner(with_seam=False)(
        audio=audio.resolve(),
        output_dir=tmp_path / "recognition",
        episode_id="episode-authenticated",
        invocation_id="recognition-authenticated",
    )
    if defect == "episode":
        expected = "different episode"
        fixture = replace(fixture, episode_id="wrong-episode")
    elif defect == "audio":
        expected = "measured normalized WAV"
        fixture = replace(fixture, audio_sha256="0" * 64)
    else:
        expected = "does not contain its typed evidence"
        fixture.primary_evidence_path.write_bytes(b"{}")

    with pytest.raises((ValueError, FileNotFoundError), match=expected):
        subject.publish_accurate_subtitle_artifacts(
            recognition=fixture,
            audio=audio,
            output_dir=tmp_path / "output",
            episode_id="episode-authenticated",
        )
    assert not (tmp_path / "output" / "final.srt").exists()


def test_explicit_invocation_is_forwarded_unchanged_to_checkpoint_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio)
    captured: dict[str, object] = {}
    fixture_runner = _fake_recognition_runner(with_seam=False)

    def capture_recognition(**kwargs: object) -> AccurateRecognitionResult:
        captured.update(kwargs)
        return fixture_runner(**kwargs)

    monkeypatch.setattr(subject, "run_accurate_recognition", capture_recognition)
    output = tmp_path / "output"
    result = subject.run_accurate_subtitle_pipeline(
        audio=audio,
        output_dir=output,
        episode_id="episode-checkpoint-replay",
        invocation_id="anji-accurate-subtitles-v2-bc652157",
    )

    assert captured["invocation_id"] == "anji-accurate-subtitles-v2-bc652157"
    assert captured["output_dir"] == output.resolve() / ".subtitle-v2"
    assert result.manifest_path.is_file()


@pytest.mark.parametrize("missing", ["audio", "book", "outline"])
def test_missing_input_fails_before_recognition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    audio = tmp_path / "normalized.wav"
    book = tmp_path / "book.txt"
    outline = tmp_path / "outline.txt"
    _write_pcm_wav(audio)
    book.write_text("書", encoding="utf-8")
    outline.write_text("訪綱", encoding="utf-8")
    if missing == "audio":
        audio = tmp_path / "missing.wav"
    elif missing == "book":
        book = tmp_path / "missing-book.txt"
    else:
        outline = tmp_path / "missing-outline.txt"
    called = False

    def forbidden(**_kwargs: object):
        nonlocal called
        called = True
        raise AssertionError("recognition must not start with missing inputs")

    monkeypatch.setattr(subject, "run_accurate_recognition", forbidden)
    with pytest.raises(FileNotFoundError):
        subject.run_accurate_subtitle_pipeline(
            audio=audio,
            output_dir=tmp_path / "output",
            episode_id="episode-missing",
            book_paths=(book,),
            outline_paths=(outline,),
        )
    assert called is False


def test_cli_forwards_repeatable_inputs_and_returns_machine_readable_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    import scripts.podcast_subtitle_accurate as cli

    captured: dict[str, object] = {}

    def successful(**kwargs: object) -> AccurateSubtitlePipelineResult:
        captured.update(kwargs)
        output = Path(kwargs["output_dir"])
        return AccurateSubtitlePipelineResult(
            status="completed",
            episode_id=str(kwargs["episode_id"]),
            normalized_audio_hash="a" * 64,
            output_dir=output,
            recognition_path=output / "recognition.json",
            correction_path=output / "correction.json",
            review_path=output / "review.json",
            srt_path=output / "final.srt",
            manifest_path=output / "manifest.json",
            unresolved_correction_count=0,
            seam_conflict_count=0,
            owned_range_repair_count=0,
            corroborating_segment_boundary_repair_count=1,
            corroborating_seam_conflict_count=2,
            corroborating_owned_range_repair_count=3,
            boundary_review_count=0,
        )

    monkeypatch.setattr(cli, "run_accurate_subtitle_pipeline", successful)
    exit_code = cli.main(
        [
            "--audio",
            str(tmp_path / "normalized.wav"),
            "--output-dir",
            str(tmp_path / "output"),
            "--episode-id",
            "episode-cli",
            "--book",
            str(tmp_path / "book-a.txt"),
            "--book",
            str(tmp_path / "book-b.txt"),
            "--outline",
            str(tmp_path / "outline.txt"),
            "--glossary",
            "臺灣製造",
            "--glossary",
            "安吉",
            "--invocation-id",
            "anji-accurate-subtitles-v2-bc652157",
        ]
    )
    stdout, stderr = capsysbinary.readouterr()

    assert exit_code == 0
    assert stderr == b""
    summary = json.loads(stdout)
    assert summary["status"] == "completed"
    assert summary["review_counts"]["corroborating_segment_boundary_repairs"] == 1
    assert summary["review_counts"]["corroborating_seam_conflicts"] == 2
    assert summary["review_counts"]["corroborating_owned_range_repairs"] == 3
    assert len(captured["book_paths"]) == 2
    assert len(captured["outline_paths"]) == 1
    assert captured["glossary_terms"] == ["臺灣製造", "安吉"]
    assert captured["invocation_id"] == "anji-accurate-subtitles-v2-bc652157"

    def failed(**_kwargs: object):
        raise FileNotFoundError("normalized WAV does not exist")

    monkeypatch.setattr(cli, "run_accurate_subtitle_pipeline", failed)
    assert (
        cli.main(
            [
                "--audio",
                str(tmp_path / "missing.wav"),
                "--output-dir",
                str(tmp_path / "output"),
                "--episode-id",
                "episode-cli",
            ]
        )
        == 2
    )
    stdout, stderr = capsysbinary.readouterr()
    assert stdout == b""
    assert json.loads(stderr)["error_type"] == "FileNotFoundError"
