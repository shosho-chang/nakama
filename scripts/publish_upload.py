"""publish_upload — 發布線 Slice 3：YouTube uploader worker（Q2 桌機側）。

    # 核准 + 排程（Bridge 審核 board 未建前的 CLI 替代）
    python scripts/publish_upload.py --approve punch-L5 --episode "20260723 謝伯讓" \\
        --schedule "2026-08-10T20:00:00+08:00"

    # 上傳全部 approved 的 youtube targets（上傳成 private，publishAt 交給平台的鐘）
    python scripts/publish_upload.py --run [--dry-run]

    # 單支重試（已有 video_id 永不重傳）
    python scripts/publish_upload.py --run --cut punch-L5 --episode "..."

    # 同步已上傳影片的 processing / privacy 狀態（不建立新影片）
    python scripts/publish_upload.py --reconcile --cut punch-S1 --episode "..."

狀態機（ADR-055）：draft → approved → uploading → uploaded →（平台到點自動
公開）published。failed 可重試。防重複上傳：target 已有 video_id 就 skip
（不提供強制重傳）；resumable session URI 逐 chunk 持久化——crash 後續傳
不重傳（YT 無天然 idempotency key，這兩道就是防護）。

上傳內容：檔案（releases.file_path）+ 標題/描述（Slice 2 回填）+ 縮圖
（vault-relative → 絕對路徑，thumbnails.set）+ 長片 CC 字幕（tight SRT，
captions.insert，zh-TW）；Short 的字幕已燒入畫面，不另傳 CC。publishAt
有值就排程（upload 與 publish 時間解耦——Q2 凍結）。

OAuth：`data/youtube_token.json`（scripts/youtube_auth.py 一次性 consent；
Slice 0 探針 #1124 已實測上傳/排程/無降權）。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.usopp.youtube_credentials import (  # noqa: E402
    YouTubeCredentialError,
    load_youtube_client,
)
from shared.config import get_runtime_data_dir  # noqa: E402

logger = logging.getLogger("publish_upload")

_DATA_DIR = get_runtime_data_dir()
TOKEN_PATH = _DATA_DIR / "youtube_token.json"
PROGRESS_DIR = _DATA_DIR / "upload_progress"
CHUNK_MB = 8  # resumable chunk；小檔一發、1.35GB 約 170 chunks
YOUTUBE_PUBLISH_SCOPES = frozenset(
    {
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.force-ssl",
    }
)


class YouTubeCredentialPreflightError(RuntimeError):
    """OAuth token cannot safely complete video + caption publication."""


class UploadSessionNeedsRestart(RuntimeError):
    """The persisted YouTube resumable session expired and needs human restart."""


@dataclass(frozen=True, slots=True)
class ResumeProbe:
    state: Literal["active", "complete", "expired"]
    next_offset: int = 0
    response: dict | None = None

    @classmethod
    def active(cls, *, next_offset: int) -> ResumeProbe:
        return cls("active", next_offset=next_offset)

    @classmethod
    def complete(cls, response: dict) -> ResumeProbe:
        return cls("complete", response=response)

    @classmethod
    def expired(cls) -> ResumeProbe:
        return cls("expired")


class GoogleResumableUploadTransport:
    """Small HTTP seam for resuming a persisted YouTube upload session."""

    def __init__(self, credentials, *, session=None):
        if session is None:
            from google.auth.transport.requests import AuthorizedSession

            session = AuthorizedSession(credentials)
        self._session = session

    @staticmethod
    def _next_offset(response) -> int:
        uploaded = response.headers.get("Range")
        if not uploaded:
            return 0
        return int(uploaded.rsplit("-", 1)[1]) + 1

    def probe(self, session_uri: str, total_bytes: int) -> ResumeProbe:
        response = self._session.request(
            "PUT",
            session_uri,
            data=b"",
            headers={
                "Content-Length": "0",
                "Content-Range": f"bytes */{total_bytes}",
            },
            timeout=30,
        )
        if response.status_code in (404, 410):
            return ResumeProbe.expired()
        if response.status_code in (200, 201):
            return ResumeProbe.complete(response.json())
        if response.status_code == 308:
            return ResumeProbe.active(next_offset=self._next_offset(response))
        raise RuntimeError(f"YouTube resumable session probe 失敗: HTTP {response.status_code}")

    def upload(
        self,
        session_uri: str,
        video_path: Path,
        *,
        start_offset: int,
        chunk_bytes: int,
        on_progress,
    ) -> dict:
        total_bytes = video_path.stat().st_size
        offset = start_offset
        with video_path.open("rb") as stream:
            stream.seek(offset)
            while offset < total_bytes:
                payload = stream.read(min(chunk_bytes, total_bytes - offset))
                end = offset + len(payload) - 1
                response = self._session.request(
                    "PUT",
                    session_uri,
                    data=payload,
                    headers={
                        "Content-Length": str(len(payload)),
                        "Content-Range": f"bytes {offset}-{end}/{total_bytes}",
                    },
                    timeout=120,
                )
                if response.status_code in (404, 410):
                    raise UploadSessionNeedsRestart(
                        "YouTube resumable session 在續傳時過期；需要人工重新核准"
                    )
                if response.status_code in (200, 201):
                    on_progress(total_bytes, total_bytes)
                    return response.json()
                if response.status_code != 308:
                    raise RuntimeError(f"YouTube resumable chunk 失敗: HTTP {response.status_code}")
                next_offset = self._next_offset(response)
                if next_offset <= offset:
                    raise RuntimeError("YouTube resumable session offset 沒有前進")
                offset = next_offset
                stream.seek(offset)
                on_progress(offset, total_bytes)
        raise RuntimeError("YouTube resumable upload 未回傳完成 response")


def resume_video_upload(
    transport,
    session_uri: str,
    video_path: Path,
    *,
    chunk_bytes: int,
    on_progress,
) -> dict:
    """Resume one persisted session from YouTube's authoritative remote offset."""

    total_bytes = video_path.stat().st_size
    probe = transport.probe(session_uri, total_bytes)
    if probe.state == "expired":
        raise UploadSessionNeedsRestart("YouTube resumable session 已過期；需要人工重新核准上傳")
    if probe.state == "complete":
        response = probe.response or {}
    else:
        if not 0 <= probe.next_offset <= total_bytes:
            raise RuntimeError("YouTube resumable session 回傳不合法 offset")
        response = transport.upload(
            session_uri,
            video_path,
            start_offset=probe.next_offset,
            chunk_bytes=chunk_bytes,
            on_progress=on_progress,
        )
    if not response.get("id"):
        raise RuntimeError("YouTube resumable upload 完成但缺少 video id")
    return response


