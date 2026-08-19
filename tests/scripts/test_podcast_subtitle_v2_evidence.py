"""Operator evidence CLI for the Memo-first Podcast Subtitle V2 boundary."""

from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.adapters.memo_recognition import (
    MemoRecognitionAcceptanceReceiptV1,
    MemoRecognitionManifestV1,
    MemoRecognitionTokenV1,
    MemoRecognizerAdapter,
    load_memo_recognition_manifest,
)
from agents.brook.podcast_subtitles.adapters.normalized_handoff import (
    VerifiedNormalizedAudioHandoffAdapter,
)
from agents.brook.podcast_subtitles.composition import FactoryContextV1
from agents.brook.podcast_subtitles.hashing import canonical_json_bytes, hash_file
from agents.brook.podcast_subtitles.memo_boundary import (
    MemoSrtAcceptanceReceiptV1,
    MemoSrtReviewManifestV1,
)
from agents.brook.podcast_subtitles.ports import (
    AdapterIntegrityError,
    NormalizeRequest,
    RecognitionRequest,
)
from agents.brook.podcast_subtitles.production import build_production
from scripts import podcast_subtitle_v2_evidence as evidence_cli


def _wav(path: Path, *, duration_ms: int = 1_000) -> Path:
    with wave.open(str(path), "wb") as target:
        target.setnchannels(2)
        target.setsampwidth(2)
        target.setframerate(48_000)
        target.writeframes(b"\0" * (48_000 * duration_ms // 1_000) * 2 * 2)
    return path


def test_seal_normalized_creates_a_canonical_verified_handoff(tmp_path: Path) -> None:
    audio = _wav(tmp_path / "normalized.wav")
    manifest = tmp_path / "normalized-handoff.json"

    assert evidence_cli.main(
        [
            "seal-normalized",
            "--audio",
            str(audio),
            "--output",
            str(manifest),
            "--accepted-at",
            "2026-08-19T01:00:00+08:00",
        ]
    ) == 0

    result = VerifiedNormalizedAudioHandoffAdapter(manifest).normalize(
        NormalizeRequest(source_audio=audio, expected_source_hash=hash_file(audio))
    )
    assert result.receipt.normalized_duration_ms == 1_000


def test_prepare_recognition_binds_review_to_normalized_audio_and_raw_export(
    tmp_path: Path,
) -> None:
    audio = _wav(tmp_path / "normalized.wav")
    handoff = tmp_path / "normalized-handoff.json"
    assert evidence_cli.main(
        [
            "seal-normalized",
            "--audio",
            str(audio),
            "--output",
            str(handoff),
            "--accepted-at",
            "2026-08-19T01:00:00+08:00",
        ]
    ) == 0
    source_export = tmp_path / "memo.json"
    source_export.write_bytes(b'{"memo":"raw immutable export"}')
    tokens = tmp_path / "memo-tokens.json"
    tokens.write_bytes(
        canonical_json_bytes(
            {
                "tokens": [
                    {
                        "confidence": 0.98,
                        "end_ms": 500,
                        "id": "memo-token-000001",
                        "speaker": "guest",
                        "start_ms": 100,
                        "text": "高薪賽道",
                    }
                ]
            }
        )
    )
    review = tmp_path / "memo-recognition-review.json"

    assert evidence_cli.main(
        [
            "prepare-recognition",
            "--normalized-audio",
            str(audio),
            "--normalized-manifest",
            str(handoff),
            "--source-export",
            str(source_export),
            "--source-export-kind",
            "memo_json",
            "--tokens-json",
            str(tokens),
            "--memo-version",
            "1.7.5",
            "--language",
            "zh",
            "--prompt",
            "抹布 陳暐軒 高薪賽道",
            "--output",
            str(review),
        ]
    ) == 0

    payload = json.loads(review.read_bytes())
    assert payload["contract"] == "memo-recognition-review-v1"
    assert payload["normalized_audio_sha256"] == hash_file(audio)
    assert payload["source_export_sha256"] == hash_file(source_export)
    assert payload["tokens"][0]["id"] == "memo-token-000001"
    assert "accepted" not in payload


def test_accept_recognition_requires_explicit_reviewer_and_builds_importable_evidence(
    tmp_path: Path,
) -> None:
    audio = _wav(tmp_path / "normalized.wav")
    handoff = tmp_path / "normalized-handoff.json"
    evidence_cli.main(
        [
            "seal-normalized",
            "--audio",
            str(audio),
            "--output",
            str(handoff),
            "--accepted-at",
            "2026-08-19T01:00:00+08:00",
        ]
    )
    source_export = tmp_path / "memo.json"
    source_export.write_bytes(b'{"memo":"raw immutable export"}')
    tokens = tmp_path / "memo-tokens.json"
    tokens.write_bytes(
        canonical_json_bytes(
            {
                "tokens": [
                    {
                        "confidence": 0.98,
                        "end_ms": 500,
                        "id": "memo-token-000001",
                        "speaker": "guest",
                        "start_ms": 100,
                        "text": "高薪賽道",
                    }
                ]
            }
        )
    )
    review = tmp_path / "memo-recognition-review.json"
    evidence_cli.main(
        [
            "prepare-recognition",
            "--normalized-audio",
            str(audio),
            "--normalized-manifest",
            str(handoff),
            "--source-export",
            str(source_export),
            "--source-export-kind",
            "memo_json",
            "--tokens-json",
            str(tokens),
            "--memo-version",
            "1.7.5",
            "--language",
            "zh",
            "--prompt",
            "抹布 陳暐軒 高薪賽道",
            "--output",
            str(review),
        ]
    )
    receipt = tmp_path / "memo-recognition-acceptance.json"
    manifest = tmp_path / "memo-recognition.json"

    assert evidence_cli.main(
        [
            "accept-recognition",
            "--review",
            str(review),
            "--normalized-audio",
            str(audio),
            "--normalized-manifest",
            str(handoff),
            "--source-export",
            str(source_export),
            "--tokens-json",
            str(tokens),
            "--reviewer",
            "shosho",
            "--accepted-at",
            "2026-08-19T01:30:00+08:00",
            "--confirm-reviewed",
            "--receipt-output",
            str(receipt),
            "--manifest-output",
            str(manifest),
        ]
    ) == 0

    accepted = MemoRecognitionAcceptanceReceiptV1.model_validate_json(
        receipt.read_bytes(), strict=True
    )
    assert accepted.reviewer == "shosho"
    imported, _ = load_memo_recognition_manifest(manifest)
    assert imported.accepted_by_receipt_sha256 == hash_file(receipt)
    adapter = MemoRecognizerAdapter(
        manifest, source_export=source_export, acceptance_receipt=receipt
    )
    recognition = adapter.recognize(
        RecognitionRequest(
            episode_id="moboo",
            invocation_id="memo-import-1",
            normalized_audio=audio,
            expected_normalized_audio_hash=hash_file(audio),
        )
    )
    assert recognition.tokens[0].text == "高薪賽道"


def test_prepare_cues_parses_srt_and_binds_it_to_recognition(tmp_path: Path) -> None:
    receipt = tmp_path / "recognition-acceptance.json"
    receipt.write_bytes(b'{"accepted":true}')
    source = tmp_path / "memo.json"
    source.write_bytes(b'{"memo":"raw"}')
    recognition = MemoRecognitionManifestV1(
        memo_version="1.7.5",
        language="zh",
        prompt="抹布",
        normalized_audio_sha256="a" * 64,
        normalized_audio_size_bytes=100,
        normalized_audio_duration_ms=2_000,
        source_export_sha256=hash_file(source),
        source_export_size_bytes=source.stat().st_size,
        source_export_kind="memo_json",
        accepted_by_receipt_sha256=hash_file(receipt),
        tokens=(
            MemoRecognitionTokenV1(
                id="memo-token-000001", text="高薪賽道", start_ms=100, end_ms=900
            ),
        ),
    )
    recognition_path = tmp_path / "memo-recognition.json"
    recognition_path.write_bytes(canonical_json_bytes(recognition))
    srt = tmp_path / "memo-gui.srt"
    srt.write_text(
        "1\n00:00:00,100 --> 00:00:00,900\n高薪賽道\n", encoding="utf-8"
    )
    review_path = tmp_path / "memo-cue-review.json"

    assert evidence_cli.main(
        [
            "prepare-cues",
            "--recognition-manifest",
            str(recognition_path),
            "--source-export",
            str(srt),
            "--output",
            str(review_path),
        ]
    ) == 0

    review = MemoSrtReviewManifestV1.model_validate_json(review_path.read_bytes(), strict=True)
    assert review.recognition_manifest_sha256 == hash_file(recognition_path)
    assert review.source_export_sha256 == hash_file(srt)
    assert review.cues[0].id == "memo-cue-000001"
    assert review.cues[0].text == "高薪賽道"


def test_accept_cues_requires_explicit_reviewer_and_seals_exact_srt(tmp_path: Path) -> None:
    recognition_path, srt, review_path = _prepared_cue_review(tmp_path)
    receipt_path = tmp_path / "memo-cue-acceptance.json"

    assert evidence_cli.main(
        [
            "accept-cues",
            "--review",
            str(review_path),
            "--recognition-manifest",
            str(recognition_path),
            "--source-export",
            str(srt),
            "--reviewer",
            "shosho",
            "--accepted-at",
            "2026-08-19T02:00:00+08:00",
            "--confirm-reviewed",
            "--receipt-output",
            str(receipt_path),
        ]
    ) == 0

    receipt = MemoSrtAcceptanceReceiptV1.model_validate_json(
        receipt_path.read_bytes(), strict=True
    )
    assert receipt.reviewer == "shosho"
    assert receipt.source_export_sha256 == hash_file(srt)
    assert receipt.recognition_manifest_sha256 == hash_file(recognition_path)
    assert receipt.review_manifest_sha256 == hash_file(review_path)


def _prepared_cue_review(tmp_path: Path) -> tuple[Path, Path, Path]:
    receipt = tmp_path / "recognition-acceptance.json"
    receipt.write_bytes(b'{"accepted":true}')
    source = tmp_path / "memo.json"
    source.write_bytes(b'{"memo":"raw"}')
    recognition = MemoRecognitionManifestV1(
        memo_version="1.7.5",
        language="zh",
        prompt="抹布",
        normalized_audio_sha256="a" * 64,
        normalized_audio_size_bytes=100,
        normalized_audio_duration_ms=2_000,
        source_export_sha256=hash_file(source),
        source_export_size_bytes=source.stat().st_size,
        source_export_kind="memo_json",
        accepted_by_receipt_sha256=hash_file(receipt),
        tokens=(
            MemoRecognitionTokenV1(
                id="memo-token-000001", text="高薪賽道", start_ms=100, end_ms=900
            ),
        ),
    )
    recognition_path = tmp_path / "memo-recognition.json"
    recognition_path.write_bytes(canonical_json_bytes(recognition))
    srt = tmp_path / "memo-gui.srt"
    srt.write_text(
        "1\n00:00:00,100 --> 00:00:00,900\n高薪賽道\n", encoding="utf-8"
    )
    review_path = tmp_path / "memo-cue-review.json"
    evidence_cli.main(
        [
            "prepare-cues",
            "--recognition-manifest",
            str(recognition_path),
            "--source-export",
            str(srt),
            "--output",
            str(review_path),
        ]
    )
    return recognition_path, srt, review_path


def test_status_verifies_complete_bundle_and_exports_production_environment(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    bundle = _accepted_evidence_bundle(tmp_path)

    assert evidence_cli.main(
        [
            "status",
            "--normalized-audio",
            str(bundle["audio"]),
            "--normalized-manifest",
            str(bundle["handoff"]),
            "--recognition-manifest",
            str(bundle["recognition_manifest"]),
            "--recognition-source-export",
            str(bundle["recognition_source"]),
            "--recognition-acceptance-receipt",
            str(bundle["recognition_receipt"]),
            "--cue-source-export",
            str(bundle["cue_source"]),
            "--cue-acceptance-receipt",
            str(bundle["cue_receipt"]),
        ]
    ) == 0

    status = json.loads(capsys.readouterr().out)
    assert status["ready"] is True
    environment = status["environment"]
    assert set(environment) == {
        "PODCAST_SUBTITLE_V2_NORMALIZED_HANDOFF_MANIFEST",
        "PODCAST_SUBTITLE_V2_MEMO_RECOGNITION_MANIFEST",
        "PODCAST_SUBTITLE_V2_MEMO_RECOGNITION_SOURCE_EXPORT",
        "PODCAST_SUBTITLE_V2_MEMO_RECOGNITION_ACCEPTANCE_RECEIPT",
        "PODCAST_SUBTITLE_V2_MEMO_CUE_SOURCE_EXPORT",
        "PODCAST_SUBTITLE_V2_MEMO_CUE_ACCEPTANCE_RECEIPT",
    }
    environment.update(
        {
            "PODCAST_SUBTITLE_V2_TEXT_AUDIT_MODEL": "gpt-5.6-sol",
            "PODCAST_SUBTITLE_V2_TEXT_AUDIT_MODEL_VERSION": "2026-08-12",
            "PODCAST_SUBTITLE_V2_SEMANTIC_MODEL": "gpt-5.6-sol",
            "PODCAST_SUBTITLE_V2_SEMANTIC_MODEL_VERSION": "2026-08-12",
            "PODCAST_SUBTITLE_V2_AUDIO_AUDIT_MODEL": "gemini-3.6-flash",
            "PODCAST_SUBTITLE_V2_AUDIO_AUDIT_MODEL_VERSION": "2026-07-22",
        }
    )
    monkeypatch.setattr(
        "agents.brook.podcast_subtitles.production.os.environ", environment
    )
    module = build_production(FactoryContextV1(1, tmp_path / "episode", None))
    assert isinstance(module._recognizers[0], MemoRecognizerAdapter)


def _accepted_evidence_bundle(tmp_path: Path) -> dict[str, Path]:
    audio = _wav(tmp_path / "normalized.wav", duration_ms=2_000)
    handoff = tmp_path / "normalized-handoff.json"
    evidence_cli.main(
        [
            "seal-normalized",
            "--audio",
            str(audio),
            "--output",
            str(handoff),
            "--accepted-at",
            "2026-08-19T01:00:00+08:00",
        ]
    )
    recognition_source = tmp_path / "memo.json"
    recognition_source.write_bytes(b'{"memo":"raw immutable export"}')
    tokens = tmp_path / "memo-tokens.json"
    tokens.write_bytes(
        canonical_json_bytes(
            {
                "tokens": [
                    {
                        "confidence": 0.98,
                        "end_ms": 900,
                        "id": "memo-token-000001",
                        "speaker": "guest",
                        "start_ms": 100,
                        "text": "高薪賽道",
                    }
                ]
            }
        )
    )
    recognition_review = tmp_path / "memo-recognition-review.json"
    evidence_cli.main(
        [
            "prepare-recognition",
            "--normalized-audio",
            str(audio),
            "--normalized-manifest",
            str(handoff),
            "--source-export",
            str(recognition_source),
            "--source-export-kind",
            "memo_json",
            "--tokens-json",
            str(tokens),
            "--memo-version",
            "1.7.5",
            "--language",
            "zh",
            "--prompt",
            "抹布 陳暐軒 高薪賽道",
            "--output",
            str(recognition_review),
        ]
    )
    recognition_receipt = tmp_path / "memo-recognition-acceptance.json"
    recognition_manifest = tmp_path / "memo-recognition.json"
    evidence_cli.main(
        [
            "accept-recognition",
            "--review",
            str(recognition_review),
            "--normalized-audio",
            str(audio),
            "--normalized-manifest",
            str(handoff),
            "--source-export",
            str(recognition_source),
            "--tokens-json",
            str(tokens),
            "--reviewer",
            "shosho",
            "--accepted-at",
            "2026-08-19T01:30:00+08:00",
            "--confirm-reviewed",
            "--receipt-output",
            str(recognition_receipt),
            "--manifest-output",
            str(recognition_manifest),
        ]
    )
    cue_source = tmp_path / "memo-gui.srt"
    cue_source.write_text(
        "1\n00:00:00,100 --> 00:00:00,900\n高薪賽道\n", encoding="utf-8"
    )
    cue_review = tmp_path / "memo-cue-review.json"
    evidence_cli.main(
        [
            "prepare-cues",
            "--recognition-manifest",
            str(recognition_manifest),
            "--source-export",
            str(cue_source),
            "--output",
            str(cue_review),
        ]
    )
    cue_receipt = tmp_path / "memo-cue-acceptance.json"
    evidence_cli.main(
        [
            "accept-cues",
            "--review",
            str(cue_review),
            "--recognition-manifest",
            str(recognition_manifest),
            "--source-export",
            str(cue_source),
            "--reviewer",
            "shosho",
            "--accepted-at",
            "2026-08-19T02:00:00+08:00",
            "--confirm-reviewed",
            "--receipt-output",
            str(cue_receipt),
        ]
    )
    return {
        "audio": audio,
        "handoff": handoff,
        "recognition_source": recognition_source,
        "tokens": tokens,
        "recognition_review": recognition_review,
        "recognition_receipt": recognition_receipt,
        "recognition_manifest": recognition_manifest,
        "cue_source": cue_source,
        "cue_receipt": cue_receipt,
    }


@pytest.mark.parametrize(
    "token_ids",
    [
        ("word-1",),
        ("memo-token-000001", "memo-token-000001"),
    ],
)
def test_prepare_recognition_rejects_noncanonical_or_duplicate_token_ids(
    tmp_path: Path, token_ids: tuple[str, ...]
) -> None:
    audio = _wav(tmp_path / "normalized.wav")
    handoff = tmp_path / "normalized-handoff.json"
    evidence_cli.main(
        [
            "seal-normalized",
            "--audio",
            str(audio),
            "--output",
            str(handoff),
            "--accepted-at",
            "2026-08-19T01:00:00+08:00",
        ]
    )
    source = tmp_path / "memo.json"
    source.write_bytes(b'{"memo":"raw"}')
    tokens = tmp_path / "tokens.json"
    tokens.write_bytes(
        canonical_json_bytes(
            {
                "tokens": [
                    {
                        "confidence": None,
                        "end_ms": 200 + index * 100,
                        "id": token_id,
                        "speaker": None,
                        "start_ms": 100 + index * 100,
                        "text": "字",
                    }
                    for index, token_id in enumerate(token_ids)
                ]
            }
        )
    )
    with pytest.raises(ValueError, match="canonical and sequential"):
        evidence_cli.main(
            [
                "prepare-recognition",
                "--normalized-audio",
                str(audio),
                "--normalized-manifest",
                str(handoff),
                "--source-export",
                str(source),
                "--source-export-kind",
                "memo_json",
                "--tokens-json",
                str(tokens),
                "--memo-version",
                "1.7.5",
                "--language",
                "zh",
                "--prompt",
                "抹布",
                "--output",
                str(tmp_path / "review.json"),
            ]
        )


def test_status_fails_closed_when_normalized_audio_is_tampered(tmp_path: Path) -> None:
    bundle = _accepted_evidence_bundle(tmp_path)
    bundle["audio"].write_bytes(bundle["audio"].read_bytes() + b"tampered")
    with pytest.raises(AdapterIntegrityError, match="belongs to other audio"):
        evidence_cli.main(_status_args(bundle))


def test_status_fails_closed_when_memo_export_is_tampered(tmp_path: Path) -> None:
    bundle = _accepted_evidence_bundle(tmp_path)
    bundle["recognition_source"].write_bytes(b"tampered")
    with pytest.raises(AdapterIntegrityError, match="source export differs"):
        evidence_cli.main(_status_args(bundle))


def test_status_fails_closed_when_acceptance_receipt_is_tampered(tmp_path: Path) -> None:
    bundle = _accepted_evidence_bundle(tmp_path)
    bundle["recognition_receipt"].write_bytes(b'{"accepted":true}')
    with pytest.raises(ValueError):
        evidence_cli.main(_status_args(bundle))


def test_status_fails_closed_when_required_artifact_is_missing(tmp_path: Path) -> None:
    bundle = _accepted_evidence_bundle(tmp_path)
    bundle["cue_receipt"] = tmp_path / "missing-cue-acceptance.json"
    with pytest.raises(FileNotFoundError):
        evidence_cli.main(_status_args(bundle))


def test_accept_recognition_rejects_unresolved_findings(tmp_path: Path) -> None:
    bundle = _accepted_evidence_bundle(tmp_path)
    unresolved_review = tmp_path / "unresolved-recognition-review.json"
    evidence_cli.main(
        [
            "prepare-recognition",
            "--normalized-audio",
            str(bundle["audio"]),
            "--normalized-manifest",
            str(bundle["handoff"]),
            "--source-export",
            str(bundle["recognition_source"]),
            "--source-export-kind",
            "memo_json",
            "--tokens-json",
            str(bundle["tokens"]),
            "--memo-version",
            "1.7.5",
            "--language",
            "zh",
            "--prompt",
            "抹布 陳暐軒 高薪賽道",
            "--unresolved-finding",
            "guest name spelling requires review",
            "--output",
            str(unresolved_review),
        ]
    )
    with pytest.raises(ValueError, match="unresolved findings"):
        evidence_cli.main(
            [
                "accept-recognition",
                "--review",
                str(unresolved_review),
                "--normalized-audio",
                str(bundle["audio"]),
                "--normalized-manifest",
                str(bundle["handoff"]),
                "--source-export",
                str(bundle["recognition_source"]),
                "--tokens-json",
                str(bundle["tokens"]),
                "--reviewer",
                "shosho",
                "--accepted-at",
                "2026-08-19T02:30:00+08:00",
                "--confirm-reviewed",
                "--receipt-output",
                str(tmp_path / "unresolved-receipt.json"),
                "--manifest-output",
                str(tmp_path / "unresolved-manifest.json"),
            ]
        )


def test_accept_cues_rejects_unresolved_findings(tmp_path: Path) -> None:
    bundle = _accepted_evidence_bundle(tmp_path)
    unresolved_review = tmp_path / "unresolved-cue-review.json"
    evidence_cli.main(
        [
            "prepare-cues",
            "--recognition-manifest",
            str(bundle["recognition_manifest"]),
            "--source-export",
            str(bundle["cue_source"]),
            "--unresolved-finding",
            "cue boundary requires listening review",
            "--output",
            str(unresolved_review),
        ]
    )
    with pytest.raises(ValueError, match="unresolved findings"):
        evidence_cli.main(
            [
                "accept-cues",
                "--review",
                str(unresolved_review),
                "--recognition-manifest",
                str(bundle["recognition_manifest"]),
                "--source-export",
                str(bundle["cue_source"]),
                "--reviewer",
                "shosho",
                "--accepted-at",
                "2026-08-19T02:30:00+08:00",
                "--confirm-reviewed",
                "--receipt-output",
                str(tmp_path / "unresolved-cue-receipt.json"),
            ]
        )


def test_accept_recognition_rejects_wrong_normalized_audio_binding(tmp_path: Path) -> None:
    bundle = _accepted_evidence_bundle(tmp_path)
    other_audio = _wav(tmp_path / "other-normalized.wav", duration_ms=1_000)
    with pytest.raises(AdapterIntegrityError):
        evidence_cli.main(
            [
                "accept-recognition",
                "--review",
                str(bundle["recognition_review"]),
                "--normalized-audio",
                str(other_audio),
                "--normalized-manifest",
                str(bundle["handoff"]),
                "--source-export",
                str(bundle["recognition_source"]),
                "--tokens-json",
                str(bundle["tokens"]),
                "--reviewer",
                "shosho",
                "--accepted-at",
                "2026-08-19T02:30:00+08:00",
                "--confirm-reviewed",
                "--receipt-output",
                str(tmp_path / "wrong-binding-receipt.json"),
                "--manifest-output",
                str(tmp_path / "wrong-binding-manifest.json"),
            ]
        )


def _status_args(bundle: dict[str, Path]) -> list[str]:
    return [
        "status",
        "--normalized-audio",
        str(bundle["audio"]),
        "--normalized-manifest",
        str(bundle["handoff"]),
        "--recognition-manifest",
        str(bundle["recognition_manifest"]),
        "--recognition-source-export",
        str(bundle["recognition_source"]),
        "--recognition-acceptance-receipt",
        str(bundle["recognition_receipt"]),
        "--cue-source-export",
        str(bundle["cue_source"]),
        "--cue-acceptance-receipt",
        str(bundle["cue_receipt"]),
    ]
