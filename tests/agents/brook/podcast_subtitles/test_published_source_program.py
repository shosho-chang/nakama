from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agents.brook.podcast_subtitles.hashing import hash_file, hash_object, sha256_bytes
from agents.brook.podcast_subtitles.published_source_program import (
    MediaProcessCaptureV1,
    PinnedMediaExecutableV1,
    PublishedAudioExpectationV1,
    PublishedExportDecodeReceiptV1,
    PublishedSourceProgramIntegrityError,
    canonical_receipt_bytes,
    execute_and_seal,
    load_receipt_bytes,
    receipt_content_hash,
    seal_request,
    verify_and_replay,
)
from agents.brook.podcast_subtitles.source_program_binding import (
    SourceProgramBinding,
    verify_source_program_binding,
)
from shared.schemas.podcast_subtitles_v2 import ArtifactDigest


def _artifact(path: Path) -> ArtifactDigest:
    digest = hash_file(path)
    return ArtifactDigest(
        uri=f"urn:sha256:{digest}",
        sha256=digest,
        size_bytes=path.stat().st_size,
    )


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass
class FakeMediaRunner:
    source_path: Path
    ffmpeg_path: Path
    ffprobe_path: Path
    duration_ts: int = 4_800
    output_bytes: bytes = b"RIFF" + (b"\x00" * 252)
    source_overrides: dict[str, Any] = field(default_factory=dict)
    output_overrides: dict[str, Any] = field(default_factory=dict)
    source_format_overrides: dict[str, Any] = field(default_factory=dict)
    output_format_overrides: dict[str, Any] = field(default_factory=dict)
    extra_audio_stream: bool = False
    output_extra_stream: bool = False
    fail_step: str | None = None
    calls: list[tuple[list[str], dict[str, Any]]] = field(default_factory=list)

    ffmpeg_version: bytes = b"ffmpeg version 7.1.1-static fixture\nconfiguration: fixed\n"
    ffprobe_version: bytes = b"ffprobe version 7.1.1-static fixture\nconfiguration: fixed\n"

    def _source_probe(self) -> bytes:
        stream: dict[str, Any] = {
            "index": 1,
            "codec_type": "audio",
            "codec_name": "aac",
            "sample_rate": "48000",
            "channels": 2,
            "channel_layout": "stereo",
            "bits_per_sample": 0,
            "time_base": "1/48000",
            "duration_ts": self.duration_ts,
        }
        stream.update(self.source_overrides)
        streams: list[dict[str, Any]] = [
            {"index": 0, "codec_type": "video", "codec_name": "h264"},
            stream,
        ]
        if self.extra_audio_stream:
            streams.append({**stream, "index": 2})
        media_format = {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": "0.100000",
            "size": str(self.source_path.stat().st_size),
        }
        media_format.update(self.source_format_overrides)
        return _json_bytes({"streams": streams, "format": media_format})

    def _output_probe(self, path: Path) -> bytes:
        stream: dict[str, Any] = {
            "index": 0,
            "codec_type": "audio",
            "codec_name": "pcm_s24le",
            "sample_rate": "48000",
            "channels": 2,
            "channel_layout": "stereo",
            "bits_per_sample": 24,
            "time_base": "1/48000",
            "duration_ts": self.duration_ts,
        }
        stream.update(self.output_overrides)
        streams = [stream]
        if self.output_extra_stream:
            streams.append({"index": 1, "codec_type": "data", "codec_name": "bin_data"})
        media_format = {
            "format_name": "wav",
            "duration": "0.100000",
            "size": str(path.stat().st_size),
        }
        media_format.update(self.output_format_overrides)
        return _json_bytes({"streams": streams, "format": media_format})

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        self.calls.append((list(command), dict(kwargs)))
        assert kwargs["capture_output"] is True
        assert kwargs["check"] is False
        assert kwargs["shell"] is False
        assert kwargs["env"]["LC_ALL"] == "C"
        assert kwargs["env"]["AV_LOG_FORCE_NOCOLOR"] == "1"

        executable = Path(command[0])
        if command[1:] == ["-version"]:
            step = "ffmpeg_version" if executable == self.ffmpeg_path else "ffprobe_version"
            stdout = self.ffmpeg_version if step == "ffmpeg_version" else self.ffprobe_version
        elif "-show_entries" in command:
            media_path = Path(command[-1])
            step = "source_probe" if media_path == self.source_path else "output_probe"
            stdout = (
                self._source_probe() if step == "source_probe" else self._output_probe(media_path)
            )
        else:
            step = "decode"
            assert executable == self.ffmpeg_path
            assert command[command.index("-map") + 1] == "0:1"
            assert command[command.index("-i") + 1] == str(self.source_path)
            assert command[-9:] == [
                "0:1",
                "-vn",
                "-c:a",
                "pcm_s24le",
                "-ar",
                "48000",
                "-ac",
                "2",
                command[-1],
            ]
            Path(command[-1]).write_bytes(self.output_bytes)
            stdout = b""
        return subprocess.CompletedProcess(
            command,
            17 if self.fail_step == step else 0,
            stdout=stdout,
            stderr=b"fixture failure" if self.fail_step == step else b"",
        )


