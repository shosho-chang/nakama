# Google Search Console Service Account 設定 — ADR-009 Slice A

**Scope:** 讓 `shared/gsc_client.py` 能讀 `shosho.tw` 與 `fleet.shosho.tw` 兩個 GSC property 的 Search Analytics 資料，供 ADR-009 Phase 1 Slice B (`seo-keyword-enrich`) 消費。
**Owner:** 修修手動（GCP console / GSC console 都需登入）。
**時間預估:** 15–20 分鐘（首次；GCP 有 project 可再省 5 分鐘）。
**輸出:** `GSC_SERVICE_ACCOUNT_JSON_PATH` / `GSC_PROPERTY_SHOSHO` / `GSC_PROPERTY_FLEET` 三個 env 值 + smoke test 通過。

---

## 為什麼用 Service Account（不是 OAuth consent flow）

- Nakama agent 跑在 VPS / 本機 headless，**不能**走 OAuth consent redirect。
- Service account 是 long-lived JSON key，單次設定後無需使用者互動刷新 token。
- GSC API 支援 service account 授權 — 在對應 property 加 service account email 為 user 即可。
- Scope：`https://www.googleapis.com/auth/webmasters.readonly`（只讀，不動 GSC 設定）。

---

## 前置條件

- [ ] `shosho.tw` 和 `fleet.shosho.tw` 已在 [Google Search Console](https://search.google.com/search-console) 驗證成 property owner
- [ ] 有 Google Cloud Platform 帳號（可用同一 Google 帳號）
- [ ] 本機 / VPS 的 `.env` 可寫入（參照 [feedback_env_push_diff_before_overwrite.md](../../memory/claude/feedback_env_push_diff_before_overwrite.md)）

---

## 跨機策略（Mac + 桌機 + VPS）

Nakama 開發環境目前三台：Mac、桌機（Windows）、VPS（Linux）。JSON key 含 private key **絕對不能 commit repo**、**不走 iCloud / Dropbox**，所以不能單純「下載一次各機同步」。三個可行策略：

| 策略 | 安全 | 便利 | Rotation 成本 | 建議 |
|---|---|---|---|---|
| **A. 各機獨立下載** | ⭐️⭐️⭐️ 零跨機傳輸 | ⭐️⭐️ 首次多跑一次 step 3 | 低（各機獨立 revoke） | ✅ **推薦** |
| B. 一台下載後 scp 到另一台 | ⭐️⭐️ 加密傳輸但曾入網 | ⭐️⭐️⭐️ 簡單 | 中（一動全動） | 🟡 備選 |
| C. `age` / `gpg` 加密 commit | ⭐️⭐️⭐️ 若私鑰控制得當 | ⭐️ 每次 decrypt | 高（tooling 要同步） | ❌ 不建議 |

**策略 A 做法**（推薦）：

1. **Mac**：跑 step 3 下載第一把 JSON → 放 `~/.config/nakama/gsc-service-account.json`
2. **桌機**：同一個 GCP service account 頁面 **Add Key → Create new JSON key** 下載第二把 → 放 `C:\Users\<user>\.config\nakama\gsc-service-account.json`（或 `%USERPROFILE%\.config\nakama\`）
3. **VPS**：特例 — VPS 不能從瀏覽器下 GCP key。從 Mac 或桌機其中一台 `scp` 一把上去（step 6 有指令）。這是唯一單程跨機傳輸，SSH 加密、VPS 受控

GCP service account 上限 10 把 key，三機三把綽綽有餘。**一台 compromise 時只 revoke 該機那把**，其他機不受影響。

**為何不建議 B/C**：
- B 的 scp 雖加密，還是多一次網路傳輸窗口；rotation 要同步兩端
- C 雖技術可行但修修目前沒 age/gpg workflow，引入新 tooling 成本 > 收益；若未來 secrets 數量增加再評估

---

## 步驟

### 1. GCP Project 與 API 啟用

1. 打開 [GCP Console](https://console.cloud.google.com/)，選一個現有 project 或 **+ New Project**（名稱例：`nakama-seo`）。
2. 左側 **APIs & Services → Library** → 搜尋 `Google Search Console API` → **Enable**。
3. 記下 project ID（非 name；右上角 project dropdown 可看）。

### 2. 建 Service Account

1. 同一 project **APIs & Services → Credentials** → **+ Create Credentials → Service account**。
2. Service account name：`nakama-gsc-reader`（service account ID 自動產出為 `nakama-gsc-reader@<project>.iam.gserviceaccount.com`）。
3. **Grant access**：選 `Viewer` role（寬鬆，但 GSC 資料授權是在 GSC 端另外給的，project-level role 只影響 GCP 其他服務）。
4. 跳過 user access，點 **Done**。

### 3. 產生 JSON Key

依上方 **跨機策略** 章節，每台開發機（Mac / 桌機）各跑一次此 step 下載**自己那把**（VPS 例外，從其中一台 scp 上去）：

1. 在 Credentials 頁點開剛建的 service account。
2. **Keys 分頁 → Add Key → Create new key → JSON** → 下載。
3. 檔案存到本機安全位置：

   **Mac**：
   ```bash
   mkdir -p ~/.config/nakama
   chmod 700 ~/.config/nakama
   mv ~/Downloads/<下載的 JSON 檔名>.json ~/.config/nakama/gsc-service-account.json
   chmod 600 ~/.config/nakama/gsc-service-account.json
   ```

   **桌機（Windows，PowerShell）**：
   ```powershell
   $dir = "$env:USERPROFILE\.config\nakama"
   New-Item -ItemType Directory -Force -Path $dir | Out-Null
   Move-Item "$env:USERPROFILE\Downloads\<下載的 JSON 檔名>.json" "$dir\gsc-service-account.json"
   # 權限收緊（移除 inherited 權限，只留 owner）
   icacls "$dir\gsc-service-account.json" /inheritance:r /grant:r "${env:USERNAME}:(R)"
   ```

   > ℹ️ **iCloud / OneDrive 提醒**：Mac 不要放 `~/Documents/` 或 `~/Desktop/`（預設 iCloud 同步）。Windows 不要放 `%USERPROFILE%\Documents\` / `%USERPROFILE%\OneDrive\`。`~/.config/` / `%USERPROFILE%\.config\` 兩平台都不在任何雲端同步範圍內。

4. **絕對路徑**是等下 `.env` 要填的值（step 6）。

> ⚠️ JSON 含 private key，**不可 commit 進 repo**、不可貼 Slack、不可 scp 整份到 VPS 前 diff。見 [feedback_no_secrets_in_chat.md](../../memory/claude/feedback_no_secrets_in_chat.md)。

### 4. 在 GSC 加 Service Account 為 User

對**每個** property 都要做一次：

1. 打開 [GSC](https://search.google.com/search-console)，左上 property dropdown 選 `shosho.tw`（或 `fleet.shosho.tw`）。
2. 左下 **⚙ Settings → Users and permissions → Add user**。
3. Email：貼 service account email（step 2 的 `...@<project>.iam.gserviceaccount.com`）。
4. Permission：選 **Restricted**（對應 read-only；`Owner` / `Full` 不需要）。
5. 重複 step 1–4 for `fleet.shosho.tw`。

### 5. 找到 Property 識別字串

GSC property 有兩種型態，GSC API `siteUrl` 格式不同：

| GSC property 型態 | API `siteUrl` 格式 | 範例 |
|---|---|---|
| **Domain property**（推薦） | `sc-domain:<domain>` | `sc-domain:shosho.tw` |
| **URL-prefix property** | 含 trailing slash 的 full URL | `https://shosho.tw/` |

判斷：在 GSC Settings 頁面 property type 欄位會標明。若兩種都有，優先用 domain property。

### 6. 寫入 `.env`

三台環境 `.env` 路徑不同，`GSC_PROPERTY_*` 值相同。

**Mac `.env`**：

```bash
GSC_SERVICE_ACCOUNT_JSON_PATH=/Users/shosho/.config/nakama/gsc-service-account.json
GSC_PROPERTY_SHOSHO=sc-domain:shosho.tw
GSC_PROPERTY_FLEET=sc-domain:fleet.shosho.tw
```

**桌機 `.env`**（Windows，注意路徑用 forward slash 或 double backslash 避免 dotenv escape 問題）：

```bash
GSC_SERVICE_ACCOUNT_JSON_PATH=C:/Users/shosho/.config/nakama/gsc-service-account.json
GSC_PROPERTY_SHOSHO=sc-domain:shosho.tw
GSC_PROPERTY_FLEET=sc-domain:fleet.shosho.tw
```

**VPS `.env`**：從 Mac / 桌機其中一台 `scp` 一把 JSON 上去（見跨機策略 A.3），再編 `.env`：

```bash
# 從本機（Mac 範例）：
scp ~/.config/nakama/gsc-service-account.json nakama-vps:/home/nakama/.config/nakama/gsc-service-account.json
ssh nakama-vps "chmod 600 /home/nakama/.config/nakama/gsc-service-account.json"
```

然後 `ssh nakama-vps` in-place 編輯 `.env`，append 三個 key（**不要 scp 整份 `.env` 覆蓋** — 見 [feedback_env_push_diff_before_overwrite.md](../../memory/claude/feedback_env_push_diff_before_overwrite.md)）：

```bash
ssh nakama-vps
cd /home/nakama
cp .env .env.bak.$(date +%Y%m%d_%H%M%S)
cat >> .env <<'EOF'

# ADR-009 SEO Solution
GSC_SERVICE_ACCOUNT_JSON_PATH=/home/nakama/.config/nakama/gsc-service-account.json
GSC_PROPERTY_SHOSHO=sc-domain:shosho.tw
GSC_PROPERTY_FLEET=sc-domain:fleet.shosho.tw
EOF
```

### 7. 驗收 Smoke Test

本機跑一次真實 query（過去 7 天 top 10 query）：

```bash
cd /Users/shosho/Documents/nakama
source .venv/bin/activate
python -c "
from datetime import date, timedelta
from shared.gsc_client import GSCClient
import os

client = GSCClient.from_env()
today = date.today()
# GSC 資料延遲 2–3 天，避掉最近 3 天
rows = client.query(
    site=os.environ['GSC_PROPERTY_SHOSHO'],
    start_date=(today - timedelta(days=10)).isoformat(),
    end_date=(today - timedelta(days=3)).isoformat(),
    dimensions=['query'],
    row_limit=10,
)
print(f'Got {len(rows)} rows; top: {rows[0] if rows else \"(empty)\"}')"
```

預期：印出 `Got N rows; top: {'keys': ['...'], 'clicks': ..., 'impressions': ..., 'ctr': ..., 'position': ...}`。

---

## 常見錯誤

| 症狀 | 原因 | 修法 |
|---|---|---|
| `GSCCredentialsError: GSC service account JSON not found` | 路徑錯 / 檔案沒上傳 | 檢查 `GSC_SERVICE_ACCOUNT_JSON_PATH` 絕對路徑 + 檔案存在 |
| `HttpError 403 ... User does not have sufficient permission` | Step 4 沒做 / email 貼錯 | 回 GSC Settings 重新 Add user |
| `HttpError 404 ... Search Console entity does not exist` | `siteUrl` 格式錯 | domain property 要 `sc-domain:` 前綴；URL-prefix 要 trailing `/` |
| `HttpError 429 ... Quota exceeded` | 每日 quota（~200 queries） | 降低 query 次數 or 走 Phase 2 共用 rate limit middleware |

---

## 相關

- [shared/gsc_client.py](../../shared/gsc_client.py) — 本 runbook 的對象 module
- [ADR-009](../decisions/ADR-009-seo-solution-architecture.md) §D3 — SEOContextV1 schema
- [docs/research/seo-prior-art-2026-04-24.md](../research/seo-prior-art-2026-04-24.md) §5.1 — GSC quota / rate limit 預估
