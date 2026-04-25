# Secret Rotation — Nakama 全 secret 輪替流程

**Scope:** VPS `.env` 內所有 secret 的 rotation 流程：每個 secret 怎麼產新的、怎麼換上線、怎麼驗證、緊急 compromise 時的優先序。
**Owner:** 修修手動 ssh + 各 SaaS console 操作。
**Cadence:** 每季 (3 個月) 例行 rotation；compromise 立即 rotation。

---

## 1. Rotation 觸發條件

| 觸發 | 動作 | 緊急度 |
|------|------|-------:|
| 例行季度 rotation | 走 §3 per-secret 流程，每季全清一輪 | 低 |
| 疑似洩漏（commit / log / 截圖） | §4 emergency rotation 全清 | 高 |
| 員工 / contributor 離職 | 與該人共享過的 secret 全清 | 高 |
| GH secret scanning alert | 那個 secret 立即清，順便排查 git history | 高 |
| API quota 異常飆升 | 當 compromise 處理 + 看 audit log | 高 |

**Compromise 時的順序原則**：先 revoke（讓舊 key 失效）→ 再產新 key → 再上 VPS → 最後 smoke。
中間 service 短暫無法 call 該 API 是可以接受的代價。

---

## 2. Secret inventory（定價排序）

| 類別 | Env 變數 | 攻擊代價 / quota | 哪裡 rotate |
|------|---------|----------------|-------------|
| 🔴 LLM | `ANTHROPIC_API_KEY` | $5+ / 萬 token | console.anthropic.com → API Keys |
| 🔴 LLM | `XAI_API_KEY` | quota burn | console.x.ai → API Keys |
| 🔴 LLM | `GEMINI_API_KEY` | quota burn | aistudio.google.com → API keys |
| 🟡 LLM | `FIRECRAWL_API_KEY` | quota burn | firecrawl.dev dashboard |
| 🟢 LLM read-only | `PUBMED_API_KEY` | rate limit only | ncbi.nlm.nih.gov/account |
| 🟢 LLM read-only | `YOUTUBE_API_KEY` | quota only | console.cloud.google.com |
| 🟢 LLM read-only | `TWITTER_BEARER_TOKEN` | rate limit | developer.twitter.com |
| 🔴 Slack | `NAMI_SLACK_BOT_TOKEN` / `NAMI_SLACK_APP_TOKEN` | 控帳號發訊息 | api.slack.com → Apps → Nami |
| 🔴 Slack | `SANJI_*` / `ZORO_*` | 同上 | 同上，各自 app |
| 🔴 Google | `data/google_oauth_credentials.json` | OAuth client | console.cloud.google.com |
| 🔴 Google | `data/google_gmail_token.json` | 收發 Gmail | 重跑 `scripts/google_gmail_auth.py` |
| 🔴 Google | `data/google_calendar_token.json` | 改 Calendar | 重跑 `scripts/google_calendar_auth.py` |
| 🟡 Google | `GCP_SERVICE_ACCOUNT_JSON` | GSC + GA4 read | console.cloud.google.com → IAM → service accounts |
| 🔴 WordPress | `WP_SHOSHO_APP_PASSWORD` | 改 / 刪 / 發文 | shosho.tw/wp-admin → Users → bot_usopp → App Passwords |
| 🔴 WordPress | `WP_FLEET_APP_PASSWORD` | 同上（fleet 站） | fleet.shosho.tw/wp-admin |
| 🟡 R2 | `R2_*` / `NAKAMA_R2_*` | 讀 / 寫 backup | dash.cloudflare.com → R2 → API Tokens |
| 🟡 Web | `WEB_PASSWORD` + `WEB_SECRET` | Bridge UI 登入 | 自己改 .env |
| 🟢 SMTP | `SMTP_USER` / `SMTP_PASS` | 寄 notification | 對應 SMTP provider |

🔴 高敏感（compromise 立即 rotate）/ 🟡 中（quota 風險為主）/ 🟢 低（rate-limit only）

---

## 3. Per-secret rotation 流程

### 3.1 Anthropic / xAI / Gemini API key

```bash
# 1. Console 上 create new key（保留舊 key 暫不 revoke）
# 2. 桌機本機 .env 改新 key + smoke 一次：
python -c "from shared.anthropic_client import client; print(client.messages.create(model='claude-haiku-4-5', max_tokens=10, messages=[{'role':'user','content':'ping'}]).content[0].text)"

# 3. 上 VPS（用 deploy-usopp-vps.md §3 的 diff-append 法）
ssh nakama-vps 'cp /home/nakama/.env /home/nakama/.env.bak.$(date +%Y%m%d_%H%M%S)'
# 在 VPS 直接 sed -i "s/^ANTHROPIC_API_KEY=.*/ANTHROPIC_API_KEY=新值/" /home/nakama/.env
# 或 scp 後手動 edit

# 4. Restart services that consume this key
ssh nakama-vps 'systemctl restart thousand-sunny nakama-gateway nakama-usopp'
journalctl -u thousand-sunny -n 30 --no-pager   # 確認啟動沒錯誤

# 5. 確認新 key 工作（Bridge UI 隨便 trigger 一個 LLM call，例如丟 Slack DM 給 Nami）
# 6. 24h 後 console revoke 舊 key（舊 key 殘留 cache 都過了再 revoke 才安全）
```

