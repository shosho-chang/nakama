# ADR-070 panel review（2026-09-24）

審查對象：[ADR-070](../decisions/ADR-070-llm-two-lanes-subscription-and-openrouter.md) v1（commit 5e51a37e）。
方法：`multi-agent-panel` skill。三個 subagent 各自冷讀 ADR，看不到撰寫時的對話脈絡，每人負責一個角度：

| Reviewer | Model | 角度 |
|---|---|---|
| A | Fable | 對抗式架構批判（一年後、十倍規模、失效模式） |
| B | Opus | 事實查核與可行性（實際核對 40 個以上的引用） |
| C | Sonnet | 營運現實（回滾、維護負擔、測試、修修要親手做的事） |

**限制**：三個 reviewer 都是 Claude，會共享同一類盲點。這次 review 的價值主要來自「沒有對話脈絡的冷讀」和「角度分工」。

## 主執行緒查證（採納前逐項核對）

| Reviewer 說法 | 查證 | 結果 |
|---|---|---|
| SDK 有結構化的額度訊號 | `claude_agent_sdk/types.py:1008-1035`（`AssistantMessageError` 含 `billing_error`、`rate_limit`）、`:1276-1307`（`RateLimitEvent`；`rate_limit_type` 有 `five_hour`、`seven_day`、`seven_day_opus`、`seven_day_sonnet`、`overage`；另有 `resets_at`、`overage_status`） | ✓ |
| `can_use_tool` 會被 allow 規則或 `permission_mode` 蓋掉 | `types.py:1931-1947` docstring 原文 | ✓ |
| 用 `asyncio.wait_for` 逾時可能漏殺子進程 | `_internal/transport/subprocess_cli.py:941-950` | ✓ |
| 內附 CLI 把 `opus` 解析成 `claude-opus-5` | 內附 `claude.exe` 2.1.226；binary 內有 `latest_per_family:{…opus:"claude-opus-5"…}`；`claude-opus-5-5` 出現 0 次 | ✓ |
| CLI 讀 `CLAUDE_CODE_MAX_OUTPUT_TOKENS` | binary 內出現 10 次 | ✓ |
| OpenRouter 上登記了 Anthropic BYOK key | `docs/runbooks/openrouter-canary.md:9` 已勾選 | ✓ |
| 在 async 函式裡同步呼叫 LLM | `gateway/handlers/nami_tools.py:43-44`（`async def _handler` → `handler._execute_tool`）；`thousand_sunny/routers/bridge_digests.py:169,191`（`async def digest_ask_post` → `ask(...)`） | ✓ |
| `alert()` 沒給 `dedupe_key` 就不會去重 | `shared/alerts.py:58,78` | ✓ |
| `/bridge/models` 依賴 `openrouter_enabled` 和 `get_auth_policy` | `thousand_sunny/routers/bridge_models.py:44-68` | ✓ |
| 綁著 `AUTH_*` / `LLM_TRANSPORT` / `subscription_*` 的測試檔 | `git grep -l` 找到 21 支（C 說約 25） | ✓（數量修正為 21） |
| VPS 和桌機各有自己的 `state.db` | VPS `/home/nakama/data/state.db`（`config.yaml:2`）；桌機 `E:\nakama\data\`（`.env` 的 `DB_PATH`） | ✓ |
| `requirements.txt` 版本限制在第 86 行 | 已看過 | ✓ |
| Structured output 只接受 JSON Schema draft-07 | 出自官方文件（B 引用），主執行緒未另行驗證 | 採信文件，S0 實測 |
| Opus 被 biology 分類器攔下時會自動改用較舊的 Opus | 出自官方文件（B 引用），主執行緒未另行驗證 | 列為風險，記錄實際 model |

## 整合矩陣

| # | 主題 | v1 立場 | A | B | C | 型態 | 處置 |
|---|---|---|---|---|---|---|---|
| 1 | 併發上限 | 每個 process 一個 semaphore | ✗ 管不到機器層級（F-6a） | ✗ 同上，而且要用 `threading` 版 | ✗ 同上（#2） | **三人一致** | 採納：改成機器層級上限（`state.db` lease） |
| 2 | 額度用完的偵測 | 錯誤分類 + 暫行啟發式規則，觸發後 fail-fast | ✗ 假陽性、假陰性都有（F-3） | ✗ SDK 已有結構化訊號（U1 寫錯了） | — | 兩人 + 查證 | 採納：主條件改用 `RateLimitEvent` / `AssistantMessage.error`；啟發式規則只發 DM，不 fail-fast |
| 3 | 自動切回 | 每 30 分鐘打一次 Haiku 探針 | ✗ 會來回震盪、Opus 與 Haiku 額度分開算（F-2） | ✗ 同上 | 建議交給 Franky 的 probe | **三人一致** | 採納：依 `resets_at` 排程，探失敗的那個 model family，由 Franky 負責 |
| 4 | Lane 狀態存在哪裡 | `state.db` 裡一列 | ✗ 兩台機器各一份，Q1 做不到（F-1 BLOCKER） | — | — | 單人 + 查證 | 採納：只存在 VPS，桌機透過 Bridge API 讀 |
| 5 | OpenRouter 模式的路由 | 只換 SDK 子進程 env | ✗ 失去 BYOK / allow_fallbacks、沒有實際 cost、容易腐爛（F-4） | ✗ 會先扣 Anthropic BYOK key、拿不到實際 cost | — | 兩人 + 查證 | 採納：文字呼叫走 L2 client，agentic 呼叫才換 env；每天跑一次 canary；key 缺失時 fail closed |
| 6 | 核准切 OpenRouter 的花費上限 | 沒有 | ✗ 按一次就授權所有批次照 API 價跑（F-5） | — | — | 單人 | **修修決定**：每次核准帶 USD 上限（建議值見 ADR） |
| 7 | 別名 = 最新版 | `opus` 等於最新 | ✗ 跟 R5 釘死版本衝突（F-7） | ✗ 解析成內附 CLI 認得的最新版 | — | 兩人 + 查證 | **修修決定**：`opus` 目前實際是 Opus 5，不是 5.5 |
| 8 | RenderWatcher 的寫入範圍 | 用 `can_use_tool` 擋 | ✗ Bash 繞得過（F-9） | ✗ 會被 allow 規則蓋掉 | — | 兩人 + 查證 | 採納：`PreToolUse` hook + `setting_sources=[]`；承認 script 寫檔擋不住，靠事後驗證 |
| 9 | 同步 `ask()` 在 async 函式內呼叫 | 未提 | ✗ `asyncio.run` 會炸（F-6b） | ✗ 已重現，並指出具體呼叫點 | — | 兩人 + 查證 | 採納：偵測到有 loop 在跑，就改丟到專用 thread 執行；巢狀呼叫不佔併發名額 |
| 10 | 回滾 | 沒寫 | — | — | ✗ BLOCKER：拆掉了秒級 kill-switch（#1） | 單人 | 採納：新增「回滾」一節（逐 PR revert + lane 手動覆寫 CLI） |
| 11 | 重用現有設施 | 新開探針 + 新頁面 | 探針交給 Franky（F-12） | — | Franky probe + approval_queue（#3） | 兩人 | 採納：探針交給 Franky；決策 UI 先評估能不能套 approval_queue |
| 12 | `/bridge/models` | 沒提 | — | — | ✗ 依賴要刪的函式（#4） | 單人 + 查證 | 採納：排進 S1 |
| 13 | 既有測試 | 沒提 | — | — | ✗ 約 25 支（#5） | 單人 + 查證（21 支） | 採納：S1 列出清單並逐支改寫 |
| 14 | 修修本人的使用也吃同一份額度 | 沒提 | ✗ 額度用完會是常態（F-8） | — | cron 在凌晨，跟即時對話重疊少 | 部分分歧 | 採納為風險 R9；用 `allowed_warning` 訊號讓批次先讓路 |
| 15 | 「每次都問」vs「自動切」是假二分 | Option B vs D | 提出第三條路：分類別、有上限的常備授權 | — | — | 單人，與 D-c 字面衝突 | **修修決定**（不自行採納） |
| 16 | 憑證清理 | 只清 `ANTHROPIC_API_KEY` | 每台機器各用一個 token（F-10） | ✗ `ANTHROPIC_AUTH_TOKEN`、雲端 provider 旗標也要清 | 桌機的 token 沒盤點（#6） | 三人互補 | 採納：清掉全部 Anthropic 認證相關 env；每台機器一個 token；S0 盤點桌機 |
| 17 | 重試會放大子進程數 | 沒提 | ✗（F-11） | — | — | 單人 | 採納：`SubscriptionExhausted` 標為不可重試 |
| 18 | 成功指標 | 「0 筆走 OpenRouter」 | ✗ 指標量錯東西（F-13） | — | — | 單人 | 採納：加「每週停在等待決策狀態的時數」 |
| 19 | 事實精確度 | — | — | 多處行號偏移、F1/F7/F9/F11/F12/F13 措辭 | — | 單人 + 查證 | 採納：逐項修正 |
| 20 | Codex semantic 的遷移 | 一次性呼叫 + `output_format` | — | ✗ 要讀檔；schema 是 2020-12，SDK 只收 draft-07 | — | 單人 | 採納：開放 Read 工具，schema 轉成 draft-07，`max_turns` ≥ 3 |
| 21 | 縮圖分析腳本 | 列為一次性，退役 | — | ✗ playbook 文件規定要重跑這條流程 | — | 單人 + 查證 | **修修決定** |
| 22 | 訂閱的 usage credits / overage | 沒提 | — | ✗ 開了的話會默默付費 | — | 單人 | 採納：列入 S0，由修修確認帳號設定 |
| 23 | Opus 被 biology 分類器攔下時自動換 model | 沒提 | — | 出自文件 | — | 單人 | 採納為風險 R10 |
| 24 | runbook 過期 | 沒提 | — | — | `openrouter-canary.md`（#7） | 單人 | 採納：S6 處理 |
| 25 | 修修要親手做的事 | 散在各節 | — | — | 整理出清單（#8 等） | 單人 | 採納：新增一節 |

## Reviewer 之間的直接矛盾

無。#14 是看法不同（A 認為額度會常態用完；C 查了 cron 時段，認為凌晨批次跟即時對話重疊少），兩者可以並存：採納為風險，並用 `allowed_warning` 訊號處理。

## 與修修既有裁決衝突的建議（不自行採納，交給修修）

- #15 常備授權：D-c 的字面意思是「每次都問我」。
- #3 的一部分：A 認為「自動切回會把修修的決定默默作廢」。ADR v2 的處理方式是：核准後，在上限、重置時間或修修手動切回之前都維持 OpenRouter；之後才依 D-c 前半句「有訂閱就走訂閱」切回。這是否符合修修的意思，要由修修確認。