def upload_failure_status(exc: BaseException) -> str:
    """Map failures without making an expired session silently restartable."""

    return "needs_restart" if isinstance(exc, UploadSessionNeedsRestart) else "failed"


def target_requires_explicit_restart(target: dict) -> bool:
    """Detect a crash window where replaying could create a duplicate YouTube video.

    ``googleapiclient`` only exposes ``resumable_uri`` after the first ``next_chunk``
    call returns.  If the worker dies during that call, YouTube may already own a
    resumable session (or even the uploaded video) while the database only says
    ``uploading``.  Without a persisted URI or video id there is no safe automatic
    operation, so the operator must inspect Studio and explicitly approve a restart.
    """

    return bool(
        target.get("status") == "uploading"
        and not target.get("upload_session_uri")
        and not target.get("video_id")
    )


def assert_youtube_publish_credentials(credentials) -> None:
    """Fail before API construction unless scopes and offline refresh are complete."""

    granted = set(
        getattr(credentials, "granted_scopes", None) or getattr(credentials, "scopes", None) or ()
    )
    missing = sorted(YOUTUBE_PUBLISH_SCOPES - granted)
    if missing:
        raise YouTubeCredentialPreflightError(
            "YouTube OAuth token 缺少 scope: " + ", ".join(missing)
        )
    if not getattr(credentials, "refresh_token", None):
        raise YouTubeCredentialPreflightError(
            "YouTube OAuth token 沒有 refresh_token，無法安全完成長時間上傳"
        )


