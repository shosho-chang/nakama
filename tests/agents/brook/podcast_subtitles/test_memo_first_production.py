from __future__ import annotations

import wave
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.adapters import MemoRecognizerAdapter
from agents.brook.podcast_subtitles.adapters.normalized_handoff import (
    NormalizedAudioHandoffManifestV1,
    VerifiedNormalizedAudioHandoffAdapter,
)
from agents.brook.podcast_subtitles.hashing import canonical_json_bytes, hash_file
from agents.brook.podcast_subtitles.memo_boundary import (
    MemoAcceptedCueV1,
    MemoBoundaryAuthorityV1,
    MemoBoundaryRepairProofV1,
    MemoBoundaryRepairV1,
    MemoCueBoundaryManifestV1,
    MemoSourceCueV1,
    MemoSrtAcceptanceReceiptV1,
)
from agents.brook.podcast_subtitles.ports import (
    AdapterIntegrityError,
    NormalizeRequest,
    RecognitionRequest,
)


def _write(path: Path, value: object) -> Path:
    path.write_bytes(canonical_json_bytes(value))
    return path


def _memo_fixture(tmp_path: Path, *, final_text: str = "謝謝"):
    audio = tmp_path / "normalized.wav"
    audio.write_bytes(b"minimized-zheng-normalized-audio")
    source_export = tmp_path / "memo.stdout"
    source_export.write_bytes(b"accepted Memo attempt 02 source bytes")
    recognition_acceptance = tmp_path / "recognition-acceptance.json"
    recognition_acceptance.write_bytes(b'{"accepted":true,"attempt":2}')
    from agents.brook.podcast_subtitles.adapters.memo_recognition import (
        MemoRecognitionManifestV1,
        MemoRecognitionTokenV1,
    )

    raw_tokens = (
        MemoRecognitionTokenV1(
            id="z-001", text="今天", start_ms=100, end_ms=500, confidence=0.99, speaker="guest"
        ),
        MemoRecognitionTokenV1(
            id="z-002", text="我們談", start_ms=500, end_ms=1_000, confidence=0.99, speaker="guest"
        ),
        MemoRecognitionTokenV1(
            id="z-003", text="修修", start_ms=1_000, end_ms=1_500, confidence=0.99, speaker="guest"
        ),
        MemoRecognitionTokenV1(
            id="z-004",
            text="再談內容",
            start_ms=1_500,
            end_ms=2_000,
            confidence=0.99,
            speaker="guest",
        ),
        MemoRecognitionTokenV1(
            id="z-005",
            text=final_text,
            start_ms=2_000,
            end_ms=3_000,
            confidence=0.99,
            speaker="guest",
        ),
    )
    recognition = MemoRecognitionManifestV1(
        memo_version="1.7.5",
        language="zh",
        prompt="鄭國威 泛科學 修修",
        normalized_audio_sha256=hash_file(audio),
        normalized_audio_size_bytes=audio.stat().st_size,
        normalized_audio_duration_ms=4_000,
        source_export_sha256=hash_file(source_export),
        source_export_size_bytes=source_export.stat().st_size,
        source_export_kind="memo_stdout",
        accepted_by_receipt_sha256=hash_file(recognition_acceptance),
        tokens=raw_tokens,
    )
    recognition_path = _write(tmp_path / "memo-recognition.json", recognition)
    adapter = MemoRecognizerAdapter(
        recognition_path,
        source_export=source_export,
        acceptance_receipt=recognition_acceptance,
    )
    request = RecognitionRequest(
        episode_id="zheng-minimized",
        invocation_id="recognition-zheng-minimized",
        normalized_audio=audio,
        expected_normalized_audio_hash=hash_file(audio),
    )
    evidence = adapter.recognize(request)

    cue_export = tmp_path / "memo-gui.srt"
    cue_export.write_text(
        "1\n00:00:00,100 --> 00:00:01,000\n今天，我們談\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\n修修再談內容\n\n"
        f"3\n00:00:02,000 --> 00:00:03,000\n{final_text}。\n",
        encoding="utf-8",
    )
    cue_acceptance = tmp_path / "cue-acceptance.json"
    _write(
        cue_acceptance,
        MemoSrtAcceptanceReceiptV1(
            source_export_sha256=hash_file(cue_export),
            source_export_size_bytes=cue_export.stat().st_size,
            reviewer="human",
            accepted_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
        ),
    )
    cues = (
        MemoAcceptedCueV1(
            id="memo-cue-001",
            start_ms=100,
            end_ms=1_000,
            text="今天我們談",
            source_token_ids=("z-001", "z-002"),
        ),
        MemoAcceptedCueV1(
            id="memo-cue-002",
            start_ms=1_000,
            end_ms=2_000,
            text="修修再談內容",
            source_token_ids=("z-003", "z-004"),
        ),
        MemoAcceptedCueV1(
            id="memo-cue-003",
            start_ms=2_000,
            end_ms=3_000,
            text=final_text,
            source_token_ids=("z-005",),
        ),
    )
    cue_manifest = MemoCueBoundaryManifestV1(
        recognition_manifest_sha256=hash_file(recognition_path),
        source_export_sha256=hash_file(cue_export),
        source_export_size_bytes=cue_export.stat().st_size,
        source_export_kind="memo_gui_srt",
        acceptance_receipt_sha256=hash_file(cue_acceptance),
        base_cues=tuple(MemoSourceCueV1(**cue.model_dump()) for cue in cues),
        cues=cues,
    )
    cue_path = _write(tmp_path / "memo-cues.json", cue_manifest)
    authority = MemoBoundaryAuthorityV1.load_verified(
        cue_path,
        recognition_manifest_sha256=hash_file(recognition_path),
        recognition_evidence=evidence,
        source_export=cue_export,
        acceptance_receipt=cue_acceptance,
    )
    return adapter, request, evidence, authority, source_export


