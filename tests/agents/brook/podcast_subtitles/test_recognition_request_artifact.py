from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.brook.podcast_subtitles.hashing import hash_file
from agents.brook.podcast_subtitles.ports import RecognitionRequest
from agents.brook.podcast_subtitles.recognition_request import (
    RecognitionRequestArtifactV1,
    build_recognition_request_artifact,
    materialize_recognition_request,
)


def test_recognition_request_artifact_replays_exact_language_and_empty_context(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "normalized.wav"
    audio.write_bytes(b"normalized audio")
    request = RecognitionRequest(
        episode_id="episode-1",
        invocation_id="recognition-1",
        normalized_audio=audio,
        expected_normalized_audio_hash=hash_file(audio),
        raw_output_dir=tmp_path / "raw",
        language_hint="zh-Hant-TW",
    )

    artifact = build_recognition_request_artifact(request)
    replay = materialize_recognition_request(
        artifact,
        normalized_audio=audio,
        raw_output_dir=tmp_path / "replay",
    )

    assert replay.language_hint == "zh-Hant-TW"
    assert replay.context_policy.context_bytes == b""
    assert replay.episode_id == request.episode_id
    assert replay.invocation_id == request.invocation_id
    assert artifact.normalized_audio_size_bytes == audio.stat().st_size


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("language_hint", "en"),
        ("context_sha256", "f" * 64),
        ("context_size_bytes", 1),
        ("normalized_audio_sha256", "a" * 64),
    ],
)
def test_recognition_request_artifact_rejects_tampering(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    audio = tmp_path / "normalized.wav"
    audio.write_bytes(b"normalized audio")
    request = RecognitionRequest(
        episode_id="episode-1",
        invocation_id="recognition-1",
        normalized_audio=audio,
        expected_normalized_audio_hash=hash_file(audio),
        language_hint="zh-Hant-TW",
    )
    artifact = build_recognition_request_artifact(request)
    payload = artifact.model_dump(mode="json")
    payload[field] = value

    if field in {"language_hint", "context_sha256", "context_size_bytes"}:
        with pytest.raises(ValidationError):
            RecognitionRequestArtifactV1.model_validate_json(json.dumps(payload))
        return

    forged_without_hash = {key: item for key, item in payload.items() if key != "content_hash"}
    from agents.brook.podcast_subtitles.hashing import hash_object

    payload["content_hash"] = hash_object(forged_without_hash)
    forged = RecognitionRequestArtifactV1.model_validate(payload)
    with pytest.raises(ValueError):
        materialize_recognition_request(
            forged,
            normalized_audio=audio,
            raw_output_dir=tmp_path / "replay",
        )


def test_recognition_request_replay_rejects_same_size_source_swap(tmp_path: Path) -> None:
    audio = tmp_path / "normalized.wav"
    audio.write_bytes(b"version-one")
    artifact = build_recognition_request_artifact(
        RecognitionRequest(
            episode_id="episode-1",
            invocation_id="recognition-1",
            normalized_audio=audio,
            expected_normalized_audio_hash=hash_file(audio),
            language_hint="zh-Hant-TW",
        )
    )
    replacement = tmp_path / "replacement.wav"
    replacement.write_bytes(b"version-two")
    os.replace(replacement, audio)

    with pytest.raises(ValueError, match="audio binding mismatch"):
        materialize_recognition_request(
            artifact,
            normalized_audio=audio,
            raw_output_dir=tmp_path / "replay",
        )


def test_recognition_request_rejects_reparse_audio(tmp_path: Path) -> None:
    target = tmp_path / "target.wav"
    target.write_bytes(b"audio")
    link = tmp_path / "linked.wav"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("host does not permit unprivileged symlink creation")

    with pytest.raises(ValueError, match="unavailable"):
        build_recognition_request_artifact(
            RecognitionRequest(
                episode_id="episode-1",
                invocation_id="recognition-1",
                normalized_audio=link,
                language_hint="zh-Hant-TW",
            )
        )


def test_recognition_request_rejects_path_replacement_between_stat_and_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = tmp_path / "normalized.wav"
    replacement = tmp_path / "replacement.wav"
    audio.write_bytes(b"version-one")
    replacement.write_bytes(b"version-two")
    original_open = Path.open
    swapped = False

    def swapping_open(path: Path, *args, **kwargs):
        nonlocal swapped
        if path == audio and not swapped:
            swapped = True
            os.replace(replacement, audio)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", swapping_open)
    with pytest.raises(ValueError, match="unavailable"):
        build_recognition_request_artifact(
            RecognitionRequest(
                episode_id="episode-1",
                invocation_id="recognition-1",
                normalized_audio=audio,
                language_hint="zh-Hant-TW",
            )
        )
