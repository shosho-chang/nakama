# ADR-070：LLM 只剩兩條路 — Claude 訂閱（Agent SDK）與 OpenRouter

- **Status**：Accepted（方向：修修 2026-09-24 裁決）。v2 已依 panel review 修訂（[審查紀錄](../research/2026-09-24-adr070-panel-review.md)）；v3 寫入修修第三輪裁決（Q4–Q8）。實作依 §遷移 Slices 進行；**S0 的待驗證項沒有答案前，不得切換任何 production 路徑**
- **Date**：2026-09-24（v1 初稿、v2 依 panel review 修訂）／2026-09-25（v3 第三輪裁決）
- **Owner**：修修
- **Supersedes after cutover**：[ADR-026](ADR-026-llm-router-auth-dimension.md) 整個 auth 維度：`api` / `subscription_preferred` / `subscription_required` 三元 policy、`AUTH_*` env、`NAKAMA_REQUIRE_MAX_PLAN`、`claude -p` CLI 訂閱路徑
- **Amends**：[ADR-049](ADR-049-openrouter-transport.md)。OpenRouter 從「api-tier transport kill-switch」改為兩條 lane 之一；`LLM_TRANSPORT*` env 與 xAI carve-out 退場
- **Supersedes（從未合併）**：`origin/feat/default-auth-subscription` 上的 c7a72880「ADR-026 Amendment 2026-08-19」。修修 2026-08-18 已裁決「都改成預設使用訂閱額度」，但這個 amendment 始終沒進 main
- **Preserves**：ADR-063 字幕正式路徑（本 ADR 只動它周邊的 LLM 呼叫方式）；ADR-054 D11「render 只在桌機」分工
- **Related**：issue #1299；PR #1298（記憶抽取 thread 失去 agent context）；PR #1300（退役 Gemini 音訊仲裁與一次性審查腳本）
- **Principles**：[reliability](../principles/reliability.md) §3 SoT、§5 Retry、§7 Timeout 必填、§9 可觀察的失敗；[observability](../principles/observability.md) §1 三層觀察、§6 Alert 三層；[schemas](../principles/schemas.md) §9 LLM 結構化輸出

---

## 一句話

程式庫裡每一次 LLM 呼叫，只准走兩個地方：**Claude 模型走 Claude 訂閱（Agent SDK）**；**其他模型走 OpenRouter API**。走哪條由 model 字串決定，政策寫在 code 裡，不靠 `.env` 開關。訂閱額度用完時**不自動改道**：先通知修修，由修修決定要不要把 Claude 呼叫轉到 OpenRouter。

## 修修的裁決（2026-09-24，本 ADR 的依據）

| # | 裁決（原話摘要） |
|---|---|
| D-a | 「一個已經使用 Agent SDK 來使用訂閱額度的 Agent，會有其他的功能會用到 API？這不是應該會統一嗎？」→ 要統一 |
| D-b | 「我現在要把全部的 agent 都改成 agent SDK。所以以後就兩種選擇：一種是走訂閱額度，另外一種是走 OpenRouter」 |
| D-c | 「有訂閱的話就直接走訂閱，所有的 Agent SDK 動作都是這樣。如果訂閱額度用完了，要給我提示，讓我能做下一個決策：問我要不要直接轉到 OpenRouter 裡面的額度」 |
| D-d | 「為什麼要把這一行加到 .env 檔案裡面？這不是應該在程式裡面加的嗎？」→ 政策寫在 code |
| D-e | 「我想要整個程式庫裡面就統一呼叫兩個地方：1. Claude 的訂閱 2. OpenRouter 的 API。所以我要在程式裡面可以自由選擇，看是要用 OpenRouter 裡面的哪一個模型」 |
| D-f | 「RenderWatcher 也改過來，改成預設是 Claude 訂閱的 Opus 最新版」 |
| D-g | 「沒用到的就直接刪掉」 |
| Q1–Q8 | 見文末「修修裁決」第二輪、第三輪 |

仍然有效的前序裁決：2026-08-17 全面停用 Gemini，非 Claude 模型預設用 OpenAI 並經 OpenRouter 呼叫（`memory/claude/feedback_no_gemini_default_openai.md`）。

---

## Context

### 現況：六種 LLM 入口並存（2026-09-24 盤點，`origin/main` @ 8a37e666）

| 入口 | 怎麼決定計費 | 呼叫點 |
|---|---|---|
| ① Agent SDK `query` | 子進程 env 塞 OAuth token（`shared/agent_sdk.py:44-47`）；Nami 另用 `NAMI_SDK_OAUTH_TOKEN`（`gateway/handlers/nami.py:115-134`） | 3 個：Nami（`NAMI_USE_AGENT_SDK=1` 時）、Robin annotation merger（`ROBIN_MERGE_USE_AGENT_SDK=1` 時）、Sanji judge |
| ② `shared.llm.ask / ask_multi / ask_with_tools` → ADR-026 dispatch | 看 `AUTH_<AGENT>[_<TASK>]` env，沒設就用 `DEFAULT_AUTH["default"]="api"`（`shared/llm_router.py:187-196`）；`api` 再依 `LLM_TRANSPORT*` 選原生 SDK 或 OpenRouter | 約 56 個（`ask` 約 45、`ask_multi` 8、`ask_with_tools` 4），其中約 10–12 個已經沒有 production caller。`ask_with_audio` 的 3 個呼叫點由 PR #1300 移除 |
| ③ 直接呼叫 `ask_claude(auth_policy="subscription_required")` → `claude -p` | 寫死在程式裡 | `agents/usopp/video_description.py:383` |
| ④ 直接 `get_client().messages.stream` | 繞過 router、auth、OpenRouter 和 cost 紀錄 | `scripts/cluster_thumbnail_patterns.py`、`scripts/compose_playbook_v1.py` |
| ⑤ Codex CLI `codex exec` | ChatGPT 帳號 | 4 個：`scripts/render_watcher.py:468-487`（`gpt-5.6-sol`）、`agents/brook/script_video/finished_cut_production/_codex_semantic.py`、`scripts/podcast_highlight_visual_orchestrator.py`、`scripts/dispatch_codex_playbook_audit.py` |
| ⑥ 本機 LLM | llama.cpp / Ollama | `scripts/ab_ingest_bench.py`（bench）；Robin ingest 只在估算等待時間時探測本機伺服器（`agents/robin/ingest.py:79-85`），實際生成不走本機 |

另外，走 ② 的 `tool_use` 一律計入 API 帳（`shared/anthropic_client.py:368-370`）：CLI 路徑沒辦法載入 raw tool-use。

### 事故鏈：為什麼現在要收斂

1. **2026-08-17**：Anthropic **API credit** 用完。Nami、Franky、Robin、memory-reflection 都走 `api` 預設，同時停擺（commit 4d2c3b47；`docs/plans/2026-08-18-annotation-merger-agent-sdk-plan.md:5,36`）。
2. **2026-08-18**：修修裁決「都改成預設使用訂閱額度」。c7a72880 把 `DEFAULT_AUTH` 改成 `subscription_preferred`，但**沒有合併**。
3. **2026-09-24**：Nami 背景記憶抽取 3 天內 22 次 400「credit balance is too low」。原因是 `threading.Thread` 沒有繼承 agent 的 ContextVar，`get_auth_policy(agent=None)` 因此落回 `api`（PR #1298）。

