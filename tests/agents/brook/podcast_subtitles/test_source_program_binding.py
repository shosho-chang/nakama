from __future__ import annotations

import wave
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.source_program import (
    FrameRateV1,
    ResolveLosslessAudioSettingsV1,
    ResolveProjectIdentityV1,
    ResolveRendererIdentityV1,
    ResolveRenderJobV1,
    ResolveSourceProgramCaptureV1,
    ResolveTimelineIdentityV1,
    seal_source_program,
    source_program_receipt_bytes,
)
from agents.brook.podcast_subtitles.source_program_binding import (
    SourceProgramBinding,
    SourceProgramBindingError,
    VerifiedSourceProgramBinding,
    reverify_source_program_capability,
    verify_source_program_binding,
)


def _write_pcm24_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as target:
        target.setnchannels(2)
        target.setsampwidth(3)
        target.setframerate(48_000)
        target.writeframes(b"\0" * 48_000 * 2 * 3)


def _capture() -> ResolveSourceProgramCaptureV1:
    return ResolveSourceProgramCaptureV1(
        episode_id="episode-source-binding",
        renderer=ResolveRendererIdentityV1(
            product="DaVinci Resolve",
            version="20.1.1.0007",
        ),
        project=ResolveProjectIdentityV1(
            stable_id="project-source-binding",
            name="Source binding fixture",
        ),
        timeline=ResolveTimelineIdentityV1(
            stable_id="timeline-source-binding",
            name="Complete one-second program",
            frame_rate=FrameRateV1(numerator=24, denominator=1),
            start_frame=86_400,
            end_frame=86_423,
            start_timecode="01:00:00:00",
            end_timecode="01:00:00:23",
        ),
        render_job=ResolveRenderJobV1(
            stable_id="render-job-source-binding",
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


def test_direct_binding_replays_canonical_receipt_and_exact_output(tmp_path: Path) -> None:
    output = tmp_path / "program.wav"
    _write_pcm24_wav(output)
    receipt = seal_source_program(capture=_capture(), output_path=output)
    binding = SourceProgramBinding(
        kind="resolve_direct_lossless_render",
        receipt_bytes=source_program_receipt_bytes(receipt),
        output_path=output,
    )

    verified = verify_source_program_binding(binding)

    assert verified.kind == "resolve_direct_lossless_render"
    assert verified.source_quality == "lossless_timeline_render"
    assert verified.output_path == output.resolve()
    assert verified.output == receipt.output
    assert verified.receipt_hash == receipt.content_hash
    assert verified.receipt_bytes == source_program_receipt_bytes(receipt)


def test_direct_binding_rejects_receipt_tamper_and_output_drift(tmp_path: Path) -> None:
    output = tmp_path / "program.wav"
    _write_pcm24_wav(output)
    receipt = seal_source_program(capture=_capture(), output_path=output)
    canonical = source_program_receipt_bytes(receipt)

    with pytest.raises(SourceProgramBindingError, match="direct Source Program"):
        verify_source_program_binding(
            SourceProgramBinding(
                kind="resolve_direct_lossless_render",
                receipt_bytes=canonical + b"\n",
                output_path=output,
            )
        )

    drifted = bytearray(output.read_bytes())
    drifted[-1] = 1
    output.write_bytes(drifted)
    with pytest.raises(SourceProgramBindingError, match="direct Source Program"):
        verify_source_program_binding(
            SourceProgramBinding(
                kind="resolve_direct_lossless_render",
                receipt_bytes=canonical,
                output_path=output,
            )
        )


def test_binding_kind_rejects_cross_kind_transport_fields(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="direct Source Program"):
        SourceProgramBinding(
            kind="resolve_direct_lossless_render",
            receipt_bytes=b"{}",
            output_path=tmp_path / "program.wav",
            published_source_path=tmp_path / "published.mp4",
        )


def test_capability_recheck_rejects_forgery_and_post_verification_drift(
    tmp_path: Path,
) -> None:
    output = tmp_path / "program.wav"
    _write_pcm24_wav(output)
    receipt = seal_source_program(capture=_capture(), output_path=output)
    binding = SourceProgramBinding(
        kind="resolve_direct_lossless_render",
        receipt_bytes=source_program_receipt_bytes(receipt),
        output_path=output,
    )
    granted = verify_source_program_binding(binding)
    forged = VerifiedSourceProgramBinding(
        kind=granted.kind,
        source_quality=granted.source_quality,
        receipt_bytes=granted.receipt_bytes,
        receipt_hash=granted.receipt_hash,
        output_path=granted.output_path,
        output=granted.output,
        sample_frames=granted.sample_frames,
        duration_ms=granted.duration_ms,
        binding=binding,
    )

    with pytest.raises(SourceProgramBindingError, match="not granted"):
        reverify_source_program_capability(forged)

    drifted = bytearray(output.read_bytes())
    drifted[-1] = 1
    output.write_bytes(drifted)
    with pytest.raises(SourceProgramBindingError, match="capability replay"):
        reverify_source_program_capability(granted)
