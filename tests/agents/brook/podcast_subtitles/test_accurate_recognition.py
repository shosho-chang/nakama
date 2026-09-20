from __future__ import annotations

import json
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

import agents.brook.podcast_subtitles.accurate_recognition as subject
from agents.brook.podcast_subtitles.adapters.faster_whisper_recognition import (
    FasterWhisperRecognizerAdapter,
)
from agents.brook.podcast_subtitles.adapters.recognition import Qwen3ASRRecognizerAdapter
from agents.brook.podcast_subtitles.hashing import canonical_json_bytes, hash_object, sha256_bytes
from agents.brook.podcast_subtitles.ports import (
    NO_LEXICAL_BIAS_CONTEXT,
    AdapterInputError,
    AdapterIntegrityError,
    RecognitionRequest,
)
from agents.brook.podcast_subtitles.recognition_pilot import (
    FASTER_MODEL_REVISION,
    QWEN_ALIGNER_REVISION,
    QWEN_MODEL_REVISION,
)
from agents.brook.podcast_subtitles.recognition_run import (
    RecognitionRunIntegrityError,
    build_recognition_run_failure,
)
from shared.schemas.podcast_subtitles_v2 import (
    ArtifactDigest,
    EvidenceToken,
    RecognitionEvidence,
)


def _write_pcm_wav(
    path: Path,
    *,
    duration_seconds: float = 0.1,
    rate: int = 16_000,
) -> None:
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"\x00\x00" * round(rate * duration_seconds))


def _evidence(request: RecognitionRequest, *, adapter: str) -> RecognitionEvidence:
    raw = f"{adapter}-raw".encode("utf-8")
    raw_hash = sha256_bytes(raw)
    assert request.expected_normalized_audio_hash is not None
    return RecognitionEvidence(
        episode_id=request.episode_id,
        invocation_id=request.invocation_id,
        adapter=adapter,
        model=f"{adapter}-模型@fixture",
        language="zh-TW",
        config_hash=hash_object({"adapter": adapter}),
        raw_output=ArtifactDigest(
            uri=f"fixture://{adapter}",
            sha256=raw_hash,
            size_bytes=len(raw),
        ),
        raw_output_hash=raw_hash,
        normalized_audio_hash=request.expected_normalized_audio_hash,
        tokens=(
            EvidenceToken(
                id=f"{adapter}-token-0",
                text="台灣製造",
                start_ms=10,
                end_ms=80,
                confidence=0.95,
            ),
        ),
    )


class _FixtureRecognizer:
    def __init__(self, role: str, events: list[str], requests: list[RecognitionRequest]) -> None:
        self.role = role
        self.events = events
        self.requests = requests

    def recognize(self, request: RecognitionRequest) -> RecognitionEvidence:
        self.events.append(self.role)
        self.requests.append(request)
        return _evidence(request, adapter=self.role)


_SEAM_EVENTS = (
    ("開始", 0.5, 1.0),
    ("接縫甲", 3.8, 4.2),
    ("中段", 5.0, 5.5),
    ("接縫乙", 7.8, 8.2),
    ("結尾", 8.5, 8.8),
)


def _event_runner(
    events: tuple[tuple[str, float, float], ...],
    *,
    calls: list[int],
    mutation: tuple[int, str] | None = None,
    forbidden: bool = False,
):
    def run(path: Path, _request: RecognitionRequest) -> dict[str, object]:
        chunk_index = int(path.stem.split("-")[2])
        calls.append(chunk_index)
        if forbidden:
            raise AssertionError("terminal seam recovery must not call Qwen again")
        inference_start_sample = int(path.stem.split("-")[3])
        with wave.open(str(path), "rb") as reader:
            rate = reader.getframerate()
            duration = reader.getnframes() / rate
        inference_start = inference_start_sample / rate
        words: list[dict[str, object]] = []
        for text, global_start, global_end in events:
            midpoint = (global_start + global_end) / 2
            if inference_start <= midpoint < inference_start + duration:
                observed = (
                    text + "錯"
                    if mutation == (chunk_index, text)
                    else text
                )
                words.append(
                    {
                        "word": observed,
                        "start": global_start - inference_start,
                        "end": global_end - inference_start,
                    }
                )
        return {
            "language": "zh",
            "transcript_text": "".join(str(word["word"]) for word in words),
            "words": words,
        }

    return run


def _chunk_words_runner(
    words_by_chunk: dict[int, tuple[tuple[str, float, float], ...]],
    *,
    calls: list[int],
):
    """Return exact per-chunk global observations as Qwen-local timestamps."""

    def run(path: Path, _request: RecognitionRequest) -> dict[str, object]:
        chunk_index = int(path.stem.split("-")[2])
        calls.append(chunk_index)
        inference_start_sample = int(path.stem.split("-")[3])
        with wave.open(str(path), "rb") as reader:
            rate = reader.getframerate()
        inference_start = inference_start_sample / rate
        words = [
            {
                "word": text,
                "start": start - inference_start,
                "end": end - inference_start,
            }
            for text, start, end in words_by_chunk[chunk_index]
        ]
        return {
            "language": "zh",
            "transcript_text": "".join(str(word["word"]) for word in words),
            "words": words,
        }

    return run


_REAL_OVERLAP_SHAPE = {
    0: (("填零", 1.0, 1.2), ("有", 3.84, 4.08)),
    1: (("有", 3.92, 4.08), ("填一", 5.0, 5.2), ("但", 7.84, 8.08)),
    2: (("但", 7.92, 8.08), ("填二", 9.0, 9.2), ("我", 11.92, 12.0)),
    3: (("我", 11.92, 12.08), ("填三", 13.0, 13.2), ("就是", 15.84, 16.08)),
    4: (
        ("是", 15.92, 16.08),
        ("填四", 17.0, 17.2),
        ("忘記了哈哈哈哈哈哈哈台灣", 18.0, 20.4),
    ),
    5: (("灣", 20.08, 20.32), ("製", 20.32, 20.48), ("填五", 21.0, 21.2)),
}


