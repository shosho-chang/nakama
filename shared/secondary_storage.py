"""Secondary off-site storage — Backblaze B2 (S3-compatible) for backup mirroring.

R2 already replicates within Cloudflare's network, but a sustained Cloudflare
outage (Asia region, account-level lockout, billing dispute) would still take
the only copy down. Mirroring to a different vendor (B2) cuts that single-
vendor risk for a few cents/month at our scale (~10 MB total backup volume).

B2 speaks the S3 protocol with `s3` boto3 client + a custom `endpoint_url`,
so the surface mirrors `R2Client` deliberately (same `list_objects` /
`upload_file` / `download_file` shape) — kept as a separate class rather
than a base hierarchy so neither this module nor `shared/r2_client.py` has
to coordinate releases. Refactor later if a third vendor lands.

Env (all required for `from_env`):
    B2_BUCKET_NAME       — destination bucket (e.g. "nakama-backup-mirror")
    B2_KEY_ID            — application key ID
    B2_APPLICATION_KEY   — application key secret
    B2_ENDPOINT_URL      — region endpoint, e.g. "https://s3.us-west-002.backblazeb2.com"
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from shared.log import get_logger

logger = get_logger("nakama.b2_client")

_REQUIRED_ENV = (
    "B2_BUCKET_NAME",
    "B2_KEY_ID",
    "B2_APPLICATION_KEY",
    "B2_ENDPOINT_URL",
)

# B2 has worse cross-Pacific latency than R2 (typically 200-400 ms RTT vs R2's
# 30-80 ms). Bump timeouts modestly so a daily mirror tick doesn't spuriously
# fail under transient congestion.
_B2_CONNECT_TIMEOUT_S = 10
_B2_READ_TIMEOUT_S = 30


class B2Unavailable(RuntimeError):
    """Raised when B2 is not reachable (missing env, network error, auth failure)."""


@dataclass(frozen=True)
class B2Object:
    """Immutable record of a single B2 object (mirrors R2Object's shape)."""

    key: str
    size: int
    last_modified: datetime
    etag: str


class B2Client:
    """Minimal S3-compatible client for Backblaze B2 backup mirroring."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        key_id: str,
        application_key: str,
        bucket: str,
    ) -> None:
        self._bucket = bucket
        self._endpoint_url = endpoint_url
        self._s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=key_id,
            aws_secret_access_key=application_key,
            # B2 ignores `region_name` but boto3 requires *something* — "us-east-1"
            # is the harmless conventional placeholder.
            region_name="us-east-1",
            config=BotoConfig(
                connect_timeout=_B2_CONNECT_TIMEOUT_S,
                read_timeout=_B2_READ_TIMEOUT_S,
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )
        # Mask the key id in logs — first 4 chars + length is enough to identify
        # the rotation generation without leaking the credential.
        logger.info(
            "B2Client init bucket=%s endpoint=%s key=%s...(%d)",
            bucket,
            endpoint_url,
            key_id[:4],
            len(key_id),
        )

    @classmethod
    def from_env(cls) -> B2Client:
        missing = [k for k in _REQUIRED_ENV if not os.getenv(k)]
        if missing:
            raise B2Unavailable(f"missing B2 env: {missing}")
        return cls(
            endpoint_url=os.environ["B2_ENDPOINT_URL"],
            key_id=os.environ["B2_KEY_ID"],
            application_key=os.environ["B2_APPLICATION_KEY"],
            bucket=os.environ["B2_BUCKET_NAME"],
        )

    @property
    def bucket(self) -> str:
        return self._bucket

    @property
    def endpoint_url(self) -> str:
        return self._endpoint_url

    def list_objects(self, *, prefix: str = "", max_keys: int = 100) -> list[B2Object]:
        try:
            resp = self._s3.list_objects_v2(
                Bucket=self._bucket,
                Prefix=prefix,
                MaxKeys=max_keys,
            )
        except (BotoCoreError, ClientError) as exc:
            raise B2Unavailable(f"list_objects failed bucket={self._bucket}: {exc}") from exc
        contents = resp.get("Contents") or []
        return [
            B2Object(
                key=str(obj["Key"]),
                size=int(obj.get("Size", 0)),
                last_modified=obj["LastModified"],
                etag=str(obj.get("ETag", "")).strip('"'),
            )
            for obj in contents
        ]

    def head_object(self, key: str) -> B2Object:
        try:
            resp = self._s3.head_object(Bucket=self._bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            raise B2Unavailable(f"head_object key={key} failed: {exc}") from exc
        return B2Object(
            key=key,
            size=int(resp["ContentLength"]),
            last_modified=resp["LastModified"],
            etag=str(resp.get("ETag", "")).strip('"'),
        )

    def upload_file(self, local_path: Path, key: str, *, content_type: str | None = None) -> None:
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
            raise B2Unavailable(f"upload_file key={key} failed: {exc}") from exc
        logger.info("b2 upload ok bucket=%s key=%s", self._bucket, key)

    def download_file(self, key: str, local_path: Path) -> None:
        try:
            self._s3.download_file(
                Bucket=self._bucket,
                Key=key,
                Filename=str(local_path),
            )
        except (BotoCoreError, ClientError) as exc:
            raise B2Unavailable(f"download_file key={key} failed: {exc}") from exc
        logger.info("b2 download ok bucket=%s key=%s", self._bucket, key)