共同根因：**走訂閱是 opt-in**。新功能、新 thread、忘了設 env 的地方，都會默默掉回一把已經沒錢的 API key。

### 查證過的事實

| # | 事實 | 來源 |
|---|---|---|
| F1 | 桌機裝的是 `claude-agent-sdk` 0.2.134（內附 CLI 2.1.226）。main 的 `requirements.txt:86` 範圍是 `>=0.2.128,<0.3`。0.2.140 曾因上游 API 漂移打破 CI，所以在 cf7e7a31 改釘 0.2.128；這個 commit **只在未合併的分支上** | `pip show`；`claude.exe --version`；git |
| F2 | `ClaudeAgentOptions` 有 45 個欄位，包括 `output_format`、`fallback_model`、`effort`、`thinking`、`add_dirs`、`skills`、`setting_sources`、`permission_mode`、`can_use_tool`、`hooks`、`max_budget_usd`、`load_timeout_ms`。**沒有 `temperature`，也沒有 `max_tokens`**。max_tokens 可以改用 CLI 的 `CLAUDE_CODE_MAX_OUTPUT_TOKENS` env（在內附 binary 裡確認過）；temperature 找不到任何替代 | `dataclasses.fields`；binary 字串比對 |
| F3 | 別名（`opus` / `sonnet` / `haiku` / `fable`）**由內附 CLI 在本機解析，解析結果是「這個 CLI 版本認得的最新版」**。CLI 2.1.226 的對照表是 `opus → claude-opus-5`、`sonnet → claude-sonnet-5`、`haiku → claude-haiku-4-5`，而且它**不認得 `claude-opus-5-5`**。官方文件說 Anthropic API 上的最新 Opus 是 5.5。實際跑的 model 可以從 `AssistantMessage.model` 讀到 | binary 內的 `latest_per_family`；SDK `types.py`；`shared/llm_router.py:306-307`；code.claude.com/docs/en/model-config |
| F4 | 憑證優先序：`ANTHROPIC_AUTH_TOKEN` > `ANTHROPIC_API_KEY` > … > `CLAUDE_CODE_OAUTH_TOKEN`。API key 會壓過訂閱 token | code.claude.com/docs/en/authentication；2026-08-18 實測（`shared/agent_sdk.py:10-14`） |
| F5 | `claude setup-token` 產生一年期 token | 同上 |
| F6 | Claude Code / Agent SDK 可以用 `ANTHROPIC_BASE_URL=https://openrouter.ai/api` + `ANTHROPIC_AUTH_TOKEN` + `ANTHROPIC_API_KEY=""` 走 OpenRouter，但**只限 Claude 模型**；Anthropic 不支援經 gateway 跑非 Claude 模型。OpenRouter 只保證 Anthropic 1P provider 能正常運作 | openrouter.ai/docs/cookbook/coding-agents/claude-code-integration；code.claude.com/docs/en/llm-gateway |
| F7 | SDK 每次 `query` 都會起一個 CLI 子進程，程式註解估計約 100MB（`nami.py:1301`，**不是量測值**）。VPS 總共 3.9G RAM | `docs/research/2026-07-29-agent-sdk-spike-findings.md:83` |
| F8 | 延遲：merger（Opus）單次 14–24s；20 次序列 Haiku 總共 89s | `docs/research/2026-08-18-merger-sdk-spike-findings.md:32,38-41` |
| F9 | 沒設 `tools=[]` 時，成本是有設時的 3 倍（$0.1102 vs $0.0352）。`setting_sources` 也必須明確給 | `docs/research/2026-07-29-agent-sdk-spike-findings.md:24-33` |
| F10 | SDK 失敗時先 yield 一個 error `ResultMessage`，再 raise | `docs/research/2026-08-18-merger-sdk-spike-findings.md:49-50` |
| F11 | Slack gateway **沒有**任何 `block_actions` handler。Bridge 有按鈕式 HITL（`thousand_sunny/routers/bridge.py:860-931`、`:1504-1580`） | `gateway/bot.py:283-290` |
| F12 | `shared/alerts.alert("error", …)` 會發 Franky Slack DM；**要傳 `dedupe_key`** 才會在 30 分鐘內去重 | `shared/alerts.py:58,78` |
| F13 | OpenRouter 目前是封閉白名單：`get_provider` 只接受 6 個前綴加上 `SDK_MODEL_ALIASES`，`_SLUG_MAP` 有 11 筆，查不到就 raise | `shared/llm_router.py:320-334`；`shared/openrouter_models.py:21-38,63-66` |
| F14 | OpenRouter client 會記**實際** cost（`usage.include=True`），並預設 `allow_fallbacks=False`。這兩點只在那個 OpenAI-compatible client 生效，CLI 直接打 OpenRouter 時不適用 | `shared/openrouter_client.py:91-100,214-249` |
| F15 | SDK 有結構化的額度訊號：`RateLimitEvent`（`status` 為 `allowed` / `allowed_warning` / `rejected`；`rate_limit_type` 為 `five_hour` / `seven_day` / `seven_day_opus` / `seven_day_sonnet` / `overage`；`resets_at`；`overage_status`），以及 `AssistantMessage.error`（含 `rate_limit`、`billing_error`、`authentication_failed`） | SDK `types.py:1008-1035,1276-1307`；code.claude.com/docs/en/errors |
| F16 | `can_use_tool` 對「已經被 `allowed_tools`、`permission_mode` 或 settings allow 規則放行」的呼叫**不會觸發**。要攔每一次工具呼叫，必須用 `PreToolUse` hook | SDK `types.py:1931-1947` |
| F17 | 用 `asyncio.wait_for` / `asyncio.timeout` 取消，可能跳過子進程的 terminate / kill 流程 | SDK `_internal/transport/subprocess_cli.py:941-950` |
| F18 | OpenRouter 帳號已登記 **Anthropic BYOK key**。BYOK 會優先使用這把 key，也就是 8/17 已經沒錢的那個帳號 | `docs/runbooks/openrouter-canary.md:9` |
| F19 | 已經有 async 函式在同步呼叫 LLM：Nami 的 MCP tool handler（`gateway/handlers/nami_tools.py:43-44` → `_tool_ask_zoro` → `shared.llm.ask`），以及 `thousand_sunny/routers/bridge_digests.py:169,191`。在這些地方用 `asyncio.run` 包 SDK 會丟 `RuntimeError` | 程式碼 |
| F20 | 官方文件寫明：Opus 被 biology 分類器攔下時，會自動改用較舊的 Opus 重跑，而且不受 `fallback_model` 控制；fallback chain 也不會因為 billing 或 rate limit 錯誤而觸發 | code.claude.com/docs/en/model-config（panel reviewer B 引用） |

### 尚未查證（S0 必須先回答）

