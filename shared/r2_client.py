"""Cloudflare R2 client — read-only surface for Franky backup verification.

Wraps boto3 S3 with R2-specific endpoint construction. Minimal surface (list + head);
write operations are out of scope — Franky doesn't create backups, only verifies them.

Env (all required):
    R2_ACCOUNT_ID           — account id; endpoint = https://<id>.r2.cloudflarestorage.com
    R2_ACCESS_KEY_ID        — access key (Object Read)
    R2_SECRET_ACCESS_KEY    — secret
    R2_BUCKET_NAME          — bucket to read

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
from datetime import datetime

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
