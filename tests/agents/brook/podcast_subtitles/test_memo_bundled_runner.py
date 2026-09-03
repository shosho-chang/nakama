from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.hashing import canonical_json_bytes, hash_file
from agents.brook.podcast_subtitles.memo_bundled_runner import (
    MemoBundledRunnerRequest,
    execute_memo_bundled_runner,
    load_verified_memo_bundled_runner_execution,
)
from agents.brook.podcast_subtitles.ports import (
    AdapterInputError,
    AdapterIntegrityError,
)


def _wav(path: Path) -> Path:
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16_000)
        stream.writeframes(b"\0\0" * 1_000)
    return path


def _request(tmp_path: Path) -> MemoBundledRunnerRequest:
    runner = tmp_path / "main.exe"
    runner.write_bytes(b"runner")
    model = tmp_path / "ggml-large-v2.bin"
    model.write_bytes(b"model")
    return MemoBundledRunnerRequest(
        runner=runner,
        model=model,
        input_wav=_wav(tmp_path / "gap.wav"),
        output_srt=tmp_path / "gap.srt",
        stdout_output=tmp_path / "gap.stdout.log",
        stderr_output=tmp_path / "gap.stderr.log",
        receipt_output=tmp_path / "gap.execution.v1.json",
        gpu="0",
        language="zh",
        prompt="，。",
        max_context=-1,
        max_len=0,
    )