class YouTubeVideoNotFoundError(RuntimeError):
    """The stored video_id no longer resolves; never clear it or auto-replace it."""


@dataclass(frozen=True, slots=True)
class YouTubeVideoObservation:
    """One read-only platform observation, safe to pass to orchestration code."""

    outcome: Literal["published", "failed", "pending"]
    evidence_category: str
    certain: bool
    error: str | None = None
    permalink: str | None = None
    privacy_status: str | None = None
    upload_status: str | None = None
    processing_status: str | None = None
    publish_at: str | None = None


def _progress_file(episode: str, cut_id: str) -> Path:
    safe = f"{episode}_{cut_id}".replace("/", "_").replace("\\", "_")
    return PROGRESS_DIR / f"{safe}.json"


def write_progress(episode: str, cut_id: str, pct: float, note: str = "") -> None:
    """上傳進度落檔——Bridge 審核頁的 /status endpoint 讀它畫進度條。"""
    from datetime import datetime as _dt

    PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
    _progress_file(episode, cut_id).write_text(
        json.dumps({"pct": round(pct, 1), "note": note, "at": _dt.now(timezone.utc).isoformat()}),
        encoding="utf-8",
    )


def _credentials_from_token():
    """讀回 token 檔裡的憑證；讀不動就回 None，讓載入器自己去報它的錯。"""
    try:
        from google.oauth2.credentials import Credentials

        return Credentials.from_authorized_user_file(str(TOKEN_PATH))
    except Exception:
        return None


def _load_yt(
    *,
    credentials_loader=None,
    request_factory=None,
    service_builder=None,
    return_credentials: bool = False,
):
    """上傳用的 YouTube client。

    憑證的 refresh 與原子寫回交給 Stage 6 共用的 `load_youtube_client`（observer
    走的是同一支），這裡多做一件它不做的事：驗 scope。缺 force-ssl 的 token 影片
    傳得上去但 CC 會 403——那是白傳一支才發現。

    給了注入點時走可測試的內嵌路徑，並在建立 client **之前**擋下 scope 問題。
    """

    if credentials_loader or request_factory or service_builder:
        if credentials_loader is None:
            from google.oauth2.credentials import Credentials

            credentials_loader = Credentials.from_authorized_user_file
        if request_factory is None:
            from google.auth.transport.requests import Request

            request_factory = Request
        if service_builder is None:
            from googleapiclient.discovery import build

            service_builder = build
        if not TOKEN_PATH.exists():
            raise SystemExit(f"找不到 {TOKEN_PATH}——先跑 python scripts/youtube_auth.py")
        creds = credentials_loader(str(TOKEN_PATH))
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(request_factory())
                TOKEN_PATH.write_text(creds.to_json())
            else:
                raise SystemExit("token 無效且無法 refresh——重跑 scripts/youtube_auth.py")
        try:
            assert_youtube_publish_credentials(creds)
        except YouTubeCredentialPreflightError as exc:
            raise SystemExit(
                f"OAuth preflight 失敗：{exc}——重跑 scripts/youtube_auth.py"
            ) from exc
        service = service_builder("youtube", "v3", credentials=creds)
        return (service, creds) if return_credentials else service

    service = _load_stage6_youtube_client()
    credentials = _credentials_from_token()
    if credentials is not None:
        try:
            assert_youtube_publish_credentials(credentials)
        except YouTubeCredentialPreflightError as exc:
            raise SystemExit(
                f"OAuth preflight 失敗：{exc}——重跑 scripts/youtube_auth.py"
            ) from exc
    if not return_credentials:
        return service
    if credentials is None:
        raise SystemExit(f"讀不到 {TOKEN_PATH} 的憑證——重跑 scripts/youtube_auth.py")
    return service, credentials


def _load_stage6_youtube_client():
    try:
        return load_youtube_client(TOKEN_PATH)
    except YouTubeCredentialError as exc:
        raise SystemExit(str(exc)) from None


def load_youtube_observer():
    """Build a read-only observer client with the shared credential lifecycle."""

    return _load_stage6_youtube_client()


