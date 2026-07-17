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
    ap.add_argument("--min-coverage", type=float, default=0.5)
    ap.add_argument("--near-sec", type=float, default=5.0)
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

    # 判準（audit 2026-07-18 改版）：雙向覆蓋率 max(inter/len_sb, inter/len_gt)
    # ≥ coverage 判位置命中 — 解決長短懸殊區間 IoU 失效（完全包含卻落榜）。
    # 成績三欄：位置命中 / near-miss（邊界距 ≤ near_sec）/ 真 miss。
    def coverage(a, b):
        lo, hi = max(a[0], b[0]), min(a[1], b[1])
        inter = max(0.0, hi - lo)
        if inter <= 0:
            return 0.0
        return max(inter / (a[1] - a[0]), inter / (b[1] - b[0]))

    def gap(a, b):
        if b[0] > a[1]:
            return b[0] - a[1]
        if a[0] > b[1]:
            return a[0] - b[1]
        return 0.0

    hits, near, misses = [], [], []
    matched_beats: set[int] = set()
    for g in gt_broll:
        gi = interval(g)
        best, best_cov = None, 0.0
        for c in sb_cut:
            v = coverage(gi, (c["start"], c["end"]))
            if v > best_cov:
                best, best_cov = c, v
        if best and best_cov >= args.min_coverage:
            matched_beats.add(best["beat_id"])
            hits.append({"gt": g, "beat": best, "coverage": round(best_cov, 2),
                         "type_match": best["component"] == g["type"],
                         "offset_s": round(g["start"] - best["start"], 1)})
            continue
        nearest = min(sb_cut, key=lambda c: gap(gi, (c["start"], c["end"]))) if sb_cut else None
        if nearest and gap(gi, (nearest["start"], nearest["end"])) <= args.near_sec:
            near.append({"gt": g, "nearest_beat": nearest,
                         "gap_s": round(gap(gi, (nearest["start"], nearest["end"])), 1)})
        else:
            misses.append(g)

    fp_true, fp_near = [], []
    for c in sb_cut:
        if c["beat_id"] in matched_beats:
            continue
        ci = (c["start"], c["end"])
        g = min(gt_broll, key=lambda g: gap(interval(g), ci)) if gt_broll else None
        d = gap(interval(g), ci) if g else 999
        (fp_near if d <= args.near_sec else fp_true).append({**c, "nearest_gt_gap_s": round(d, 1)})

    summary = {
        "gt_broll_shots": len(gt_broll),
        "sb_cutaways": len(sb_cut),
        "hits": len(hits),
        "hits_type_match": sum(1 for h in hits if h["type_match"]),
        "near_misses": len(near),
        "true_misses": len(misses),
        "recall_position": round(len(hits) / len(gt_broll), 2) if gt_broll else None,
        "recall_with_near": round((len(hits) + len(near)) / len(gt_broll), 2) if gt_broll else None,
        "fp_near": len(fp_near),
        "fp_true": len(fp_true),
    }
    Path(args.out).write_text(
        yaml.dump({"summary": summary, "hits": hits, "near_misses": near,
                   "true_misses": misses, "fp_near": fp_near, "fp_true": fp_true},
                  allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(yaml.dump(summary, sort_keys=False))


if __name__ == "__main__":
    main()