| # | 未知 | 為什麼重要 |
|---|---|---|
| U1 | 實際把額度用完時，F15 那些欄位的值分別是什麼（`rate_limit_type`、有沒有 `resets_at`），以及錯誤文字 | D5 的偵測規則要以真實值為準 |
| U2 | 訂閱模式下，`ResultMessage.total_cost_usd` 是 0 還是 API 等值價。兩份紀錄互相矛盾：`memory/claude/reference_agent_sdk_supports_oauth.md` 說是 0，`shared/claude_cli_client.py:237-241` 說是 API 等值價。另外，官方文件說它是 client 端依內建價目表算的估計值 | 成本面板和 `max_budget_usd` 煞車還有沒有意義 |
| U3 | VPS `/home/nakama/.env` 目前實際的路由設定：`AUTH_*`、`MODEL_*`、`LLM_TRANSPORT_*`。#1173 的 commit message 說 2026-08-17 已經把 Franky、memory-reflection、Robin 改走 OpenRouter + OpenAI | 切換前後的行為要對得上 |
| U4 | VPS 上一次性小呼叫的 p50 / p95 延遲，以及每個 CLI 子進程實際吃多少 RSS | 意圖分類、記憶抽取、併發上限的數值 |
| U5 | OpenRouter 帳號的 `data_collection: deny` 還沒勾（`docs/runbooks/openrouter-canary.md:10`） | 資料政策 |
| U6 | 切到 OpenRouter 後，別名解析出來的 model id（例如裸的 `claude-opus-5`）OpenRouter 認不認得 | D5 的 OpenRouter 模式能不能直接用 |
| U7 | VPS 實際安裝的 SDK / CLI 版本，以及別名在 VPS 上解析成什麼 | F3 的結果會跟著版本變 |
| U8 | 修修的 Claude 訂閱有沒有開 usage credits / overage。如果有開，額度用完時可能直接改成付費，而不是回報失敗 | 開了的話，D5 永遠不會被觸發，違反 D-c |
| U9 | 桌機上非互動的 process（Thousand Sunny 排程、RenderWatcher）拿不拿得到 `CLAUDE_CODE_OAUTH_TOKEN` | D7 |
| U10 | Structured output 在 `max_turns` 較小時的表現，以及 schema 版本限制（文件說只收 draft-07） | D2、D7 |

---

## Decision

### D1　兩條 lane，由 model 字串決定

| model 字串 | lane | 例 |
|---|---|---|
| Claude 別名或 id：`opus`、`sonnet`、`haiku`、`fable`、`claude-*` | **L1 Claude 訂閱**（Agent SDK） | `model="opus"` |
| OpenRouter slug：`vendor/model`（含 `/`） | **L2 OpenRouter API**（OpenAI-compatible，`shared/openrouter_client.py`） | `model="openai/gpt-5.6-terra"` |

- **沒有第三條路**。以下全部退場：`ANTHROPIC_API_KEY` 計費、`claude -p` CLI、Gemini 與 xAI 的原生 SDK、Codex CLI、直接呼叫 `get_client()`。
- 呼叫端一律透過 `shared.llm` facade（`ask` / `ask_multi` 等函式簽名不變），由 facade 依 model 字串分派。但 facade 內部必須處理 async 情境（D2 第 8 項），不能假設呼叫端一定是同步的。
- 「L1 在修修核准後改走 OpenRouter」不算第三條路，細節見 D5。

### D2　L1 只有一個實作：`shared/agent_sdk.py`

所有 Claude 呼叫都經過這個模組，它負責：

1. **憑證注入**：唯一來源是 `CLAUDE_CODE_OAUTH_TOKEN`。注入時，把會壓過它的變數全部清空或拿掉：`ANTHROPIC_API_KEY`、`ANTHROPIC_AUTH_TOKEN`、`ANTHROPIC_BASE_URL`，以及雲端 provider 旗標 `CLAUDE_CODE_USE_BEDROCK`、`CLAUDE_CODE_USE_VERTEX` 等（F4）。
2. **一次性文字呼叫的預設值**：`tools=[]`、`setting_sources=[]`、`max_turns=1`（F9）。**有 structured output 的呼叫，`max_turns` 至少 3**：SDK 在輸出不合 schema 時會要求模型重寫，需要額外的回合。
3. **結構化輸出**：有 pydantic schema 的呼叫改用 `output_format`（schemas §9），逐步取代 merger 強制指定的 `tool_choice`，以及各處用 regex 撈 JSON 的寫法。schema 版本以 S0 的 U10 實測結果為準。
4. **Timeout 必填**（reliability §7）：用 `anyio.fail_after`，**不用** `asyncio.wait_for`（F17）。agentic 長任務（例如 RenderWatcher 的 4 小時）各自設定上限。
5. **機器層級的併發上限**：不能只做 process 內的 semaphore。VPS 上 gateway、Bridge 和十幾個 cron 是各自獨立的 process，每個 process 各限 2 等於沒限。改用 `state.db` 裡的 lease 表（租約含 TTL，process 掛掉時名額會自動回收），整台機器共用一個上限，初始值依 U4 決定。
6. **用量紀錄**：每次呼叫寫一筆 `api_calls`，內容包括 `lane_actual`（`subscription` 或 `openrouter`）、`AssistantMessage.model` 讀到的實際 model、token 數，以及 `rate_limit_type` / `status`（F15）。`cost_usd` 只記 OpenRouter 回報的實際值；U2 還沒釐清之前，訂閱模式的 `total_cost_usd` 只當參考、不入帳。
7. **錯誤原樣保存**：保存 error `ResultMessage`、`AssistantMessage.error`、`RateLimitEvent` 的完整欄位和例外文字（F10、F15），交給 D5 使用（reliability §9）。`SubscriptionExhausted` 標為**不可重試**，避免 `shared/retry.py` 為一個早已知道的結果再多起幾次子進程。
8. **async 情境**：偵測到目前已經有 event loop 在跑（F19），就把 SDK 呼叫丟到專用 worker thread 執行，不在 loop 裡直接用 `asyncio.run`。巢狀呼叫（在一個 L1 session 裡又呼叫 L1，例如 Nami tool 呼叫 `research_keywords`）用 contextvar 標記，**不另外佔併發名額**，避免 Nami session 占著名額、又等自己裡面的呼叫而卡死。

**沒有對應參數的設定**：`max_tokens` 改用 `CLAUDE_CODE_MAX_OUTPUT_TOKENS` env 傳入（F2）。`temperature` 直接丟掉。目前會傳 `temperature` 的呼叫點包括 `gateway/router.py:205`（0）、`agents/robin/source_map_extractor.py:128`（0）、`agents/zoro/keyword_research.py`（0 / 0.5）、`agents/brook/synthesize/_outline.py:148`（0.3），這些地方接受這個行為改變，輸出品質的回歸由各 slice 的驗收涵蓋。

**`ask_multi`** 的多輪訊息，沿用 `claude_cli_client.py:88-124` 的做法攤平成單一 prompt。現有 8 個呼叫點都是「失敗就補一句提醒再試一次」的一到兩輪形狀，攤平不會有問題。

### D3　憑證單一化

- **L1**：用 `CLAUDE_CODE_OAUTH_TOKEN`（`claude setup-token` 產生，一年期，F5）。**每台機器各自產生一把**，這樣要撤銷或輪換時，兩台機器不會一起斷。`NAMI_SDK_OAUTH_TOKEN` 併入這個變數。
- **L2**：用 `OPENROUTER_API_KEY`。
- 全部切換完成後，由修修從 VPS `.env` 移除 `ANTHROPIC_API_KEY`、`GEMINI_API_KEY`、`XAI_API_KEY`。
- Token 到期前 30 天，由 Franky 發提醒。收到提醒後的操作步驟寫在 runbook（見 §修修要親手做的事）。

### D4　政策寫在 code，不寫在 `.env`

- **退場**：`AUTH_*`、`NAKAMA_REQUIRE_MAX_PLAN`、`DEFAULT_AUTH`、`LLM_TRANSPORT*`，以及遷移期間的 flag `NAMI_USE_AGENT_SDK`、`ROBIN_MERGE_USE_AGENT_SDK`（各自切換完成後刪除）。
- **Model 選擇**：預設值寫在 `MODEL_REGISTRY`（code），修修要即時調整就用 Bridge `/bridge/models` 的 override。`MODEL_<AGENT>*` env 退場。
- 拿掉 `.env` 開關後，回滾方式改寫在 §回滾。