def to_utc_iso(ts: str) -> str:
    """publish_at → RFC3339 UTC（YT API 要求）。naive 時間拒收——排程是硬承諾，
    時區不能用猜的。"""
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        raise ValueError(f"publish_at 缺時區: {ts!r}（要 +08:00 或 Z）")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def uploadable_targets(episode: str | None = None, cut: str | None = None) -> list[dict]:
    """approved 且未上傳的 youtube targets（含 release 檔案資訊）。"""
    from shared.release_store import list_releases

    out = []
    for rel in list_releases(episode):
        if cut and rel["cut_id"] != cut:
            continue
        from shared.release_store import get_release

        full = get_release(rel["episode"], rel["cut_id"])
        for t in full["targets"]:
            if t["platform"] != "youtube":
                continue
            if t["status"] not in ("approved", "failed", "uploading"):
                continue
            out.append({"release": full, "target": t})
    return out


def observe_youtube_video(yt, video_id: str) -> YouTubeVideoObservation:
    """Read one YouTube video once; never insert, upload, or mutate local state."""

    video_id = video_id.strip()
    if not video_id:
        raise ValueError("YouTube video_id must be non-empty")
    response = (
        yt.videos()
        .list(
            part="status,processingDetails",
            id=video_id,
        )
        .execute()
    )
    items = response.get("items", []) if isinstance(response, dict) else []
    if not items:
        raise YouTubeVideoNotFoundError(
            f"YouTube 找不到 video_id={video_id}；保留既有 ID，不會自動重傳"
        )

    item = items[0]
    status = item.get("status") or {}
    processing = item.get("processingDetails") or {}
    privacy = status.get("privacyStatus")
    upload_status = status.get("uploadStatus")
    processing_status = processing.get("processingStatus")
    publish_at = status.get("publishAt")
    failed = upload_status in {"failed", "rejected", "deleted"} or processing_status == "terminated"
    observation_fields = {
        "privacy_status": privacy,
        "upload_status": upload_status,
        "processing_status": processing_status,
        "publish_at": publish_at,
    }
    if failed and privacy == "public":
        return YouTubeVideoObservation(
            "pending",
            "unknown",
            False,
            **observation_fields,
        )
    if failed:
        reasons = [
            status.get("failureReason"),
            status.get("rejectionReason"),
            processing.get("processingFailureReason"),
        ]
        reason = "; ".join(str(value) for value in reasons if value) or (
            f"uploadStatus={upload_status}, processingStatus={processing_status}"
        )
        return YouTubeVideoObservation(
            "failed",
            "processing_failed",
            True,
            f"YouTube processing failed/rejected: {reason}",
            **observation_fields,
        )
    if privacy == "public":
        return YouTubeVideoObservation("published", "public", True, **observation_fields)
    if processing_status in {"processing", "queued"}:
        return YouTubeVideoObservation("pending", "processing", True, **observation_fields)
    if privacy in {"private", "unlisted"}:
        return YouTubeVideoObservation("pending", "private", True, **observation_fields)
    return YouTubeVideoObservation("pending", "unknown", False, **observation_fields)


def reconcile_target(yt, release: dict, target: dict) -> dict:
    """Synchronise one stored YouTube target without creating a replacement."""
    from shared.release_store import update_target

    video_id = target.get("video_id")
    if not video_id:
        raise ValueError(f"{release['cut_id']} 沒有 video_id，不能 reconciliation")
    observation = observe_youtube_video(yt, str(video_id))
    local_status = {
        "published": "published",
        "failed": "failed",
        "pending": "uploaded",
    }[observation.outcome]
    error = observation.error

    update_target(target["id"], status=local_status, error=error)
    return {
        "cut_id": release["cut_id"],
        "video_id": video_id,
        "status": local_status,
        "privacy_status": observation.privacy_status,
        "upload_status": observation.upload_status,
        "processing_status": observation.processing_status,
        "publish_at": observation.publish_at,
        "evidence_category": observation.evidence_category,
        "error": error,
    }


