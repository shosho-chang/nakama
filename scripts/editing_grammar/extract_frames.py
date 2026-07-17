# -*- coding: utf-8 -*-
"""Extract representative frames per shot for vision classification.

For each shot: frame at start+0.15s and midpoint (plus 80%-point when the
shot is longer than 6s). 960px-wide JPEGs named shot{NNN}_{a|b|c}_{t}.jpg.

Usage:
  python extract_frames.py <video> <shots.yaml> <out_dir> [--workers 8]
"""
from __future__ import annotations

import argparse
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml


def grab(video: str, t: float, out: Path) -> None:
    if out.exists():
        return
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-ss", f"{t:.3f}", "-i", video,
         "-frames:v", "1", "-vf", "scale=960:-2", "-q:v", "4", str(out)],
        check=True, timeout=120)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("shots")
    ap.add_argument("out_dir")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    shots = yaml.safe_load(Path(args.shots).read_text(encoding="utf-8"))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    jobs: list[tuple[float, Path]] = []
    for s in shots:
        sid, start, end, dur = s["shot_id"], s["start"], s["end"], s["duration"]
        points = [("a", min(start + 0.15, end - 0.05)),
                  ("b", start + dur / 2)]
        if dur > 6.0:
            points.append(("c", start + dur * 0.8))
        for tag, t in points:
            jobs.append((t, out_dir / f"shot{sid:03d}_{tag}_{t:.2f}.jpg"))

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(grab, args.video, t, p) for t, p in jobs]
        for f in futs:
            f.result()
    print(f"extracted {len(jobs)} frames -> {out_dir}")


if __name__ == "__main__":
    main()
