"""Tests for scripts/verify_backup_integrity.py."""

from __future__ import annotations

import gzip
import io
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from shared.r2_client import R2Object


def _make_state_db(path: Path, n_rows: int = 3) -> bytes:
    """Build a small valid state.db, return gzipped bytes."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE smoke (id INTEGER PRIMARY KEY, v TEXT)")
    for i in range(n_rows):
        conn.execute("INSERT INTO smoke (v) VALUES (?)", (f"row{i}",))
    conn.commit()
    conn.close()

    with open(path, "rb") as f:
        raw = f.read()
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(raw)
    return buf.getvalue()


@pytest.fixture
def fake_r2(monkeypatch, tmp_path):
    """Stub R2Client.from_nakama_backup_env so no real boto3 call runs."""
    objects_root = tmp_path / "_r2_objects"
    objects_root.mkdir()

    def fake_download_file(*, Bucket, Key, Filename):  # noqa: N803
        src = objects_root / Key
        if not src.exists():
            raise RuntimeError(f"fake R2: key not found {Key}")
        Path(Filename).write_bytes(src.read_bytes())

    fake = MagicMock()
    fake.bucket = "nakama-backup"
    # Use side_effect (not direct assignment) so MagicMock keeps tracking call_count
    fake._s3.download_file.side_effect = fake_download_file

    def stub_list_objects(*, prefix, max_keys=100):
        all_objs = list(getattr(fake, "_objects", []))
        return [o for o in all_objs if o.key.startswith(prefix)]

    fake.list_objects.side_effect = stub_list_objects

    with (
        patch("scripts.verify_backup_integrity.R2Client") as mock_cls,
        patch("scripts.verify_backup_integrity.load_config", lambda: {}),
    ):
        mock_cls.from_nakama_backup_env.return_value = fake
        yield fake, objects_root


def _seed(fake_pair, key: str, gz_bytes: bytes, when: datetime) -> None:
    fake, root = fake_pair
    target = root / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(gz_bytes)
    objs = list(getattr(fake, "_objects", []))
    objs.append(R2Object(key=key, size=len(gz_bytes), last_modified=when, etag="x"))
    fake._objects = objs


def test_main_returns_0_when_all_snapshots_pass(fake_r2, tmp_path, monkeypatch):
    from scripts.verify_backup_integrity import main

    db = tmp_path / "src.db"
    payload = _make_state_db(db)

    _seed(
        fake_r2, "state/2026/04/25/state.db.gz", payload, datetime(2026, 4, 25, tzinfo=timezone.utc)
    )
    _seed(
        fake_r2, "state/2026/04/24/state.db.gz", payload, datetime(2026, 4, 24, tzinfo=timezone.utc)
    )

    # Limit samples so test runs quickly
    monkeypatch.setenv("NAKAMA_INTEGRITY_DAILY_SAMPLES", "2")
    monkeypatch.setenv("NAKAMA_INTEGRITY_WEEKLY_SAMPLES", "0")
    monkeypatch.setenv("NAKAMA_INTEGRITY_MONTHLY_SAMPLES", "0")

    rc = main()
    assert rc == 0


def test_main_returns_1_on_corrupt_snapshot(fake_r2, tmp_path, monkeypatch):
    from scripts.verify_backup_integrity import main

    db = tmp_path / "src.db"
    good_payload = _make_state_db(db)

    # One good snapshot
    _seed(
        fake_r2,
        "state/2026/04/25/state.db.gz",
        good_payload,
        datetime(2026, 4, 25, tzinfo=timezone.utc),
    )

    # One corrupt snapshot — gzip wrapping garbage that gunzips fine but has bad sqlite header
    corrupt_inner = b"SQLite format 3\x00" + b"\xff" * 1024
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(corrupt_inner)
    _seed(
        fake_r2,
        "state/2026/04/24/state.db.gz",
        buf.getvalue(),
        datetime(2026, 4, 24, tzinfo=timezone.utc),
    )

    monkeypatch.setenv("NAKAMA_INTEGRITY_DAILY_SAMPLES", "2")
    monkeypatch.setenv("NAKAMA_INTEGRITY_WEEKLY_SAMPLES", "0")
    monkeypatch.setenv("NAKAMA_INTEGRITY_MONTHLY_SAMPLES", "0")

    rc = main()
    assert rc == 1


def test_main_returns_1_when_r2_unavailable(monkeypatch):
    from scripts.verify_backup_integrity import main
    from shared.r2_client import R2Unavailable

    with (
        patch("scripts.verify_backup_integrity.R2Client") as mock_cls,
        patch("scripts.verify_backup_integrity.load_config", lambda: {}),
    ):
        mock_cls.from_nakama_backup_env.side_effect = R2Unavailable("missing R2 env")
        rc = main()

    assert rc == 1


def test_main_handles_empty_bucket_gracefully(fake_r2, monkeypatch):
    from scripts.verify_backup_integrity import main

    # Don't seed anything → list_objects returns []
    monkeypatch.setenv("NAKAMA_INTEGRITY_DAILY_SAMPLES", "5")
    monkeypatch.setenv("NAKAMA_INTEGRITY_WEEKLY_SAMPLES", "0")
    monkeypatch.setenv("NAKAMA_INTEGRITY_MONTHLY_SAMPLES", "0")

    rc = main()
    # No snapshots = nothing to verify = vacuously OK
    assert rc == 0


def test_samples_env_override_respected(fake_r2, tmp_path, monkeypatch):
    """Confirm that the samples env vars actually limit how many snapshots get verified."""
    from scripts.verify_backup_integrity import main

    db = tmp_path / "src.db"
    payload = _make_state_db(db)

    # Seed 5 daily snapshots
    for day in range(20, 25):
        _seed(
            fake_r2,
            f"state/2026/04/{day:02d}/state.db.gz",
            payload,
            datetime(2026, 4, day, tzinfo=timezone.utc),
        )

    # Only verify 2 latest
    monkeypatch.setenv("NAKAMA_INTEGRITY_DAILY_SAMPLES", "2")
    monkeypatch.setenv("NAKAMA_INTEGRITY_WEEKLY_SAMPLES", "0")
    monkeypatch.setenv("NAKAMA_INTEGRITY_MONTHLY_SAMPLES", "0")

    fake, _ = fake_r2
    rc = main()
    assert rc == 0
    # download_file (the work that scales with samples) should only happen
    # for state DB × 2 daily samples + nakama DB × 2 (but nakama prefix has 0 objects,
    # so nakama branch is no-op). Net: 2 downloads.
    assert fake._s3.download_file.call_count == 2  # noqa: SLF001


# ── heartbeat + alert wiring (sweep PR C) ───────────────────────────────────
# PR #154 shipped without record_failure / alert("error", ...) — corruption
# detected by the cron only logged, never DM'd. These tests pin the wiring.


def test_main_records_success_when_all_pass(fake_r2, tmp_path, monkeypatch):
    from scripts import verify_backup_integrity as mod

    db = tmp_path / "src.db"
    payload = _make_state_db(db)
    _seed(
        fake_r2,
        "state/2026/04/25/state.db.gz",
        payload,
        datetime(2026, 4, 25, tzinfo=timezone.utc),
    )

    with (
        patch.object(mod, "record_success") as rec_ok,
        patch.object(mod, "record_failure") as rec_fail,
        patch.object(mod, "alert") as alert_fn,
    ):
        rc = mod.main()

    assert rc == 0
    rec_ok.assert_called_once_with("nakama-backup-integrity")
    rec_fail.assert_not_called()
    alert_fn.assert_not_called()


def test_main_alerts_and_records_failure_on_corrupt_snapshot(fake_r2, tmp_path):
    from scripts import verify_backup_integrity as mod

    # Seed a corrupt snapshot — verify_db returns (False, ...) → counted as failure
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"SQLite format 3\x00" + b"\xff" * 1024)
    with open(bad, "rb") as f:
        raw = f.read()
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(raw)
    _seed(
        fake_r2,
        "state/2026/04/25/state.db.gz",
        buf.getvalue(),
        datetime(2026, 4, 25, tzinfo=timezone.utc),
    )

    with (
        patch.object(mod, "record_success") as rec_ok,
        patch.object(mod, "record_failure") as rec_fail,
        patch.object(mod, "alert") as alert_fn,
    ):
        rc = mod.main()

    assert rc == 1
    rec_ok.assert_not_called()
    rec_fail.assert_called_once()
    assert rec_fail.call_args[0][0] == "nakama-backup-integrity"
    alert_fn.assert_called_once()
    args, kwargs = alert_fn.call_args
    assert args[0] == "error"
    assert args[1] == "backup"
    assert "integrity:" in args[2]
    assert kwargs.get("dedupe_key") == "backup-integrity-fail"


def test_main_records_failure_when_r2_unavailable():
    from scripts import verify_backup_integrity as mod
    from shared.r2_client import R2Unavailable

    with (
        patch("scripts.verify_backup_integrity.R2Client") as r2_cls,
        patch("scripts.verify_backup_integrity.load_config", lambda: {}),
        patch.object(mod, "record_failure") as rec_fail,
        patch.object(mod, "alert") as alert_fn,
    ):
        r2_cls.from_nakama_backup_env.side_effect = R2Unavailable("missing")
        rc = mod.main()

    assert rc == 1
    rec_fail.assert_called_once()
    alert_fn.assert_called_once()
    assert alert_fn.call_args.kwargs["dedupe_key"] == "backup-integrity-r2-unavailable"
