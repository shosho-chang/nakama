"""一次性 YouTube 上傳 OAuth consent 腳本（Slice 0 探針的前置）。

在**本機**執行（會開瀏覽器）：

    python scripts/youtube_auth.py

沿用既有的 `data/google_oauth_credentials.json`（Calendar / Gmail 已在用的同一個
OAuth client），只是多要 YouTube 的兩個 scope，token 另存
`data/youtube_token.json`——**不會覆蓋** Calendar / Gmail 的 token。

前置（GCP console，只要做一次）：
1. 該專案啟用 **YouTube Data API v3**
2. OAuth consent screen 的 scope 清單加入本檔 `SCOPES` 兩項
   （兩個都是 sensitive scope；app 未驗證時走 Advanced -> Go to ... (unsafe)）

完成後接 `scripts/youtube_publish_probe.py`。
"""

from __future__ import annotations

import sys
from pathlib import Path

# Windows cp1252 console 會把中文掛掉，強制 UTF-8 輸出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402

from shared.config import get_runtime_data_dir  # noqa: E402

# upload = videos.insert；force-ssl = CC + 發布後狀態 reconciliation
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    # captions.insert 需要 force-ssl（2026-08-04 實測：CC 上傳 403
    # insufficientPermissions，前兩個 scope 蓋不到 captions endpoint）
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

_DATA_DIR = get_runtime_data_dir()
_CREDS_PATH = _DATA_DIR / "google_oauth_credentials.json"
_TOKEN_PATH = _DATA_DIR / "youtube_token.json"


def main() -> int:
    if not _CREDS_PATH.exists():
        print(f"[ERROR] 找不到 OAuth credentials: {_CREDS_PATH}")
        print("        Calendar / Gmail 用的是同一個檔。")
        print("        取得方式見 docs/setup/google-calendar.md Step 4。")
        return 1

    print(f"Credentials: {_CREDS_PATH}（與 Calendar / Gmail 共用）")
    print(f"Token 將寫到: {_TOKEN_PATH}（獨立檔，不覆蓋既有 token）")
    for s in SCOPES:
        print(f"Scope: {s}")
    print()
    print("即將開啟瀏覽器進行授權...")
    print("（app 未驗證時：點 Advanced -> Go to ... (unsafe) 繼續）")
    print("（若出現「這個應用程式未通過 Google 驗證」且無法繼續，代表 consent screen")
    print("  還沒把上面兩個 scope 加進去，或 YouTube Data API v3 未啟用）")
    print()

    flow = InstalledAppFlow.from_client_secrets_file(str(_CREDS_PATH), SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

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

    granted = set(creds.scopes or [])
    missing = [s for s in SCOPES if s not in granted]
    if missing:
        print()
        print("[WARN] 以下 scope 沒被授予，探針會失敗：")
        for s in missing:
            print(f"       - {s}")
        return 1

    print()
    print("下一步：python scripts/youtube_publish_probe.py --help")
    return 0


if __name__ == "__main__":
    sys.exit(main())
