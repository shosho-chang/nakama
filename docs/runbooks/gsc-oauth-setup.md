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

1. 在 Credentials 頁點開剛建的 service account。
2. **Keys 分頁 → Add Key → Create new key → JSON** → 下載。
3. 檔案存到本機安全位置，例 `~/.config/nakama/gsc-service-account.json`；**絕對路徑**是等下 `.env` 要填的值。
4. `chmod 600` 收緊權限（避免 shared machine 洩漏）。

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

本機 `.env`：

```bash
GSC_SERVICE_ACCOUNT_JSON_PATH=/Users/shosho/.config/nakama/gsc-service-account.json
GSC_PROPERTY_SHOSHO=sc-domain:shosho.tw
GSC_PROPERTY_FLEET=sc-domain:fleet.shosho.tw
```

VPS `.env`：先把 JSON 用 `scp` 上去：

```bash
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
