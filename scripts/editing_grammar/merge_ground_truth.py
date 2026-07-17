# -*- coding: utf-8 -*-
"""Merge shot list + vision classifications + SRT into a ground-truth table.

Inputs:
  shots.yaml            [{shot_id, start, end, duration}]
  classifications.yaml  [{shot_id, type, subtype?, note?}]  (from vision agents)
  transcript .srt       narration cues

Output ground_truth.yaml — one row per shot:
  {shot_id, start, end, duration, type, note, cues: [srt ids], text: "narration"}
plus a stats block (density per minute, duration percentiles by type,
run-length of consecutive aroll).

Usage:
  python merge_ground_truth.py <shots.yaml> <classes.yaml> <srt> <out.yaml>
"""
from __future__ import annotations

import argparse
import re
import statistics
from pathlib import Path

import yaml

_TS = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")


def _ts(s: str) -> float:
    m = _TS.match(s.strip())
    h, mi, sec, ms = (int(g) for g in m.groups())
    return h * 3600 + mi * 60 + sec + ms / 1000


def parse_srt(text: str):
    cues = []
    for block in re.split(r"\n\n+", text.strip()):
        lines = block.strip().splitlines()
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        a, _, b = lines[1].partition("-->")
        cues.append({"id": int(lines[0]), "start": _ts(a), "end": _ts(b),
                     "text": " ".join(x.strip() for x in lines[2:])})
    return cues


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("shots")
    ap.add_argument("classes")
    ap.add_argument("srt")
    ap.add_argument("out")
    args = ap.parse_args()

    shots = yaml.safe_load(Path(args.shots).read_text(encoding="utf-8"))
    classes = {c["shot_id"]: c for c in
               yaml.safe_load(Path(args.classes).read_text(encoding="utf-8"))}
    cues = parse_srt(Path(args.srt).read_text(encoding="utf-8-sig"))

    rows = []
    for s in shots:
        c = classes.get(s["shot_id"], {})
        overlap = [q for q in cues
                   if q["end"] > s["start"] + 0.05 and q["start"] < s["end"] - 0.05]
        rows.append({
            **s,
            "type": c.get("type", "UNCLASSIFIED"),
            "note": c.get("note"),
            "cues": [q["id"] for q in overlap],
            "text": "".join(q["text"] for q in overlap)[:120],
        })

    broll = [r for r in rows if r["type"] not in ("aroll", "UNCLASSIFIED")]
    durs = sorted(r["duration"] for r in broll)
    by_type: dict[str, list[float]] = {}
    for r in broll:
        by_type.setdefault(r["type"], []).append(r["duration"])
    total_min = shots[-1]["end"] / 60 if shots else 0

    # B-roll「事件」：連續 B-roll 鏡合併為一個 cutaway 事件（audit 2026-07-18：
    # 與 guardrail 的 cutaway 決策同單位；「一事件多鏡快切」另計 shots_per_event）
    events: list[list[dict]] = []
    aroll_runs: list[float] = []
    run = 0.0
    for r in rows:
        if r["type"] == "aroll":
            run += r["duration"]
            continue
        if run > 0:
            aroll_runs.append(run)
            run = 0.0
        if events and abs(events[-1][-1]["end"] - r["start"]) < 0.05:
            events[-1].append(r)
        else:
            events.append([r])
    if run > 0:
        aroll_runs.append(run)
    ev_shot_counts = sorted(len(e) for e in events)
    runs_sorted = sorted(aroll_runs)

    stats = {
        "total_shots": len(rows),
        "broll_shots": len(broll),
        "broll_shots_per_minute": round(len(broll) / total_min, 2) if total_min else 0,
        "broll_events": len(events),
        "broll_events_per_minute": round(len(events) / total_min, 2) if total_min else 0,
        "shots_per_event_p50": ev_shot_counts[len(ev_shot_counts) // 2] if events else None,
        "shots_per_event_max": ev_shot_counts[-1] if events else None,
        "broll_duration_p50": round(statistics.median(durs), 2) if durs else None,
        "broll_duration_p90": round(durs[int(len(durs) * 0.9)], 2) if durs else None,
        "broll_time_share": round(sum(durs) / shots[-1]["end"], 3) if durs else 0,
        "aroll_runs": {
            "n": len(runs_sorted),
            "p50": round(statistics.median(runs_sorted), 1) if runs_sorted else None,
            "p90": round(runs_sorted[int(len(runs_sorted) * 0.9)], 1) if runs_sorted else None,
            "max": round(runs_sorted[-1], 1) if runs_sorted else None,
        },
        "by_type": {k: {"n": len(v), "p50": round(statistics.median(v), 2),
                        "max": round(max(v), 2)}
                    for k, v in sorted(by_type.items())},
    }
    Path(args.out).write_text(
        yaml.dump({"stats": stats, "shots": rows}, allow_unicode=True, sort_keys=False),
        encoding="utf-8")
    print(yaml.dump(stats, allow_unicode=True, sort_keys=False))


if __name__ == "__main__":
    main()
