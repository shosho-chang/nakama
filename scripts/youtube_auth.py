"""一次性 YouTube OAuth consent 腳本——一個頻道一把 token。

在**本機**執行（會開瀏覽器）：

    python scripts/youtube_auth.py                      # 研究所（Podcast）頻道，同舊行為
    python scripts/youtube_auth.py --channel main       # 主頻道：上傳＋管理＋分析數據
    python scripts/youtube_auth.py --channel podcast --analytics   # 研究所也加分析數據

沿用既有的 `data/google_oauth_credentials.json`（Calendar / Gmail 已在用的同一個
OAuth client），token 依頻道各存一檔，**互不覆蓋**：

| --channel | 頻道 | token |
|---|---|---|
| podcast（預設） | 張修修的不正常人類研究所 | `data/youtube_token.json`（發布線在用，檔名不變） |
| main | 張修修的不正常人生（主頻道） | `data/youtube_token_main.json` |

**頻道身分驗證**：consent 完成後先用這把 token 呼叫 `channels.list(mine=true)`，
回來的 channel ID 跟 `CHANNELS` 對不上就**不寫檔**、回傳非 0。授權畫面選錯頻道
（同一個 Google 帳號可管多頻道）會在這裡被擋下，而不是等到上傳時才傳錯頻道。

前置（GCP console，只要做一次）：
1. 該專案啟用 **YouTube Data API v3**；要分析數據再啟用 **YouTube Analytics API** 與
   **YouTube Reporting API**
2. OAuth consent screen 的 scope 清單加入本檔用到的 scope
   （都是 sensitive scope；app 未驗證時走 Advanced -> Go to ... (unsafe)）

完成後：上傳接 `scripts/youtube_publish_probe.py`；分析數據接 `scripts/youtube_analytics_fetch.py`。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Windows cp1252 console 會把中文掛掉，強制 UTF-8 輸出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import get_runtime_data_dir  # noqa: E402

# upload = videos.insert；force-ssl = CC + 發布後狀態 reconciliation
# （發布線 preflight 以這組為準：scripts/publish_upload.py YOUTUBE_PUBLISH_SCOPES）
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    # captions.insert 需要 force-ssl（2026-08-04 實測：CC 上傳 403
    # insufficientPermissions，前兩個 scope 蓋不到 captions endpoint）
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
# YouTube Analytics API（reports.query）與 Reporting API（曝光／點閱率 reach 報表）共用
ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"

#: 頻道 ID 來源：memory/shared/user/youtube_channel_routing.md
CHANNELS: dict[str, dict[str, str]] = {
    "podcast": {
        "channel_id": "UCvipegP35x3-OcAs--PgAig",
        "name": "張修修的不正常人類研究所",
        "token_file": "youtube_token.json",
    },
    "main": {
        "channel_id": "UC7_BNdimJrNLPDeectTg6Ig",
        "name": "張修修的不正常人生 Shosho's Abnormal Life",
        "token_file": "youtube_token_main.json",
    },
}

_DATA_DIR = get_runtime_data_dir()
_CREDS_PATH = _DATA_DIR / "google_oauth_credentials.json"
_TOKEN_PATH = _DATA_DIR / CHANNELS["podcast"]["token_file"]


class ChannelMismatchError(RuntimeError):
    """The consented token belongs to a different channel than requested."""


def token_path(channel: str, data_dir: Path | None = None) -> Path:
    return (data_dir or _DATA_DIR) / CHANNELS[channel]["token_file"]


def scopes_for(channel: str, *, analytics: bool | None = None) -> list[str]:
    """主頻道預設含分析數據；研究所預設維持發布線原本的兩個 scope。"""
    if analytics is None:
        analytics = channel == "main"
    return [*SCOPES, ANALYTICS_SCOPE] if analytics else list(SCOPES)


def verify_channel(channels_list_response: dict, channel: str) -> str:
    """Return the channel id if the token is bound to the expected channel, else raise."""
    expected = CHANNELS[channel]["channel_id"]
    items = channels_list_response.get("items") or []
    ids = [item.get("id") for item in items]
    if ids != [expected]:
        titles = [((item.get("snippet") or {}).get("title")) for item in items]
        raise ChannelMismatchError(
            f"這把 token 綁到的頻道是 {list(zip(ids, titles))}，"
            f"不是要求的「{CHANNELS[channel]['name']}」（{expected}）。"
            "重跑一次，授權畫面要選對頻道。"
        )
    return expected


def fetch_own_channel(creds) -> dict:
    from googleapiclient.discovery import build

    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    return youtube.channels().list(part="id,snippet", mine=True).execute()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--channel", choices=sorted(CHANNELS), default="podcast")
    ap.add_argument(
        "--analytics",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="是否加 yt-analytics.readonly（main 預設開、podcast 預設關）",
    )
    a = ap.parse_args(argv)

    if not _CREDS_PATH.exists():
        print(f"[ERROR] 找不到 OAuth credentials: {_CREDS_PATH}")
        print("        Calendar / Gmail 用的是同一個檔。")
        print("        取得方式見 docs/setup/google-calendar.md Step 4。")
        return 1

    scopes = scopes_for(a.channel, analytics=a.analytics)
    out = token_path(a.channel)
    info = CHANNELS[a.channel]
    print(f"頻道: {info['name']}（{info['channel_id']}）")
    print(f"Credentials: {_CREDS_PATH}（與 Calendar / Gmail 共用）")
    print(f"Token 將寫到: {out}（驗證頻道身分通過才寫）")
    for s in scopes:
        print(f"Scope: {s}")
    print()
    print("即將開啟瀏覽器進行授權...")
    print(f"⚠️  選擇帳號／頻道時，請選「{info['name']}」")
    print("（app 未驗證時：點 Advanced -> Go to ... (unsafe) 繼續）")
    print("（若出現「這個應用程式未通過 Google 驗證」且無法繼續，代表 consent screen")
    print("  還沒把上面的 scope 加進去，或對應的 API 未啟用）")
    print()

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(_CREDS_PATH), scopes)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    granted = set(creds.scopes or [])
    missing = [s for s in scopes if s not in granted]
    if missing:
        print()
        print("[ERROR] 以下 scope 沒被授予（授權畫面有沒勾到的項目），token 不寫入：")
        for s in missing:
            print(f"       - {s}")
        return 1

    try:
        verify_channel(fetch_own_channel(creds), a.channel)
    except ChannelMismatchError as exc:
        print()
        print(f"[ERROR] {exc}")
        print("        token 沒有寫入，既有檔案維持原狀。")
        return 1

    out.write_text(creds.to_json())

    print()
    print(f"[OK] 頻道身分已驗證：{info['name']}")
    print(f"[OK] Token 已寫入 {out}")
    print(f"     scope: {creds.scopes}")
    print(f"     has refresh_token: {bool(creds.refresh_token)}")

    if not creds.refresh_token:
        print()
        print("[WARN] 沒拿到 refresh_token！Token 會過期。")
        print("       檢查 OAuth consent screen 是否已 PUBLISH（Audience 頁面），重跑此腳本。")
        return 1

    print()
    if a.channel == "main":
        print("下一步：python scripts/youtube_analytics_fetch.py --channel main")
    else:
        print("下一步：python scripts/youtube_publish_probe.py --help")
    return 0


if __name__ == "__main__":
    sys.exit(main())
