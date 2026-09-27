"""Operator evidence CLI for the Memo-first Podcast Subtitle V2 boundary."""

from __future__ import annotations

import json
import wave
from datetime import datetime
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
from agents.brook.podcast_subtitles.memo_bundled_runner import (
    MemoBundledRunnerExecutionReceiptV1,
)
from agents.brook.podcast_subtitles.memo_srt_repair import (
    load_verified_memo_srt_repair,
    repair_memo_srt_bytes,
)
from agents.brook.podcast_subtitles.ports import (
    AdapterInputError,
    AdapterIntegrityError,
    NormalizeRequest,
    RecognitionRequest,
)
from agents.brook.podcast_subtitles.production import build_production
from scripts import podcast_subtitle_v2_evidence as _evidence_cli


def _wav(path: Path, *, duration_ms: int = 1_000) -> Path:
    with wave.open(str(path), "wb") as target:
        target.setnchannels(2)
        target.setsampwidth(2)
        target.setframerate(48_000)
        target.writeframes(b"\0" * (48_000 * duration_ms // 1_000) * 2 * 2)
    return path


def _memo_execution_args(
    root: Path,
    *,
    audio: Path,
    source_export: Path,
    source_kind: str,
    tag: str = "memo",
    language: str = "zh",
    prompt: str = "抹布 陳暐軒 高薪賽道",
) -> list[str]:
    """Create one sealed fake execution fixture without invoking Memo."""

    runner = root / "memo-whisper.exe"
    model = root / "ggml-large-v2.bin"
    output_srt = source_export if source_kind == "memo_srt" else root / f"{tag}-output.srt"
    stdout = root / f"{tag}.stdout"
    stderr = root / f"{tag}.stderr"
    receipt = root / f"{tag}-execution.json"
    runner.write_bytes(b"memo-runner-v1")
    model.write_bytes(b"memo-large-v2-model")
    if source_kind != "memo_srt":
        output_srt.write_text(
            "1\n00:00:00,100 --> 00:00:00,500\nexecution output\n",
            encoding="utf-8",
        )
    stdout.write_bytes(source_export.read_bytes() if source_kind != "memo_srt" else b"")
    stderr.write_bytes(b"")
    invocation_input = root / ".memo-staging" / audio.name
    argv = (
        str(runner.resolve()),
        "-m",
        str(model.resolve()),
        "-l",
        language,
        "--prompt",
        prompt,
        "--no-colors",
        "--use-gpu",
        "auto",
        "--output-srt",
        "--max-context",
        "0",
        "--max-len",
        "0",
        "-f",
        str(invocation_input.resolve()),
    )
    payload = MemoBundledRunnerExecutionReceiptV1(
        argv=argv,
        runner_path=str(runner.resolve()),
        runner_sha256=hash_file(runner),
        runner_size_bytes=runner.stat().st_size,
        model_path=str(model.resolve()),
        model_sha256=hash_file(model),
        model_size_bytes=model.stat().st_size,
        input_wav_path=str(audio.resolve()),
        input_wav_sha256=hash_file(audio),
        input_wav_size_bytes=audio.stat().st_size,
        invocation_input_path=str(invocation_input.resolve()),
        gpu="auto",
        language=language,
        prompt=prompt,
        max_context=0,
        max_len=0,
        started_at=datetime.fromisoformat("2026-08-19T00:00:00+00:00"),
        completed_at=datetime.fromisoformat("2026-08-19T00:01:00+00:00"),
        stdout_sha256=hash_file(stdout),
        stdout_size_bytes=stdout.stat().st_size,
        stderr_sha256=hash_file(stderr),
        stderr_size_bytes=stderr.stat().st_size,
        output_srt_path=str(output_srt.resolve()),
        output_srt_sha256=hash_file(output_srt),
        output_srt_size_bytes=output_srt.stat().st_size,
    )
    receipt.write_bytes(canonical_json_bytes(payload))
    return [
        "--episode-root",
        str(root),
        "--memo-execution-receipt",
        str(receipt),
        "--memo-runner",
        str(runner),
        "--memo-model",
        str(model),
        "--memo-output-srt",
        str(output_srt),
        "--memo-stdout",
        str(stdout),
        "--memo-stderr",
        str(stderr),
    ]


def _value(argv: list[str], option: str) -> str:
    return argv[argv.index(option) + 1]


def _replace_value(argv: list[str], option: str, value: Path) -> None:
    argv[argv.index(option) + 1] = str(value)


def _valid_srt_prepare_argv(root: Path) -> list[str]:
    audio = _wav(root / "normalized.wav")
    handoff = root / "normalized-handoff.json"
    _evidence_cli.main(
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
    source = root / "memo.srt"
    source.write_text(
        "1\n00:00:00,100 --> 00:00:00,500\n合法輸出\n", encoding="utf-8"
    )
    prompt = "fixture prompt"
    return [
        "prepare-recognition",
        "--normalized-audio",
        str(audio),
        "--normalized-manifest",
        str(handoff),
        "--source-export",
        str(source),
        "--source-export-kind",
        "memo_srt",
        *_memo_execution_args(
            root,
            audio=audio,
            source_export=source,
            source_kind="memo_srt",
            prompt=prompt,
        ),
        "--memo-version",
        "1.7.5",
        "--language",
        "zh",
        "--prompt",
        prompt,
        "--output",
        str(root / "review.json"),
    ]


class _EvidenceCliProxy:
    @staticmethod
    def main(argv: list[str]) -> int:
        if argv[0] == "prepare-recognition" and "--memo-execution-receipt" not in argv:
            audio = Path(_value(argv, "--normalized-audio"))
            source = Path(
                _value(argv, "--raw-source-export")
                if "--raw-source-export" in argv
                else _value(argv, "--source-export")
            )
            source_kind = _value(argv, "--source-export-kind")
            execution_args = _memo_execution_args(
                audio.parent,
                audio=audio,
                source_export=source,
                source_kind="memo_srt" if source.suffix.lower() == ".srt" else source_kind,
                tag=Path(_value(argv, "--output")).stem,
                language=_value(argv, "--language"),
                prompt=_value(argv, "--prompt"),
            )
            argv = [argv[0], *execution_args, *argv[1:]]
        return _evidence_cli.main(argv)


evidence_cli = _EvidenceCliProxy()


def _recognition_quorum_args(root: Path, review: Path) -> list[str]:
    payload = json.loads(review.read_bytes())
    paths: list[Path] = []
    for worker in ("recognition-worker-a", "recognition-worker-b"):
        path = root / f"{worker}.json"
        path.write_bytes(
            canonical_json_bytes(
                {
                    "schema_version": 1,
                    "contract": "memo-recognition-worker-audit-v1",
                    "episode_id": root.name,
                    "worker_id": worker,
                    "normalized_audio_sha256": payload["normalized_audio_sha256"],
                    "normalized_audio_size_bytes": payload[
                        "normalized_audio_size_bytes"
                    ],
                    "source_export_sha256": payload["source_export_sha256"],
                    "source_export_size_bytes": payload["source_export_size_bytes"],
                    "review_manifest_sha256": hash_file(review),
                    "token_export_sha256": payload["token_export_sha256"],
                    "memo_execution_receipt_sha256": payload[
                        "memo_execution_receipt"
                    ]["sha256"],
                    "reviewed_item_count": len(payload["tokens"]),
                    "qc_passed": True,
                    "accepted": True,
                    "unresolved_findings": [],
                }
            )
        )
        paths.append(path)
    return [
        "--episode-root",
        str(root),
        "--audit-a",
        str(paths[0]),
        "--audit-b",
        str(paths[1]),
    ]


def _cue_quorum_args(root: Path, review: Path, recognition: Path) -> list[str]:
    payload = json.loads(review.read_bytes())
    recognition_payload = json.loads(recognition.read_bytes())
    paths: list[Path] = []
    for worker in ("cue-worker-a", "cue-worker-b"):
        path = root / f"{worker}.json"
        path.write_bytes(
            canonical_json_bytes(
                {
                    "schema_version": 1,
                    "contract": "memo-cue-worker-audit-v1",
                    "episode_id": root.name,
                    "worker_id": worker,
                    "normalized_audio_sha256": recognition_payload[
                        "normalized_audio_sha256"
                    ],
                    "normalized_audio_size_bytes": recognition_payload[
                        "normalized_audio_size_bytes"
                    ],
                    "source_export_sha256": payload["source_export_sha256"],
                    "source_export_size_bytes": payload["source_export_size_bytes"],
                    "review_manifest_sha256": hash_file(review),
                    "recognition_manifest_sha256": payload[
                        "recognition_manifest_sha256"
                    ],
                    "reviewed_item_count": len(payload["cues"]),
                    "qc_passed": True,
                    "accepted": True,
                    "unresolved_findings": [],
                }
            )
        )
        paths.append(path)
    return [
        "--episode-root",
        str(root),
        "--audit-a",
        str(paths[0]),
        "--audit-b",
        str(paths[1]),
    ]


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


def test_prepare_recognition_rejects_valid_srt_from_another_execution(
    tmp_path: Path,
) -> None:
    episode_a = tmp_path / "episode-a"
    episode_b = tmp_path / "episode-b"
    episode_a.mkdir()
    episode_b.mkdir()
    audio_a = _wav(episode_a / "normalized.wav")
    audio_b = _wav(episode_b / "normalized.wav", duration_ms=1_100)
    handoff_b = episode_b / "normalized-handoff.json"
    evidence_cli.main(
        [
            "seal-normalized",
            "--audio",
            str(audio_b),
            "--output",
            str(handoff_b),
            "--accepted-at",
            "2026-08-19T01:00:00+08:00",
        ]
    )
    srt_a = episode_a / "memo.srt"
    srt_a.write_text(
        "1\n00:00:00,100 --> 00:00:00,500\n另一集但格式合法\n",
        encoding="utf-8",
    )
    execution_args = _memo_execution_args(
        episode_a,
        audio=audio_a,
        source_export=srt_a,
        source_kind="memo_srt",
    )

    with pytest.raises(ValueError, match="episode root|normalized audio|lineage"):
        evidence_cli.main(
            [
                "prepare-recognition",
                "--normalized-audio",
                str(audio_b),
                "--normalized-manifest",
                str(handoff_b),
                "--source-export",
                str(srt_a),
                "--source-export-kind",
                "memo_srt",
                *execution_args,
                "--memo-version",
                "1.7.5",
                "--language",
                "zh",
                "--prompt",
                "episode b",
                "--output",
                str(episode_b / "review.json"),
            ]
        )


def test_prepare_recognition_requires_execution_receipt_cli() -> None:
    with pytest.raises(SystemExit):
        _evidence_cli.main(["prepare-recognition"])


@pytest.mark.parametrize(
    "artifact_option",
    [
        "--memo-execution-receipt",
        "--memo-runner",
        "--memo-model",
        "--memo-output-srt",
        "--memo-stdout",
        "--memo-stderr",
        "--normalized-audio",
    ],
)
def test_prepare_recognition_rejects_tampered_execution_artifact(
    tmp_path: Path, artifact_option: str
) -> None:
    argv = _valid_srt_prepare_argv(tmp_path)
    artifact = Path(_value(argv, artifact_option))
    artifact.write_bytes(artifact.read_bytes() + b"tamper")

    with pytest.raises((AdapterInputError, AdapterIntegrityError, ValueError)):
        _evidence_cli.main(argv)


@pytest.mark.parametrize("artifact_option", ["--memo-runner", "--memo-model"])
def test_prepare_recognition_rejects_wrong_runtime_path(
    tmp_path: Path, artifact_option: str
) -> None:
    argv = _valid_srt_prepare_argv(tmp_path)
    alternate = tmp_path / "alternate" / Path(_value(argv, artifact_option)).name
    alternate.parent.mkdir()
    alternate.write_bytes(b"different-valid-file")
    _replace_value(argv, artifact_option, alternate)

    with pytest.raises((AdapterInputError, AdapterIntegrityError, ValueError)):
        _evidence_cli.main(argv)


def test_prepare_recognition_rejects_execution_receipt_path_escape(
    tmp_path: Path,
) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    argv = _valid_srt_prepare_argv(episode)
    outside = tmp_path / "outside-receipt.json"
    outside.write_bytes(Path(_value(argv, "--memo-execution-receipt")).read_bytes())
    _replace_value(argv, "--memo-execution-receipt", outside)

    with pytest.raises(ValueError, match="inside the episode root"):
        _evidence_cli.main(argv)


def test_accept_recognition_fresh_rejects_execution_tamper_after_review(
    tmp_path: Path,
) -> None:
    prepare_argv = _valid_srt_prepare_argv(tmp_path)
    assert _evidence_cli.main(prepare_argv) == 0
    review = Path(_value(prepare_argv, "--output"))
    runner = Path(_value(prepare_argv, "--memo-runner"))
    runner.write_bytes(runner.read_bytes() + b"tamper-after-review")

    with pytest.raises((AdapterInputError, AdapterIntegrityError, ValueError)):
        _evidence_cli.main(
            [
                "accept-recognition",
                "--review",
                str(review),
                "--normalized-audio",
                _value(prepare_argv, "--normalized-audio"),
                "--normalized-manifest",
                _value(prepare_argv, "--normalized-manifest"),
                "--source-export",
                _value(prepare_argv, "--source-export"),
                *_recognition_quorum_args(tmp_path, review),
                "--accepted-at",
                "2026-08-19T01:30:00+08:00",
                "--receipt-output",
                str(tmp_path / "acceptance.json"),
                "--manifest-output",
                str(tmp_path / "recognition.json"),
            ]
        )


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
            *_recognition_quorum_args(tmp_path, review),
            "--accepted-at",
            "2026-08-19T01:30:00+08:00",
            "--receipt-output",
            str(receipt),
            "--manifest-output",
            str(manifest),
        ]
    ) == 0

    accepted = MemoRecognitionAcceptanceReceiptV1.model_validate_json(
        receipt.read_bytes(), strict=True
    )
    assert accepted.reviewer == "agent-quorum"
    assert len(accepted.agent_audits) == 2
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


def test_accept_recognition_free_string_only_is_not_official_authority(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit):
        evidence_cli.main(
            [
                "accept-recognition",
                "--review",
                str(tmp_path / "review.json"),
                "--normalized-audio",
                str(tmp_path / "audio.wav"),
                "--normalized-manifest",
                str(tmp_path / "handoff.json"),
                "--source-export",
                str(tmp_path / "memo.srt"),
                "--reviewer",
                "free-form-reviewer",
                "--confirm-reviewed",
                "--accepted-at",
                "2026-08-19T01:30:00+08:00",
                "--receipt-output",
                str(tmp_path / "receipt.json"),
                "--manifest-output",
                str(tmp_path / "manifest.json"),
            ]
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda audit: audit.update({"accepted": False}), "resolved acceptance"),
        (
            lambda audit: audit.update({"unresolved_findings": ["uncertain"]}),
            "resolved acceptance",
        ),
        (
            lambda audit: audit.update({"normalized_audio_sha256": "0" * 64}),
            "binding mismatch",
        ),
        (
            lambda audit: audit.update({"review_manifest_sha256": "0" * 64}),
            "binding mismatch",
        ),
    ],
)
def test_accept_recognition_rejects_invalid_worker_quorum(
    tmp_path: Path, mutation, message: str
) -> None:
    bundle = _accepted_evidence_bundle(tmp_path)
    quorum = _recognition_quorum_args(tmp_path, bundle["recognition_review"])
    audit_b = Path(quorum[-1])
    payload = json.loads(audit_b.read_bytes())
    mutation(payload)
    audit_b.write_bytes(canonical_json_bytes(payload))
    with pytest.raises(ValueError, match=message):
        evidence_cli.main(
            [
                "accept-recognition",
                "--review",
                str(bundle["recognition_review"]),
                "--normalized-audio",
                str(bundle["audio"]),
                "--normalized-manifest",
                str(bundle["handoff"]),
                "--source-export",
                str(bundle["recognition_source"]),
                "--tokens-json",
                str(bundle["tokens"]),
                *quorum,
                "--accepted-at",
                "2026-08-19T03:00:00+08:00",
                "--receipt-output",
                str(tmp_path / "second-receipt.json"),
                "--manifest-output",
                str(tmp_path / "second-manifest.json"),
            ]
        )


