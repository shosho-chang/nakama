# Runbook — WP + Community 整合憑證建立 Checklist

**目的**：Phase 1（Brook + Usopp + Franky）上線前，修修需手動建立的帳號與憑證清單。

**預估時間**：約 60-90 分鐘全部跑完。

---

## 1. WordPress Bot 帳號 × 2

### 1a. `bot_usopp` on shosho.tw

- [ ] 登入 `https://shosho.tw/<wps-hide-login 路徑>/wp-admin`
- [ ] 新增使用者：
  - Username: `bot_usopp`
  - Email: `bot-usopp@shosho.tw`（或任何你專用的 alias）
  - Role: **Editor**（能建/改/發所有 post，但不能改其他 user / plugin）
  - Password: 自動生成，不重要（不會用它登入）
- [ ] 進 user profile → **Application Passwords** 區塊 → 新增
  - Name: `nakama-usopp-publisher`
  - 複製生成的 24 字母密碼（只顯示一次）
- [ ] 填入 `.env`：
  ```
  WP_SHOSHO_BASE_URL=https://shosho.tw/wp-json
  WP_SHOSHO_USERNAME=bot_usopp
  WP_SHOSHO_APP_PASSWORD=xxxx xxxx xxxx xxxx xxxx xxxx
  ```

### 1b. `bot_chopper` on fleet.shosho.tw（Phase 3 才用，但建議一起建）

**⚠️ 前置步驟：先開啟 Application Password**

fleet 站裝了 **Fluent Security** plugin，預設會關掉 Application Password 功能。必須先：

- [ ] 登入 fleet.shosho.tw 的 wp-admin
- [ ] 左側選單 → **FluentAuth / Fluent Security**
- [ ] General Settings → 找到 **"Disable Application Passwords"** toggle
- [ ] **關掉**（toggle off）
- [ ] 存檔
- [ ] 回 Users → bot user → profile 頁面最下方 → 這時 Application Passwords 區塊會出現

接著建 bot user：
- [ ] 同樣流程，Role 設為 **Contributor** 或自訂 role（不能改他人文章）
- [ ] Application Password 名：`nakama-chopper-community`
- [ ] 填入 `.env`：
  ```
  WP_FLEET_BASE_URL=https://fleet.shosho.tw/wp-json
  WP_FLEET_USERNAME=bot_chopper
  WP_FLEET_APP_PASSWORD=xxxx xxxx xxxx xxxx xxxx xxxx
  ```

**安全補強**（替代 Fluent Security 原本的保護）：
- bot user 不要給 Administrator role
- Application Password 有辨識度的名字（一有異常立即 revoke）
- Cloudflare WAF 對 `/wp-json/wp/v2/posts` 等寫入 endpoint 加 rate limit
- Franky 每日檢查 application password last_used，異常告警

**備註**：兩個 bot 帳號都要確認能走 REST API（HTTPS 一定要開，Application Password 不支援 HTTP）。

---

### 1c. Cloudflare WAF — 放行 `nakama-wordpress-client` UA（Bot Fight Mode bypass）

**為什麼需要**：`shared/wordpress_client.py` 從 VPS 打 `https://shosho.tw/wp-json/...` 走 httpx，預設 User-Agent 是 `python-httpx/x.y`，會被 Cloudflare Bot Fight Mode 擋下，回 HTTP 403 + `<title>Just a moment...</title>` 的 challenge HTML。client 端會誤判為 WP auth 403。對齊 PR #111 external probe 的 `nakama-external-probe/1.0` 模式：client 帶穩定 UA，CF 為這個 UA 加 skip rule。

**操作**：對 **shosho.tw** + **fleet.shosho.tw** 兩個 zone 各做一次（fleet 是同 zone subdomain，但 WAF rule 在 zone level 寫一次即可，所以實際只設定一次）：

