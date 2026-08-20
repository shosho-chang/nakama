"""Meta Graph API anti-corruption layer for Stage 6 social publishing.

The public client is deliberately transport-agnostic.  Production code may
provide an authenticated HTTP transport; tests use deterministic fakes.  The
transport never receives the Page access token as a request argument, which
keeps credentials out of fake call logs and publish checkpoints.
"""

from __future__ import annotations

import copy
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping, Protocol


class MetaGraphError(RuntimeError):
    """A Meta operation failed or returned an invalid response."""


class MetaGraphConfigurationError(MetaGraphError):
    """Required Meta settings are missing or invalid."""


class MetaGraphRejectedError(MetaGraphError):
    """Meta returned an explicit Graph error for a valid transport response."""

    def __init__(self, message: str, *, is_transient: bool | None = None) -> None:
        super().__init__(message)
        self.is_transient = is_transient


class MetaGraphTransport(Protocol):
    """Authenticated transport boundary used by :class:`MetaGraphClient`."""

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]: ...

    def upload_file(
        self,
        upload_url: str,
        file_path: Path,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> Mapping[str, Any]: ...


Checkpoint = MutableMapping[str, Any]
SaveCheckpoint = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class MetaGraphConfig:
    api_version: str
    page_id: str
    ig_user_id: str
    page_access_token: str

    @classmethod
    def from_env(cls) -> MetaGraphConfig:
        names = {
            "api_version": "META_GRAPH_API_VERSION",
            "page_id": "META_PAGE_ID",
            "ig_user_id": "META_IG_USER_ID",
            "page_access_token": "META_PAGE_ACCESS_TOKEN",
        }
        values = {field: os.getenv(env_name, "").strip() for field, env_name in names.items()}
        missing = [env_name for field, env_name in names.items() if not values[field]]
        if missing:
            raise MetaGraphConfigurationError(
                "missing required Meta settings: " + ", ".join(sorted(missing))
            )
        version = values["api_version"]
        if not version.startswith("v") or not version[1:].replace(".", "", 1).isdigit():
            raise MetaGraphConfigurationError(
                "META_GRAPH_API_VERSION must be explicit, for example v23.0"
            )
        return cls(**values)


@dataclass(frozen=True)
class MetaPublishResult:
    external_id: str
    permalink: str | None
    checkpoint: dict[str, Any]


class MetaGraphClient:
    """Publish Instagram/Facebook media while persisting crash checkpoints."""

    def __init__(
        self,
        config: MetaGraphConfig,
        transport: MetaGraphTransport,
        *,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval_seconds: float = 2.0,
        max_poll_attempts: int = 60,
    ) -> None:
        if max_poll_attempts < 1:
            raise ValueError("max_poll_attempts must be positive")
        self.config = config
        self.transport = transport
        self.sleep = sleep
        self.poll_interval_seconds = poll_interval_seconds
        self.max_poll_attempts = max_poll_attempts

    def credential_probe(self) -> dict[str, Any]:
        """Verify the configured Page and Instagram professional identities."""
        page = self._request("GET", self.config.page_id, params={"fields": "id,name"})
        instagram = self._request("GET", self.config.ig_user_id, params={"fields": "id,username"})
        return {"page": dict(page), "instagram": dict(instagram)}

    def publish_instagram_reel(
        self,
        *,
        video_url: str,
        caption: str,
        checkpoint: Checkpoint,
        save_checkpoint: SaveCheckpoint,
    ) -> MetaPublishResult:
        container_id = self._checkpoint_id(checkpoint, "container_id")
        if not container_id:
            response = self._request(
                "POST",
                f"{self.config.ig_user_id}/media",
                data={"media_type": "REELS", "video_url": video_url, "caption": caption},
            )
            container_id = self._required_id(response, "IG Reel create")
            checkpoint["container_id"] = container_id
            self._save(checkpoint, save_checkpoint)

        if not checkpoint.get("container_finished"):
            self._poll_ig_container(container_id)
            checkpoint["container_finished"] = True
            self._save(checkpoint, save_checkpoint)

        media_id = self._checkpoint_id(checkpoint, "media_id")
        if not media_id:
            response = self._request(
                "POST",
                f"{self.config.ig_user_id}/media_publish",
                data={"creation_id": container_id},
            )
            media_id = self._required_id(response, "IG Reel publish")
            checkpoint["media_id"] = media_id
            self._save(checkpoint, save_checkpoint)

        permalink = self._resolve_permalink(media_id, checkpoint, save_checkpoint)
        return self._result(media_id, permalink, checkpoint)

    def publish_instagram_carousel(
        self,
        *,
        image_urls: list[str],
        caption: str,
        checkpoint: Checkpoint,
        save_checkpoint: SaveCheckpoint,
    ) -> MetaPublishResult:
        if not 2 <= len(image_urls) <= 10:
            raise MetaGraphError("Instagram carousel requires 2 to 10 images")

        child_ids = checkpoint.setdefault("child_ids", [])
        if not isinstance(child_ids, list) or len(child_ids) > len(image_urls):
            raise MetaGraphError("invalid Instagram carousel child_ids checkpoint")
        for index in range(len(child_ids), len(image_urls)):
            response = self._request(
                "POST",
                f"{self.config.ig_user_id}/media",
                data={"image_url": image_urls[index], "is_carousel_item": "true"},
            )
            child_ids.append(self._required_id(response, f"IG carousel child {index}"))
            self._save(checkpoint, save_checkpoint)

        parent_id = self._checkpoint_id(checkpoint, "parent_container_id")
        if not parent_id:
            response = self._request(
                "POST",
                f"{self.config.ig_user_id}/media",
                data={
                    "media_type": "CAROUSEL",
                    "caption": caption,
                    "children": ",".join(child_ids),
                },
            )
            parent_id = self._required_id(response, "IG carousel parent")
            checkpoint["parent_container_id"] = parent_id
            self._save(checkpoint, save_checkpoint)

        if not checkpoint.get("parent_finished"):
            self._poll_ig_container(parent_id)
            checkpoint["parent_finished"] = True
            self._save(checkpoint, save_checkpoint)

        media_id = self._checkpoint_id(checkpoint, "media_id")
        if not media_id:
            response = self._request(
                "POST",
                f"{self.config.ig_user_id}/media_publish",
                data={"creation_id": parent_id},
            )
            media_id = self._required_id(response, "IG carousel publish")
            checkpoint["media_id"] = media_id
            self._save(checkpoint, save_checkpoint)

        permalink = self._resolve_permalink(media_id, checkpoint, save_checkpoint)
        return self._result(media_id, permalink, checkpoint)

    def publish_facebook_reel(
        self,
        *,
        video_path: Path,
        description: str,
        scheduled_at: datetime | None = None,
        checkpoint: Checkpoint,
        save_checkpoint: SaveCheckpoint,
    ) -> MetaPublishResult:
        if scheduled_at is not None:
            if scheduled_at.tzinfo is None or scheduled_at.utcoffset() is None:
                raise ValueError("scheduled_at must be timezone-aware")
            scheduled_at = scheduled_at.astimezone(timezone.utc)
        video_path = Path(video_path)
        if not video_path.is_file():
            raise MetaGraphError(f"Facebook Reel file does not exist: {video_path}")

        restart_required = bool(checkpoint.get("facebook_reel_restart_required"))
        if restart_required:
            for key in (
                "video_id",
                "upload_url",
                "uploaded",
                "finished",
                "finish_mode",
                "scheduled_publish_time",
                "permalink",
            ):
                checkpoint.pop(key, None)

        video_id = self._checkpoint_id(checkpoint, "video_id")
        upload_url = self._checkpoint_id(checkpoint, "upload_url")
        if not video_id or not upload_url:
            response = self._request(
                "POST",
                f"{self.config.page_id}/video_reels",
                data={"upload_phase": "start"},
            )
            video_id = self._required_id(response, "Facebook Reel start", keys=("video_id", "id"))
            upload_url = str(response.get("upload_url") or "").strip()
            if not upload_url:
                raise MetaGraphError("Facebook Reel start response missing upload_url")
            checkpoint.pop("facebook_reel_restart_required", None)
            checkpoint.pop("facebook_reel_restart_reason", None)
            checkpoint.update({"video_id": video_id, "upload_url": upload_url})
            self._save(checkpoint, save_checkpoint)

        if not checkpoint.get("uploaded"):
            self.transport.upload_file(
                upload_url,
                video_path,
                headers={
                    "offset": "0",
                    "file_size": str(video_path.stat().st_size),
                },
            )
            checkpoint["uploaded"] = True
            self._save(checkpoint, save_checkpoint)

        if not checkpoint.get("finished"):
            finish_mode = "scheduled" if scheduled_at is not None else "published"
            finish_data: dict[str, Any] = {
                "upload_phase": "finish",
                "video_id": video_id,
                "video_state": "SCHEDULED" if scheduled_at is not None else "PUBLISHED",
                "description": description,
            }
            if scheduled_at is not None:
                finish_data["scheduled_publish_time"] = int(scheduled_at.timestamp())
            try:
                self._request(
                    "POST",
                    f"{self.config.page_id}/video_reels",
                    data=finish_data,
                )
            except MetaGraphRejectedError as exc:
                if exc.is_transient is True:
                    raise
                for key in (
                    "video_id",
                    "upload_url",
                    "uploaded",
                    "finished",
                    "finish_mode",
                    "scheduled_publish_time",
                    "permalink",
                ):
                    checkpoint.pop(key, None)
                checkpoint["facebook_reel_restart_required"] = True
                checkpoint["facebook_reel_restart_reason"] = "finish_rejected"
                self._save(checkpoint, save_checkpoint)
                raise
            checkpoint["finished"] = True
            checkpoint["finish_mode"] = finish_mode
            if scheduled_at is not None:
                checkpoint["scheduled_publish_time"] = int(scheduled_at.timestamp())
            self._save(checkpoint, save_checkpoint)

        permalink = self._poll_facebook_video(video_id)
        checkpoint["permalink"] = permalink
        self._save(checkpoint, save_checkpoint)
        return self._result(video_id, permalink, checkpoint)

    def publish_facebook_multi_photo(
        self,
        *,
        image_urls: list[str],
        message: str,
        checkpoint: Checkpoint,
        save_checkpoint: SaveCheckpoint,
    ) -> MetaPublishResult:
        if not image_urls:
            raise MetaGraphError("Facebook multi-photo post requires at least one image")
        photo_ids = checkpoint.setdefault("photo_ids", [])
        if not isinstance(photo_ids, list) or len(photo_ids) > len(image_urls):
            raise MetaGraphError("invalid Facebook photo_ids checkpoint")
        for index in range(len(photo_ids), len(image_urls)):
            response = self._request(
                "POST",
                f"{self.config.page_id}/photos",
                data={"url": image_urls[index], "published": "false"},
            )
            photo_ids.append(self._required_id(response, f"Facebook unpublished photo {index}"))
            self._save(checkpoint, save_checkpoint)

        post_id = self._checkpoint_id(checkpoint, "post_id")
        if not post_id:
            response = self._request(
                "POST",
                f"{self.config.page_id}/feed",
                data={
                    "message": message,
                    "attached_media": json.dumps(
                        [{"media_fbid": photo_id} for photo_id in photo_ids],
                        separators=(",", ":"),
                    ),
                },
            )
            post_id = self._required_id(response, "Facebook multi-photo post")
            checkpoint["post_id"] = post_id
            self._save(checkpoint, save_checkpoint)

        permalink = self._resolve_permalink(
            post_id, checkpoint, save_checkpoint, field="permalink_url"
        )
        return self._result(post_id, permalink, checkpoint)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        response = self.transport.request(method, path, params=params, data=data)
        if not isinstance(response, Mapping):
            raise MetaGraphError(f"Meta {method} {path} returned a non-object response")
        error = response.get("error")
        if error:
            message = error.get("message") if isinstance(error, Mapping) else str(error)
            is_transient = error.get("is_transient") if isinstance(error, Mapping) else None
            raise MetaGraphRejectedError(
                f"Meta {method} {path} failed: {message}",
                is_transient=is_transient if isinstance(is_transient, bool) else None,
            )
        return response

    def _poll_ig_container(self, container_id: str) -> None:
        for attempt in range(self.max_poll_attempts):
            response = self._request("GET", container_id, params={"fields": "status_code,status"})
            status = str(response.get("status_code") or response.get("status") or "").upper()
            if status == "FINISHED":
                return
            if status in {"ERROR", "EXPIRED"}:
                raise MetaGraphError(f"Instagram container {container_id} ended as {status}")
            if attempt + 1 < self.max_poll_attempts:
                self.sleep(self.poll_interval_seconds)
        raise MetaGraphError(f"Instagram container {container_id} did not finish in time")

    def _poll_facebook_video(self, video_id: str) -> str | None:
        for attempt in range(self.max_poll_attempts):
            response = self._request("GET", video_id, params={"fields": "id,status,permalink_url"})
            status_value = response.get("status")
            if isinstance(status_value, Mapping):
                status = str(
                    status_value.get("processing_phase", {}).get("status")
                    if isinstance(status_value.get("processing_phase"), Mapping)
                    else status_value.get("video_status") or ""
                ).upper()
            else:
                status = str(status_value or "").upper()
            if status in {"COMPLETE", "COMPLETED", "READY", "PUBLISHED"}:
                value = response.get("permalink_url")
                return self._normalize_facebook_permalink(value)
            if status in {"ERROR", "FAILED", "EXPIRED"}:
                raise MetaGraphError(f"Facebook video {video_id} ended as {status}")
            if attempt + 1 < self.max_poll_attempts:
                self.sleep(self.poll_interval_seconds)
        raise MetaGraphError(f"Facebook video {video_id} did not finish in time")

    @staticmethod
    def _normalize_facebook_permalink(value: object) -> str | None:
        if not value:
            return None
        permalink = str(value)
        if permalink.startswith(("http://", "https://")):
            return permalink
        if permalink.startswith("/reel/"):
            return "https://www.facebook.com" + permalink
        return permalink

    def _resolve_permalink(
        self,
        external_id: str,
        checkpoint: Checkpoint,
        save_checkpoint: SaveCheckpoint,
        *,
        field: str = "permalink",
    ) -> str | None:
        existing = checkpoint.get("permalink")
        if existing:
            return str(existing)
        response = self._request("GET", external_id, params={"fields": f"id,{field}"})
        value = response.get(field)
        permalink = str(value) if value else None
        checkpoint["permalink"] = permalink
        self._save(checkpoint, save_checkpoint)
        return permalink

    @staticmethod
    def _checkpoint_id(checkpoint: Mapping[str, Any], key: str) -> str:
        return str(checkpoint.get(key) or "").strip()

    @staticmethod
    def _required_id(
        response: Mapping[str, Any],
        operation: str,
        *,
        keys: tuple[str, ...] = ("id",),
    ) -> str:
        for key in keys:
            value = str(response.get(key) or "").strip()
            if value:
                return value
        raise MetaGraphError(f"{operation} response missing {'/'.join(keys)}")

    @staticmethod
    def _save(checkpoint: Checkpoint, save_checkpoint: SaveCheckpoint) -> None:
        save_checkpoint(copy.deepcopy(dict(checkpoint)))

    @staticmethod
    def _result(
        external_id: str, permalink: str | None, checkpoint: Checkpoint
    ) -> MetaPublishResult:
        return MetaPublishResult(
            external_id=external_id,
            permalink=permalink,
            checkpoint=copy.deepcopy(dict(checkpoint)),
        )
