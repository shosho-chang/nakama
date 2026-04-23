"""Cloudflare R2 client — list/head for verify + put/delete for Nakama self-backup.

Wraps boto3 S3 with R2-specific endpoint construction. Two usage modes:

1. Franky backup-verify (read-only) — `from_env()` uses `R2_*` env, reads `R2_BUCKET_NAME`.
2. Nakama self-backup (read/write) — `from_nakama_backup_env()` writes to
   `NAKAMA_R2_BACKUP_BUCKET`, re-using `R2_*` credentials unless
   `NAKAMA_R2_ACCESS_KEY_ID` / `NAKAMA_R2_SECRET_ACCESS_KEY` override them.

Env (base, all required):
    R2_ACCOUNT_ID           — account id; endpoint = https://<id>.r2.cloudflarestorage.com
    R2_ACCESS_KEY_ID        — access key
    R2_SECRET_ACCESS_KEY    — secret
    R2_BUCKET_NAME          — Franky verify bucket (xcloud-backup)

Env (backup-only, required for `from_nakama_backup_env`):
    NAKAMA_R2_BACKUP_BUCKET — destination bucket (nakama-backup)
    NAKAMA_R2_ACCESS_KEY_ID     — optional override
    NAKAMA_R2_SECRET_ACCESS_KEY — optional override

Usage:
    from shared.r2_client import R2Client, R2Unavailable
    try:
        client = R2Client.from_env()
        objs = client.list_objects(prefix="daily/", max_keys=50)
    except R2Unavailable as exc:
        # env missing or API error — caller decides how to degrade
        ...
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from shared.log import get_logger

logger = get_logger("nakama.r2_client")

_REQUIRED_ENV = (
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET_NAME",
)

# reliability.md §7 — external API in same-continent budget
_R2_CONNECT_TIMEOUT_S = 5
_R2_READ_TIMEOUT_S = 15


class R2Unavailable(RuntimeError):
    """Raised when R2 is not reachable (missing env, network error, auth failure)."""


@dataclass(frozen=True)
class R2Object:
    """Immutable record of a single R2 object (subset of S3 ListObjectsV2 fields)."""

    key: str
    size: int
    last_modified: datetime  # boto3 returns timezone-aware UTC datetime
    etag: str


class R2Client:
    """Minimal read-only R2 client for backup verification."""

    def __init__(
        self,
        *,
        account_id: str,
        access_key_id: str,
        secret_access_key: str,
        bucket: str,
    ) -> None:
        self._bucket = bucket
        self._account_id = account_id
        self._s3 = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
            config=BotoConfig(
                connect_timeout=_R2_CONNECT_TIMEOUT_S,
                read_timeout=_R2_READ_TIMEOUT_S,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )
        logger.info("R2Client init bucket=%s account=...%s", bucket, account_id[-4:])

    @classmethod
    def from_env(cls) -> R2Client:
        missing = [k for k in _REQUIRED_ENV if not os.getenv(k)]
        if missing:
            raise R2Unavailable(f"missing R2 env: {missing}")
        return cls(
            account_id=os.environ["R2_ACCOUNT_ID"],
            access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            bucket=os.environ["R2_BUCKET_NAME"],
        )

    @classmethod
    def from_nakama_backup_env(cls) -> R2Client:
        """Writer client for the Nakama self-backup bucket.

        Requires `R2_ACCOUNT_ID` + `NAKAMA_R2_BACKUP_BUCKET`. Uses
        `NAKAMA_R2_ACCESS_KEY_ID` / `NAKAMA_R2_SECRET_ACCESS_KEY` if present,
        otherwise falls back to `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY`.
        """
        if not os.getenv("R2_ACCOUNT_ID"):
            raise R2Unavailable("missing R2 env: ['R2_ACCOUNT_ID']")
        if not os.getenv("NAKAMA_R2_BACKUP_BUCKET"):
            raise R2Unavailable("missing R2 env: ['NAKAMA_R2_BACKUP_BUCKET']")
        access_key = os.getenv("NAKAMA_R2_ACCESS_KEY_ID") or os.getenv("R2_ACCESS_KEY_ID")
        secret_key = os.getenv("NAKAMA_R2_SECRET_ACCESS_KEY") or os.getenv("R2_SECRET_ACCESS_KEY")
        if not access_key or not secret_key:
            raise R2Unavailable(
                "missing R2 credentials: set NAKAMA_R2_ACCESS_KEY_ID/SECRET_ACCESS_KEY "
                "or R2_ACCESS_KEY_ID/SECRET_ACCESS_KEY"
            )
        return cls(
            account_id=os.environ["R2_ACCOUNT_ID"],
            access_key_id=access_key,
            secret_access_key=secret_key,
            bucket=os.environ["NAKAMA_R2_BACKUP_BUCKET"],
        )

    @property
    def bucket(self) -> str:
        return self._bucket

    def list_objects(self, *, prefix: str = "", max_keys: int = 100) -> list[R2Object]:
        """List up to `max_keys` objects; no pagination (Franky only needs the latest few)."""
        try:
            resp = self._s3.list_objects_v2(
                Bucket=self._bucket,
                Prefix=prefix,
                MaxKeys=max_keys,
            )
        except (BotoCoreError, ClientError) as exc:
            raise R2Unavailable(f"list_objects failed bucket={self._bucket}: {exc}") from exc

        contents = resp.get("Contents") or []
        return [
            R2Object(
                key=str(obj["Key"]),
                size=int(obj.get("Size", 0)),
                last_modified=obj["LastModified"],
                etag=str(obj.get("ETag", "")).strip('"'),
            )
            for obj in contents
        ]

    def head_object(self, key: str) -> R2Object:
        """HEAD a single object; raises R2Unavailable on 404 / network error."""
        try:
            resp = self._s3.head_object(Bucket=self._bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            raise R2Unavailable(f"head_object key={key} failed: {exc}") from exc
        return R2Object(
            key=key,
            size=int(resp["ContentLength"]),
            last_modified=resp["LastModified"],
            etag=str(resp.get("ETag", "")).strip('"'),
        )

    # ---- write surface (Nakama self-backup) -----------------------------------

    def upload_file(self, local_path: Path, key: str, *, content_type: str | None = None) -> None:
        """Upload a local file to `key`. Streams from disk — safe for files larger than RAM."""
        extra: dict[str, str] = {}
        if content_type:
            extra["ContentType"] = content_type
        try:
            self._s3.upload_file(
                Filename=str(local_path),
                Bucket=self._bucket,
                Key=key,
                ExtraArgs=extra or None,
            )
        except (BotoCoreError, ClientError) as exc:
            raise R2Unavailable(f"upload_file key={key} failed: {exc}") from exc
        logger.info("r2 upload ok bucket=%s key=%s", self._bucket, key)

    def delete_objects(self, keys: list[str]) -> int:
        """Batch delete; returns count deleted. Empty list is a no-op."""
        if not keys:
            return 0
        # S3 DeleteObjects caps at 1000 keys per request.
        total = 0
        for chunk_start in range(0, len(keys), 1000):
            chunk = keys[chunk_start : chunk_start + 1000]
            try:
                resp = self._s3.delete_objects(
                    Bucket=self._bucket,
                    Delete={"Objects": [{"Key": k} for k in chunk], "Quiet": True},
                )
            except (BotoCoreError, ClientError) as exc:
                raise R2Unavailable(f"delete_objects failed: {exc}") from exc
            errors = resp.get("Errors") or []
            if errors:
                logger.warning("r2 delete_objects partial failure errors=%s", errors)
            total += len(chunk) - len(errors)
        logger.info("r2 delete_objects ok bucket=%s count=%d", self._bucket, total)
        return total

    def delete_older_than(self, days: int, *, prefix: str = "") -> int:
        """Delete all objects under `prefix` older than `days`. Returns count deleted.

        Paginates the full prefix so retention works for thousands of objects.
        """
        if days < 0:
            raise ValueError(f"days must be >= 0, got {days}")
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        paginator = self._s3.get_paginator("list_objects_v2")
        victims: list[str] = []
        try:
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                for obj in page.get("Contents") or []:
                    if obj["LastModified"] < cutoff:
                        victims.append(str(obj["Key"]))
        except (BotoCoreError, ClientError) as exc:
            raise R2Unavailable(f"list_objects (for delete_older_than) failed: {exc}") from exc
        return self.delete_objects(victims)
