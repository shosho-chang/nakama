"""Tests for agents/franky/r2_backup_verify.py.

Coverage (task prompt §5 verification):
- happy path → status=ok, no alert
- missing R2 env → status=missing, no alert on first day
- second consecutive day missing → Critical alert
- stale snapshot → status=stale
- too-small snapshot → status=too_small
- empty bucket → status=missing
- R2 list_objects failure → status=missing
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from agents.franky import r2_backup_verify
from agents.franky.r2_backup_verify import verify_once
from shared.r2_client import R2Object, R2Unavailable


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fresh_object(
    *, size: int = 10 * 1024 * 1024, age_h: float = 2.0, key: str = "daily/db.tar.zst"
) -> R2Object:
    return R2Object(
        key=key,
        size=size,
        last_modified=_now() - timedelta(hours=age_h),
        etag="abc123",
    )


@pytest.fixture
def _all_env(monkeypatch):
    """Provide all R2 env so R2Client.from_env() doesn't trip on missing."""
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setenv("R2_BUCKET_NAME", "backups")
    yield


def _mock_r2_client(objects: list[R2Object] | Exception):
    """Return a MagicMock R2Client whose list_objects returns the given objects or raises."""
    client = MagicMock()
    if isinstance(objects, Exception):
        client.list_objects.side_effect = objects
    else:
        client.list_objects.return_value = objects
    client.bucket = "backups"
    return client


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_verify_ok_returns_ok_and_no_alert(_all_env):
    client = _mock_r2_client([_fresh_object()])
    with patch.object(r2_backup_verify, "R2Client") as mock_cls:
        mock_cls.from_env.return_value = client
        result = verify_once()
    assert result["status"] == "ok"
    assert result["alert"] is None
    assert "latest=daily/db.tar.zst" in result["detail"]


# ---------------------------------------------------------------------------
# Missing / unavailable
# ---------------------------------------------------------------------------


def test_verify_env_missing_returns_missing(monkeypatch):
    for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"):
        monkeypatch.delenv(k, raising=False)
    result = verify_once()
    assert result["status"] == "missing"
    # First day — below critical threshold
    assert result["alert"] is None


def test_verify_list_fails_returns_missing(_all_env):
    client = _mock_r2_client(R2Unavailable("network flake"))
    with patch.object(r2_backup_verify, "R2Client") as mock_cls:
        mock_cls.from_env.return_value = client
        result = verify_once()
    assert result["status"] == "missing"
    assert "list_objects failed" in result["detail"]


def test_verify_empty_bucket_returns_missing(_all_env):
    client = _mock_r2_client([])
    with patch.object(r2_backup_verify, "R2Client") as mock_cls:
        mock_cls.from_env.return_value = client
        result = verify_once()
    assert result["status"] == "missing"


# ---------------------------------------------------------------------------
# Status discrimination
# ---------------------------------------------------------------------------


def test_verify_stale_snapshot(_all_env):
    client = _mock_r2_client([_fresh_object(age_h=30.0)])
    with patch.object(r2_backup_verify, "R2Client") as mock_cls:
        mock_cls.from_env.return_value = client
        result = verify_once()
    assert result["status"] == "stale"
    assert "30." in result["detail"] or "older" in result["detail"] or "old" in result["detail"]


def test_verify_too_small_snapshot(_all_env):
    client = _mock_r2_client([_fresh_object(size=1024)])  # 1 KB, way under 1 MB min
    with patch.object(r2_backup_verify, "R2Client") as mock_cls:
        mock_cls.from_env.return_value = client
        result = verify_once()
    assert result["status"] == "too_small"


# ---------------------------------------------------------------------------
# Consecutive fail → Critical alert
# ---------------------------------------------------------------------------


def _seed_fail_history(days: int) -> None:
    """Insert N day-distinct failure rows ending one day before today."""
    from shared.state import _get_conn

    conn = _get_conn()
    base = _now() - timedelta(days=1)  # yesterday's failure
    for i in range(days):
        day = (base - timedelta(days=i)).isoformat()
        conn.execute(
            """INSERT INTO r2_backup_checks
                  (checked_at, latest_object_key, latest_object_size,
                   latest_object_mtime, status, detail)
               VALUES (?, NULL, NULL, NULL, 'missing', 'seeded test failure')""",
            (day,),
        )
    conn.commit()


def test_verify_first_day_missing_no_alert(_all_env):
    client = _mock_r2_client([])
    with patch.object(r2_backup_verify, "R2Client") as mock_cls:
        mock_cls.from_env.return_value = client
        result = verify_once()
    assert result["status"] == "missing"
    assert result["alert"] is None  # only 1 consecutive day fail → below threshold of 2


def test_verify_second_consecutive_day_missing_emits_critical(_all_env):
    _seed_fail_history(days=1)  # yesterday already failed
    client = _mock_r2_client([])
    with patch.object(r2_backup_verify, "R2Client") as mock_cls:
        mock_cls.from_env.return_value = client
        result = verify_once()
    assert result["status"] == "missing"
    assert result["alert"] is not None
    assert result["alert"].severity == "critical"
    assert result["alert"].rule_id == "r2_backup_missing"
    assert result["alert"].context["consecutive_fail_days"] >= 2


def test_verify_ok_after_fail_history_clears_alert(_all_env):
    _seed_fail_history(days=3)  # 3 prior day failures
    client = _mock_r2_client([_fresh_object()])  # today is ok
    with patch.object(r2_backup_verify, "R2Client") as mock_cls:
        mock_cls.from_env.return_value = client
        result = verify_once()
    assert result["status"] == "ok"
    assert result["alert"] is None  # ok → no alert regardless of history
