"""Slice 0 探針 — 在寫任何發布層 code 之前，先確認「貨出得去」。

設計依據：`docs/plans/2026-07-26-video-publishing-plan.md` §6。
2026-04 那套發布機器的死因不是 schema 選錯，是先蓋了工廠卻沒先確認貨出得去。

驗四件事：
1. OAuth 拿得到 upload 權限（`scripts/youtube_auth.py` 先跑）
2. `videos.insert` 上傳成 private 會成功
3. `status.publishAt` 排程設得進去
4. **到點真的變 public，而且沒被標「third party tool failed our verification」降權**
   （§1.1：該句不在任何官方文件內，觸發條件查不到 → 只能實測）

⚠️ **這支 script 會在修修的真實頻道上讓一支影片公開**（哪怕只有幾分鐘）。
預設用合成測試片（ffmpeg 產生的彩條 + 靜音），不是真內容；`--keep` 沒給的話
驗完立即刪除。

用法：

    # 產生測試素材並上傳，排 10 分鐘後公開，然後輪詢到公開為止
    python scripts/youtube_publish_probe.py --delay-min 10

    # 用自己的影片（例如真的剪好的短片）
    python scripts/youtube_publish_probe.py --video path/to/short.mp4

    # 只上傳不排程（更保守：留 private，人工去後台看有沒有被標降權）
    python scripts/youtube_publish_probe.py --no-schedule

    # 驗完保留影片不刪
    python scripts/youtube_publish_probe.py --keep
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from google.auth.transport.requests import Request  # noqa: E402
from google.oauth2.credentials import Credentials  # noqa: E402
from googleapiclient.discovery import build  # noqa: E402
from googleapiclient.http import MediaFileUpload  # noqa: E402

_DATA_DIR = Path(os.environ.get("NAKAMA_DATA_DIR", "data"))
_TOKEN_PATH = _DATA_DIR / "youtube_token.json"
_REPORT_PATH = _DATA_DIR / "youtube_probe_report.json"

# 降權訊號：§1.1 的使用者回報。官方文件查無此句，只能字串比對實測結果。
_DEMOTION_MARKERS = ("third party", "failed our verification", "limited access")


def _load_creds() -> Credentials:
    if not _TOKEN_PATH.exists():
        raise SystemExit(f"[ERROR] 找不到 {_TOKEN_PATH}——先跑 python scripts/youtube_auth.py")
    creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH))
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _TOKEN_PATH.write_text(creds.to_json())
        else:
            raise SystemExit("[ERROR] token 無效且無法 refresh——重跑 scripts/youtube_auth.py")
    return creds


def _make_test_clip(dest: Path, seconds: int = 8) -> Path:
    """ffmpeg 合成 1080x1920 彩條 + 靜音——直式，貼近 Shorts 的實際形狀。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=1080x1920:rate=30:duration={seconds}",
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=channel_layout=stereo:sample_rate=48000:d={seconds}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "28",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(dest),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return dest


def _fetch_status(yt, video_id: str) -> dict:
    resp = yt.videos().list(part="status,processingDetails,suggestions", id=video_id).execute()
    items = resp.get("items") or []
    return items[0] if items else {}


