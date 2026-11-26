"""抓 YouTube 頻道的分析數據（唯讀）→ 本機 CSV。

    python scripts/youtube_analytics_fetch.py --channel main [--top-n 40] [--out-dir <dir>]

前置：`python scripts/youtube_auth.py --channel main`（token 含 yt-analytics.readonly）。
全程唯讀；唯一的「寫」是在 YouTube Reporting API 建立 reach 報表排程（曝光／點閱率
只有這條路拿得到，排程建立後 YouTube 每天產一份，可用 --no-reach-job 關掉）。

輸出（預設 `E:/data/youtube-analytics/<channel>/<YYYYMMDD>/`）：

| 檔案 | 內容 |
|---|---|
| videos.csv | 全部影片：標題、發布日、長度、公開統計、縮圖網址 |
| video_metrics.csv | 每支影片的終身數據：觀看、觀看分鐘、平均觀看時間／百分比、新增訂閱… |
| demographics_channel.csv | 全頻道觀眾年齡 × 性別 |
| demographics_by_video.csv | 前 N 支影片各自的年齡 × 性別 |
| country.csv | 觀看國家／地區 |
| traffic_sources.csv | 流量來源類型 |
| search_terms_by_video.csv | 前 N 支影片各自的 YouTube 搜尋字詞（每支前 25） |
| comments.csv | 前 N 支影片的熱門留言（不含留言者名稱） |
| reach/*.csv | Reporting API reach 報表（曝光次數、曝光點閱率；排程產出後才有） |
| manifest.json | 這次跑了什麼、筆數、哪些查詢失敗（失敗不中斷，逐項記錄） |
"""

from __future__ import annotations

import argparse
import csv
import json
import socket
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import youtube_auth  # noqa: E402

# googleapiclient（httplib2）預設不設逾時：2026-11-12 首跑卡在逐支查詢 10 分鐘、CPU 0.9 秒，
# 整支程式靜止不動。設全域逾時讓卡住的請求變成一筆可記錄的錯誤，而不是無限等待。
socket.setdefaulttimeout(60)

REACH_REPORT_TYPE = "channel_reach_basic_a1"
DEFAULT_OUT_ROOT = Path("E:/data/youtube-analytics")


# ---------------------------------------------------------------- helpers (pure)


def report_rows(resp: dict) -> list[dict]:
    """YouTube Analytics reports.query 回應 → list of dict（欄名取 columnHeaders）。"""
    headers = [h["name"] for h in resp.get("columnHeaders", [])]
    return [dict(zip(headers, row)) for row in resp.get("rows", []) or []]


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = []
        for r in rows:
            for k in r:
                if k not in fields:
                    fields.append(k)
    # utf-8-sig：Excel 直接開不亂碼
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


class Runner:
    """收集每一項查詢的結果與錯誤；單項失敗不中斷整次執行。"""

    def __init__(self) -> None:
        self.errors: list[dict] = []
        self.counts: dict[str, int] = {}

    def attempt(self, label: str, fn: Callable[[], Any], default: Any = None) -> Any:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — 記下來、繼續跑其他項
            self.errors.append({"query": label, "error": f"{type(exc).__name__}: {exc}"[:800]})
            print(f"  [skip] {label}: {type(exc).__name__}")
            return default


# ---------------------------------------------------------------- API calls


def load_credentials(channel: str):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = youtube_auth.token_path(channel)
    if not path.is_file():
        raise SystemExit(f"找不到 {path}——先跑 python scripts/youtube_auth.py --channel {channel}")
    creds = Credentials.from_authorized_user_file(str(path))
    if youtube_auth.ANALYTICS_SCOPE not in set(creds.scopes or []):
        raise SystemExit(
            f"{path} 沒有 yt-analytics.readonly——重跑 python scripts/youtube_auth.py "
            f"--channel {channel} --analytics"
        )
    if not creds.valid:
        creds.refresh(Request())
    return creds


