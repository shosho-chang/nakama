"""Execute Memo's bundled Whisper runner and seal one observed execution."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .hashing import canonical_json_bytes, measure_regular_file, sha256_bytes
from .memo_projection import parse_srt
from .ports import AdapterInputError, AdapterIntegrityError


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class MemoBundledRunnerRequest(_StrictModel):
    runner: Path
    model: Path
    input_wav: Path
    output_srt: Path
    stdout_output: Path
    stderr_output: Path
    receipt_output: Path
    gpu: str
    language: str
    prompt: str
    max_context: int
    max_len: int = Field(ge=0)

    @model_validator(mode="after")
    def _valid_request(self) -> "MemoBundledRunnerRequest":
        outputs = (
            self.output_srt,
            self.stdout_output,
            self.stderr_output,
            self.receipt_output,
        )
        if len(set(outputs)) != len(outputs):
            raise ValueError("Memo bundled runner outputs must be distinct")
        if not self.gpu.strip() or not self.language.strip():
            raise ValueError("Memo bundled runner GPU and language are required")
        if self.model.name != "ggml-large-v2.bin":
            raise ValueError("Memo bundled runner requires ggml-large-v2.bin")
        return self


class MemoBundledRunnerExecutionReceiptV1(_StrictModel):
    schema_version: Literal[1] = 1
    contract: Literal["memo-bundled-runner-execution-v1"] = "memo-bundled-runner-execution-v1"
    argv: tuple[str, ...]
    runner_path: str
    runner_sha256: str
    runner_size_bytes: int = Field(gt=0)
    model_path: str
    model_sha256: str
    model_size_bytes: int = Field(gt=0)
    input_wav_path: str
    input_wav_sha256: str
    input_wav_size_bytes: int = Field(gt=0)
    invocation_input_path: str
    gpu: str
    language: str
    prompt: str
    max_context: int
    max_len: int = Field(ge=0)
    started_at: datetime
    completed_at: datetime
    exit_code: Literal[0] = 0
    stdout_sha256: str
    stdout_size_bytes: int = Field(ge=0)
    stderr_sha256: str
    stderr_size_bytes: int = Field(ge=0)
    output_srt_path: str
    output_srt_sha256: str
    output_srt_size_bytes: int = Field(gt=0)

    @model_validator(mode="after")
    def _valid_receipt(self) -> "MemoBundledRunnerExecutionReceiptV1":
        digests = (
            self.runner_sha256,
            self.model_sha256,
            self.input_wav_sha256,
            self.stdout_sha256,
            self.stderr_sha256,
            self.output_srt_sha256,
        )
        if any(
            len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
            for value in digests
        ):
            raise ValueError("Memo bundled runner digest must be lowercase SHA-256")
        if self.completed_at < self.started_at:
            raise ValueError("Memo bundled runner completion precedes start")
        expected = (
            self.runner_path,
            "-m",
            self.model_path,
            "-l",
            self.language,
            "--prompt",
            self.prompt,
            "--no-colors",
            "--use-gpu",
            self.gpu,
            "--output-srt",
            "--max-context",
            str(self.max_context),
            "--max-len",
            str(self.max_len),
            "-f",
            self.invocation_input_path,
        )
        if self.argv != expected:
            raise ValueError("Memo bundled runner argv differs from declared invocation")
        return self


Invoker = Callable[[tuple[str, ...]], subprocess.CompletedProcess[bytes]]


def _default_invoke(argv: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(argv, capture_output=True, check=False)  # noqa: S603


def _atomic_write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _publish_execution_outputs(
    outputs: tuple[tuple[Path, bytes], ...],
) -> None:
    if any(path.exists() for path, _ in outputs):
        raise FileExistsError("Memo bundled runner outputs must not already exist")
    created: list[Path] = []
    try:
        for path, payload in outputs:
            _atomic_write_new(path, payload)
            created.append(path)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise


def _build_argv(request: MemoBundledRunnerRequest, *, invocation_input: Path) -> tuple[str, ...]:
    return (
        str(request.runner.resolve()),
        "-m",
        str(request.model.resolve()),
        "-l",
        request.language,
        "--prompt",
        request.prompt,
        "--no-colors",
        "--use-gpu",
        request.gpu,
        "--output-srt",
        "--max-context",
        str(request.max_context),
        "--max-len",
        str(request.max_len),
        "-f",
        str(invocation_input.resolve()),
    )


def execute_memo_bundled_runner(
    request: MemoBundledRunnerRequest, *, invoke: Invoker | None = None
) -> MemoBundledRunnerExecutionReceiptV1:
    """Run once, then atomically publish exact output/log bytes and its receipt."""

    outputs = (
        request.output_srt,
        request.stdout_output,
        request.stderr_output,
        request.receipt_output,
    )
    if any(path.exists() for path in outputs):
        raise FileExistsError("Memo bundled runner outputs must not already exist")
    runner_hash, runner_size = measure_regular_file(request.runner)
    model_hash, model_size = measure_regular_file(request.model)
    input_hash, input_size = measure_regular_file(request.input_wav)
    invoker = invoke or _default_invoke
    request.output_srt.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="memo-bundled-runner-", dir=request.output_srt.parent
    ) as directory:
        invocation_input = Path(directory) / request.input_wav.name
        try:
            os.link(request.input_wav, invocation_input)
        except OSError:
            shutil.copyfile(request.input_wav, invocation_input)
        if measure_regular_file(invocation_input) != (input_hash, input_size):
            raise AdapterIntegrityError("Memo bundled runner staging input drifted")
        argv = _build_argv(request, invocation_input=invocation_input)
        started_at = datetime.now(timezone.utc)
        completed = invoker(argv)
        completed_at = datetime.now(timezone.utc)
        if completed.returncode != 0:
            raise RuntimeError(f"Memo bundled runner failed with exit code {completed.returncode}")
        invocation_output = invocation_input.with_suffix(".srt")
        try:
            output_bytes = invocation_output.read_bytes()
            # Zero-duration cues are a documented, repairable Memo output condition whose
            # only sanctioned handling is the `repair-memo-srt` branch, and that branch
            # consumes this raw export. Rejecting it here destroys its own input, so the
            # publish gate admits `end == start` and leaves the decision downstream.
            # Negative duration, overlap, empty text and malformed timing still fail closed.
            parse_srt(output_bytes.decode("utf-8-sig"), allow_zero_duration=True)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise ValueError(f"Memo bundled runner produced invalid SRT: {exc}") from exc

    stdout = bytes(completed.stdout)
    stderr = bytes(completed.stderr)
    output_hash, output_size = _measure_bytes(output_bytes)
    stdout_hash, stdout_size = _measure_bytes(stdout)
    stderr_hash, stderr_size = _measure_bytes(stderr)
    receipt = MemoBundledRunnerExecutionReceiptV1(
        argv=argv,
        runner_path=str(request.runner.resolve()),
        runner_sha256=runner_hash,
        runner_size_bytes=runner_size,
        model_path=str(request.model.resolve()),
        model_sha256=model_hash,
        model_size_bytes=model_size,
        input_wav_path=str(request.input_wav.resolve()),
        input_wav_sha256=input_hash,
        input_wav_size_bytes=input_size,
        invocation_input_path=argv[-1],
        gpu=request.gpu,
        language=request.language,
        prompt=request.prompt,
        max_context=request.max_context,
        max_len=request.max_len,
        started_at=started_at,
        completed_at=completed_at,
        stdout_sha256=stdout_hash,
        stdout_size_bytes=stdout_size,
        stderr_sha256=stderr_hash,
        stderr_size_bytes=stderr_size,
        output_srt_path=str(request.output_srt.resolve()),
        output_srt_sha256=output_hash,
        output_srt_size_bytes=output_size,
    )
    receipt_bytes = canonical_json_bytes(receipt)
    _publish_execution_outputs(
        (
            (request.output_srt, output_bytes),
            (request.stdout_output, stdout),
            (request.stderr_output, stderr),
            (request.receipt_output, receipt_bytes),
        )
    )
    return receipt


def _measure_bytes(payload: bytes) -> tuple[str, int]:
    return sha256_bytes(payload), len(payload)


def load_verified_memo_bundled_runner_execution(
    *, request: MemoBundledRunnerRequest
) -> MemoBundledRunnerExecutionReceiptV1:
    """Verify observed artifacts without rerunning nondeterministic GPU inference."""

    try:
        receipt_bytes = request.receipt_output.read_bytes()
        json.loads(receipt_bytes, object_pairs_hook=_reject_duplicate_keys)
        receipt = MemoBundledRunnerExecutionReceiptV1.model_validate_json(
            receipt_bytes, strict=True
        )
        runner = measure_regular_file(request.runner)
        model = measure_regular_file(request.model)
        source = measure_regular_file(request.input_wav)
        output = measure_regular_file(request.output_srt)
        stdout = measure_regular_file(request.stdout_output)
        stderr = measure_regular_file(request.stderr_output)
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise AdapterInputError(f"invalid Memo bundled runner execution lineage: {exc}") from exc
    if canonical_json_bytes(receipt) != receipt_bytes:
        raise AdapterIntegrityError("Memo bundled runner receipt must be canonical JSON")
    expected_request = (
        str(request.runner.resolve()),
        str(request.model.resolve()),
        str(request.input_wav.resolve()),
        str(request.output_srt.resolve()),
        request.gpu,
        request.language,
        request.prompt,
        request.max_context,
        request.max_len,
    )
    actual_request = (
        receipt.runner_path,
        receipt.model_path,
        receipt.input_wav_path,
        receipt.output_srt_path,
        receipt.gpu,
        receipt.language,
        receipt.prompt,
        receipt.max_context,
        receipt.max_len,
    )
    expected_artifacts = (
        (receipt.runner_sha256, receipt.runner_size_bytes),
        (receipt.model_sha256, receipt.model_size_bytes),
        (receipt.input_wav_sha256, receipt.input_wav_size_bytes),
        (receipt.output_srt_sha256, receipt.output_srt_size_bytes),
        (receipt.stdout_sha256, receipt.stdout_size_bytes),
        (receipt.stderr_sha256, receipt.stderr_size_bytes),
    )
    if expected_request != actual_request or expected_artifacts != (
        runner,
        model,
        source,
        output,
        stdout,
        stderr,
    ):
        raise AdapterIntegrityError("Memo bundled runner execution lineage is stale or tampered")
    return receipt


def load_memo_bundled_runner_execution_receipt(
    path: str | Path,
) -> MemoBundledRunnerExecutionReceiptV1:
    """Load only the canonical declaration before resolving its artifact paths."""

    try:
        payload = Path(path).read_bytes()
        json.loads(payload, object_pairs_hook=_reject_duplicate_keys)
        receipt = MemoBundledRunnerExecutionReceiptV1.model_validate_json(payload, strict=True)
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise AdapterInputError(f"invalid Memo bundled runner execution receipt: {exc}") from exc
    if canonical_json_bytes(receipt) != payload:
        raise AdapterIntegrityError("Memo bundled runner receipt must be canonical JSON")
    return receipt


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


__all__ = [
    "MemoBundledRunnerExecutionReceiptV1",
    "MemoBundledRunnerRequest",
    "execute_memo_bundled_runner",
    "load_memo_bundled_runner_execution_receipt",
    "load_verified_memo_bundled_runner_execution",
]