def _demotion_signals(status_block: dict) -> list[str]:
    """把整個 status/suggestions 區塊攤平成字串找降權關鍵詞。"""
    blob = json.dumps(status_block, ensure_ascii=False).lower()
    return [m for m in _DEMOTION_MARKERS if m in blob]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="YouTube 發布線 Slice 0 探針")
    ap.add_argument("--video", help="要上傳的影片；不給就用 ffmpeg 合成測試片")
    ap.add_argument("--delay-min", type=int, default=10, help="publishAt 距現在幾分鐘（預設 10）")
    ap.add_argument("--no-schedule", action="store_true", help="只上傳 private，不設 publishAt")
    ap.add_argument("--keep", action="store_true", help="驗完保留影片，不刪除")
    ap.add_argument("--poll-sec", type=int, default=60, help="輪詢間隔秒數（預設 60）")
    args = ap.parse_args(argv)

    creds = _load_creds()
    yt = build("youtube", "v3", credentials=creds)

    if args.video:
        video_path = Path(args.video)
        if not video_path.exists():
            raise SystemExit(f"[ERROR] 影片不存在: {video_path}")
        synthetic = False
    else:
        video_path = _make_test_clip(_DATA_DIR / "probe" / "slice0_testclip.mp4")
        synthetic = True
        print(f"[1/5] 已合成測試片: {video_path}")

    publish_at = None
    status = {"privacyStatus": "private", "selfDeclaredMadeForKids": False}
    if not args.no_schedule:
        publish_at = datetime.now(timezone.utc) + timedelta(minutes=args.delay_min)
        status["publishAt"] = publish_at.isoformat().replace("+00:00", "Z")

    title = f"[nakama probe] Slice 0 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    body = {
        "snippet": {
            "title": title,
            "description": "發布線 Slice 0 探針，驗證上傳與排程。稍後自動刪除。",
            "categoryId": "22",
        },
        "status": status,
    }

    print(
        f"[2/5] 上傳中（private{'，排程 ' + status['publishAt'] if publish_at else '，不排程'}）..."
    )
    req = yt.videos().insert(
        part="snippet,status",
        body=body,
        media_body=MediaFileUpload(str(video_path), chunksize=-1, resumable=True),
    )
    resp = req.execute()
    video_id = resp["id"]
    url = f"https://www.youtube.com/watch?v={video_id}"
    print(f"      [OK] videos.insert 成功 — {url}")

    report = {
        "probe_at": datetime.now(timezone.utc).isoformat(),
        "video_id": video_id,
        "url": url,
        "synthetic_clip": synthetic,
        "scheduled_publish_at": status.get("publishAt"),
        "upload_ok": True,
    }

    print("[3/5] 讀回狀態，檢查排程是否吃進去 + 有無降權訊號...")
    time.sleep(5)
    st = _fetch_status(yt, video_id)
    report["status_after_upload"] = st.get("status")
    report["upload_status"] = (st.get("status") or {}).get("uploadStatus")
    report["schedule_accepted"] = (
        bool((st.get("status") or {}).get("publishAt")) if publish_at else None
    )
    signals = _demotion_signals(st)
    report["demotion_signals_after_upload"] = signals
    print(f"      uploadStatus={report['upload_status']}")
    print(f"      scheduleAccepted={report['schedule_accepted']}")
    print(f"      降權訊號: {signals or '無'}")

    if publish_at:
        print(f"[4/5] 輪詢到 {status['publishAt']} 之後，確認真的變 public...")
        deadline = publish_at + timedelta(minutes=15)
        went_public = False
        while datetime.now(timezone.utc) < deadline:
            time.sleep(args.poll_sec)
            st = _fetch_status(yt, video_id)
            privacy = (st.get("status") or {}).get("privacyStatus")
            now = datetime.now(timezone.utc).strftime("%H:%M:%S")
            print(f"      {now} privacyStatus={privacy}")
            if privacy == "public":
                went_public = True
                break
        report["went_public"] = went_public
        report["public_at_check"] = datetime.now(timezone.utc).isoformat()
        signals = _demotion_signals(st)
        report["demotion_signals_after_publish"] = signals
        report["status_after_publish"] = st.get("status")
        print(f"      {'[OK] 準時變 public' if went_public else '[FAIL] 逾時仍未公開'}")
        print(f"      降權訊號: {signals or '無'}")
    else:
        print("[4/5] --no-schedule：跳過公開驗證（人工去 YouTube Studio 看有無降權標記）")
        report["went_public"] = None

    if args.keep or args.no_schedule:
        print(f"[5/5] 保留影片（--keep / --no-schedule）：{url}")
        report["deleted"] = False
    else:
        yt.videos().delete(id=video_id).execute()
        print(f"[5/5] 已刪除探針影片 {video_id}")
        report["deleted"] = True

    _REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"報告：{_REPORT_PATH}")

    verdict_ok = report["upload_ok"] and not report.get("demotion_signals_after_upload")
    if publish_at:
        verdict_ok = (
            verdict_ok
            and report.get("went_public")
            and not report.get("demotion_signals_after_publish")
        )
    print(f"判定：{'PASS — 貨出得去，可以往下蓋工廠' if verdict_ok else 'FAIL — 停，先解這裡'}")
    return 0 if verdict_ok else 1


if __name__ == "__main__":
    sys.exit(main())