def list_videos(yt, uploads_playlist: str) -> list[dict]:
    ids: list[str] = []
    token = None
    while True:
        resp = (
            yt.playlistItems()
            .list(
                part="contentDetails", playlistId=uploads_playlist, maxResults=50, pageToken=token
            )
            .execute()
        )
        ids += [it["contentDetails"]["videoId"] for it in resp.get("items", [])]
        token = resp.get("nextPageToken")
        if not token:
            break
    out: list[dict] = []
    for batch in chunks(ids, 50):
        resp = (
            yt.videos()
            .list(part="snippet,contentDetails,statistics", id=",".join(batch), maxResults=50)
            .execute()
        )
        for v in resp.get("items", []):
            sn, st, cd = v.get("snippet", {}), v.get("statistics", {}), v.get("contentDetails", {})
            thumbs = sn.get("thumbnails", {})
            best = thumbs.get("maxres") or thumbs.get("high") or thumbs.get("default") or {}
            out.append(
                {
                    "video_id": v["id"],
                    "title": sn.get("title"),
                    "published_at": sn.get("publishedAt"),
                    "duration": cd.get("duration"),
                    "views": st.get("viewCount"),
                    "likes": st.get("likeCount"),
                    "comments": st.get("commentCount"),
                    "thumbnail_url": best.get("url"),
                }
            )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="抓 YouTube 頻道分析數據（唯讀）")
    ap.add_argument("--channel", choices=sorted(youtube_auth.CHANNELS), default="main")
    ap.add_argument(
        "--top-n", type=int, default=40, help="逐支查詢（人口、搜尋字詞、留言）的影片數"
    )
    ap.add_argument("--comments-per-video", type=int, default=100)
    ap.add_argument("--out-dir", type=Path, default=None)
    ap.add_argument("--no-reach-job", action="store_true", help="不建立 reach 報表排程")
    a = ap.parse_args(argv)

    from google.auth.transport.requests import AuthorizedSession
    from googleapiclient.discovery import build

    creds = load_credentials(a.channel)
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    ya = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)

    # 先驗頻道身分：token 綁錯頻道就整個停下
    me = yt.channels().list(part="id,snippet,contentDetails", mine=True).execute()
    youtube_auth.verify_channel(me, a.channel)
    ch = me["items"][0]
    start = (ch["snippet"].get("publishedAt") or "2010-01-01")[:10]
    end = date.today().isoformat()
    uploads = ch["contentDetails"]["relatedPlaylists"]["uploads"]

    out = a.out_dir or DEFAULT_OUT_ROOT / a.channel / date.today().strftime("%Y%m%d")
    out.mkdir(parents=True, exist_ok=True)
    run = Runner()
    print(f"頻道 {ch['snippet']['title']}｜{start} → {end}｜輸出 {out}")

    def q(**kw) -> list[dict]:
        return report_rows(
            ya.reports().query(ids="channel==MINE", startDate=start, endDate=end, **kw).execute()
        )

    # 1. 影片清單
    videos = run.attempt("videos", lambda: list_videos(yt, uploads), [])
    run.counts["videos"] = write_csv(out / "videos.csv", videos)
    titles = {v["video_id"]: v["title"] for v in videos}

    # 2. 每支影片終身數據
    metrics = run.attempt(
        "video_metrics",
        lambda: q(
            dimensions="video",
            sort="-views",
            maxResults=200,
            metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,"
            "subscribersGained,subscribersLost,likes,comments,shares",
        ),
        [],
    )
    for r in metrics:
        r["title"] = titles.get(r.get("video"))
    run.counts["video_metrics"] = write_csv(out / "video_metrics.csv", metrics)

    # 3. 全頻道：人口、國家、流量來源
    run.counts["demographics_channel"] = write_csv(
        out / "demographics_channel.csv",
        run.attempt(
            "demographics_channel",
            lambda: q(dimensions="ageGroup,gender", metrics="viewerPercentage"),
            [],
        ),
    )
    run.counts["country"] = write_csv(
        out / "country.csv",
        run.attempt(
            "country",
            lambda: q(
                dimensions="country",
                metrics="views,estimatedMinutesWatched",
                sort="-views",
                maxResults=50,
            ),
            [],
        ),
    )
    run.counts["traffic_sources"] = write_csv(
        out / "traffic_sources.csv",
        run.attempt(
            "traffic_sources",
            lambda: q(
                dimensions="insightTrafficSourceType",
                metrics="views,estimatedMinutesWatched",
                sort="-views",
            ),
            [],
        ),
    )

    # 4. 前 N 支：人口、搜尋字詞、留言
    top = [r["video"] for r in metrics[: a.top_n] if r.get("video")]
    demo_rows, term_rows, comment_rows = [], [], []
    for i, vid in enumerate(top, 1):
        print(f"  [{i}/{len(top)}] {titles.get(vid, vid)}")
        for r in run.attempt(
            f"demographics:{vid}",
            lambda: q(
                dimensions="ageGroup,gender", metrics="viewerPercentage", filters=f"video=={vid}"
            ),
            [],
        ):
            demo_rows.append({"video": vid, "title": titles.get(vid), **r})
        for r in run.attempt(
            f"search_terms:{vid}",
            lambda: q(
                dimensions="insightTrafficSourceDetail",
                metrics="views",
                sort="-views",
                maxResults=25,
                filters=f"video=={vid};insightTrafficSourceType==YT_SEARCH",
            ),
            [],
        ):
            term_rows.append({"video": vid, "title": titles.get(vid), **r})
        resp = run.attempt(
            f"comments:{vid}",
            lambda: (
                yt.commentThreads()
                .list(
                    part="snippet",
                    videoId=vid,
                    order="relevance",
                    textFormat="plainText",
                    maxResults=min(a.comments_per_video, 100),
                )
                .execute()
            ),
            {},
        )
        for it in (resp or {}).get("items", []):
            top_c = it["snippet"]["topLevelComment"]["snippet"]
            # 不存留言者名稱：分析只需要內容，不需要知道是誰
            comment_rows.append(
                {
                    "video": vid,
                    "title": titles.get(vid),
                    "published_at": top_c.get("publishedAt"),
                    "likes": top_c.get("likeCount"),
                    "replies": it["snippet"].get("totalReplyCount"),
                    "text": top_c.get("textDisplay"),
                }
            )
    run.counts["demographics_by_video"] = write_csv(out / "demographics_by_video.csv", demo_rows)
    run.counts["search_terms_by_video"] = write_csv(out / "search_terms_by_video.csv", term_rows)
    run.counts["comments"] = write_csv(out / "comments.csv", comment_rows)

    # 5. Reporting API：曝光／點閱率（排程制，建立後 YouTube 每天產一份）
    reach_note = "skipped (--no-reach-job)"
    if not a.no_reach_job:
        yr = build("youtubereporting", "v1", credentials=creds, cache_discovery=False)
        types = (
            run.attempt("reach:reportTypes", lambda: yr.reportTypes().list().execute(), {}) or {}
        )
        if REACH_REPORT_TYPE not in {t["id"] for t in types.get("reportTypes", [])}:
            reach_note = f"{REACH_REPORT_TYPE} 不在這個頻道可用的報表類型裡"
        else:
            jobs = run.attempt("reach:jobs", lambda: yr.jobs().list().execute(), {}) or {}
            job = next(
                (j for j in jobs.get("jobs", []) if j.get("reportTypeId") == REACH_REPORT_TYPE),
                None,
            )
            if job is None:
                job = run.attempt(
                    "reach:create_job",
                    lambda: (
                        yr.jobs()
                        .create(body={"reportTypeId": REACH_REPORT_TYPE, "name": "nakama reach"})
                        .execute()
                    ),
                )
                reach_note = (
                    f"已建立排程 {job and job.get('id')}；YouTube 通常要 1–2 天後才產出第一份報表"
                )
            else:
                reports = (
                    run.attempt(
                        "reach:reports",
                        lambda: yr.jobs().reports().list(jobId=job["id"]).execute(),
                        {},
                    )
                    or {}
                )
                session = AuthorizedSession(creds)
                saved = 0
                for rep in reports.get("reports", []):
                    name = f"{rep.get('startTime', '')[:10]}_{rep.get('id')}.csv"
                    target = out / "reach" / name
                    data = run.attempt(
                        f"reach:download:{rep.get('id')}",
                        lambda: session.get(rep["downloadUrl"]).content,
                    )
                    if data:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(data)
                        saved += 1
                run.counts["reach_reports"] = saved
                reach_note = f"排程 {job['id']}：下載 {saved} 份報表"

    manifest = {
        "channel": a.channel,
        "channel_id": ch["id"],
        "channel_title": ch["snippet"]["title"],
        "range": [start, end],
        "top_n": len(top),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "counts": run.counts,
        "reach": reach_note,
        "errors": run.errors,
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n完成：{json.dumps(run.counts, ensure_ascii=False)}")
    print(f"reach：{reach_note}")
    if run.errors:
        print(f"{len(run.errors)} 項查詢失敗，細節見 manifest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
