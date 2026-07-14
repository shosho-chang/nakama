"""Phase 0 / Q2: dump real get_activity_exercise_sets JSON + field/null report.

Lists strength_training activities in a window, dumps each activity's RAW
exerciseSets JSON to spike/samples/, and prints a field-shape + null-behavior
report so we can lock the schema-validation adapter (v2 WP1, §7 schema-drift risk).

Run AFTER spike/garmin_auth_spike.py has produced a token store.
    python spike/dump_exercise_sets.py --since 8w
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import login  # noqa: E402

SAMPLES = Path(__file__).resolve().parent / "samples"


def _parse_since(s: str) -> date:
    m = re.fullmatch(r"(\d+)([dw])", s.strip().lower())
    if not m:
        sys.exit("--since must look like '8w' or '30d'")
    n, unit = int(m.group(1)), m.group(2)
    return date.today() - timedelta(days=n * (7 if unit == "w" else 1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="8w", help="window, e.g. 8w or 30d (default 8w)")
    args = ap.parse_args()

    start, end = _parse_since(args.since), date.today()
    SAMPLES.mkdir(parents=True, exist_ok=True)

    g = login()
    # Phase 0 finding: get_activities_by_date REJECTS "strength_training" as a type
    # arg ("Activity type cannot be an activity sub type" -> HTTP 400). It only
    # accepts PARENT types. Fetch all, filter client-side on activityType.typeKey.
    # This corrects the v2-cited call signature get_activities_by_date(s,e,"strength_training").
    all_acts = g.get_activities_by_date(start.isoformat(), end.isoformat())
    acts = [a for a in all_acts if (a.get("activityType") or {}).get("typeKey") == "strength_training"]
    print(f"[dump] {len(acts)}/{len(all_acts)} strength_training activities {start}..{end}")

    cats: Counter = Counter()
    names: Counter = Counter()
    types: Counter = Counter()
    report = {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "activity_count": len(acts),
        "set_total": 0,
        "active_sets": 0,
        "active_weight_null": 0,   # ACTIVE sets with weight=null (bodyweight / not entered)
        "active_reps_null": 0,
        "weight_min_g": None,
        "weight_max_g": None,
        "name_ever_populated": False,
        "dumped": [],
    }

    for a in acts:
        aid = a.get("activityId")
        data = g.get_activity_exercise_sets(aid)
        out = SAMPLES / f"exercise_sets_{aid}.json"
        out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        report["dumped"].append(out.name)

        for s in (data or {}).get("exerciseSets", []) or []:
            report["set_total"] += 1
            types[s.get("setType")] += 1
            if s.get("setType") == "ACTIVE":
                report["active_sets"] += 1
                w, r = s.get("weight"), s.get("repetitionCount")
                if w is None:
                    report["active_weight_null"] += 1
                else:
                    report["weight_min_g"] = w if report["weight_min_g"] is None else min(report["weight_min_g"], w)
                    report["weight_max_g"] = w if report["weight_max_g"] is None else max(report["weight_max_g"], w)
                if r is None:
                    report["active_reps_null"] += 1
            for ex in s.get("exercises") or []:
                cats[ex.get("category")] += 1
                if ex.get("name") is not None:
                    names[ex.get("name")] += 1
                    report["name_ever_populated"] = True

    report["set_type_counts"] = dict(types)
    report["exercise_categories"] = dict(cats)
    report["exercise_names"] = dict(names)
    (SAMPLES / "_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(report, indent=2, ensure_ascii=False))
    if report["weight_min_g"] is not None:
        lo, hi = report["weight_min_g"], report["weight_max_g"]
        print(
            f"\n[sanity] weight range {lo}..{hi} g = {lo / 1000:.1f}..{hi / 1000:.1f} kg "
            "(grams hypothesis holds iff these look like real loads)"
        )
    print(f"[dump] raw JSON + _report.json written to {SAMPLES}")


if __name__ == "__main__":
    main()
