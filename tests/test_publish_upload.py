"""publish_upload 純函數測試（真上傳靠修修 approve 後首跑 UAT）。"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import publish_upload  # noqa: E402
from publish_upload import (  # noqa: E402
    YouTubeVideoNotFoundError,
    _upload_one,
    build_insert_body,
    cmd_cc_only,
    cmd_run,
    observe_youtube_video,
    reconcile_target,
    to_utc_iso,
)


def test_to_utc_iso_converts_taipei():
    assert to_utc_iso("2026-08-10T20:00:00+08:00") == "2026-08-10T12:00:00Z"


def test_to_utc_iso_rejects_naive():
    """排程是硬承諾——缺時區的時間不能用猜的。"""
    with pytest.raises(ValueError):
        to_utc_iso("2026-08-10T20:00:00")


TARGET = {
    "title": "腦科學家的腦腐自救 3 步",
    "description": "hook…\n\n⏱ 00:00 開場",
    "publish_at": "2026-08-10T20:00:00+08:00",
}
RELEASE = {"cut_id": "punch-L5"}


def test_build_insert_body_private_with_schedule():
    body = build_insert_body(TARGET, RELEASE)
    assert body["status"]["privacyStatus"] == "private"
    assert body["status"]["publishAt"] == "2026-08-10T12:00:00Z"
    assert body["snippet"]["defaultAudioLanguage"] == "zh-TW"


def test_build_insert_body_no_schedule_stays_private():
    body = build_insert_body({**TARGET, "publish_at": None}, RELEASE)
    assert "publishAt" not in body["status"]
    assert body["status"]["privacyStatus"] == "private"


def test_build_insert_body_requires_copy():
    """title/description 未回填 = Slice 2 沒跑——不拿工作代號充當發布標題。"""
    with pytest.raises(ValueError):
        build_insert_body({"title": None, "description": None}, RELEASE)


class _UploadRequest:
    resumable_uri = None

    def next_chunk(self):
        return None, {"id": "yt-new"}


class _UploadVideos:
    def insert(self, **_kwargs):
        return _UploadRequest()


class _UploadYouTube:
    def videos(self):
        return _UploadVideos()


@pytest.mark.parametrize(("format_name", "caption_calls"), [("long", 1), ("short", 0)])
def test_upload_one_routes_captions_by_format(tmp_path, monkeypatch, format_name, caption_calls):
    video = tmp_path / "episode" / "highlights" / "exports" / "cut.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"video")
    upload_cc = MagicMock()
    monkeypatch.setattr(publish_upload, "upload_captions", upload_cc)
    monkeypatch.setattr(publish_upload, "write_progress", MagicMock())
    monkeypatch.setattr("shared.release_store.update_target", MagicMock())

    item = {
        "release": {
            "episode": "episode",
            "cut_id": "cut",
            "format": format_name,
            "file_path": str(video),
        },
        "target": {
            "id": 1,
            "title": "標題",
            "description": "描述",
            "publish_at": None,
            "thumbnail_path": None,
        },
    }
    result = _upload_one(_UploadYouTube(), item, tmp_path)
    assert result["video_id"] == "yt-new"
    assert upload_cc.call_count == caption_calls


def test_cc_only_short_fails_before_loading_youtube(tmp_path, monkeypatch):
    from shared.release_store import ensure_target, register_release, update_target

    rid = register_release("ep", "short-1", "short", str(tmp_path / "short.mp4"))
    tid = ensure_target(rid, "youtube")
    update_target(tid, video_id="yt-short", status="uploaded")
    load_yt = MagicMock(side_effect=AssertionError("must not load YouTube for Short CC"))
    monkeypatch.setattr(publish_upload, "_load_yt", load_yt)
    with pytest.raises(SystemExit, match="不可使用 --cc-only"):
        cmd_cc_only(SimpleNamespace(episode="ep", cc_only="short-1"))
    load_yt.assert_not_called()


def test_run_exact_uploaded_target_skips_duplicate_without_loading_youtube(
    tmp_path, monkeypatch, capsys
):
    from shared.release_store import ensure_target, register_release, update_target

    rid = register_release("ep", "short-1", "short", str(tmp_path / "short.mp4"))
    tid = ensure_target(rid, "youtube")
    update_target(tid, video_id="yt-short", status="uploaded")
    load_yt = MagicMock(side_effect=AssertionError("must not upload duplicate"))
    monkeypatch.setattr(publish_upload, "_load_yt", load_yt)

    result = cmd_run(SimpleNamespace(episode="ep", cut="short-1", force=False, dry_run=False))
    assert result == 0
    assert "已有 video_id" in capsys.readouterr().out
    load_yt.assert_not_called()


def test_uploader_and_observer_share_controlled_credential_loader(tmp_path, monkeypatch):
    token_path = tmp_path / "youtube_token.json"
    token_path.write_text('{"fixture":"redacted"}', encoding="utf-8")
    clients = [object(), object()]
    calls = []

    def controlled_loader(path):
        calls.append(path)
        return clients[len(calls) - 1]

    monkeypatch.setattr(publish_upload, "TOKEN_PATH", token_path)
    monkeypatch.setattr(publish_upload, "load_youtube_client", controlled_loader, raising=False)

    assert publish_upload._load_yt() is clients[0]
    assert publish_upload.load_youtube_observer() is clients[1]
    assert calls == [token_path, token_path]


class _ListRequest:
    def __init__(self, response):
        self.response = response

    def execute(self):
        return self.response


class _ListVideos:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return _ListRequest(self.response)


class _ListYouTube:
    def __init__(self, response):
        self.resource = _ListVideos(response)

    def videos(self):
        return self.resource


@pytest.mark.parametrize(
    ("response", "outcome", "evidence", "certain"),
    [
        (
            {
                "items": [
                    {
                        "status": {"privacyStatus": "public", "uploadStatus": "processed"},
                        "processingDetails": {"processingStatus": "succeeded"},
                    }
                ]
            },
            "published",
            "public",
            True,
        ),
        (
            {
                "items": [
                    {
                        "status": {"privacyStatus": "private", "uploadStatus": "uploaded"},
                        "processingDetails": {"processingStatus": "processing"},
                    }
                ]
            },
            "pending",
            "processing",
            True,
        ),
        (
            {
                "items": [
                    {
                        "status": {
                            "privacyStatus": "private",
                            "uploadStatus": "rejected",
                            "rejectionReason": "copyright",
                        },
                        "processingDetails": {"processingStatus": "terminated"},
                    }
                ]
            },
            "failed",
            "processing_failed",
            True,
        ),
        (
            {
                "items": [
                    {
                        "status": {
                            "privacyStatus": "public",
                            "uploadStatus": "rejected",
                        },
                        "processingDetails": {"processingStatus": "terminated"},
                    }
                ]
            },
            "pending",
            "unknown",
            False,
        ),
        ({"items": [{"status": {}, "processingDetails": {}}]}, "pending", "unknown", False),
    ],
)
def test_observe_youtube_video_classifies_one_read_without_mutation(
    response, outcome, evidence, certain
):
    youtube = _ListYouTube(response)

    observation = observe_youtube_video(youtube, "yt-1")

    assert observation.outcome == outcome
    assert observation.evidence_category == evidence
    assert observation.certain is certain
    assert observation.permalink is None
    assert youtube.resource.calls == [{"part": "status,processingDetails", "id": "yt-1"}]


def _stored_target(tmp_path, *, cut_id="short-1"):
    from shared.release_store import ensure_target, get_release, register_release, update_target

    rid = register_release("ep", cut_id, "short", str(tmp_path / f"{cut_id}.mp4"))
    tid = ensure_target(rid, "youtube")
    update_target(
        tid,
        video_id=f"yt-{cut_id}",
        url=f"https://youtu.be/yt-{cut_id}",
        status="uploaded",
    )
    rel = get_release("ep", cut_id)
    return rel, rel["targets"][0]


@pytest.mark.parametrize(
    ("privacy", "processing", "expected"),
    [("private", "processing", "uploaded"), ("public", "succeeded", "published")],
)
def test_reconcile_private_and_public_states(tmp_path, privacy, processing, expected):
    from shared.release_store import get_release

    rel, target = _stored_target(tmp_path)
    yt = _ListYouTube(
        {
            "items": [
                {
                    "status": {"privacyStatus": privacy, "uploadStatus": "processed"},
                    "processingDetails": {"processingStatus": processing},
                }
            ]
        }
    )
    result = reconcile_target(yt, rel, target)
    stored = get_release("ep", "short-1")["targets"][0]
    assert result["status"] == expected
    assert stored["status"] == expected
    assert stored["video_id"] == "yt-short-1"
    assert yt.resource.calls == [{"part": "status,processingDetails", "id": "yt-short-1"}]
    assert result["privacy_status"] == privacy
    assert result["processing_status"] == processing
    assert result["upload_status"] == "processed"
    assert "publish_at" in result


def test_reconcile_processing_rejection_marks_failed_with_reason(tmp_path):
    from shared.release_store import get_release

    rel, target = _stored_target(tmp_path)
    yt = _ListYouTube(
        {
            "items": [
                {
                    "status": {
                        "privacyStatus": "private",
                        "uploadStatus": "rejected",
                        "rejectionReason": "copyright",
                    },
                    "processingDetails": {"processingStatus": "terminated"},
                }
            ]
        }
    )
    reconcile_target(yt, rel, target)
    stored = get_release("ep", "short-1")["targets"][0]
    assert stored["status"] == "failed"
    assert "copyright" in stored["error"]
    assert stored["video_id"] == "yt-short-1"


def test_reconcile_not_found_fails_loud_without_clearing_video_id(tmp_path):
    from shared.release_store import get_release

    rel, target = _stored_target(tmp_path)
    with pytest.raises(YouTubeVideoNotFoundError, match="保留既有 ID"):
        reconcile_target(_ListYouTube({"items": []}), rel, target)
    stored = get_release("ep", "short-1")["targets"][0]
    assert stored["status"] == "uploaded"
    assert stored["video_id"] == "yt-short-1"