def _checkpoint_qwen(output: Path, runner) -> Qwen3ASRRecognizerAdapter:
    repository = subject.RecognitionRunRepository(output)
    return Qwen3ASRRecognizerAdapter(
        model_revision="fixture-qwen-model-v1",
        forced_aligner_revision="fixture-qwen-aligner-v1",
        owned_chunk_seconds=4,
        seam_context_ms=1_000,
        runner=runner,
        runner_runtime_components={"fixture-qwen": "v1"},
        runner_code_hash="b" * 64,
        runner_execution_mode="fixture",
        recognition_run_repository=repository,
        logical_namespace=Qwen3ASRRecognizerAdapter.RECOGNITION_RUN_NAMESPACE,
    )


def _faster_observation(
    *,
    segment_start: float = 0.30,
    first_word_start: float = 0.20,
) -> dict[str, object]:
    return {
        "language": "zh",
        "language_probability": 0.99,
        "duration": 1.0,
        "duration_after_vad": 1.0,
        "segments": [
            {
                "id": 0,
                "seek": 0,
                "start": segment_start,
                "end": 0.80,
                "text": "甲乙",
                "tokens": [1, 2],
                "avg_logprob": -0.1,
                "compression_ratio": 1.0,
                "no_speech_prob": 0.0,
                "temperature": 0.0,
                "words": [
                    {
                        "word": "甲",
                        "start": first_word_start,
                        "end": 0.40,
                        "probability": 0.95,
                    },
                    {
                        "word": "乙",
                        "start": 0.40,
                        "end": 0.70,
                        "probability": 0.94,
                    },
                ],
            }
        ],
    }


def _checkpoint_faster(
    output: Path,
    *,
    runner,
) -> FasterWhisperRecognizerAdapter:
    return FasterWhisperRecognizerAdapter(
        model_revision="fixture-faster-model-v1",
        owned_chunk_seconds=4,
        seam_context_ms=1_000,
        runner=runner,
        runner_runtime_components={"fixture-faster": "v1"},
        runner_code_hash="c" * 64,
        runner_execution_mode="fixture",
        recognition_run_repository=subject.RecognitionRunRepository(output),
        logical_namespace=FasterWhisperRecognizerAdapter.RECOGNITION_RUN_NAMESPACE,
    )


def _invalid_faster_observation(case: str) -> dict[str, object]:
    observation = _faster_observation()
    segments = observation["segments"]
    assert isinstance(segments, list)
    segment = segments[0]
    assert isinstance(segment, dict)
    words = segment["words"]
    assert isinstance(words, list)
    first = words[0]
    second = words[1]
    assert isinstance(first, dict)
    assert isinstance(second, dict)

    if case == "201ms":
        first["start"] = 0.099
    elif case == "non-first-word":
        segment["start"] = 0.20
        first.update({"start": 0.20, "end": 0.25})
        second.update({"start": 0.15, "end": 0.40})
    elif case == "word-end":
        first["end"] = 0.81
    elif case == "text-mismatch":
        segment["text"] = "甲丙"
    elif case == "previous-segment-overlap":
        segment.update(
            {
                "id": 1,
                "start": 0.55,
                "words": [
                    {"word": "甲", "start": 0.45, "end": 0.60, "probability": 0.95},
                    {"word": "乙", "start": 0.60, "end": 0.70, "probability": 0.94},
                ],
            }
        )
        segments.insert(
            0,
            {
                "id": 0,
                "seek": 0,
                "start": 0.10,
                "end": 0.50,
                "text": "前",
                "tokens": [0],
                "avg_logprob": -0.1,
                "compression_ratio": 1.0,
                "no_speech_prob": 0.0,
                "temperature": 0.0,
                "words": [
                    {
                        "word": "前",
                        "start": 0.10,
                        "end": 0.45,
                        "probability": 0.96,
                    }
                ],
            },
        )
    elif case == "chunk-duration":
        observation["duration"] = 0.99
        observation["duration_after_vad"] = 0.99
    else:  # pragma: no cover - fixture authoring guard
        raise AssertionError(f"unknown Faster invalid case: {case}")
    return observation


def _faster_overlap_runner(
    *,
    calls: list[int],
    forbidden: bool = False,
    matched: bool = False,
):
    """Two independent shared-context observations with one real 40 ms spill."""

    by_chunk = {
        0: (("前", 1.00, 1.20), ("甲", 3.94, 4.02)),
        1: (
            (("甲", 3.94, 4.02) if matched else ("乙", 3.98, 4.06)),
            ("後", 5.00, 5.20),
        ),
    }

    def runner(path: Path, _request: RecognitionRequest) -> dict[str, object]:
        chunk_index = int(path.stem.split("-")[3])
        calls.append(chunk_index)
        if forbidden:
            raise AssertionError("completed Faster checkpoint must not call its provider")
        inference_start_sample = int(path.stem.split("-")[4])
        with wave.open(str(path), "rb") as reader:
            rate = reader.getframerate()
            duration = reader.getnframes() / rate
        inference_start = inference_start_sample / rate
        local_words = [
            {
                "word": text,
                "start": start - inference_start,
                "end": end - inference_start,
                "probability": 0.95,
            }
            for text, start, end in by_chunk[chunk_index]
        ]
        first_start = float(local_words[0]["start"])
        return {
            "language": "zh",
            "language_probability": 0.99,
            "duration": duration,
            "duration_after_vad": duration,
            "segments": [
                {
                    "id": 0,
                    "seek": 0,
                    "start": first_start + (0.10 if chunk_index == 0 else 0.02),
                    "end": float(local_words[-1]["end"]),
                    "text": "".join(str(word["word"]) for word in local_words),
                    "tokens": [1, 2],
                    "avg_logprob": -0.1,
                    "compression_ratio": 1.0,
                    "no_speech_prob": 0.0,
                    "temperature": 0.0,
                    "words": local_words,
                }
            ],
        }

    return runner


