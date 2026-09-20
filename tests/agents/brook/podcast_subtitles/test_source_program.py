from __future__ import annotations

import json
import subprocess
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.brook.podcast_subtitles.source_program import (
    FrameRateV1,
    ResolveLosslessAudioSettingsV1,
    ResolveProjectIdentityV1,
    ResolveRendererIdentityV1,
    ResolveRenderJobV1,
    ResolveSourceProgramCaptureV1,
    ResolveTimelineIdentityV1,
    SourceProgramIntegrityError,
    SourceProgramReceiptV1,
    seal_source_program,
    source_program_receipt_bytes,
    verify_source_program_receipt,
)


def _write_pcm24_wav(path: Path, *, sample_frames: int = 48_000) -> None:
    with wave.open(str(path), "wb") as target:
        target.setnchannels(2)
        target.setsampwidth(3)
        target.setframerate(48_000)
        target.writeframes(b"\0" * sample_frames * 2 * 3)


def _capture() -> ResolveSourceProgramCaptureV1:
    return ResolveSourceProgramCaptureV1(
        episode_id="episode-anji-119",
        renderer=ResolveRendererIdentityV1(
            product="DaVinci Resolve",
            version="20.1.1.0007",
        ),
        project=ResolveProjectIdentityV1(
            stable_id="project-8b6ea2f1",
            name="20260415 安吉",
        ),
        timeline=ResolveTimelineIdentityV1(
            stable_id="timeline-3d4c1ef2",
            name="EP119 完整節目",
            frame_rate=FrameRateV1(numerator=24, denominator=1),
            start_frame=86_400,
            end_frame=86_423,
            start_timecode="01:00:00:00",
            end_timecode="01:00:00:23",
        ),
        render_job=ResolveRenderJobV1(
            stable_id="render-job-9d83ac71",
            status="Complete",
            requested_at=datetime(2026, 8, 13, 2, 0, tzinfo=timezone.utc),
            completed_at=datetime(2026, 8, 13, 2, 1, tzinfo=timezone.utc),
            mark_in_frame=86_400,
            mark_out_frame=86_423,
            target_type="single_clip",
            audio=ResolveLosslessAudioSettingsV1(
                container="wav",
                codec="pcm_s24le",
                sample_rate_hz=48_000,
                bit_depth=24,
                channels=2,
                audio_stream_count=1,
            ),
        ),
    )


def test_complete_resolve_lossless_render_becomes_verified_auphonic_source(
    tmp_path: Path,
) -> None:
    output = tmp_path / "complete-program.wav"
    _write_pcm24_wav(output)

    receipt = seal_source_program(capture=_capture(), output_path=output)
    verified = verify_source_program_receipt(receipt=receipt, output_path=output)

    assert verified.path == output
    assert verified.receipt_hash == receipt.content_hash
    assert verified.source.sha256 == receipt.output.sha256
    assert verified.sample_frames == 48_000
    assert verified.duration_ms == 1_000
    assert receipt.capture.source_kind == "resolve_direct_lossless_render"
    assert receipt.capture.source_quality == "lossless_timeline_render"
    assert "complete-program.wav" not in receipt.model_dump_json()


def test_wrong_audio_format_is_rejected_before_receipt_exists(tmp_path: Path) -> None:
    output = tmp_path / "pcm16.wav"
    with wave.open(str(output), "wb") as target:
        target.setnchannels(2)
        target.setsampwidth(2)
        target.setframerate(48_000)
        target.writeframes(b"\0" * 48_000 * 2 * 2)

    with pytest.raises(SourceProgramIntegrityError, match="48 kHz, 24-bit, stereo"):
        seal_source_program(capture=_capture(), output_path=output)


def test_non_complete_render_job_and_operator_final_boolean_are_unrepresentable() -> None:
    payload = _capture().model_dump(mode="python")
    payload["render_job"]["status"] = "Failed"
    with pytest.raises(ValidationError, match="Complete"):
        ResolveSourceProgramCaptureV1.model_validate(payload)

    payload = _capture().model_dump(mode="python")
    payload["is_final"] = True
    with pytest.raises(ValidationError, match="is_final"):
        ResolveSourceProgramCaptureV1.model_validate(payload)


def test_raw_interview_shorter_than_complete_timeline_is_rejected(tmp_path: Path) -> None:
    output = tmp_path / "raw-interview-core.wav"
    _write_pcm24_wav(output, sample_frames=48_000)
    payload = _capture().model_dump(mode="python")
    payload["timeline"].update(
        {
            "end_frame": 86_447,
            "end_timecode": "01:00:01:23",
        }
    )
    payload["render_job"]["mark_out_frame"] = 86_447
    complete_two_second_timeline = ResolveSourceProgramCaptureV1.model_validate(payload)

    with pytest.raises(SourceProgramIntegrityError, match="complete program clock"):
        seal_source_program(capture=complete_two_second_timeline, output_path=output)


def test_fractional_frame_clock_allows_only_adjacent_sample_boundaries(tmp_path: Path) -> None:
    payload = _capture().model_dump(mode="python")
    payload["timeline"].update(
        {
            "frame_rate": {"schema_version": 1, "numerator": 30_000, "denominator": 1_001},
            "start_frame": 0,
            "end_frame": 0,
            "start_timecode": "00:00:00;00",
            "end_timecode": "00:00:00;00",
        }
    )
    payload["render_job"].update({"mark_in_frame": 0, "mark_out_frame": 0})
    fractional = ResolveSourceProgramCaptureV1.model_validate(payload)

    accepted = tmp_path / "one-frame-rounded.wav"
    _write_pcm24_wav(accepted, sample_frames=1_602)
    seal_source_program(capture=fractional, output_path=accepted)

    outside_exact_projection = tmp_path / "one-frame-too-long.wav"
    _write_pcm24_wav(outside_exact_projection, sample_frames=1_603)
    with pytest.raises(SourceProgramIntegrityError, match="expected 1601 or 1602"):
        seal_source_program(capture=fractional, output_path=outside_exact_projection)