- [ ] CF dashboard → 進 `shosho.tw` zone → **Security → WAF → Custom rules** → **Create rule**
  - Rule name: `Allow Nakama WordPressClient`
  - When incoming requests match：
    - Field: `User Agent` · Operator: `equals` · Value: `nakama-wordpress-client/1.0`
    - And Field: `URI Path` · Operator: `starts with` · Value: `/wp-json/`
    - And Field: `IP Source Address` · Operator: `equals` · Value: `202.182.107.202`（VPS IP — 三條 AND 收緊濫用面）
  - Then take action: **Skip** → 勾起 **All remaining custom rules** + **Bot Fight Mode** + **Super Bot Fight Mode**（依 zone 啟用情況勾相應的 bot 防護層）
  - Place at: **Top**（在其他 block rule 之前 evaluate）
- [ ] 存檔 → CF 提示 「Rule deployed」
- [ ] 驗證（VPS 上）：
  ```bash
  curl -sS -o /dev/null -w "%{http_code}\n" \
    -A "nakama-wordpress-client/1.0" \
    -u "$WP_SHOSHO_USERNAME:$WP_SHOSHO_APP_PASSWORD" \
    "https://shosho.tw/wp-json/wp/v2/posts?per_page=1"
  # 預期 200。若仍 403 + Just a moment HTML → CF rule order 沒對 / 或還沒生效（5-30s）
  ```

**操作後動作**：`git pull && systemctl restart thousand-sunny nakama-usopp` 拉到加 UA 的 wordpress_client.py 並重啟兩個 service。重啟前 service 仍跑舊版（無 UA），CF rule 即使設好也接不到流量。

---

## 2. Google Cloud — Search Console + Analytics 4 API

### 2a. 建立 GCP Service Account

- [ ] 到 `https://console.cloud.google.com/`
- [ ] 開新 project 或用既有的：**建議命名 `nakama-monitoring`**
- [ ] 啟用 API：
  - **Search Console API**
  - **Google Analytics Data API**
- [ ] IAM & Admin → Service Accounts → Create Service Account
  - **Step 1 (Service account details)**：
    - Service account name: `nakama-franky`
    - Service account ID: 自動生成，留預設
    - Description: `Nakama Franky monitoring agent - GSC + GA4 reader`
    - 按 **CREATE AND CONTINUE**
  - **Step 2 (Grant this service account access to project, OPTIONAL)**：
    - **整個跳過，不要選任何 role**
    - 理由：project-level role 權限太大；我們只要特定 GSC / GA4 property 的讀取權限，在 GSC / GA4 admin 個別授權（2b / 2c 步驟做）
    - 這個 section 留空，直接按 **CONTINUE**
  - **Step 3 (Grant users access to this service account, OPTIONAL)**：
    - **也整個跳過**
    - 直接按 **DONE**
- [ ] 建立完成後 → service account 列表點進去 → **KEYS** 頁籤 → **ADD KEY → Create new key → JSON** → 下載 JSON
- [ ] 存到 VPS `/home/nakama/secrets/gcp-nakama-franky.json`（chmod 600）
- [ ] `.env` 指定：
  ```
  GCP_SERVICE_ACCOUNT_JSON=/home/nakama/secrets/gcp-nakama-franky.json
  ```

### 2b. 授權此 Service Account 存取 GSC Property

- [ ] `https://search.google.com/search-console` → 選 `sc-domain:shosho.tw` property → Settings → Users and permissions → Add user
  - Email: `nakama-franky@<project-id>.iam.gserviceaccount.com`（service account email）
  - Permission: **Restricted**（只讀就夠）
- [ ] 對 `sc-domain:fleet.shosho.tw` 重複同步驟
- [ ] 確認：之後跑 `python -m agents.franky.gsc_test` 能列出兩個 property

**ADR-009 SEO solution 額外 env keys**（`shared/gsc_client.py` 消費）：

```
GSC_PROPERTY_SHOSHO=sc-domain:shosho.tw
GSC_PROPERTY_FLEET=sc-domain:fleet.shosho.tw
```

