from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.adapters.speech_coverage import (
    CommandResult,
    FFmpegSpeechCoverageAnalyzer,
    FixtureSpeechCoverageAnalyzer,
)
from agents.brook.podcast_subtitles.hashing import (
    canonical_json_bytes,
    hash_file,
    hash_object,
    sha256_bytes,
)
from agents.brook.podcast_subtitles.ports import (
    AdapterIntegrityError,
    SpeechCoverageRequest,
)
from shared.schemas.podcast_subtitles_v2 import (
    ArtifactDigest,
    EvidenceToken,
    RecognitionEvidence,
    speech_coverage_receipt_content_hash,
)


def _evidence(
    audio: Path,
    intervals: tuple[tuple[int, int], ...],
    *,
    episode_id: str = "episode-coverage",
    invocation_id: str = "recognition-coverage",
    adapter: str = "fixture-recognizer",
) -> RecognitionEvidence:
    raw = audio.parent / f"recognition-{adapter}.json"
    raw.write_text("{}", encoding="utf-8")
    digest = hash_file(raw)
    return RecognitionEvidence(
        episode_id=episode_id,
        invocation_id=invocation_id,
        adapter=adapter,
        model="fixture-v1",
        language="zh-Hant-TW",
        config_hash=hash_object({"recognizer": adapter}),
        raw_output=ArtifactDigest(
            uri=raw.resolve().as_uri(),
            sha256=digest,
            size_bytes=raw.stat().st_size,
        ),
        raw_output_hash=digest,
        normalized_audio_hash=hash_file(audio),
        tokens=tuple(
            EvidenceToken(
                id=f"{adapter}-token-{index}",
                text=f"詞{index}",
                start_ms=start,
                end_ms=end,
                confidence=0.99,
                speaker="guest",
            )
            for index, (start, end) in enumerate(intervals)
        ),
    )


def _request(
    tmp_path: Path,
    intervals: tuple[tuple[int, int], ...],
    *,
    duration_ms: int = 4_000,
) -> SpeechCoverageRequest:
    tmp_path.mkdir(parents=True, exist_ok=True)
    audio = tmp_path / "normalized.wav"
    audio.write_bytes(b"normalized-audio-fixture")
    evidence = _evidence(audio, intervals)
    return SpeechCoverageRequest(
        episode_id=evidence.episode_id,
        invocation_id=evidence.invocation_id,
        normalized_audio=audio,
        expected_normalized_audio_hash=hash_file(audio),
        expected_normalized_audio_size_bytes=audio.stat().st_size,
        normalized_audio_duration_ms=duration_ms,
        recognition_evidence=(evidence,),
        raw_output_dir=tmp_path / "coverage-raw",
    )


def _runner(
    *,
    duration_seconds: float = 4.0,
    silencedetect_stderr: bytes = b"",
    ffprobe_returncode: int = 0,
    ffmpeg_returncode: int = 0,
):
    def run(command: tuple[str, ...]) -> CommandResult:
        if command[0] == "ffprobe":
            return CommandResult(
                returncode=ffprobe_returncode,
                stdout=json.dumps({"format": {"duration": str(duration_seconds)}}).encode("utf-8"),
                stderr=b"probe-stderr",
            )
        assert command[0] == "ffmpeg"
        return CommandResult(
            returncode=ffmpeg_returncode,
            stdout=b"analysis-stdout",
            stderr=silencedetect_stderr,
        )

    return run


def _analyzer(**runner_kwargs: object) -> FFmpegSpeechCoverageAnalyzer:
    return FFmpegSpeechCoverageAnalyzer(
        runner=_runner(**runner_kwargs),
        runtime_identity=b"ffmpeg fixture runtime 1",
        coverage_tolerance_ms=0,
        minimum_uncovered_ms=100,
    )


def _ranges(intervals: object) -> tuple[tuple[int, int], ...]:
    return tuple((item.start_ms, item.end_ms) for item in intervals)


