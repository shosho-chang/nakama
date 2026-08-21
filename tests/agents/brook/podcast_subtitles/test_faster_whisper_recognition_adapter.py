from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.brook.podcast_subtitles.adapters.faster_whisper_recognition import (
    FasterWhisperRecognizerAdapter,
)
from agents.brook.podcast_subtitles.hashing import hash_file, hash_object
from agents.brook.podcast_subtitles.ports import (
    AdapterInputError,
    AdapterIntegrityError,
    AdapterUnavailableError,
    RecognitionRequest,
)
from agents.brook.podcast_subtitles.recognition_run import RecognitionRunRepository

MODEL_REVISION = "edaa852ec7e145841d8ffdb056a99866b5f0a478"


def _install_fake_production_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    snapshot_download,
) -> None:
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(snapshot_download=snapshot_download),
    )
    monkeypatch.setitem(
        sys.modules,
        "ctranslate2",
        SimpleNamespace(get_cuda_device_count=lambda: 1),
    )
    real_version = importlib.metadata.version

    def runtime_version(distribution: str) -> str:
        versions = {
            "ctranslate2": "4.8.1",
            "faster-whisper": "1.2.1",
        }
        if distribution in versions:
            return versions[distribution]
        return real_version(distribution)

    monkeypatch.setattr(importlib.metadata, "version", runtime_version)


def _write_wav(path: Path, *, duration_seconds: float = 1.0) -> None:
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16_000)
        writer.writeframes(b"\x00\x00" * round(16_000 * duration_seconds))


def _request(tmp_path: Path, audio: Path) -> RecognitionRequest:
    return RecognitionRequest(
        episode_id="episode-fw",
        invocation_id="invocation-fw",
        normalized_audio=audio,
        expected_normalized_audio_hash=hash_file(audio),
        raw_output_dir=tmp_path / "raw",
        language_hint="zh-Hant-TW",
    )


def _observation() -> dict[str, object]:
    return {
        "language": "zh",
        "language_probability": 0.99,
        "duration": 1.0,
        "duration_after_vad": 1.0,
        "segments": [
            {
                "id": 0,
                "seek": 0,
                "start": 0.0,
                "end": 0.8,
                "text": " 約會對象",
                "tokens": [1, 2],
                "avg_logprob": -0.1,
                "compression_ratio": 1.1,
                "no_speech_prob": 0.01,
                "temperature": 0.0,
                "words": [
                    {"word": " 約", "start": 0.0, "end": 0.0, "probability": 0.9},
                    {"word": "會對象", "start": 0.1, "end": 0.8, "probability": 0.95},
                ],
            }
        ],
    }


def _adapter(runner) -> FasterWhisperRecognizerAdapter:
    return FasterWhisperRecognizerAdapter(
        model_revision=MODEL_REVISION,
        runner=runner,
        runner_runtime_components={"faster-whisper": "fixture-1"},
        runner_code_hash="a" * 64,
        runner_execution_mode="fixture",
    )


