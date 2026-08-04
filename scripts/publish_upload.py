"""publish_upload — 發布線 Slice 3：YouTube uploader worker（Q2 桌機側）。

    # 核准 + 排程（Bridge 審核 board 未建前的 CLI 替代）
    python scripts/publish_upload.py --approve punch-L5 --episode "20260723 謝伯讓" \\
        --schedule "2026-08-10T20:00:00+08:00"

    # 上傳全部 approved 的 youtube targets（上傳成 private，publishAt 交給平台的鐘）
    python scripts/publish_upload.py --run [--dry-run]

    # 單支重試 / 強制重傳
    python scripts/publish_upload.py --run --cut punch-L5 --episode "..." [--force]

狀態機（ADR-055）：draft → approved → uploading → uploaded →（平台到點自動
公開）published。failed 可重試。防重複上傳：target 已有 video_id 就 skip
（--force 才重傳）；resumable session URI 逐 chunk 持久化——crash 後續傳
不重傳（YT 無天然 idempotency key，這兩道就是防護）。

上傳內容：檔案（releases.file_path）+ 標題/描述（Slice 2 回填）+ 縮圖
（vault-relative → 絕對路徑，thumbnails.set）+ CC 字幕（tight SRT，
captions.insert，zh-TW）。publishAt 有值就排程（upload 與 publish 時間
解耦——Q2 凍結）。

OAuth：`data/youtube_token.json`（scripts/youtube_auth.py 一次性 consent；
Slice 0 探針 #1124 已實測上傳/排程/無降權）。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger("publish_upload")

_DATA_DIR = Path(
    os.environ.get("NAKAMA_DATA_DIR", "") or Path(__file__).resolve().parent.parent / "data"
)
TOKEN_PATH = _DATA_DIR / "youtube_token.json"
PROGRESS_DIR = _DATA_DIR / "upload_progress"
CHUNK_MB = 8  # resumable chunk；小檔一發、1.35GB 約 170 chunks


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


def _load_yt():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    if not TOKEN_PATH.exists():
        raise SystemExit(f"找不到 {TOKEN_PATH}——先跑 python scripts/youtube_auth.py")
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH))
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_PATH.write_text(creds.to_json())
        else:
            raise SystemExit("token 無效且無法 refresh——重跑 scripts/youtube_auth.py")
    return build("youtube", "v3", credentials=creds)


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


def upload_captions(yt, video_id: str, episode_dir: Path, cid: str) -> None:
    """CC 字幕上傳（captions.insert；需 youtube.force-ssl scope）。"""
    from googleapiclient.http import MediaFileUpload as _MFU

    srts = sorted((episode_dir / "highlights/srt").glob(f"{cid}_tight_r*.srt"))
    if not srts:
        logger.warning("%s: 沒有 tight SRT——跳過 CC", cid)
        return
    yt.captions().insert(
        part="snippet",
        body={
            "snippet": {
                "videoId": video_id,
                "language": "zh-TW",
                "name": "中文（台灣）",
            }
        },
        media_body=_MFU(str(srts[-1]), mimetype="application/octet-stream"),
    ).execute()
    logger.info("%s: CC 字幕 OK（%s）", cid, srts[-1].name)


def cmd_cc_only(args) -> int:
    """補傳 CC（影片已上傳、CC 曾失敗——如 scope 補齊重新 auth 之後）。"""
    from shared.release_store import get_release, update_target

    rel = get_release(args.episode, args.cc_only)
    if rel is None:
        raise SystemExit(f"{args.cc_only} 未登錄")
    t = next((x for x in rel["targets"] if x["platform"] == "youtube"), None)
    if t is None or not t.get("video_id"):
        raise SystemExit("沒有 video_id——影片還沒上傳，走正常 --run")
    yt = _load_yt()
    upload_captions(yt, t["video_id"], Path(rel["file_path"]).parents[2], rel["cut_id"])
    update_target(t["id"], error=None)
    print(f"[OK] {rel['cut_id']} CC 已補傳（video {t['video_id']}）")
    return 0


def _upload_one(yt, item: dict, vault: Path) -> dict:
    """單支上傳：resumable video → 縮圖 → CC。逐步回寫 DB。"""
    from googleapiclient.http import MediaFileUpload

    from shared.release_store import update_target

    rel, t = item["release"], item["target"]
    cid, tid = rel["cut_id"], t["id"]
    video = Path(rel["file_path"])
    if not video.exists():
        raise SystemExit(f"{cid} 檔案不存在: {video}——重跑 publish_prep")

    body = build_insert_body(t, rel)
    update_target(tid, status="uploading")
    write_progress(rel["episode"], cid, 0.0, "開始上傳")
    logger.info("%s: 上傳中（%.1f MB）…", cid, video.stat().st_size / 1e6)

    media = MediaFileUpload(str(video), chunksize=CHUNK_MB * 1024 * 1024, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        # session URI 持久化：crash 後可查（googleapiclient 內建續傳同 process；
        # 跨 process 續傳 v2 再接——URI 先記下來，別讓它只活在記憶體）
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
    logger.info("%s: videos.insert OK — %s", cid, url)

    # 縮圖（長片；vault-relative → 絕對）
    if t.get("thumbnail_path"):
        thumb = vault / t["thumbnail_path"]
        if not thumb.exists():
            raise SystemExit(f"{cid} 縮圖不存在: {thumb}")
        yt.thumbnails().set(videoId=video_id, media_body=str(thumb)).execute()
        logger.info("%s: 縮圖 OK", cid)

    # CC 字幕（tight SRT，Q4b：長片不燒、上 CC）。episode 目錄從 file_path
    # 推導（exports/<cut>.mp4 的上上上層）——不硬編磁碟位置。
    # ⚠️ CC 失敗**不算整支失敗**——影片已在平台上，標 failed 會誤導重試
    # 重傳整支（2026-08-04 實測：token 缺 force-ssl scope 時 CC 403，但
    # 影片+縮圖+排程都成功）。CC 缺就記在 error 欄，--cc-only 補傳。
    cc_error = None
    try:
        upload_captions(yt, video_id, video.parents[2], cid)
    except Exception as exc:  # noqa: BLE001
        cc_error = f"CC 字幕上傳失敗（影片本體 OK，可 --cc-only 補傳）: {str(exc)[:300]}"
        logger.error("%s: %s", cid, cc_error)

    update_target(tid, status="uploaded", error=cc_error, upload_session_uri=None)
    return {
        "cut_id": cid,
        "video_id": video_id,
        "url": url,
        "scheduled": body["status"].get("publishAt"),
    }


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

    items = uploadable_targets(args.episode, args.cut)
    if args.cut and not items:
        raise SystemExit(f"{args.cut} 沒有可上傳的 youtube target（要先 --approve）")
    picked = []
    for it in items:
        if it["target"].get("video_id") and not args.force:
            logger.info(
                "%s: 已有 video_id（%s），skip——防重複上傳（--force 重傳）",
                it["release"]["cut_id"],
                it["target"]["video_id"],
            )
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
        yt = _load_yt()
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
            results.append(_upload_one(yt, it, vault))
        except (Exception, SystemExit) as exc:  # noqa: BLE001 — 單支失敗不擋整批，記進 DB
            update_target(it["target"]["id"], status="failed", error=str(exc)[:500])
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
    parser.add_argument("--episode", help="episode 資料夾名（--approve 必填；--run 可選過濾）")
    parser.add_argument("--cut", help="--run 時只處理這支")
    parser.add_argument("--force", action="store_true", help="已有 video_id 也重傳")
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
    if args.run:
        return cmd_run(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
