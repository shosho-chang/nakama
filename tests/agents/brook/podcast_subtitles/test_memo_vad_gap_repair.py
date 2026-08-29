from __future__ import annotations

import json
import subprocess
import wave
from datetime import datetime
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.hashing import canonical_json_bytes, hash_file
from agents.brook.podcast_subtitles.memo_bundled_runner import (
    MemoBundledRunnerExecutionReceiptV1,
    MemoBundledRunnerRequest,
    execute_memo_bundled_runner,
)
from agents.brook.podcast_subtitles.memo_projection import parse_srt
from agents.brook.podcast_subtitles.memo_vad_gap_repair import (
    MemoVadGapRepairInputs,
    load_verified_memo_vad_gap_repair,
    repair_memo_vad_gap,
)
from agents.brook.podcast_subtitles.ports import (
    AdapterInputError,
    AdapterIntegrityError,
)
from scripts import podcast_subtitle_v2_evidence as evidence_cli


def _recognition_quorum_args(root: Path, review: Path) -> list[str]:
    payload = json.loads(review.read_bytes())
    audits: list[Path] = []
    for worker_id in ("recognition-worker-a", "recognition-worker-b"):
        audit = root / f"{worker_id}.json"
        audit.write_bytes(
            canonical_json_bytes(
                {
                    "schema_version": 1,
                    "contract": "memo-recognition-worker-audit-v1",
                    "episode_id": root.name,
                    "worker_id": worker_id,
                    "normalized_audio_sha256": payload["normalized_audio_sha256"],
                    "normalized_audio_size_bytes": payload["normalized_audio_size_bytes"],
                    "source_export_sha256": payload["source_export_sha256"],
                    "source_export_size_bytes": payload["source_export_size_bytes"],
                    "review_manifest_sha256": hash_file(review),
                    "token_export_sha256": payload["token_export_sha256"],
                    "memo_execution_receipt_sha256": payload["memo_execution_receipt"]["sha256"],
                    "reviewed_item_count": len(payload["tokens"]),
                    "qc_passed": True,
                    "accepted": True,
                    "unresolved_findings": [],
                }
            )
        )
        audits.append(audit)
    return [
        "--episode-root",
        str(root),
        "--audit-a",
        str(audits[0]),
        "--audit-b",
        str(audits[1]),
    ]


def _cue_quorum_args(root: Path, review: Path, recognition: Path) -> list[str]:
    payload = json.loads(review.read_bytes())
    recognition_payload = json.loads(recognition.read_bytes())
    audits: list[Path] = []
    for worker_id in ("cue-worker-a", "cue-worker-b"):
        audit = root / f"{worker_id}.json"
        audit.write_bytes(
            canonical_json_bytes(
                {
                    "schema_version": 1,
                    "contract": "memo-cue-worker-audit-v1",
                    "episode_id": root.name,
                    "worker_id": worker_id,
                    "normalized_audio_sha256": recognition_payload["normalized_audio_sha256"],
                    "normalized_audio_size_bytes": recognition_payload[
                        "normalized_audio_size_bytes"
                    ],
                    "source_export_sha256": payload["source_export_sha256"],
                    "source_export_size_bytes": payload["source_export_size_bytes"],
                    "review_manifest_sha256": hash_file(review),
                    "recognition_manifest_sha256": payload["recognition_manifest_sha256"],
                    "reviewed_item_count": len(payload["cues"]),
                    "qc_passed": True,
                    "accepted": True,
                    "unresolved_findings": [],
                }
            )
        )
        audits.append(audit)
    return [
        "--episode-root",
        str(root),
        "--audit-a",
        str(audits[0]),
        "--audit-b",
        str(audits[1]),
    ]