def test_memo_import_is_immutable_and_binds_actual_export_and_acceptance(tmp_path: Path) -> None:
    adapter, request, evidence, _authority, source_export = _memo_fixture(tmp_path)
    raw = Path(adapter._manifest_path).read_bytes()
    portable = evidence.model_copy(
        update={
            "raw_output": evidence.raw_output.model_copy(
                update={"uri": "generation-artifact://raw"}
            )
        }
    )
    assert adapter.verify(portable, request=request, raw_output=raw) == portable
    source_export.write_bytes(b"tampered")
    with pytest.raises(AdapterIntegrityError, match="source export differs"):
        adapter.recognize(request)


def test_punctuation_repair_is_local_logged_and_exactly_verified() -> None:
    base = MemoSourceCueV1(
        id="base-1", start_ms=0, end_ms=2_000, text="今天，我們聊", source_token_ids=("a", "b")
    )
    outputs = (
        MemoAcceptedCueV1(
            id="out-1", start_ms=0, end_ms=1_000, text="今天，", source_token_ids=("a",)
        ),
        MemoAcceptedCueV1(
            id="out-2", start_ms=1_000, end_ms=2_000, text="我們聊", source_token_ids=("b",)
        ),
    )
    fixed = dict(
        recognition_manifest_sha256="1" * 64,
        source_export_sha256="2" * 64,
        source_export_size_bytes=10,
        source_export_kind="reviewed_memo_cue_projection",
        acceptance_receipt_sha256="3" * 64,
        base_cues=(base,),
        cues=outputs,
    )
    manifest = MemoCueBoundaryManifestV1(
        **fixed,
        repairs=(
            MemoBoundaryRepairV1(
                id="repair-1",
                reason_code="explicit_punctuation",
                source_cue_ids=("base-1",),
                output_cue_ids=("out-1", "out-2"),
                proof=MemoBoundaryRepairProofV1(punctuation="，", punctuation_scalar_offset=3),
            ),
        ),
    )
    assert manifest.repairs[0].reason_code == "explicit_punctuation"
    with pytest.raises(ValueError, match="explicit_punctuation"):
        MemoCueBoundaryManifestV1(
            **fixed,
            repairs=(
                MemoBoundaryRepairV1(
                    id="repair-1",
                    reason_code="explicit_punctuation",
                    source_cue_ids=("base-1",),
                    output_cue_ids=("out-1", "out-2"),
                    proof=MemoBoundaryRepairProofV1(punctuation="。", punctuation_scalar_offset=3),
                ),
            ),
        )


def test_normalized_handoff_constructs_content_bound_reused_receipt(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "normalized.wav"
    with wave.open(str(audio), "wb") as target:
        target.setnchannels(2)
        target.setsampwidth(2)
        target.setframerate(48_000)
        target.writeframes(b"\0" * 48_000 * 2 * 2)
    manifest = NormalizedAudioHandoffManifestV1(
        normalized_audio_sha256=hash_file(audio),
        normalized_audio_size_bytes=audio.stat().st_size,
        normalized_audio_duration_ms=1_000,
        accepted_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
    )
    manifest_path = _write(tmp_path / "normalized-handoff.json", manifest)

    result = VerifiedNormalizedAudioHandoffAdapter(manifest_path).normalize(
        NormalizeRequest(
            source_audio=audio,
            expected_source_hash=hash_file(audio),
        )
    )

    assert result.normalized_audio == audio.resolve()
    assert result.receipt.status == "accepted"
    assert result.receipt.production_source == "reused"
    assert result.receipt.original_production_id == (f"upstream-normalized-{hash_file(audio)}")
    assert result.receipt.normalized_duration_ms == 1_000


def test_recognition_request_has_no_reference_vocabulary_prompt_surface(
    tmp_path: Path,
) -> None:
    with pytest.raises(TypeError, match="vocabulary"):
        RecognitionRequest(
            episode_id="episode",
            invocation_id="invocation",
            normalized_audio=tmp_path / "audio.wav",
            vocabulary=("reference-only-term",),
        )
