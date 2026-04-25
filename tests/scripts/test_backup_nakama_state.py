"""Tests for scripts/backup_nakama_state.py."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    """Stub R2Client.from_nakama_backup_env → MagicMock so no boto3 call runs.

    Also stubs load_config() so main() doesn't load the real `.env` from disk
    (which would leak SLACK_USER_ID_SHOSHO etc. into other tests — see
    memory/claude/feedback_windows_abs_path_silent.md-style isolation issues).
    """
    fake = MagicMock()
    fake.delete_older_than.return_value = 0
    with (
        patch("scripts.backup_nakama_state.R2Client") as mock_cls,
        patch("scripts.backup_nakama_state.load_config", lambda: {}),
    ):
        mock_cls.from_nakama_backup_env.return_value = fake
        yield fake


def test_main_uploads_non_empty_dbs(_data_dir_with_state, _fake_r2):
    from scripts.backup_nakama_state import main

    rc = main()
    assert rc == 0

    # state.db got uploaded; nakama.db skipped (0 bytes)
    calls = _fake_r2.upload_file.call_args_list
    assert len(calls) == 1
    local_path, key = calls[0].args[:2]
    assert str(local_path).endswith("state.db.gz")
    # date-partitioned key: state/YYYY/MM/DD/state.db.gz
    assert key.startswith("state/")
    assert key.endswith("/state.db.gz")
    parts = key.split("/")
    assert len(parts) == 5  # state / YYYY / MM / DD / state.db.gz


def test_main_calls_retention_with_default_30_days(_data_dir_with_state, _fake_r2):
    from scripts.backup_nakama_state import main

    assert main() == 0
    # retention called once per DB (state + nakama) with default 30
    retention_calls = _fake_r2.delete_older_than.call_args_list
    assert len(retention_calls) == 2
    for call in retention_calls:
        assert call.args[0] == 30
        assert call.kwargs["prefix"] in ("state/", "nakama/")


def test_main_respects_custom_retention_days(_data_dir_with_state, _fake_r2, monkeypatch):
    from scripts.backup_nakama_state import main

    monkeypatch.setenv("NAKAMA_BACKUP_RETENTION_DAYS", "7")
    assert main() == 0
    for call in _fake_r2.delete_older_than.call_args_list:
        assert call.args[0] == 7


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
    assert main() == 0


def test_main_upload_failure_returns_1(_data_dir_with_state, _fake_r2):
    from scripts.backup_nakama_state import main
    from shared.r2_client import R2Unavailable

    _fake_r2.upload_file.side_effect = R2Unavailable("access denied")
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
    ):
        monkeypatch.delenv(key, raising=False)

    # Stub load_config so main() doesn't reload these from disk `.env`.
    with patch("scripts.backup_nakama_state.load_config", lambda: {}):
        assert main() == 1


def test_main_uses_taipei_date_for_key_across_utc_day_boundary(_data_dir_with_state, _fake_r2):
    """Key must use Asia/Taipei date, not UTC.

    04:00 Asia/Taipei = 20:00 UTC previous day; a UTC-based key would stamp
    the backup with yesterday's folder. Regression guard for the PR #67
    category of timezone bug.
    """
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _Zone

    import scripts.backup_nakama_state as backup_mod

    # Freeze Taipei-aware clock at 04:00 Taipei on 2026-04-24 → 20:00 UTC 2026-04-23.
    taipei_now = _dt(2026, 4, 24, 4, 0, tzinfo=_Zone("Asia/Taipei"))

    class _FakeDateTime:
        @staticmethod
        def now(tz):
            assert tz is not None
            return taipei_now.astimezone(tz)

    with patch.object(backup_mod, "datetime", _FakeDateTime):
        assert backup_mod.main() == 0

    keys = [c.args[1] for c in _fake_r2.upload_file.call_args_list]
    assert keys == ["state/2026/04/24/state.db.gz"], f"expected Taipei-date key but got {keys!r}"


def test_main_records_heartbeat_success_on_happy_path(_data_dir_with_state, _fake_r2):
    from scripts.backup_nakama_state import main
    from shared import heartbeat

    assert main() == 0

    hb = heartbeat.get_heartbeat("nakama-backup")
    assert hb is not None
    assert hb.last_status == "success"
    assert hb.consecutive_failures == 0


def test_main_records_heartbeat_failure_on_r2_unavailable(tmp_path, monkeypatch):
    """When env missing, backup fails fast — heartbeat must still record the failure."""
    from scripts.backup_nakama_state import main
    from shared import heartbeat

    for key in (
        "R2_ACCOUNT_ID",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "NAKAMA_R2_BACKUP_BUCKET",
        "NAKAMA_R2_ACCESS_KEY_ID",
        "NAKAMA_R2_SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    with patch("scripts.backup_nakama_state.load_config", lambda: {}):
        assert main() == 1

    hb = heartbeat.get_heartbeat("nakama-backup")
    assert hb is not None
    assert hb.last_status == "fail"
    assert "r2 client unavailable" in (hb.last_error or "").lower()
    assert hb.consecutive_failures == 1


def test_main_records_heartbeat_failure_on_upload_error(_data_dir_with_state, _fake_r2):
    from scripts.backup_nakama_state import main
    from shared import heartbeat
    from shared.r2_client import R2Unavailable

    _fake_r2.upload_file.side_effect = R2Unavailable("access denied")
    assert main() == 1

    hb = heartbeat.get_heartbeat("nakama-backup")
    assert hb is not None
    assert hb.last_status == "fail"
    assert "backup failed" in (hb.last_error or "").lower()


def test_backed_up_content_matches_source(_data_dir_with_state, tmp_path):
    """Smoke test the .backup + gzip chain: what we upload is what gunzips back to the DB."""
    import gzip

    from scripts.backup_nakama_state import _backup_one

    captured: dict[str, Path] = {}

    class _CapturingClient:
        bucket = "nakama-backup"

        def upload_file(self, local_path, key, *, content_type=None):
            captured["path"] = Path(local_path)
            captured["key"] = key
            # Copy out before tempdir cleanup.
            dst = tmp_path / "captured.gz"
            dst.write_bytes(Path(local_path).read_bytes())
            captured["copy"] = dst

    client = _CapturingClient()
    now = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)
    ok = _backup_one(_data_dir_with_state, "state.db", client, now)
    assert ok
    assert captured["key"] == "state/2026/04/23/state.db.gz"

    # Gunzip the uploaded bytes, open as SQLite, confirm our test row is intact.
    restored = tmp_path / "restored.db"
    with gzip.open(captured["copy"], "rb") as f_in:
        restored.write_bytes(f_in.read())
    conn = sqlite3.connect(str(restored))
    try:
        row = conn.execute("SELECT v FROM smoke").fetchone()
        assert row == ("hello",)
    finally:
        conn.close()
