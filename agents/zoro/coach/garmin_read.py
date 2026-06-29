"""Garmin strength read-back adapter (WP1).

Two layers:
  * PURE (no garminconnect): ``validate_exercise_sets_shape`` + ``parse_exercise_sets``
    turn the raw exerciseSets dict into ``StrengthSet`` rows. These are unit-tested.
  * IO (lazy garminconnect import): ``login`` + ``fetch_*``. garminconnect is an
    OPTIONAL dep (Python >=3.12), imported lazily so the pure layer stays importable
    and testable without it.

The Garmin endpoint (/activity-service/activity/{id}/exerciseSets) is a bare
passthrough with NO schema guarantee, so we validate the load-bearing shape and
RAISE on mutation rather than silently miscompute (v2 §7 schema-drift risk).

Phase 0 field findings (real Fenix 8 dump) baked in here:
  * weight is GRAMS -> kg (None or 0.0 == no load); reps None on REST / 0 on aborted.
  * exercises[].category/name are uppercase FIT enums; name often None; take [0].
  * extra keys (messageIndex, wktStepIndex) tolerated; setType in {ACTIVE, REST}.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from shared.strength_sets_store import StrengthSet

_TAIPEI = timezone(timedelta(hours=8))
_VALID_SET_TYPES = {"ACTIVE", "REST"}
_STRENGTH_TYPE_KEY = "strength_training"


class SchemaValidationError(Exception):
    """Raised when the raw exerciseSets payload doesn't match the known shape."""


def validate_exercise_sets_shape(raw: Any) -> None:
    """Reject unknown structure (fail loud, never silently miscompute).

    Strict on the load-bearing fields (top-level ``exerciseSets`` list + per-set
    ``setType`` enum + ``exercises`` being a list); lenient on extra/unknown keys
    so new Garmin fields (e.g. messageIndex) don't trip the guard.
    """
    if not isinstance(raw, dict):
        raise SchemaValidationError(f"expected dict, got {type(raw).__name__}")
    sets = raw.get("exerciseSets")
    if not isinstance(sets, list):
        raise SchemaValidationError("missing or non-list 'exerciseSets'")
    for i, s in enumerate(sets):
        if not isinstance(s, dict):
            raise SchemaValidationError(f"set[{i}] is not an object")
        if s.get("setType") not in _VALID_SET_TYPES:
            raise SchemaValidationError(f"set[{i}] unknown setType {s.get('setType')!r}")
        ex = s.get("exercises")
        if ex is not None and not isinstance(ex, list):
            raise SchemaValidationError(f"set[{i}] 'exercises' is not a list")


def grams_to_kg(weight_g: Optional[float]) -> Optional[float]:
    """Garmin weights are grams. None stays None; 0.0 stays 0.0 (explicit no load)."""
    if weight_g is None:
        return None
    return round(weight_g / 1000.0, 3)


def _to_aware_iso(start_time: Optional[str]) -> str:
    """exerciseSets startTime is naive local; treat as Asia/Taipei (+08:00)."""
    if not start_time:
        return datetime.now(_TAIPEI).isoformat()
    dt = datetime.fromisoformat(start_time)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_TAIPEI)
    return dt.isoformat()


def parse_exercise_sets(
    raw: dict,
    *,
    activity_id: str,
    source: str = "garmin",
) -> list[StrengthSet]:
    """Validate + parse the raw exerciseSets dict into ``StrengthSet`` rows."""
    validate_exercise_sets_shape(raw)
    out: list[StrengthSet] = []
    for position, s in enumerate(raw["exerciseSets"]):
        set_index = s.get("messageIndex")
        if not isinstance(set_index, int):
            set_index = position
        set_type = s["setType"].lower()
        exercises = s.get("exercises") or []
        primary = exercises[0] if exercises and isinstance(exercises[0], dict) else {}
        category = primary.get("category")
        name = primary.get("name")
        exercise_key = (name or category) if set_type == "active" else None
        out.append(
            StrengthSet(
                activity_id=activity_id,
                set_index=set_index,
                set_type=set_type,
                exercise_key=exercise_key,
                category=category,
                exercise_name=name,
                reps=s.get("repetitionCount"),
                weight_kg=grams_to_kg(s.get("weight")),
                duration_sec=s.get("duration"),
                is_warmup=(category == "WARM_UP"),
                performed_at=_to_aware_iso(s.get("startTime")),
                wkt_step_index=s.get("wktStepIndex"),
                source=source,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# IO layer — lazy garminconnect import (optional dep, Python >=3.12).          #
# --------------------------------------------------------------------------- #

def login():
    """Resume-first Garmin login from the token store (data/garmin/).

    Mirrors shared/google_calendar.py: a one-time local MFA login produces the
    token (see spike/garmin_auth_spike.py), copied to the VPS; here we just
    resume + silently refresh. Never prompts interactively.
    """
    import os
    from pathlib import Path

    from garminconnect import Garmin  # lazy: optional dep

    tokenstore = os.environ.get(
        "GARMINTOKENS",
        str(Path(os.environ.get("NAKAMA_DATA_DIR", "data")) / "garmin"),
    )
    g = Garmin()
    g.login(tokenstore)
    return g


def fetch_strength_activities(client, since: date, until: Optional[date] = None) -> list[dict]:
    """List strength_training activities in [since, until].

    NOTE (Phase 0): get_activities_by_date REJECTS "strength_training" as a type
    arg (it is a sub-type -> HTTP 400). Fetch all, filter client-side on
    activityType.typeKey.
    """
    until = until or date.today()
    activities = client.get_activities_by_date(since.isoformat(), until.isoformat())
    return [
        a for a in activities
        if (a.get("activityType") or {}).get("typeKey") == _STRENGTH_TYPE_KEY
    ]


def fetch_exercise_sets(client, activity_id) -> dict:
    """Raw passthrough read of one activity's exerciseSets."""
    return client.get_activity_exercise_sets(activity_id)
