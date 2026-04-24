# External Uptime Probe — GitHub Actions 設定

**Scope:** ADR-007 §2 VPS SPOF 的外部告警通道。GitHub Actions runner 每 5 分鐘
probe 3 個公網 URL，失敗時透過 Franky 既有 Slack bot token DM 修修。
**Owner:** 修修手動設兩個 GitHub repo secrets，一次性。
**執行條件:** Franky 已部署（SLACK_FRANKY_BOT_TOKEN 已存在）、公網 URL 可訪問。
**時間預估:** 10-15 分鐘（含驗收測試）。

---

## 為什麼不用 UptimeRobot

2026-04-24 實測 UptimeRobot free plan 三層坑（見
[feedback_uptimerobot_cost_benefit](../../memory/claude/feedback_uptimerobot_cost_benefit.md)）：
HEAD-default vs GET-only、keyword 特殊字元、CF Bot Fight Mode 擋 datacenter IP。
GitHub Actions runner 不在 CF bot list，零 CF 配置就跑得起來；Slack mobile push
繞過 VPS 夠用於個人創作者場景。

本方案 **sacrifice** 的是「VPS + CF Tunnel 同時死」這種 1-2 次/年的邊緣案例的
SMS fallback（因為 Slack 仍依賴 GitHub → Slack 路徑）。若未來真的需要繞過全部第三方
的 SMS fallback，再啟用 UptimeRobot paid plan 或類似服務。

---

## Probe 設定摘要

| Target | URL | 通過條件 |
|---|---|---|
| nakama-gateway-healthz | `https://nakama.shosho.tw/healthz` | HTTP 200 + body 含 `nakama-gateway` |
| shosho-tw-homepage | `https://shosho.tw/` | HTTP 200 |
| fleet-shosho-tw-homepage | `https://fleet.shosho.tw/` | HTTP 200 |

- **Schedule:** 每 5 分鐘（GitHub cron 延遲可能 5-15 分鐘，見 notes）
- **Timeout:** curl `--max-time 30`，job 3 min
- **Alert policy:** DOWN-only，每 5 分鐘持續 DOWN 會持續送 DM（無 state tracking）
- **Keyword 選擇依據:** `nakama-gateway` 取自
  `shared/schemas/franky.py:95` 的 `service: Literal["nakama-gateway"]` 契約。
  schema 改版（換名 / 移除欄位）要同步更新 workflow matrix 的 `keyword`。

---

## 設定步驟（修修手動）

### 1. 確認 Franky secrets 已存在

從 VPS 的 `/home/nakama/.env` 取值（或從桌機 password manager）：

- `SLACK_FRANKY_BOT_TOKEN` — `xoxb-...` 開頭
- `SLACK_USER_ID_SHOSHO` — `U07XXXXXXX` 格式

Franky 已用這組，不重新建 Slack app。

### 2. 設定 GitHub repo secrets

在 repo root 執行（需先 `gh auth login`）：

```bash
# 貼 Franky bot token
gh secret set SLACK_FRANKY_BOT_TOKEN

# 貼 Shosho 的 Slack user ID（U 開頭那串）
gh secret set SLACK_USER_ID_SHOSHO
```

或者從 GitHub UI：Repo → Settings → Secrets and variables → Actions → New repository secret

驗證：

```bash
gh secret list | grep -E "SLACK_FRANKY_BOT_TOKEN|SLACK_USER_ID_SHOSHO"
```

應看到兩行 + updated 時間戳。

### 3. 確認 workflow 已 merge 到 main

```bash
gh workflow list | grep "External Uptime Probe"
```

應輸出類似：

```
External Uptime Probe  active  <ID>
```

若看不到，checkout main 確認 `.github/workflows/external-probe.yml` 存在，
且 branch 已 merge（`on: schedule` 只在 default branch 啟用）。

### 4. 撤掉 UptimeRobot（若之前有設）