def _range_policy_values(
    *,
    overlap_ms: int = 40,
    left_text: str = "甲",
    right_text: str = "乙",
    right_chunk_index: int = 1,
    same_chunk: bool = False,
    comparison_start: int = 800,
):
    chunk_count = max(2, right_chunk_index + 1)
    chunks = tuple(
        SimpleNamespace(id=f"chunk-{index}", index=index)
        for index in range(chunk_count)
    )
    right_chunk_id = "chunk-0" if same_chunk else f"chunk-{right_chunk_index}"
    words = (
        subject.faster_recognition._MergedWord(
            index=0,
            word=left_text,
            start=0.90,
            end=(1_000 + overlap_ms) / 1_000,
            probability=0.95,
            source_chunk_id="chunk-0",
            source_word_index=0,
            ownership="word_midpoint_in_half_open_owned_interval_v1",
        ),
        subject.faster_recognition._MergedWord(
            index=1,
            word=right_text,
            start=1.00,
            end=1.10,
            probability=0.94,
            source_chunk_id=right_chunk_id,
            source_word_index=0,
            ownership="word_midpoint_in_half_open_owned_interval_v1",
        ),
    )
    seams = (
        subject.faster_recognition._SeamReceipt(
            id="faster-whisper-seam-1000",
            seam_sample=1_000,
            left_chunk_id="chunk-0",
            right_chunk_id=right_chunk_id,
            comparison_start_sample=comparison_start,
            comparison_end_sample=1_200,
            left_text=left_text,
            right_text=right_text,
            left_normalized=subject.FasterWhisperRecognizerAdapter._seam_text(left_text),
            right_normalized=subject.FasterWhisperRecognizerAdapter._seam_text(right_text),
            status="conflict",
        ),
    )
    return words, chunks, seams


def test_runner_uses_one_request_and_runs_qwen_before_faster(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio)
    events: list[str] = []
    requests: list[RecognitionRequest] = []
    monkeypatch.setattr(subject, "_release_primary_gpu_memory", lambda: events.append("release"))

    result = subject.run_accurate_recognition(
        audio=audio,
        output_dir=tmp_path / "output",
        episode_id="episode-anji",
        primary_recognizer=_FixtureRecognizer("qwen", events, requests),
        corroborating_recognizer=_FixtureRecognizer("faster", events, requests),
    )

    assert events == ["qwen", "release", "faster"]
    assert result.qwen_evidence.adapter == "qwen"
    assert result.faster_evidence.adapter == "faster"
    assert result.qwen_evidence_path == result.primary_evidence_path
    assert result.faster_evidence_path == result.corroborating_evidence_path
    assert len(requests) == 2
    assert requests[0] is not requests[1]
    assert requests[0].normalized_audio == audio.resolve()
    assert requests[0].episode_id == "episode-anji"
    assert requests[0].invocation_id == result.invocation_id
    assert requests[0].expected_normalized_audio_hash == result.audio_sha256
    assert requests[0].language_hint == "zh-TW"
    assert requests[0].context_policy == NO_LEXICAL_BIAS_CONTEXT
    assert requests[1].normalized_audio == requests[0].normalized_audio
    assert requests[1].episode_id == requests[0].episode_id
    assert requests[1].invocation_id == requests[0].invocation_id
    assert requests[1].expected_normalized_audio_hash == result.audio_sha256
    assert requests[0].raw_output_dir != requests[1].raw_output_dir
    assert "primary" in requests[0].raw_output_dir.parts
    assert "corroborating" in requests[1].raw_output_dir.parts
    assert result.primary_seam_conflicts == ()
    assert result.corroborating_segment_boundary_repairs == ()


def test_runner_persists_canonical_content_addressed_evidence(tmp_path: Path) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio)
    events: list[str] = []
    requests: list[RecognitionRequest] = []

    first = subject.run_accurate_recognition(
        audio=audio,
        output_dir=tmp_path / "output",
        episode_id="episode-anji",
        primary_recognizer=_FixtureRecognizer("qwen", events, requests),
        corroborating_recognizer=_FixtureRecognizer("faster", events, requests),
    )
    second = subject.run_accurate_recognition(
        audio=audio,
        output_dir=tmp_path / "output",
        episode_id="episode-anji",
        primary_recognizer=_FixtureRecognizer("qwen", events, requests),
        corroborating_recognizer=_FixtureRecognizer("faster", events, requests),
    )

    assert first.invocation_id == second.invocation_id
    assert first.primary_evidence_path == second.primary_evidence_path
    assert first.corroborating_evidence_path == second.corroborating_evidence_path
    assert first.primary_evidence_path.read_bytes() == canonical_json_bytes(first.primary_evidence)
    assert first.corroborating_evidence_path.read_bytes() == canonical_json_bytes(
        first.corroborating_evidence
    )
    assert "台灣製造".encode("utf-8") in first.primary_evidence_path.read_bytes()
    assert tuple((tmp_path / "output" / "evidence").glob("*.json")) == (
        first.corroborating_evidence_path,
        first.primary_evidence_path,
    )


def test_explicit_invocation_is_shared_by_both_models(tmp_path: Path) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio)
    events: list[str] = []
    requests: list[RecognitionRequest] = []

    result = subject.run_accurate_recognition(
        audio=audio,
        output_dir=tmp_path / "output",
        episode_id="episode-anji",
        invocation_id="manual-invocation-1",
        primary_recognizer=_FixtureRecognizer("qwen", events, requests),
        corroborating_recognizer=_FixtureRecognizer("faster", events, requests),
    )

    assert result.invocation_id == "manual-invocation-1"
    assert [request.invocation_id for request in requests] == [
        "manual-invocation-1",
        "manual-invocation-1",
    ]


def test_default_adapters_are_pinned_local_and_share_checkpoint_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, dict[str, object]] = {}

    class FakeQwen:
        RECOGNITION_RUN_NAMESPACE = "qwen-checkpoint-v3"

        def __init__(self, **kwargs: object) -> None:
            captured["qwen"] = kwargs

    class FakeFaster:
        RECOGNITION_RUN_NAMESPACE = "faster-checkpoint-v2"

        def __init__(self, **kwargs: object) -> None:
            captured["faster"] = kwargs

    monkeypatch.setattr(subject, "Qwen3ASRRecognizerAdapter", FakeQwen)
    monkeypatch.setattr(subject, "FasterWhisperRecognizerAdapter", FakeFaster)

    primary, corroborating = subject.build_accurate_recognizers(tmp_path / "output")

    assert isinstance(primary, FakeQwen)
    assert isinstance(corroborating, FakeFaster)
    assert captured["qwen"]["model_revision"] == QWEN_MODEL_REVISION
    assert captured["qwen"]["forced_aligner_revision"] == QWEN_ALIGNER_REVISION
    assert captured["faster"]["model_revision"] == FASTER_MODEL_REVISION
    assert captured["qwen"]["local_files_only"] is True
    assert captured["faster"]["local_files_only"] is True
    assert captured["qwen"]["logical_namespace"] == "qwen-checkpoint-v3"
    assert captured["faster"]["logical_namespace"] == "faster-checkpoint-v2"
    repository = captured["qwen"]["recognition_run_repository"]
    assert repository is captured["faster"]["recognition_run_repository"]
    assert repository.subtitle_root == (tmp_path / "output").resolve()


