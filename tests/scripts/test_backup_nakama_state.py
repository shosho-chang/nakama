"""Tests for scripts/backup_nakama_state.py."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest


@pytest.fixture
def _data_dir_with_state(tmp_path: Path, monkeypatch) -> Path:
    """Create a realistic Nakama data/ dir with a non-empty state.db."""
    data = tmp_path / "data"
    data.mkdir()
    state = data / "state.db"
    conn = sqlite3.connect(str(state))
    conn.execute("CREATE TABLE smoke (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO smoke (v) VALUES ('hello')")
    conn.commit()
    conn.close()
    # nakama.db present but empty (matches observed VPS layout)
    (data / "nakama.db").touch()

    monkeypatch.setenv("NAKAMA_DATA_DIR", str(data))
    monkeypatch.setenv("NAKAMA_R2_BACKUP_BUCKET", "nakama-backup")
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct12345678")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setenv("R2_BUCKET_NAME", "xcloud-backup")  # unused by backup, but from_env needs it
    yield data


@pytest.fixture
def _fake_r2(monkeypatch):
    """Stub R2Client.from_nakama_backup_env → MagicMock so no boto3 call runs."""
    fake = MagicMock()
    fake.delete_older_than.return_value = 0
    with (
        patch("scripts.backup_nakama_state.R2Client") as mock_cls,
        patch("scripts.backup_nakama_state.load_config", lambda: {}),
    ):
        mock_cls.from_nakama_backup_env.return_value = fake
        yield fake


def _patch_now(taipei_dt: datetime):
    """Return a context manager that pins backup_mod.datetime.now(tz) to `taipei_dt`."""
    import scripts.backup_nakama_state as backup_mod

    class _FakeDateTime:
        @staticmethod
        def now(tz):
            assert tz is not None
            return taipei_dt.astimezone(tz)

    return patch.object(backup_mod, "datetime", _FakeDateTime)


# ---- happy-path daily-only behavior (Mon-Sat, day != 1) --------------------


def test_main_uploads_non_empty_dbs_on_ordinary_weekday(_data_dir_with_state, _fake_r2):
    from scripts.backup_nakama_state import main

    # Friday 2026-04-24 — neither Sunday nor 1st of month → daily only
    with _patch_now(datetime(2026, 4, 24, 4, 0, tzinfo=ZoneInfo("Asia/Taipei"))):
        assert main() == 0

    # state.db got uploaded (1 daily call); nakama.db skipped (0 bytes)
    calls = _fake_r2.upload_file.call_args_list
    assert len(calls) == 1
    local_path, key = calls[0].args[:2]
    assert str(local_path).endswith("state.db.gz")
    assert key == "state/2026/04/24/state.db.gz"


def test_main_calls_retention_per_tier_per_db(_data_dir_with_state, _fake_r2):
    """Retention runs across all 3 tiers × 2 DBs even on a daily-only day."""
    from scripts.backup_nakama_state import main

    with _patch_now(datetime(2026, 4, 24, 4, 0, tzinfo=ZoneInfo("Asia/Taipei"))):
        assert main() == 0

    retention_calls = _fake_r2.delete_older_than.call_args_list
    # 2 dbs (state, nakama) × 3 tiers (daily, weekly, monthly) = 6
    assert len(retention_calls) == 6

    prefixes = [c.kwargs["prefix"] for c in retention_calls]
    assert sorted(prefixes) == sorted(
        [
            "state/",
            "state-weekly/",
            "state-monthly/",
            "nakama/",
            "nakama-weekly/",
            "nakama-monthly/",
        ]
    )


def test_main_respects_custom_daily_retention(_data_dir_with_state, _fake_r2, monkeypatch):
    from scripts.backup_nakama_state import main

    monkeypatch.setenv("NAKAMA_BACKUP_RETENTION_DAYS", "7")
    with _patch_now(datetime(2026, 4, 24, 4, 0, tzinfo=ZoneInfo("Asia/Taipei"))):
        assert main() == 0

    daily_prune = [
        c
        for c in _fake_r2.delete_older_than.call_args_list
        if c.kwargs["prefix"] in ("state/", "nakama/")
    ]
    assert len(daily_prune) == 2
    for c in daily_prune:
        assert c.args[0] == 7  # custom value


# ---- weekly tier (Sundays) --------------------------------------------------


def test_main_writes_weekly_on_sunday(_data_dir_with_state, _fake_r2):
    from scripts.backup_nakama_state import main

    # Sunday 2026-04-26 (NOT 1st of month) → daily + weekly, no monthly
    with _patch_now(datetime(2026, 4, 26, 4, 0, tzinfo=ZoneInfo("Asia/Taipei"))):
        assert main() == 0

    keys = [c.args[1] for c in _fake_r2.upload_file.call_args_list]
    assert keys == [
        "state/2026/04/26/state.db.gz",
        "state-weekly/2026-W17/state.db.gz",
    ]


def test_weekly_retention_uses_weeks_env(_data_dir_with_state, _fake_r2, monkeypatch):
    from scripts.backup_nakama_state import main

    monkeypatch.setenv("NAKAMA_BACKUP_WEEKLY_RETENTION_WEEKS", "4")
    with _patch_now(datetime(2026, 4, 26, 4, 0, tzinfo=ZoneInfo("Asia/Taipei"))):
        assert main() == 0

    weekly_prune = [
        c
        for c in _fake_r2.delete_older_than.call_args_list
        if c.kwargs["prefix"].endswith("-weekly/")
    ]
    assert len(weekly_prune) == 2
    for c in weekly_prune:
        assert c.args[0] == 4 * 7  # 4 weeks → 28 days


# ---- monthly tier (1st of month) -------------------------------------------


def test_main_writes_monthly_on_first(_data_dir_with_state, _fake_r2):
    from scripts.backup_nakama_state import main

    # 2026-05-01 is Friday → daily + monthly, no weekly
    with _patch_now(datetime(2026, 5, 1, 4, 0, tzinfo=ZoneInfo("Asia/Taipei"))):
        assert main() == 0

    keys = [c.args[1] for c in _fake_r2.upload_file.call_args_list]
    assert keys == [
        "state/2026/05/01/state.db.gz",
        "state-monthly/2026-05/state.db.gz",
    ]


def test_monthly_retention_uses_months_env(_data_dir_with_state, _fake_r2, monkeypatch):
    from scripts.backup_nakama_state import main

    monkeypatch.setenv("NAKAMA_BACKUP_MONTHLY_RETENTION_MONTHS", "3")
    with _patch_now(datetime(2026, 5, 1, 4, 0, tzinfo=ZoneInfo("Asia/Taipei"))):
        assert main() == 0

    monthly_prune = [
        c
        for c in _fake_r2.delete_older_than.call_args_list
        if c.kwargs["prefix"].endswith("-monthly/")
    ]
    for c in monthly_prune:
        assert c.args[0] == 3 * 31  # 3 months × 31 days cushion


# ---- triple-tier write (Sunday + 1st) --------------------------------------


def test_main_writes_all_three_when_first_is_sunday(_data_dir_with_state, _fake_r2):
    """E.g. 2026-11-01 is a Sunday → daily + weekly + monthly all fire."""
    from scripts.backup_nakama_state import main

    with _patch_now(datetime(2026, 11, 1, 4, 0, tzinfo=ZoneInfo("Asia/Taipei"))):
        assert main() == 0

    keys = [c.args[1] for c in _fake_r2.upload_file.call_args_list]
    assert keys == [
        "state/2026/11/01/state.db.gz",
        "state-weekly/2026-W44/state.db.gz",
        "state-monthly/2026-11/state.db.gz",
    ]


# ---- failure paths ---------------------------------------------------------


def test_main_skips_missing_db(tmp_path, monkeypatch, _fake_r2):
    from scripts.backup_nakama_state import main

    data = tmp_path / "data"
    data.mkdir()
    # no state.db, no nakama.db — both missing
    monkeypatch.setenv("NAKAMA_DATA_DIR", str(data))
    monkeypatch.setenv("NAKAMA_R2_BACKUP_BUCKET", "nakama-backup")
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setenv("R2_BUCKET_NAME", "xcloud-backup")

    with _patch_now(datetime(2026, 4, 24, 4, 0, tzinfo=ZoneInfo("Asia/Taipei"))):
        assert main() == 0
    _fake_r2.upload_file.assert_not_called()


def test_main_returns_1_when_data_dir_missing(tmp_path, monkeypatch, _fake_r2):
    from scripts.backup_nakama_state import main

    monkeypatch.setenv("NAKAMA_DATA_DIR", str(tmp_path / "does-not-exist"))
    monkeypatch.setenv("NAKAMA_R2_BACKUP_BUCKET", "nakama-backup")
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setenv("R2_BUCKET_NAME", "xcloud-backup")

    assert main() == 1
    _fake_r2.upload_file.assert_not_called()


def test_main_retention_failure_does_not_fail_run(_data_dir_with_state, _fake_r2):
    from scripts.backup_nakama_state import main
    from shared.r2_client import R2Unavailable

    _fake_r2.delete_older_than.side_effect = R2Unavailable("prune failed")
    # Upload succeeded; retention warning is non-fatal.
    with _patch_now(datetime(2026, 4, 24, 4, 0, tzinfo=ZoneInfo("Asia/Taipei"))):
        assert main() == 0


def test_main_upload_failure_returns_1(_data_dir_with_state, _fake_r2):
    from scripts.backup_nakama_state import main
    from shared.r2_client import R2Unavailable

    _fake_r2.upload_file.side_effect = R2Unavailable("access denied")
    with _patch_now(datetime(2026, 4, 24, 4, 0, tzinfo=ZoneInfo("Asia/Taipei"))):
        assert main() == 1


def test_main_returns_1_when_r2_env_missing(tmp_path, monkeypatch):
    """from_nakama_backup_env raises → main logs + exits 1 (does not crash)."""
    from scripts.backup_nakama_state import main

    # Clear every R2 env; main should catch and return 1, not raise.
    for key in (
        "R2_ACCOUNT_ID",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "NAKAMA_R2_BACKUP_BUCKET",
        "NAKAMA_R2_ACCESS_KEY_ID",
        "NAKAMA_R2_SECRET_ACCESS_KEY",
        "NAKAMA_R2_WRITE_ACCESS_KEY_ID",
        "NAKAMA_R2_WRITE_SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    # Stub load_config so main() doesn't reload these from disk `.env`.
    with patch("scripts.backup_nakama_state.load_config", lambda: {}):
        assert main() == 1


# ---- timezone-correctness regression --------------------------------------


def test_main_uses_taipei_date_for_key_across_utc_day_boundary(_data_dir_with_state, _fake_r2):
    """Key must use Asia/Taipei date, not UTC.

    04:00 Asia/Taipei = 20:00 UTC previous day; a UTC-based key would stamp
    the backup with yesterday's folder. Regression guard for the PR #67
    category of timezone bug.
    """
    # 2026-04-24 04:00 Taipei → 2026-04-23 20:00 UTC. A UTC-based key would
    # produce state/2026/04/23/...; our Taipei-aware code must produce 04/24.
    with _patch_now(datetime(2026, 4, 24, 4, 0, tzinfo=ZoneInfo("Asia/Taipei"))):
        assert main_call() == 0

    keys = [c.args[1] for c in _fake_r2.upload_file.call_args_list]
    # Friday → daily only; key uses Taipei date 04/24
    assert keys == ["state/2026/04/24/state.db.gz"], f"expected Taipei-date key but got {keys!r}"


def main_call() -> int:
    """Indirection lets the timezone-regression test patch the module-level datetime
    using `_patch_now` without colliding with `from scripts.backup_nakama_state import main`
    being bound to the original module reference at import time."""
    import scripts.backup_nakama_state as backup_mod

    return backup_mod.main()


# ---- gzip round-trip integrity --------------------------------------------


def test_backed_up_content_matches_source(_data_dir_with_state, tmp_path):
    """Smoke test the .backup + gzip chain: what we upload is what gunzips back to the DB."""
    import gzip

    from scripts.backup_nakama_state import _backup_one

    captured: dict[str, Path] = {}

    class _CapturingClient:
        bucket = "nakama-backup"

        def upload_file(self, local_path, key, *, content_type=None):
            captured.setdefault("paths", []).append(Path(local_path))
            captured.setdefault("keys", []).append(key)
            # Copy out before tempdir cleanup.
            dst = tmp_path / f"captured.{len(captured['keys'])}.gz"
            dst.write_bytes(Path(local_path).read_bytes())
            captured.setdefault("copies", []).append(dst)

    client = _CapturingClient()
    # Use ZoneInfo so weekday/day calc matches production (Mon=0..Sun=6 from
    # tz-aware datetime — same as what backup_one sees in main()).
    now = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)
    ok = _backup_one(_data_dir_with_state, "state.db", client, now)
    assert ok
    # 2026-04-23 (Thursday in UTC) — daily only
    assert captured["keys"] == ["state/2026/04/23/state.db.gz"]

    # Gunzip the uploaded bytes, open as SQLite, confirm our test row is intact.
    restored = tmp_path / "restored.db"
    with gzip.open(captured["copies"][0], "rb") as f_in:
        restored.write_bytes(f_in.read())
    conn = sqlite3.connect(str(restored))
    try:
        row = conn.execute("SELECT v FROM smoke").fetchone()
        assert row == ("hello",)
    finally:
        conn.close()