def test_accept_recognition_rejects_same_worker_identity(tmp_path: Path) -> None:
    bundle = _accepted_evidence_bundle(tmp_path)
    quorum = _recognition_quorum_args(tmp_path, bundle["recognition_review"])
    audit_a, audit_b = Path(quorum[-3]), Path(quorum[-1])
    first, second = json.loads(audit_a.read_bytes()), json.loads(audit_b.read_bytes())
    second["worker_id"] = first["worker_id"]
    audit_b.write_bytes(canonical_json_bytes(second))
    with pytest.raises(ValueError, match="independent workers"):
        evidence_cli.main(
            [
                "accept-recognition",
                "--review",
                str(bundle["recognition_review"]),
                "--normalized-audio",
                str(bundle["audio"]),
                "--normalized-manifest",
                str(bundle["handoff"]),
                "--source-export",
                str(bundle["recognition_source"]),
                "--tokens-json",
                str(bundle["tokens"]),
                *quorum,
                "--accepted-at",
                "2026-08-19T03:00:00+08:00",
                "--receipt-output",
                str(tmp_path / "second-receipt.json"),
                "--manifest-output",
                str(tmp_path / "second-manifest.json"),
            ]
        )


def test_accept_recognition_rejects_tampered_worker_audit(tmp_path: Path) -> None:
    bundle = _accepted_evidence_bundle(tmp_path)
    quorum = _recognition_quorum_args(tmp_path, bundle["recognition_review"])
    Path(quorum[-1]).write_bytes(b"tampered")
    with pytest.raises((ValueError, json.JSONDecodeError)):
        evidence_cli.main(
            [
                "accept-recognition",
                "--review",
                str(bundle["recognition_review"]),
                "--normalized-audio",
                str(bundle["audio"]),
                "--normalized-manifest",
                str(bundle["handoff"]),
                "--source-export",
                str(bundle["recognition_source"]),
                "--tokens-json",
                str(bundle["tokens"]),
                *quorum,
                "--accepted-at",
                "2026-08-19T03:00:00+08:00",
                "--receipt-output",
                str(tmp_path / "second-receipt.json"),
                "--manifest-output",
                str(tmp_path / "second-manifest.json"),
            ]
        )


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
            *_cue_quorum_args(tmp_path, review_path, recognition_path),
            "--accepted-at",
            "2026-08-19T02:00:00+08:00",
            "--receipt-output",
            str(receipt_path),
        ]
    ) == 0

    receipt = MemoSrtAcceptanceReceiptV1.model_validate_json(
        receipt_path.read_bytes(), strict=True
    )
    assert receipt.reviewer == "agent-quorum"
    assert len(receipt.agent_audits) == 2
    assert receipt.source_export_sha256 == hash_file(srt)
    assert receipt.recognition_manifest_sha256 == hash_file(recognition_path)
    assert receipt.review_manifest_sha256 == hash_file(review_path)