@dataclass(frozen=True)
class EpisodeFixture:
    source: Path
    output: Path
    ffmpeg: Path
    ffprobe: Path
    runner: FakeMediaRunner
    request: Any


def test_published_receipt_grants_lossy_source_program_capability(
    episode: EpisodeFixture,
) -> None:
    receipt = execute_and_seal(
        episode.request,
        source_path=episode.source,
        output_path=episode.output,
        ffmpeg_executable=episode.ffmpeg,
        ffprobe_executable=episode.ffprobe,
        _runner=episode.runner,
    )

    verified = verify_source_program_binding(
        SourceProgramBinding(
            kind="published_export_audio_decode",
            receipt_bytes=canonical_receipt_bytes(receipt),
            output_path=episode.output,
            published_source_path=episode.source,
            ffmpeg_executable=episode.ffmpeg,
            ffprobe_executable=episode.ffprobe,
        ),
        _runner=episode.runner,
    )

    assert verified.kind == "published_export_audio_decode"
    assert verified.source_quality == "lossy_published_export_decoded_to_pcm"
    assert verified.output == receipt.output
    assert verified.receipt_hash == receipt.content_hash


@pytest.fixture
def episode(tmp_path: Path) -> EpisodeFixture:
    source = tmp_path / "published.mp4"
    output = tmp_path / "source-program.wav"
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffprobe = tmp_path / "ffprobe.exe"
    source.write_bytes(b"published-export-fixture" * 17)
    ffmpeg.write_bytes(b"exact-ffmpeg-build-fixture")
    ffprobe.write_bytes(b"exact-ffprobe-build-fixture")
    runner = FakeMediaRunner(source, ffmpeg, ffprobe)
    request = seal_request(
        source=_artifact(source),
        expected_audio=PublishedAudioExpectationV1(stream_index=1, duration_ts=runner.duration_ts),
        ffmpeg=PinnedMediaExecutableV1(
            role="ffmpeg",
            artifact=_artifact(ffmpeg),
            version_stdout=runner.ffmpeg_version,
        ),
        ffprobe=PinnedMediaExecutableV1(
            role="ffprobe",
            artifact=_artifact(ffprobe),
            version_stdout=runner.ffprobe_version,
        ),
    )
    return EpisodeFixture(source, output, ffmpeg, ffprobe, runner, request)


def _execute(episode: EpisodeFixture, output: Path | None = None) -> PublishedExportDecodeReceiptV1:
    return execute_and_seal(
        episode.request,
        source_path=episode.source,
        output_path=output or episode.output,
        ffmpeg_executable=episode.ffmpeg,
        ffprobe_executable=episode.ffprobe,
        _runner=episode.runner,
    )


