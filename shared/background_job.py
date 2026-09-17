"""Small durable receipt for bounded fire-and-forget web jobs."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


def atomic_job_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        Path(tmp_name).unlink(missing_ok=True)


def load_job(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def new_job(*, status: str, timeout_seconds: int, **identity: object) -> dict:
    now = datetime.now(timezone.utc)
    return {
        **identity,
        "attempt_id": uuid.uuid4().hex,
        "status": status,
        "pid": None,
        "started_at": now.isoformat(),
        "deadline_at": (now + timedelta(seconds=timeout_seconds)).isoformat(),
        "exit_code": None,
    }


def job_expired(payload: dict, *, now: datetime | None = None) -> bool:
    try:
        deadline = datetime.fromisoformat(str(payload["deadline_at"]))
    except (KeyError, TypeError, ValueError):
        return True
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) >= deadline


def job_progress(payload: dict, *, now: datetime | None = None) -> tuple[int, int] | None:
    """(已經跑了幾秒, 這次嘗試的上限幾秒)。讀不出時間就回 None。

    畫面寧可不說，也不要說一個算錯的秒數——「還要多久」這種話一旦不準，比不說更糟。
    """
    try:
        started = datetime.fromisoformat(str(payload["started_at"]))
        deadline = datetime.fromisoformat(str(payload["deadline_at"]))
    except (KeyError, TypeError, ValueError):
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    limit = round((deadline - started).total_seconds())
    if limit <= 0:
        return None
    elapsed = round(((now or datetime.now(timezone.utc)) - started).total_seconds())
    return max(elapsed, 0), limit