def test_runner_rejects_non_pcm_input_before_recognition(tmp_path: Path) -> None:
    audio = tmp_path / "not-a-wave.mp3"
    audio.write_bytes(b"not pcm")
    called: list[str] = []
    fixture = _FixtureRecognizer("qwen", called, [])

    with pytest.raises(RecognitionRunIntegrityError, match="PCM WAV"):
        subject.run_accurate_recognition(
            audio=audio,
            output_dir=tmp_path / "output",
            episode_id="episode-anji",
            primary_recognizer=fixture,
            corroborating_recognizer=fixture,
        )

    assert called == []


def test_runner_rejects_partial_fixture_pair_and_bad_identities(tmp_path: Path) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio)
    fixture = _FixtureRecognizer("qwen", [], [])

    with pytest.raises(ValueError, match="supplied together"):
        subject.run_accurate_recognition(
            audio=audio,
            output_dir=tmp_path / "output",
            episode_id="episode-anji",
            primary_recognizer=fixture,
        )
    with pytest.raises(ValueError, match="episode_id"):
        subject.run_accurate_recognition(
            audio=audio,
            output_dir=tmp_path / "output",
            episode_id=" ",
            primary_recognizer=fixture,
            corroborating_recognizer=fixture,
        )
    with pytest.raises(ValueError, match="invocation_id"):
        subject.run_accurate_recognition(
            audio=audio,
            output_dir=tmp_path / "output",
            episode_id="episode-anji",
            invocation_id=" bad ",
            primary_recognizer=fixture,
            corroborating_recognizer=fixture,
        )


def test_runner_rejects_evidence_from_a_different_audio_clock(tmp_path: Path) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio)
    events: list[str] = []
    requests: list[RecognitionRequest] = []

    class WrongClock(_FixtureRecognizer):
        def recognize(self, request: RecognitionRequest) -> RecognitionEvidence:
            evidence = super().recognize(request)
            return RecognitionEvidence.model_validate(
                {
                    **evidence.model_dump(mode="python"),
                    "normalized_audio_hash": "0" * 64,
                },
                strict=True,
            )

    with pytest.raises(ValueError, match="shared request"):
        subject.run_accurate_recognition(
            audio=audio,
            output_dir=tmp_path / "output",
            episode_id="episode-anji",
            primary_recognizer=WrongClock("qwen", events, requests),
            corroborating_recognizer=_FixtureRecognizer("faster", events, requests),
        )

    assert events == ["qwen"]


def test_faster_checkpoint_recovers_only_bounded_first_word_segment_start_drift(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio, duration_seconds=1.0)
    output = tmp_path / "output"
    calls: list[str] = []

    def runner(path: Path, _request: RecognitionRequest) -> dict[str, object]:
        calls.append(path.name)
        return _faster_observation()

    result = subject.run_accurate_recognition(
        audio=audio,
        output_dir=output,
        episode_id="episode-faster-boundary-repair",
        primary_recognizer=_FixtureRecognizer("qwen", [], []),
        corroborating_recognizer=_checkpoint_faster(output, runner=runner),
    )

    assert len(calls) == 1
    assert [token.text for token in result.corroborating_evidence.tokens] == ["甲", "乙"]
    assert len(result.corroborating_segment_boundary_repairs) == 1
    repair = result.corroborating_segment_boundary_repairs[0]
    assert repair.chunk_index == 0
    assert repair.segment_id == 0
    assert repair.word_index == 0
    assert repair.word_text == "甲"
    assert repair.before_segment_start_seconds == 0.30
    assert repair.after_segment_start_seconds == 0.20
    assert repair.delta_ms == 100
    assert repair.id.startswith("faster-segment-boundary-repair-")

    original = next(
        (output / "recognition-runs" / "runs").rglob("adapter-observation.json")
    ).read_text(encoding="utf-8")
    assert '"start":0.3' in original
    recovered_raw = Path(
        subject.qwen_recognition._file_uri_path_for_adapter(
            result.corroborating_evidence.raw_output.uri
        )
    ).read_text(encoding="utf-8")
    assert '"kind":"faster_whisper_secondary_recovery"' in recovered_raw
    assert repair.id in recovered_raw


def test_faster_secondary_recovery_records_seam_conflict_and_40ms_clamp(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio, duration_seconds=6.0)
    output = tmp_path / "output"
    calls: list[int] = []

    result = subject.run_accurate_recognition(
        audio=audio,
        output_dir=output,
        episode_id="episode-faster-secondary-recovery",
        primary_recognizer=_FixtureRecognizer("qwen", [], []),
        corroborating_recognizer=_checkpoint_faster(
            output,
            runner=_faster_overlap_runner(calls=calls),
        ),
    )

    assert calls == [0, 1]
    assert "".join(token.text for token in result.corroborating_evidence.tokens) == "前甲乙後"
    assert len(result.corroborating_segment_boundary_repairs) == 2
    assert len(result.corroborating_seam_conflicts) == 1
    conflict = result.corroborating_seam_conflicts[0]
    assert conflict.seam_sample == 4 * 16_000
    assert conflict.seam_ms == 4_000
    assert conflict.comparison_start_ms == 3_000
    assert conflict.comparison_end_ms == 5_000
    assert (conflict.left_normalized, conflict.right_normalized) == ("甲", "乙")
    assert conflict.id.startswith("faster-seam-conflict-")
    assert len(result.corroborating_owned_range_repairs) == 1
    repair = result.corroborating_owned_range_repairs[0]
    assert repair.reason == "clamp_left_end_cross_chunk_timestamp_spill"
    assert repair.overlap_ms == 40
    assert repair.before_tokens[0].end_ms == 4_020
    assert repair.after_tokens[0].end_ms == 3_980
    assert repair.before_tokens[1] == repair.after_tokens[1]
    assert [
        (token.text, token.start_ms, token.end_ms)
        for token in result.corroborating_evidence.tokens
    ] == [
        ("前", 1_000, 1_200),
        ("甲", 3_940, 3_980),
        ("乙", 3_980, 4_060),
        ("後", 5_000, 5_200),
    ]
    raw = json.loads(
        subject.qwen_recognition._file_uri_path_for_adapter(
            result.corroborating_evidence.raw_output.uri
        ).read_bytes()
    )
    assert raw["matched_seam_ids"] == []
    assert raw["seam_conflicts"][0]["id"] == conflict.id
    assert raw["owned_range_repairs"][0]["id"] == repair.id
    assert "status" not in raw["seam_conflicts"][0]