def test_execute_replay_and_determinism_are_byte_exact(
    episode: EpisodeFixture, tmp_path: Path
) -> None:
    first = _execute(episode)
    verified = verify_and_replay(
        first,
        source_path=episode.source,
        output_path=episode.output,
        ffmpeg_executable=episode.ffmpeg,
        ffprobe_executable=episode.ffprobe,
        _runner=episode.runner,
    )
    second = _execute(episode, tmp_path / "second.wav")

    assert verified == first
    assert canonical_receipt_bytes(first) == canonical_receipt_bytes(second)
    assert load_receipt_bytes(canonical_receipt_bytes(first)) == first
    assert receipt_content_hash(first) == first.content_hash
    assert first.source_quality == "lossy_published_export_decoded_to_pcm"
    assert first.output_facts.duration_ts == 4_800
    assert first.output_facts.sample_frames == 4_800
    assert first.output.sha256 == sha256_bytes(episode.runner.output_bytes)
    assert all(call_kwargs["shell"] is False for _, call_kwargs in episode.runner.calls)


def test_local_paths_are_excluded_from_request_and_receipt(episode: EpisodeFixture) -> None:
    receipt = _execute(episode)
    rendered = canonical_receipt_bytes(receipt).decode("utf-8")

    assert str(episode.source) not in rendered
    assert str(episode.output) not in rendered
    assert str(episode.ffmpeg) not in rendered
    assert str(episode.ffprobe) not in rendered
    assert "urn:sha256:" in rendered
    assert str(episode.source).encode() not in receipt.source_probe.stdout
    assert str(episode.output).encode() not in receipt.output_probe.stdout


def test_probe_rejects_filename_or_other_nonallowlisted_fields(
    episode: EpisodeFixture,
) -> None:
    episode.runner.source_format_overrides["filename"] = str(episode.source)

    with pytest.raises(PublishedSourceProgramIntegrityError, match="path-free allowlist"):
        _execute(episode)


def test_receipt_loader_rejects_noncanonical_and_tampered_bytes(
    episode: EpisodeFixture,
) -> None:
    receipt = _execute(episode)
    exact = canonical_receipt_bytes(receipt)

    with pytest.raises(PublishedSourceProgramIntegrityError, match="not canonical"):
        load_receipt_bytes(exact + b"\n")
    with pytest.raises(PublishedSourceProgramIntegrityError, match="contract validation"):
        load_receipt_bytes(exact.replace(receipt.content_hash.encode(), b"0" * 64, 1))


def test_stale_preexisting_output_fails_before_process_execution(episode: EpisodeFixture) -> None:
    episode.output.write_bytes(b"stale")

    with pytest.raises(PublishedSourceProgramIntegrityError, match="already exists"):
        _execute(episode)

    assert episode.runner.calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("codec_name", "mp3"),
        ("sample_rate", "44100"),
        ("channels", 1),
        ("duration_ts", 4_799),
    ],
)
def test_wrong_published_codec_rate_channels_or_duration_fail_closed(
    episode: EpisodeFixture, field: str, value: object
) -> None:
    episode.runner.source_overrides[field] = value

    with pytest.raises(
        PublishedSourceProgramIntegrityError, match="published source|format duration"
    ):
        _execute(episode)


def test_multiple_published_audio_streams_fail_closed(episode: EpisodeFixture) -> None:
    episode.runner.extra_audio_stream = True

    with pytest.raises(PublishedSourceProgramIntegrityError, match="exactly one audio stream"):
        _execute(episode)


def test_container_duration_must_equal_exact_published_audio_clock(
    episode: EpisodeFixture,
) -> None:
    episode.runner.source_format_overrides["duration"] = "0.099999"

    with pytest.raises(PublishedSourceProgramIntegrityError, match="format duration"):
        _execute(episode)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("codec_name", "pcm_s16le"),
        ("sample_rate", "44100"),
        ("channels", 1),
        ("bits_per_sample", 16),
        ("duration_ts", 4_799),
    ],
)
def test_wrong_output_codec_rate_channels_depth_or_duration_fail_closed(
    episode: EpisodeFixture, field: str, value: object
) -> None:
    episode.runner.output_overrides[field] = value

    with pytest.raises(PublishedSourceProgramIntegrityError, match="output|PCM|format duration"):
        _execute(episode)


def test_extra_output_stream_fails_closed(episode: EpisodeFixture) -> None:
    episode.runner.output_extra_stream = True

    with pytest.raises(PublishedSourceProgramIntegrityError, match="exactly one audio stream"):
        _execute(episode)


