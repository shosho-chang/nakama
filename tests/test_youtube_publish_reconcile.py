from __future__ import annotations

from datetime import datetime, timezone

from agents.usopp.youtube_publish import reconcile_and_persist, reconcile_youtube_target


class _Request:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class _Resource:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return _Request(self.payload)


class _YouTube:
    def __init__(self, video_payload, caption_payload):
        self.video_resource = _Resource(video_payload)
        self.caption_resource = _Resource(caption_payload)

    def videos(self):
        return self.video_resource

    def captions(self):
        return self.caption_resource


def test_reconcile_reads_processing_privacy_schedule_and_missing_zh_tw_caption():
    yt = _YouTube(
        {
            "items": [
                {
                    "id": "video-1",
                    "status": {
                        "privacyStatus": "private",
                        "publishAt": "2026-08-20T12:00:00Z",
                    },
                    "processingDetails": {"processingStatus": "processing"},
                }
            ]
        },
        {"items": []},
    )
    now = datetime(2026, 8, 19, tzinfo=timezone.utc)

    result = reconcile_youtube_target(yt, {"video_id": "video-1"}, now=now)

    assert result.target_fields() == {
        "status": "uploaded",
        "video_processing_status": "processing",
        "platform_privacy_status": "private",
        "platform_publish_at": "2026-08-20T12:00:00Z",
        "caption_status": "missing",
        "reconciliation_error": "zh-TW caption missing",
        "last_reconciled_at": "2026-08-19T00:00:00+00:00",
    }
    assert yt.video_resource.calls == [
        {"part": "status,processingDetails", "id": "video-1"}
    ]
    assert yt.caption_resource.calls == [{"part": "snippet", "videoId": "video-1"}]


def test_reconcile_observes_manual_studio_publish_and_serving_caption():
    yt = _YouTube(
        {
            "items": [
                {
                    "id": "video-1",
                    "status": {"privacyStatus": "public", "uploadStatus": "processed"},
                    "processingDetails": {"processingStatus": "succeeded"},
                }
            ]
        },
        {
            "items": [
                {
                    "id": "caption-1",
                    "snippet": {"language": "zh-TW", "status": "serving"},
                }
            ]
        },
    )

    result = reconcile_youtube_target(yt, {"video_id": "video-1"})

    assert result.release_status == "published"
    assert result.video_processing_status == "processed"
    assert result.caption_status == "serving"
    assert result.reconciliation_error is None


def test_manual_studio_publish_reconciliation_is_written_back_to_target():
    yt = _YouTube(
        {
            "items": [
                {
                    "id": "video-1",
                    "status": {"privacyStatus": "public"},
                    "processingDetails": {"processingStatus": "succeeded"},
                }
            ]
        },
        {"items": [{"snippet": {"language": "zh-TW", "status": "serving"}}]},
    )
    updates = []

    result = reconcile_and_persist(
        yt,
        {"id": 9, "video_id": "video-1"},
        update_target=lambda target_id, **fields: updates.append((target_id, fields)),
        now=datetime(2026, 8, 19, tzinfo=timezone.utc),
    )

    assert result.release_status == "published"
    assert updates == [(9, result.target_fields())]