### D5　訂閱額度用完：先通知修修，由修修決定要不要切 OpenRouter

**唯一真相在 VPS。** lane 狀態只存在 VPS 的 `state.db`（reliability §3），而且只有一列全域狀態（Q1）。桌機不自己做決定，而是透過 Bridge API 讀取狀態（`GET https://nakama.shosho.tw/api/llm-lane`，快取 60 秒）；讀不到時沿用最後一次讀到的狀態。每次狀態轉換都用 `version` 欄位做 compare-and-swap，並留下紀錄。

**狀態機**：

**呼叫分兩類**（修修 Q7 選 (b)，第三輪裁決）。每個呼叫點在程式裡宣告 `call_class`，沒宣告的一律當作 `batch`：

| 類別 | 定義 | 範例 | 額度用完時 |
|---|---|---|---|
| `interactive` | 修修此刻正在等回應 | Nami 對話迴圈、gateway 意圖分類、Sanji / Zoro handler、orchestrator、修修在 Bridge 上等結果的請求（digest ask、replan） | **自動**轉 OpenRouter，**每日上限 US$5**（台北時間 00:00 歸零），轉了之後 DM 通知修修 |
| `batch` | 沒有人在等 | cron、翻譯、ingest、記憶抽取、RenderWatcher、桌機腳本 | **等額度重置**；修修可以在 Bridge 手動核准先跑，**每次核准預設上限 US$20**（按的時候可以改） |

**狀態機**（每一類各自一份，存在同一列全域狀態裡）：

```
subscription ──(偵測到額度用完)──▶ exhausted
exhausted ──[interactive] 當日上限還有剩，自動──▶ openrouter_auto
exhausted ──[batch] 修修在 Bridge 核准，附上限──▶ openrouter_approved
openrouter_auto / openrouter_approved ──(上限用完)──▶ exhausted（再 DM）
exhausted / openrouter_* ──(resets_at 到了且探針成功 / 修修按「切回訂閱」)──▶ subscription
```

- **偵測以 SDK 的結構化訊號為主**（F15）：`RateLimitEvent.status == "rejected"`，或 `AssistantMessage.error` 是 `rate_limit` / `billing_error`，就轉入 `exhausted`，並記下 `rate_limit_type` 和 `resets_at`。
- **只擋用完的那一類 model**：如果用完的是 `seven_day_opus`，只擋 Opus 呼叫，Sonnet / Haiku 照常執行；如果是 `five_hour` / `seven_day` 這種全域額度，就全部擋下。
- **`allowed_warning` 是軟訊號**：收到時，批次類呼叫（cron、翻譯、ingest）延後執行，互動類呼叫（Nami、gateway）照常進行。這樣可以把額度留給修修的即時對話和修修自己的 Claude Code 使用（R9）。
- **啟發式規則只能觸發軟警告**：「10 分鐘內未分類的 SDK 失敗」只會發「疑似額度用完」的 DM，並**立刻**跑一次探針，但**不會** fail-fast。401 / OAuth 失效另外歸為 `auth_error`，發「token 失效」alert，不進這個狀態機。
- **被擋下的 `batch` 呼叫**會立刻丟出 `SubscriptionExhausted`，不花錢，也不會默默改道。`interactive` 呼叫在當日上限內自動改走 OpenRouter；上限用完後同樣丟出 `SubscriptionExhausted`，Nami 會回覆「訂閱額度和今日 OpenRouter 上限都用完了，已通知修修」。
- **通知**：走 `shared.alerts.alert("error", "llm_lane", …, dedupe_key=…)`（F12），由 Franky DM 修修。會發通知的時機：額度用完（附 `rate_limit_type`、`resets_at` 和 batch 的核准連結）、interactive 自動轉 OpenRouter、任一類上限用完、切回訂閱。
- **決策介面**：只有 `batch` 需要人決定。S2 先評估能不能沿用 Bridge 既有的 approval queue（ADR-006），套不上才另開最小的 `/bridge/llm-lane` 頁面。頁面要顯示：從何時開始、用完的是哪一類額度、`resets_at`、今天 interactive 已經花了多少、目前正在跑和排隊中的 batch 工作。按鈕有兩個：「batch 切到 OpenRouter（上限 US$20，可改）」和「等重置」。Slack 沒有按鈕（F11）。
- **上限怎麼算**：OpenRouter 模式下，每次呼叫的實際 cost 都累加進 VPS `state.db`（interactive 按日累計，batch 按每次核准累計）；超過上限就轉回 `exhausted`。兩個金額（US$5 / 日、US$20 / 次）寫在 code 常數裡（D4），不放 env。
- **OpenRouter 模式下的路由**（`openrouter_auto` 和 `openrouter_approved` 相同）：
  - 一次性文字呼叫（絕大多數）改走 **L2 client**，slug 為 `anthropic/<id>`。這樣可以保留實際 cost、`allow_fallbacks=False`，以及 Anthropic 1P provider 優先（F14）。
  - agentic 呼叫（有工具、skill、session 的）才用 SDK，並把子進程 env 換成 `ANTHROPIC_BASE_URL=https://openrouter.ai/api`、`ANTHROPIC_AUTH_TOKEN=$OPENROUTER_API_KEY`、`ANTHROPIC_API_KEY=""`（F6）。這條路拿不到 OpenRouter 的實際 cost，所以用量改用 OpenRouter 的 generation 查詢補記；可不可行在 S2 驗證。
  - `OPENROUTER_API_KEY` 是空的時候一律 fail closed，不能讓 OAuth token 被送到 OpenRouter。
- **切回訂閱**：優先依 `resets_at` 排程；沒有 `resets_at` 時，每 30 分鐘探一次**失敗的那一類 model**。探針由 Franky 的 health cron（VPS `*/5`，`cron.conf:35`）負責，新增一個 probe target，全系統只有這一個 owner。切回後有 60 分鐘觀察期：這段時間內如果又失敗，就不再自動切回，只發 DM 通知。
- **OpenRouter 路徑每天 canary 一次**：每天用 OpenRouter env 打一次最小的 Haiku 呼叫，確認這條平常不走的備援路徑沒有壞掉（`lane_actual=openrouter`）。
- **不使用** SDK 的 `fallback_model`（它會默默換 model、不會通知，也不會因 rate limit 觸發，F20）。

### D6　L2：程式裡可以自由指定 OpenRouter 上任何 model

- registry、Bridge override 或呼叫點直接寫的 `vendor/model` slug，一律原樣送 OpenRouter。檢查方式改成比對 OpenRouter `/models`（加快取），不再用手動維護的白名單（F13）。`_SLUG_MAP` 只在遷移期間留給舊的裸 id 用，S6 刪除。
- 非 Claude 模型預設選 OpenAI（2026-08-17 裁決）。
- 保留 ADR-049 的 BYOK 與 `allow_fallbacks=False`。**Anthropic BYOK key 由修修從 OpenRouter 後台移除**（F18，Q4），這樣 Claude 呼叫走 OpenRouter 時，扣的是 OpenRouter 的額度。
- L2 目前支援文字和多輪對話；tool-use 和圖片等真的有呼叫點需要時再做。