def test_accept_cues_rejects_same_worker_identity(tmp_path: Path) -> None:
    recognition_path, srt, review_path = _prepared_cue_review(tmp_path)
    quorum = _cue_quorum_args(tmp_path, review_path, recognition_path)
    audit_a, audit_b = Path(quorum[-3]), Path(quorum[-1])
    first, second = json.loads(audit_a.read_bytes()), json.loads(audit_b.read_bytes())
    second["worker_id"] = first["worker_id"]
    audit_b.write_bytes(canonical_json_bytes(second))
    with pytest.raises(ValueError, match="independent workers"):
        evidence_cli.main(
            [
                "accept-cues",
                "--review",
                str(review_path),
                "--recognition-manifest",
                str(recognition_path),
                "--source-export",
                str(srt),
                *quorum,
                "--accepted-at",
                "2026-08-19T03:00:00+08:00",
                "--receipt-output",
                str(tmp_path / "second-cue-receipt.json"),
            ]
        )


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
            *_recognition_quorum_args(tmp_path, recognition_review),
            "--accepted-at",
            "2026-08-19T01:30:00+08:00",
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
            *_cue_quorum_args(tmp_path, cue_review, recognition_manifest),
            "--accepted-at",
            "2026-08-19T02:00:00+08:00",
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
                *_recognition_quorum_args(tmp_path, unresolved_review),
                "--accepted-at",
                "2026-08-19T02:30:00+08:00",
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
                *_cue_quorum_args(
                    tmp_path, unresolved_review, bundle["recognition_manifest"]
                ),
                "--accepted-at",
                "2026-08-19T02:30:00+08:00",
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
                *_recognition_quorum_args(tmp_path, bundle["recognition_review"]),
                "--accepted-at",
                "2026-08-19T02:30:00+08:00",
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