`GSC_PROPERTY_*` 是 GSC API `siteUrl` 字串。Domain property 用 `sc-domain:<domain>`，URL-prefix property 用 `https://<host>/`（含 trailing slash）。Service account 已在本 §2b 授權兩 property，env key 直接填即可，無需重新授權。

### 2c. 授權此 Service Account 存取 GA4 Property

- [ ] 你已在 GA4 追蹤 shosho.tw 嗎？（若無，先到 `https://analytics.google.com/` 開 property）
- [ ] GA4 → Admin → Property Access Management → Add users
  - Email: service account 的 email
  - Role: **Viewer**
- [ ] Admin → Property Settings → 記錄 **Property ID**（9 位數字）填入 `.env`：
  ```
  GA4_PROPERTY_SHOSHO=123456789
  GA4_PROPERTY_FLEET=987654321
  ```

### 2d. 啟用 Google Signals（才有 demographics）

- [ ] GA4 Admin → Data Collection and Modification → Data Collection → **Google signals data collection** → 開啟
- [ ] 開啟後等 24-48 小時資料才會出現
- [ ] 同意 Google 的 demographic data 使用條款

### 2e. PageSpeed Insights API Key（ADR-009 Phase 1.5 Slice D — `seo-audit-post`）

PageSpeed Insights API **不**走 service account，走獨立 API key（GCP Console 建立後直接拿到字串）。免費 25,000 req/day quota，本專案僅在 audit 體檢時觸發，遠遠用不完。

- [ ] 到 `https://console.cloud.google.com/apis/library` (用 §2a 同一個 `nakama-monitoring` project)
- [ ] 搜尋 **PageSpeed Insights API** → **ENABLE**
- [ ] APIs & Services → Credentials → **CREATE CREDENTIALS → API key**
- [ ] 複製生成的 key
- [ ] 點 **EDIT API KEY** 限制 key 使用範圍：
  - **Application restrictions**: HTTP referrers 留空 / 或選 None（VPS server 端呼叫）
  - **API restrictions**: Restrict key → 勾 **PageSpeed Insights API** 一項
- [ ] 填入 `.env`：
  ```
  PAGESPEED_INSIGHTS_API_KEY=AIzaSy...
  ```
- [ ] 驗證（D.2 skill ship 後）：`python -c "from shared.pagespeed_client import PageSpeedClient; print(PageSpeedClient().run('https://shosho.tw').get('lighthouseResult', {}).get('categories', {}).get('performance', {}).get('score'))"` 應印 0-1 之間 score

**消費者**：`shared/pagespeed_client.py` → `shared/seo_audit/performance.py`（P1 LCP / P2 INP / P3 CLS rule）。

---

## 3. Cloudflare API Token

- [ ] `https://dash.cloudflare.com/profile/api-tokens` → Create Token → Custom token
- [ ] Permissions（**只讀**，不給 edit）：
  - Account · Account Analytics · Read
  - Zone · Zone · Read
  - Zone · Analytics · Read
  - Zone · Logs · Read（若要看 attack logs）
- [ ] Zone Resources: Include → Specific zone → **shosho.tw**（只一條）
  - **註**：fleet.shosho.tw 是 shosho.tw 下的 subdomain，共用同一個 Cloudflare zone，不會在選單裡出現獨立 entry。Cloudflare zone 以 root domain 為單位，一個 zone 就涵蓋所有 subdomain。Franky 分別抓 shosho vs fleet 流量時透過 GraphQL `clientRequestHTTPHost` filter 分開。
- [ ] Client IP Address Filtering: 填 VPS 的 IP `202.182.107.202`（縮小濫用面）
- [ ] 複製 token，填入 `.env`：
  ```
  CLOUDFLARE_API_TOKEN=xxxxxxxx
  CLOUDFLARE_ACCOUNT_ID=<在 dashboard 右側 API 區塊看>
  CLOUDFLARE_ZONE_ID=<在 dashboard 右側 API 區塊看，shosho.tw 的 Zone ID>
  ```

