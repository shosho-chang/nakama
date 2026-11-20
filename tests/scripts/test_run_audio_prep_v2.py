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


def test_local_decode_writes_pcm_wav_and_v2_handoff_without_auphonic(
    tmp_path: Path, monkeypatch
) -> None:
    """社群直播：m4a → 本機解成 PCM WAV，不碰 Auphonic，照樣寫 V2 handoff。"""
    import shutil
    import subprocess

    if shutil.which("ffmpeg") is None:
        import pytest

        pytest.skip("ffmpeg not installed")

    episode = tmp_path / "2026-11-19 18.52.50 AI Agent"
    episode.mkdir()
    source = tmp_path / "audio123.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=2",
            "-ac",
            "2",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("--local-decode must not call Auphonic")

    monkeypatch.setattr(run_audio_prep, "normalize", forbidden)
    monkeypatch.setattr(run_audio_prep, "_align_trim", forbidden)

    normalized = run_audio_prep.run_prep(episode, audio=source, local_decode=True)

    assert normalized == episode / "normalized.wav"
    with wave.open(str(normalized), "rb") as wav:
        assert wav.getsampwidth() == 2
        assert wav.getcomptype() == "NONE"
        assert wav.getnchannels() == 2
        assert wav.getframerate() == 48_000
    result = VerifiedNormalizedAudioHandoffAdapter(
        episode / "normalized-handoff.v1.json"
    ).normalize(
        NormalizeRequest(source_audio=normalized, expected_source_hash=hash_file(normalized))
    )
    assert result.receipt.clock_map.drift_ms == 0.0
    import json

    manifest = json.loads((episode / "prep_manifest.json").read_text(encoding="utf-8"))
    assert manifest["auphonic"] is False
    assert manifest["local_decode"] is True
    assert source.is_file()
    assert not list(episode.glob("*.decoding.wav"))


def test_local_decode_rejects_pre_processed(tmp_path: Path) -> None:
    import pytest

    episode = tmp_path / "ep"
    episode.mkdir()
    with pytest.raises(ValueError):
        run_audio_prep.run_prep(
            episode,
            audio=tmp_path / "a.m4a",
            pre_processed=tmp_path / "b.wav",
            local_decode=True,
        )