def _original_execution_args(inputs: MemoVadGapRepairInputs, root: Path) -> list[str]:
    stdout = root / "original-memo.stdout"
    stderr = root / "original-memo.stderr"
    receipt_path = root / "original-memo-execution.json"
    stdout.write_bytes(b"")
    stderr.write_bytes(b"")
    invocation_input = root / ".original-memo-stage" / inputs.normalized_audio.name
    argv = (
        str(inputs.runner.resolve()),
        "-m",
        str(inputs.model.resolve()),
        "-l",
        "zh",
        "--prompt",
        "fixture",
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
    receipt = MemoBundledRunnerExecutionReceiptV1(
        argv=argv,
        runner_path=str(inputs.runner.resolve()),
        runner_sha256=hash_file(inputs.runner),
        runner_size_bytes=inputs.runner.stat().st_size,
        model_path=str(inputs.model.resolve()),
        model_sha256=hash_file(inputs.model),
        model_size_bytes=inputs.model.stat().st_size,
        input_wav_path=str(inputs.normalized_audio.resolve()),
        input_wav_sha256=hash_file(inputs.normalized_audio),
        input_wav_size_bytes=inputs.normalized_audio.stat().st_size,
        invocation_input_path=str(invocation_input.resolve()),
        gpu="auto",
        language="zh",
        prompt="fixture",
        max_context=0,
        max_len=0,
        started_at=datetime.fromisoformat("2026-08-19T00:00:00+00:00"),
        completed_at=datetime.fromisoformat("2026-08-19T00:01:00+00:00"),
        stdout_sha256=hash_file(stdout),
        stdout_size_bytes=stdout.stat().st_size,
        stderr_sha256=hash_file(stderr),
        stderr_size_bytes=stderr.stat().st_size,
        output_srt_path=str(inputs.raw_source.resolve()),
        output_srt_sha256=hash_file(inputs.raw_source),
        output_srt_size_bytes=inputs.raw_source.stat().st_size,
    )
    receipt_path.write_bytes(canonical_json_bytes(receipt))
    return [
        "--episode-root",
        str(root),
        "--memo-execution-receipt",
        str(receipt_path),
        "--memo-output-srt",
        str(inputs.raw_source),
        "--memo-stdout",
        str(stdout),
        "--memo-stderr",
        str(stderr),
    ]


def _wav(path: Path, *, duration_ms: int) -> Path:
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(1_000)
        stream.writeframes(b"\0\0" * duration_ms)
    return path


def _fixture(tmp_path: Path) -> tuple[MemoVadGapRepairInputs, Path, Path]:
    normalized = _wav(tmp_path / "normalized.wav", duration_ms=10_000)
    handoff = tmp_path / "handoff.json"
    handoff.write_text('{"sealed":true}\n', encoding="utf-8")
    raw = tmp_path / "raw.srt"
    raw.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nA\n\n"
        "2\n00:00:01,000 --> 00:00:01,000\nB\n\n"
        "3\n00:00:01,000 --> 00:00:02,000\nC\n\n"
        "4\n00:00:06,000 --> 00:00:07,000\nD\n",
        encoding="utf-8",
    )
    from agents.brook.podcast_subtitles.memo_srt_repair import repair_memo_srt_bytes

    parent_bytes, parent_receipt = repair_memo_srt_bytes(raw.read_bytes())
    parent = tmp_path / "parent.srt"
    parent.write_bytes(parent_bytes)
    parent_receipt_path = tmp_path / "parent-repair.json"
    parent_receipt_path.write_bytes(canonical_json_bytes(parent_receipt))
    target_wav = _wav(tmp_path / "target.wav", duration_ms=3_000)
    target_srt = tmp_path / "target.srt"
    runner = tmp_path / "main.exe"
    runner.write_bytes(b"runner")
    model = tmp_path / "ggml-large-v2.bin"
    model.write_bytes(b"model")
    target_stdout = tmp_path / "target.stdout.log"
    target_stderr = tmp_path / "target.stderr.log"
    target_execution_receipt = tmp_path / "target.execution.v1.json"
    execution_request = MemoBundledRunnerRequest(
        runner=runner,
        model=model,
        input_wav=target_wav,
        output_srt=target_srt,
        stdout_output=target_stdout,
        stderr_output=target_stderr,
        receipt_output=target_execution_receipt,
        gpu="0",
        language="zh",
        prompt="，。",
        max_context=-1,
        max_len=0,
    )

    def invoke(argv: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
        invocation_input = Path(argv[argv.index("-f") + 1])
        invocation_input.with_suffix(".srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nX\n\n2\n00:00:01,000 --> 00:00:03,000\n台場\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(argv, 0, b"stdout", b"")

    execute_memo_bundled_runner(execution_request, invoke=invoke)
    inputs = MemoVadGapRepairInputs(
        normalized_audio=normalized,
        normalized_handoff=handoff,
        raw_source=raw,
        parent_repaired_source=parent,
        parent_repair_receipt=parent_receipt_path,
        target_wav=target_wav,
        target_srt=target_srt,
        target_stdout=target_stdout,
        target_stderr=target_stderr,
        target_execution_receipt=target_execution_receipt,
        runner=runner,
        model=model,
        global_offset_ms=2_500,
        declared_gap_start_ms=2_500,
        declared_gap_end_ms=5_500,
    )
    return inputs, tmp_path / "composite.srt", tmp_path / "gap-repair.json"


def _replace_execution_srt(inputs: MemoVadGapRepairInputs, srt_text: str) -> None:
    for path in (
        inputs.target_srt,
        inputs.target_stdout,
        inputs.target_stderr,
        inputs.target_execution_receipt,
    ):
        path.unlink()
    request = MemoBundledRunnerRequest(
        runner=inputs.runner,
        model=inputs.model,
        input_wav=inputs.target_wav,
        output_srt=inputs.target_srt,
        stdout_output=inputs.target_stdout,
        stderr_output=inputs.target_stderr,
        receipt_output=inputs.target_execution_receipt,
        gpu="0",
        language="zh",
        prompt="，。",
        max_context=-1,
        max_len=0,
    )

    def invoke(argv: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
        invocation_input = Path(argv[argv.index("-f") + 1])
        invocation_input.with_suffix(".srt").write_text(srt_text, encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, b"stdout", b"")

    execute_memo_bundled_runner(request, invoke=invoke)


def test_gap_repair_exactly_inserts_offset_runner_cues_and_replays(tmp_path: Path) -> None:
    inputs, output, receipt_path = _fixture(tmp_path)
    output_bytes, receipt = repair_memo_vad_gap(inputs)
    output.write_bytes(output_bytes)
    receipt_path.write_bytes(canonical_json_bytes(receipt))

    cues = parse_srt(output.read_text(encoding="utf-8"))
    assert [(cue.start_ms, cue.end_ms, cue.text) for cue in cues] == [
        (0, 1_000, "A"),
        (1_000, 2_000, "BC"),
        (2_500, 3_500, "X"),
        (3_500, 5_500, "台場"),
        (6_000, 7_000, "D"),
    ]
    assert [item.text for item in receipt.inserted_cues] == ["X", "台場"]
    assert receipt.output_sha256 == hash_file(output)
    assert (
        load_verified_memo_vad_gap_repair(
            inputs=inputs, composite_source=output, receipt_path=receipt_path
        )
        == receipt
    )


@pytest.mark.parametrize(
    "change",
    [
        "normalized_audio",
        "normalized_handoff",
        "raw_source",
        "parent_repaired_source",
        "parent_repair_receipt",
        "target_wav",
        "target_srt",
        "target_stdout",
        "target_stderr",
        "target_execution_receipt",
        "runner",
        "model",
        "output",
    ],
)
def test_gap_repair_replay_rejects_any_artifact_drift(tmp_path: Path, change: str) -> None:
    inputs, output, receipt_path = _fixture(tmp_path)
    output_bytes, receipt = repair_memo_vad_gap(inputs)
    output.write_bytes(output_bytes)
    receipt_path.write_bytes(canonical_json_bytes(receipt))
    target = output if change == "output" else Path(getattr(inputs, change))
    target.write_bytes(target.read_bytes() + b"drift")

    with pytest.raises((AdapterInputError, AdapterIntegrityError)):
        load_verified_memo_vad_gap_repair(
            inputs=inputs, composite_source=output, receipt_path=receipt_path
        )


@pytest.mark.parametrize(
    ("target_srt", "gap_start", "gap_end", "error"),
    [
        (
            "1\n00:00:00,000 --> 00:00:03,001\nX\n",
            2_500,
            5_500,
            "outside declared gap",
        ),
        (
            "1\n00:00:00,000 --> 00:00:03,000\nX\n",
            1_500,
            4_500,
            "overlaps parent",
        ),
    ],
)
def test_gap_repair_rejects_outside_or_overlapping_insertion(
    tmp_path: Path, target_srt: str, gap_start: int, gap_end: int, error: str
) -> None:
    inputs, _, _ = _fixture(tmp_path)
    _replace_execution_srt(inputs, target_srt)
    invalid = inputs.model_copy(
        update={
            "global_offset_ms": gap_start,
            "declared_gap_start_ms": gap_start,
            "declared_gap_end_ms": gap_end,
        }
    )
    with pytest.raises(ValueError, match=error):
        repair_memo_vad_gap(invalid)


def test_gap_repair_receipt_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    inputs, output, receipt_path = _fixture(tmp_path)
    output_bytes, receipt = repair_memo_vad_gap(inputs)
    output.write_bytes(output_bytes)
    payload = canonical_json_bytes(receipt)
    receipt_path.write_bytes(payload.replace(b'{"contract":', b'{"contract":"x","contract":', 1))
    with pytest.raises(AdapterInputError, match="duplicate JSON key"):
        load_verified_memo_vad_gap_repair(
            inputs=inputs, composite_source=output, receipt_path=receipt_path
        )


def test_gap_repair_cli_flows_through_recognition_and_cue_evidence(tmp_path: Path) -> None:
    inputs, output, receipt_path = _fixture(tmp_path)
    handoff = tmp_path / "normalized-handoff.json"
    evidence_cli.main(
        [
            "seal-normalized",
            "--audio",
            str(inputs.normalized_audio),
            "--output",
            str(handoff),
            "--accepted-at",
            "2026-08-19T10:00:00+08:00",
        ]
    )
    inputs = inputs.model_copy(update={"normalized_handoff": handoff})
    repair_args = [
        "--normalized-audio",
        str(inputs.normalized_audio),
        "--normalized-manifest",
        str(inputs.normalized_handoff),
        "--raw-source-export",
        str(inputs.raw_source),
        "--parent-repaired-source",
        str(inputs.parent_repaired_source),
        "--parent-repair-receipt",
        str(inputs.parent_repair_receipt),
        "--vad-gap-target-wav",
        str(inputs.target_wav),
        "--vad-gap-target-srt",
        str(inputs.target_srt),
        "--vad-gap-target-stdout",
        str(inputs.target_stdout),
        "--vad-gap-target-stderr",
        str(inputs.target_stderr),
        "--vad-gap-execution-receipt",
        str(inputs.target_execution_receipt),
        "--memo-runner",
        str(inputs.runner),
        "--memo-model",
        str(inputs.model),
    ]
    assert (
        evidence_cli.main(
            [
                "repair-memo-vad-gap",
                *repair_args,
                "--global-offset-ms",
                "2500",
                "--declared-gap-start-ms",
                "2500",
                "--declared-gap-end-ms",
                "5500",
                "--output",
                str(output),
                "--receipt-output",
                str(receipt_path),
            ]
        )
        == 0
    )
    with pytest.raises(FileExistsError, match="must not already exist"):
        evidence_cli.main(
            [
                "repair-memo-vad-gap",
                *repair_args,
                "--global-offset-ms",
                "2500",
                "--declared-gap-start-ms",
                "2500",
                "--declared-gap-end-ms",
                "5500",
                "--output",
                str(output),
                "--receipt-output",
                str(receipt_path),
            ]
        )

    lineage_args = [
        *repair_args,
        "--repair-receipt",
        str(receipt_path),
    ]
    original_execution_args = _original_execution_args(inputs, tmp_path)
    review = tmp_path / "recognition-review.json"
    assert (
        evidence_cli.main(
            [
                "prepare-recognition",
                *lineage_args,
                *original_execution_args,
                "--source-export",
                str(output),
                "--source-export-kind",
                "memo_srt",
                "--memo-version",
                "bundled-runner",
                "--language",
                "zh",
                "--prompt",
                "fixture",
                "--output",
                str(review),
            ]
        )
        == 0
    )
    recognition_receipt = tmp_path / "recognition-acceptance.json"
    recognition_manifest = tmp_path / "recognition.json"
    assert (
        evidence_cli.main(
            [
                "accept-recognition",
                *lineage_args,
                "--review",
                str(review),
                "--source-export",
                str(output),
                *_recognition_quorum_args(tmp_path, review),
                "--accepted-at",
                "2026-08-19T10:10:00+08:00",
                "--receipt-output",
                str(recognition_receipt),
                "--manifest-output",
                str(recognition_manifest),
            ]
        )
        == 0
    )
    cue_review = tmp_path / "cue-review.json"
    assert (
        evidence_cli.main(
            [
                "prepare-cues",
                *lineage_args,
                "--recognition-manifest",
                str(recognition_manifest),
                "--source-export",
                str(output),
                "--output",
                str(cue_review),
            ]
        )
        == 0
    )
    cue_receipt = tmp_path / "cue-acceptance.json"
    assert (
        evidence_cli.main(
            [
                "accept-cues",
                *lineage_args,
                "--review",
                str(cue_review),
                "--recognition-manifest",
                str(recognition_manifest),
                "--source-export",
                str(output),
                *_cue_quorum_args(tmp_path, cue_review, recognition_manifest),
                "--accepted-at",
                "2026-08-19T10:20:00+08:00",
                "--receipt-output",
                str(cue_receipt),
            ]
        )
        == 0
    )