### D7　Codex CLI 呼叫全部改走 L1

| 呼叫點 | 改法 |
|---|---|
| `scripts/render_watcher.py` packaging job | 改用 SDK agentic 呼叫，**`model="opus"`**（D-f）。能不能真的拿到最新版，靠 D10 的 SDK 自動升級。呼叫類別是 `batch`。<br>• 環境：`cwd=job_dir`；`add_dirs` 包含 working episode、vault packaging、vault cutout，以及 repo root（skill 要執行 repo 裡的 script）；`setting_sources=[]`，避免桌機 `~/.claude/settings.json` 和 repo `.claude/settings.json` 的 allow 規則蓋掉限制（F16）；`permission_mode` 明確指定。<br>• 寫入限制：用 **`PreToolUse` hook** 擋掉 Write / Edit 對這些路徑以外的寫入。允許寫入的清單是：`job_dir`、working episode 的 packaging、vault packaging、vault cutouts；repo root 只能讀。<br>• 已知限制：透過 Bash 執行的 script 自己寫檔擋不住，最後防線是既有的成功驗證（`_validate_initial_packaging_outputs`，`render_watcher.py:570`），加上 prompt 規則。<br>• wall-clock 上限 4 小時（沿用 `timeout=14400`），用 `anyio.fail_after` 實作。<br>• 記錄 `AssistantMessage.model`。<br>• `tests/test_render_watcher.py:437-468` 改鎖新的 model。 |
| `.../finished_cut_production/_codex_semantic.py` | 改用 SDK 呼叫：開放 Read 工具讓它讀 `packet.json`（`:720`），或把 packet 直接放進 prompt。搭配 `output_format`，schema 從 2020-12（`:830`）轉成 draft-07（U10），`max_turns` 至少 3。 |
| `scripts/podcast_highlight_visual_orchestrator.py` | 改用 SDK agentic 呼叫。沿用現有的 resume 契約：session id 要一致（`:197-203`），改用 SDK 的 `resume` / `session_id`，並固定 `cwd`（resume 必須在同一個 cwd）。 |
| `scripts/dispatch_codex_playbook_audit.py` | 一次性腳本，在 D8 退役。 |

### D8　退役清單（D-g：沒用到的直接刪）

每一項刪之前都要再確認一次，grep 範圍包含 `.claude/skills/**`、`.agents/skills/**`、`thousand_sunny/templates/**`、`thousand_sunny/static/**`、`cron.conf`、`scripts/*.ps1`、`prompts/**`。**S6 要等 PR #1300 合併後才能開始**：`gemini_client` 最後幾個呼叫點靠 #1300 移除。

- **退場的 lane 程式**：
  - `shared/claude_cli_client.py`
  - `shared/anthropic_client.py` 的 API-key 路徑、`call_claude_with_tools`、`get_client`
  - `shared/gemini_client.py`、`shared/xai_client.py`
  - `shared/llm.py` 的 `ask_with_audio`
  - `shared/llm_transport.py` 的 env 開關
  - `google-genai` 依賴
  - `pyproject.toml:141-145` 裡已刪腳本的 ruff ignore
  - `podcast_subtitles/production.py` 的 `ALLOW_PAID_GEMINI` guard（`:31`、`:196-200`）
- **沒有 production caller 的 LLM 程式**（2026-09-24 用 grep 複核，panel reviewer B 抽查也通過）：
  - **整支刪除**：`agents/brook/line1b_extractor.py`、`shared/coverage_classifier.py`、`shared/figure_triage.py`；`agents/brook/seo_narrow.py`、`agents/robin/style_extractor.py`。後兩支刪除時，一起改掉提到它們的 `thousand_sunny/templates/bridge/inventory.html`、`thousand_sunny/static/crew/index.html`、`CONTENT-PIPELINE.md`。
  - **只刪 LLM 函式、模組保留**：`agents/brook/podcast_carousel_copy.py` 的 `generate_copy_spec` 等 LLM 函式、`agents/brook/podcast_carousel_panel.py` 的 LLM 評審函式。這兩個模組的其他部分仍被 `scripts/run_podcast_carousel.py:15-16`、`scripts/podcast_carousel_correction_job.py:20` 使用。
  - **分支與設定**：`agents/robin/kb_search.py` 的 `engine="haiku"` 分支；`podcast_subtitles/adapters/correction.py`、`semantic.py` 的付費 runner（production 固定 `allow_paid_api=False`）；registry 裡沒人用的 `project_angle_scan`、`project_mechanism`、`thumbnail_*`；`scripts/Invoke-IngestTextbook.ps1`（它呼叫的 `run_s8_batch.py` 已經不存在）。
- **本機 LLM**：`shared/local_llm.py`、`config.yaml` 的 `local_llm` 區塊、`scripts/ab_ingest_bench.py`。Robin ingest 生成時早就一律走雲端（`agents/robin/ingest.py:510-519`），只剩估算等待時間時還在探測（`:79-85`），改成固定用雲端的估算區間。
- **一次性腳本**：`scripts/dispatch_codex_playbook_audit.py`、`scripts/spikes/agent_sdk_probe.py`、`scripts/spikes/merger_sdk_probe.py`。
- **縮圖 playbook 建置流程（Q6，修修裁決刪除）**：`scripts/extract_thumbnail_features.py`、`scripts/cluster_thumbnail_patterns.py`、`scripts/compose_playbook_v1.py`，以及同一條流程的 `scripts/splice_playbook_v1.py`、`scripts/fix_playbook_v1_to_v1_1.py`。
  - 刪除理由：五支都在 2026-05-27 跑過一次後就沒再動。原始素材 `E:\Thumbnail-example` 和中間產物 `thumbnail_reference_extraction_v1.json` 都已經不存在，又是直接呼叫 Anthropic API，現在根本跑不起來。
  - **保留**它們的產出 `prompts/thumbnail/playbook_v1.md`、`playbook_data_v1.json`，title-brainstorm / thumbnail-brainstorm 一直在用。
  - 要一起改的地方：`playbook_v1.md` §8「When adding a new corpus row」改成「v2 時在 Claude Code 對話中看圖重建（走訂閱）」；清掉 `pyproject.toml` 和 `thousand_sunny/templates/bridge/inventory.html` 裡提到這些腳本的地方。

### D10　SDK 自動升級：「最新版模型」不靠人維護

別名解析成哪一版，取決於 SDK 內附的 CLI（F3）。修修的要求是「不要又變成一張沒人維護、永遠指向舊版的對照表」（Q5），所以不在 code 裡放 model 對照表，改成讓 SDK 自己保持在最新版：

1. **每週自動升級**：GitHub Actions 排程檢查 PyPI 上的 `claude-agent-sdk`，有新版就自動開 PR，把 `requirements.txt` / `pyproject.toml` 的**精確版本號**（`==`）升上去。
2. **CI 把關、綠燈自動合併**：修修同意這類 PR 在 CI 全綠時自動合併（Q5）。main 有 strict 保護，PR 落後時由 workflow 先同步分支再合併。CI 紅燈就不合併，並 DM 修修。
3. **合併後通知**：Franky DM 修修：「SDK 升到 X，`opus` / `sonnet` / `haiku` 現在解析成 …，請部署」。解析結果由 CI 裡的一個小步驟讀內附 CLI 的別名表取得，不必實際呼叫 LLM。
4. **每週落後偵測**：Franky 拿 `api_calls` 記到的實際 model（D2 第 6 項），比對 OpenRouter 公開的 `/models` 清單中最新的 Anthropic model。發現落後（例如新 Opus 已經上架，我們還在跑舊的）就 DM 修修。
5. **修修要做的事**：只有部署。