def test_faster_secondary_recovery_records_matched_seam_without_a_conflict(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio, duration_seconds=6.0)
    output = tmp_path / "output"
    result = subject.run_accurate_recognition(
        audio=audio,
        output_dir=output,
        episode_id="episode-faster-matched-seam",
        primary_recognizer=_FixtureRecognizer("qwen", [], []),
        corroborating_recognizer=_checkpoint_faster(
            output,
            runner=_faster_overlap_runner(calls=[], matched=True),
        ),
    )

    assert result.corroborating_seam_conflicts == ()
    assert result.corroborating_owned_range_repairs == ()
    assert "".join(token.text for token in result.corroborating_evidence.tokens) == "前甲後"
    raw = json.loads(
        subject.qwen_recognition._file_uri_path_for_adapter(
            result.corroborating_evidence.raw_output.uri
        ).read_bytes()
    )
    assert raw["seam_conflicts"] == []
    assert raw["matched_seam_ids"] == ["faster-whisper-seam-64000"]


def test_faster_secondary_recovery_rejects_a_tampered_raw_envelope(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio, duration_seconds=6.0)
    output = tmp_path / "output"
    first = subject.run_accurate_recognition(
        audio=audio,
        output_dir=output,
        episode_id="episode-faster-raw-tamper",
        primary_recognizer=_FixtureRecognizer("qwen", [], []),
        corroborating_recognizer=_checkpoint_faster(
            output,
            runner=_faster_overlap_runner(calls=[]),
        ),
    )
    raw_path = subject.qwen_recognition._file_uri_path_for_adapter(
        first.corroborating_evidence.raw_output.uri
    )
    raw_path.write_bytes(b"{}")

    with pytest.raises(AdapterIntegrityError, match="raw Evidence artifact collision"):
        subject.run_accurate_recognition(
            audio=audio,
            output_dir=output,
            episode_id="episode-faster-raw-tamper",
            primary_recognizer=_FixtureRecognizer("qwen", [], []),
            corroborating_recognizer=_checkpoint_faster(
                output,
                runner=_faster_overlap_runner(calls=[], forbidden=True),
            ),
        )


def test_faster_owned_range_policy_accepts_only_bounded_distinct_shared_spill() -> None:
    words, chunks, seams = _range_policy_values()
    repaired, repairs = subject._repair_faster_owned_range_overlaps(
        words,
        chunks=chunks,
        seams=seams,
        sample_rate_hz=1_000,
    )

    assert len(repairs) == 1
    assert repairs[0].overlap_ms == 40
    assert repaired[0].word == "甲"
    assert repaired[0].start == words[0].start
    assert repaired[0].end == words[1].start
    assert repaired[1] == words[1]


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"overlap_ms": 41}, "1-40 ms"),
        ({"same_chunk": True}, "adjacent source chunks"),
        ({"right_chunk_index": 2}, "adjacent source chunks"),
        ({"comparison_start": 950}, "shared comparison interval"),
        ({"right_text": "甲"}, "distinct nonempty"),
        ({"left_text": "，"}, "distinct nonempty"),
    ],
)
def test_faster_owned_range_policy_rejects_every_unapproved_shape(
    updates: dict[str, object],
    message: str,
) -> None:
    words, chunks, seams = _range_policy_values(**updates)

    with pytest.raises(AdapterIntegrityError, match=message):
        subject._repair_faster_owned_range_overlaps(
            words,
            chunks=chunks,
            seams=seams,
            sample_rate_hz=1_000,
        )


@pytest.mark.parametrize(
    "case",
    [
        "201ms",
        "non-first-word",
        "word-end",
        "text-mismatch",
        "previous-segment-overlap",
        "chunk-duration",
    ],
)
def test_faster_boundary_recovery_fails_closed_for_every_other_provider_shape(
    tmp_path: Path,
    case: str,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio, duration_seconds=1.0)
    output = tmp_path / case

    def runner(_path: Path, _request: RecognitionRequest) -> dict[str, object]:
        return _invalid_faster_observation(case)

    with pytest.raises((AdapterInputError, AdapterIntegrityError)):
        subject.run_accurate_recognition(
            audio=audio,
            output_dir=output,
            episode_id=f"episode-faster-reject-{case}",
            primary_recognizer=_FixtureRecognizer("qwen", [], []),
            corroborating_recognizer=_checkpoint_faster(output, runner=runner),
        )

    assert not tuple((output / "evidence").glob("corroborating-*.json"))


def test_faster_boundary_recovery_requires_a_complete_checkpoint_prefix(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio, duration_seconds=5.0)
    output = tmp_path / "output"
    calls = 0

    def runner(_path: Path, _request: RecognitionRequest) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise AdapterInputError("fixture runner stopped before the complete prefix")
        return _faster_observation()

    with pytest.raises(AdapterIntegrityError, match="complete durable chunk prefix"):
        subject.run_accurate_recognition(
            audio=audio,
            output_dir=output,
            episode_id="episode-faster-incomplete-prefix",
            primary_recognizer=_FixtureRecognizer("qwen", [], []),
            corroborating_recognizer=_checkpoint_faster(output, runner=runner),
        )

    assert calls == 2


