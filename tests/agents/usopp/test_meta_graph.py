from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pytest

from agents.usopp.meta_graph import (
    MetaGraphClient,
    MetaGraphConfig,
    MetaGraphConfigurationError,
)


class FakeTransport:
    def __init__(self, responses: list[Mapping[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.uploads: list[dict[str, Any]] = []

    def request(self, method, path, *, params=None, data=None):
        self.calls.append({"method": method, "path": path, "params": params, "data": data})
        assert self.responses, f"unexpected request: {method} {path}"
        return self.responses.pop(0)

    def upload_file(self, upload_url, file_path, *, headers=None):
        self.uploads.append({"upload_url": upload_url, "file_path": file_path, "headers": headers})
        return {"success": True}


def config() -> MetaGraphConfig:
    return MetaGraphConfig(
        api_version="v23.0",
        page_id="page-1",
        ig_user_id="ig-1",
        page_access_token="secret-never-in-call",
    )


def test_config_from_env_reports_every_missing_setting(monkeypatch):
    for name in (
        "META_GRAPH_API_VERSION",
        "META_PAGE_ID",
        "META_IG_USER_ID",
        "META_PAGE_ACCESS_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(MetaGraphConfigurationError) as exc:
        MetaGraphConfig.from_env()
    assert "META_GRAPH_API_VERSION" in str(exc.value)
    assert "META_PAGE_ACCESS_TOKEN" in str(exc.value)


def test_credential_probe_reads_page_and_instagram_without_leaking_token():
    transport = FakeTransport(
        [{"id": "page-1", "name": "Page"}, {"id": "ig-1", "username": "creator"}]
    )
    result = MetaGraphClient(config(), transport).credential_probe()
    assert result["page"]["name"] == "Page"
    assert result["instagram"]["username"] == "creator"
    assert "secret-never-in-call" not in repr(transport.calls)


def test_instagram_reel_create_poll_publish_permalink_checkpoint_order():
    transport = FakeTransport(
        [
            {"id": "container-1"},
            {"status_code": "IN_PROGRESS"},
            {"status_code": "FINISHED"},
            {"id": "media-1"},
            {"id": "media-1", "permalink": "https://ig.example/reel/1"},
        ]
    )
    saves: list[dict[str, Any]] = []
    checkpoint: dict[str, Any] = {}
    result = MetaGraphClient(
        config(), transport, sleep=lambda _: None, max_poll_attempts=3
    ).publish_instagram_reel(
        video_url="https://signed.example/video",
        caption="caption",
        checkpoint=checkpoint,
        save_checkpoint=saves.append,
    )

    assert [call["path"] for call in transport.calls] == [
        "ig-1/media",
        "container-1",
        "container-1",
        "ig-1/media_publish",
        "media-1",
    ]
    assert saves[0] == {"container_id": "container-1"}
    assert saves[1]["container_finished"] is True
    assert saves[2]["media_id"] == "media-1"
    assert result.external_id == "media-1"
    assert result.permalink == "https://ig.example/reel/1"


def test_instagram_carousel_saves_each_child_and_retry_does_not_recreate_known_resources():
    transport = FakeTransport(
        [
            {"id": "child-2"},
            {"id": "parent-1"},
            {"status_code": "FINISHED"},
            {"id": "media-1"},
            {"permalink": "https://ig.example/p/1"},
        ]
    )
    checkpoint: dict[str, Any] = {"child_ids": ["child-1"]}
    saves: list[dict[str, Any]] = []
    client = MetaGraphClient(config(), transport, sleep=lambda _: None)
    result = client.publish_instagram_carousel(
        image_urls=["https://signed.example/1", "https://signed.example/2"],
        caption="cards",
        checkpoint=checkpoint,
        save_checkpoint=saves.append,
    )
    assert saves[0]["child_ids"] == ["child-1", "child-2"]
    assert transport.calls[0]["data"]["image_url"] == "https://signed.example/2"
    assert transport.calls[1]["data"]["children"] == "child-1,child-2"
    assert result.external_id == "media-1"

    retry_transport = FakeTransport([])
    retry_result = MetaGraphClient(config(), retry_transport).publish_instagram_carousel(
        image_urls=["https://new.example/1", "https://new.example/2"],
        caption="cards",
        checkpoint=checkpoint,
        save_checkpoint=lambda _: None,
    )
    assert retry_transport.calls == []
    assert retry_result.external_id == "media-1"


def test_facebook_reel_start_upload_finish_reconcile_and_retry(tmp_path: Path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    transport = FakeTransport(
        [
            {"video_id": "video-1", "upload_url": "https://upload.example/session"},
            {"success": True},
            {
                "id": "video-1",
                "status": {"processing_phase": {"status": "complete"}},
                "permalink_url": "https://fb.example/reel/1",
            },
        ]
    )
    checkpoint: dict[str, Any] = {}
    saves: list[dict[str, Any]] = []
    result = MetaGraphClient(config(), transport).publish_facebook_reel(
        video_path=video,
        description="description",
        checkpoint=checkpoint,
        save_checkpoint=saves.append,
    )
    phases = [
        call["data"].get("upload_phase") if call["data"] else None for call in transport.calls
    ]
    assert phases == [
        "start",
        "finish",
        None,
    ]
    assert transport.uploads[0]["file_path"] == video
    assert saves[0]["video_id"] == "video-1"
    assert saves[1]["uploaded"] is True
    assert saves[2]["finished"] is True
    assert result.permalink == "https://fb.example/reel/1"

    retry_transport = FakeTransport(
        [
            {
                "status": {"processing_phase": {"status": "complete"}},
                "permalink_url": "https://fb.example/reel/1",
            }
        ]
    )
    MetaGraphClient(config(), retry_transport).publish_facebook_reel(
        video_path=video,
        description="description",
        checkpoint=checkpoint,
        save_checkpoint=lambda _: None,
    )
    assert retry_transport.uploads == []
    assert len(retry_transport.calls) == 1


def test_facebook_multi_photo_creates_unpublished_photos_and_one_feed_post():
    transport = FakeTransport(
        [
            {"id": "photo-2"},
            {"id": "page-1_post-1"},
            {"permalink_url": "https://fb.example/posts/1"},
        ]
    )
    checkpoint: dict[str, Any] = {"photo_ids": ["photo-1"]}
    saves: list[dict[str, Any]] = []
    result = MetaGraphClient(config(), transport).publish_facebook_multi_photo(
        image_urls=["https://signed.example/1", "https://signed.example/2"],
        message="message",
        checkpoint=checkpoint,
        save_checkpoint=saves.append,
    )
    assert [call["path"] for call in transport.calls] == [
        "page-1/photos",
        "page-1/feed",
        "page-1_post-1",
    ]
    assert transport.calls[0]["data"]["published"] == "false"
    assert transport.calls[1]["data"]["attached_media"] == (
        '[{"media_fbid":"photo-1"},{"media_fbid":"photo-2"}]'
    )
    assert saves[0]["photo_ids"] == ["photo-1", "photo-2"]
    assert result.external_id == "page-1_post-1"
