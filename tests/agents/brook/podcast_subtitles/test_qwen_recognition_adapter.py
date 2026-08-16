from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import agents.brook.podcast_subtitles.adapters.recognition as recognition_module
from agents.brook.podcast_subtitles.adapters.recognition import (
    Qwen3ASRRecognizerAdapter,
)
from agents.brook.podcast_subtitles.hashing import canonical_json_bytes
from agents.brook.podcast_subtitles.ports import (
    AdapterInputError,
    AdapterIntegrityError,
    AdapterUnavailableError,
    RecognitionRequest,
)
from agents.brook.podcast_subtitles.recognition_run import (
    RecognitionRunIntegrityError,
    RecognitionRunRepository,
)
from agents.brook.podcast_subtitles.store import GenerationStore

_QWEN_V3_NAMESPACE = "podcast-subtitle-v2/qwen3-asr-bounded-overlap-envelope/v3"


def _request(tmp_path: Path, audio: Path) -> RecognitionRequest:
    return RecognitionRequest(
        episode_id="episode-qwen",
        invocation_id="invocation-qwen",
        normalized_audio=audio,
        expected_normalized_audio_hash=hashlib.sha256(audio.read_bytes()).hexdigest(),
        raw_output_dir=tmp_path / "raw",
        language_hint="zh",
    )


def _write_wav(path: Path, *, duration_seconds: float = 4.0, rate: int = 1_000) -> None:
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"\x00\x00" * round(duration_seconds * rate))


def _adapter(
    runner,
    *,
    model_revision: str = "snapshot-model-20260812",
    runtime: str = "fixture-runtime-v1",
    runner_code_hash: str = "a" * 64,
    owned_chunk_seconds: int = 176,
    seam_context_ms: int = 2_000,
    recognition_run_repository: RecognitionRunRepository | None = None,
    logical_namespace: str | None = None,
) -> Qwen3ASRRecognizerAdapter:
    repository_options = (
        {
            "recognition_run_repository": recognition_run_repository,
            "logical_namespace": logical_namespace,
        }
        if recognition_run_repository is not None or logical_namespace is not None
        else {}
    )
    return Qwen3ASRRecognizerAdapter(
        model_revision=model_revision,
        forced_aligner_revision="snapshot-aligner-20260812",
        owned_chunk_seconds=owned_chunk_seconds,
        seam_context_ms=seam_context_ms,
        runner=runner,
        runner_runtime_components={"fixture_runner": runtime},
        runner_code_hash=runner_code_hash,
        runner_execution_mode="fixture",
        **repository_options,
    )


def _raw() -> dict[str, object]:
    return {
        "language": "zh",
        "transcript_text": "心理健康很重要",
        "segments": [
            {
                "id": 0,
                "start": 0.1,
                "end": 0.8,
                "text": "心理健康",
                "words": [
                    {"word": "心理健康", "start": 0.1, "end": 0.8},
                ],
            },
            {
                "id": 1,
                "start": 1.2,
                "end": 1.9,
                "text": "很重要",
                "words": [
                    {"word": "很重要", "start": 1.2, "end": 1.9},
                ],
            },
        ],
        "provider_diagnostics": {"beam": 1},
    }


