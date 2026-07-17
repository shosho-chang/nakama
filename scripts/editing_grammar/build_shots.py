# -*- coding: utf-8 -*-
"""Parse ffmpeg select/metadata scene-score dump into a shot list.

Input: metadata=print file produced by
  -vf "scale=480:-2,select='gt(scene,0.04)',metadata=print:file=..."
Each retained frame appears as:
  frame:N    pts:...     pts_time:12.345
  lavfi.scene_score=0.123456

Usage:
  python build_shots.py <scene_scores.txt> <duration_sec> <out_shots.yaml>
         [--threshold 0.30] [--min-shot 0.30]

Threshold calibration: run with --histogram to print the score distribution
before committing to a threshold.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import yaml

_PTS = re.compile(r"pts_time:([0-9.]+)")
_SCORE = re.compile(r"lavfi\.scene_score=([0-9.]+)")


def parse_scores(path: Path) -> list[tuple[float, float]]:
    events: list[tuple[float, float]] = []
    pts: float | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _PTS.search(line)
        if m:
            pts = float(m.group(1))
            continue
        m = _SCORE.search(line)
        if m and pts is not None:
            events.append((pts, float(m.group(1))))
            pts = None
    return events


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scores")
    ap.add_argument("duration", type=float)
    ap.add_argument("out", nargs="?")
    ap.add_argument("--threshold", type=float, default=0.30)
    ap.add_argument("--min-shot", type=float, default=0.30,
                    help="merge cuts closer than this (flash/transition debounce)")
    ap.add_argument("--histogram", action="store_true")
    args = ap.parse_args()

    events = parse_scores(Path(args.scores))
    if args.histogram:
        import collections
        buckets = collections.Counter()
        for _, s in events:
            buckets[round(s, 1)] += 1
        for k in sorted(buckets):
            print(f"{k:.1f}: {buckets[k]}")
        print(f"total events: {len(events)}")
        return

    cuts = [t for t, s in events if s >= args.threshold]
    cuts.sort()
    merged: list[float] = []
    for t in cuts:
        if merged and t - merged[-1] < args.min_shot:
            continue
        merged.append(t)

    bounds = [0.0] + merged + [args.duration]
    shots = []
    for i in range(len(bounds) - 1):
        start, end = bounds[i], bounds[i + 1]
        shots.append({
            "shot_id": i + 1,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
        })
    Path(args.out).write_text(
        yaml.dump(shots, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"cuts={len(merged)} shots={len(shots)} "
          f"median={sorted(s['duration'] for s in shots)[len(shots)//2]:.2f}s")


if __name__ == "__main__":
    main()