def test_faster_whisper_seals_raw_words_and_groups_zero_duration_without_epsilon(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_wav(audio)
    adapter = _adapter(lambda _audio, _request: _observation())

    evidence = adapter.recognize(_request(tmp_path, audio))

    assert [(token.text, token.start_ms, token.end_ms) for token in evidence.tokens] == [
        (" 約會對象", 0, 800)
    ]
    envelope = json.loads(next((tmp_path / "raw").glob("*.json")).read_text("utf-8"))
    assert envelope["planner"]["owned_chunk_samples"] == 174 * 16_000
    assert len(envelope["chunks"]) == 1
    grouping = envelope["chunks"][0]["timestamp_grouping"]
    assert grouping["observations"] == [
        {"index": 0, "text": " 約", "start_seconds": 0.0, "end_seconds": 0.0},
        {"index": 1, "text": "會對象", "start_seconds": 0.1, "end_seconds": 0.8},
    ]
    assert grouping["groups"][0]["source_observation_indices"] == [0, 1]
    assert grouping["groups"][0]["timing_basis"] == ("provider_word_timestamp_bounded_group")


def test_faster_whisper_preserves_one_based_provider_segment_ids(tmp_path: Path) -> None:
    audio = tmp_path / "normalized.wav"
    _write_wav(audio)
    observation = _observation()
    observation["segments"][0]["id"] = 1

    evidence = _adapter(lambda _audio, _request: observation).recognize(_request(tmp_path, audio))

    assert evidence.tokens
    envelope = json.loads(next((tmp_path / "raw").glob("*.json")).read_text("utf-8"))
    assert envelope["chunks"][0]["provider_output"]["segments"][0]["id"] == 1


def test_faster_whisper_fresh_verify_replays_raw_bytes_without_provider_call(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_wav(audio)
    request = _request(tmp_path, audio)
    evidence = _adapter(lambda _audio, _request: _observation()).recognize(request)
    raw_path = next((tmp_path / "raw").glob("*.json"))
    raw_bytes = raw_path.read_bytes()

    def forbidden_provider(_audio, _request):
        raise AssertionError("fresh replay must not call Faster-Whisper")

    fresh = _adapter(forbidden_provider)
    assert fresh.verify(evidence, request=request, raw_output=raw_bytes) == evidence

    tampered = raw_bytes.replace("約會對象".encode(), "約會對上".encode())
    tampered_hash = hashlib.sha256(tampered).hexdigest()
    forged = evidence.model_copy(
        update={
            "raw_output": evidence.raw_output.model_copy(
                update={"sha256": tampered_hash, "size_bytes": len(tampered)}
            ),
            "raw_output_hash": tampered_hash,
        }
    )
    with pytest.raises((AdapterInputError, AdapterIntegrityError)):
        fresh.verify(
            forged,
            request=request,
            raw_output=tampered,
        )


def test_faster_whisper_uses_closed_no_lexical_bias_request(tmp_path: Path) -> None:
    audio = tmp_path / "normalized.wav"
    _write_wav(audio)
    captured: dict[str, object] = {}

    class FakeModel:
        def transcribe(self, audio_path, **kwargs):
            captured["audio"] = audio_path
            captured["kwargs"] = kwargs
            segment = SimpleNamespace(
                id=0,
                seek=0,
                start=0.0,
                end=0.8,
                text=" 約會對象",
                tokens=[1, 2],
                avg_logprob=-0.1,
                compression_ratio=1.1,
                no_speech_prob=0.01,
                temperature=0.0,
                words=[SimpleNamespace(word=" 約會對象", start=0.0, end=0.8, probability=0.95)],
            )
            info = SimpleNamespace(
                language="zh",
                language_probability=0.99,
                duration=1.0,
                duration_after_vad=1.0,
            )
            return iter((segment,)), info

    adapter = FasterWhisperRecognizerAdapter(model_revision=MODEL_REVISION)
    raw = adapter._transcribe_with_model(FakeModel(), audio, _request(tmp_path, audio))

    assert raw["segments"][0]["words"][0]["word"] == " 約會對象"
    kwargs = captured["kwargs"]
    assert kwargs["language"] == "zh"
    assert kwargs["task"] == "transcribe"
    assert kwargs["initial_prompt"] is None
    assert kwargs["hotwords"] is None
    assert kwargs["prefix"] is None
    assert kwargs["condition_on_previous_text"] is False
    assert kwargs["word_timestamps"] is True
    assert kwargs["vad_filter"] is False
    assert kwargs["temperature"] == 0.0


def test_faster_whisper_identity_is_bound_to_complete_local_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = tmp_path / "snapshots" / MODEL_REVISION
    snapshot.mkdir(parents=True)
    for name, payload in {
        "config.json": b"{}",
        "model.bin": b"pinned weights",
        "preprocessor_config.json": b"{}",
        "tokenizer.json": b"{}",
        "vocabulary.json": b"{}",
    }.items():
        (snapshot / name).write_bytes(payload)

    def local_snapshot_download(**kwargs):
        assert kwargs == {
            "repo_id": "Systran/faster-whisper-large-v3",
            "revision": MODEL_REVISION,
            "local_files_only": True,
        }
        return str(snapshot)

    _install_fake_production_runtime(
        monkeypatch,
        snapshot_download=local_snapshot_download,
    )
    adapter = FasterWhisperRecognizerAdapter(
        model_revision=MODEL_REVISION,
        device="cpu",
        compute_type="float32",
    )

    identity = adapter.identity

    components = dict(identity.runtime_components)
    assert identity.model_version == MODEL_REVISION
    assert identity.execution_mode == "local"
    assert components["model_snapshot_revision"] == MODEL_REVISION
    assert len(components["model_snapshot_inventory"]) == 64
    assert components["faster_whisper"] == "1.2.1"
    assert components["ctranslate2"] == "4.8.1"


def test_faster_whisper_snapshot_identity_hashes_symlink_target_bytes_and_detects_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "models--Systran--faster-whisper-large-v3"
    snapshot = repo / "snapshots" / MODEL_REVISION
    blobs = repo / "blobs"
    snapshot.mkdir(parents=True)
    blobs.mkdir()
    for name in (
        "config.json",
        "preprocessor_config.json",
        "tokenizer.json",
        "vocabulary.json",
    ):
        (snapshot / name).write_bytes(b"{}")
    blob = blobs / "model-blob"
    blob.write_bytes(b"model-version-one")
    try:
        (snapshot / "model.bin").symlink_to(os.path.relpath(blob, start=snapshot))
    except OSError:
        pytest.skip("host does not permit unprivileged symlink creation")
    _install_fake_production_runtime(
        monkeypatch,
        snapshot_download=lambda **_kwargs: str(snapshot),
    )
    adapter = FasterWhisperRecognizerAdapter(
        model_revision=MODEL_REVISION,
        device="cpu",
        compute_type="float32",
    )
    _ = adapter.identity
    blob.write_bytes(b"model-version-two")

    audio = tmp_path / "normalized.wav"
    _write_wav(audio)
    with pytest.raises(AdapterIntegrityError, match="snapshot bytes changed"):
        adapter.recognize(_request(tmp_path, audio))


def test_faster_whisper_snapshot_rejects_symlink_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "models--Systran--faster-whisper-large-v3"
    snapshot = repo / "snapshots" / MODEL_REVISION
    (repo / "blobs").mkdir(parents=True)
    snapshot.mkdir(parents=True)
    for name in (
        "config.json",
        "preprocessor_config.json",
        "tokenizer.json",
        "vocabulary.json",
    ):
        (snapshot / name).write_bytes(b"{}")
    outside = tmp_path / "outside-model.bin"
    outside.write_bytes(b"escaped model")
    try:
        (snapshot / "model.bin").symlink_to(os.path.relpath(outside, start=snapshot))
    except OSError:
        pytest.skip("host does not permit unprivileged symlink creation")
    _install_fake_production_runtime(
        monkeypatch,
        snapshot_download=lambda **_kwargs: str(snapshot),
    )

    with pytest.raises(AdapterUnavailableError, match="escapes"):
        FasterWhisperRecognizerAdapter(
            model_revision=MODEL_REVISION,
            device="cpu",
            compute_type="float32",
        ).identity


def test_faster_whisper_local_execution_uses_only_the_measured_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_wav(audio)
    captured: dict[str, object] = {}

    class FakeWhisperModel:
        def __init__(self, model_path, **kwargs):
            captured["model_path"] = model_path
            captured["load"] = kwargs

        def transcribe(self, _audio, **kwargs):
            captured["transcribe"] = kwargs
            segment = SimpleNamespace(
                id=0,
                seek=0,
                start=0.0,
                end=0.8,
                text=" 約會對象",
                tokens=[1],
                avg_logprob=-0.1,
                compression_ratio=1.1,
                no_speech_prob=0.01,
                temperature=0.0,
                words=[SimpleNamespace(word=" 約會對象", start=0.0, end=0.8, probability=0.95)],
            )
            info = SimpleNamespace(
                language="zh",
                language_probability=0.99,
                duration=1.0,
                duration_after_vad=1.0,
            )
            return iter((segment,)), info

    adapter = FasterWhisperRecognizerAdapter(model_revision=MODEL_REVISION)
    adapter._identity_cache = adapter._make_identity(
        runtime_components=(("fixture", "1"),),
        code_hash=adapter._source_hash,
        execution_mode="local",
    )
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "model.bin").write_bytes(b"fixture-model")
    adapter._model_snapshot_path = snapshot
    adapter._model_snapshot_inventory_hash = hash_object(
        {"snapshot_inventory": adapter._snapshot_inventory(snapshot)}
    )
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=FakeWhisperModel),
    )

    evidence = adapter.recognize(_request(tmp_path, audio))

    assert evidence.tokens[0].text == " 約會對象"
    assert captured["model_path"] == str(snapshot)
    assert captured["load"] == {
        "device": "cuda",
        "device_index": 0,
        "compute_type": "float16",
        "cpu_threads": 0,
        "num_workers": 1,
        "local_files_only": True,
        "revision": MODEL_REVISION,
    }
    assert captured["transcribe"]["initial_prompt"] is None
    assert captured["transcribe"]["hotwords"] is None


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda value: value["segments"].append(value["segments"][0].copy()),
            "segment IDs",
        ),
        (
            lambda value: value["segments"][0]["words"][1].update(start=-0.1),
            "greater than or equal to 0",
        ),
        (
            lambda value: value["segments"][0]["words"][1].update(end=1.2),
            "exceeds its provider segment",
        ),
    ],
)
def test_faster_whisper_rejects_unreplayable_provider_topology(
    tmp_path: Path,
    mutate,
    message: str,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_wav(audio)
    payload = _observation()
    mutate(payload)

    with pytest.raises(AdapterInputError, match=message):
        _adapter(lambda _audio, _request: payload).recognize(_request(tmp_path, audio))


@pytest.mark.parametrize(
    ("segment_start", "segment_end", "word_start", "word_end"),
    [
        (0.12, 0.8, 0.1, 0.8),
        (0.1, 0.78, 0.1, 0.8),
    ],
)
def test_faster_whisper_accepts_one_provider_timestamp_quantum_at_segment_seam(
    tmp_path: Path,
    segment_start: float,
    segment_end: float,
    word_start: float,
    word_end: float,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_wav(audio)
    payload = _observation()
    payload["segments"][0].update(start=segment_start, end=segment_end)
    payload["segments"][0]["words"][1].update(start=word_start, end=word_end)

    evidence = _adapter(lambda _audio, _request: payload).recognize(_request(tmp_path, audio))

    assert evidence.tokens[0].text == " 約會對象"


def test_faster_whisper_accepts_first_word_that_crosses_segment_start(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_wav(audio)
    payload = _observation()
    payload["segments"][0].update(start=0.3)
    payload["segments"][0]["words"] = [
        {"word": " 約會對象", "start": 0.1, "end": 0.32, "probability": 0.95}
    ]

    evidence = _adapter(lambda _audio, _request: payload).recognize(_request(tmp_path, audio))

    assert evidence.tokens[0].text == " 約會對象"


@pytest.mark.parametrize(
    ("words", "message"),
    [
        (
            [{"word": " 約會對象", "start": 0.1, "end": 0.2, "probability": 0.95}],
            "exceeds its provider segment",
        ),
        (
            [
                {"word": " 約", "start": 0.0, "end": 0.0, "probability": 0.9},
                {"word": "會對象", "start": 0.1, "end": 0.32, "probability": 0.95},
            ],
            "exceeds its provider segment",
        ),
    ],
)
def test_faster_whisper_rejects_unanchored_early_provider_word(
    tmp_path: Path,
    words: list[dict[str, object]],
    message: str,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_wav(audio)
    payload = _observation()
    payload["segments"][0].update(start=0.3)
    payload["segments"][0]["words"] = words

    with pytest.raises(AdapterInputError, match=message):
        _adapter(lambda _audio, _request: payload).recognize(_request(tmp_path, audio))


def test_faster_whisper_recognition_run_recovers_without_recalling_provider(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_wav(audio)
    calls = 0

    def first_runner(_audio, _request):
        nonlocal calls
        calls += 1
        return _observation()

    repository = RecognitionRunRepository(tmp_path / "runs")
    first = FasterWhisperRecognizerAdapter(
        model_revision=MODEL_REVISION,
        runner=first_runner,
        runner_runtime_components={"faster-whisper": "fixture-1"},
        runner_code_hash="a" * 64,
        runner_execution_mode="fixture",
        recognition_run_repository=repository,
        logical_namespace=FasterWhisperRecognizerAdapter.RECOGNITION_RUN_NAMESPACE,
    )
    request = _request(tmp_path, audio)
    evidence = first.recognize(request)
    assert calls == 1

    def forbidden_runner(_audio, _request):
        raise AssertionError("durable Recognition run must replay without provider")

    fresh = FasterWhisperRecognizerAdapter(
        model_revision=MODEL_REVISION,
        runner=forbidden_runner,
        runner_runtime_components={"faster-whisper": "fixture-1"},
        runner_code_hash="a" * 64,
        runner_execution_mode="fixture",
        recognition_run_repository=repository,
        logical_namespace=FasterWhisperRecognizerAdapter.RECOGNITION_RUN_NAMESPACE,
    )
    assert fresh.recognize(request) == evidence


def test_faster_whisper_bounded_multichunk_resume_never_rematerializes_full_audio(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_wav(audio, duration_seconds=2.5)
    repository = RecognitionRunRepository(tmp_path / "runs")
    request = _request(tmp_path, audio)
    observed_chunk_sizes: list[int] = []
    first_calls = 0

    def observation_for(chunk: Path) -> dict[str, object]:
        with wave.open(str(chunk), "rb") as reader:
            duration = reader.getnframes() / reader.getframerate()
        payload = _observation()
        payload["duration"] = duration
        payload["duration_after_vad"] = duration
        segment = payload["segments"][0]
        segment["end"] = 0.3
        segment["text"] = " 詞"
        segment["words"] = [{"word": " 詞", "start": 0.1, "end": 0.2, "probability": 0.95}]
        return payload

    def interrupted_runner(chunk: Path, _request_value):
        nonlocal first_calls
        first_calls += 1
        observed_chunk_sizes.append(chunk.stat().st_size)
        if first_calls == 2:
            raise RuntimeError("synthetic interruption after one durable chunk")
        return observation_for(chunk)

    common = {
        "model_revision": MODEL_REVISION,
        "runner_runtime_components": {"faster-whisper": "fixture-1"},
        "runner_code_hash": "a" * 64,
        "runner_execution_mode": "fixture",
        "recognition_run_repository": repository,
        "logical_namespace": FasterWhisperRecognizerAdapter.RECOGNITION_RUN_NAMESPACE,
        "owned_chunk_seconds": 1,
        "seam_context_ms": 1,
    }
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        FasterWhisperRecognizerAdapter(
            runner=interrupted_runner,
            **common,
        ).recognize(request)
    resumed_calls = 0

    def resumed_runner(chunk: Path, _request_value):
        nonlocal resumed_calls
        resumed_calls += 1
        observed_chunk_sizes.append(chunk.stat().st_size)
        return observation_for(chunk)

    evidence = FasterWhisperRecognizerAdapter(
        runner=resumed_runner,
        **common,
    ).recognize(request)

    assert first_calls == 2
    assert resumed_calls == 2
    assert len(evidence.tokens) == 3
    assert max(observed_chunk_sizes) < audio.stat().st_size

    def forbidden_runner(_chunk: Path, _request_value):
        raise AssertionError("complete durable replay must make zero provider calls")

    replayed = FasterWhisperRecognizerAdapter(
        runner=forbidden_runner,
        **common,
    ).recognize(request)
    assert replayed == evidence