---

## 4. Cloudflare R2（已在 xCloud 設定，僅確認）

- [ ] 確認 xCloud backup schedule 每日運作
- [ ] 從 xCloud 或 R2 dashboard 拿到 R2 credentials：
  ```
  R2_ACCOUNT_ID=...
  R2_ACCESS_KEY_ID=...
  R2_SECRET_ACCESS_KEY=...
  R2_BUCKET_NAME=<你的 bucket name>
  ```
- [ ] 填入 `.env`（Franky 只驗證備份存在，不寫入）

---

## 5. Bricks AI Studio（選配，建議裝）

**建議理由**：
- 一次性購買、無訂閱
- BYOK 模式可接 Claude API（用我們現有 `ANTHROPIC_API_KEY`，無額外 LLM 費用）
- 支援 screenshot → native Bricks JSON，是 Claude Design 產出（HTML/screenshot）→ 你的 Bricks template 的官方橋
- 單價 $0.01-0.25 / page，可忽略

**步驟**：
- [ ] `https://bricksaistudio.com/` 購買 license（launch pricing，之後會漲）
- [ ] 在 shosho.tw 裝 plugin
- [ ] License key 填入 WordPress Bricks AI Studio 設定頁
- [ ] BYOK 設定：Provider = Anthropic Claude，API Key = `ANTHROPIC_API_KEY`
- [ ] 暫時不在 fleet.shosho.tw 裝（community 側不需要）

---

## 6. Slack — Franky bot

**目標**：Franky 有獨立 bot，緊急 DM 修修。

- [ ] `https://api.slack.com/apps` → Create New App → From scratch
  - App Name: `Franky`
  - Workspace: 你的工作空間
- [ ] Bot icon 建議：Franky（草帽海賊團船工）圖片
- [ ] OAuth & Permissions → Bot Token Scopes：
  - `chat:write` (DM)
  - `im:write` (開啟 DM channel)
  - `users:read`（找你的 user ID）
- [ ] Install to Workspace → 拿 `xoxb-...` token
- [ ] Socket Mode 開啟（同 Nami 模式）
- [ ] Event Subscriptions：不用訂閱（單向 bot，只主動發訊息）
- [ ] `.env` 填：
  ```
  SLACK_FRANKY_BOT_TOKEN=xoxb-...
  SLACK_FRANKY_APP_TOKEN=xapp-...
  SLACK_USER_ID_SHOSHO=U0xxxxxx  # 你的 Slack user ID（Franky DM 目標）
  ```

（完整步驟請參考 [docs/runbooks/add-agent-slack-bot.md](add-agent-slack-bot.md)）

---

## 7. 驗證

建立完成後，我會跑以下驗證並回報：

```bash
python -m tests.verify_wp_integration        # WP × 2 site 連線 + SEOPress + media upload
python -m tests.verify_fluent_integration    # FluentCommunity REST + FluentCRM
python -m tests.verify_franky_credentials    # GSC + GA4 + Cloudflare + R2 readable
python -m tests.verify_slack_franky          # Franky bot 發出測試 DM 給你
```

驗證全部通過 → Phase 1 開工。

---

## 預計時間

| 項目 | 時間 |
|---|---|
| 2 個 WP bot 帳號 + App Password | 10 分鐘 |
| GCP Service Account + API 啟用 + GSC/GA 授權 | 30 分鐘 |
| Google Signals（資料生效要等 24-48 小時） | 2 分鐘 + 等 |
| Cloudflare API token | 10 分鐘 |
| R2 credentials 從 xCloud 撈出 | 5 分鐘 |
| Bricks AI Studio 購買 + 安裝 | 15 分鐘 |
| Slack Franky bot | 15 分鐘 |
| **合計** | **約 90 分鐘**（不含 Google Signals 等待） |