def test_energy_activity_wholly_covered_passes_and_restart_reverifies(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path, ((500, 3_500),))
    stderr = b"silence_start: 0\nsilence_end: 0.5\nsilence_start: 3.5\n"
    analyzer = _analyzer(silencedetect_stderr=stderr)

    receipt = analyzer.analyze(request)

    assert receipt.status == "completed"
    assert receipt.passed is True
    assert _ranges(receipt.activity_intervals) == ((500, 3_500),)
    assert receipt.uncovered_intervals == ()
    raw = Path(receipt.raw_output.uri.removeprefix("file:///"))
    if not raw.is_file():  # POSIX URI shape used outside Windows.
        from urllib.parse import unquote, urlparse
        from urllib.request import url2pathname

        parsed = urlparse(receipt.raw_output.uri)
        raw = Path(url2pathname(unquote(parsed.path)))
    same_runtime = _analyzer(silencedetect_stderr=stderr)
    assert same_runtime.verify(receipt, request=request, raw_output=raw.read_bytes()) == receipt
    assert len(speech_coverage_receipt_content_hash(receipt)) == 64


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        (((1_000, 4_000),), ((0, 1_000),)),
        (((0, 1_000), (2_000, 4_000)), ((1_000, 2_000),)),
        (((0, 3_000),), ((3_000, 4_000),)),
    ],
    ids=("leading", "middle", "trailing"),
)
def test_uncovered_energy_activity_fails_closed(
    tmp_path: Path,
    tokens: tuple[tuple[int, int], ...],
    expected: tuple[tuple[int, int], ...],
) -> None:
    request = _request(tmp_path, tokens)

    receipt = _analyzer().analyze(request)

    assert receipt.status == "completed"
    assert receipt.passed is False
    assert _ranges(receipt.uncovered_intervals) == expected


def test_corroborating_asr_cannot_hide_primary_canonical_omission(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path, ((0, 1_000),))
    secondary = _evidence(
        request.normalized_audio,
        ((0, 4_000),),
        adapter="secondary-recognizer",
    )
    request = SpeechCoverageRequest(
        episode_id=request.episode_id,
        invocation_id=request.invocation_id,
        normalized_audio=request.normalized_audio,
        expected_normalized_audio_hash=request.expected_normalized_audio_hash,
        expected_normalized_audio_size_bytes=request.expected_normalized_audio_size_bytes,
        normalized_audio_duration_ms=request.normalized_audio_duration_ms,
        recognition_evidence=(*request.recognition_evidence, secondary),
        raw_output_dir=request.raw_output_dir,
    )

    receipt = _analyzer().analyze(request)

    assert receipt.passed is False
    assert _ranges(receipt.uncovered_intervals) == ((1_000, 4_000),)


def test_long_silence_between_tokens_is_not_an_asr_coverage_gap(tmp_path: Path) -> None:
    request = _request(tmp_path, ((0, 1_000), (2_000, 4_000)))
    stderr = b"silence_start: 1.0\nsilence_end: 2.0\n"

    receipt = _analyzer(silencedetect_stderr=stderr).analyze(request)

    assert receipt.passed is True
    assert _ranges(receipt.activity_intervals) == ((0, 1_000), (2_000, 4_000))


def test_tolerance_absorbs_only_audio_boundary_lead_and_lag(tmp_path: Path) -> None:
    request = _request(tmp_path, ((100, 3_900),))
    analyzer = FFmpegSpeechCoverageAnalyzer(
        runner=_runner(),
        runtime_identity=b"ffmpeg fixture runtime 1",
        coverage_tolerance_ms=100,
        minimum_uncovered_ms=1,
    )

    assert analyzer.analyze(request).passed is True


