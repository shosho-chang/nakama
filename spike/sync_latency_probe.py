"""Phase 0 / Q5: device->cloud sync latency probe.

Polls get_activities_by_date for TODAY's strength activities and reports the gap
between each activity's END time and 'now' (when it first became visible to the
cloud API). Relevant to the Sunday 20:00 cron reading same-day training.

    python spike/sync_latency_probe.py             # one-shot snapshot
    python spike/sync_latency_probe.py --watch 15  # poll every 15s until one appears
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import login  # noqa: E402


def _poll(g):
    today = date.today().isoformat()
    # Sync latency is type-agnostic; fetch ALL today's activities. (Do NOT pass
    # "strength_training" — it is a sub-type and returns HTTP 400.)
    acts = g.get_activities_by_date(today, today)
    now = datetime.now().astimezone()
    rows = []
    for a in acts:
        start_s = a.get("startTimeLocal") or a.get("startTimeGMT")
        dur = a.get("duration") or 0
        row = {
            "activityId": a.get("activityId"),
            "type": (a.get("activityType") or {}).get("typeKey"),
            "startTimeLocal": start_s,
            "duration_s": dur,
        }
        try:
            st = datetime.fromisoformat(str(start_s).replace("Z", "")).astimezone()
            end_ts = st.timestamp() + float(dur)
            row["cloud_visible_lag_s_since_end"] = round(now.timestamp() - end_ts, 1)
        except Exception:  # noqa: BLE001
            pass
        rows.append(row)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=int, default=0, help="poll interval secs until an activity appears (0=one-shot)")
    ap.add_argument("--timeout", type=int, default=1800, help="max secs to poll in --watch mode")
    args = ap.parse_args()

    g = login()
    if not args.watch:
        for row in _poll(g):
            print(row)
        print(
            "\nNOTE: lag is measured since activity END. True sync latency = "
            "(cloud-visible time) - (wall-clock you pressed Save). Note your Save time to interpret."
        )
        return

    deadline = time.time() + args.timeout
    seen = set()
    while time.time() < deadline:
        for row in _poll(g):
            if row["activityId"] not in seen:
                seen.add(row["activityId"])
                print(f"[{datetime.now().astimezone().isoformat()}] visible: {row}")
        if seen:
            break
        time.sleep(args.watch)
    if not seen:
        print("[sync] no strength activity became visible within timeout")


if __name__ == "__main__":
    main()