### D9　每次 LLM 呼叫都要帶 agent context

- `shared/llm_context.py` 提供 `spawn_thread` / `submit` helper。**每次** spawn 或 submit 都重新 `copy_context()`；共用同一個 context 物件會丟出 `RuntimeError: cannot enter context`。開 thread 呼叫 LLM 時必須用這個 helper。
- 盤點出的缺口一次補齊：
  - `memory_extractor`（#1298 已修）
  - `fb_renderer` 的 ThreadPoolExecutor、整條 `run_repurpose`
  - Zoro scout cron
  - Bridge 中除了 translate / SEO / keyword 以外的路由（S1 逐一列出）
  - Robin merger 路由（`thousand_sunny/routers/robin.py:705-708`）
  - Sanji judge
  - `agents/zoro/keyword_research.py:142`：設了 `zoro` 之後沒有還原
- 沒帶 context 的呼叫照樣執行，但記成 `agent="unknown"` 並發 warning（observability §1）。S6 驗收時 `unknown` 必須是 0。

---

## 回滾

D4 拿掉了 `.env` 開關，等於放棄了「改一行 env 再重啟就能秒退」這個能力（見舊版 `docs/runbooks/openrouter-canary.md:50-54`）。這是刻意的取捨，替代方案如下：

1. **每個切換 slice 都是一個可以單獨 revert 的 PR**。S1a–d、S4、S5 切換時**只改呼叫路徑，不刪舊程式**，舊程式等到 S6 才刪。出問題時的做法是 `git revert <PR 的 merge commit>`，然後重啟受影響的 service。每個 slice 的 PR 內文要寫明要重啟哪些 service（`nakama-gateway`、`thousand-sunny`，cron 不用重啟）。
2. **lane 手動覆寫**：`python -m shared.llm_lane set subscription|openrouter|exhausted --reason "..."`。這個指令直接改寫 VPS `state.db` 的那一列，並留下紀錄，是 D5 狀態機卡住時的急救手段。Bridge 頁面上也放一個同樣功能的按鈕。
3. **S6 之前 VPS 的 `ANTHROPIC_API_KEY` 不刪**。反正它已經沒錢，留著不會被誤用：D2 第 1 項注入憑證時會把它清空。等 S6 驗收通過才移除。

## 遷移 Slices

| Slice | 內容 | 依賴 | 驗收 |
|---|---|---|---|
| **S0 取證**（不改行為） | ① 3 個既有 SDK 呼叫點和 `claude -p` 保存失敗原文，含 F15 欄位；② 修修在 **VPS 和桌機**各跑一行指令，貼出 env 的 key 名稱（U3、U9）；③ 量訂閱模式下 `total_cost_usd` 的語意，以及別名解析結果（U2、U7）；④ 量 VPS 小呼叫的 p50 / p95 延遲和每個子進程的 RSS（U4）；⑤ 修修確認 OpenRouter `data_collection`、Anthropic BYOK 的去留，以及訂閱帳號的 usage credits 設定（U5、U8、Q4）；⑥ 用 OpenRouter env 跑一次別名（U6）；⑦ structured output 小實驗（U10） | — | U2–U10 都有書面答案並寫回本 ADR；U1 的原始資料開始累積 |
| **S1 L1 核心** | `shared/agent_sdk.py` 補齊 D2 的 8 項職責；facade 依 model 字串分派；D9 context helper；D3 憑證合併；Q2 別名化（照 Q5 的結果）；`thousand_sunny/routers/bridge_models.py::_transport_for` 改用 D1 規則和 lane 狀態；**盤點並改寫 21 支綁著 `AUTH_*` / `LLM_TRANSPORT` / `subscription_*` 的測試檔**（清單列在 PR）。**只建模組，不切換任何 production 路徑** | S0 | 單元測試涵蓋：分派、憑證清理、timeout、機器層級 lease、async 情境、巢狀呼叫、用量紀錄、context 傳遞。除了 D6 刻意保留的 `_SLUG_MAP`，code 裡找不到寫死的 `claude-*` id |
| **S2 額度用完的處理** | D5 全部：偵測、per-family 阻擋、`allowed_warning` 讓批次讓路、Franky probe target、決策介面、OpenRouter 模式雙路由、每日 canary、`shared.llm_lane` 手動覆寫 CLI | S1 | 注入假的 `RateLimitEvent` 能走完整條狀態機；VPS 和桌機看到同一個狀態；Bridge 頁面合併前經 dev server + 瀏覽器實際操作過 |
| **S1a–d 分批切換** | 依執行環境分批：**a** gateway（意圖分類、Sanji / Zoro handler、orchestrator、記憶抽取）→ **b** VPS cron（Robin、Franky、Zoro、memory-reflection）→ **c** Bridge（translator、digest、SEO、keyword、Usopp）→ **d** 桌機腳本（Brook repurpose、planner、`subtitle_correct`） | S2 | 每批上線後 72 小時內：`lane_actual` 全部是 `subscription`、沒有 `api`；journal 沒有新增 LLM 錯誤；PR 內文寫明回滾步驟 |
| **S5 Codex → L1** | D7，**RenderWatcher 優先**（D-f） | S2 | 在桌機跑一次真的 packaging job，通過既有驗證；log 裡記到的 model 符合 Q5 |
| **S4 tool-use 呼叫點** | Nami 預設走 SDK；merger 同樣改；`replan_agent` 改用 in-process MCP tools。**舊 loop 和 flag 等到 S6 才刪** | S1a | Nami golden path，含 ask_user 的暫停與恢復；merger 的 capture 契約測試 |
| **S3 L2 自由選模型** | D6；Bridge model 面板接受任意 slug | S1 | 用一個不在舊白名單上的 slug 實際呼叫成功，並記到實際 cost |
| **S7 SDK 自動升級** | D10：每週升級 workflow、CI 讀別名表步驟、自動合併、Franky 升級通知與落後偵測 | S1（精確釘版） | 手動觸發一次 workflow，確認會開 PR、CI 綠了自動合併、收到 DM |
| **S6 清理** | D8 全部（**等 #1300 合併**）；刪掉 S4 留下的舊 loop 和 flag；修修從 VPS `.env` 移除退場的 key；ADR-026 / ADR-049 標記狀態；`CONTEXT-MAP.md` 更新 LLM Router / Auth policy / Fallback reason 詞條，並新增 lane 詞條；`docs/runbooks/openrouter-canary.md` 改寫或標記 deprecated | 以上全部 | 連續 7 天：`api_calls` 除了 `lane_actual` 兩種值以外沒有其他計費路徑，而且沒有 `agent="unknown"` |

S5、S4 可以和 S1a–d 並行。S3 不依賴 S2，隨時可以做。S7 在 S1 之後隨時可以做，但要**排在 S5 之前**：RenderWatcher 要用到最新版 Opus，得先有 S7 把 SDK 升上來。

## 修修要親手做的事

