"""publish_upload 純函數測試（真上傳靠修修 approve 後首跑 UAT）。"""

import sys
from pathlib import Path

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
    assert_youtube_publish_credentials,
    build_insert_body,
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
    credentials = _Credentials(
        ["https://www.googleapis.com/auth/youtube.upload"]
    )

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
