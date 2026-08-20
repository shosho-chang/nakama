from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agents.usopp.media_staging import (
    MediaStager,
    MediaStagingConfig,
    MediaStagingError,
)


class FakeS3:
    def __init__(self, *, fail_on_upload: int | None = None) -> None:
        self.uploads = []
        self.presigns = []
        self.deletes = []
        self.fail_on_upload = fail_on_upload

    def upload_file(self, filename, bucket, key, ExtraArgs=None):
        if self.fail_on_upload == len(self.uploads) + 1:
            raise RuntimeError("upload failed")
        self.uploads.append((filename, bucket, key, ExtraArgs))

    def generate_presigned_url(self, client_method, Params, ExpiresIn):
        self.presigns.append((client_method, Params, ExpiresIn))
        return f"https://signed.example/{Params['Key']}?expires={ExpiresIn}"

    def delete_object(self, *, Bucket, Key):
        self.deletes.append((Bucket, Key))


def config(*, bucket: str = "nakama-meta-staging") -> MediaStagingConfig:
    return MediaStagingConfig(
        account_id="account",
        access_key_id="access",
        secret_access_key="secret",
        bucket=bucket,
        presigned_ttl_seconds=600,
    )


def test_from_env_uses_only_meta_namespace_and_rejects_backup_bucket(monkeypatch):
    monkeypatch.setenv("META_MEDIA_R2_ACCOUNT_ID", "meta-account")
    monkeypatch.setenv("META_MEDIA_R2_ACCESS_KEY_ID", "meta-access")
    monkeypatch.setenv("META_MEDIA_R2_SECRET_ACCESS_KEY", "meta-secret")
    monkeypatch.setenv("META_MEDIA_R2_BUCKET", "nakama-backup")
    monkeypatch.setenv("R2_ACCOUNT_ID", "unrelated-backup-account")
    monkeypatch.setenv("NAKAMA_R2_BACKUP_BUCKET", "nakama-backup")
    with pytest.raises(MediaStagingError, match="must not reuse a backup bucket"):
        MediaStagingConfig.from_env()

    monkeypatch.setenv("META_MEDIA_R2_BUCKET", "meta-staging")
    loaded = MediaStagingConfig.from_env()
    assert loaded.account_id == "meta-account"
    assert loaded.bucket == "meta-staging"


def test_stage_file_uses_opaque_key_presigned_ttl_and_receipt(tmp_path: Path):
    original = tmp_path / "patient-jane-private-name.png"
    original.write_bytes(b"image bytes")
    fake = FakeS3()
    stager = MediaStager(
        config(),
        fake,
        now=lambda: datetime(2026, 8, 19, tzinfo=UTC),
        token_factory=lambda: "opaque-token-12345678901234567890",
    )
    receipt = stager.stage_file(original)

    assert receipt.key == "meta-stage/opaque-token-12345678901234567890"
    assert original.name not in receipt.key
    assert original.name not in receipt.url
    assert receipt.expires_at == "2026-08-19T00:10:00+00:00"
    assert receipt.bytes == len(b"image bytes")
    assert fake.uploads[0][1] == "nakama-meta-staging"
    assert fake.uploads[0][3] == {"ContentType": "image/png"}
    assert fake.presigns[0][2] == 600


def test_stage_files_cleans_already_uploaded_keys_when_later_upload_fails(tmp_path: Path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    fake = FakeS3(fail_on_upload=2)
    tokens = iter(["a" * 24, "b" * 24])
    stager = MediaStager(config(), fake, token_factory=lambda: next(tokens))

    with pytest.raises(RuntimeError, match="upload failed"):
        stager.stage_files([first, second])
    assert fake.deletes == [("nakama-meta-staging", f"meta-stage/{'a' * 24}")]


def test_cleanup_deduplicates_keys_and_rejects_non_staging_key():
    fake = FakeS3()
    stager = MediaStager(config(), fake)
    key = "meta-stage/" + "x" * 24
    assert stager.cleanup([key, key]) == (key,)
    assert fake.deletes == [("nakama-meta-staging", key)]

    with pytest.raises(MediaStagingError, match="failed to clean"):
        stager.cleanup(["backups/do-not-delete"])
    assert fake.deletes == [("nakama-meta-staging", key)]


def test_missing_boto3_dependency_names_component_and_install_action(monkeypatch):
    monkeypatch.setitem(sys.modules, "boto3", None)

    with pytest.raises(MediaStagingError) as exc:
        config().build_client()

    message = str(exc.value)
    assert "R2 media staging" in message
    assert "boto3" in message
    assert "pip install -r requirements.txt" in message
    assert config().secret_access_key not in message
