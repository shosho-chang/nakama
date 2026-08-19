"""Deterministic media preflight for externally edited YouTube Shorts.

The importer intentionally does not transcode partner deliveries.  This module
turns ``ffprobe`` output into a stable, testable result so unsuitable files can
be rejected before they enter the canonical exports directory.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class ShortPreflightResult:
    file_path: str
    file_bytes: int = 0
    container_format: str | None = None
    duration_sec: float = 0.0
    width: int | None = None
    height: int | None = None
    sample_aspect_ratio: str | None = None
    reported_display_aspect_ratio: str | None = None
    display_aspect_ratio: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    fps: float | None = None
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["ok"] = self.ok
        data["errors"] = list(self.errors)
        data["warnings"] = list(self.warnings)
        return data


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _ratio(value: Any) -> float | None:
    if value in (None, "", "N/A", "0:1", "0/0"):
        return None
    text = str(value)
    for separator in (":", "/"):
        if separator in text:
            left, right = text.split(separator, 1)
            numerator = _number(left)
            denominator = _number(right)
            if numerator is None or denominator in (None, 0):
                return None
            return numerator / denominator
    return _number(text)


def _fps(stream: dict[str, Any]) -> float | None:
    for field in ("avg_frame_rate", "r_frame_rate"):
        value = _ratio(stream.get(field))
        if value and value > 0:
            return value
    return None


def _empty_result(path: Path, error: str) -> ShortPreflightResult:
    size = path.stat().st_size if path.is_file() else 0
    return ShortPreflightResult(file_path=str(path.resolve()), file_bytes=size, errors=(error,))


def preflight_short(
    file_path: str | Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> ShortPreflightResult:
    """Probe one partner MP4 and apply the Stage 6 Short admission policy."""
    path = Path(file_path)
    if not path.exists():
        return _empty_result(path, f"檔案不存在: {path}")
    if not path.is_file():
        return _empty_result(path, f"不是 regular file: {path}")
    if path.stat().st_size == 0:
        return _empty_result(path, f"檔案是空的: {path}")

    try:
        proc = runner(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _empty_result(path, f"ffprobe 執行失敗: {exc}")
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip()[-300:] or f"exit {proc.returncode}"
        return _empty_result(path, f"ffprobe 失敗: {detail}")
    try:
        payload = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        return _empty_result(path, f"ffprobe JSON 無法解析: {exc}")
    if not isinstance(payload, dict):
        return _empty_result(path, "ffprobe JSON 根節點不是 object")

    streams = payload.get("streams")
    streams = streams if isinstance(streams, list) else []
    video = next(
        (
            stream
            for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "video"
        ),
        None,
    )
    audio = next(
        (
            stream
            for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "audio"
        ),
        None,
    )
    fmt = payload.get("format") if isinstance(payload.get("format"), dict) else {}

    errors: list[str] = []
    warnings: list[str] = []
    if video is None:
        errors.append("沒有 video stream")
        video = {}
    if audio is None:
        errors.append("沒有 audio stream")
        audio = {}

    duration = _number(fmt.get("duration"))
    if duration is None:
        duration = _number(video.get("duration"))
    duration = duration or 0.0
    if not 3 <= duration <= 180:
        errors.append(f"duration 必須在 3–180 秒，目前 {duration:.3f} 秒")

    width = _positive_int(video.get("width"))
    height = _positive_int(video.get("height"))
    if width is None or height is None:
        errors.append("width/height 無法解析")

    sar_text = video.get("sample_aspect_ratio")
    dar_text = video.get("display_aspect_ratio")
    parsed_sar = _ratio(sar_text)
    reported_dar = _ratio(dar_text)
    if width and height:
        display_ratio = (
            width * parsed_sar / height
            if parsed_sar is not None
            else reported_dar or width / height
        )
    else:
        display_ratio = None
    if display_ratio is not None and display_ratio > 1.001:
        errors.append(
            f"Short 必須是 vertical 或 square；display aspect ratio={display_ratio:.4f}"
        )

    video_codec = str(video.get("codec_name") or "").lower() or None
    audio_codec = str(audio.get("codec_name") or "").lower() or None
    frame_rate = _fps(video)
    container = str(fmt.get("format_name") or "") or None

    if width != 1080 or height != 1920 or (
        parsed_sar is not None and abs(parsed_sar - 1.0) > 0.001
    ):
        warnings.append("建議交付 1080×1920、square pixels")
    if display_ratio is not None and not (
        abs(display_ratio - 9 / 16) <= 0.01 or abs(display_ratio - 1.0) <= 0.01
    ):
        warnings.append(f"非常用 display aspect ratio: {display_ratio:.4f}（建議 9:16 或 1:1）")
    if video and video_codec != "h264":
        warnings.append(f"video codec 建議 H.264，目前 {video_codec or 'unknown'}")
    if audio and audio_codec != "aac":
        warnings.append(f"audio codec 建議 AAC，目前 {audio_codec or 'unknown'}")
    if frame_rate is None or not 23 <= frame_rate <= 60:
        warnings.append(
            f"fps 建議在 23–60，目前 {frame_rate:.3f}" if frame_rate is not None else "fps 無法解析"
        )
    if 120 < duration <= 180:
        warnings.append("片長超過內部 120 秒內容規格，但仍符合 YouTube 3 分鐘上限")

    return ShortPreflightResult(
        file_path=str(path.resolve()),
        file_bytes=path.stat().st_size,
        container_format=container,
        duration_sec=round(duration, 3),
        width=width,
        height=height,
        sample_aspect_ratio=str(sar_text) if sar_text else None,
        reported_display_aspect_ratio=str(dar_text) if dar_text else None,
        display_aspect_ratio=round(display_ratio, 6) if display_ratio is not None else None,
        video_codec=video_codec,
        audio_codec=audio_codec,
        fps=round(frame_rate, 6) if frame_rate is not None else None,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )
