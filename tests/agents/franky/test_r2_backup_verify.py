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


# ---------------------------------------------------------------------------
# Per-prefix isolation (migration 008)
# ---------------------------------------------------------------------------
#
# Motivation: xcloud-backup bucket holds shosho/ (daily fresh) + fleet/ (newer,
# may go stale silently). A bucket-wide verify_once masks fleet staleness via
# `max(objects, key=last_modified)`. Each prefix must record + escalate
# independently.


def _seed_fail_history_for_prefix(prefix: str, days: int) -> None:
    """Insert N day-distinct failure rows under a specific prefix."""
    from shared.state import _get_conn

    conn = _get_conn()
    base = _now() - timedelta(days=1)
    for i in range(days):
        day = (base - timedelta(days=i)).isoformat()
        conn.execute(
            """INSERT INTO r2_backup_checks
                  (checked_at, latest_object_key, latest_object_size,
                   latest_object_mtime, status, detail, prefix)
               VALUES (?, NULL, NULL, NULL, 'missing', 'seeded test failure', ?)""",
            (day, prefix),
        )
    conn.commit()


def test_verify_writes_prefix_column(_all_env):
    """verify_once(prefix='shosho/') 必須把 prefix 寫進 r2_backup_checks。"""
    from shared.state import _get_conn

    client = _mock_r2_client([_fresh_object(key="shosho/db.tar.zst")])
    with patch.object(r2_backup_verify, "R2Client") as mock_cls:
        mock_cls.from_env.return_value = client
        verify_once(prefix="shosho/")

    conn = _get_conn()
    row = conn.execute("SELECT prefix FROM r2_backup_checks ORDER BY id DESC LIMIT 1").fetchone()
    assert row["prefix"] == "shosho/"


def test_consecutive_fail_count_does_not_cross_prefixes(_all_env):
    """fleet/ 連 3 天 fail 但 shosho/ 連 3 天 ok → fleet 才會升 critical，shosho 不受影響。"""
    _seed_fail_history_for_prefix("fleet/", days=3)
    # shosho/ 同期 3 天 ok（一個 ok row 就足以打斷 streak）
    from shared.state import _get_conn

    conn = _get_conn()
    conn.execute(
        """INSERT INTO r2_backup_checks
              (checked_at, latest_object_key, latest_object_size,
               latest_object_mtime, status, detail, prefix)
           VALUES (?, 'shosho/x', 100, ?, 'ok', 'seeded ok', 'shosho/')""",
        ((_now() - timedelta(days=1)).isoformat(), (_now() - timedelta(days=1)).isoformat()),
    )
    conn.commit()

    # 今天 fleet/ 又掛 → 應該升 critical；shosho/ 同時 ok → 完全不受影響
    fleet_client = _mock_r2_client([])
    with patch.object(r2_backup_verify, "R2Client") as mock_cls:
        mock_cls.from_env.return_value = fleet_client
        fleet_result = verify_once(prefix="fleet/")
    assert fleet_result["status"] == "missing"
    assert fleet_result["alert"] is not None  # consecutive_fail >= 2
    assert fleet_result["alert"].context["prefix"] == "fleet/"

    # 同時 shosho/ 今天 ok — 不受 fleet 失敗影響
    shosho_client = _mock_r2_client([_fresh_object(key="shosho/x")])
    with patch.object(r2_backup_verify, "R2Client") as mock_cls:
        mock_cls.from_env.return_value = shosho_client
        shosho_result = verify_once(prefix="shosho/")
    assert shosho_result["status"] == "ok"
    assert shosho_result["alert"] is None


def test_alert_dedup_key_includes_prefix(_all_env):
    """兩 prefix 同日掛 → dedup_key 必須有差，避免 alert_router 把它們 collapse 成同一 alert。"""
    _seed_fail_history_for_prefix("fleet/", days=2)
    _seed_fail_history_for_prefix("shosho/", days=2)

    fleet_client = _mock_r2_client([])
    with patch.object(r2_backup_verify, "R2Client") as mock_cls:
        mock_cls.from_env.return_value = fleet_client
        fleet_result = verify_once(prefix="fleet/")

    shosho_client = _mock_r2_client([])
    with patch.object(r2_backup_verify, "R2Client") as mock_cls:
        mock_cls.from_env.return_value = shosho_client
        shosho_result = verify_once(prefix="shosho/")

    assert fleet_result["alert"] is not None
    assert shosho_result["alert"] is not None
    assert fleet_result["alert"].dedup_key != shosho_result["alert"].dedup_key
    assert "fleet/" in fleet_result["alert"].dedup_key
    assert "shosho/" in shosho_result["alert"].dedup_key


def test_resolve_prefixes_csv_env(monkeypatch):
    monkeypatch.setenv("FRANKY_R2_PREFIXES", "shosho/,fleet/")
    monkeypatch.delenv("FRANKY_R2_PREFIX", raising=False)
    assert r2_backup_verify._resolve_prefixes() == ["shosho/", "fleet/"]


def test_resolve_prefixes_csv_strips_empty_entries(monkeypatch):
    monkeypatch.setenv("FRANKY_R2_PREFIXES", "shosho/, ,fleet/, ")
    monkeypatch.delenv("FRANKY_R2_PREFIX", raising=False)
    assert r2_backup_verify._resolve_prefixes() == ["shosho/", "fleet/"]


def test_resolve_prefixes_legacy_singular_env(monkeypatch):
    """FRANKY_R2_PREFIXES 未設 → 退回 FRANKY_R2_PREFIX 單元素 list（向後兼容）。"""
    monkeypatch.delenv("FRANKY_R2_PREFIXES", raising=False)
    monkeypatch.setenv("FRANKY_R2_PREFIX", "legacy/")
    assert r2_backup_verify._resolve_prefixes() == ["legacy/"]


def test_resolve_prefixes_unset_returns_empty_string(monkeypatch):
    """兩 env 都沒設 → [''] 一輪整 bucket verify。"""
    monkeypatch.delenv("FRANKY_R2_PREFIXES", raising=False)
    monkeypatch.delenv("FRANKY_R2_PREFIX", raising=False)
    assert r2_backup_verify._resolve_prefixes() == [""]


def test_verify_all_prefixes_runs_each(_all_env, monkeypatch):
    """verify_all_prefixes 對每個 env CSV 條目跑一次 verify_once。"""
    monkeypatch.setenv("FRANKY_R2_PREFIXES", "shosho/,fleet/")
    client = _mock_r2_client([_fresh_object(key="any")])
    with patch.object(r2_backup_verify, "R2Client") as mock_cls:
        mock_cls.from_env.return_value = client
        results = r2_backup_verify.verify_all_prefixes()

    assert len(results) == 2
    assert {r["prefix"] for r in results} == {"shosho/", "fleet/"}
    # All should share the same operation_id for cron-pass log correlation
    op_ids = {r["operation_id"] for r in results}
    assert len(op_ids) == 1


def test_verify_all_prefixes_independent_alerts(_all_env, monkeypatch):
    """fleet stale + shosho ok → 只 fleet 有 alert，shosho alert=None。"""
    monkeypatch.setenv("FRANKY_R2_PREFIXES", "shosho/,fleet/")
    _seed_fail_history_for_prefix("fleet/", days=1)  # 昨天已掛

    def fake_list(prefix, max_keys):
        if prefix == "fleet/":
            return []  # 今天還是空
        return [_fresh_object(key=f"{prefix}db.tar.zst")]

    client = MagicMock()
    client.list_objects.side_effect = fake_list
    client.bucket = "backups"
    with patch.object(r2_backup_verify, "R2Client") as mock_cls:
        mock_cls.from_env.return_value = client
        results = r2_backup_verify.verify_all_prefixes()

    by_prefix = {r["prefix"]: r for r in results}
    assert by_prefix["shosho/"]["status"] == "ok"
    assert by_prefix["shosho/"]["alert"] is None
    assert by_prefix["fleet/"]["status"] == "missing"
    assert by_prefix["fleet/"]["alert"] is not None
    assert by_prefix["fleet/"]["alert"].context["consecutive_fail_days"] >= 2