| # | 事項 | 時機 |
|---|---|---|
| 1 | 在 VPS 和桌機各跑一行指令，貼出 env 的 key 名稱（不含值） | S0 |
| 2 | OpenRouter 後台：勾 `data_collection: deny`；**在 Integrations（BYOK）頁刪掉 Anthropic key**（Q4）；設定 Anthropic 1P provider 優先；存入足夠支付上限的 OpenRouter 額度（interactive 每日 US$5、batch 每次 US$20） | S0 |
| 3 | 確認 Claude 訂閱有沒有開 usage credits / overage，決定要不要關掉 | S0 |
| 4 | 在每台機器各跑一次 `claude setup-token`，把 token 寫進各自的 `.env` | S1 前 |
| 5 | 每次部署：`ssh nakama-vps "cd /home/nakama && ./scripts/deploy_vps.sh"` | 每個 slice |
| 6 | 額度用完時：interactive 會自動處理，你只會收到通知；batch 要不要先跑，由你在 Bridge 決定（切 OpenRouter 並設上限，或等重置）。**這會是常態操作，不是一次性步驟** | S2 之後 |
| 6b | 收到 Franky「SDK 已升級」的通知後部署 | S7 之後，每週 |
| 7 | Token 到期前收到 Franky 提醒後：在該機器重跑 `claude setup-token` → 更新 `.env` → 重啟 `nakama-gateway`、`thousand-sunny`（桌機則重啟 Thousand Sunny 排程）。cron 會在下一輪自動讀到新值 | 每年 |
| 8 | 移除 VPS `.env` 裡退場的 key | S6 |
| 9 | D7 完成後，Codex CLI 就不再被 nakama 程式呼叫。ChatGPT / Codex 訂閱要不要留著給自己開發用，由修修決定（跟本 ADR 無關） | S5 之後 |

## 成功指標

1. S6 之後連續 7 天：`api_calls` 裡由 `ANTHROPIC_API_KEY` 計費的筆數為 0，`agent="unknown"` 的筆數為 0。
2. 發生額度用完事件後 5 分鐘內，修修收到 DM；`batch` 呼叫在修修核准之前，沒有任何一筆走 OpenRouter；`interactive` 每日 OpenRouter 花費不超過 US$5。
3. **每週 `batch` 停在 `exhausted` 的時數**，以及每週 interactive 自動轉 OpenRouter 的次數與金額，作為修修調整上限或決策模式的依據。
4. `journalctl -u nakama-gateway` 不再出現 `Memory extraction LLM call failed` / `Episodic extraction LLM call failed`。
5. gateway 意圖分類的 p95 延遲不超過 S0 量測後修修訂下的門檻。

## 風險

| # | 風險 | 緩解 |
|---|---|---|
| R1 | 所有 Claude 呼叫共用一份訂閱額度，大量批次可能擠掉 Nami 的即時對話 | 機器層級的併發上限；收到 `allowed_warning` 時批次讓路（D5）；修修可以把個別高量呼叫點改指定 OpenRouter model（D6、Q3） |
| R2 | 每次呼叫都要起一個子進程，每則 Slack 訊息的意圖分類、每輪對話兩次的記憶抽取都會變慢 | S0 先量；意圖分類本來就先走關鍵字路由（`gateway/router.py:115-139`），失敗才用 LLM；記憶抽取本來就在背景跑。S0 同時評估 `ClaudeSDKClient` 常駐 session 的做法 |
| R3 | VPS 記憶體吃緊 | 機器層級的 lease 上限；S0 量出實際 RSS |
| R4 | 2026-07-29 的研究引用過 Anthropic 對第三方產品使用 claude.ai 登入的限制；ADR-026 也記錄過 OAuth 直連 SDK 時遇到 anti-automation 429 | 本系統是帳號本人自用的內部工具，不是對外產品。429 列入 D5 的觀察項 |
| R5 | SDK 版本漂移（F1）；別名解析結果會跟著內附 CLI 一起變（F3）；自動升級可能帶進 CI 沒抓到的行為改變 | SDK 釘精確版本，只由 D10 的自動 PR 升級（有 CI 把關，0.2.140 那次就是被 CI 擋下）；每次升級都 DM 修修新的別名解析結果；出問題就 revert 那個升級 PR（§回滾） |
| R6 | 失去 `temperature` 控制（F2） | 列為已知的行為改變，由各 slice 的驗收檢查輸出品質 |
| R7 | 訂閱模式下看不到成本（U2） | 用 token 數追蹤用量；只有 OpenRouter 的呼叫記金額 |
| R8 | 桌機長任務（4 小時）中途 token 過期 | 用一年期的 setup-token，不把 `~/.claude/.credentials.json` 裡的短效存取 token 塞進 env（2026-05-16 事故，`memory/claude/feedback_oauth_env_pinning_long_batch.md`） |
| R9 | **修修自己的 Claude Code / Cowork 也吃同一份訂閱**：Nakama 可能把修修開發用的額度吃光，反過來也一樣 | `allowed_warning` 時批次讓路；成功指標 3 會追蹤等待決策的時數；Bridge 頁顯示是哪一類工作用掉了額度 |
| R10 | Opus 被 biology 分類器攔下時，會默默改用較舊的 Opus（F20）。修修的內容是健康主題，容易觸發 | D2 第 6 項記錄每次呼叫實際用的 model；Bridge 用量頁可以看到發生頻率 |
| R11 | 平常不走的 OpenRouter 備援路徑，可能在真正需要時已經壞了 | D5 每天跑一次 canary |

## Considered Options

| 選項 | 內容 | 結論 |
|---|---|---|
| A. 沿用 ADR-026，只把預設改成走訂閱（c7a72880） | 改動最小 | ✗ 仍然有六種入口；`claude -p` 做不了 tool-use；還是要靠 `.env` 補洞（違反 D-d）；8/18 的裁決一個月都沒落地 |
| **B. 兩條 lane：Agent SDK + OpenRouter** | 本 ADR | ✓ 符合 D-a 到 D-g |
| C. 只用 OpenRouter | 最單純 | ✗ 修修長期付 Max 訂閱，額度閒置，還要另外按 token 付費 |
| D. 額度用完就全部自動改走 OpenRouter | 服務不中斷 | ✗ 修修要求先問（D-c）；批次可能以 API 價格大量花錢 |
| E. 用 SDK 的 `fallback_model` | 內建功能 | ✗ 只換 model、不換 lane，而且是默默切換；也不會因 rate limit 觸發（F20） |
| **F. 分類別、有上限的常備授權**（panel reviewer A 提出） | 互動類在額度用完時，於每日上限內自動轉 OpenRouter 並通知；批次類等重置或由修修核准 | ✓ **修修 Q7 選定**（第三輪），併入 D5 |

## 修修裁決（2026-09-24 第二輪）

三題都照建議（修修：「三個都照建議做」）：

1. **Q1 切換範圍 → 全部一起切**。實作方式：VPS 上一列全域狀態，桌機讀取同一份（D5）。
2. **Q2 預設 model → 改用別名**。對應方式：`claude-opus-4-8`、`claude-opus-4-7` → `opus`；`claude-sonnet-4-6`、`claude-sonnet-4-5-20250929` → `sonnet`；`claude-haiku-4-5`、`claude-haiku-4-5-20251001` → `haiku`。這會改變實際使用的 model 版本，屬於已接受的行為改變。取代 2026-08-19 沒合併的「全面 Opus 5」（eb0cb5bb）。panel review 發現別名不會自動跟上最新版（F3），第三輪 Q5 改由 D10 的 SDK 自動升級解決。
3. **Q3 高量或低延遲的呼叫點 → 先全部走 L1**。S0 量完後，修修再決定要不要把個別呼叫點改指定 OpenRouter model。