### 3.2 Slack bot tokens（Nami / Sanji / Zoro）

⚠️ Slack bot token 不能直接「rotate」— 要重新 install app 或 reinstall to workspace。

```
1. api.slack.com → Apps → 該 agent → OAuth & Permissions
2. "Reinstall App" → 拿到新的 xoxb-...
3. 同時去 Basic Information → App-Level Tokens → revoke 舊的、create 新的 xapp-...
4. 上 VPS 換 .env 的 4 個 *_SLACK_BOT_TOKEN / *_SLACK_APP_TOKEN
5. systemctl restart nakama-gateway
6. Slack DM 該 bot 確認響應
```

### 3.3 Google OAuth tokens（Gmail / Calendar）

OAuth refresh token 不容易直接 revoke，但可走 Google account 移除 app 授權：

```
1. myaccount.google.com → Security → Third-party apps with account access
2. 找 "Nakama" / "Nami" 相關 app → Remove access
   （這會讓所有 refresh token 失效）
3. 桌機端重跑 OAuth flow:
   python scripts/google_gmail_auth.py
   python scripts/google_calendar_auth.py
4. 確認 data/google_*_token.json 是新的（看檔案 mtime）
5. scp 上 VPS:
   scp data/google_gmail_token.json nakama-vps:/home/nakama/data/
   scp data/google_calendar_token.json nakama-vps:/home/nakama/data/
6. systemctl restart nakama-gateway
7. Slack DM Nami: "查信" / "看行事曆" 驗證
```

### 3.4 Google Service Account JSON

```
1. console.cloud.google.com → IAM & Admin → Service Accounts → nakama-franky
2. Keys tab → Create new key → JSON → 下載
3. 桌機本機放到 ~/.config/nakama/gcp-nakama-franky-NEW.json
4. 改本機 .env GCP_SERVICE_ACCOUNT_JSON 路徑
5. 跑 SEO smoke（python -m skills.seo_keyword_enrich 對任一關鍵字）
6. scp 新 JSON 上 VPS:
   scp ~/.config/nakama/gcp-nakama-franky-NEW.json nakama-vps:/home/nakama/data/gcp-nakama-franky.json
7. ssh VPS 改 .env 路徑（如有不同）
8. systemctl restart thousand-sunny
9. console 上 disable 舊 key（保留 7 天再刪，避免 race condition）
```

### 3.5 WordPress app password

```
1. shosho.tw/wp-admin → Users → bot_usopp → 滑到底 Application Passwords
2. 給舊 password 點 "Revoke"（立即生效）
3. 同頁建新 application password，name = "nakama-usopp-rotated-YYYYMMDD"
4. 複製新 password（含空格的形式 `xxxx xxxx xxxx xxxx xxxx xxxx`）
5. 桌機本機 .env 改 WP_SHOSHO_APP_PASSWORD
6. smoke: python -c "from shared.wordpress_client import WordPressClient; print(WordPressClient.from_env('shosho').list_categories()[:3])"
7. 上 VPS:
   ssh nakama-vps "sed -i 's/^WP_SHOSHO_APP_PASSWORD=.*/WP_SHOSHO_APP_PASSWORD=<新值含空格>/' /home/nakama/.env"
   # 或手動 edit
8. systemctl restart nakama-usopp thousand-sunny
9. journalctl -u nakama-usopp -f  # 看下次 cycle 沒有 401
```

`WP_FLEET_APP_PASSWORD` 同流程，WP-admin 走 fleet.shosho.tw。

### 3.6 R2 API tokens

```
1. dash.cloudflare.com → R2 → Manage R2 API Tokens → 找舊 token "nakama-write" / "nakama-read"
2. Create API token：
   - 名字：nakama-write-YYYYMMDD
   - Permissions: Object Read & Write (寫的 token) 或 Object Read (Phase 2 讀 token)
   - Specify bucket: nakama-backup（限制 scope）
   - TTL: leave blank（手動 rotate）
3. 拿到新 access_key_id + secret_access_key
4. 桌機 .env 改 NAKAMA_R2_ACCESS_KEY_ID / NAKAMA_R2_SECRET_ACCESS_KEY
5. smoke:
   python -c "from shared.r2_client import R2Client; print(R2Client.from_nakama_backup_env().list_objects(prefix='state/', max_keys=3))"
6. 上 VPS:
   ssh nakama-vps "sed -i 's/^NAKAMA_R2_ACCESS_KEY_ID=.*/NAKAMA_R2_ACCESS_KEY_ID=新/' /home/nakama/.env"
   ssh nakama-vps "sed -i 's/^NAKAMA_R2_SECRET_ACCESS_KEY=.*/NAKAMA_R2_SECRET_ACCESS_KEY=新/' /home/nakama/.env"
7. 手動跑一次 backup 確認:
   ssh nakama-vps 'cd /home/nakama && python3 scripts/backup_nakama_state.py'
8. CF dashboard revoke 舊 token
```

