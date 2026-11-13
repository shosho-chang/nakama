"""唯讀：列出頻道最近上架的影片標題——title-brainstorm 的風格基準。

為什麼（修修 2026-11-13，20261111 李惠貞）：標題 agent 拿上一集的 title-brainstorm 審查報告
當範本，把「公司在算帳」的寫法延續成「離職前算的那筆帳」——生成器抄生成器，AI 腔一集傳一集。
風格基準要是修修**實際上架**的標題（他在 YouTube Studio 改過的定稿），不是我們產的候選。

用法：
    E:\\nakama\\.venv-v2\\Scripts\\python.exe scripts\\youtube_recent_titles.py [--limit 30]

只呼叫 channels.list(mine) 與 playlistItems.list（讀上傳清單），不寫任何東西。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.usopp.youtube_credentials import load_youtube_client  # noqa: E402
from shared.config import get_runtime_data_dir  # noqa: E402

TOKEN_PATH = get_runtime_data_dir() / "youtube_token.json"


def recent_titles(youtube, limit: int) -> list[tuple[str, str]]:
    """回傳 [(publishedAt, title)]，新到舊。"""
    channels = youtube.channels().list(part="contentDetails", mine=True).execute()
    items = channels.get("items") or []
    if not items:
        raise SystemExit("這組 OAuth 憑證看不到任何頻道")
    uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    out: list[tuple[str, str]] = []
    token = None
    while len(out) < limit:
        page = (
            youtube.playlistItems()
            .list(
                part="snippet",
                playlistId=uploads,
                maxResults=min(50, limit - len(out)),
                pageToken=token,
            )
            .execute()
        )
        for item in page.get("items", []):
            snippet = item["snippet"]
            out.append((snippet.get("publishedAt", ""), snippet.get("title", "")))
        token = page.get("nextPageToken")
        if not token:
            break
    return out[:limit]


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args(argv)
    youtube = load_youtube_client(TOKEN_PATH)
    for published, title in recent_titles(youtube, args.limit):
        print(f"{published[:10]}  {title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