def test_output_container_duration_must_equal_exact_sample_count(
    episode: EpisodeFixture,
) -> None:
    episode.runner.output_format_overrides["duration"] = "0.100001"

    with pytest.raises(PublishedSourceProgramIntegrityError, match="format duration"):
        _execute(episode)


@pytest.mark.parametrize(
    "step",
    ["ffmpeg_version", "ffprobe_version", "source_probe", "decode", "output_probe"],
)
def test_nonzero_subprocess_never_seals_receipt(episode: EpisodeFixture, step: str) -> None:
    episode.runner.fail_step = step

    with pytest.raises(PublishedSourceProgramIntegrityError, match="failed with status 17"):
        _execute(episode)


def test_source_output_and_binary_drift_are_rejected_on_replay(
    episode: EpisodeFixture, tmp_path: Path
) -> None:
    receipt = _execute(episode)

    episode.source.write_bytes(episode.source.read_bytes() + b"tamper")
    with pytest.raises(PublishedSourceProgramIntegrityError, match="published source content"):
        verify_and_replay(
            receipt,
            source_path=episode.source,
            output_path=episode.output,
            ffmpeg_executable=episode.ffmpeg,
            ffprobe_executable=episode.ffprobe,
            _runner=episode.runner,
        )

    episode.source.write_bytes(b"published-export-fixture" * 17)
    episode.output.write_bytes(episode.output.read_bytes() + b"tamper")
    with pytest.raises(PublishedSourceProgramIntegrityError, match="materialized decoded output"):
        verify_and_replay(
            receipt,
            source_path=episode.source,
            output_path=episode.output,
            ffmpeg_executable=episode.ffmpeg,
            ffprobe_executable=episode.ffprobe,
            _runner=episode.runner,
        )

    episode.output.write_bytes(episode.runner.output_bytes)
    episode.ffmpeg.write_bytes(b"different ffmpeg bytes")
    with pytest.raises(PublishedSourceProgramIntegrityError, match="ffmpeg executable content"):
        verify_and_replay(
            receipt,
            source_path=episode.source,
            output_path=episode.output,
            ffmpeg_executable=episode.ffmpeg,
            ffprobe_executable=episode.ffprobe,
            _runner=episode.runner,
        )


def test_fresh_probe_tamper_is_detected_even_when_output_bytes_match(
    episode: EpisodeFixture,
) -> None:
    receipt = _execute(episode)
    episode.runner.output_overrides["duration_ts"] = 4_799

    with pytest.raises(PublishedSourceProgramIntegrityError, match="PCM|output|format duration"):
        verify_and_replay(
            receipt,
            source_path=episode.source,
            output_path=episode.output,
            ffmpeg_executable=episode.ffmpeg,
            ffprobe_executable=episode.ffprobe,
            _runner=episode.runner,
        )


def test_stored_raw_probe_tamper_is_rejected_by_fresh_replay(
    episode: EpisodeFixture,
) -> None:
    receipt = _execute(episode)
    changed_stdout = receipt.source_probe.stdout + b" "
    changed_probe = MediaProcessCaptureV1(
        **{
            **receipt.source_probe.model_dump(mode="python"),
            "stdout": changed_stdout,
            "stdout_sha256": sha256_bytes(changed_stdout),
        }
    )
    provisional = receipt.model_copy(
        update={"source_probe": changed_probe, "content_hash": "0" * 64}
    )
    forged_payload = provisional.model_dump(mode="python", exclude={"content_hash"})
    forged = PublishedExportDecodeReceiptV1(
        **forged_payload,
        content_hash=hash_object(provisional.model_dump(mode="python", exclude={"content_hash"})),
    )

    with pytest.raises(PublishedSourceProgramIntegrityError, match="replay differs"):
        verify_and_replay(
            forged,
            source_path=episode.source,
            output_path=episode.output,
            ffmpeg_executable=episode.ffmpeg,
            ffprobe_executable=episode.ffprobe,
            _runner=episode.runner,
        )


