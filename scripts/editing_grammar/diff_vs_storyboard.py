# -*- coding: utf-8 -*-
"""Diff ground-truth shot table vs a Director storyboard.

For each ground-truth B-roll shot: did the storyboard place a cutaway
overlapping it (time IoU > 0.3)? For each storyboard cutaway: does it
correspond to a real B-roll shot? Outputs hit / miss / false-positive lists
with narration text, so the lesson extraction can cite concrete lines.

Usage:
  python diff_vs_storyboard.py <ground_truth.yaml> <storyboard.yaml> <out.yaml>
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml


def interval(r) -> tuple[float, float]:
    return r["start"], r["end"]


def iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    lo, hi = max(a[0], b[0]), min(a[1], b[1])
    inter = max(0.0, hi - lo)
    union = (a[1] - a[0]) + (b[1] - b[0]) - inter
    return inter / union if union > 0 else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ground_truth")
    ap.add_argument("storyboard")
    ap.add_argument("out")
    ap.add_argument("--min-iou", type=float, default=0.3)
    args = ap.parse_args()

    gt = yaml.safe_load(Path(args.ground_truth).read_text(encoding="utf-8"))["shots"]
    sb = yaml.safe_load(Path(args.storyboard).read_text(encoding="utf-8"))

    gt_broll = [r for r in gt if r["type"] not in ("aroll", "UNCLASSIFIED")]
    sb_cut = []
    for b in sb:
        if b.get("broll_decision") != "cutaway" or not b.get("timing"):
            continue
        t = b["timing"]
        sb_cut.append({
            "beat_id": b["beat_id"], "start": t["start"],
            "end": t["start"] + t["duration"],
            "component": (b.get("broll") or {}).get("component"),
            "text": (b.get("start_quote") or "")[:60],
        })

    hits, misses = [], []
    matched_beats: set[int] = set()
    for g in gt_broll:
        best, best_iou = None, 0.0
        for c in sb_cut:
            v = iou(interval(g), (c["start"], c["end"]))
            if v > best_iou:
                best, best_iou = c, v
        if best and best_iou >= args.min_iou:
            matched_beats.add(best["beat_id"])
            hits.append({"gt": g, "beat": best, "iou": round(best_iou, 2),
                         "type_match": best["component"] in (g["type"], None)})
        else:
            misses.append(g)

    false_pos = [c for c in sb_cut if c["beat_id"] not in matched_beats]

    summary = {
        "gt_broll_shots": len(gt_broll),
        "sb_cutaways": len(sb_cut),
        "hits": len(hits),
        "recall": round(len(hits) / len(gt_broll), 2) if gt_broll else None,
        "misses": len(misses),
        "false_positives": len(false_pos),
    }
    Path(args.out).write_text(
        yaml.dump({"summary": summary, "hits": hits, "misses": misses,
                   "false_positives": false_pos},
                  allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(yaml.dump(summary, sort_keys=False))


if __name__ == "__main__":
    main()