def test_duration_mismatch_and_process_failure_produce_failed_receipts(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path, ((0, 4_000),))

    duration_failure = _analyzer(duration_seconds=4.2).analyze(request)
    process_failure = _analyzer(ffmpeg_returncode=7).analyze(request)

    assert duration_failure.status == "failed"
    assert duration_failure.passed is False
    assert "duration differs" in (duration_failure.failure_reason or "")
    assert process_failure.status == "failed"
    assert "status 7" in (process_failure.failure_reason or "")


def test_audio_hash_or_size_mismatch_is_integrity_failure(tmp_path: Path) -> None:
    request = _request(tmp_path, ((0, 4_000),))
    request.normalized_audio.write_bytes(b"mutated")

    with pytest.raises(AdapterIntegrityError, match="audio bytes"):
        _analyzer().analyze(request)


def test_verify_rejects_raw_tamper_and_receipt_claim_tamper(tmp_path: Path) -> None:
    request = _request(tmp_path, ((0, 4_000),))
    analyzer = _analyzer()
    receipt = analyzer.analyze(request)
    raw_path = Path(receipt.raw_output.uri.removeprefix("file:///"))
    if not raw_path.is_file():
        from urllib.parse import unquote, urlparse
        from urllib.request import url2pathname

        parsed = urlparse(receipt.raw_output.uri)
        raw_path = Path(url2pathname(unquote(parsed.path)))
    raw = raw_path.read_bytes()

    with pytest.raises(AdapterIntegrityError, match="bytes differ"):
        analyzer.verify(receipt, request=request, raw_output=raw + b"x")
    false_failure = receipt.model_copy(
        update={"status": "failed", "passed": False, "failure_reason": "fabricated"}
    )
    with pytest.raises(AdapterIntegrityError, match="not reproducible"):
        analyzer.verify(false_failure, request=request, raw_output=raw)


def test_fixture_analyzer_is_text_free_and_fail_closed(tmp_path: Path) -> None:
    request = _request(tmp_path, ((500, 1_500),))
    fixture = FixtureSpeechCoverageAnalyzer(
        ((0, 2_000),),
        coverage_tolerance_ms=0,
        minimum_uncovered_ms=100,
    )

    receipt = fixture.analyze(request)

    assert _ranges(receipt.uncovered_intervals) == ((0, 500), (1_500, 2_000))
    assert not hasattr(receipt.activity_intervals[0], "text")
    failed = FixtureSpeechCoverageAnalyzer(failure_reason="analyzer refused").analyze(request)
    assert failed.status == "failed"
    assert failed.passed is False


def test_default_fixture_mirrors_recognition_intervals_only_for_module_tests(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path, ((200, 500), (550, 850)))

    receipt = FixtureSpeechCoverageAnalyzer().analyze(request)

    assert receipt.passed is True
    assert _ranges(receipt.activity_intervals) == ((200, 500), (550, 850))


def test_zero_activity_is_not_fabricated_speech(tmp_path: Path) -> None:
    request = _request(tmp_path, ((100, 200),))
    stderr = b"silence_start: 0\nsilence_end: 4.0\n"

    receipt = _analyzer(silencedetect_stderr=stderr).analyze(request)

    assert receipt.status == "completed"
    assert receipt.passed is True
    assert receipt.activity_intervals == ()
    assert receipt.uncovered_intervals == ()


def test_full_activity_and_touching_boundaries_have_exact_coverage(tmp_path: Path) -> None:
    request = _request(tmp_path, ((0, 1_000), (1_000, 4_000)))

    production = _analyzer().analyze(request)
    fixture = FixtureSpeechCoverageAnalyzer(
        ((0, 1_000), (1_000, 4_000)),
        minimum_uncovered_ms=1,
    ).analyze(request)

    assert production.passed is True
    assert fixture.passed is True
    assert _ranges(production.activity_intervals) == ((0, 4_000),)
    assert _ranges(fixture.activity_intervals) == ((0, 4_000),)


