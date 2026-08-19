"""V2 handoff behavior at the audio-prep boundary."""

from __future__ import annotations

import wave
from pathlib import Path

from agents.brook.podcast_subtitles.adapters.normalized_handoff import (
    VerifiedNormalizedAudioHandoffAdapter,
)
from agents.brook.podcast_subtitles.hashing import hash_file
from agents.brook.podcast_subtitles.ports import NormalizeRequest
from scripts import run_audio_prep


def _wav(path: Path, duration_ms: int = 1_000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as target:
        target.setnchannels(2)
        target.setsampwidth(2)
        target.setframerate(48_000)
        target.writeframes(b"\0" * (48_000 * duration_ms // 1_000) * 4)
    return path


def test_audio_prep_preserves_program_clock_and_emits_v2_handoff(
    tmp_path: Path, monkeypatch
) -> None:
    episode = tmp_path / "moboo"
    source = _wav(episode / "Audio" / "Live-Mix.wav")
    processed = _wav(tmp_path / "auphonic-output.wav")
    monkeypatch.setattr(run_audio_prep, "_align_trim", lambda *_args: processed)
    monkeypatch.setattr(run_audio_prep, "_get_audio_duration", lambda _path: 1.0)

    def forbidden_trim(*_args, **_kwargs):
        raise AssertionError("default podcast prep must not inspect or trim edge silence")

    monkeypatch.setattr(run_audio_prep, "detect_edge_silence", forbidden_trim)

    normalized = run_audio_prep.run_prep(episode, pre_processed=processed)

    handoff = episode / "normalized-handoff.v1.json"
    result = VerifiedNormalizedAudioHandoffAdapter(handoff).normalize(
        NormalizeRequest(
            source_audio=normalized,
            expected_source_hash=hash_file(normalized),
        )
    )
    assert source.is_file()
    assert result.receipt.normalized_duration_ms == 1_000
    assert result.receipt.clock_map.drift_ms == 0.0