def cmd_reconcile(args) -> int:
    """Reconcile exactly one existing target; no insert/upload API is reachable here."""
    from shared.release_store import get_release

    rel = get_release(args.episode, args.cut)
    if rel is None:
        raise SystemExit(f"{args.cut} 未登錄")
    target = next((item for item in rel["targets"] if item["platform"] == "youtube"), None)
    if target is None:
        raise SystemExit("youtube target 不存在")
    if not target.get("video_id"):
        raise SystemExit("target 沒有 video_id——先完成正常上傳")
    yt = _load_yt()
    try:
        output = reconcile_target(yt, rel, target)
    except YouTubeVideoNotFoundError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps({"reconciled": [output]}, ensure_ascii=False, indent=2))
    return 0


def build_insert_body(target: dict, release: dict) -> dict:
    """videos.insert body。title/description 必須已回填（Slice 2）——缺了 fail
    loud，不拿工作代號充當發布標題。"""
    if not target.get("title") or not target.get("description"):
        raise ValueError(
            f"{release['cut_id']} 的 title/description 未回填——先跑 publish_description"
        )
    body = {
        "snippet": {
            "title": target["title"],
            "description": target["description"],
            "categoryId": "22",
            "defaultLanguage": "zh-TW",
            "defaultAudioLanguage": "zh-TW",
        },
        "status": {
            "privacyStatus": "private",
            "selfDeclaredMadeForKids": False,
        },
    }
    if target.get("publish_at"):
        body["status"]["publishAt"] = to_utc_iso(target["publish_at"])
    return body


def upload_captions(yt, video_id: str, episode_dir: Path, cid: str) -> dict | None:
    """CC 字幕上傳（captions.insert；需 youtube.force-ssl scope）。"""
    from googleapiclient.http import MediaFileUpload as _MFU

    from agents.usopp.publish_timeline import release_subtitle
    from shared.tight_srt import latest_tight_srt

    # CC 必須是**成品那一份**。tight SRT 是 ADR-065 製作線的殘留：punch-L04 的只有
    # 260 秒舊剪輯，成品卻是 492 秒，貼上去等於整支片的字幕都對不上畫面。
    # 沒有 Release 對應表的舊集數仍沿用 shared.tight_srt 的挑選規則。
    srt = release_subtitle(episode_dir, cid) or latest_tight_srt(episode_dir, cid)
    if srt is None:
        logger.warning("%s: 找不到字幕——跳過 CC", cid)
        return None
    response = (
        yt.captions()
        .insert(
            part="snippet",
            body={
                "snippet": {
                    "videoId": video_id,
                    "language": "zh-TW",
                    "name": "中文（台灣）",
                }
            },
            media_body=_MFU(str(srt), mimetype="application/octet-stream"),
        )
        .execute()
    )
    logger.info("%s: CC 字幕 OK（%s）", cid, srt.name)
    return response


def _remote_zh_tw_caption(yt, video_id: str) -> dict | None:
    """Return an already-created zh-TW caption before attempting another insert."""

    response = yt.captions().list(part="snippet", videoId=video_id).execute()
    return next(
        (
            item
            for item in response.get("items", [])
            if item.get("snippet", {}).get("language") == "zh-TW"
        ),
        None,
    )


def _ensure_zh_tw_caption(yt, release: dict, target: dict) -> None:
    """Idempotently submit CC, recovering a crash after captions.insert."""

    from shared.release_store import update_target

    if target.get("caption_status") == "serving" or target.get("caption_id"):
        return
    target_id = target["id"]
    update_target(target_id, caption_status="processing")
    remote = _remote_zh_tw_caption(yt, target["video_id"])
    if remote is not None:
        remote_status = remote.get("snippet", {}).get("status") or remote.get("snippet", {}).get(
            "syncStatus"
        )
        caption_status = (
            "serving"
            if remote_status in {"serving", "succeeded"}
            else "failed"
            if remote_status == "failed"
            else "processing"
        )
        update_target(
            target_id,
            caption_id=remote.get("id"),
            caption_status=caption_status,
        )
        target.update(caption_id=remote.get("id"), caption_status=caption_status)
        if caption_status == "failed":
            raise RuntimeError("YouTube 已有 zh-TW caption，但平台處理狀態為 failed")
        return

    episode_dir = Path(release["file_path"]).parents[2]
    response = upload_captions(yt, target["video_id"], episode_dir, release["cut_id"])
    if response is None or not response.get("id"):
        raise FileNotFoundError(f"{release['cut_id']} 沒有可上傳的 tight SRT")
    update_target(
        target_id,
        caption_id=response["id"],
        caption_status="processing",
    )
    target.update(caption_id=response["id"], caption_status="processing")