## 修修裁決（2026-09-25 第三輪，panel review 之後）

依據：[審查紀錄](../research/2026-09-24-adr070-panel-review.md)。修修回覆：「Q4 ok」「Q5 可以，Q6 刪掉，金額都 OK」「Q7 b」。

- **Q4 → 從 OpenRouter 後台移除 Anthropic BYOK key**（F18）。這樣 Claude 呼叫走 OpenRouter 時，扣的是 OpenRouter 的額度，不是已經沒錢的 Anthropic API 帳號。由修修操作。
- **Q5 → 不在 code 裡放 model 對照表，改成 SDK 自動升級**（D10）。修修指出：對照表每次新 model 出來都要有人改，最後一定沒人維護、永遠指到舊版。修修同意 SDK 升級 PR 在 CI 全綠時自動合併。
- **Q6 → 刪掉縮圖 playbook 建置流程**（D8）。產出的 playbook 保留。
- **Q7 → (b) 分類別常備授權**（D5）：`interactive` 在額度用完時自動轉 OpenRouter，每日上限 US$5，事後通知；`batch` 等重置，或由修修在 Bridge 核准。
- **Q8 → batch 每次核准的預設上限 US$20**，按的時候可以改。

## S0 取證結果（2026-09-25）

U2、U3、U7、U9 的事實由 orchestrator 在 2026-09-25 查證，照原樣記錄。最後一小段是實作 S0 PR 時讀 SDK 原始碼和 CLI binary 另外查到的，都附來源。

### 已有答案

- **U2　訂閱模式下 `total_cost_usd` 不是 0**，是 API 等值的估計值。證據：VPS journal `nami sdk result: session=f884a681… subtype=success turns=3 cost_usd=0.06852`（2026-09-24 16:47:20 +08），當時 Anthropic API credit 已經用完。`memory/claude/reference_agent_sdk_supports_oauth.md` 說「cost 回報 0」是錯的。
- **U3　VPS 目前的路由設定**：
  - `/home/nakama/.env` 有 `AUTH_FRANKY`、`AUTH_ROBIN`、`AUTH_ZORO`、`AUTH_SANJI`、`AUTH_GATEWAY`、`AUTH_MEMORY_REFLECTION`，全部是 `=subscription_preferred`；`LLM_TRANSPORT_SANJI=openrouter`；`ROBIN_MERGE_USE_AGENT_SDK=1`；`NAKAMA_CLAUDE_CLI` 指向內附 CLI；`CLAUDE_CODE_OAUTH_TOKEN` 和 `NAMI_SDK_OAUTH_TOKEN` 都有；`OPENROUTER_API_KEY` 和 `XAI_API_KEY` 都有。
  - 沒有 `GEMINI_API_KEY`、沒有 `AUTH_NAMI`、沒有任何 `MODEL_*`。
  - `NAMI_USE_AGENT_SDK=1` 設在 systemd unit `nakama-gateway` 的 `Environment=`，不在 `.env`。
  - Bridge 的 override 檔 `data/model_overrides.json` 把 robin 的 7 個 task 全部釘在 `claude-sonnet-4-6`。
  - 沒有任何 agent 被路由到 OpenAI（跟 U3 引用的 #1173 commit message 說法不同）。
- **U7　版本與別名**：VPS 是 SDK 0.2.128，內附 CLI 2.1.220。桌機 `C:\Python314` 是 SDK 0.2.134（CLI 2.1.226），`E:\nakama\.venv-v2` 是 SDK 0.2.140。兩個 CLI 都解析成 `opus→claude-opus-5`、`sonnet→claude-sonnet-5`、`haiku→claude-haiku-4-5`，兩個都不含 `claude-opus-5-5`。
- **U9　桌機憑證**：`E:\nakama\.env` **沒有** `CLAUDE_CODE_OAUTH_TOKEN`，也**沒有** `OPENROUTER_API_KEY`；有 `ANTHROPIC_API_KEY`、`GEMINI_API_KEY`、`OPENAI_API_KEY`、`XAI_API_KEY`。`~/.claude/.credentials.json` 存在（互動式登入）。Thousand Sunny 和 RenderWatcher 用 `C:\Python314\python.exe` 執行（`scripts/start_thousand_sunny.ps1:17`）。

### 還開著

| # | 狀態 | 怎麼回答 |
|---|---|---|
| U1 | 開始累積原始資料 | 3 個 SDK 呼叫點（Nami、Robin merger、Sanji judge）和 `claude -p` 把原文寫進 logger `nakama.llm_lane`：事件 `sdk_rate_limit`、`sdk_assistant_error`、`sdk_result_error`、`sdk_exception`、`cli_result_error`、`cli_exit_error`；成功的 SDK 呼叫另有一行 `sdk_result_ok`（cost、token 數、實際 model id）。第一次真的用完額度時，從 log 取原文回填 |
| U4 | 待量 | 部署後在 VPS 跑 `scripts/llm_lane_probe.py latency --model haiku --n 5` |
| U6 | 待量 | 部署後在 VPS 跑 `scripts/llm_lane_probe.py openrouter-alias --model haiku`（桌機沒有 `OPENROUTER_API_KEY`，會 exit 2） |
| U10 | 待量 | 部署後跑 `scripts/llm_lane_probe.py structured --model haiku` |
| U5 | 待修修 | OpenRouter 後台 `data_collection: deny` |
| U8 | 待修修 | Claude 訂閱的 usage credits / overage 設定 |

### 實作 S0 時另外查到的（SDK 0.2.134 原始碼、CLI 2.1.226 binary）

- **F15 的欄位在 `RateLimitEvent.rate_limit_info` 底下**，不是直接掛在 `RateLimitEvent` 上。`RateLimitInfo` 的欄位是 `status`、`resets_at`、`rate_limit_type`、`utilization`、`overage_status`、`overage_resets_at`、`overage_disabled_reason`、`raw`（`types.py:1281-1317`）。CLI 在 stdout 送出 `{"type":"rate_limit_event","rate_limit_info":{…camelCase…}}`，SDK 把它當一般 message 轉進 stream（`_internal/query.py:273-367` → `:951-960` → `_internal/client.py:227-230`），由 `_internal/message_parser.py:337-353` 轉成 `RateLimitEvent`。S2 的偵測要讀 `event.rate_limit_info.status`。
- **error result 之後的例外文字**：SDK 的註解說 CLI 回報 `is_error` 的 result 後會故意以非 0 結束（`_internal/query.py:379-384`）。SDK 把接著來的 `ProcessError` 換成 `Exception("Claude Code returned an error result: <errors 用 ; 串起來，沒有就是 subtype>")`（`:349-355`、`:385-398`、`:957-958`）。
- **`ClaudeAgentOptions(env=…)` 只能覆寫、不能刪除**：子進程 env 是 `{**os.environ, **options.env}`（`_internal/transport/subprocess_cli.py:791-797`）。D2 第 1 項要「拿掉」的變數，透過 `options.env` 只能設成空字串。
- **CLI 2.1.226 的別名表還有 `per_provider`**：`opus` 的 `gateway` 欄是 `claude-opus-4-7`，`sonnet` 的 `gateway` 欄是 `claude-sonnet-4-6`（`llm_lane_probe.py aliases` 會印出整張表）。`ANTHROPIC_BASE_URL` 指向 OpenRouter 時，CLI 會不會把它當成 `gateway` provider、改用這一欄，由 U6 的實測回答。