def test_output_tamper_is_rejected_by_fresh_rehash(tmp_path: Path) -> None:
    output = tmp_path / "program.wav"
    _write_pcm24_wav(output)
    receipt = seal_source_program(capture=_capture(), output_path=output)
    payload = bytearray(output.read_bytes())
    payload[-1] = 1
    output.write_bytes(payload)

    with pytest.raises(SourceProgramIntegrityError, match="output bytes"):
        verify_source_program_receipt(receipt=receipt, output_path=output)


def test_receipt_metadata_tamper_is_rejected_before_media_replay(tmp_path: Path) -> None:
    output = tmp_path / "program.wav"
    _write_pcm24_wav(output)
    receipt = seal_source_program(capture=_capture(), output_path=output)
    payload = json.loads(source_program_receipt_bytes(receipt))
    payload["capture"]["timeline"]["name"] = "另一條 timeline"
    tampered = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")

    with pytest.raises(SourceProgramIntegrityError, match="receipt bytes are invalid"):
        verify_source_program_receipt(receipt=tampered, output_path=output)


def test_unvalidated_model_copy_cannot_bypass_receipt_identity(tmp_path: Path) -> None:
    output = tmp_path / "program.wav"
    _write_pcm24_wav(output)
    receipt = seal_source_program(capture=_capture(), output_path=output)
    forged = receipt.model_copy(update={"content_hash": "0" * 64})

    with pytest.raises((SourceProgramIntegrityError, ValidationError), match="hash"):
        verify_source_program_receipt(receipt=forged, output_path=output)


@pytest.mark.parametrize(
    ("field_path", "floating_value"),
    [
        (("project", "stable_id"), "current-project"),
        (("timeline", "stable_id"), "selected-timeline"),
        (("render_job", "stable_id"), "latest-render-job"),
        (("renderer", "version"), "latest"),
    ],
)
def test_floating_resolve_identity_is_rejected(
    field_path: tuple[str, str], floating_value: str
) -> None:
    payload = _capture().model_dump(mode="python")
    payload[field_path[0]][field_path[1]] = floating_value
    with pytest.raises(ValidationError, match="floating|pinned"):
        ResolveSourceProgramCaptureV1.model_validate(payload)


def test_same_render_bytes_moved_to_another_path_keep_one_logical_identity(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first-name.wav"
    moved = tmp_path / "unrelated-name.wav"
    _write_pcm24_wav(first)
    moved.write_bytes(first.read_bytes())

    first_receipt = seal_source_program(capture=_capture(), output_path=first)
    moved_receipt = seal_source_program(capture=_capture(), output_path=moved)

    assert first_receipt == moved_receipt
    assert source_program_receipt_bytes(first_receipt) == source_program_receipt_bytes(
        moved_receipt
    )


def test_receipt_replays_in_a_fresh_process(tmp_path: Path) -> None:
    output = tmp_path / "program.wav"
    receipt_path = tmp_path / "source-program-receipt.json"
    _write_pcm24_wav(output)
    receipt = seal_source_program(capture=_capture(), output_path=output)
    receipt_path.write_bytes(source_program_receipt_bytes(receipt))
    script = (
        "from pathlib import Path; "
        "from agents.brook.podcast_subtitles.source_program import "
        "verify_source_program_receipt; "
        "import sys; "
        "r=verify_source_program_receipt(receipt=Path(sys.argv[1]).read_bytes(), "
        "output_path=Path(sys.argv[2])); print(r.receipt_hash)"
    )

    process = subprocess.run(
        [sys.executable, "-c", script, str(receipt_path), str(output)],
        check=False,
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[4],
    )

    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == receipt.content_hash


def test_render_range_and_timeline_timecode_must_be_exact() -> None:
    payload = _capture().model_dump(mode="python")
    payload["render_job"]["mark_in_frame"] += 1
    with pytest.raises(ValidationError, match="complete timeline"):
        ResolveSourceProgramCaptureV1.model_validate(payload)

    payload = _capture().model_dump(mode="python")
    payload["timeline"]["end_timecode"] = "01:00:01:00"
    with pytest.raises(ValidationError, match="timecode does not match"):
        ResolveSourceProgramCaptureV1.model_validate(payload)


def test_receipt_with_path_or_compressed_codec_metadata_is_rejected(tmp_path: Path) -> None:
    output = tmp_path / "program.wav"
    _write_pcm24_wav(output)
    receipt = seal_source_program(capture=_capture(), output_path=output)
    payload = receipt.model_dump(mode="python")
    payload["path"] = str(output)
    with pytest.raises(ValidationError, match="path"):
        SourceProgramReceiptV1.model_validate(payload)

    capture = _capture().model_dump(mode="python")
    capture["render_job"]["audio"]["container"] = "mp4"
    capture["render_job"]["audio"]["codec"] = "aac"
    with pytest.raises(ValidationError, match="wav|pcm_s24le"):
        ResolveSourceProgramCaptureV1.model_validate(capture)

    payload = receipt.model_dump(mode="python")
    payload["output"]["size_bytes"] = True
    with pytest.raises(ValidationError, match="exact integer"):
        SourceProgramReceiptV1.model_validate(payload)