def test_runner_exception_is_a_persisted_failed_receipt(tmp_path: Path) -> None:
    request = _request(tmp_path, ((0, 4_000),))

    def exploding_runner(_command: tuple[str, ...]) -> CommandResult:
        raise OSError("executable missing")

    analyzer = FFmpegSpeechCoverageAnalyzer(
        runner=exploding_runner,
        runtime_identity=b"missing runtime fixture",
    )
    receipt = analyzer.analyze(request)

    assert receipt.status == "failed"
    assert receipt.passed is False
    assert "unavailable" in (receipt.failure_reason or "")


@pytest.mark.parametrize(
    "field",
    (
        "analyzer_config_hash",
        "analyzer_code_hash",
        "analyzer_runtime_hash",
    ),
)
def test_verify_rejects_tampered_adapter_identity(
    tmp_path: Path,
    field: str,
) -> None:
    request = _request(tmp_path, ((0, 4_000),))
    analyzer = _analyzer()
    receipt = analyzer.analyze(request)
    from urllib.parse import unquote, urlparse
    from urllib.request import url2pathname

    parsed = urlparse(receipt.raw_output.uri)
    raw = Path(url2pathname(unquote(parsed.path))).read_bytes()
    forged = receipt.model_copy(update={field: "0" * 64})

    with pytest.raises(AdapterIntegrityError, match="not reproducible"):
        analyzer.verify(forged, request=request, raw_output=raw)


def test_verify_rejects_rehashed_ffmpeg_payload_tamper(tmp_path: Path) -> None:
    request = _request(tmp_path, ((0, 4_000),))
    analyzer = _analyzer()
    receipt = analyzer.analyze(request)
    from urllib.parse import unquote, urlparse
    from urllib.request import url2pathname

    parsed = urlparse(receipt.raw_output.uri)
    raw = Path(url2pathname(unquote(parsed.path))).read_bytes()
    payload = json.loads(raw)
    payload["policy"]["noise_db"] = -99.0
    forged_raw = canonical_json_bytes(payload)
    forged_digest = sha256_bytes(forged_raw)
    forged_artifact = receipt.raw_output.model_copy(
        update={"sha256": forged_digest, "size_bytes": len(forged_raw)}
    )
    forged_receipt = receipt.model_copy(
        update={"raw_output": forged_artifact, "raw_output_hash": forged_digest}
    )

    with pytest.raises(AdapterIntegrityError, match="Adapter identity"):
        analyzer.verify(forged_receipt, request=request, raw_output=forged_raw)


def test_request_rejects_recognition_duration_overrun(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exceeds normalized audio duration"):
        _request(tmp_path, ((0, 4_001),), duration_ms=4_000)


def test_verify_rejects_cross_audio_replay(tmp_path: Path) -> None:
    first = _request(tmp_path / "first", ((0, 4_000),))
    analyzer = _analyzer()
    receipt = analyzer.analyze(first)
    from urllib.parse import unquote, urlparse
    from urllib.request import url2pathname

    parsed = urlparse(receipt.raw_output.uri)
    raw = Path(url2pathname(unquote(parsed.path))).read_bytes()
    second_root = tmp_path / "second"
    second_root.mkdir(parents=True)
    second = _request(second_root, ((0, 4_000),))
    second.normalized_audio.write_bytes(b"different-normalized-audio")
    second = SpeechCoverageRequest(
        episode_id=second.episode_id,
        invocation_id=second.invocation_id,
        normalized_audio=second.normalized_audio,
        expected_normalized_audio_hash=hash_file(second.normalized_audio),
        expected_normalized_audio_size_bytes=second.normalized_audio.stat().st_size,
        normalized_audio_duration_ms=second.normalized_audio_duration_ms,
        recognition_evidence=(
            _evidence(
                second.normalized_audio,
                ((0, 4_000),),
                episode_id=second.episode_id,
                invocation_id=second.invocation_id,
            ),
        ),
        raw_output_dir=second.raw_output_dir,
    )

    with pytest.raises(AdapterIntegrityError, match="crossed request"):
        analyzer.verify(receipt, request=second, raw_output=raw)