def test_qwen_adapter_preserves_only_real_segment_anchors_and_raw_bytes(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_wav(audio)
    calls: list[Path] = []

    def run(path: Path, _request: RecognitionRequest):
        calls.append(path)
        return _raw()

    adapter = _adapter(run)
    evidence = adapter.recognize(_request(tmp_path, audio))

    assert len(calls) == 1
    assert calls[0].name.startswith("qwen-chunk-0000-")
    assert [token.text for token in evidence.tokens] == ["心理健康", "很重要"]
    assert [(token.start_ms, token.end_ms) for token in evidence.tokens] == [
        (100, 800),
        (1200, 1900),
    ]
    assert evidence.adapter == "qwen3-asr-forced-alignment"
    assert evidence.model.endswith("@snapshot-model-20260812")
    raw_path = next((tmp_path / "raw").glob("qwen3-asr-*.json"))
    envelope = json.loads(raw_path.read_text(encoding="utf-8"))
    assert envelope["chunks"][0]["provider_output"]["provider_diagnostics"] == {"beam": 1}
    assert envelope["kind"] == "qwen3_asr_bounded_overlap_evidence"
    assert envelope["identity_hash"] == adapter.identity.content_hash
    assert evidence.raw_output.sha256 == hashlib.sha256(raw_path.read_bytes()).hexdigest()
    assert evidence.config_hash == adapter.adapter_config_hash


def test_qwen_adapter_is_deterministic_and_deduplicates_raw_artifact(tmp_path: Path) -> None:
    audio = tmp_path / "normalized.wav"
    _write_wav(audio)
    adapter = _adapter(lambda _path, _request: _raw())
    request = _request(tmp_path, audio)

    first = adapter.recognize(request)
    second = adapter.recognize(request)

    assert first == second
    assert len(tuple((tmp_path / "raw").glob("qwen3-asr-*.json"))) == 1


def test_qwen_adapter_rejects_unaligned_or_overlapping_provider_output(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_wav(audio)
    missing = _raw()
    missing["segments"][0]["words"][0].pop("start")

    with pytest.raises(AdapterInputError, match="requires exact start and end"):
        _adapter(lambda _path, _request: missing).recognize(_request(tmp_path, audio))

    overlapping = _raw()
    overlapping["segments"][1]["words"][0]["start"] = 0.7
    with pytest.raises(AdapterIntegrityError, match="overlapping merged words"):
        _adapter(lambda _path, _request: overlapping).recognize(_request(tmp_path, audio))


def test_qwen_adapter_hashes_audio_before_spending_gpu_work(tmp_path: Path) -> None:
    audio = tmp_path / "normalized.wav"
    _write_wav(audio)
    called = False

    def must_not_run(_path: Path, _request: RecognitionRequest):
        nonlocal called
        called = True
        return _raw()

    request = _request(tmp_path, audio)
    request = replace(request, expected_normalized_audio_hash="0" * 64)
    with pytest.raises(AdapterIntegrityError, match="normalized audio hash"):
        _adapter(must_not_run).recognize(request)
    assert called is False


def test_qwen_custom_runner_requires_explicit_executable_identity() -> None:
    with pytest.raises(ValueError, match="explicit runtime, code, and execution"):
        Qwen3ASRRecognizerAdapter(
            model_revision="model-revision",
            forced_aligner_revision="aligner-revision",
            runner=lambda _path, _request: _raw(),
        )


def _global_event_runner(
    events: tuple[tuple[str, float, float], ...],
    *,
    mutate_chunk: int | None = None,
    omit_chunk: int | None = None,
):
    def run(path: Path, _request: RecognitionRequest):
        parts = path.stem.split("-")
        chunk_index = int(parts[2])
        inference_start_sample = int(parts[3])
        with wave.open(str(path), "rb") as reader:
            rate = reader.getframerate()
            duration = reader.getnframes() / rate
        inference_start = inference_start_sample / rate
        words: list[dict[str, object]] = []
        for text, global_start, global_end in events:
            midpoint = (global_start + global_end) / 2
            if inference_start <= midpoint < inference_start + duration:
                if omit_chunk == chunk_index and text.startswith("接縫"):
                    continue
                observed = text
                if mutate_chunk == chunk_index and text.startswith("接縫"):
                    observed = text + "錯"
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


_LONG_EVENTS = (
    ("開頭", 10.0, 11.0),
    ("接縫甲", 175.0, 175.8),
    ("中段", 200.0, 201.0),
    ("接縫乙", 351.0, 351.8),
    ("結尾", 359.0, 359.5),
)


def _chunk_index(path: Path) -> int:
    return int(path.stem.split("-")[2])


def _repository(episode_root: Path) -> RecognitionRunRepository:
    return GenerationStore(episode_root).recognition_run_repository()


def test_qwen_checkpoint_fresh_run_and_complete_rerun_use_durable_observations(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "long.wav"
    _write_wav(audio, duration_seconds=360.0, rate=10)
    request = _request(tmp_path, audio)
    repository = _repository(tmp_path / "episode")
    fresh_calls: list[int] = []
    fresh_outputs: list[dict[str, object]] = []
    base = _global_event_runner(_LONG_EVENTS)

    def fresh_runner(path: Path, recognition_request: RecognitionRequest):
        fresh_calls.append(_chunk_index(path))
        observed = dict(base(path, recognition_request))
        fresh_outputs.append(observed)
        return observed

    first = _adapter(
        fresh_runner,
        recognition_run_repository=repository,
        logical_namespace=_QWEN_V3_NAMESPACE,
    ).recognize(request)
    first_raw = Path(first.raw_output.uri.removeprefix("file:///"))

    assert fresh_calls == [0, 1, 2]
    observation_paths = tuple(
        sorted(repository.runs_dir.glob("*/chunks/*/adapter-observation.json"))
    )
    assert len(observation_paths) == 3
    assert [path.read_bytes() for path in observation_paths] == [
        canonical_json_bytes(observed) for observed in fresh_outputs
    ]
    finalization_path = next(repository.runs_dir.glob("*/finalizations/*.json"))
    finalization = json.loads(finalization_path.read_text(encoding="utf-8"))
    assert (
        finalization["recognition_evidence_hash"]
        == hashlib.sha256(canonical_json_bytes(first)).hexdigest()
    )

    replay_calls: list[int] = []

    def must_not_call_provider(path: Path, _request: RecognitionRequest):
        replay_calls.append(_chunk_index(path))
        raise AssertionError("a complete durable run must not call the provider")

    replayed = _adapter(
        must_not_call_provider,
        recognition_run_repository=RecognitionRunRepository(repository.subtitle_root),
        logical_namespace=_QWEN_V3_NAMESPACE,
    ).recognize(request)

    assert replay_calls == []
    assert replayed == first
    assert canonical_json_bytes(replayed) == canonical_json_bytes(first)
    assert Path(replayed.raw_output.uri.removeprefix("file:///")).read_bytes() == (
        first_raw.read_bytes()
    )


def test_qwen_checkpoint_derives_only_current_chunk_before_each_provider_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = tmp_path / "long.wav"
    _write_wav(audio, duration_seconds=360.0, rate=10)
    request = _request(tmp_path, audio)
    repository = _repository(tmp_path / "episode")
    original_derive = recognition_module._derive_pcm_wav_chunk
    derive_calls: list[tuple[int, int]] = []
    provider_observed_counts: list[int] = []
    base = _global_event_runner(_LONG_EVENTS)

    def derive(path, *, topology, start_sample, end_sample):
        derive_calls.append((start_sample, end_sample))
        return original_derive(
            path,
            topology=topology,
            start_sample=start_sample,
            end_sample=end_sample,
        )

    def runner(path: Path, recognition_request: RecognitionRequest):
        provider_observed_counts.append(len(derive_calls))
        return base(path, recognition_request)

    monkeypatch.setattr(recognition_module, "_derive_pcm_wav_chunk", derive)

    _adapter(
        runner,
        recognition_run_repository=repository,
        logical_namespace=_QWEN_V3_NAMESPACE,
    ).recognize(request)

    assert provider_observed_counts[:3] == [1, 2, 3]


def test_qwen_checkpoint_complete_replay_retains_at_most_two_bounded_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = tmp_path / "long.wav"
    _write_wav(audio, duration_seconds=720.0, rate=10)
    request = _request(tmp_path, audio)
    repository = _repository(tmp_path / "episode")
    events = (*_LONG_EVENTS, ("後段", 500.0, 501.0), ("後半", 600.0, 601.0), ("收尾", 710.0, 711.0))
    base = _global_event_runner(events)
    _adapter(
        base,
        recognition_run_repository=repository,
        logical_namespace=_QWEN_V3_NAMESPACE,
    ).recognize(request)

    original_derive = recognition_module._derive_pcm_wav_chunk
    metrics = {"live": 0, "peak": 0, "calls": 0}

    class TrackedChunk(bytes):
        def __new__(cls, payload: bytes):
            instance = super().__new__(cls, payload)
            metrics["live"] += 1
            metrics["peak"] = max(metrics["peak"], metrics["live"])
            return instance

        def __del__(self) -> None:
            metrics["live"] -= 1

    def derive(path, *, topology, start_sample, end_sample):
        metrics["calls"] += 1
        return TrackedChunk(
            original_derive(
                path,
                topology=topology,
                start_sample=start_sample,
                end_sample=end_sample,
            )
        )

    monkeypatch.setattr(recognition_module, "_derive_pcm_wav_chunk", derive)
    provider_calls: list[Path] = []

    def must_not_call(path: Path, _request: RecognitionRequest):
        provider_calls.append(path)
        raise AssertionError("complete replay must not call provider")

    replayed = _adapter(
        must_not_call,
        recognition_run_repository=RecognitionRunRepository(repository.subtitle_root),
        logical_namespace=_QWEN_V3_NAMESPACE,
    ).recognize(request)

    assert replayed.tokens
    assert provider_calls == []
    assert metrics["calls"] >= 5
    assert metrics["live"] == 0
    assert metrics["peak"] <= 2


def test_qwen_checkpoint_resumes_only_after_exact_durable_prefix_and_matches_uninterrupted(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "long.wav"
    _write_wav(audio, duration_seconds=360.0, rate=10)
    request = _request(tmp_path, audio)
    base = _global_event_runner(_LONG_EVENTS)
    uninterrupted = _adapter(base).recognize(request)
    uninterrupted_raw = Path(uninterrupted.raw_output.uri.removeprefix("file:///")).read_bytes()

    repository = _repository(tmp_path / "resumed-episode")
    failed_calls: list[int] = []

    def fail_after_prefix(path: Path, recognition_request: RecognitionRequest):
        index = _chunk_index(path)
        failed_calls.append(index)
        if index == 1:
            raise RuntimeError("fixture crash after one durable chunk")
        return base(path, recognition_request)

    with pytest.raises(AdapterUnavailableError, match="recognition failed"):
        _adapter(
            fail_after_prefix,
            recognition_run_repository=repository,
            logical_namespace=_QWEN_V3_NAMESPACE,
        ).recognize(request)

    assert failed_calls == [0, 1]
    pointer = json.loads(
        next(repository.runs_dir.glob("*/active-head.json")).read_text(encoding="utf-8")
    )
    assert pointer["completed_prefix"] == 1

    resumed_calls: list[int] = []

    def resume_runner(path: Path, recognition_request: RecognitionRequest):
        resumed_calls.append(_chunk_index(path))
        return base(path, recognition_request)

    resumed = _adapter(
        resume_runner,
        recognition_run_repository=RecognitionRunRepository(repository.subtitle_root),
        logical_namespace=_QWEN_V3_NAMESPACE,
    ).recognize(request)

    assert resumed_calls == [1, 2]
    assert canonical_json_bytes(resumed) == canonical_json_bytes(uninterrupted)
    assert Path(resumed.raw_output.uri.removeprefix("file:///")).read_bytes() == uninterrupted_raw


@pytest.mark.parametrize(
    "changed",
    [
        {"model_revision": "snapshot-model-20260813"},
        {"runtime": "fixture-runtime-v2"},
        {"runner_code_hash": "b" * 64},
        {"owned_chunk_seconds": 170},
    ],
)
def test_qwen_checkpoint_runtime_model_or_config_drift_fails_before_provider_call(
    tmp_path: Path,
    changed: dict[str, object],
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_wav(audio)
    request = _request(tmp_path, audio)
    repository = _repository(tmp_path / "episode")
    _adapter(
        lambda _path, _request: _raw(),
        recognition_run_repository=repository,
        logical_namespace=_QWEN_V3_NAMESPACE,
    ).recognize(request)
    calls: list[Path] = []

    def must_not_call(path: Path, _request: RecognitionRequest):
        calls.append(path)
        raise AssertionError("identity drift must fail before provider execution")

    with pytest.raises(RecognitionRunIntegrityError, match="different complete plan|drifted"):
        _adapter(
            must_not_call,
            recognition_run_repository=RecognitionRunRepository(repository.subtitle_root),
            logical_namespace=_QWEN_V3_NAMESPACE,
            **changed,
        ).recognize(request)

    assert calls == []


@pytest.mark.parametrize("corruption", ["slot", "pointer", "finalization"])
def test_qwen_checkpoint_corruption_fails_closed_without_provider_call(
    tmp_path: Path,
    corruption: str,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_wav(audio)
    request = _request(tmp_path, audio)
    repository = _repository(tmp_path / "episode")
    _adapter(
        lambda _path, _request: _raw(),
        recognition_run_repository=repository,
        logical_namespace=_QWEN_V3_NAMESPACE,
    ).recognize(request)
    run_directory = next(repository.runs_dir.glob("recognition-run-*"))
    targets = {
        "slot": run_directory / "chunks" / "000000" / "adapter-observation.json",
        "pointer": run_directory / "active-head.json",
        "finalization": next((run_directory / "finalizations").glob("*.json")),
    }
    targets[corruption].write_bytes(targets[corruption].read_bytes() + b" ")
    calls: list[Path] = []

    def must_not_call(path: Path, _request: RecognitionRequest):
        calls.append(path)
        raise AssertionError("corrupt durable state must fail before provider execution")

    with pytest.raises(RecognitionRunIntegrityError):
        _adapter(
            must_not_call,
            recognition_run_repository=RecognitionRunRepository(repository.subtitle_root),
            logical_namespace=_QWEN_V3_NAMESPACE,
        ).recognize(request)

    assert calls == []


def test_qwen_checkpoint_repairs_finalization_publication_tail_without_provider_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_wav(audio)
    request = _request(tmp_path, audio)
    repository = _repository(tmp_path / "episode")
    provider_calls: list[Path] = []
    original_publish = repository._publish_active_pointer
    crashed = False

    def crash_before_terminal_pointer(directory, pointer):
        nonlocal crashed
        if pointer.finalization_id is not None and not crashed:
            crashed = True
            raise OSError("fixture crash after immutable finalization publication")
        return original_publish(directory, pointer)

    monkeypatch.setattr(repository, "_publish_active_pointer", crash_before_terminal_pointer)

    def runner(path: Path, _request: RecognitionRequest):
        provider_calls.append(path)
        return _raw()

    with pytest.raises(OSError, match="immutable finalization"):
        _adapter(
            runner,
            recognition_run_repository=repository,
            logical_namespace=_QWEN_V3_NAMESPACE,
        ).recognize(request)
    assert len(provider_calls) == 1

    replay_calls: list[Path] = []

    def must_not_call(path: Path, _request: RecognitionRequest):
        replay_calls.append(path)
        raise AssertionError("finalization repair must remain disk-only")

    replayed = _adapter(
        must_not_call,
        recognition_run_repository=RecognitionRunRepository(repository.subtitle_root),
        logical_namespace=_QWEN_V3_NAMESPACE,
    ).recognize(request)

    assert replay_calls == []
    assert replayed.tokens
    pointer = json.loads(
        next(repository.runs_dir.glob("*/active-head.json")).read_text(encoding="utf-8")
    )
    assert pointer["finalization_id"] is not None


def test_qwen_checkpoint_execution_lease_serializes_independent_adapters(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_wav(audio)
    request = _request(tmp_path, audio)
    repository = _repository(tmp_path / "episode")
    provider_started = threading.Event()
    release_provider = threading.Event()
    calls: list[Path] = []
    calls_lock = threading.Lock()

    def runner(path: Path, _request: RecognitionRequest):
        with calls_lock:
            calls.append(path)
        provider_started.set()
        if not release_provider.wait(timeout=5):
            raise RuntimeError("test did not release provider")
        return _raw()

    first = _adapter(
        runner,
        recognition_run_repository=repository,
        logical_namespace=_QWEN_V3_NAMESPACE,
    )
    second = _adapter(
        runner,
        recognition_run_repository=RecognitionRunRepository(repository.subtitle_root),
        logical_namespace=_QWEN_V3_NAMESPACE,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(first.recognize, request)
        assert provider_started.wait(timeout=5)
        second_future = pool.submit(second.recognize, request)
        time.sleep(0.1)
        release_provider.set()
        first_evidence = first_future.result(timeout=10)
        second_evidence = second_future.result(timeout=10)

    assert len(calls) == 1
    assert second_evidence == first_evidence


def test_qwen_checkpoint_terminal_rejection_replays_without_provider_or_raw_error(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "long.wav"
    _write_wav(audio, duration_seconds=360.0, rate=10)
    request = _request(tmp_path, audio)
    repository = _repository(tmp_path / "episode")
    first_calls: list[int] = []
    conflicted = _global_event_runner(_LONG_EVENTS, mutate_chunk=1)

    def first_runner(path: Path, recognition_request: RecognitionRequest):
        first_calls.append(_chunk_index(path))
        return conflicted(path, recognition_request)

    with pytest.raises(AdapterIntegrityError, match="seam hypotheses conflict"):
        _adapter(
            first_runner,
            recognition_run_repository=repository,
            logical_namespace=_QWEN_V3_NAMESPACE,
        ).recognize(request)

    assert first_calls == [0, 1, 2]
    finalization_path = next(repository.runs_dir.glob("*/finalizations/*.json"))
    finalization = json.loads(finalization_path.read_text(encoding="utf-8"))
    assert finalization["status"] == "rejected"
    assert finalization["failure"]["code"] == "seam_conflict"
    assert finalization["failure"]["diagnostics"] == [
        ["failure_class", "AdapterIntegrityError"],
        ["stage", "qwen_envelope_v3_replay"],
    ]
    assert "seam hypotheses conflict" not in finalization_path.read_text(encoding="utf-8")

    replay_calls: list[int] = []

    def must_not_call(path: Path, _request: RecognitionRequest):
        replay_calls.append(_chunk_index(path))
        raise AssertionError("terminal rejection must never call the provider again")

    with pytest.raises(AdapterIntegrityError, match="terminally rejected: seam_conflict"):
        _adapter(
            must_not_call,
            recognition_run_repository=RecognitionRunRepository(repository.subtitle_root),
            logical_namespace=_QWEN_V3_NAMESPACE,
        ).recognize(request)

    assert replay_calls == []
    assert len(tuple(finalization_path.parent.glob("*.json"))) == 1


def test_qwen_checkpoint_requires_repository_and_namespace_as_one_explicit_pair(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path / "episode")

    with pytest.raises(ValueError, match="repository.*logical_namespace"):
        _adapter(
            lambda _path, _request: _raw(),
            recognition_run_repository=repository,
        )


def test_qwen_long_audio_has_exact_primary_coverage_and_overlap_seam_proof(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "long.wav"
    _write_wav(audio, duration_seconds=360.0, rate=10)
    adapter = _adapter(_global_event_runner(_LONG_EVENTS))

    evidence = adapter.recognize(_request(tmp_path, audio))
    envelope = json.loads(next((tmp_path / "raw").glob("*.json")).read_text("utf-8"))

    chunks = envelope["chunks"]
    assert len(chunks) == 3
    assert chunks[0]["owned_start_sample"] == 0
    assert chunks[-1]["owned_end_sample"] == 3_600
    assert [chunk["index"] for chunk in chunks] == [0, 1, 2]
    assert all(
        left["owned_end_sample"] == right["owned_start_sample"]
        for left, right in zip(chunks, chunks[1:])
    )
    assert all(
        left["inference_end_sample"] > right["inference_start_sample"]
        for left, right in zip(chunks, chunks[1:])
    )
    assert [seam["status"] for seam in envelope["seams"]] == ["matched", "matched"]
    assert [token.text for token in evidence.tokens] == [
        "開頭",
        "接縫甲",
        "中段",
        "接縫乙",
        "結尾",
    ]


@pytest.mark.parametrize(
    ("runner", "message"),
    [
        (_global_event_runner(_LONG_EVENTS, mutate_chunk=1), "seam hypotheses conflict"),
        (_global_event_runner(_LONG_EVENTS, omit_chunk=1), "seam hypotheses conflict"),
    ],
)
def test_qwen_seam_disagreement_or_omission_fails_closed(
    tmp_path: Path,
    runner,
    message: str,
) -> None:
    audio = tmp_path / "long.wav"
    _write_wav(audio, duration_seconds=360.0, rate=10)

    with pytest.raises(AdapterIntegrityError, match=message):
        _adapter(runner).recognize(_request(tmp_path, audio))
    assert tuple((tmp_path / "raw").glob("*.json")), "conflict envelope remains auditable"


def _tampered_evidence(evidence, raw_bytes: bytes):
    digest = hashlib.sha256(raw_bytes).hexdigest()
    return evidence.model_copy(
        update={
            "raw_output": evidence.raw_output.model_copy(
                update={"sha256": digest, "size_bytes": len(raw_bytes)}
            ),
            "raw_output_hash": digest,
        }
    )


@pytest.mark.parametrize("mutation", ["reorder", "duplicate", "gap", "overlap"])
def test_qwen_replay_rejects_chunk_topology_mutation(
    tmp_path: Path,
    mutation: str,
) -> None:
    audio = tmp_path / "long.wav"
    _write_wav(audio, duration_seconds=360.0, rate=10)
    adapter = _adapter(_global_event_runner(_LONG_EVENTS))
    request = _request(tmp_path, audio)
    evidence = adapter.recognize(request)
    raw_path = next((tmp_path / "raw").glob("*.json"))
    envelope = json.loads(raw_path.read_text("utf-8"))
    if mutation == "reorder":
        envelope["chunks"][0], envelope["chunks"][1] = (
            envelope["chunks"][1],
            envelope["chunks"][0],
        )
    elif mutation == "duplicate":
        envelope["chunks"][1] = envelope["chunks"][0]
    elif mutation == "gap":
        envelope["chunks"][1]["owned_start_sample"] += 1
    else:
        envelope["chunks"][1]["owned_start_sample"] -= 1
    raw_bytes = json.dumps(
        envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")

    with pytest.raises(
        AdapterIntegrityError,
        match="reordered, duplicated, gapped, or overlapping",
    ):
        adapter.verify(
            _tampered_evidence(evidence, raw_bytes),
            request=request,
            raw_output=raw_bytes,
        )


def test_qwen_replay_rejects_raw_tamper_before_derivation(tmp_path: Path) -> None:
    audio = tmp_path / "normalized.wav"
    _write_wav(audio)
    adapter = _adapter(lambda _path, _request: _raw())
    request = _request(tmp_path, audio)
    evidence = adapter.recognize(request)
    raw = next((tmp_path / "raw").glob("*.json")).read_bytes() + b" "

    with pytest.raises(AdapterIntegrityError, match="raw envelope digest"):
        adapter.verify(evidence, request=request, raw_output=raw)


def test_qwen_projects_exact_mixed_transcript_without_fabricating_anchors_and_replays(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_wav(audio)
    aligned = ("心", "理", "Traveling", "Village", "GPT", "56")
    provider = {
        "language": "zh",
        "transcript_text": "「心理，Traveling Village；GPT 5.6！」",
        "segments": [
            {
                "id": index,
                "start": round(0.1 + index * 0.2, 1),
                "end": round(0.3 + index * 0.2, 1),
                "text": text,
                "words": [
                    {
                        "word": text,
                        "start": round(0.1 + index * 0.2, 1),
                        "end": round(0.3 + index * 0.2, 1),
                    }
                ],
            }
            for index, text in enumerate(aligned)
        ],
    }
    adapter = _adapter(lambda _path, _request: provider)
    request = _request(tmp_path, audio)

    evidence = adapter.recognize(request)
    raw_path = next((tmp_path / "raw").glob("*.json"))
    envelope = json.loads(raw_path.read_text("utf-8"))
    items = envelope["chunks"][0]["alignment_items"]

    assert [token.text for token in evidence.tokens] == [
        "心",
        "理",
        "Traveling ",
        "Village",
        "GPT ",
        "56",
    ]
    assert [item["aligned_text"] for item in items] == list(aligned)
    assert "".join(token.text for token in evidence.tokens) == ("心理Traveling VillageGPT 56")
    assert envelope["chunks"][0]["transcript_text"] == provider["transcript_text"]
    separators = envelope["chunks"][0]["separators"]
    assert "".join(separator["text"] for separator in separators) == "「， ； .！」"
    assert [
        separator["text"]
        for separator in separators
        if separator["classification"] == "retained_orthographic_whitespace"
    ] == [" ", " "]
    assert all(
        separator["attached_alignment_index"] is None
        for separator in separators
        if separator["classification"] == "provider_only_separator"
    )
    assert [(item["local_start_seconds"], item["local_end_seconds"]) for item in items] == [
        (0.1, 0.3),
        (0.3, 0.5),
        (0.5, 0.7),
        (0.7, 0.9),
        (0.9, 1.1),
        (1.1, 1.3),
    ]
    raw_bytes = raw_path.read_bytes()
    assert adapter.verify(evidence, request=request, raw_output=raw_bytes) == evidence

    separators[0]["text"] = "（"
    tampered = json.dumps(
        envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    with pytest.raises(AdapterInputError, match="separator classification does not replay"):
        adapter.verify(
            _tampered_evidence(evidence, tampered),
            request=request,
            raw_output=tampered,
        )


def test_qwen_rejects_zero_duration_alignment_without_fabricated_timing(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_wav(audio)
    provider = _raw()
    provider["segments"][0]["words"][0]["end"] = 0.1

    with pytest.raises(AdapterIntegrityError, match="zero or negative duration"):
        _adapter(lambda _path, _request: provider).recognize(_request(tmp_path, audio))


@pytest.mark.parametrize(
    "changed",
    [
        {"model_revision": "different-model"},
        {"runtime": "different-runtime"},
        {"runner_code_hash": "b" * 64},
        {"owned_chunk_seconds": 170},
    ],
)
def test_qwen_fresh_adapter_rejects_model_runtime_code_or_config_drift(
    tmp_path: Path,
    changed: dict[str, object],
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_wav(audio)

    def runner(_path, _request):
        return _raw()

    adapter = _adapter(runner)
    request = _request(tmp_path, audio)
    evidence = adapter.recognize(request)
    raw = next((tmp_path / "raw").glob("*.json")).read_bytes()
    drifted = _adapter(runner, **changed)

    with pytest.raises(AdapterIntegrityError, match="executable identity mismatch"):
        drifted.verify(evidence, request=request, raw_output=raw)


@pytest.mark.parametrize("revision", ["", " untrimmed "])
def test_qwen_adapter_requires_explicit_pinned_revisions(revision: str) -> None:
    with pytest.raises(ValueError, match="explicit non-blank"):
        Qwen3ASRRecognizerAdapter(
            model_revision=revision,
            forced_aligner_revision="aligner-revision",
            runner=lambda _path, _request: _raw(),
        )


def test_qwen_local_snapshot_preflight_resolves_exact_commit_without_loading_weights(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = "a" * 40
    snapshot = tmp_path / "snapshots" / revision
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    (snapshot / "model.safetensors").write_bytes(b"cached-weights")
    calls: list[dict[str, object]] = []

    def snapshot_download(**kwargs):
        calls.append(kwargs)
        return str(snapshot)

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(snapshot_download=snapshot_download),
    )

    resolved_path, resolved, inventory_hash = Qwen3ASRRecognizerAdapter._measure_local_snapshot(
        repo_id="Qwen/model",
        revision=revision,
        label="model",
    )

    assert resolved_path == snapshot.resolve()
    assert resolved == revision
    assert len(inventory_hash) == 64
    assert calls == [
        {
            "repo_id": "Qwen/model",
            "revision": revision,
            "local_files_only": True,
        }
    ]


@pytest.mark.parametrize(
    ("revision", "failure"),
    [
        ("main", "immutable 40-character lowercase commit SHA"),
        ("b" * 40, "has no config.json"),
    ],
)
def test_qwen_local_snapshot_preflight_rejects_unpinned_or_incomplete_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    revision: str,
    failure: str,
) -> None:
    snapshot = tmp_path / "snapshots" / revision
    snapshot.mkdir(parents=True)
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(snapshot_download=lambda **_kwargs: str(snapshot)),
    )

    with pytest.raises(AdapterUnavailableError, match=failure):
        Qwen3ASRRecognizerAdapter._measure_local_snapshot(
            repo_id="Qwen/model",
            revision=revision,
            label="model",
        )


def test_qwen_loader_uses_measured_local_snapshots_without_hub_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_revision = "a" * 40
    aligner_revision = "b" * 40
    model_snapshot = tmp_path / "model" / "snapshots" / model_revision
    aligner_snapshot = tmp_path / "aligner" / "snapshots" / aligner_revision
    for snapshot in (model_snapshot, aligner_snapshot):
        snapshot.mkdir(parents=True)
        (snapshot / "config.json").write_text("{}", encoding="utf-8")
        (snapshot / "model.safetensors").write_bytes(b"cached-weights")

    snapshot_calls: list[dict[str, object]] = []

    def snapshot_download(**kwargs):
        snapshot_calls.append(kwargs)
        assert kwargs["local_files_only"] is True
        return str(
            model_snapshot if kwargs["repo_id"] == "Qwen/Qwen3-ASR-1.7B" else aligner_snapshot
        )

    def poison_hub(*args, **kwargs):
        raise AssertionError(f"Hub access is forbidden: {args!r} {kwargs!r}")

    captured: dict[str, object] = {}

    class FakeModel:
        @classmethod
        def from_pretrained(cls, model_path, **kwargs):
            captured["model_path"] = model_path
            captured["kwargs"] = kwargs
            if not Path(model_path).is_dir():
                poison_hub("model", model_path)
            if not Path(kwargs["forced_aligner"]).is_dir():
                poison_hub("forced-aligner", kwargs["forced_aligner"])
            return cls()

    class PoisonHfApi:
        model_info = poison_hub

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(
            HfApi=PoisonHfApi,
            hf_hub_download=poison_hub,
            model_info=poison_hub,
            snapshot_download=snapshot_download,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            __version__="fixture-torch",
            float32=object(),
            version=SimpleNamespace(cuda=None),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "qwen_asr",
        SimpleNamespace(__version__="fixture-qwen", Qwen3ASRModel=FakeModel),
    )
    adapter = Qwen3ASRRecognizerAdapter(
        model_revision=model_revision,
        forced_aligner_revision=aligner_revision,
        device="cpu",
        dtype="float32",
    )

    identity = adapter.identity
    loaded = adapter._load_qwen_model()

    assert isinstance(loaded, FakeModel)
    assert Path(captured["model_path"]) == model_snapshot
    assert Path(captured["kwargs"]["forced_aligner"]) == aligner_snapshot
    assert identity.model == "Qwen/Qwen3-ASR-1.7B"
    assert identity.model_version == model_revision
    assert identity.aligner == "Qwen/Qwen3-ForcedAligner-0.6B"
    assert identity.aligner_version == aligner_revision
    runtime = dict(identity.runtime_components)
    assert len(runtime["model_snapshot_inventory"]) == 64
    assert len(runtime["aligner_snapshot_inventory"]) == 64
    assert snapshot_calls == [
        {
            "repo_id": "Qwen/Qwen3-ASR-1.7B",
            "revision": model_revision,
            "local_files_only": True,
        },
        {
            "repo_id": "Qwen/Qwen3-ForcedAligner-0.6B",
            "revision": aligner_revision,
            "local_files_only": True,
        },
    ]


def test_qwen_snapshot_identity_hashes_symlink_blob_and_detects_preload_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = "a" * 40
    repo = tmp_path / "models--Qwen--model"
    snapshot = repo / "snapshots" / revision
    blobs = repo / "blobs"
    snapshot.mkdir(parents=True)
    blobs.mkdir()
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    blob = blobs / "weights"
    blob.write_bytes(b"qwen-weights-one")
    try:
        (snapshot / "model.safetensors").symlink_to(os.path.relpath(blob, start=snapshot))
    except OSError:
        pytest.skip("host does not permit unprivileged symlink creation")
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(snapshot_download=lambda **_kwargs: str(snapshot)),
    )
    resolved, _, inventory = Qwen3ASRRecognizerAdapter._measure_local_snapshot(
        repo_id="Qwen/model",
        revision=revision,
        label="model",
    )
    assert resolved == snapshot.resolve()
    assert len(inventory) == 64

    adapter = _adapter(lambda _path, _request: _raw())
    adapter._model_snapshot_path = snapshot.resolve()
    adapter._forced_aligner_snapshot_path = snapshot.resolve()
    adapter._model_snapshot_inventory_hash = inventory
    adapter._forced_aligner_snapshot_inventory_hash = inventory
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(float32=object(), bfloat16=object()),
    )
    monkeypatch.setitem(
        sys.modules,
        "qwen_asr",
        SimpleNamespace(
            Qwen3ASRModel=SimpleNamespace(from_pretrained=lambda *_args, **_kwargs: object())
        ),
    )
    blob.write_bytes(b"qwen-weights-two")
    with pytest.raises(AdapterIntegrityError, match="snapshot bytes changed"):
        adapter._load_qwen_model()


def test_qwen_production_contract_reads_forced_align_result_items_and_maps_language(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "normalized.wav"
    audio.write_bytes(b"audio")
    captured: dict[str, object] = {}

    class FakeModel:
        def transcribe(self, **kwargs):
            captured["transcribe"] = kwargs
            return [
                SimpleNamespace(
                    language="Chinese",
                    text="心理健康",
                    time_stamps=SimpleNamespace(
                        items=[
                            SimpleNamespace(text="心理", start_time=0.1, end_time=0.4),
                            SimpleNamespace(text="健康", start_time=0.4, end_time=0.8),
                        ]
                    ),
                )
            ]

    adapter = Qwen3ASRRecognizerAdapter(
        model_revision="model-revision",
        forced_aligner_revision="aligner-revision",
    )

    raw = adapter._transcribe_qwen_chunk(FakeModel(), audio, _request(tmp_path, audio))

    assert captured["transcribe"]["language"] == "Chinese"
    assert captured["transcribe"]["context"] == ""
    assert raw["transcript_text"] == "心理健康"
    assert [segment["text"] for segment in raw["segments"]] == ["心理", "健康"]
    grouping = raw["alignment_grouping"]
    assert [group["timing_basis"] for group in grouping["groups"]] == [
        "forced_alignment_exact",
        "forced_alignment_exact",
    ]


@pytest.mark.parametrize(
    ("items", "expected_groups"),
    [
        (
            [
                ("甲", 0.08, 0.16),
                ("一", 0.16, 0.16),
                ("乙", 0.24, 0.32),
            ],
            [
                ("甲", 0.08, 0.16, (0,), "forced_alignment_exact"),
                ("一乙", 0.16, 0.32, (1, 2), "forced_alignment_bounded_group"),
            ],
        ),
        (
            [
                ("首", 0.0, 0.0),
                ("連", 0.0, 0.0),
                ("正", 0.08, 0.16),
                ("尾", 0.16, 0.16),
                ("末", 0.24, 0.24),
            ],
            [
                ("首連正尾末", 0.0, 0.24, (0, 1, 2, 3, 4), "forced_alignment_bounded_group"),
            ],
        ),
    ],
)
def test_qwen_zero_duration_observations_become_explicit_bounded_groups(
    tmp_path: Path,
    items: list[tuple[str, float, float]],
    expected_groups: list[tuple[str, float, float, tuple[int, ...], str]],
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_wav(audio)

    class FakeModel:
        def transcribe(self, **_kwargs):
            return [
                SimpleNamespace(
                    language="Chinese",
                    text="".join(text for text, _start, _end in items),
                    time_stamps=SimpleNamespace(
                        items=[
                            SimpleNamespace(text=text, start_time=start, end_time=end)
                            for text, start, end in items
                        ]
                    ),
                )
            ]

    production_adapter = Qwen3ASRRecognizerAdapter(
        model_revision="model-revision",
        forced_aligner_revision="aligner-revision",
    )
    raw = production_adapter._transcribe_qwen_chunk(FakeModel(), audio, _request(tmp_path, audio))
    grouping = raw["alignment_grouping"]

    assert [
        (
            group["text"],
            group["start_seconds"],
            group["end_seconds"],
            tuple(group["source_observation_indices"]),
            group["timing_basis"],
        )
        for group in grouping["groups"]
    ] == expected_groups
    assert [
        (item["text"], item["start_seconds"], item["end_seconds"])
        for item in grouping["observations"]
    ] == items

    adapter = _adapter(lambda _path, _request: raw)
    evidence = adapter.recognize(_request(tmp_path, audio))
    assert [token.text for token in evidence.tokens] == [group[0] for group in expected_groups]
    assert [(token.start_ms, token.end_ms) for token in evidence.tokens] == [
        (round(group[1] * 1_000), round(group[2] * 1_000)) for group in expected_groups
    ]
    envelope = json.loads(next((tmp_path / "raw").glob("*.json")).read_text("utf-8"))
    assert envelope["chunks"][0]["provider_output"]["alignment_grouping"] == grouping


@pytest.mark.parametrize(
    "items",
    [
        [("甲", 0.08, 0.08), ("乙", 0.16, 0.16)],
        [("甲", 0.16, 0.24), ("乙", 0.08, 0.08)],
        [("甲", 0.08, 0.24), ("乙", 0.16, 0.32)],
    ],
)
def test_qwen_zero_duration_without_anchor_or_nonmonotonic_sequence_fails_closed(
    tmp_path: Path,
    items: list[tuple[str, float, float]],
) -> None:
    audio = tmp_path / "normalized.wav"
    _write_wav(audio)

    class FakeModel:
        def transcribe(self, **_kwargs):
            return [
                SimpleNamespace(
                    language="Chinese",
                    text="".join(text for text, _start, _end in items),
                    time_stamps=SimpleNamespace(
                        items=[
                            SimpleNamespace(text=text, start_time=start, end_time=end)
                            for text, start, end in items
                        ]
                    ),
                )
            ]

    adapter = Qwen3ASRRecognizerAdapter(
        model_revision="model-revision",
        forced_aligner_revision="aligner-revision",
    )
    with pytest.raises(
        AdapterIntegrityError,
        match="positive-duration anchor|observation sequence",
    ):
        adapter._transcribe_qwen_chunk(FakeModel(), audio, _request(tmp_path, audio))


def test_qwen_replay_rejects_bounded_alignment_grouping_tamper(tmp_path: Path) -> None:
    audio = tmp_path / "normalized.wav"
    _write_wav(audio)

    class FakeModel:
        def transcribe(self, **_kwargs):
            return [
                SimpleNamespace(
                    language="Chinese",
                    text="甲一乙",
                    time_stamps=SimpleNamespace(
                        items=[
                            SimpleNamespace(text="甲", start_time=0.08, end_time=0.16),
                            SimpleNamespace(text="一", start_time=0.16, end_time=0.16),
                            SimpleNamespace(text="乙", start_time=0.24, end_time=0.32),
                        ]
                    ),
                )
            ]

    production_adapter = Qwen3ASRRecognizerAdapter(
        model_revision="model-revision",
        forced_aligner_revision="aligner-revision",
    )
    raw = production_adapter._transcribe_qwen_chunk(FakeModel(), audio, _request(tmp_path, audio))
    adapter = _adapter(lambda _path, _request: raw)
    request = _request(tmp_path, audio)
    evidence = adapter.recognize(request)
    envelope = json.loads(next((tmp_path / "raw").glob("*.json")).read_text("utf-8"))
    envelope["chunks"][0]["provider_output"]["alignment_grouping"]["groups"][1][
        "source_observation_indices"
    ] = [2]
    tampered = json.dumps(
        envelope,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    with pytest.raises(AdapterInputError, match="alignment grouping receipt"):
        adapter.verify(
            _tampered_evidence(evidence, tampered),
            request=request,
            raw_output=tampered,
        )


def test_qwen_production_contract_rejects_alignment_that_drops_provider_text(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "normalized.wav"
    audio.write_bytes(b"audio")

    class FakeModel:
        def transcribe(self, **_kwargs):
            return [
                SimpleNamespace(
                    language="Chinese",
                    text="心理健康",
                    time_stamps=SimpleNamespace(
                        items=[
                            SimpleNamespace(text="心理", start_time=0.1, end_time=0.4),
                        ]
                    ),
                )
            ]

    adapter = Qwen3ASRRecognizerAdapter(
        model_revision="model-revision",
        forced_aligner_revision="aligner-revision",
    )

    with pytest.raises(AdapterIntegrityError, match="lexical mismatch"):
        adapter._transcribe_qwen_chunk(FakeModel(), audio, _request(tmp_path, audio))
