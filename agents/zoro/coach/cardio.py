"""有氧一覽 data layer — pure aggregation over cardio_sessions rows for the panel.

Per-modality totals (count / distance / duration) over the synced window + a
recent-sessions list. Design-neutral; feeds the Bridge 有氧一覽 section.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

_LABEL = {
    "running": "跑步", "treadmill_running": "跑步機", "trail_running": "越野跑",
    "cycling": "單車", "indoor_cycling": "室內單車", "virtual_ride": "虛擬單車",
    "lap_swimming": "游泳", "open_water_swimming": "開放水域",
}
_ICON = {
    "running": "🏃", "treadmill_running": "🏃", "trail_running": "🏃",
    "cycling": "🚴", "indoor_cycling": "🚴", "virtual_ride": "🚴",
    "lap_swimming": "🏊", "open_water_swimming": "🏊",
}


def label(activity_type: str) -> str:
    return _LABEL.get(activity_type, activity_type or "其他")


def icon(activity_type: str) -> str:
    return _ICON.get(activity_type, "•")


def cardio_overview(sessions: Iterable[dict], *, recent_n: int = 8) -> dict:
    """Per-modality totals + a recent-sessions list, shaped for the template."""
    sessions = list(sessions)
    agg: dict = defaultdict(lambda: {"count": 0, "distance_km": 0.0, "duration_min": 0.0})
    for s in sessions:
        a = agg[s.get("activity_type")]
        a["count"] += 1
        a["distance_km"] += (s.get("distance_m") or 0) / 1000
        a["duration_min"] += (s.get("duration_sec") or 0) / 60
    modalities = {
        t: {
            "label": label(t),
            "icon": icon(t),
            "count": v["count"],
            "distance_km": round(v["distance_km"], 1),
            "duration_min": round(v["duration_min"]),
        }
        for t, v in agg.items()
    }
    recent = sorted(sessions, key=lambda s: s.get("performed_at") or "", reverse=True)[:recent_n]
    recent_view = [
        {
            "activity_type": s.get("activity_type"),
            "label": label(s.get("activity_type")),
            "icon": icon(s.get("activity_type")),
            "performed_at": s.get("performed_at"),
            "distance_km": round((s.get("distance_m") or 0) / 1000, 1),
            "duration_min": round((s.get("duration_sec") or 0) / 60),
            "avg_hr": s.get("avg_hr"),
        }
        for s in recent
    ]
    return {"modalities": modalities, "recent": recent_view}
