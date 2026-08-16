"""Independent normalized-audio activity coverage for Podcast Subtitle V2.

The production Adapter observes energy-active intervals by taking the
complement of ``ffmpeg`` ``silencedetect`` output.  It never decodes, predicts,
or writes transcript text.  Energy activity is deliberately conservative:
music or noise can produce a review request, but can never become words.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from shared.schemas.podcast_subtitles_v2 import (
    ArtifactDigest,
    SpeechActivityInterval,
    SpeechCoverageReceipt,
    recognition_evidence_content_hash,
    recognition_evidence_set_hash,
)

from ..hashing import canonical_json_bytes, hash_file, hash_object, sha256_bytes
from ..ports import (
    AdapterInputError,
    AdapterIntegrityError,
    SpeechCoverageRequest,
)

_PRODUCTION_ADAPTER = "ffmpeg-energy-speech-coverage"
_FIXTURE_ADAPTER = "fixture-speech-coverage"
_ADAPTER_VERSION = "1"
_PRODUCTION_METHOD = "ffmpeg_silencedetect_complement_v1"
_FIXTURE_METHOD = "fixture_intervals_v1"
_SILENCE_START_RE = re.compile(rb"silence_start:\s*(-?(?:\d+(?:\.\d*)?|\.\d+))")
_SILENCE_END_RE = re.compile(rb"silence_end:\s*(-?(?:\d+(?:\.\d*)?|\.\d+))")


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Bytes-preserving process result used by the injectable command seam."""

    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""

    def __post_init__(self) -> None:
        if not isinstance(self.returncode, int) or isinstance(self.returncode, bool):
            raise TypeError("command returncode must be an integer")
        if not isinstance(self.stdout, bytes) or not isinstance(self.stderr, bytes):
            raise TypeError("command output must be bytes")


CommandRunner = Callable[[tuple[str, ...]], CommandResult]


def _default_runner(command: tuple[str, ...]) -> CommandResult:
    completed = subprocess.run(command, capture_output=True, check=False)
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _bounded_failure(exc: BaseException) -> str:
    message = " ".join(str(exc).split())[:1024]
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def _run_captured(runner: CommandRunner, command: tuple[str, ...]) -> dict[str, object]:
    try:
        result = runner(command)
        if not isinstance(result, CommandResult):
            raise TypeError("runner must return CommandResult")
    except Exception as exc:  # Adapter failure is persisted and reviewed, not hidden.
        return {
            "returncode": None,
            "stdout_b64": "",
            "stderr_b64": "",
            "launch_error": _bounded_failure(exc),
        }
    return {
        "returncode": result.returncode,
        "stdout_b64": base64.b64encode(result.stdout).decode("ascii"),
        "stderr_b64": base64.b64encode(result.stderr).decode("ascii"),
        "launch_error": None,
    }


def _decode_process_bytes(process: object, field: str) -> bytes:
    if not isinstance(process, Mapping) or set(process) != {
        "returncode",
        "stdout_b64",
        "stderr_b64",
        "launch_error",
    }:
        raise AdapterIntegrityError("speech coverage process record has an invalid shape")
    value = process.get(field)
    if not isinstance(value, str):
        raise AdapterIntegrityError("speech coverage process output is not base64 text")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AdapterIntegrityError("speech coverage process output is invalid base64") from exc