def test_faster_boundary_replay_calls_no_provider_and_does_not_mutate_checkpoint(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio, duration_seconds=1.0)
    output = tmp_path / "output"
    invocation_id = subject.stable_recognition_invocation_id(
        episode_id="episode-faster-no-replay",
        audio_sha256=subject.build_recognition_audio_binding(audio).normalized_audio.sha256,
    )
    request = subject._request_for_role(
        root=output,
        role="corroborating",
        episode_id="episode-faster-no-replay",
        invocation_id=invocation_id,
        audio_path=audio.resolve(),
        audio_sha256=subject.build_recognition_audio_binding(audio).normalized_audio.sha256,
    )
    initial_calls: list[str] = []

    def initial_runner(path: Path, _request: RecognitionRequest) -> dict[str, object]:
        initial_calls.append(path.name)
        return _faster_observation()

    with pytest.raises(AdapterInputError, match="word exceeds its provider segment"):
        _checkpoint_faster(output, runner=initial_runner).recognize(request)
    checkpoint_root = output / "recognition-runs"
    before = {
        path.relative_to(checkpoint_root): path.read_bytes()
        for path in checkpoint_root.rglob("*")
        if path.is_file()
    }
    replay_calls: list[str] = []

    def forbidden_runner(path: Path, _request: RecognitionRequest) -> dict[str, object]:
        replay_calls.append(path.name)
        raise AssertionError("completed Faster checkpoint must not call its provider")

    result = subject.run_accurate_recognition(
        audio=audio,
        output_dir=output,
        episode_id="episode-faster-no-replay",
        primary_recognizer=_FixtureRecognizer("qwen", [], []),
        corroborating_recognizer=_checkpoint_faster(output, runner=forbidden_runner),
    )
    after = {
        path.relative_to(checkpoint_root): path.read_bytes()
        for path in checkpoint_root.rglob("*")
        if path.is_file()
    }

    assert len(initial_calls) == 1
    assert replay_calls == []
    assert before == after
    assert len(result.corroborating_segment_boundary_repairs) == 1


@pytest.mark.parametrize("status", ["rejected", "verified"])
def test_faster_boundary_recovery_refuses_every_terminal_checkpoint(
    tmp_path: Path,
    status: str,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio, duration_seconds=1.0)
    output = tmp_path / status
    audio_sha256 = subject.build_recognition_audio_binding(
        audio
    ).normalized_audio.sha256
    invocation_id = subject.stable_recognition_invocation_id(
        episode_id=f"episode-faster-terminal-{status}",
        audio_sha256=audio_sha256,
    )
    request = subject._request_for_role(
        root=output,
        role="corroborating",
        episode_id=f"episode-faster-terminal-{status}",
        invocation_id=invocation_id,
        audio_path=audio.resolve(),
        audio_sha256=audio_sha256,
    )
    calls: list[str] = []

    def runner(path: Path, _request: RecognitionRequest) -> dict[str, object]:
        calls.append(path.name)
        return _faster_observation()

    adapter = _checkpoint_faster(output, runner=runner)
    with pytest.raises(AdapterInputError, match="word exceeds its provider segment"):
        adapter.recognize(request)
    plan, _audio, _chunks = adapter._build_recognition_run_plan(request=request)
    repository = adapter._recognition_run_repository
    assert repository is not None
    if status == "rejected":
        repository.commit_finalization(
            plan=plan,
            status="rejected",
            failure=build_recognition_run_failure(
                code="recognition_evidence_rejected",
                affected_chunk_ids=tuple(chunk.id for chunk in plan.chunks),
                diagnostics={"reason": "fixture-terminal-rejection"},
            ),
        )
        expected = "terminally rejected"
    else:
        repository.commit_finalization(
            plan=plan,
            status="verified",
            recognition_evidence_bytes=canonical_json_bytes(
                {"fixture": "mismatched-evidence"}
            ),
        )
        expected = "refuses a terminal"

    with pytest.raises(AdapterIntegrityError, match=expected):
        subject.run_accurate_recognition(
            audio=audio,
            output_dir=output,
            episode_id=f"episode-faster-terminal-{status}",
            primary_recognizer=_FixtureRecognizer("qwen", [], []),
            corroborating_recognizer=adapter,
        )

    assert len(calls) == 1


def test_corrupt_faster_checkpoint_never_enters_boundary_recovery(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio, duration_seconds=1.0)
    output = tmp_path / "output"
    audio_sha256 = subject.build_recognition_audio_binding(
        audio
    ).normalized_audio.sha256
    invocation_id = subject.stable_recognition_invocation_id(
        episode_id="episode-faster-corrupt-checkpoint",
        audio_sha256=audio_sha256,
    )
    request = subject._request_for_role(
        root=output,
        role="corroborating",
        episode_id="episode-faster-corrupt-checkpoint",
        invocation_id=invocation_id,
        audio_path=audio.resolve(),
        audio_sha256=audio_sha256,
    )
    calls: list[str] = []

    def runner(path: Path, _request: RecognitionRequest) -> dict[str, object]:
        calls.append(path.name)
        return _faster_observation()

    adapter = _checkpoint_faster(output, runner=runner)
    with pytest.raises(AdapterInputError):
        adapter.recognize(request)
    observation = next(
        (output / "recognition-runs" / "runs").rglob("adapter-observation.json")
    )
    observation.write_bytes(b"{}")

    with pytest.raises(RecognitionRunIntegrityError):
        subject.run_accurate_recognition(
            audio=audio,
            output_dir=output,
            episode_id="episode-faster-corrupt-checkpoint",
            primary_recognizer=_FixtureRecognizer("qwen", [], []),
            corroborating_recognizer=adapter,
        )

    assert len(calls) == 1


def test_qwen_seam_conflict_recovers_owned_evidence_and_typed_review_item(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio, duration_seconds=9.0, rate=100)
    output = tmp_path / "output"
    qwen_calls: list[int] = []
    faster_events: list[str] = []
    faster_requests: list[RecognitionRequest] = []
    primary = _checkpoint_qwen(
        output,
        _event_runner(
            _SEAM_EVENTS,
            calls=qwen_calls,
            mutation=(1, "接縫甲"),
        ),
    )

    result = subject.run_accurate_recognition(
        audio=audio,
        output_dir=output,
        episode_id="episode-seam",
        primary_recognizer=primary,
        corroborating_recognizer=_FixtureRecognizer(
            "faster",
            faster_events,
            faster_requests,
        ),
    )

    assert qwen_calls == [0, 1, 2]
    assert faster_events == ["faster"]
    assert [token.text for token in result.primary_evidence.tokens] == [
        "開始",
        "接縫甲錯",
        "中段",
        "接縫乙",
        "結尾",
    ]
    assert len(result.primary_seam_conflicts) == 1
    conflict = result.primary_seam_conflicts[0]
    assert conflict.id == "qwen-seam-400"
    assert (conflict.seam_sample, conflict.seam_ms) == (400, 4_000)
    assert (
        conflict.comparison_start_sample,
        conflict.comparison_end_sample,
        conflict.comparison_start_ms,
        conflict.comparison_end_ms,
    ) == (300, 500, 3_000, 5_000)
    assert (conflict.left_text, conflict.right_text) == ("接縫甲", "接縫甲錯")
    assert "primary" in result.primary_evidence.raw_output.uri
    assert "corroborating" in faster_requests[0].raw_output_dir.parts

    finalization = next((output / "recognition-runs" / "runs").rglob("finalizations/*.json"))
    assert '"code":"seam_conflict"' in finalization.read_text(encoding="utf-8")