def test_memo_srt_is_a_complete_recognition_import_without_handwritten_tokens(
    tmp_path: Path,
) -> None:
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
    memo_srt = tmp_path / "memo-recognition.srt"
    memo_srt.write_text(
        "1\n00:00:00,100 --> 00:00:00,900\n高薪賽道\n\n"
        "2\n00:00:01,000 --> 00:00:01,800\n科技工作講\n",
        encoding="utf-8",
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
            str(memo_srt),
            "--source-export-kind",
            "memo_srt",
            "--memo-version",
            "1.7.5",
            "--language",
            "zh",
            "--prompt",
            "抹布 陳暐軒",
            "--output",
            str(review),
        ]
    ) == 0
    review_payload = json.loads(review.read_bytes())
    assert [token["id"] for token in review_payload["tokens"]] == [
        "memo-token-000001",
        "memo-token-000002",
    ]
    assert [token["text"] for token in review_payload["tokens"]] == [
        "高薪賽道",
        "科技工作講",
    ]

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
            str(memo_srt),
            *_recognition_quorum_args(tmp_path, review),
            "--accepted-at",
            "2026-08-19T01:30:00+08:00",
            "--receipt-output",
            str(receipt),
            "--manifest-output",
            str(manifest),
        ]
    ) == 0
    imported, _ = load_memo_recognition_manifest(manifest)
    assert imported.source_export_sha256 == hash_file(memo_srt)
    assert imported.tokens[1].end_ms == 1_800


