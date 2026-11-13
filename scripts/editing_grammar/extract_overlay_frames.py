# -*- coding: utf-8 -*-
"""Extract dense 1s-interval frames from aroll shots for overlay detection.

Scene-detect misses overlays that slide in over a static talking-head shot
(audit 2026-07-18 finding). Second pass: for every shot classified aroll in
ground_truth.yaml, grab a frame every --interval seconds so vision agents can
spot keyword cards / banners / insets appearing mid-shot.

Frames named shot{NNN}_t{abs_seconds}.jpg (absolute video time, zero-padded).

Usage:
  python extract_overlay_frames.py <video> <ground_truth.yaml> <out_dir>
         [--interval 1.0] [--workers 6]
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
        [
            "ffmpeg",
            "-hide_banner",
            "-v",
            "error",
            "-ss",
            f"{t:.3f}",
            "-i",
            video,
            "-frames:v",
            "1",
            "-vf",
            "scale=960:-2",
            "-q:v",
            "4",
            str(out),
        ],
        check=True,
        timeout=120,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("ground_truth")
    ap.add_argument("out_dir")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    gt = yaml.safe_load(Path(args.ground_truth).read_text(encoding="utf-8"))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    jobs: list[tuple[float, Path]] = []
    n_shots = 0
    for s in gt["shots"]:
        if s["type"] != "aroll":
            continue
        n_shots += 1
        t = s["start"] + 0.2
        while t < s["end"] - 0.05:
            jobs.append((t, out_dir / f"shot{s['shot_id']:03d}_t{t:08.2f}.jpg"))
            t += args.interval

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(grab, args.video, t, p) for t, p in jobs]
        for f in futs:
            f.result()
    print(f"{n_shots} aroll shots -> {len(jobs)} dense frames -> {out_dir}")


if __name__ == "__main__":
    main()