若 2026-04-24 deprecate 前已在 UptimeRobot 建過 monitor：

1. 登入 https://uptimerobot.com/
2. Dashboard → 三個 monitor 全勾 → **Pause**（保留設定但停止戳）
3. 或直接 **Delete** 釋放 monitor 額度

無需改動 CF WAF skip rule（之前若為 UptimeRobot 設過，保留無害）。

---

## 驗收測試

### A. Workflow YAML 有效性

```bash
gh workflow list
```

應看到 `External Uptime Probe` 為 `active`。

### B. Happy path — 手動跑一次

```bash
gh workflow run external-probe.yml
```

等約 15-30 秒後：

```bash
gh run list --workflow=external-probe.yml --limit 1
```

應顯示 `completed  success`。進 run 頁面：

```bash
gh run view --log --workflow=external-probe.yml
```

看到三個 matrix job（`nakama-gateway-healthz` / `shosho-tw-homepage` /
`fleet-shosho-tw-homepage`）都綠燈，每個都有 `Probe OK: ...` 行。

### C. Simulate DOWN — Slack alert path 測試

```bash
gh workflow run external-probe.yml -f simulate_down=true
```

預期：

- `nakama-gateway-healthz` matrix job 失敗
- 另外兩個 probe 照常跑，成功
- 修修的 Slack DM 收到 `:rotating_light: External probe DOWN — nakama-gateway-healthz` 訊息
- 訊息含：URL、HTTP 000、Reason、UTC + Taipei 時間、workflow run 連結

若 Slack DM 沒到，檢查：

1. `gh secret list` 兩個 secret 是否都在
2. run log 的「Slack DM on failure」步驟是否有 `::error::` 行
3. `SLACK_FRANKY_BOT_TOKEN` 有沒有過期或被 revoke（Slack app → OAuth & Permissions → Install to Workspace）

### D. 排程是否有在跑

Workflow merge 進 main 後 5-10 分鐘內，scheduled run 會開始出現：

```bash
gh run list --workflow=external-probe.yml --limit 5
```

三個 run 以上都綠 = 上線成功。

---

## 撤掉或停用

若要完全停用 probe：

```bash
gh workflow disable external-probe.yml
```

重新啟用：

```bash
gh workflow enable external-probe.yml
```

---

## Notes / 踩坑

- **GitHub cron 延遲**：`*/5 * * * *` 在公有 runner 滿載時可能延遲 5-15+ 分鐘。
  不是 SLA，只是「大約每 5 分鐘」。若需要 ≤1 min 檢查頻率，本方案不適用。
- **60 天不活躍自動停用**：repo 若 60 天無 commit，scheduled workflows 會被
  GitHub 停用，要手動 `gh workflow enable` 重新啟用。Nakama repo 活躍度高，不會踩到。
- **UTC-only cron**：GitHub cron 不支援時區。目前每 5 分鐘跑所以無差，未來若要改
  「上班時段密集、深夜低頻」要自己算 UTC offset。
- **Simulate down 只戳 nakama-gateway-healthz**：測試其他兩個 URL 的 failure path
  需要手動 mock（例如 checkout branch 改 matrix URL 為不存在的 host）。Phase 1 暫不做。
- **每 5 分鐘重發**：持續 DOWN 會每 5 分鐘送一次 DM，flooding 可接受。若未來要加
  state tracking（只在 state transition 時發），用 `actions/cache` 存 last_state.json
  或 issue labels 代替；Phase 1 不做。

---

## 不做（明確 out of scope）

- State tracking / flapping detection
- Maintenance window（VPS weekly patch 不會每週精確同一時段，先看噪音再說）
- Status page（private / public 都不做，Phase 1 修修自己看 GH Actions tab）
- SMS fallback（UptimeRobot paid 或 Twilio，真的需要再啟用）
- 外部 probe uptime % 匯入 Franky weekly digest（等有幾週資料再做）
