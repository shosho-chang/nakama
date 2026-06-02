"""Single-store edit log for foundry storyboard mutations (ADR-032 §9).

Only `replan` actions append here — bare field edits do NOT write. The log
doubles as raw material for the promote-to-example UI (PR-5) which curates
high-signal entries into agents/foundry/examples/ for future few-shot.

JSONL one entry per line:
    {"timestamp": "2026-05-26T10:32:14+08:00", "episode_id": "ep-001",
     "beat_id": 7, "action": "replan", "before": {...}, "after": {...},
     "user_note": "this is too generic",
     "diff": [["-", 7, {...}], ["+", 7, {...}]]}

ADR-038 §D7 enrichment: when callers pass ``storyboard_before`` /
``storyboard_after`` (the full episode storyboards bracketing the edit), the
entry includes a Myers-style LCS ``diff`` field. Existing entries without the
field remain readable (treated as ``None``).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.foundry.storyboard_diff import diff_storyboards

_LOG_DIR = Path(__file__).parent / "edit_log"


def _log_path(episode_id: str) -> Path:
    return _LOG_DIR / f"{episode_id}.jsonl"


def append_entry(
    episode_id: str,
    beat_id: int,
    action: str,
    before: dict[str, Any],
    after: dict[str, Any],
    user_note: str | None = None,
    *,
    storyboard_before: list[dict[str, Any]] | None = None,
    storyboard_after: list[dict[str, Any]] | None = None,
    edit_ops: list[dict[str, Any]] | None = None,
) -> Path:
    """Append a replan entry to the episode's edit log. Returns the file path.

    When both ``storyboard_before`` and ``storyboard_after`` are provided, the
    persisted entry includes a Myers-style LCS ``diff`` field (ADR-038 §D7).
    Otherwise ``diff`` is ``None`` for backward compat.

    ADR-038 §D3 enrichment: when callers pass ``edit_ops`` (a list of
    serialised ``BeatEdit`` instances from ``beat_editor``), the entry records
    the typed op trail alongside the LCS diff. Existing entries written before
    this enrichment land as ``None`` on read.

    Raises ValueError if action is not in the writable set (currently {"replan",
    "replan-visual"} — edit-field actions are intentionally not logged per
    ADR-032 §9).
    """
    if action not in {"replan", "replan-visual"}:
        raise ValueError(f"edit_log only records replan actions, got {action!r} (ADR-032 §9)")
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    diff: list[list[Any]] | None = None
    if storyboard_before is not None and storyboard_after is not None:
        diff = [
            [op, bid, beat]
            for op, bid, beat in diff_storyboards(storyboard_before, storyboard_after)
        ]
    entry = {
        "timestamp": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "episode_id": episode_id,
        "beat_id": beat_id,
        "action": action,
        "before": before,
        "after": after,
        "user_note": user_note,
        "diff": diff,
        "edit_ops": edit_ops,
    }
    path = _log_path(episode_id)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


def read_entries(episode_id: str) -> list[dict[str, Any]]:
    """Return all entries for an episode (or empty list if no log file).

    Entries written before ADR-038 §D7 lack the ``diff`` field; this reader
    fills it in as ``None`` so callers can treat the schema uniformly.
    """
    path = _log_path(episode_id)
    if not path.exists():
        return []
    entries = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    for entry in entries:
        entry.setdefault("diff", None)
        entry.setdefault("edit_ops", None)
    return entries
