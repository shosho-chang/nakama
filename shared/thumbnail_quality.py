"""Lightweight visual QA for rendered thumbnail candidates.

This is intentionally deterministic and local. It does not try to judge taste
or predict CTR; it catches mechanical problems that should never reach upload:
invalid images, wrong dimensions, blank/near-blank renders, and extremely low
contrast that will usually make title text unreadable at thumbnail size.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from PIL import Image, ImageStat, UnidentifiedImageError

QaStatus = Literal["pass", "warn", "fail"]


@dataclass(frozen=True)
class ThumbnailQaCheck:
    """One deterministic QA check result."""

    check_id: str
    status: QaStatus
    message: str
    value: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "status": self.status,
            "message": self.message,
            "value": self.value,
        }


@dataclass(frozen=True)
class ThumbnailQaResult:
    """Aggregate visual QA result for a rendered PNG."""

    status: QaStatus
    width: int | None
    height: int | None
    file_size_bytes: int | None
    checks: tuple[ThumbnailQaCheck, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "width": self.width,
            "height": self.height,
            "file_size_bytes": self.file_size_bytes,
            "checks": [check.to_dict() for check in self.checks],
        }


def evaluate_thumbnail_render(
    path: Path,
    *,
    expected_width: int = 1280,
    expected_height: int = 720,
) -> ThumbnailQaResult:
    """Evaluate a rendered thumbnail candidate.

    The thresholds are deliberately conservative. They are meant to block
    obviously broken files and flag likely readability issues, not replace a
    human final visual review.
    """

    checks: list[ThumbnailQaCheck] = []
    path = Path(path)
    file_size: int | None = None

    if not path.is_file():
        checks.append(
            ThumbnailQaCheck("file_exists", "fail", "thumbnail candidate file is missing")
        )
        return _result(None, None, file_size, checks)

    file_size = path.stat().st_size
    checks.append(
        ThumbnailQaCheck(
            "file_size",
            "warn" if file_size < 2048 else "pass",
            "file is unusually small" if file_size < 2048 else "file size looks plausible",
            file_size,
        )
    )

    try:
        with Image.open(path) as img:
            img.load()
            width, height = img.size
            mode = img.mode
            gray = img.convert("L").resize((320, 180))
    except (OSError, UnidentifiedImageError) as exc:
        checks.append(
            ThumbnailQaCheck(
                "image_readable",
                "fail",
                f"cannot decode image as a valid thumbnail: {type(exc).__name__}",
            )
        )
        return _result(None, None, file_size, checks)

    checks.append(ThumbnailQaCheck("image_readable", "pass", f"valid image mode={mode}"))

    expected = f"{expected_width}x{expected_height}"
    actual = f"{width}x{height}"
    checks.append(
        ThumbnailQaCheck(
            "dimensions",
            "pass" if (width, height) == (expected_width, expected_height) else "fail",
            "dimensions match YouTube thumbnail export target"
            if (width, height) == (expected_width, expected_height)
            else f"expected {expected}, got {actual}",
            {"expected": expected, "actual": actual},
        )
    )

    aspect = width / height if height else 0
    target_aspect = expected_width / expected_height
    checks.append(
        ThumbnailQaCheck(
            "aspect_ratio",
            "pass" if abs(aspect - target_aspect) <= 0.02 else "fail",
            "aspect ratio is close to 16:9"
            if abs(aspect - target_aspect) <= 0.02
            else f"aspect ratio should be 16:9, got {aspect:.3f}",
            round(aspect, 4),
        )
    )

    stat = ImageStat.Stat(gray)
    luminance_stddev = float(stat.stddev[0])
    if luminance_stddev < 2.0:
        variance_status: QaStatus = "fail"
        variance_message = "image is near-blank"
    elif luminance_stddev < 12.0:
        variance_status = "warn"
        variance_message = "image has low visual variation"
    else:
        variance_status = "pass"
        variance_message = "image has enough visual variation"
    checks.append(
        ThumbnailQaCheck(
            "luminance_variation",
            variance_status,
            variance_message,
            round(luminance_stddev, 2),
        )
    )

    p05, p95 = _histogram_percentile(gray.histogram(), 0.05), _histogram_percentile(
        gray.histogram(), 0.95
    )
    dynamic_range = p95 - p05
    if dynamic_range < 25:
        contrast_status: QaStatus = "fail"
        contrast_message = "image contrast is too low for reliable thumbnail readability"
    elif dynamic_range < 55:
        contrast_status = "warn"
        contrast_message = "image contrast is marginal; inspect title readability"
    else:
        contrast_status = "pass"
        contrast_message = "image contrast looks usable"
    checks.append(
        ThumbnailQaCheck(
            "contrast_proxy",
            contrast_status,
            contrast_message,
            {"p05": p05, "p95": p95, "range": dynamic_range},
        )
    )

    return _result(width, height, file_size, checks)


def _histogram_percentile(histogram: list[int], percentile: float) -> int:
    total = sum(histogram)
    if total <= 0:
        return 0
    target = total * percentile
    cumulative = 0
    for value, count in enumerate(histogram):
        cumulative += count
        if cumulative >= target:
            return value
    return len(histogram) - 1


def _result(
    width: int | None,
    height: int | None,
    file_size: int | None,
    checks: list[ThumbnailQaCheck],
) -> ThumbnailQaResult:
    if any(check.status == "fail" for check in checks):
        status: QaStatus = "fail"
    elif any(check.status == "warn" for check in checks):
        status = "warn"
    else:
        status = "pass"
    return ThumbnailQaResult(
        status=status,
        width=width,
        height=height,
        file_size_bytes=file_size,
        checks=tuple(checks),
    )