def test_receipt_and_decode_command_tamper_are_rejected(episode: EpisodeFixture) -> None:
    receipt = _execute(episode)
    with pytest.raises(ValidationError, match="content_hash"):
        PublishedExportDecodeReceiptV1.model_validate(
            {**receipt.model_dump(mode="python"), "content_hash": "0" * 64},
            strict=True,
        )

    drifted_decode = MediaProcessCaptureV1(
        **{
            **receipt.decode.model_dump(mode="python"),
            "argv_template": receipt.decode.argv_template[:-2] + ("1", "@output"),
        }
    )
    provisional = receipt.model_copy(update={"decode": drifted_decode})
    with pytest.raises(ValidationError, match="argv template drifted"):
        PublishedExportDecodeReceiptV1.model_validate(
            {
                **provisional.model_dump(mode="python"),
                "content_hash": receipt_content_hash(receipt),
            },
            strict=True,
        )


def test_version_identity_must_be_role_correct_pinned_and_content_addressed(
    episode: EpisodeFixture,
) -> None:
    artifact = _artifact(episode.ffmpeg)
    with pytest.raises(ValidationError, match="declared executable role"):
        PinnedMediaExecutableV1(
            role="ffmpeg",
            artifact=artifact,
            version_stdout=b"ffprobe version 7.1.1\n",
        )
    with pytest.raises(ValidationError, match="pinned version"):
        PinnedMediaExecutableV1(
            role="ffmpeg",
            artifact=artifact,
            version_stdout=b"ffmpeg version latest\n",
        )
    with pytest.raises(ValidationError, match="canonical SHA-256 URN"):
        PinnedMediaExecutableV1(
            role="ffmpeg",
            artifact=artifact.model_copy(update={"uri": str(episode.ffmpeg)}),
            version_stdout=episode.runner.ffmpeg_version,
        )


def test_version_response_drift_fails_before_media_probe(episode: EpisodeFixture) -> None:
    episode.runner.ffmpeg_version = b"ffmpeg version 7.1.2-static fixture\n"

    with pytest.raises(PublishedSourceProgramIntegrityError, match="version output drifted"):
        _execute(episode)


def test_replay_never_invokes_a_drifted_probe_binary(episode: EpisodeFixture) -> None:
    receipt = _execute(episode)
    call_count = len(episode.runner.calls)
    episode.ffprobe.write_bytes(b"untrusted replacement")

    with pytest.raises(PublishedSourceProgramIntegrityError, match="ffprobe executable content"):
        verify_and_replay(
            receipt,
            source_path=episode.source,
            output_path=episode.output,
            ffmpeg_executable=episode.ffmpeg,
            ffprobe_executable=episode.ffprobe,
            _runner=episode.runner,
        )

    assert len(episode.runner.calls) == call_count


def test_request_tamper_and_mismatched_roles_are_rejected(episode: EpisodeFixture) -> None:
    with pytest.raises(ValidationError, match="content_hash"):
        type(episode.request).model_validate(
            {
                **episode.request.model_dump(mode="python"),
                "decode_timeout_seconds": 3_600,
                "content_hash": "f" * 64,
            },
            strict=True,
        )
    with pytest.raises(ValidationError, match="executable roles are mismatched"):
        seal_request(
            source=episode.request.source,
            expected_audio=episode.request.expected_audio,
            ffmpeg=episode.request.ffmpeg,
            ffprobe=episode.request.ffmpeg,
        )


def test_probe_json_duplicate_keys_and_malformed_payload_fail_closed(
    episode: EpisodeFixture,
) -> None:
    original = episode.runner._source_probe
    episode.runner._source_probe = lambda: b'{"streams":[],"streams":[],"format":{}}'  # type: ignore[method-assign]
    with pytest.raises(PublishedSourceProgramIntegrityError, match="duplicate keys"):
        _execute(episode)

    episode.runner._source_probe = lambda: b"not-json"  # type: ignore[method-assign]
    with pytest.raises(PublishedSourceProgramIntegrityError, match="strict UTF-8 JSON"):
        _execute(episode, episode.output.with_name("malformed.wav"))
    episode.runner._source_probe = original  # type: ignore[method-assign]