def _successful_invoker(srt: bytes):
    def invoke(argv: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
        input_path = Path(argv[argv.index("-f") + 1])
        input_path.with_suffix(".srt").write_bytes(srt)
        return subprocess.CompletedProcess(argv, 0, b"stdout", b"warning")

    return invoke


def test_execution_receipt_binds_actual_invocation_and_output(tmp_path: Path) -> None:
    request = _request(tmp_path)
    receipt = execute_memo_bundled_runner(
        request,
        invoke=_successful_invoker(b"1\n00:00:00,000 --> 00:00:01,000\nA\n"),
    )

    assert receipt.exit_code == 0
    assert receipt.argv[receipt.argv.index("--use-gpu") + 1] == "0"
    assert receipt.argv[receipt.argv.index("--max-context") + 1] == "-1"
    assert receipt.output_srt_sha256 == hash_file(request.output_srt)
    assert receipt.stdout_sha256 == hash_file(request.stdout_output)
    assert receipt.stderr_sha256 == hash_file(request.stderr_output)
    assert load_verified_memo_bundled_runner_execution(request=request) == receipt


@pytest.mark.parametrize(
    "artifact",
    ["runner", "model", "input_wav", "output_srt", "stdout_output", "stderr_output"],
)
def test_execution_receipt_rejects_any_artifact_drift(tmp_path: Path, artifact: str) -> None:
    request = _request(tmp_path)
    execute_memo_bundled_runner(
        request,
        invoke=_successful_invoker(b"1\n00:00:00,000 --> 00:00:01,000\nA\n"),
    )
    path = Path(getattr(request, artifact))
    path.write_bytes(path.read_bytes() + b"drift")
    with pytest.raises((AdapterInputError, AdapterIntegrityError)):
        load_verified_memo_bundled_runner_execution(request=request)


def test_execution_receipt_rejects_argv_or_duplicate_key_tamper(tmp_path: Path) -> None:
    request = _request(tmp_path)
    receipt = execute_memo_bundled_runner(
        request,
        invoke=_successful_invoker(b"1\n00:00:00,000 --> 00:00:01,000\nA\n"),
    )
    request.receipt_output.write_bytes(canonical_json_bytes(receipt).replace(b'"zh"', b'"en"', 1))
    with pytest.raises((AdapterInputError, AdapterIntegrityError)):
        load_verified_memo_bundled_runner_execution(request=request)

    request.receipt_output.write_bytes(
        canonical_json_bytes(receipt).replace(b'{"argv":', b'{"argv":[],"argv":', 1)
    )
    with pytest.raises(AdapterInputError, match="duplicate JSON key"):
        load_verified_memo_bundled_runner_execution(request=request)


def test_failed_execution_produces_no_admissible_artifacts(tmp_path: Path) -> None:
    request = _request(tmp_path)

    def fail(argv: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, 7, b"partial", b"failed")

    with pytest.raises(RuntimeError, match="exit code 7"):
        execute_memo_bundled_runner(request, invoke=fail)
    assert not request.output_srt.exists()
    assert not request.receipt_output.exists()
    assert not request.stdout_output.exists()
    assert not request.stderr_output.exists()


def test_execution_does_not_overwrite_any_output(tmp_path: Path) -> None:
    request = _request(tmp_path)
    request.stdout_output.write_bytes(b"owned")
    with pytest.raises(FileExistsError):
        execute_memo_bundled_runner(
            request,
            invoke=_successful_invoker(b"1\n00:00:00,000 --> 00:00:01,000\nA\n"),
        )


def test_nondeterministic_outputs_have_distinct_valid_execution_identities(
    tmp_path: Path,
) -> None:
    first_dir = tmp_path / "first"
    first_dir.mkdir()
    first = _request(first_dir)
    second_dir = tmp_path / "second"
    second_dir.mkdir()
    second = _request(second_dir)
    first_receipt = execute_memo_bundled_runner(
        first,
        invoke=_successful_invoker(b"1\n00:00:00,000 --> 00:00:01,000\nA\n"),
    )
    second_receipt = execute_memo_bundled_runner(
        second,
        invoke=_successful_invoker(b"1\n00:00:00,000 --> 00:00:01,000\nA this time\n"),
    )
    assert first_receipt.output_srt_sha256 != second_receipt.output_srt_sha256
    assert load_verified_memo_bundled_runner_execution(request=first) == first_receipt
    assert load_verified_memo_bundled_runner_execution(request=second) == second_receipt


# --- publish gate vs. the documented zero-duration repair branch ---------------
# The runbook sequence is `run-memo-bundled -> [zero-duration only: repair-memo-srt]`,
# and `repair-memo-srt` parses the raw export with allow_zero_duration=True. If the
# runner refused to publish that export, the repair branch could never receive its own
# input. These tests pin the seam between the two modules.

_ZERO_DURATION_SRT = (
    b"1\n00:00:00,100 --> 00:00:00,500\nA\n\n"
    b"2\n00:00:00,500 --> 00:00:00,500\nB\n\n"
    b"3\n00:00:00,500 --> 00:00:00,900\nC\n"
)


def test_zero_duration_cue_is_published_for_the_repair_branch(tmp_path: Path) -> None:
    request = _request(tmp_path)

    receipt = execute_memo_bundled_runner(
        request, invoke=_successful_invoker(_ZERO_DURATION_SRT)
    )

    assert request.output_srt.read_bytes() == _ZERO_DURATION_SRT
    assert receipt.output_srt_sha256 == hash_file(request.output_srt)
    assert request.receipt_output.exists()
    assert request.stdout_output.exists()
    assert request.stderr_output.exists()
    assert load_verified_memo_bundled_runner_execution(request=request) == receipt


@pytest.mark.parametrize(
    ("label", "srt"),
    [
        (
            "negative duration",
            b"1\n00:00:00,100 --> 00:00:00,500\nA\n\n"
            b"2\n00:00:00,900 --> 00:00:00,500\nB\n",
        ),
        (
            "overlap",
            b"1\n00:00:00,100 --> 00:00:00,900\nA\n\n"
            b"2\n00:00:00,500 --> 00:00:01,200\nB\n",
        ),
        (
            "empty cue text",
            b"1\n00:00:00,100 --> 00:00:00,500\n\n\n"
            b"2\n00:00:00,500 --> 00:00:00,900\nB\n",
        ),
    ],
)
def test_malformed_timebase_still_fails_closed(
    tmp_path: Path, label: str, srt: bytes
) -> None:
    request = _request(tmp_path)

    with pytest.raises(ValueError, match="produced invalid SRT"):
        execute_memo_bundled_runner(request, invoke=_successful_invoker(srt))
    assert not request.output_srt.exists()
    assert not request.receipt_output.exists()
    assert not request.stdout_output.exists()
    assert not request.stderr_output.exists()