def _finish_ancillary_steps(yt, release: dict, target: dict, vault: Path) -> dict:
    """Finish thumbnail/CC for an existing video without creating another video."""

    from shared.release_store import update_target

    target_id = target["id"]
    video_id = target["video_id"]
    if target.get("thumbnail_path"):
        if target.get("thumbnail_status") != "set":
            update_target(target_id, thumbnail_status="processing")
            thumbnail = vault / target["thumbnail_path"]
            if not thumbnail.exists():
                update_target(target_id, thumbnail_status="failed")
                raise FileNotFoundError(f"{release['cut_id']} 縮圖不存在: {thumbnail}")
            try:
                yt.thumbnails().set(videoId=video_id, media_body=str(thumbnail)).execute()
            except Exception:
                update_target(target_id, thumbnail_status="failed")
                raise
            update_target(target_id, thumbnail_status="set")
            target["thumbnail_status"] = "set"
            logger.info("%s: 縮圖 OK", release["cut_id"])
    elif target.get("thumbnail_status") != "skipped":
        update_target(target_id, thumbnail_status="skipped")
        target["thumbnail_status"] = "skipped"

    # ADR-055 Q4b：長片不燒字幕、改上 sidecar CC；Short 已經把字燒進畫面，
    # 再上一份 CC 只會在手機上疊成兩層字。
    cc_error = None
    if release.get("format") != "short":
        try:
            _ensure_zh_tw_caption(yt, release, target)
        except Exception as exc:  # noqa: BLE001 - video exists; expose CC-only recovery
            cc_error = f"CC 字幕上傳失敗（影片本體 OK，可 --cc-only 補傳）: {str(exc)[:300]}"
            update_target(target_id, caption_status="failed")
            logger.error("%s: %s", release["cut_id"], cc_error)
    else:
        logger.info("%s: Short 字幕已燒入畫面——不另上 CC", release["cut_id"])

    update_target(
        target_id,
        status="uploaded",
        error=cc_error,
        upload_session_uri=None,
    )
    _reconcile_best_effort(yt, target)
    return {
        "cut_id": release["cut_id"],
        "video_id": video_id,
        "url": target.get("url") or f"https://www.youtube.com/watch?v={video_id}",
    }


def _reconcile_best_effort(yt, target: dict) -> None:
    """Persist platform truth without turning a completed upload into a reupload."""

    from agents.usopp.youtube_publish import reconcile_and_persist
    from shared.release_store import update_target

    try:
        reconcile_and_persist(yt, target)
    except Exception as exc:  # noqa: BLE001 - upload remains valid; expose query failure
        update_target(
            target["id"],
            reconciliation_error=f"YouTube reconciliation failed: {str(exc)[:400]}",
            last_reconciled_at=datetime.now(timezone.utc).isoformat(),
        )


def cmd_cc_only(args) -> int:
    """補傳 CC（影片已上傳、CC 曾失敗——如 scope 補齊重新 auth 之後）。"""
    from shared.release_store import get_release, update_target

    rel = get_release(args.episode, args.cc_only)
    if rel is None:
        raise SystemExit(f"{args.cc_only} 未登錄")
    if rel["format"] == "short":
        raise SystemExit("Short 字幕已燒入畫面，不可使用 --cc-only 重複上傳字幕")
    t = next((x for x in rel["targets"] if x["platform"] == "youtube"), None)
    if t is None or not t.get("video_id"):
        raise SystemExit("沒有 video_id——影片還沒上傳，走正常 --run")
    update_target(t["id"], caption_status="processing")
    t["caption_status"] = "processing"
    try:
        yt = _load_yt()
        _ensure_zh_tw_caption(yt, rel, t)
    except (Exception, SystemExit) as exc:
        update_target(
            t["id"],
            caption_status="failed",
            error=f"CC 字幕上傳失敗: {str(exc)[:400]}",
        )
        raise
    update_target(t["id"], error=None)
    _reconcile_best_effort(yt, t)
    print(f"[OK] {rel['cut_id']} CC 已補傳（video {t['video_id']}）")
    return 0


