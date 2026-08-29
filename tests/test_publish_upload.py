"""publish_upload 純函數測試（真上傳靠修修 approve 後首跑 UAT）。"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import publish_upload  # noqa: E402
import youtube_auth  # noqa: E402
from publish_upload import (  # noqa: E402
    YOUTUBE_PUBLISH_SCOPES,
    ResumeProbe,
    UploadSessionNeedsRestart,
    YouTubeCredentialPreflightError,
    YouTubeVideoNotFoundError,
    _upload_one,
    assert_youtube_publish_credentials,
    build_insert_body,
    cmd_cc_only,
    cmd_run,
    observe_youtube_video,
    reconcile_target,
    resume_video_upload,
    target_requires_explicit_restart,
    to_utc_iso,
    upload_failure_status,
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


class _Credentials:
    def __init__(self, scopes, *, refresh_token="refresh-token"):
        self.scopes = scopes
        self.refresh_token = refresh_token


def test_oauth_preflight_rejects_missing_force_ssl_before_upload_client_use():
    credentials = _Credentials(["https://www.googleapis.com/auth/youtube.upload"])

    with pytest.raises(YouTubeCredentialPreflightError, match="youtube.force-ssl"):
        assert_youtube_publish_credentials(credentials)


def test_oauth_preflight_accepts_exact_publish_scopes_and_refresh_token():
    assert_youtube_publish_credentials(_Credentials(list(YOUTUBE_PUBLISH_SCOPES)))


def test_auth_requests_the_same_scopes_enforced_by_upload_preflight():
    assert set(youtube_auth.SCOPES) == set(YOUTUBE_PUBLISH_SCOPES)


def test_youtube_client_rejects_missing_scope_before_service_builder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    token = tmp_path / "youtube_token.json"
    token.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(publish_upload, "TOKEN_PATH", token)
    credentials = _Credentials(["https://www.googleapis.com/auth/youtube.upload"])
    credentials.valid = True
    built = []

    with pytest.raises(SystemExit, match="youtube.force-ssl"):
        publish_upload._load_yt(
            credentials_loader=lambda _path: credentials,
            request_factory=lambda: object(),
            service_builder=lambda *_args, **_kwargs: built.append(True),
        )

    assert built == []


class _ResumeTransport:
    def __init__(self, probe: ResumeProbe):
        self.probe_result = probe
        self.probes = []
        self.uploads = []

    def probe(self, session_uri: str, total_bytes: int) -> ResumeProbe:
        self.probes.append((session_uri, total_bytes))
        return self.probe_result

    def upload(self, session_uri, video_path, *, start_offset, chunk_bytes, on_progress):
        self.uploads.append((session_uri, video_path, start_offset, chunk_bytes))
        on_progress(video_path.stat().st_size, video_path.stat().st_size)
        return {"id": "video-resumed"}


def test_crash_resume_probes_persisted_session_and_continues_from_remote_offset(
    tmp_path: Path,
):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"0123456789")
    transport = _ResumeTransport(ResumeProbe.active(next_offset=4))
    progress = []

    response = resume_video_upload(
        transport,
        "https://upload.example/session",
        video,
        chunk_bytes=3,
        on_progress=lambda sent, total: progress.append((sent, total)),
    )

    assert response == {"id": "video-resumed"}
    assert transport.probes == [("https://upload.example/session", 10)]
    assert transport.uploads[0][2] == 4
    assert progress[-1] == (10, 10)


def test_expired_session_is_cleared_and_requires_explicit_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    video = tmp_path / "exports" / "cut.mp4"
    video.parent.mkdir()
    video.write_bytes(b"video")
    item = {
        "release": {"cut_id": "cut", "episode": "episode", "file_path": str(video)},
        "target": {
            "id": 7,
            **TARGET,
            "upload_session_uri": "https://upload.example/expired",
            "video_id": None,
            "thumbnail_path": None,
        },
    }
    updates = []
    monkeypatch.setattr(
        "shared.release_store.update_target",
        lambda target_id, **fields: updates.append((target_id, fields)),
    )

    class ForbiddenYouTube:
        def videos(self):
            raise AssertionError("expired session must not create videos.insert")

    with pytest.raises(UploadSessionNeedsRestart):
        publish_upload._upload_one(
            ForbiddenYouTube(),
            item,
            tmp_path,
            resume_transport=_ResumeTransport(ResumeProbe.expired()),
        )

    assert (7, {"upload_session_uri": None}) in updates


@pytest.mark.parametrize("status", ["failed", "uploading"])
def test_existing_video_id_recovers_failed_thumbnail_without_video_insert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str
):
    thumbnail = tmp_path / "Attachments" / "thumb.png"
    thumbnail.parent.mkdir()
    thumbnail.write_bytes(b"png")
    item = {
        "release": {
            "cut_id": "cut",
            "episode": "episode",
            "file_path": str(tmp_path / "episode" / "highlights" / "exports" / "cut.mp4"),
        },
        "target": {
            "id": 7,
            "status": status,
            "video_id": "already-there",
            "url": None,
            "thumbnail_path": "Attachments/thumb.png",
            "thumbnail_status": "failed",
            "caption_status": "serving",
        },
    }
    updates = []
    monkeypatch.setattr(
        "shared.release_store.update_target",
        lambda target_id, **fields: updates.append((target_id, fields)),
    )
    monkeypatch.setattr(publish_upload, "_reconcile_best_effort", lambda *_args: None)

    class _Execute:
        def execute(self):
            return {}

    class ForbiddenYouTube:
        def videos(self):
            raise AssertionError("ancillary recovery must never call videos.insert")

        def thumbnails(self):
            return self

        def set(self, *, videoId, media_body):
            assert videoId == "already-there"
            assert media_body == str(thumbnail)
            return _Execute()

    result = publish_upload._upload_one(ForbiddenYouTube(), item, tmp_path)

    assert result["video_id"] == "already-there"
    assert result["recovered"] is True
    assert (7, {"thumbnail_status": "processing"}) in updates
    assert (7, {"thumbnail_status": "set"}) in updates
    assert any(fields.get("status") == "uploaded" for _, fields in updates)


def test_thumbnail_failure_after_video_id_is_retryable_without_video_insert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    thumbnail = tmp_path / "thumb.png"
    thumbnail.write_bytes(b"png")
    target = {
        "id": 7,
        "status": "uploading",
        "video_id": "already-there",
        "thumbnail_path": "thumb.png",
        "thumbnail_status": "failed",
        "caption_status": "serving",
    }
    item = {
        "release": {
            "cut_id": "cut",
            "episode": "episode",
            "file_path": str(tmp_path / "episode" / "highlights" / "exports" / "cut.mp4"),
        },
        "target": target,
    }
    updates = []
    monkeypatch.setattr(
        "shared.release_store.update_target",
        lambda target_id, **fields: updates.append((target_id, fields)),
    )
    monkeypatch.setattr(publish_upload, "_reconcile_best_effort", lambda *_args: None)

    class _Execute:
        attempts = 0

        def execute(self):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("transient thumbnail failure")
            return {}

    execute = _Execute()

    class AncillaryOnlyYouTube:
        def videos(self):
            raise AssertionError("thumbnail retry must never call videos.insert")

        def thumbnails(self):
            return self

        def set(self, **_kwargs):
            return execute

    with pytest.raises(RuntimeError, match="transient thumbnail"):
        publish_upload._upload_one(AncillaryOnlyYouTube(), item, tmp_path)
    assert (7, {"thumbnail_status": "failed"}) in updates

    result = publish_upload._upload_one(AncillaryOnlyYouTube(), item, tmp_path)
    assert result["recovered"] is True
    assert execute.attempts == 2
    assert any(fields.get("status") == "uploaded" for _, fields in updates)


def test_existing_remote_caption_is_persisted_without_duplicate_insert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    item = {
        "release": {
            "cut_id": "cut",
            "episode": "episode",
            "file_path": str(tmp_path / "episode" / "highlights" / "exports" / "cut.mp4"),
        },
        "target": {
            "id": 7,
            "status": "uploading",
            "video_id": "already-there",
            "thumbnail_path": None,
            "thumbnail_status": "skipped",
            "caption_status": "processing",
            "caption_id": None,
        },
    }
    updates = []
    monkeypatch.setattr(
        "shared.release_store.update_target",
        lambda target_id, **fields: updates.append((target_id, fields)),
    )
    monkeypatch.setattr(publish_upload, "_reconcile_best_effort", lambda *_args: None)

    class _Request:
        def execute(self):
            return {
                "items": [
                    {
                        "id": "caption-existing",
                        "snippet": {"language": "zh-TW", "status": "serving"},
                    }
                ]
            }

    class AncillaryOnlyYouTube:
        def videos(self):
            raise AssertionError("caption recovery must never call videos.insert")

        def captions(self):
            return self

        def list(self, **_kwargs):
            return _Request()

        def insert(self, **_kwargs):
            raise AssertionError("an existing remote caption must not be inserted again")

    result = publish_upload._upload_one(AncillaryOnlyYouTube(), item, tmp_path)

    assert result["recovered"] is True
    assert (
        7,
        {"caption_id": "caption-existing", "caption_status": "serving"},
    ) in updates


def test_cc_failure_after_video_id_stays_uploaded_for_cc_only_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    episode = tmp_path / "episode"
    srt = episode / "highlights" / "srt" / "cut_tight_r001.srt"
    srt.parent.mkdir(parents=True)
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n字幕\n", encoding="utf-8")
    item = {
        "release": {
            "cut_id": "cut",
            "episode": "episode",
            "file_path": str(episode / "highlights" / "exports" / "cut.mp4"),
        },
        "target": {
            "id": 7,
            "status": "uploading",
            "video_id": "already-there",
            "thumbnail_path": None,
            "thumbnail_status": "skipped",
            "caption_status": "failed",
            "caption_id": None,
        },
    }
    updates = []
    monkeypatch.setattr(
        "shared.release_store.update_target",
        lambda target_id, **fields: updates.append((target_id, fields)),
    )
    monkeypatch.setattr(publish_upload, "_reconcile_best_effort", lambda *_args: None)

    class _Request:
        def __init__(self, payload=None, error=None):
            self.payload = payload
            self.error = error

        def execute(self):
            if self.error:
                raise self.error
            return self.payload

    class AncillaryOnlyYouTube:
        def videos(self):
            raise AssertionError("CC retry must never call videos.insert")

        def captions(self):
            return self

        def list(self, **_kwargs):
            return _Request({"items": []})

        def insert(self, **_kwargs):
            return _Request(error=RuntimeError("caption API unavailable"))

    result = publish_upload._upload_one(AncillaryOnlyYouTube(), item, tmp_path)

    assert result["recovered"] is True
    assert (7, {"caption_status": "failed"}) in updates
    assert any(
        fields.get("status") == "uploaded" and "CC 字幕上傳失敗" in fields.get("error", "")
        for _, fields in updates
    )


def test_expired_session_uses_needs_restart_state_not_automatic_retry():
    assert upload_failure_status(UploadSessionNeedsRestart("expired")) == "needs_restart"
    assert upload_failure_status(RuntimeError("network")) == "failed"


def test_uploading_without_persisted_session_requires_explicit_restart():
    """Crash before the first chunk returns must never create a second videos.insert."""

    assert target_requires_explicit_restart(
        {"status": "uploading", "upload_session_uri": None, "video_id": None}
    )
    assert not target_requires_explicit_restart(
        {
            "status": "uploading",
            "upload_session_uri": "https://upload.example/session",
            "video_id": None,
        }
    )
    assert not target_requires_explicit_restart(
        {"status": "approved", "upload_session_uri": None, "video_id": None}
    )


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
    # ADR-066 之後長片的 CC 走 _ensure_zh_tw_caption（會先查平台上有沒有、
    # 並把 caption_status 寫回 DB），不再直接呼叫 upload_captions。
    upload_cc = MagicMock()
    monkeypatch.setattr(publish_upload, "_ensure_zh_tw_caption", upload_cc)
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