`R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY`（Franky verify 用）同流程，scope 是 xcloud-backup bucket。

### 3.7 Web auth（Bridge 登入）

```bash
# 1. 產新值
python -c "import secrets; print('WEB_PASSWORD=' + secrets.token_urlsafe(24))"
python -c "import secrets; print('WEB_SECRET=' + secrets.token_hex(32))"

# 2. 上 VPS 換 .env 兩個值
# 3. systemctl restart thousand-sunny
# 4. 桌機瀏覽器重新登入 nakama.shosho.tw/login
```

### 3.8 SMTP / 其他

各家 provider 改密碼 → 上 VPS .env → restart 所有需要 notification 的 service。

---

## 4. Emergency rotation（compromise 響應）

**徵兆**：API quota 暴增 / GH secret scanning / 截圖外洩 / log 出現非預期 origin。

### Triage 順序（10 分鐘內動完）

```
1. 立即停外發 service:
   ssh nakama-vps 'systemctl stop nakama-usopp nakama-gateway'
   thousand-sunny 可繼續跑（純 web，不發外）

2. 在所有 SaaS console 立即 revoke 該 secret（不等新 key produce）：
   - Anthropic / xAI / Gemini console
   - Slack apps (3 個)
   - WordPress wp-admin
   - Cloudflare R2 dashboard
   - Google OAuth (myaccount.google.com)

3. 看 audit log（誰 use 了什麼，量多大）：
   - Anthropic: console → Usage
   - Google: cloud.google.com → IAM → audit logs
   - WordPress: wp-admin → Users → bot_usopp → 看最近活動 (需 plugin)

4. 查 git history 看 secret 是不是在 commit 過:
   git log -p | grep -E '(sk-ant-|xoxb-|xapp-)'
```

### 重新發 key（30 分鐘內全清）

按 §3 流程逐個 secret 走，但全部跑一次（不只該洩漏的那一個）— 因為相同的 leak 路徑可能洩漏其他 secret。

### 善後

- 在 vault `Incidents/leak-YYYY-MM-DD-<short-name>.md` 寫 postmortem
- 加 GH branch protection / pre-commit `detect-secrets` 等預防（屬 quality-uplift Phase 9）
- 必要時 git filter-branch / BFG 清掉 history（謹慎，會 rewrite history → force push）

---

## 5. Quarterly rotation checklist

每季初（1 月 / 4 月 / 7 月 / 10 月）跑一次：

```markdown
# Quarterly Secret Rotation — YYYY QX

- [ ] Anthropic API key
- [ ] xAI API key
- [ ] Gemini API key
- [ ] Firecrawl API key
- [ ] Slack Nami bot+app token
- [ ] Slack Sanji bot+app token
- [ ] Slack Zoro bot+app token
- [ ] WP shosho app password
- [ ] WP fleet app password
- [ ] R2 write token
- [ ] R2 read token (Franky)
- [ ] WEB_PASSWORD + WEB_SECRET
- [ ] Google sa JSON (semi-annual is OK for this — cost of rotation higher)
- [ ] Google OAuth tokens (revoke old → re-auth)

預估時間：3-4 小時 dedicated。
```

---

## 6. Backup of secrets themselves

**問題**：VPS 整台壞時，`.env` 不在 R2 backup（敏感性太高）— 怎麼還原？

**目前方案**：
- 修修個人 1Password vault 保留一份 `.env` 的 working copy
- 每次 rotation 後同步更新 1Password
- DR 還原時從 1Password 拉出來 scp 上新 VPS

**未來升級路徑**（不在 Phase 1）：
- Cloudflare Secret Manager / 1Password CLI 集成（自動化 secret pull）
- Hashicorp Vault（複雜度過高，1 機 stack 不必）

**目前限制**：1Password 同步全靠修修記得手動更新。Phase 9 會在 deploy.yml 加 hook：每次 .env 變更觸發 reminder。

---

## 相關

- [`disaster-recovery.md`](disaster-recovery.md) — DR 主 runbook，secret 還原步驟引用本檔
- [`docs/plans/quality-bar-uplift-2026-04-25.md`](../plans/quality-bar-uplift-2026-04-25.md) — Phase 1 範圍
- [`feedback_env_push_diff_before_overwrite.md`](../../memory/claude/feedback_env_push_diff_before_overwrite.md) — `.env` push 不要整份覆蓋
- [`feedback_no_secrets_in_chat.md`](../../memory/claude/feedback_no_secrets_in_chat.md) — secret 不在對話框輸入