def test_repair_memo_srt_merges_zero_duration_cue_forward_with_exact_lineage(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "memo-raw.srt"
    raw.write_text(
        "1\n00:00:00,100 --> 00:00:00,500\nA\n\n"
        "2\n00:00:00,500 --> 00:00:00,500\nB\n\n"
        "3\n00:00:00,500 --> 00:00:00,900\nC\n",
        encoding="utf-8",
    )
    repaired = tmp_path / "memo-repaired.srt"
    repair_receipt = tmp_path / "memo-repair.json"

    assert evidence_cli.main(
        [
            "repair-memo-srt",
            "--source-export",
            str(raw),
            "--output",
            str(repaired),
            "--receipt-output",
            str(repair_receipt),
        ]
    ) == 0

    assert repaired.read_text(encoding="utf-8") == (
        "1\n00:00:00,100 --> 00:00:00,500\nA\n\n"
        "2\n00:00:00,500 --> 00:00:00,900\nBC\n"
    )
    receipt = json.loads(repair_receipt.read_bytes())
    assert receipt["raw_source_sha256"] == hash_file(raw)
    assert receipt["repaired_source_sha256"] == hash_file(repaired)
    assert receipt["merges"] == [
        {
            "output_end_ms": 900,
            "output_index": 2,
            "output_start_ms": 500,
            "output_text": "BC",
            "source_cues": [
                {"end_ms": 500, "index": 2, "start_ms": 500, "text": "B"},
                {"end_ms": 900, "index": 3, "start_ms": 500, "text": "C"},
            ],
        }
    ]

    audio = _wav(tmp_path / "normalized.wav", duration_ms=1_000)
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
    review = tmp_path / "memo-recognition-review.json"
    assert evidence_cli.main(
        [
            "prepare-recognition",
            "--normalized-audio",
            str(audio),
            "--normalized-manifest",
            str(handoff),
            "--source-export",
            str(repaired),
            "--source-export-kind",
            "memo_srt",
            "--raw-source-export",
            str(raw),
            "--repair-receipt",
            str(repair_receipt),
            "--memo-version",
            "1.7.5",
            "--language",
            "zh",
            "--prompt",
            "test",
            "--output",
            str(review),
        ]
    ) == 0
    review_payload = json.loads(review.read_bytes())
    assert review_payload["raw_source_export_sha256"] == hash_file(raw)
    assert review_payload["source_repair_receipt_sha256"] == hash_file(repair_receipt)
    assert [token["text"] for token in review_payload["tokens"]] == ["A", "BC"]

    recognition_receipt = tmp_path / "memo-recognition-acceptance.json"
    recognition_manifest = tmp_path / "memo-recognition.json"
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
            str(repaired),
            "--raw-source-export",
            str(raw),
            "--repair-receipt",
            str(repair_receipt),
            *_recognition_quorum_args(tmp_path, review),
            "--accepted-at",
            "2026-08-19T01:30:00+08:00",
            "--receipt-output",
            str(recognition_receipt),
            "--manifest-output",
            str(recognition_manifest),
        ]
    ) == 0
    accepted_recognition, _ = load_memo_recognition_manifest(recognition_manifest)
    assert accepted_recognition.raw_source_export_sha256 == hash_file(raw)
    assert accepted_recognition.source_repair_receipt_sha256 == hash_file(repair_receipt)

    cue_review = tmp_path / "memo-cue-review.json"
    assert evidence_cli.main(
        [
            "prepare-cues",
            "--recognition-manifest",
            str(recognition_manifest),
            "--source-export",
            str(repaired),
            "--raw-source-export",
            str(raw),
            "--repair-receipt",
            str(repair_receipt),
            "--output",
            str(cue_review),
        ]
    ) == 0
    cue_payload = json.loads(cue_review.read_bytes())
    assert cue_payload["raw_source_export_sha256"] == hash_file(raw)
    assert cue_payload["source_repair_receipt_sha256"] == hash_file(repair_receipt)
    cue_receipt = tmp_path / "memo-cue-acceptance.json"
    assert evidence_cli.main(
        [
            "accept-cues",
            "--review",
            str(cue_review),
            "--recognition-manifest",
            str(recognition_manifest),
            "--source-export",
            str(repaired),
            "--raw-source-export",
            str(raw),
            "--repair-receipt",
            str(repair_receipt),
            *_cue_quorum_args(tmp_path, cue_review, recognition_manifest),
            "--accepted-at",
            "2026-08-19T02:00:00+08:00",
            "--receipt-output",
            str(cue_receipt),
        ]
    ) == 0
    accepted_cues = MemoSrtAcceptanceReceiptV1.model_validate_json(
        cue_receipt.read_bytes(), strict=True
    )
    assert accepted_cues.raw_source_export_sha256 == hash_file(raw)
    assert accepted_cues.source_repair_receipt_sha256 == hash_file(repair_receipt)


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        (
            "1\n00:00:00,500 --> 00:00:00,500\nA\n\n"
            "2\n00:00:00,600 --> 00:00:00,900\nB\n",
            "no exact adjacent positive anchor",
        ),
        (
            "1\n00:00:00,500 --> 00:00:00,400\nA\n",
            "negative duration",
        ),
        (
            "1\n00:00:00,100 --> 00:00:00,600\nA\n\n"
            "2\n00:00:00,500 --> 00:00:00,500\nB\n",
            "overlaps",
        ),
    ],
)
def test_repair_memo_srt_rejects_unanchored_negative_or_overlapping_cues(
    raw: str, error: str
) -> None:
    with pytest.raises(ValueError, match=error):
        repair_memo_srt_bytes(raw.encode("utf-8"))