def _upload_one(yt, item: dict, vault: Path, *, resume_transport=None) -> dict:
    """單支上傳：resumable video → 縮圖 → CC。逐步回寫 DB。"""
    from shared.release_store import update_target

    rel, t = item["release"], item["target"]
    cid, tid = rel["cut_id"], t["id"]
    if t.get("video_id"):
        logger.info("%s: 已有 video_id（%s），只恢復縮圖/CC", cid, t["video_id"])
        result = _finish_ancillary_steps(yt, rel, t, vault)
        result["recovered"] = True
        return result
    video = Path(rel["file_path"])
    if not video.exists():
        raise SystemExit(f"{cid} 檔案不存在: {video}——重跑 publish_prep")

    body = build_insert_body(t, rel)
    update_target(tid, status="uploading")
    write_progress(rel["episode"], cid, 0.0, "開始上傳")
    logger.info("%s: 上傳中（%.1f MB）…", cid, video.stat().st_size / 1e6)

    if t.get("upload_session_uri"):
        if resume_transport is None:
            raise RuntimeError("發現 upload_session_uri，但 resumable transport 未設定")

        def resumed_progress(sent: int, total: int) -> None:
            pct = sent / total * 100 if total else 0.0
            write_progress(rel["episode"], cid, pct, "影片續傳中")
            logger.info("%s: %.0f%%（續傳）", cid, pct)

        try:
            resp = resume_video_upload(
                resume_transport,
                t["upload_session_uri"],
                video,
                chunk_bytes=CHUNK_MB * 1024 * 1024,
                on_progress=resumed_progress,
            )
        except UploadSessionNeedsRestart:
            update_target(tid, upload_session_uri=None)
            raise
    else:
        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(str(video), chunksize=CHUNK_MB * 1024 * 1024, resumable=True)
        req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
        resp = None
        while resp is None:
            status, resp = req.next_chunk()
            uri = getattr(req, "resumable_uri", None)
            if uri and not t.get("upload_session_uri"):
                update_target(tid, upload_session_uri=uri)
                t["upload_session_uri"] = uri
            if status:
                pct = status.progress() * 100
                write_progress(rel["episode"], cid, pct, "影片上傳中")
                logger.info("%s: %.0f%%", cid, pct)
    write_progress(rel["episode"], cid, 100.0, "影片完成，處理縮圖與字幕")

    video_id = resp["id"]
    url = f"https://www.youtube.com/watch?v={video_id}"
    update_target(tid, video_id=video_id, url=url)
    t.update(video_id=video_id, url=url)
    logger.info("%s: videos.insert OK — %s", cid, url)
    result = _finish_ancillary_steps(yt, rel, t, vault)
    result["scheduled"] = body["status"].get("publishAt")
    return result


def cmd_approve(args) -> int:
    from shared.release_store import get_release, update_target

    rel = get_release(args.episode, args.approve)
    if rel is None:
        raise SystemExit(f"{args.approve} 未登錄——先跑 publish_prep")
    t = next((x for x in rel["targets"] if x["platform"] == "youtube"), None)
    if t is None:
        raise SystemExit("youtube target 不存在")
    fields: dict = {"status": "approved"}
    if args.schedule:
        to_utc_iso(args.schedule)  # 先驗格式，錯就整個不寫
        fields["publish_at"] = args.schedule
    update_target(t["id"], **fields)
    print(
        f"[OK] {args.approve} → approved"
        + (f"，排程 {args.schedule}" if args.schedule else "（未排程＝上傳後留 private）")
    )
    return 0


