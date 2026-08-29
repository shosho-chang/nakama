from __future__ import annotations

import json
import subprocess

import pytest

from agents.usopp.youtube_short_preflight import preflight_short


def _payload(
    *,
    width: int = 1080,
    height: int = 1920,
    duration: float = 60,
    video_codec: str = "h264",
    audio_codec: str | None = "aac",
    fps: str = "30/1",
    sar: str = "1:1",
) -> dict:
    streams = [
        {
            "codec_type": "video",
            "codec_name": video_codec,
            "width": width,
            "height": height,
            "sample_aspect_ratio": sar,
            "avg_frame_rate": fps,
        }
    ]
    if audio_codec is not None:
        streams.append({"codec_type": "audio", "codec_name": audio_codec})
    return {
        "streams": streams,
        "format": {"duration": str(duration), "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
    }


def _runner(payload: dict | str, *, returncode: int = 0, stderr: str = ""):
    stdout = payload if isinstance(payload, str) else json.dumps(payload)

    def run(*_args, **_kwargs):
        return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)

    return run


def _file(tmp_path):
    path = tmp_path / "partner.mp4"
    path.write_bytes(b"partner-short")
    return path


def test_happy_path_returns_structured_facts(tmp_path):
    result = preflight_short(_file(tmp_path), runner=_runner(_payload()))
    assert result.ok
    assert result.errors == ()
    assert result.warnings == ()
    assert result.duration_sec == 60
    assert result.width == 1080 and result.height == 1920
    assert result.video_codec == "h264" and result.audio_codec == "aac"
    assert result.fps == 30
    assert result.display_aspect_ratio == pytest.approx(9 / 16)


def test_ffprobe_output_is_decoded_as_utf8_on_windows(tmp_path):
    seen = {}

    def runner(*_args, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess([], 0, stdout=json.dumps(_payload()), stderr="")

    result = preflight_short(_file(tmp_path), runner=runner)
    assert result.ok
    assert seen["encoding"] == "utf-8"
    assert seen["errors"] == "replace"


@pytest.mark.parametrize(
    ("payload", "needle"),
    [
        (_payload(width=1920, height=1080), "vertical 或 square"),
        (_payload(duration=181), "3–180"),
        (_payload(audio_codec=None), "audio stream"),
    ],
)
def test_policy_violations_are_hard_errors(tmp_path, payload, needle):
    result = preflight_short(_file(tmp_path), runner=_runner(payload))
    assert not result.ok
    assert needle in " ".join(result.errors)


def test_bad_json_and_nonzero_ffprobe_are_hard_errors(tmp_path):
    path = _file(tmp_path)
    bad_json = preflight_short(path, runner=_runner("not-json"))
    nonzero = preflight_short(path, runner=_runner({}, returncode=1, stderr="invalid data"))
    assert not bad_json.ok and "JSON" in bad_json.errors[0]
    assert not nonzero.ok and "invalid data" in nonzero.errors[0]


def test_square_is_allowed_and_nonstandard_codec_fps_only_warn(tmp_path):
    result = preflight_short(
        _file(tmp_path),
        runner=_runner(
            _payload(
                width=1080,
                height=1080,
                video_codec="hevc",
                audio_codec="opus",
                fps="120/1",
            )
        ),
    )
    assert result.ok
    warnings = " ".join(result.warnings)
    assert "H.264" in warnings
    assert "AAC" in warnings
    assert "23–60" in warnings


def test_display_orientation_uses_sample_aspect_ratio(tmp_path):
    # Coded dimensions look vertical, but 2:1 pixels make the displayed image landscape.
    result = preflight_short(
        _file(tmp_path),
        runner=_runner(_payload(width=1080, height=1920, sar="2:1")),
    )
    assert not result.ok
    assert "vertical 或 square" in " ".join(result.errors)


def test_display_aspect_ratio_is_fallback_when_sar_is_unavailable(tmp_path):
    payload = _payload(width=1080, height=1080)
    payload["streams"][0]["sample_aspect_ratio"] = "N/A"
    payload["streams"][0]["display_aspect_ratio"] = "9:16"
    result = preflight_short(_file(tmp_path), runner=_runner(payload))
    assert result.ok
    assert result.reported_display_aspect_ratio == "9:16"
    assert result.display_aspect_ratio == pytest.approx(9 / 16)


def test_missing_sar_metadata_does_not_warn_for_standard_vertical_delivery(tmp_path):
    payload = _payload()
    payload["streams"][0]["sample_aspect_ratio"] = None
    result = preflight_short(_file(tmp_path), runner=_runner(payload))
    assert result.ok
    assert result.display_aspect_ratio == pytest.approx(9 / 16)
    assert result.warnings == ()


def test_missing_empty_and_directory_fail_without_ffprobe(tmp_path):
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    assert not preflight_short(tmp_path / "missing.mp4").ok
    assert not preflight_short(empty).ok
    assert not preflight_short(tmp_path).ok