def _strict_payload(raw_output: bytes) -> dict[str, object]:
    duplicates: list[str] = []

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                duplicates.append(key)
            result[key] = value
        return result

    try:
        payload = json.loads(raw_output, object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterIntegrityError("speech coverage raw output is not valid JSON") from exc
    if duplicates:
        raise AdapterIntegrityError(
            f"speech coverage raw output has duplicate keys: {sorted(set(duplicates))!r}"
        )
    if not isinstance(payload, dict) or canonical_json_bytes(payload) != raw_output:
        raise AdapterIntegrityError("speech coverage raw output is not canonical JSON")
    return payload


def _write_content_addressed(output_dir: Path, payload: Mapping[str, object]) -> ArtifactDigest:
    raw = canonical_json_bytes(payload)
    digest = sha256_bytes(raw)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"speech-coverage-{digest}.json"
    if target.exists():
        if hash_file(target) != digest or target.stat().st_size != len(raw):
            raise AdapterIntegrityError("speech coverage artifact collision or tampering")
    else:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".speech-coverage-{digest}.", suffix=".tmp", dir=output_dir
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return ArtifactDigest(uri=target.resolve().as_uri(), sha256=digest, size_bytes=len(raw))


def _recognition_interval_identity(request: SpeechCoverageRequest) -> str:
    primary = request.recognition_evidence[0]
    return hash_object(
        {
            "canonical_primary_evidence_hash": recognition_evidence_content_hash(primary),
            "intervals": tuple(
                (token.id, token.start_ms, token.end_ms) for token in primary.tokens
            ),
        }
    )


def _merge_intervals(
    intervals: Sequence[tuple[int, int]],
    *,
    start_ms: int = 0,
    end_ms: int | None = None,
) -> tuple[tuple[int, int], ...]:
    bounded: list[tuple[int, int]] = []
    for start, end in intervals:
        if end_ms is not None:
            start = min(max(start, start_ms), end_ms)
            end = min(max(end, start_ms), end_ms)
        if end > start:
            bounded.append((start, end))
    bounded.sort()
    merged: list[list[int]] = []
    for start, end in bounded:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return tuple((start, end) for start, end in merged)


def _parse_silence(stderr: bytes, duration_ms: int) -> tuple[tuple[int, int], ...]:
    events: list[tuple[int, bytes]] = []
    events.extend(
        (match.start(), b"start:" + match.group(1)) for match in _SILENCE_START_RE.finditer(stderr)
    )
    events.extend(
        (match.start(), b"end:" + match.group(1)) for match in _SILENCE_END_RE.finditer(stderr)
    )
    events.sort()
    pending: int | None = None
    silence: list[tuple[int, int]] = []
    try:
        for _, event in events:
            kind, raw_seconds = event.split(b":", 1)
            milliseconds = round(float(raw_seconds) * 1000)
            if kind == b"start":
                if pending is not None:
                    raise ValueError("nested silence_start")
                pending = milliseconds
            else:
                if pending is None:
                    raise ValueError("silence_end without silence_start")
                if milliseconds <= pending:
                    raise ValueError("non-positive silence interval")
                silence.append((pending, milliseconds))
                pending = None
    except (OverflowError, ValueError) as exc:
        raise AdapterInputError("ffmpeg silencedetect output is inconsistent") from exc
    if pending is not None:
        silence.append((pending, duration_ms))
    return _merge_intervals(silence, end_ms=duration_ms)


def _activity_from_silence(
    silence: Sequence[tuple[int, int]],
    *,
    duration_ms: int,
    minimum_activity_ms: int,
) -> tuple[tuple[int, int], ...]:
    activity: list[tuple[int, int]] = []
    cursor = 0
    for start, end in silence:
        if start > cursor and start - cursor >= minimum_activity_ms:
            activity.append((cursor, start))
        cursor = max(cursor, end)
    if duration_ms > cursor and duration_ms - cursor >= minimum_activity_ms:
        activity.append((cursor, duration_ms))
    return tuple(activity)


def _uncovered_activity(
    activity: Sequence[tuple[int, int]],
    recognition: Sequence[tuple[int, int]],
    *,
    duration_ms: int,
    tolerance_ms: int,
    minimum_uncovered_ms: int,
) -> tuple[tuple[int, int], ...]:
    expanded = _merge_intervals(
        (
            (max(0, start - tolerance_ms), min(duration_ms, end + tolerance_ms))
            for start, end in recognition
        ),
        end_ms=duration_ms,
    )
    uncovered: list[tuple[int, int]] = []
    for active_start, active_end in activity:
        cursor = active_start
        for covered_start, covered_end in expanded:
            if covered_end <= cursor:
                continue
            if covered_start >= active_end:
                break
            if covered_start > cursor:
                end = min(covered_start, active_end)
                if end - cursor >= minimum_uncovered_ms:
                    uncovered.append((cursor, end))
            cursor = max(cursor, min(covered_end, active_end))
            if cursor >= active_end:
                break
        if active_end - cursor >= minimum_uncovered_ms:
            uncovered.append((cursor, active_end))
    return tuple(uncovered)


def _interval_contracts(
    intervals: Sequence[tuple[int, int]],
    *,
    normalized_audio_hash: str,
    kind: str,
    method: str,
) -> tuple[SpeechActivityInterval, ...]:
    return tuple(
        SpeechActivityInterval(
            id="speech-activity-"
            + hash_object(
                {
                    "audio": normalized_audio_hash,
                    "kind": kind,
                    "method": method,
                    "start_ms": start,
                    "end_ms": end,
                }
            ),
            start_ms=start,
            end_ms=end,
        )
        for start, end in intervals
    )


def _request_identity(request: SpeechCoverageRequest) -> dict[str, object]:
    return {
        "episode_id": request.episode_id,
        "invocation_id": request.invocation_id,
        "normalized_audio_hash": request.expected_normalized_audio_hash,
        "normalized_audio_size_bytes": request.expected_normalized_audio_size_bytes,
        "normalized_audio_duration_ms": request.normalized_audio_duration_ms,
        "recognition_evidence_hash": recognition_evidence_set_hash(request.recognition_evidence),
        "canonical_primary_evidence_hash": recognition_evidence_content_hash(
            request.recognition_evidence[0]
        ),
        "recognition_interval_hash": _recognition_interval_identity(request),
    }


def _assert_audio(request: SpeechCoverageRequest) -> None:
    audio = request.normalized_audio
    if not audio.is_file():
        raise AdapterInputError(f"normalized audio is not a file: {audio}")
    if (
        hash_file(audio) != request.expected_normalized_audio_hash
        or audio.stat().st_size != request.expected_normalized_audio_size_bytes
    ):
        raise AdapterIntegrityError("normalized audio bytes differ from coverage request")


class FFmpegSpeechCoverageAnalyzer:
    """Conservatively compare ffmpeg energy activity with Recognition spans."""

    def __init__(
        self,
        *,
        noise_db: float = -38.0,
        minimum_silence_ms: int = 350,
        minimum_activity_ms: int = 120,
        coverage_tolerance_ms: int = 180,
        minimum_uncovered_ms: int = 350,
        duration_tolerance_ms: int = 50,
        ffmpeg_executable: str = "ffmpeg",
        ffprobe_executable: str = "ffprobe",
        runner: CommandRunner | None = None,
        runtime_identity: bytes | None = None,
    ) -> None:
        if not -100.0 <= noise_db <= 0.0:
            raise ValueError("noise_db must be between -100 and 0")
        for label, value in (
            ("minimum_silence_ms", minimum_silence_ms),
            ("minimum_activity_ms", minimum_activity_ms),
            ("minimum_uncovered_ms", minimum_uncovered_ms),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{label} must be a positive integer")
        for label, value in (
            ("coverage_tolerance_ms", coverage_tolerance_ms),
            ("duration_tolerance_ms", duration_tolerance_ms),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{label} must be a non-negative integer")
        if not ffmpeg_executable.strip() or not ffprobe_executable.strip():
            raise ValueError("ffmpeg/ffprobe executable names must not be blank")
        if runner is not None and runtime_identity is None:
            raise ValueError("custom command runner requires explicit runtime_identity bytes")
        if runtime_identity is not None and not isinstance(runtime_identity, bytes):
            raise TypeError("runtime_identity must be bytes")
        self._noise_db = float(noise_db)
        self._minimum_silence_ms = minimum_silence_ms
        self._minimum_activity_ms = minimum_activity_ms
        self._coverage_tolerance_ms = coverage_tolerance_ms
        self._minimum_uncovered_ms = minimum_uncovered_ms
        self._duration_tolerance_ms = duration_tolerance_ms
        self._ffmpeg = ffmpeg_executable
        self._ffprobe = ffprobe_executable
        self._runner = runner or _default_runner
        self._runtime_identity = runtime_identity
        self._runtime_probe_cache: tuple[str, dict[str, object]] | None = None

    @property
    def adapter_name(self) -> str:
        return _PRODUCTION_ADAPTER

    @property
    def adapter_version(self) -> str:
        return _ADAPTER_VERSION

    @property
    def adapter_config_hash(self) -> str:
        return hash_object(
            {
                "adapter": self.adapter_name,
                "adapter_version": self.adapter_version,
                "method": _PRODUCTION_METHOD,
                "noise_db": self._noise_db,
                "minimum_silence_ms": self._minimum_silence_ms,
                "minimum_activity_ms": self._minimum_activity_ms,
                "coverage_tolerance_ms": self._coverage_tolerance_ms,
                "minimum_uncovered_ms": self._minimum_uncovered_ms,
                "duration_tolerance_ms": self._duration_tolerance_ms,
                "ffmpeg_executable": self._ffmpeg,
                "ffprobe_executable": self._ffprobe,
            }
        )

    @property
    def adapter_code_hash(self) -> str:
        return hash_file(Path(__file__))

    def _runtime_probe(self) -> tuple[str, dict[str, object]]:
        if self._runtime_probe_cache is not None:
            return self._runtime_probe_cache
        if self._runtime_identity is not None:
            payload: dict[str, object] = {
                "mode": "declared_test_runtime",
                "identity_b64": base64.b64encode(self._runtime_identity).decode("ascii"),
            }
        else:
            payload = {
                "mode": "probed_runtime",
                "ffmpeg": _run_captured(self._runner, (self._ffmpeg, "-version")),
                "ffprobe": _run_captured(self._runner, (self._ffprobe, "-version")),
            }
        self._runtime_probe_cache = (hash_object(payload), payload)
        return self._runtime_probe_cache

    @property
    def adapter_runtime_hash(self) -> str:
        return self._runtime_probe()[0]

    def _base_payload(self, request: SpeechCoverageRequest) -> dict[str, object]:
        runtime_hash, runtime_probe = self._runtime_probe()
        return {
            "schema_version": 1,
            "adapter": self.adapter_name,
            "adapter_version": self.adapter_version,
            "config_hash": self.adapter_config_hash,
            "code_hash": self.adapter_code_hash,
            "runtime_hash": runtime_hash,
            "runtime_probe": runtime_probe,
            "method": _PRODUCTION_METHOD,
            "request": _request_identity(request),
            "policy": {
                "noise_db": self._noise_db,
                "minimum_silence_ms": self._minimum_silence_ms,
                "minimum_activity_ms": self._minimum_activity_ms,
                "coverage_tolerance_ms": self._coverage_tolerance_ms,
                "minimum_uncovered_ms": self._minimum_uncovered_ms,
                "duration_tolerance_ms": self._duration_tolerance_ms,
            },
        }

    def analyze(self, request: SpeechCoverageRequest) -> SpeechCoverageReceipt:
        _assert_audio(request)
        probe_command = (
            self._ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(request.normalized_audio),
        )
        analyze_command = (
            self._ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-nostats",
            "-i",
            str(request.normalized_audio),
            "-map",
            "0:a:0",
            "-vn",
            "-af",
            (f"silencedetect=noise={self._noise_db:.3f}dB:d={self._minimum_silence_ms / 1000:.3f}"),
            "-f",
            "null",
            "-",
        )
        payload = {
            **self._base_payload(request),
            "probe": _run_captured(self._runner, probe_command),
            "analysis": _run_captured(self._runner, analyze_command),
        }
        _assert_audio(request)
        artifact = _write_content_addressed(request.raw_output_dir, payload)
        return self._derive_receipt(request, artifact=artifact, payload=payload)

    def _derive_receipt(
        self,
        request: SpeechCoverageRequest,
        *,
        artifact: ArtifactDigest,
        payload: Mapping[str, object],
    ) -> SpeechCoverageReceipt:
        common = self._receipt_common(request, artifact)
        try:
            probe = payload["probe"]
            analysis = payload["analysis"]
            if not isinstance(probe, Mapping) or not isinstance(analysis, Mapping):
                raise AdapterIntegrityError("speech coverage process payload is missing")
            for label, process in (("ffprobe", probe), ("ffmpeg", analysis)):
                launch_error = process.get("launch_error")
                returncode = process.get("returncode")
                if launch_error is not None:
                    if not isinstance(launch_error, str) or not launch_error:
                        raise AdapterIntegrityError("speech coverage launch error is malformed")
                    raise AdapterInputError(f"{label} unavailable: {launch_error}")
                if not isinstance(returncode, int) or isinstance(returncode, bool):
                    raise AdapterIntegrityError("speech coverage returncode is malformed")
                if returncode != 0:
                    raise AdapterInputError(f"{label} exited with status {returncode}")

            probe_stdout = _decode_process_bytes(probe, "stdout_b64")
            try:
                probe_payload = json.loads(probe_stdout)
                duration_ms = round(float(probe_payload["format"]["duration"]) * 1000)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise AdapterInputError("ffprobe returned no valid audio duration") from exc
            duration_delta = abs(duration_ms - request.normalized_audio_duration_ms)
            if duration_delta > self._duration_tolerance_ms:
                raise AdapterInputError(
                    "ffprobe duration differs from accepted normalized-audio clock"
                )
            stderr = _decode_process_bytes(analysis, "stderr_b64")
            silence = _parse_silence(stderr, request.normalized_audio_duration_ms)
            activity = _activity_from_silence(
                silence,
                duration_ms=request.normalized_audio_duration_ms,
                minimum_activity_ms=self._minimum_activity_ms,
            )
            # Canonical reconciliation is primary-led.  A corroborating ASR
            # token cannot hide speech omitted from that canonical stream.
            recognition = tuple(
                (token.start_ms, token.end_ms) for token in request.recognition_evidence[0].tokens
            )
            uncovered = _uncovered_activity(
                activity,
                recognition,
                duration_ms=request.normalized_audio_duration_ms,
                tolerance_ms=self._coverage_tolerance_ms,
                minimum_uncovered_ms=self._minimum_uncovered_ms,
            )
        except AdapterInputError as exc:
            return SpeechCoverageReceipt(
                **common,
                status="failed",
                passed=False,
                failure_reason=str(exc),
            )
        return SpeechCoverageReceipt(
            **common,
            status="completed",
            activity_intervals=_interval_contracts(
                activity,
                normalized_audio_hash=request.expected_normalized_audio_hash,
                kind="activity",
                method=_PRODUCTION_METHOD,
            ),
            uncovered_intervals=_interval_contracts(
                uncovered,
                normalized_audio_hash=request.expected_normalized_audio_hash,
                kind="uncovered",
                method=_PRODUCTION_METHOD,
            ),
            passed=not uncovered,
        )

    def _receipt_common(
        self, request: SpeechCoverageRequest, artifact: ArtifactDigest
    ) -> dict[str, object]:
        identity = _request_identity(request)
        return {
            "episode_id": request.episode_id,
            "invocation_id": request.invocation_id,
            "analyzer": self.adapter_name,
            "analyzer_version": self.adapter_version,
            "analyzer_config_hash": self.adapter_config_hash,
            "analyzer_code_hash": self.adapter_code_hash,
            "analyzer_runtime_hash": self.adapter_runtime_hash,
            "method": _PRODUCTION_METHOD,
            "normalized_audio_hash": request.expected_normalized_audio_hash,
            "normalized_audio_size_bytes": request.expected_normalized_audio_size_bytes,
            "normalized_audio_duration_ms": request.normalized_audio_duration_ms,
            "recognition_evidence_hash": identity["recognition_evidence_hash"],
            "recognition_interval_hash": identity["recognition_interval_hash"],
            "coverage_tolerance_ms": self._coverage_tolerance_ms,
            "minimum_uncovered_ms": self._minimum_uncovered_ms,
            "raw_output": artifact,
            "raw_output_hash": artifact.sha256,
        }

    def verify(
        self,
        receipt: SpeechCoverageReceipt,
        *,
        request: SpeechCoverageRequest,
        raw_output: bytes,
    ) -> SpeechCoverageReceipt:
        _assert_audio(request)
        if (
            sha256_bytes(raw_output) != receipt.raw_output.sha256
            or len(raw_output) != receipt.raw_output.size_bytes
        ):
            raise AdapterIntegrityError("stored speech coverage bytes differ from receipt")
        payload = _strict_payload(raw_output)
        required = {
            "schema_version",
            "adapter",
            "adapter_version",
            "config_hash",
            "code_hash",
            "runtime_hash",
            "runtime_probe",
            "method",
            "request",
            "policy",
            "probe",
            "analysis",
        }
        if set(payload) != required:
            raise AdapterIntegrityError("speech coverage raw output contract is incomplete")
        expected_base = self._base_payload(request)
        if any(payload.get(key) != value for key, value in expected_base.items()):
            raise AdapterIntegrityError(
                "speech coverage raw output crossed request or Adapter identity"
            )
        rebuilt = self._derive_receipt(
            request,
            artifact=receipt.raw_output,
            payload=payload,
        )
        if rebuilt != receipt:
            raise AdapterIntegrityError("speech coverage receipt is not reproducible")
        return rebuilt


class FixtureSpeechCoverageAnalyzer:
    """Deterministic fixture that still exercises receipt/restart verification."""

    def __init__(
        self,
        activity_intervals: Sequence[tuple[int, int]] | None = None,
        *,
        coverage_tolerance_ms: int = 0,
        minimum_uncovered_ms: int = 1,
        failure_reason: str | None = None,
    ) -> None:
        self._activity = None if activity_intervals is None else tuple(activity_intervals)
        self._coverage_tolerance_ms = coverage_tolerance_ms
        self._minimum_uncovered_ms = minimum_uncovered_ms
        self._failure_reason = failure_reason
        if coverage_tolerance_ms < 0 or minimum_uncovered_ms <= 0:
            raise ValueError("fixture speech coverage policy is invalid")
        if failure_reason is not None and not failure_reason.strip():
            raise ValueError("fixture failure reason must not be blank")

    @property
    def adapter_name(self) -> str:
        return _FIXTURE_ADAPTER

    @property
    def adapter_version(self) -> str:
        return _ADAPTER_VERSION

    @property
    def adapter_config_hash(self) -> str:
        return hash_object(
            {
                "adapter": self.adapter_name,
                "version": self.adapter_version,
                "method": _FIXTURE_METHOD,
                "activity_intervals": self._activity,
                "activity_source": (
                    "canonical_primary_recognition_intervals_fixture_only"
                    if self._activity is None
                    else "declared_fixture_intervals"
                ),
                "coverage_tolerance_ms": self._coverage_tolerance_ms,
                "minimum_uncovered_ms": self._minimum_uncovered_ms,
                "failure_reason": self._failure_reason,
            }
        )

    @property
    def adapter_code_hash(self) -> str:
        return hash_file(Path(__file__))

    @property
    def adapter_runtime_hash(self) -> str:
        return hash_object({"runtime": "pure-python-fixture-v1"})

    def _resolved_activity(self, request: SpeechCoverageRequest) -> tuple[tuple[int, int], ...]:
        return (
            tuple(
                (token.start_ms, token.end_ms) for token in request.recognition_evidence[0].tokens
            )
            if self._activity is None
            else self._activity
        )

    def _payload(self, request: SpeechCoverageRequest) -> dict[str, object]:
        activity = self._resolved_activity(request)
        return {
            "schema_version": 1,
            "adapter": self.adapter_name,
            "adapter_version": self.adapter_version,
            "config_hash": self.adapter_config_hash,
            "code_hash": self.adapter_code_hash,
            "runtime_hash": self.adapter_runtime_hash,
            "method": _FIXTURE_METHOD,
            "request": _request_identity(request),
            "activity_intervals": [list(interval) for interval in activity],
            "coverage_tolerance_ms": self._coverage_tolerance_ms,
            "minimum_uncovered_ms": self._minimum_uncovered_ms,
            "failure_reason": self._failure_reason,
        }

    def analyze(self, request: SpeechCoverageRequest) -> SpeechCoverageReceipt:
        _assert_audio(request)
        payload = self._payload(request)
        artifact = _write_content_addressed(request.raw_output_dir, payload)
        return self._derive(request, artifact)

    def _derive(
        self, request: SpeechCoverageRequest, artifact: ArtifactDigest
    ) -> SpeechCoverageReceipt:
        identity = _request_identity(request)
        common = {
            "episode_id": request.episode_id,
            "invocation_id": request.invocation_id,
            "analyzer": self.adapter_name,
            "analyzer_version": self.adapter_version,
            "analyzer_config_hash": self.adapter_config_hash,
            "analyzer_code_hash": self.adapter_code_hash,
            "analyzer_runtime_hash": self.adapter_runtime_hash,
            "method": _FIXTURE_METHOD,
            "normalized_audio_hash": request.expected_normalized_audio_hash,
            "normalized_audio_size_bytes": request.expected_normalized_audio_size_bytes,
            "normalized_audio_duration_ms": request.normalized_audio_duration_ms,
            "recognition_evidence_hash": identity["recognition_evidence_hash"],
            "recognition_interval_hash": identity["recognition_interval_hash"],
            "coverage_tolerance_ms": self._coverage_tolerance_ms,
            "minimum_uncovered_ms": self._minimum_uncovered_ms,
            "raw_output": artifact,
            "raw_output_hash": artifact.sha256,
        }
        if self._failure_reason is not None:
            return SpeechCoverageReceipt(
                **common,
                status="failed",
                failure_reason=self._failure_reason,
                passed=False,
            )
        activity = _merge_intervals(
            self._resolved_activity(request),
            end_ms=request.normalized_audio_duration_ms,
        )
        recognition = tuple(
            (token.start_ms, token.end_ms) for token in request.recognition_evidence[0].tokens
        )
        uncovered = _uncovered_activity(
            activity,
            recognition,
            duration_ms=request.normalized_audio_duration_ms,
            tolerance_ms=self._coverage_tolerance_ms,
            minimum_uncovered_ms=self._minimum_uncovered_ms,
        )
        return SpeechCoverageReceipt(
            **common,
            status="completed",
            activity_intervals=_interval_contracts(
                activity,
                normalized_audio_hash=request.expected_normalized_audio_hash,
                kind="activity",
                method=_FIXTURE_METHOD,
            ),
            uncovered_intervals=_interval_contracts(
                uncovered,
                normalized_audio_hash=request.expected_normalized_audio_hash,
                kind="uncovered",
                method=_FIXTURE_METHOD,
            ),
            passed=not uncovered,
        )

    def verify(
        self,
        receipt: SpeechCoverageReceipt,
        *,
        request: SpeechCoverageRequest,
        raw_output: bytes,
    ) -> SpeechCoverageReceipt:
        _assert_audio(request)
        if (
            sha256_bytes(raw_output) != receipt.raw_output.sha256
            or len(raw_output) != receipt.raw_output.size_bytes
        ):
            raise AdapterIntegrityError("stored fixture coverage bytes differ from receipt")
        if _strict_payload(raw_output) != self._payload(request):
            raise AdapterIntegrityError("fixture coverage raw output crossed immutable inputs")
        rebuilt = self._derive(request, receipt.raw_output)
        if rebuilt != receipt:
            raise AdapterIntegrityError("fixture coverage receipt is not reproducible")
        return rebuilt


__all__ = [
    "CommandResult",
    "FFmpegSpeechCoverageAnalyzer",
    "FixtureSpeechCoverageAnalyzer",
]