def cmd_run(args) -> int:
    from shared.config import get_vault_path
    from shared.release_store import update_target

    if args.force:
        raise SystemExit("--force 重傳已停用：已有 video_id 不得重傳；session 過期需重新人工核准")
    items = uploadable_targets(args.episode, args.cut)
    if args.cut and not items:
        # 已上傳 target 不在 uploadable_targets 狀態集合；精確重跑仍要明確回報
        # duplicate guard，而不是用「沒有可上傳 target」讓操作者誤以為失敗。
        from shared.release_store import get_release

        rel = get_release(args.episode, args.cut) if args.episode else None
        target = (
            next((item for item in rel["targets"] if item["platform"] == "youtube"), None)
            if rel
            else None
        )
        if target and target.get("video_id") and not args.force:
            print(f"{args.cut}: 已有 video_id（{target['video_id']}），skip——防重複上傳")
            return 0
        raise SystemExit(f"{args.cut} 沒有可上傳的 youtube target（要先 --approve）")
    picked = []
    for it in items:
        if target_requires_explicit_restart(it["target"]):
            message = (
                "上次上傳在 resumable session URI 落盤前中斷；為避免建立重複影片，"
                "請先到 YouTube Studio 確認，再重新人工核准"
            )
            update_target(it["target"]["id"], status="needs_restart", error=message)
            write_progress(it["release"]["episode"], it["release"]["cut_id"], 0.0, message)
            logger.error("%s: %s", it["release"]["cut_id"], message)
            continue
        picked.append(it)
    if not picked:
        print("沒有待上傳的 target")
        return 0
    if args.dry_run:
        for it in picked:
            body = build_insert_body(it["target"], it["release"])
            print(f"--- {it['release']['cut_id']} ---")
            print(json.dumps(body, ensure_ascii=False, indent=1))
        print(f"\n[dry-run] {len(picked)} 支待上傳，未執行")
        return 0

    try:
        yt, credentials = _load_yt(return_credentials=True)
        resume_transport = GoogleResumableUploadTransport(credentials)
        vault = get_vault_path()
    except SystemExit as exc:
        # token/環境壞掉時把所有 picked 標 failed——被 subprocess 吞掉的死法
        # 在 UI 上必須看得到（2026-08-04 修修按了上傳「後台沒反應」的根因）
        for it in picked:
            update_target(it["target"]["id"], status="failed", error=str(exc)[:500])
        raise
    results = []
    for it in picked:
        try:
            results.append(_upload_one(yt, it, vault, resume_transport=resume_transport))
        except (Exception, SystemExit) as exc:  # noqa: BLE001 — 單支失敗不擋整批，記進 DB
            update_target(
                it["target"]["id"],
                status=upload_failure_status(exc),
                error=str(exc)[:500],
            )
            logger.error("%s: 上傳失敗 — %s", it["release"]["cut_id"], exc)
    print(json.dumps({"uploaded": results}, ensure_ascii=False, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="發布線 Slice 3：YouTube uploader worker")
    parser.add_argument("--approve", metavar="CUT", help="核准這支（CLI 替代審核 board）")
    parser.add_argument("--cc-only", metavar="CUT", help="只補傳 CC 字幕（影片已上傳）")
    parser.add_argument(
        "--schedule", help="publishAt（ISO8601 含時區，如 2026-08-10T20:00:00+08:00）"
    )
    parser.add_argument("--run", action="store_true", help="上傳全部 approved targets")
    parser.add_argument(
        "--reconcile",
        action="store_true",
        help="以 videos.list 同步一支已有 video_id 的 target（不建立影片）",
    )
    parser.add_argument("--episode", help="episode 資料夾名（--approve 必填；--run 可選過濾）")
    parser.add_argument("--cut", help="--run 時只處理這支")
    parser.add_argument("--force", action="store_true", help="已停用；保留參數只為明確拒絕舊命令")
    parser.add_argument("--dry-run", action="store_true", help="只印 insert body，不上傳")
    args = parser.parse_args(argv)

    if args.approve:
        if not args.episode:
            raise SystemExit("--approve 需要 --episode")
        return cmd_approve(args)
    if args.cc_only:
        if not args.episode:
            raise SystemExit("--cc-only 需要 --episode")
        return cmd_cc_only(args)
    if args.reconcile:
        if not args.episode or not args.cut:
            raise SystemExit("--reconcile 需要 --episode 與 --cut")
        return cmd_reconcile(args)
    if args.run:
        return cmd_run(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