def test_real_six_overlap_shape_deduplicates_losslessly_and_records_exact_repairs(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio, duration_seconds=22.0, rate=100)
    output = tmp_path / "output"
    qwen_calls: list[int] = []

    result = subject.run_accurate_recognition(
        audio=audio,
        output_dir=output,
        episode_id="episode-six-overlaps",
        primary_recognizer=_checkpoint_qwen(
            output,
            _chunk_words_runner(_REAL_OVERLAP_SHAPE, calls=qwen_calls),
        ),
        corroborating_recognizer=_FixtureRecognizer("faster", [], []),
    )

    assert qwen_calls == [0, 1, 2, 3, 4, 5]
    assert "".join(token.text for token in result.primary_evidence.tokens) == (
        "填零有填一但填二我填三就是填四忘記了哈哈哈哈哈哈哈台灣製填五"
    )
    repairs = result.primary_owned_range_repairs
    assert len(repairs) == 6
    assert [repair.reason for repair in repairs] == [
        "drop_right_duplicate_left_suffix",
        "drop_right_duplicate_left_suffix",
        "drop_right_duplicate_left_suffix",
        "drop_right_duplicate_left_suffix",
        "drop_right_duplicate_left_suffix",
        "clamp_left_end_cross_seam_timestamp_spill",
    ]
    assert [repair.overlap_ms for repair in repairs] == [160, 160, 80, 160, 320, 80]
    assert [tuple(token.text for token in repair.before_tokens) for repair in repairs] == [
        ("有", "有"),
        ("但", "但"),
        ("我", "我"),
        ("就是", "是"),
        ("忘記了哈哈哈哈哈哈哈台灣", "灣"),
        ("忘記了哈哈哈哈哈哈哈台灣", "製"),
    ]
    assert tuple(token.text for token in repairs[4].after_tokens) == (
        "忘記了哈哈哈哈哈哈哈台灣",
    )
    assert [
        (token.text, token.start_ms, token.end_ms)
        for token in repairs[5].after_tokens
    ] == [
        ("忘記了哈哈哈哈哈哈哈台灣", 18_000, 20_320),
        ("製", 20_320, 20_480),
    ]
    assert all(repair.id.startswith("qwen-owned-range-repair-") for repair in repairs)
    assert all(
        right.start_ms >= left.end_ms
        for left, right in zip(
            result.primary_evidence.tokens,
            result.primary_evidence.tokens[1:],
        )
    )
    raw_path = next((output / "raw" / "primary").rglob("qwen3-asr-*.json"))
    raw_bytes = raw_path.read_bytes()
    raw_envelope = subject.qwen_recognition._QwenRecognitionEnvelope.model_validate_json(
        raw_bytes
    )
    assert sha256_bytes(raw_bytes) == result.primary_evidence.raw_output.sha256
    assert "".join(word.word for word in raw_envelope.merged_words) == (
        "填零有有填一但但填二我我填三就是是填四"
        "忘記了哈哈哈哈哈哈哈台灣灣製填五"
    )


def test_right_prefix_duplicate_drops_only_the_short_left_token(tmp_path: Path) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio, duration_seconds=6.0, rate=100)
    output = tmp_path / "output"
    result = subject.run_accurate_recognition(
        audio=audio,
        output_dir=output,
        episode_id="episode-right-prefix",
        primary_recognizer=_checkpoint_qwen(
            output,
            _chunk_words_runner(
                {
                    0: (("台", 3.84, 4.08),),
                    1: (("台灣", 3.92, 4.16),),
                },
                calls=[],
            ),
        ),
        corroborating_recognizer=_FixtureRecognizer("faster", [], []),
    )

    assert [token.text for token in result.primary_evidence.tokens] == ["台灣"]
    assert len(result.primary_owned_range_repairs) == 1
    repair = result.primary_owned_range_repairs[0]
    assert repair.reason == "drop_left_duplicate_right_prefix"
    assert tuple(token.text for token in repair.before_tokens) == ("台", "台灣")
    assert tuple(token.text for token in repair.after_tokens) == ("台灣",)


def test_overlap_repair_policy_rejects_unproven_or_unbounded_shapes(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio, duration_seconds=22.0, rate=100)
    output = tmp_path / "output"
    subject.run_accurate_recognition(
        audio=audio,
        output_dir=output,
        episode_id="episode-repair-policy",
        primary_recognizer=_checkpoint_qwen(
            output,
            _chunk_words_runner(_REAL_OVERLAP_SHAPE, calls=[]),
        ),
        corroborating_recognizer=_FixtureRecognizer("faster", [], []),
    )
    raw_path = next((output / "raw" / "primary").rglob("qwen3-asr-*.json"))
    base = subject.qwen_recognition._QwenRecognitionEnvelope.model_validate_json(
        raw_path.read_bytes()
    )
    overlap_index = next(
        index
        for index in range(1, len(base.merged_words))
        if base.merged_words[index].start_ms < base.merged_words[index - 1].end_ms
    )
    original_left = base.merged_words[overlap_index - 1]
    original_right = base.merged_words[overlap_index]

    def changed_words(*, left_updates: dict[str, object], right_updates: dict[str, object]):
        words = list(base.merged_words)
        words[overlap_index - 1] = original_left.model_copy(update=left_updates)
        words[overlap_index] = original_right.model_copy(update=right_updates)
        return base.model_copy(update={"merged_words": tuple(words)})

    cases = (
        (
            "same chunk",
            changed_words(
                left_updates={},
                right_updates={"source_chunk_id": original_left.source_chunk_id},
            ),
            "neighboring source chunks",
        ),
        (
            "non-neighbor chunk",
            changed_words(
                left_updates={},
                right_updates={"source_chunk_id": base.chunks[2].id},
            ),
            "neighboring source chunks",
        ),
        (
            "large overlap",
            changed_words(
                left_updates={"end_ms": original_right.start_ms + 321},
                right_updates={},
            ),
            "bounded seam-repair limit",
        ),
        (
            "outside comparison",
            changed_words(
                left_updates={"start_ms": 2_900, "end_ms": 3_199},
                right_updates={"start_ms": 2_999},
            ),
            "escapes its known seam comparison interval",
        ),
        (
            "distinct 81 ms spill",
            changed_words(
                left_updates={
                    "word": "甲",
                    "end_ms": original_right.start_ms + 81,
                },
                right_updates={"word": "乙"},
            ),
            "exceeds the 80 ms clamp limit",
        ),
        (
            "non-positive clamp",
            changed_words(
                left_updates={
                    "word": "甲",
                    "start_ms": original_right.start_ms,
                    "end_ms": original_right.start_ms + 80,
                },
                right_updates={"word": "乙"},
            ),
            "would make the left token non-positive",
        ),
    )

    for _label, envelope, expected in cases:
        with pytest.raises(AdapterIntegrityError, match=expected):
            subject._repair_qwen_owned_range_overlaps(envelope)


