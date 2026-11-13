# ADR-071：自由艦隊遊戲化拆成獨立 repo（freedom-fleet）

- **Status**：Accepted（修修 2026-11-09 裁決「選項 A，拆成獨立 repo」；VPS 已於同日切換並通過 24h 觀察）
- **Date**：2026-11-09（裁決、Phase 1–2 執行）／2026-11-10（Phase 3 觀察通過、本移除 PR）
- **Owner**：修修
- **Amends**：2026-08-22「不開第二條 deploy 路、Sanji 部署一律走 `scripts/deploy_vps.sh`」的裁決（該裁決的前提是單一 repo；拆 repo 後每個 repo 各一條部署腳本）
- **Related**：[執行計畫](../plans/2026-11-09-freedom-fleet-repo-split.md)；新 repo `shosho-chang/freedom-fleet`（private），其 `HISTORY.md`、`docs/runbooks/vps-cutover.md`、issue #1

---

## 一句話

自由艦隊的遊戲化系統（Sanji 記分服務 ＋ WordPress plugin `fleet-gamification`）從 nakama 搬到獨立 repo
`shosho-chang/freedom-fleet`；nakama 只留 Slack 上的 Sanji 人格，以及「`fleet.shosho.tw` 是修修的其中一個網站」這層站務（SEO / GSC / 外部探針）。

## 為什麼

遊戲化系統有自己的帳本（WP MySQL）、自己的 plugin、自己的部署節奏與 production 風險面，
和 nakama 的內容產線沒有程式相依（nakama 核心沒有任何一處 import Sanji 的遊戲化程式）。
放在同一個 repo，等於每次 nakama 部署都有機會誤重啟它，反過來也一樣。

## 裁決表（原樣來自執行計畫 §2）

| # | 裁決 | 理由 |
|---|---|---|
| **D1** | 新 repo **不做歷史改寫**（不跑 `git filter-repo`），第一個 commit 是搬入的檔案；新 repo `HISTORY.md` 記「2026-11-09 自 shosho-chang/nakama 搬入，歷史見該 repo `git log -- agents/sanji wp/fleet-gamification`」 | nakama pack 1.76 GiB，filter-repo 要 fresh clone 且易錯；歷史永遠留在 nakama，用連結即可。 |
| **D2** | **Slack 上的 Sanji 人格留在 nakama**（`gateway/handlers/sanji.py` ＋ `prompts/sanji/persona.md` ＋ router/orchestrator/bot 相關行） | 它是 Slack 海賊團群聊的一員，吃的是 nakama 的 LLM facade 與記憶，跟遊戲化程式零相依；拔掉會動到 router/orchestrator/bot 十幾個測試。日後若要讓它從新 repo 讀帳本，再開 ADR。 |
| **D3** | 新 repo **不依賴 nakama package**；用到的 shared 函式以**最小複本**放進 `fleet/shared/` | 兩個 repo 之後各自演化；Sanji 只用 8 個函式，複本總量 < 300 行。 |
| **D4** | 告警走 **Slack Incoming Webhook**（env `FLEET_ALERT_SLACK_WEBHOOK`），不再經 Franky bot | nakama 的 `alert()` 綁 Franky slack bot 與 incident archive，搬不動；webhook 零依賴。 |
| **D5** | 新 repo 用自己的 SQLite `data/fleet.db`；cutover 時把 `sanji_*` 三張表從 nakama `state.db` **複製**過去（舊表不刪，留稽核軌跡與 rollback） | cursor 掉了會重掃事件（WP 端 idempotency 會擋重複入帳，但 pending judgments 會丟）。 |
| **D6** | VPS：新 checkout `/home/freedom-fleet`，新 unit `fleet-sanji.service` ＋ `fleet-sanji-reconcile.{service,timer}`；舊 `nakama-sanji*` **stop + disable，不刪**，7 天後再清 | 可一分鐘 rollback。 |
| **D7** | 新 repo 自帶 `scripts/deploy.sh`；邏輯從 nakama `scripts/deploy_vps.sh` **對應區塊**抄，不重新發明 | 2026-08-22 的「不開第二條 deploy 路」是單一 repo 前提；拆 repo 後每 repo 一條是新前提。 |
| **D8** | Python package 名 `fleet`；佈局 `fleet/sanji/`、`fleet/shared/`、`wp/fleet-gamification/`、`tests/`、`docs/`、`memory/` | 與域名 `fleet.shosho.tw`、plugin 名一致；未來角色放 `fleet/<name>/`。 |
| **D9** | 新 repo **private** | 含會員系統營運細節與 vendor 內部行號。 |
| **D10** | 順序：先建好新 repo 並在本機全綠 → VPS 平行安裝並切換 → 跑 24h → 才合併 nakama 的移除 PR | nakama 移除 PR 一合併 `deploy_vps.sh` 就不再管 Sanji；要確定新線已接手。 |

## 執行狀況（事實，來源：freedom-fleet issue #1 與 runbook）

- 2026-11-09 10:50:20 `nakama-sanji` 停止並 disabled；10:50:40 `fleet-sanji`（`/home/freedom-fleet`）啟動。
- 2026-11-09 12:06 手動 reconcile 結束碼 0；2026-11-10 05:00 自動對帳成功（`balances mismatches 0`），無 traceback、無對帳落差告警。
- 切換後 `fleet-sanji` 處理了真實事件並發分（WP `grants` id 3723–3726，事件編號與切換前連續，無重複發分）。

## 本 PR（Phase 4）的範圍

- 刪：`agents/sanji/`、`wp/`、Sanji 遊戲化的 5 個測試檔與 `tests/shared/test_agent_sdk.py` 的兩個 judge 測試、三份遊戲化文件、四個 fleet 記憶檔。
- 改：`scripts/deploy_vps.sh`（移除 nakama-sanji 與 plugin 同步區塊）、`config.yaml`（`agents.sanji.wordpress` 子區塊）、`CONTEXT-MAP.md`、`ARCHITECTURE.md`、`README.md`、`docs/agents/domain.md`、`memory/claude/MEMORY.md`。
- **不動**：`gateway/handlers/sanji.py`、`prompts/sanji/`、`shared/llm_router.py` 的 `sanji` ModelSite、Bridge 船員卡、任何 `fleet.shosho.tw` 站務引用（SEO / GSC / 探針 / Nami / Franky 提示詞）。

## 後果

- nakama 的 `deploy_vps.sh` 此後只管 thousand-sunny、nakama-gateway、nakama-usopp。
- VPS 上舊的 `nakama-sanji*` unit 檔暫時還在（disabled），7 天後（最早 2026-11-16）經修修核准再 `rm` ＋ `daemon-reload`（Phase 5）。
- nakama `state.db` 的 `sanji_*` 三張表**留著不刪**（稽核軌跡）。
- 搬走前的完整歷史永遠留在 nakama：`git log -- agents/sanji wp/fleet-gamification`。
