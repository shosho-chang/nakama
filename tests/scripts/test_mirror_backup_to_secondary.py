"""Tests for scripts/mirror_backup_to_secondary.py."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from shared.r2_client import R2Object
from shared.secondary_storage import B2Unavailable


@pytest.fixture
def fake_clients(monkeypatch, tmp_path):
    """Stub both R2 (source) and B2 (sink) clients with file-system fakes."""
    objects_root = tmp_path / "_r2_objects"
    objects_root.mkdir()
    b2_root = tmp_path / "_b2_objects"
    b2_root.mkdir()

    def fake_r2_download_file(*, Bucket, Key, Filename):  # noqa: N803
        src = objects_root / Key
        if not src.exists():
            raise RuntimeError(f"fake R2: key not found {Key}")
        Path(Filename).write_bytes(src.read_bytes())

    fake_r2 = MagicMock()
    fake_r2.bucket = "nakama-backup"
    fake_r2._s3.download_file = fake_r2_download_file

    def stub_list_objects(*, prefix, max_keys=100):
        all_objs = list(getattr(fake_r2, "_objects", []))
        return [o for o in all_objs if o.key.startswith(prefix)]

    fake_r2.list_objects.side_effect = stub_list_objects

    fake_b2 = MagicMock()
    fake_b2.bucket = "nakama-backup-mirror"

    def fake_b2_head(key):
        if (b2_root / key).exists():
            from shared.secondary_storage import B2Object

            return B2Object(key=key, size=10, last_modified=datetime.now(timezone.utc), etag="x")
        raise B2Unavailable(f"head_object key={key} failed: 404")

    fake_b2.head_object.side_effect = fake_b2_head

    def fake_b2_upload(local_path, key, *, content_type=None):
        target = b2_root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Path(local_path).read_bytes())

    fake_b2.upload_file.side_effect = fake_b2_upload

    with (
        patch("scripts.mirror_backup_to_secondary.R2Client") as r2_cls,
        patch("scripts.mirror_backup_to_secondary.B2Client") as b2_cls,
        patch("scripts.mirror_backup_to_secondary.load_config", lambda: {}),
    ):
        r2_cls.from_nakama_backup_env.return_value = fake_r2
        b2_cls.from_env.return_value = fake_b2
        yield fake_r2, fake_b2, objects_root, b2_root


def _seed_r2(fake_r2_pair, key: str, payload: bytes, when: datetime) -> None:
    fake_r2, _, root, _ = fake_r2_pair
    target = root / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    objs = list(getattr(fake_r2, "_objects", []))
    objs.append(R2Object(key=key, size=len(payload), last_modified=when, etag="x"))
    fake_r2._objects = objs


def test_main_mirrors_new_keys_to_b2(fake_clients):
    from scripts.mirror_backup_to_secondary import main

    fake_r2, fake_b2, _, b2_root = fake_clients
    _seed_r2(
        fake_clients,
        "state/2026/04/25/state.db.gz",
        b"r2-payload",
        datetime(2026, 4, 25, tzinfo=timezone.utc),
    )

    rc = main()

    assert rc == 0
    # B2 received the upload
    assert (b2_root / "state/2026/04/25/state.db.gz").exists()
    assert (b2_root / "state/2026/04/25/state.db.gz").read_bytes() == b"r2-payload"


def test_main_skips_already_mirrored_keys(fake_clients):
    from scripts.mirror_backup_to_secondary import main

    fake_r2, fake_b2, _, b2_root = fake_clients
    # Seed both sides so head_object hits → skip
    _seed_r2(
        fake_clients,
        "state/2026/04/25/state.db.gz",
        b"already",
        datetime(2026, 4, 25, tzinfo=timezone.utc),
    )
    target = b2_root / "state/2026/04/25/state.db.gz"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"already")

    rc = main()

    assert rc == 0
    fake_b2.upload_file.assert_not_called()


def test_main_returns_1_when_r2_unavailable(monkeypatch):
    from scripts.mirror_backup_to_secondary import main
    from shared.r2_client import R2Unavailable

    with (
        patch("scripts.mirror_backup_to_secondary.R2Client") as r2_cls,
        patch("scripts.mirror_backup_to_secondary.B2Client") as b2_cls,
        patch("scripts.mirror_backup_to_secondary.load_config", lambda: {}),
    ):
        r2_cls.from_nakama_backup_env.side_effect = R2Unavailable("missing")
        b2_cls.from_env.return_value = MagicMock()
        rc = main()

    assert rc == 1


def test_main_returns_0_when_b2_not_configured(monkeypatch):
    """If B2 env not set, mirror exits clean with rc=0 — no failure on a VPS that
    hasn't set up B2 yet. Lets the cron be installed before B2 setup completes."""
    from scripts.mirror_backup_to_secondary import main

    with (
        patch("scripts.mirror_backup_to_secondary.R2Client") as r2_cls,
        patch("scripts.mirror_backup_to_secondary.B2Client") as b2_cls,
        patch("scripts.mirror_backup_to_secondary.load_config", lambda: {}),
    ):
        r2_cls.from_nakama_backup_env.return_value = MagicMock()
        b2_cls.from_env.side_effect = B2Unavailable("missing B2 env")
        rc = main()

    assert rc == 0


def test_main_returns_1_when_b2_upload_fails(fake_clients):
    from scripts.mirror_backup_to_secondary import main

    fake_r2, fake_b2, _, _ = fake_clients
    _seed_r2(
        fake_clients,
        "state/2026/04/25/state.db.gz",
        b"will-fail-on-upload",
        datetime(2026, 4, 25, tzinfo=timezone.utc),
    )
    fake_b2.upload_file.side_effect = B2Unavailable("upload denied")

    rc = main()
    assert rc == 1


def test_main_respects_tier_filter_env(fake_clients, monkeypatch):
    from scripts.mirror_backup_to_secondary import main

    monkeypatch.setenv("NAKAMA_MIRROR_TIERS", "daily")  # weekly + monthly skipped

    _seed_r2(
        fake_clients,
        "state/2026/04/25/state.db.gz",
        b"daily-payload",
        datetime(2026, 4, 25, tzinfo=timezone.utc),
    )
    _seed_r2(
        fake_clients,
        "state-weekly/2026-W17/state.db.gz",
        b"weekly-payload",
        datetime(2026, 4, 26, tzinfo=timezone.utc),
    )

    rc = main()
    assert rc == 0

    fake_r2, fake_b2, _, b2_root = fake_clients
    # daily got mirrored, weekly didn't
    assert (b2_root / "state/2026/04/25/state.db.gz").exists()
    assert not (b2_root / "state-weekly/2026-W17/state.db.gz").exists()