@pytest.mark.parametrize("tampered_artifact", ["raw", "repaired", "receipt"])
def test_verified_memo_srt_repair_rejects_stale_or_tampered_lineage(
    tmp_path: Path, tampered_artifact: str
) -> None:
    raw = tmp_path / "memo-raw.srt"
    raw.write_text(
        "1\n00:00:00,500 --> 00:00:00,500\nA\n\n"
        "2\n00:00:00,500 --> 00:00:00,900\nB\n",
        encoding="utf-8",
    )
    repaired = tmp_path / "memo-repaired.srt"
    receipt_path = tmp_path / "memo-repair.json"
    repaired_bytes, receipt = repair_memo_srt_bytes(raw.read_bytes())
    repaired.write_bytes(repaired_bytes)
    receipt_path.write_bytes(canonical_json_bytes(receipt))

    target = {"raw": raw, "repaired": repaired, "receipt": receipt_path}[tampered_artifact]
    target.write_bytes(target.read_bytes() + b"\n")

    with pytest.raises(AdapterIntegrityError):
        load_verified_memo_srt_repair(
            raw_source=raw,
            repaired_source=repaired,
            receipt_path=receipt_path,
        )


@pytest.mark.parametrize(
    ("srt_text", "error"),
    [
        ("1\nmissing timing\ntext\n", "invalid Memo recognition SRT"),
        (
            "1\n00:00:00,100 --> 00:00:01,000\nA\n\n"
            "2\n00:00:00,900 --> 00:00:01,500\nB\n",
            "overlaps",
        ),
        ("1\n00:00:00,100 --> 00:00:02,500\n太長\n", "exceeds normalized audio"),
    ],
)
def test_memo_srt_import_rejects_malformed_overlap_or_out_of_duration(
    tmp_path: Path, srt_text: str, error: str
) -> None:
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
    memo_srt = tmp_path / "memo-recognition.srt"
    memo_srt.write_text(srt_text, encoding="utf-8")
    with pytest.raises(ValueError, match=error):
        evidence_cli.main(
            [
                "prepare-recognition",
                "--normalized-audio",
                str(audio),
                "--normalized-manifest",
                str(handoff),
                "--source-export",
                str(memo_srt),
                "--source-export-kind",
                "memo_srt",
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
