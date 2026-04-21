"""一次性 Google Gmail OAuth consent 腳本。

在**本機**執行（**需登入 Gmail 那個帳號，跟 Google Calendar 帳號是不同帳號**）：

    python scripts/google_gmail_auth.py

會開瀏覽器讓使用者同意授權，然後把 token 寫到 ``data/google_gmail_token.json``。
完成後把 token 檔案 scp 到 VPS 的 ``data/`` 目錄即可。

詳細步驟見 ``docs/runbooks/nami-gmail-setup.md``。
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

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

_DATA_DIR = Path(os.environ.get("NAKAMA_DATA_DIR", "data"))
_CREDS_PATH = _DATA_DIR / "google_oauth_credentials.json"
_TOKEN_PATH = _DATA_DIR / "google_gmail_token.json"


def main() -> int:
    if not _CREDS_PATH.exists():
        print(f"[ERROR] 找不到 OAuth credentials: {_CREDS_PATH}")
        print("        請確認已在同一個 Google Cloud Project 啟用 Gmail API，")
        print("        並把 OAuth credentials JSON 放到此位置。")
        print("        詳見 docs/runbooks/nami-gmail-setup.md")
        return 1

    print(f"Credentials: {_CREDS_PATH}")
    print(f"Token 將寫到: {_TOKEN_PATH}")
    print(f"Scopes: {SCOPES}")
    print()
    print("[重要] 即將開啟瀏覽器進行授權。")
    print("       請使用 Gmail 那個 Google 帳號登入（不是 Calendar 的帳號）。")
    print("       若 app 未驗證，點 Advanced -> Go to ... (unsafe) 繼續。")
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
    print(f"     scopes: {creds.scopes}")
    print(f"     has refresh_token: {bool(creds.refresh_token)}")

    if not creds.refresh_token:
        print()
        print("[WARN] 沒拿到 refresh_token！Token 會過期。")
        print("       檢查 OAuth consent screen 是否已 PUBLISH（Audience 頁面），重跑此腳本。")
        return 1

    print()
    print("驗證（可選，在本機先試）：")
    print(
        '  python -c "from shared.google_gmail import _get_service;'
        " s = _get_service();"
        " p = s.users().getProfile(userId='me').execute();"
        " print('Gmail account:', p['emailAddress'])\""
    )
    print()
    print("下一步（部署到 VPS）：")
    print(f"  scp {_TOKEN_PATH} nakama-vps:/home/nakama/data/")
    print("  ssh nakama-vps 'chmod 600 /home/nakama/data/google_gmail_token.json'")
    print()
    print("[注意] credentials.json 若已在 VPS 上就不需要再 scp 一次。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
