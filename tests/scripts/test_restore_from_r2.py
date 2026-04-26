"""Tests for scripts/restore_from_r2.py."""

from __future__ import annotations

import gzip
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts import restore_from_r2 as restore
from shared.r2_client import R2Client, R2Object, R2Unavailable


def _make_state_db(path: Path, n_rows: int = 3) -> None:
    """Create a small valid state-like SQLite DB at `path`."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE agent_memory (id INTEGER PRIMARY KEY, content TEXT)")
    conn.execute("CREATE TABLE approval_queue (id INTEGER PRIMARY KEY, status TEXT)")
    for i in range(n_rows):
        conn.execute("INSERT INTO agent_memory (content) VALUES (?)", (f"mem{i}",))
        conn.execute("INSERT INTO approval_queue (status) VALUES (?)", ("approved",))
    conn.commit()
    conn.close()


def _make_corrupt_db(path: Path) -> None:
    """Write garbage bytes that look like a SQLite header but corrupt body."""
    path.write_bytes(b"SQLite format 3\x00" + b"\xff" * 1024)


def _gzip_bytes(src: Path) -> bytes:
    with open(src, "rb") as f:
        raw = f.read()
    import io

    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(raw)
    return buf.getvalue()


@pytest.fixture
def fake_r2(monkeypatch, tmp_path):
    """Stub R2Client.from_nakama_backup_env → MagicMock; download_file copies a
    pre-baked .gz from `tmp_path/_r2_objects/<key>` into the requested target."""
    objects_root = tmp_path / "_r2_objects"
    objects_root.mkdir()

    def fake_download_file(*, Bucket, Key, Filename):  # noqa: N803 — boto3 API kwargs
        src = objects_root / Key
        if not src.exists():
            raise RuntimeError(f"fake R2: key not found {Key}")
        Path(Filename).write_bytes(src.read_bytes())

    fake = MagicMock(spec=R2Client)
    fake.bucket = "nakama-backup"
    # `_s3` is a boto3 instance attribute (not on the class), so spec= can't
    # restrict it — re-stub explicitly so .download_file is callable.
    fake._s3 = MagicMock()
    fake._s3.download_file = fake_download_file

    def stub_list_objects(*, prefix, max_keys=100):
        # Produced by tests inserting R2Object pre-records into fake._objects
        return list(getattr(fake, "_objects", []))

    fake.list_objects.side_effect = stub_list_objects

    def stub_head_object(key):
        for obj in getattr(fake, "_objects", []):
            if obj.key == key:
                return obj
        raise R2Unavailable(f"head_object key={key} failed: not found")

    fake.head_object.side_effect = stub_head_object

    with (
        patch("scripts.restore_from_r2.R2Client") as mock_cls,
        patch("scripts.restore_from_r2.load_config", lambda: {}),
    ):
        mock_cls.from_nakama_backup_env.return_value = fake
        yield fake, objects_root


def _seed_snapshot(fake_r2_pair, db: str, date_str: str, db_payload: bytes) -> str:
    """Write a fake R2 object for `<db>/YYYY/MM/DD/<db>.db.gz` and append a
    matching R2Object record to fake._objects so list/head find it."""
    fake, root = fake_r2_pair
    parsed = datetime.strptime(date_str, "%Y-%m-%d")
    key = f"{db}/{parsed:%Y/%m/%d}/{db}.db.gz"
    target = root / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(db_payload)

    obj = R2Object(
        key=key,
        size=len(db_payload),
        last_modified=parsed.replace(tzinfo=timezone.utc),
        etag="x",
    )
    objs = list(getattr(fake, "_objects", []))
    objs.append(obj)
    fake._objects = objs
    return key


# ---- verify_db --------------------------------------------------------------


def test_verify_db_returns_ok_for_valid_db(tmp_path):
    db = tmp_path / "state.db"
    _make_state_db(db, n_rows=5)

    ok, n_tables, n_rows = restore.verify_db(db)

    assert ok is True
    assert n_tables == 2  # agent_memory + approval_queue
    assert n_rows == 10  # 5 + 5


def test_verify_db_handles_zero_byte_file(tmp_path):
    db = tmp_path / "nakama.db"
    db.touch()

    ok, n_tables, n_rows = restore.verify_db(db)

    assert ok is True  # 0-byte sentinel is a legitimate state for nakama.db today
    assert n_tables == 0
    assert n_rows == 0


def test_verify_db_detects_corrupt_db(tmp_path):
    db = tmp_path / "corrupt.db"
    _make_corrupt_db(db)

    # Should either return ok=False (sqlite3 caught it) or raise sqlite3.DatabaseError
    # — both are acceptable signals to the operator.
    try:
        ok, _, _ = restore.verify_db(db)
        assert ok is False
    except sqlite3.DatabaseError:
        pass


# ---- snapshot listing / lookup ---------------------------------------------


def test_list_snapshots_sorts_newest_first(fake_r2, tmp_path):
    fake, _ = fake_r2

    db = tmp_path / "src.db"
    _make_state_db(db)
    payload = _gzip_bytes(db)

    _seed_snapshot(fake_r2, "state", "2026-04-20", payload)
    _seed_snapshot(fake_r2, "state", "2026-04-25", payload)
    _seed_snapshot(fake_r2, "state", "2026-04-22", payload)

    snaps = restore.list_snapshots(fake, "state", limit=10)

    assert [s.key for s in snaps] == [
        "state/2026/04/25/state.db.gz",
        "state/2026/04/22/state.db.gz",
        "state/2026/04/20/state.db.gz",
    ]


def test_find_snapshot_with_explicit_date(fake_r2, tmp_path):
    fake, _ = fake_r2
    db = tmp_path / "src.db"
    _make_state_db(db)
    _seed_snapshot(fake_r2, "state", "2026-04-23", _gzip_bytes(db))

    snap = restore.find_snapshot(fake, "state", "2026-04-23")

    assert snap.key == "state/2026/04/23/state.db.gz"


def test_find_snapshot_raises_on_missing_date(fake_r2):
    fake, _ = fake_r2

    with pytest.raises(SystemExit):
        restore.find_snapshot(fake, "state", "2026-01-01")


def test_find_snapshot_falls_back_to_latest_when_no_date(fake_r2, tmp_path):
    fake, _ = fake_r2
    db = tmp_path / "src.db"
    _make_state_db(db)
    payload = _gzip_bytes(db)
    _seed_snapshot(fake_r2, "state", "2026-04-22", payload)
    _seed_snapshot(fake_r2, "state", "2026-04-25", payload)

    snap = restore.find_snapshot(fake, "state", None)

    assert "2026/04/25" in snap.key


def test_find_snapshot_raises_when_bucket_empty(fake_r2):
    fake, _ = fake_r2

    with pytest.raises(SystemExit):
        restore.find_snapshot(fake, "state", None)


# ---- apply_to_target --------------------------------------------------------


def test_apply_preserves_pre_existing_target(tmp_path):
    target = tmp_path / "data" / "state.db"
    target.parent.mkdir()
    target.write_bytes(b"old content")

    restored = tmp_path / "restored.db"
    restored.write_bytes(b"new content")

    backup = restore.apply_to_target(restored, target, datetime(2026, 4, 25, 14, 30, 0))

    assert backup is not None
    assert backup.exists()
    assert backup.read_bytes() == b"old content"
    assert target.read_bytes() == b"new content"
    assert ".pre-restore.20260425_143000" in backup.name


def test_apply_with_no_pre_existing_target(tmp_path):
    target = tmp_path / "data" / "state.db"  # parent doesn't exist yet
    restored = tmp_path / "restored.db"
    restored.write_bytes(b"new content")

    backup = restore.apply_to_target(restored, target, datetime(2026, 4, 25, 14, 30, 0))

    assert backup is None
    assert target.read_bytes() == b"new content"


# ---- end-to-end CLI through main() -----------------------------------------


def test_cmd_list_prints_snapshots(fake_r2, tmp_path, capsys):
    fake, _ = fake_r2
    db = tmp_path / "src.db"
    _make_state_db(db)
    _seed_snapshot(fake_r2, "state", "2026-04-25", _gzip_bytes(db))

    rc = restore.main(["list", "--db", "state", "--limit", "5"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "state/2026/04/25/state.db.gz" in out


def test_cmd_restore_dry_run_does_not_touch_target(fake_r2, tmp_path, capsys, monkeypatch):
    fake, _ = fake_r2
    db = tmp_path / "src.db"
    _make_state_db(db)
    _seed_snapshot(fake_r2, "state", "2026-04-25", _gzip_bytes(db))

    target_dir = tmp_path / "data"
    target_dir.mkdir()
    target = target_dir / "state.db"
    target.write_bytes(b"untouched")

    rc = restore.main(
        [
            "restore",
            "--db",
            "state",
            "--date",
            "2026-04-25",
            "--target",
            str(target),
        ]
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert target.read_bytes() == b"untouched"  # critical: dry-run leaves target alone


def test_cmd_restore_apply_replaces_target_and_keeps_backup(fake_r2, tmp_path, capsys):
    fake, _ = fake_r2
    db = tmp_path / "src.db"
    _make_state_db(db, n_rows=7)
    _seed_snapshot(fake_r2, "state", "2026-04-25", _gzip_bytes(db))

    target_dir = tmp_path / "data"
    target_dir.mkdir()
    target = target_dir / "state.db"
    target.write_bytes(b"old prod data")

    rc = restore.main(
        [
            "restore",
            "--db",
            "state",
            "--date",
            "2026-04-25",
            "--target",
            str(target),
            "--apply",
        ]
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert "APPLIED" in out

    # target now holds restored DB and is readable
    conn = sqlite3.connect(str(target))
    (n,) = conn.execute("SELECT COUNT(*) FROM agent_memory").fetchone()
    conn.close()
    assert n == 7

    # pre-restore backup preserved
    backups = list(target_dir.glob("state.db.pre-restore.*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"old prod data"


def test_cmd_restore_returns_1_on_corrupt_snapshot(fake_r2, tmp_path, capsys):
    fake, _ = fake_r2
    corrupt = tmp_path / "corrupt.db"
    _make_corrupt_db(corrupt)
    _seed_snapshot(fake_r2, "state", "2026-04-25", _gzip_bytes(corrupt))

    rc = restore.main(["restore", "--db", "state", "--date", "2026-04-25"])

    assert rc == 1


def test_main_returns_1_when_r2_unavailable(monkeypatch, capsys):
    with (
        patch("scripts.restore_from_r2.R2Client") as mock_cls,
        patch("scripts.restore_from_r2.load_config", lambda: {}),
    ):
        mock_cls.from_nakama_backup_env.side_effect = R2Unavailable("missing R2 env")

        rc = restore.main(["list", "--db", "state"])

    assert rc == 1
