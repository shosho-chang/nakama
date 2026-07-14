"""Versioned exercise -> muscle-group mapping for WP2 hard-set counting.

Keyed primarily on the Garmin FIT ``category`` enum (stable even when
``exercise_name`` is null). Unmapped categories -> 'unknown' and are EXCLUDED
from per-muscle stats — never silently miscounted (v2 §7 enum-mapping risk).
Bump VERSION on any change so stored stats can be re-derived.
"""

from __future__ import annotations

from typing import Optional

VERSION = 1

# Garmin FIT exercise category -> muscle group. Categories seen in real Fenix 8
# dumps (SHOULDER_PRESS, BENCH_PRESS, FLYE, PULL_UP, TRICEPS_EXTENSION, LUNGE,
# CURL) plus the common remainder. WARM_UP / UNKNOWN deliberately absent.
_CATEGORY_MUSCLE = {
    "BENCH_PRESS": "chest",
    "FLYE": "chest",
    "PUSH_UP": "chest",
    "SHOULDER_PRESS": "shoulders",
    "LATERAL_RAISE": "shoulders",
    "SHOULDER_STABILITY": "shoulders",
    "SHRUG": "traps",
    "PULL_UP": "back",
    "ROW": "back",
    "LAT_PULL_DOWN": "back",
    "PULL_DOWN": "back",
    "DEADLIFT": "posterior_chain",
    "HYPEREXTENSION": "posterior_chain",
    "CURL": "biceps",
    "TRICEPS_EXTENSION": "triceps",
    "SQUAT": "quads",
    "LEG_PRESS": "quads",
    "LUNGE": "quads",
    "LEG_EXTENSION": "quads",
    "LEG_CURL": "hamstrings",
    "HIP_RAISE": "glutes",
    "HIP_SWING": "glutes",
    "CALF_RAISE": "calves",
    "PLANK": "core",
    "CRUNCH": "core",
    "CORE": "core",
}


def muscle_group(category: Optional[str], name: Optional[str] = None) -> str:
    """Resolve a muscle group from the FIT category (name reserved for future
    overrides). Returns 'unknown' for unmapped / WARM_UP / None."""
    if category and category in _CATEGORY_MUSCLE:
        return _CATEGORY_MUSCLE[category]
    return "unknown"
