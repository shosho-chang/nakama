"""一次性 Google Calendar OAuth consent 腳本。

在**本機**執行：

    python scripts/google_calendar_auth.py

會開瀏覽器讓使用者同意授權，然後把 token 寫到 ``data/google_calendar_token.json``。
完成後把 credentials + token 兩個檔案 scp 到 VPS 的 ``data/`` 目錄即可。

詳細步驟見 ``docs/setup/google-calendar.md``。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Windows cp1252 console 會把中文 / emoji 掛掉，強制 UTF-8 輸出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

_DATA_DIR = Path(os.environ.get("NAKAMA_DATA_DIR", "data"))
_CREDS_PATH = _DATA_DIR / "google_oauth_credentials.json"
_TOKEN_PATH = _DATA_DIR / "google_calendar_token.json"


def main() -> int:
    # Windows cp1252 終端機不支援 emoji，只用 ASCII 輸出
    if not _CREDS_PATH.exists():
        print(f"[ERROR] 找不到 OAuth credentials: {_CREDS_PATH}")
        print("        請依 docs/setup/google-calendar.md Step 4 下載並搬到此位置。")
        return 1

    print(f"Credentials: {_CREDS_PATH}")
    print(f"Token 將寫到: {_TOKEN_PATH}")
    print(f"Scope: {SCOPES}")
    print()
    print("即將開啟瀏覽器進行授權...")
    print("（若 app 未驗證，點 Advanced -> Go to ... (unsafe) 繼續）")
    print()

    flow = InstalledAppFlow.from_client_secrets_file(str(_CREDS_PATH), SCOPES)
    # access_type=offline + prompt=consent -> 強制拿到 refresh_token
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
    )

    _TOKEN_PATH.write_text(creds.to_json())

    print()
    print(f"[OK] Token 已寫入 {_TOKEN_PATH}")
    print(f"     scope: {creds.scopes}")
    print(f"     has refresh_token: {bool(creds.refresh_token)}")

    if not creds.refresh_token:
        print()
        print("[WARN] 沒拿到 refresh_token！Token 會過期。")
        print("       檢查 OAuth consent screen 是否已 PUBLISH（Audience 頁面），重跑此腳本。")
        return 1

    print()
    print("下一步（部署到 VPS）：")
    print(f"  scp {_TOKEN_PATH} nakama-vps:/home/nakama/data/")
    print(f"  scp {_CREDS_PATH} nakama-vps:/home/nakama/data/")
    print("  ssh nakama-vps 'chmod 600 /home/nakama/data/google_*'")
    print()
    print("驗證（可選，在本機先試）：")
    print(
        '  python -c "from shared.google_calendar import list_events;'
        " from datetime import datetime, timedelta, timezone;"
        " now = datetime.now(timezone.utc);"
        " print(len(list_events(time_min=now, time_max=now+timedelta(days=7))), 'events')\""
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
