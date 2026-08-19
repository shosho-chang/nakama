"""Short-lived Cloudflare R2 staging for Meta pull-based media ingestion."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol


class MediaStagingError(RuntimeError):
    """Media staging configuration or operation failed."""


class S3StagingClient(Protocol):
    def upload_file(
        self,
        filename: str,
        bucket: str,
        key: str,
        ExtraArgs: dict[str, Any] | None = None,
    ) -> Any: ...

    def generate_presigned_url(
        self,
        client_method: str,
        Params: dict[str, Any],
        ExpiresIn: int,
    ) -> str: ...

    def delete_object(self, *, Bucket: str, Key: str) -> Any: ...


@dataclass(frozen=True)
class MediaStagingConfig:
    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket: str
    presigned_ttl_seconds: int = 900
    public_base_url: str | None = None

    @classmethod
    def from_env(cls) -> MediaStagingConfig:
        names = {
            "account_id": "META_MEDIA_R2_ACCOUNT_ID",
            "access_key_id": "META_MEDIA_R2_ACCESS_KEY_ID",
            "secret_access_key": "META_MEDIA_R2_SECRET_ACCESS_KEY",
            "bucket": "META_MEDIA_R2_BUCKET",
        }
        values = {field: os.getenv(env_name, "").strip() for field, env_name in names.items()}
        missing = [env_name for field, env_name in names.items() if not values[field]]
        if missing:
            raise MediaStagingError(
                "missing required media staging settings: " + ", ".join(sorted(missing))
            )
        ttl_raw = os.getenv("META_MEDIA_R2_PRESIGNED_TTL_SECONDS", "900").strip()
        try:
            ttl = int(ttl_raw)
        except ValueError as exc:
            raise MediaStagingError(
                "META_MEDIA_R2_PRESIGNED_TTL_SECONDS must be an integer"
            ) from exc
        if not 60 <= ttl <= 3600:
            raise MediaStagingError(
                "META_MEDIA_R2_PRESIGNED_TTL_SECONDS must be between 60 and 3600"
            )

        bucket = values["bucket"]
        backup_bucket = os.getenv("NAKAMA_R2_BACKUP_BUCKET", "").strip()
        if "backup" in bucket.casefold() or (backup_bucket and bucket == backup_bucket):
            raise MediaStagingError("META_MEDIA_R2_BUCKET must not reuse a backup bucket")
        return cls(
            **values,
            presigned_ttl_seconds=ttl,
            public_base_url=os.getenv("META_MEDIA_PUBLIC_BASE_URL", "").strip() or None,
        )

    def build_client(self) -> S3StagingClient:
        """Build an S3-compatible client only after configuration is validated."""
        import boto3

        return boto3.client(
            "s3",
            endpoint_url=f"https://{self.account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            region_name="auto",
        )


@dataclass(frozen=True)
class StagedMedia:
    key: str
    url: str
    expires_at: str
    sha256: str
    bytes: int
    content_type: str


class MediaStager:
    """Upload media under opaque keys and return short-lived signed GET URLs."""

    def __init__(
        self,
        config: MediaStagingConfig,
        client: S3StagingClient | None = None,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
    ) -> None:
        self.config = config
        self.client = client or config.build_client()
        self.now = now
        self.token_factory = token_factory

    def stage_file(self, file_path: Path) -> StagedMedia:
        path = Path(file_path)
        if not path.is_file():
            raise MediaStagingError(f"staging source does not exist: {path}")
        token = str(self.token_factory()).strip().replace("/", "_")
        if len(token) < 20:
            raise MediaStagingError("staging token must contain at least 20 characters")
        key = f"meta-stage/{token}"
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        self.client.upload_file(
            str(path),
            self.config.bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )
        try:
            url = self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.config.bucket, "Key": key},
                ExpiresIn=self.config.presigned_ttl_seconds,
            )
        except Exception:
            self.client.delete_object(Bucket=self.config.bucket, Key=key)
            raise
        expires_at = self.now() + timedelta(seconds=self.config.presigned_ttl_seconds)
        return StagedMedia(
            key=key,
            url=url,
            expires_at=expires_at.isoformat(),
            sha256=digest.hexdigest(),
            bytes=path.stat().st_size,
            content_type=content_type,
        )

    def stage_files(self, file_paths: Iterable[Path]) -> list[StagedMedia]:
        staged: list[StagedMedia] = []
        try:
            for file_path in file_paths:
                staged.append(self.stage_file(file_path))
        except Exception:
            self.cleanup(item.key for item in staged)
            raise
        return staged

    def cleanup(self, keys: Iterable[str]) -> tuple[str, ...]:
        deleted: list[str] = []
        failures: list[str] = []
        for key in dict.fromkeys(keys):
            if not key.startswith("meta-stage/") or ".." in key:
                failures.append(key)
                continue
            try:
                self.client.delete_object(Bucket=self.config.bucket, Key=key)
                deleted.append(key)
            except Exception:
                failures.append(key)
        if failures:
            raise MediaStagingError("failed to clean staging keys: " + ", ".join(failures))
        return tuple(deleted)