def test_terminal_rejected_qwen_seam_recovers_without_provider_replay(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio, duration_seconds=9.0, rate=100)
    output = tmp_path / "output"
    first_calls: list[int] = []
    first = subject.run_accurate_recognition(
        audio=audio,
        output_dir=output,
        episode_id="episode-seam",
        primary_recognizer=_checkpoint_qwen(
            output,
            _event_runner(
                _SEAM_EVENTS,
                calls=first_calls,
                mutation=(1, "接縫甲"),
            ),
        ),
        corroborating_recognizer=_FixtureRecognizer("faster", [], []),
    )
    role_raw = next((output / "raw" / "primary").rglob("qwen3-asr-*.json"))
    legacy_raw = output / "raw" / role_raw.name
    role_raw.replace(legacy_raw)
    replay_calls: list[int] = []

    replay = subject.run_accurate_recognition(
        audio=audio,
        output_dir=output,
        episode_id="episode-seam",
        primary_recognizer=_checkpoint_qwen(
            output,
            _event_runner(
                _SEAM_EVENTS,
                calls=replay_calls,
                forbidden=True,
            ),
        ),
        corroborating_recognizer=_FixtureRecognizer("faster", [], []),
    )

    assert first_calls == [0, 1, 2]
    assert replay_calls == []
    assert replay.primary_evidence == first.primary_evidence
    assert replay.primary_seam_conflicts == first.primary_seam_conflicts
    assert replay.primary_owned_range_repairs == first.primary_owned_range_repairs
    assert replay.primary_evidence_path == first.primary_evidence_path
    assert legacy_raw.is_file()
    assert next((output / "raw" / "primary").rglob("qwen3-asr-*.json")).is_file()


@pytest.mark.parametrize("raw_failure", ["corrupt", "ambiguous"])
def test_terminal_seam_recovery_rejects_corrupt_or_ambiguous_raw(
    tmp_path: Path,
    raw_failure: str,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio, duration_seconds=9.0, rate=100)
    output = tmp_path / "output"
    subject.run_accurate_recognition(
        audio=audio,
        output_dir=output,
        episode_id="episode-seam",
        primary_recognizer=_checkpoint_qwen(
            output,
            _event_runner(
                _SEAM_EVENTS,
                calls=[],
                mutation=(1, "接縫甲"),
            ),
        ),
        corroborating_recognizer=_FixtureRecognizer("faster", [], []),
    )
    raw_path = next((output / "raw" / "primary").rglob("qwen3-asr-*.json"))
    if raw_failure == "corrupt":
        raw_path.write_bytes(b"{")
        expected = "content-addressed"
    else:
        (raw_path.parent / f"qwen3-asr-{'0' * 64}.json").write_bytes(raw_path.read_bytes())
        expected = "unambiguous"

    with pytest.raises(AdapterIntegrityError, match=expected):
        subject.run_accurate_recognition(
            audio=audio,
            output_dir=output,
            episode_id="episode-seam",
            primary_recognizer=_checkpoint_qwen(
                output,
                _event_runner(_SEAM_EVENTS, calls=[], forbidden=True),
            ),
            corroborating_recognizer=_FixtureRecognizer("faster", [], []),
        )


@pytest.mark.parametrize(
    ("events", "message"),
    [
        ((("甲", 0.2, 1.2), ("乙", 1.0, 1.5)), "overlapping merged words"),
    ],
)
def test_non_seam_qwen_terminal_failures_remain_rejected(
    tmp_path: Path,
    events: tuple[tuple[str, float, float], ...],
    message: str,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio, duration_seconds=2.0, rate=100)
    output = tmp_path / "output"
    faster_events: list[str] = []

    with pytest.raises(AdapterIntegrityError, match=message):
        subject.run_accurate_recognition(
            audio=audio,
            output_dir=output,
            episode_id="episode-non-seam-failure",
            primary_recognizer=_checkpoint_qwen(
                output,
                _event_runner(events, calls=[]),
            ),
            corroborating_recognizer=_FixtureRecognizer("faster", faster_events, []),
        )

    assert faster_events == []


def test_empty_owned_qwen_output_remains_rejected(tmp_path: Path) -> None:
    audio = tmp_path / "normalized.wav"
    _write_pcm_wav(audio, duration_seconds=8.0, rate=100)
    output = tmp_path / "output"
    qwen_calls: list[int] = []
    faster_events: list[str] = []

    def outside_owned_range(path: Path, _request: RecognitionRequest) -> dict[str, object]:
        chunk_index = int(path.stem.split("-")[2])
        qwen_calls.append(chunk_index)
        local_start = 4.2 if chunk_index == 0 else 0.2
        return {
            "language": "zh",
            "transcript_text": "空",
            "words": [
                {
                    "word": "空",
                    "start": local_start,
                    "end": local_start + 0.1,
                }
            ],
        }

    with pytest.raises(AdapterIntegrityError, match="produced no owned words"):
        subject.run_accurate_recognition(
            audio=audio,
            output_dir=output,
            episode_id="episode-empty-owned",
            primary_recognizer=_checkpoint_qwen(output, outside_owned_range),
            corroborating_recognizer=_FixtureRecognizer("faster", faster_events, []),
        )

    assert qwen_calls == [0, 1]
    assert faster_events == []
