from __future__ import annotations

import hashlib
import multiprocessing
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.errors import ArtifactHashMismatchError
from agents.brook.podcast_subtitles.store import GenerationStore


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _capture_in_process(root: str, source: str, gate, results) -> None:
    gate.wait()
    store = GenerationStore(root)
    with store.snapshot_audio(source) as snapshot:
        stored = snapshot.commit()
    results.put((stored.sha256, stored.size_bytes, str(stored.path)))


def test_audio_snapshot_survives_external_file_removal(tmp_path: Path) -> None:
    payload = (b"source-audio-frame" * 1024) + b"tail"
    external = tmp_path / "source.wav"
    external.write_bytes(payload)
    store = GenerationStore(tmp_path)

    with store.snapshot_audio(external) as snapshot:
        assert snapshot.sha256 == _sha256(payload)
        assert snapshot.size_bytes == len(payload)
        stored = snapshot.commit()

    external.unlink()
    recovered = store.audio_path(stored.sha256, size_bytes=stored.size_bytes)

    assert recovered == stored.path
    assert recovered.read_bytes() == payload


def test_audio_cas_tamper_fails_closed(tmp_path: Path) -> None:
    external = tmp_path / "normalized.wav"
    external.write_bytes(b"normalized-audio")
    store = GenerationStore(tmp_path)
    with store.snapshot_audio(external) as snapshot:
        stored = snapshot.commit()

    stored.path.write_bytes(b"tampered")

    with pytest.raises(ArtifactHashMismatchError, match="identity"):
        store.audio_path(stored.sha256, size_bytes=stored.size_bytes)


def test_same_audio_bytes_are_deduplicated_idempotently(tmp_path: Path) -> None:
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    first.write_bytes(b"same-audio-bytes")
    second.write_bytes(b"same-audio-bytes")
    store = GenerationStore(tmp_path)

    with store.snapshot_audio(first) as snapshot:
        first_stored = snapshot.commit()
    with store.snapshot_audio(second) as snapshot:
        second_stored = snapshot.commit()

    assert first_stored == second_stored
    assert tuple(path.name for path in store.audio_dir.iterdir()) == (first_stored.sha256,)
    assert not tuple(store.audio_staging_dir.iterdir())


def test_concurrent_processes_commit_one_verified_audio_artifact(tmp_path: Path) -> None:
    source = tmp_path / "large.wav"
    payload = b"concurrent-audio-frame" * 8192
    source.write_bytes(payload)
    context = multiprocessing.get_context("spawn")
    gate = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_capture_in_process,
            args=(str(tmp_path), str(source), gate, results),
        )
        for _ in range(4)
    ]

    for process in processes:
        process.start()
    gate.set()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    identities = {results.get(timeout=2) for _ in processes}
    assert len(identities) == 1
    sha256, size_bytes, _path = identities.pop()
    recovered = GenerationStore(tmp_path).audio_path(sha256, size_bytes=size_bytes)
    assert recovered.read_bytes() == payload
