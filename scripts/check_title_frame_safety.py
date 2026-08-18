"""Fail closed when a rendered title overlay crosses the visible safe area.

The check runs on the actual alpha MOV, frame by frame.  Pattern rails are
allowed to span the canvas at the top/bottom; all remaining visible pixels
must stay inside the requested margin.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _content_bbox(alpha: np.ndarray) -> tuple[int, int, int, int] | None:
    """Return visible-content bbox after excluding intentional full-width rails."""
    height, width = alpha.shape
    visible = alpha > 20
    row_fill = visible.mean(axis=1)
    rail_rows = row_fill >= 0.90
    # Branding rails are only legal near the top or below the title stage.
    legal_rail_zone = (np.arange(height) < height * 0.20) | (np.arange(height) > height * 0.68)
    visible[rail_rows & legal_rail_zone, :] = False
    ys, xs = np.nonzero(visible)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def _probe(path: Path) -> tuple[int, int, float]:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe cannot open {path}: {proc.stderr[-200:]}")
    stream = json.loads(proc.stdout)["streams"][0]
    numerator, denominator = str(stream.get("r_frame_rate", "30/1")).split("/", 1)
    return int(stream["width"]), int(stream["height"]), float(numerator) / float(denominator)


def _read_exact(pipe, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining:
        chunk = pipe.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def scan(path: Path, margin: int = 24) -> dict:
    width, height, fps = _probe(path)
    decoder = subprocess.Popen(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            "alphaextract",
            "-pix_fmt",
            "gray",
            "-f",
            "rawvideo",
            "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if decoder.stdout is None:
        raise RuntimeError(f"cannot decode {path}")
    failures: list[dict] = []
    frame_index = 0
    frame_bytes = width * height
    while True:
        raw = _read_exact(decoder.stdout, frame_bytes)
        if not raw:
            break
        if len(raw) != frame_bytes:
            decoder.kill()
            raise RuntimeError(f"short alpha frame while decoding {path}")
        alpha = np.frombuffer(raw, dtype=np.uint8).reshape((height, width))
        bbox = _content_bbox(alpha)
        if bbox is not None:
            x0, y0, x1, y1 = bbox
            violations = []
            if x0 < margin:
                violations.append(f"left={x0}")
            if x1 >= width - margin:
                violations.append(f"right={x1}")
            if y0 < margin:
                violations.append(f"top={y0}")
            if y1 >= height - margin:
                violations.append(f"bottom={y1}")
            if violations:
                failures.append(
                    {
                        "frame": frame_index,
                        "time": round(frame_index / fps, 3),
                        "bbox": list(bbox),
                        "violations": violations,
                    }
                )
        frame_index += 1
    stderr = decoder.stderr.read().decode("utf-8", errors="replace") if decoder.stderr else ""
    returncode = decoder.wait()
    if returncode != 0:
        raise RuntimeError(f"ffmpeg alpha decode failed for {path}: {stderr[-300:]}")
    return {
        "path": str(path),
        "width": width,
        "height": height,
        "frames": frame_index,
        "margin": margin,
        "failure_frames": len(failures),
        "first_failures": failures[:12],
        "ok": not failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="逐幀檢查 alpha title MOV 是否越界")
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--margin", type=int, default=24)
    args = parser.parse_args()
    results = [scan(path, args.margin) for path in args.files]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(result["ok"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
