# ADR-070：LLM 只剩兩條路 — Claude 訂閱（Agent SDK）與 OpenRouter

- **Status**：Accepted（方向：修修 2026-09-24 裁決）。實作依 §遷移 Slices；**S0 的待驗證項沒有答案前，不得切換任何 production 路徑**
- **Date**：2026-09-24
- **Owner**：修修
- **Supersedes after cutover**：[ADR-026](ADR-026-llm-router-auth-dimension.md) 整個 auth 維度（`api` / `subscription_preferred` / `subscription_required` 三元 policy、`AUTH_*` env、`NAKAMA_REQUIRE_MAX_PLAN`、`claude -p` CLI 訂閱路徑）
- **Amends**：[ADR-049](ADR-049-openrouter-transport.md)。OpenRouter 原本是「api-tier 的 transport kill-switch」，改為兩條 lane 之一；`LLM_TRANSPORT*` env 和 xAI carve-out 退場
- **Supersedes（從未合併）**：`origin/feat/default-auth-subscription` 上的 c7a72880「ADR-026 Amendment 2026-08-19」。修修在 2026-08-18 已經裁決「都改成預設使用訂閱額度」，但這個 amendment 從沒進 main
- **Preserves**：ADR-063 字幕正式路徑（本 ADR 只動它周邊的 LLM 呼叫方式）；ADR-054 D11「render 只在桌機」分工
- **Related**：issue #1299、PR #1298（記憶抽取 thread 失去 agent context）、PR #1300（退役 Gemini 音訊仲裁與一次性審查腳本）
- **Principles**：[reliability](../principles/reliability.md) §3 SoT、§5 Retry、§7 Timeout 必填、§9 可觀察的失敗；[observability](../principles/observability.md) §1 三層觀察、§6 Alert 三層；[schemas](../principles/schemas.md) §9 LLM 結構化輸出

---

## 一句話

程式庫裡每一次 LLM 呼叫，只准走兩個地方：**Claude 模型 → Claude 訂閱（Agent SDK）**；**其他模型 → OpenRouter API**。走哪一條由 model 字串決定，政策寫在 code 裡，不靠 `.env` 開關。訂閱額度用完時**不自動改道**：先通知修修，由修修決定要不要把 Claude 呼叫轉到 OpenRouter。

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

仍有效的前序裁決：2026-08-17 全面停用 Gemini，非 Claude 模型預設用 OpenAI 經 OpenRouter（`memory/claude/feedback_no_gemini_default_openai.md`）。

---

## Context

### 現況：六種 LLM 入口並存（2026-09-24 盤點，`origin/main` @ 8a37e666）

| 入口 | 怎麼決定計費 | 呼叫點 |
|---|---|---|
| ① Agent SDK `query` | 子進程 env 塞 OAuth token（`shared/agent_sdk.py:44-47`；Nami 另用 `NAMI_SDK_OAUTH_TOKEN`，`gateway/handlers/nami.py:115-134`） | 3 個：Nami（`NAMI_USE_AGENT_SDK=1` 時）、Robin annotation merger（`ROBIN_MERGE_USE_AGENT_SDK=1` 時）、Sanji judge |
| ② `shared.llm.ask / ask_multi / ask_with_tools` → ADR-026 dispatch | `AUTH_<AGENT>[_<TASK>]` env，沒設就是 `DEFAULT_AUTH["default"]="api"`（`shared/llm_router.py:184-193`）；`api` 再依 `LLM_TRANSPORT*` 決定走原生 SDK 或 OpenRouter | 約 56 個（`ask` 44、`ask_multi` 8、`ask_with_tools` 4），其中 10 個已沒有 production caller |
| ③ 直接 `ask_claude(auth_policy="subscription_required")` → `claude -p` | 寫死 | `agents/usopp/video_description.py:383` |
| ④ 直接 `get_client().messages.stream` | 繞過 router、auth、OpenRouter 與 cost 紀錄 | `scripts/cluster_thumbnail_patterns.py`、`scripts/compose_playbook_v1.py`（一次性） |
| ⑤ Codex CLI `codex exec` | ChatGPT 帳號 | 4 個：`scripts/render_watcher.py:468-487`（`gpt-5.6-sol`）、`agents/brook/script_video/finished_cut_production/_codex_semantic.py`、`scripts/podcast_highlight_visual_orchestrator.py`、`scripts/dispatch_codex_playbook_audit.py` |
| ⑥ 本機 LLM | llama.cpp / Ollama | `scripts/ab_ingest_bench.py`（bench）；Robin ingest 只在估算等待時間時探測本機伺服器（`agents/robin/ingest.py:79-85`），實際生成不用 |

`tool_use` 在 ② 永遠走 API（`shared/anthropic_client.py:368-370`，CLI 載不動 raw tool-use）。

### 事故鏈：為什麼現在要收斂

1. **2026-08-17**：Anthropic **API credit** 用完。Nami / Franky / Robin / memory-reflection 因為走 `api` 預設，一起停擺（commit 4d2c3b47；`docs/plans/2026-08-18-annotation-merger-agent-sdk-plan.md:5,36`）。
2. **2026-08-18**：修修裁決「都改成預設使用訂閱額度」。c7a72880 把 `DEFAULT_AUTH` 翻成 `subscription_preferred`，但**沒有合併**。
3. **2026-09-24**：Nami 背景記憶抽取 3 天內 22 次 400 "credit balance is too low"。原因是 `threading.Thread` 沒繼承 agent ContextVar，於是 `get_auth_policy(agent=None)` 落回 `api`（PR #1298）。

共同根因：**走訂閱是 opt-in**。每個新功能、每條新 thread、每個忘了設 env 的 context，都會默默掉回一個已經沒錢的 API key。

### 查證過的事實

| # | 事實 | 來源 |
|---|---|---|
| F1 | 本機安裝 `claude-agent-sdk` 0.2.134；requirements 釘 `>=0.2.128,<0.3`（`requirements.txt:85`）。0.2.140 曾因上游 API 漂移打破 CI（cf7e7a31） | `pip show`；git |
| F2 | `ClaudeAgentOptions` 共 45 個欄位，含 `output_format`、`fallback_model`、`effort`、`thinking`、`max_thinking_tokens`、`add_dirs`、`skills`、`setting_sources`、`permission_mode`、`can_use_tool`、`max_budget_usd`、`task_budget`、`load_timeout_ms`；**沒有 `temperature`，也沒有 `max_tokens`** | `dataclasses.fields(ClaudeAgentOptions)` |
| F3 | model 可以用別名 `opus` / `sonnet` / `haiku`；`opus` 指向最新的 Opus；實際跑的 model id 可以從 `AssistantMessage.model` 讀到 | code.claude.com/docs/en/model-config |
| F4 | 憑證優先序：`ANTHROPIC_AUTH_TOKEN` > `ANTHROPIC_API_KEY` > … > `CLAUDE_CODE_OAUTH_TOKEN`。API key 會壓過訂閱 token（2026-08-18 已實測：`shared/agent_sdk.py:10-14`） | code.claude.com/docs/en/authentication |
| F5 | `claude setup-token` 產生的是一年期 token | 同上 |
| F6 | Claude Code / Agent SDK 可以用 `ANTHROPIC_BASE_URL=https://openrouter.ai/api` + `ANTHROPIC_AUTH_TOKEN=<OpenRouter key>` + `ANTHROPIC_API_KEY=""` 走 OpenRouter（僅限 Claude 模型）。Anthropic **不支援**經 gateway 跑非 Claude 模型 | openrouter.ai/docs/cookbook/coding-agents/claude-code-integration；code.claude.com/docs/en/llm-gateway |
| F7 | SDK 每次 `query` 會起一個 CLI 子進程，約 100MB（`nami.py:1301`）。VPS 有 3.9G RAM，約 1.7G 空閒 | `docs/research/2026-07-29-agent-sdk-spike-findings.md:83` |
| F8 | 延遲：merger（Opus）單次 14–24s；20 次序列 Haiku 共 89s | `docs/research/2026-08-18-merger-sdk-spike-findings.md:32,38-41` |
| F9 | 預設 options 會載入本機 settings / plugins，成本是 `tools=[]` 版的 3 倍（$0.1102 vs $0.0352） | `docs/research/2026-07-29-agent-sdk-spike-findings.md:24-33` |
| F10 | SDK 的錯誤形狀是「先 yield 一個 error `ResultMessage`，再 raise」 | `docs/research/2026-08-18-merger-sdk-spike-findings.md:49-50` |
| F11 | Slack gateway **沒有**任何 `block_actions` / 互動按鈕 handler；Bridge 有按鈕式 HITL | `gateway/bot.py:283-290`；`thousand_sunny/routers/bridge.py:938-1106` |
| F12 | `shared/alerts.alert("error", …)` 會發 Franky Slack DM，30 分鐘內去重 | `shared/alerts.py:47-93` |
| F13 | OpenRouter 現在是封閉白名單：`get_provider` 只放行 6 個前綴，`_SLUG_MAP` 只有 11 筆，沒對到就 raise | `shared/llm_router.py:201-210`；`shared/openrouter_models.py:19-36,63-66` |
| F14 | OpenRouter 呼叫會記**實際** cost（`usage.include=True`）；BYOK 預設 `allow_fallbacks=False` | `shared/openrouter_client.py:94,214-249`；ADR-049 |

### 尚未查證（S0 必須先回答）

| # | 未知 | 為什麼重要 |
|---|---|---|
| U1 | **訂閱額度用完時，SDK / CLI 回什麼**（錯誤文字、`subtype`、`api_error_status`、有沒有重置時間）。repo 裡沒有任何紀錄；官方文件只寫了 `error_max_budget_usd` | D5 的偵測條件完全取決於它 |
| U2 | 訂閱模式下 `ResultMessage.total_cost_usd` 是 0 還是「API 等值價」。兩份紀錄互相矛盾：`memory/claude/reference_agent_sdk_supports_oauth.md` 說回報 0；`shared/claude_cli_client.py:237-241` 說是 API 等值 | 成本面板與 `max_budget_usd` 煞車還有沒有意義 |
| U3 | VPS `/home/nakama/.env` 的實際路由：`AUTH_*`、`MODEL_*`、`LLM_TRANSPORT_*` 目前設了哪些。#1173 的 commit message 說 2026-08-17 已把 Franky / memory-reflection / Robin 移到 OpenRouter + OpenAI，但 `.env` 不在 repo 裡 | cutover 前後行為要對得上，不能默默換模型 |
| U4 | VPS 上一次性小呼叫（Haiku、`tools=[]`、`max_turns=1`）的 p50 / p95 延遲與記憶體 | 每則 Slack 訊息的意圖分類、Nami 每輪 2 次記憶抽取都受影響 |
| U5 | OpenRouter 帳號的 `data_collection: deny` 設定還沒勾（`docs/runbooks/openrouter-canary.md:10-11`） | 資料政策 |
| U6 | `openrouter_approved` 模式下（`ANTHROPIC_BASE_URL` 指向 OpenRouter），別名 `opus` / `sonnet` / `haiku` 解析出的 model id，OpenRouter 認不認得？ | Q2 改用別名後，D5 切換能不能直接用 |

---

## Decision

### D1　兩條 lane，由 model 字串決定

| model 字串 | lane | 例 |
|---|---|---|
| Claude 別名或 id：`opus`、`sonnet`、`haiku`、`claude-*` | **L1 Claude 訂閱**（Agent SDK） | `model="opus"` |
| OpenRouter slug：`vendor/model`（含 `/`） | **L2 OpenRouter API**（OpenAI-compatible，`shared/openrouter_client.py`） | `model="openai/gpt-5.6-terra"` |

- **沒有第三條路**。退場的有：`ANTHROPIC_API_KEY` 計費、`claude -p` CLI、Gemini / xAI 原生 SDK、Codex CLI、直接 `get_client()`。
- 呼叫端一律透過 `shared.llm` facade（`ask` / `ask_multi` 等簽名不變），由 facade 依 model 字串分派，所以 S1 不必逐一改 56 個呼叫點。
- 「L1 在修修核准下改走 OpenRouter」不是第三條路：同一段 SDK 程式只換子進程 env（見 D5）。

### D2　L1 只有一個實作：`shared/agent_sdk.py`

所有 Claude 呼叫都經過這一個模組，它負責：

1. **憑證注入**：唯一來源是 `CLAUDE_CODE_OAUTH_TOKEN`，注入時同時清空 `ANTHROPIC_API_KEY`（F4）。
2. **一次性文字呼叫的預設值**：`tools=[]`、`setting_sources=[]`、`max_turns=1`（F9）。
3. **結構化輸出**：有 pydantic schema 的呼叫改用 `output_format`（JSON schema，schemas §9），逐步取代 merger 的強制 `tool_choice` 和各處 regex 撈 JSON。
4. **Timeout 必填**（reliability §7）：每次呼叫都有 wall-clock 上限；agentic 長任務（例如 RenderWatcher 4h）用自己的上限。
5. **併發上限**：process 內共用一個 semaphore（VPS 初始值 2，由 U4 的量測結果調整），防止同時跑太多 CLI 子進程吃光記憶體（F7）。
6. **用量紀錄**：每次呼叫寫一筆 `api_calls`，內容包括 `lane_actual`（`subscription` / `openrouter`）、`AssistantMessage.model` 讀到的實際 model、token 數；`cost_usd` 只記 OpenRouter 回報的實際值（U2 未解前，訂閱的 `total_cost_usd` 只當參考、不入帳）。
7. **錯誤原樣保存**：失敗時保存 error `ResultMessage` 的完整欄位和例外文字（F10），給 D5 的分類器用（reliability §9）。

**沒有對應參數、會被丟掉的**：`temperature`、`max_tokens`（F2）。目前傳 `temperature` 的呼叫點（`gateway/router.py:205` 用 0、`agents/robin/source_map_extractor.py:128` 用 0、`agents/zoro/keyword_research.py` 用 0 / 0.5、`agents/brook/synthesize/_outline.py` 用 0.3）接受這個行為改變；輸出品質回歸由各 slice 的驗收涵蓋。

**`ask_multi`** 的多輪訊息沿用 `claude_cli_client.py:88-124` 的做法，攤平成單一 prompt。圖片輸入在遷移後已沒有 production 呼叫點（唯一的 `scripts/extract_thumbnail_features.py` 是一次性腳本，列入 D8）。

### D3　憑證單一化

- **L1**：VPS 和桌機都用 `CLAUDE_CODE_OAUTH_TOKEN`（`claude setup-token`，一年期，F5）。`NAMI_SDK_OAUTH_TOKEN` 併入它。
- **L2**：`OPENROUTER_API_KEY`。
- 全部 cutover 後，從 VPS `.env` 移除 `ANTHROPIC_API_KEY`、`GEMINI_API_KEY`、`XAI_API_KEY`（由修修執行）。拿掉 API key 也順便拆掉 F4 那個「API key 壓過訂閱」的陷阱。
- Franky 在 token 到期前 30 天發提醒（token 建立日期寫在 runbook）。

### D4　政策寫在 code，不在 `.env`

- **退場**：`AUTH_*`、`NAKAMA_REQUIRE_MAX_PLAN`、`DEFAULT_AUTH`、`LLM_TRANSPORT*`，以及遷移用的 flag `NAMI_USE_AGENT_SDK`、`ROBIN_MERGE_USE_AGENT_SDK`（各自 cutover 完就刪）。
- **model 選擇**：預設值在 `MODEL_REGISTRY`（code），修修的即時調整走 Bridge `/bridge/models` override。`MODEL_<AGENT>*` env 退場，讓 runtime 只剩 Bridge 這一個調整入口。

### D5　訂閱額度用完：先通知修修，由修修決定要不要切 OpenRouter

**狀態機**（單一真相存在 `state.db` 的一列，reliability §3；每次轉換都是 atomic 並留下紀錄）：

```
subscription ──(偵測到額度用完)──▶ exhausted_awaiting_decision
exhausted_awaiting_decision ──(修修在 Bridge 按「切到 OpenRouter」)──▶ openrouter_approved
exhausted_awaiting_decision / openrouter_approved ──(訂閱探針成功)──▶ subscription
```

- **`exhausted_awaiting_decision`**：L1 呼叫立刻丟 `SubscriptionExhausted`，**不花 API 的錢，也不默默改道**。同時呼叫 `shared.alerts.alert("error", "llm_subscription_exhausted", …)`，由 Franky DM 修修（F12），訊息附 Bridge 連結。
  - 互動型呼叫（Nami）回覆「訂閱額度用完，已通知修修」。
  - cron 批次照既有的失敗 / 重試語意處理（reliability §5）。
- **決策介面**：Bridge 新增 `/bridge/llm-lane` 頁，顯示從何時開始、哪些呼叫失敗、錯誤訊息裡的重置時間（如果有），以及兩個按鈕：「切到 OpenRouter（同一個 Claude model）」和「維持等待」。Slack 沒有互動按鈕（F11），所以決策放在 Bridge。
- **`openrouter_approved`**：L1 呼叫**同一段 SDK 程式**，只把子進程 env 換成 `ANTHROPIC_BASE_URL=https://openrouter.ai/api`、`ANTHROPIC_AUTH_TOKEN=$OPENROUTER_API_KEY`、`ANTHROPIC_API_KEY=""`（F6）。呼叫點完全不用改，cost 記 OpenRouter 回報的實際值。
- **自動切回訂閱**：只要不在 `subscription` 狀態，每 30 分鐘用訂閱 env 打一次最小的 Haiku 探針；成功就切回 `subscription`，並 DM 修修「已切回訂閱」。依據是 D-c「有訂閱就直接走訂閱」。
- **偵測規則**（受 U1 限制）：
  - S0 先上線「保存所有 SDK 失敗的原始內容」。
  - 分類：401 / OAuth invalid → `auth_error`，另發「token 失效」alert，不進入這個狀態機；符合已確認的額度用完特徵 → 進入 `exhausted_awaiting_decision`。
  - 特徵確認前的暫行規則：10 分鐘內、至少 2 個不同呼叫點、累計至少 3 次「未分類的 SDK 失敗」→ 視為「疑似額度用完」，同樣進入狀態機，但 DM 文字寫「疑似」並附原始錯誤。
- **不使用** SDK 的 `fallback_model`，因為它會默默換 model，違反 D-c。

### D6　L2：程式裡可以自由指定 OpenRouter 上任何 model

- registry、Bridge override 或呼叫點直接寫的 `vendor/model` slug，一律原樣送 OpenRouter。檢查方式是查 OpenRouter `/models`（快取），不再用手動維護的白名單（F13）。`_SLUG_MAP` 只在遷移期間保留給舊的裸 id，之後刪掉。
- 非 Claude 模型預設選 OpenAI（2026-08-17 裁決）。
- 保留 ADR-049 的 BYOK 與 `allow_fallbacks=False`。
- L2 支援文字和多輪。tool-use 和圖片等到有實際呼叫點需要時才做（遷移後沒有）。

### D7　Codex CLI 呼叫全部改走 L1

| 呼叫點 | 改法 |
|---|---|
| `scripts/render_watcher.py` 的 packaging job | 用 SDK agentic 呼叫，**`model="opus"`（最新 Opus，D-f）**。`cwd=job_dir`；`add_dirs` 包含 working episode、vault packaging、vault cutout 三個目錄，加上 repo root（skill 要執行 repo 內的 script）。工具開 Bash / Read / Write / Edit / Glob / Grep / 子代理 / WebSearch。寫入範圍不只寫在 prompt 裡，另外用 `can_use_tool` 在 code 層卡住：只准寫那四個路徑。wall-clock 上限 4h（沿用 `timeout=14400`）。成功判定不變（`_validate_initial_packaging_outputs`），另外記錄 `AssistantMessage.model`。`tests/test_render_watcher.py:437-468` 目前鎖的是 `gpt-5.6-sol`，要改成鎖 `opus` |
| `.../finished_cut_production/_codex_semantic.py` | SDK 一次性呼叫 + `output_format`（對應現在的 `--output-schema`），model `opus` |
| `scripts/podcast_highlight_visual_orchestrator.py` | SDK agentic 呼叫，model `opus` |
| `scripts/dispatch_codex_playbook_audit.py` | 一次性腳本 → D8 退役 |

### D8　退役清單（D-g：沒用到的直接刪）

刪除前每一項都要再確認一次：grep 範圍包含 `.claude/skills/**`、`.agents/skills/**`、`thousand_sunny/templates/**`、`cron.conf`、`scripts/*.ps1`。

- **退場的 lane 程式**：`shared/claude_cli_client.py`；`shared/anthropic_client.py` 的 API-key 路徑、`call_claude_with_tools`、`get_client`；`shared/gemini_client.py`；`shared/xai_client.py`；`shared/llm.py` 的 `ask_with_audio`；`shared/llm_transport.py` 的 env 開關；`google-genai` 依賴；`pyproject.toml:143` 殘留的 ruff ignore；`podcast_subtitles/production.py` 的 `ALLOW_PAID_GEMINI` guard。
- **沒有 production caller 的 LLM 程式**（盤點 §F，2026-09-24 已用 grep 複核）：
  - 整支刪：`agents/brook/line1b_extractor.py`、`shared/coverage_classifier.py`、`shared/figure_triage.py`（repo 內零引用）；`agents/brook/seo_narrow.py`、`agents/robin/style_extractor.py`（只剩 `thousand_sunny/templates/bridge/inventory.html`、`thousand_sunny/static/crew/index.html`、`CONTENT-PIPELINE.md` 在提，刪的時候一起改）。
  - **只刪 LLM 函式、模組保留**：`agents/brook/podcast_carousel_copy.py` 的 `generate_copy_spec` 等 LLM 函式、`agents/brook/podcast_carousel_panel.py` 的 LLM 評審函式。這兩個模組的其他部分仍被 `scripts/run_podcast_carousel.py:15-16`、`scripts/podcast_carousel_correction_job.py:20` 使用。
  - 分支 / 設定：`agents/robin/kb_search.py` 的 `engine="haiku"` 分支；`podcast_subtitles/adapters/correction.py`、`semantic.py` 的付費 runner（production 固定 `allow_paid_api=False`）；registry 裡沒人用的 `project_angle_scan`、`project_mechanism`、`thumbnail_*`；`scripts/Invoke-IngestTextbook.ps1`（它呼叫的 `run_s8_batch.py` 已不存在）。
- **本機 LLM**：`shared/local_llm.py`、`config.yaml` 的 `local_llm` 區塊、`scripts/ab_ingest_bench.py`。Robin ingest 生成摘要早就一律走雲端（`agents/robin/ingest.py:510-519`），只剩等待時間估算還在探測 `localhost:8080`（`agents/robin/ingest.py:79-85`），這段改成固定用雲端的估算區間。
- **一次性腳本**：`scripts/extract_thumbnail_features.py`、`scripts/cluster_thumbnail_patterns.py`、`scripts/compose_playbook_v1.py`、`scripts/dispatch_codex_playbook_audit.py`、`scripts/spikes/agent_sdk_probe.py`、`scripts/spikes/merger_sdk_probe.py`。

### D9　每次 LLM 呼叫都要帶 agent context

- `shared/llm_context.py` 提供 `spawn_thread` / `submit` helper（內部用 `contextvars.copy_context().run`），開 thread 呼叫 LLM 時必須用它。
- 盤點出的缺口一次補齊：
  - `memory_extractor`（#1298 已修）
  - `fb_renderer` 的 ThreadPoolExecutor、整條 `run_repurpose`
  - Zoro scout cron（`agents/zoro/brainstorm_scout.py`）
  - Bridge 裡除了 translate / SEO / keyword 以外的所有路由
  - Robin merger 路由（`thousand_sunny/routers/robin.py:705-708`）
  - Sanji judge（`agents/sanji/judge.py`）
  - `agents/zoro/keyword_research.py:142` 設了 `zoro` 卻沒還原
- 沒帶 context 的呼叫照樣執行，但記為 `agent="unknown"` 並打 warning（observability §1）；S6 驗收時 `unknown` 必須是 0。

---

## 遷移 Slices

| Slice | 內容 | 依賴 | 驗收 |
|---|---|---|---|
| **S0 取證**（不改行為） | ① 3 個既有 SDK 呼叫點 + `claude -p` 失敗時保存原始內容；② 修修在 VPS 跑一行指令貼出 `.env` 的 key 名稱（U3）；③ 訂閱模式下量 `total_cost_usd` 的語意、`opus` 解析成哪個 model（U2、F3）；④ VPS 上小呼叫的 p50 / p95 延遲與記憶體（U4）；⑤ 修修確認 OpenRouter `data_collection`（U5）；⑥ 用 OpenRouter env 跑一次 `opus` / `sonnet` / `haiku` 別名（U6） | — | U2–U6 有書面答案，寫回本 ADR；U1 的原始內容開始累積 |
| **S1 L1 核心** | `shared/agent_sdk.py` 補齊 D2 七項職責；facade 依 model 字串分派；D9 context helper；D3 憑證合併；Q2 registry 與寫死的 Claude id 改用別名。**只建模組，不切任何 production 路徑** | S0 | 單元測試：分派、憑證注入、timeout、semaphore、用量紀錄、context 傳遞；`git grep` 在 code 裡找不到寫死的 `claude-*` id |
| **S2 額度用完處理** | D5 狀態機 + 分類器 + Franky DM + `/bridge/llm-lane` + OpenRouter 模式 + 自動切回探針 | S1 | 注入假錯誤能走完整條狀態機；Bridge 頁在合併前經 dev server + 瀏覽器實際操作過 |
| **S1a–d 分批切換** | 依執行環境分批：**a** gateway（意圖分類、Sanji / Zoro handler、orchestrator、記憶抽取）→ **b** VPS cron（Robin、Franky、Zoro、memory-reflection）→ **c** Bridge（translator、digest、SEO、keyword、Usopp）→ **d** 桌機腳本（Brook repurpose、planner、`subtitle_correct`） | S2 | 每批上線後 72 小時：`lane_actual` 全部是 `subscription`、沒有 `api`；journal 無新增 LLM 錯誤 |
| **S5 Codex → L1** | D7，**RenderWatcher 優先**（D-f） | S2 | 在桌機跑一次真實 packaging job 並通過既有驗證；log 記到的 model 是最新 Opus |
| **S4 tool-use 呼叫點** | Nami 改成預設走 SDK，刪舊 loop 和 flag；merger 同樣處理；`replan_agent` 改用 in-process MCP tools；刪 `call_claude_with_tools` | S1a | Nami golden path + ask_user pause / resume；merger 的 capture 契約測試 |
| **S3 L2 自由選模型** | D6，Bridge model 面板接受任意 slug | S1 | 用一個不在舊白名單的 slug 實際呼叫成功，並記到實際 cost |
| **S6 清理** | D8 全部；修修移除 VPS `.env` 退場的 key；ADR-026 與 ADR-049 標記狀態；`CONTEXT-MAP.md` 更新 LLM Router / Auth policy / Fallback reason 詞條、新增 lane 詞條 | 以上全部 | 7 天內 `api_calls` 沒有 `lane_actual` 以外的計費路徑，也沒有 `agent="unknown"` |

S5 和 S4 可以跟 S1a–d 並行。S3 不依賴 S2，任何時候都能做。

## 成功指標

1. S6 之後 7 天：`api_calls` 裡 `ANTHROPIC_API_KEY` 計費筆數為 0，`agent="unknown"` 筆數為 0。
2. 額度用完事件發生後 5 分鐘內，修修收到 DM；從偵測到修修按下決策之間，走 OpenRouter 的 Claude 呼叫是 0 筆。
3. `journalctl -u nakama-gateway` 不再出現 `Memory extraction LLM call failed` / `Episodic extraction LLM call failed`。
4. gateway 意圖分類的 p95 延遲不超過 S0 量測後訂下的門檻（門檻由修修在 S0 結果出來後決定）。

## 風險

| # | 風險 | 緩解 |
|---|---|---|
| R1 | 所有 Claude 呼叫共用一份訂閱額度，大量批次（翻譯、ingest、字幕校正）可能擠掉 Nami 的即時對話 | semaphore；S0 量用量；修修可以在 registry 把特定高量呼叫點改指定 OpenRouter model（D6） |
| R2 | 每次呼叫都起子進程，每則 Slack 訊息的意圖分類和每輪 2 次記憶抽取會變慢 | S0 先量；關鍵字路由優先（`gateway/router.py:117-139` 已有）；記憶抽取本來就在背景跑 |
| R3 | VPS 記憶體：每個 CLI 約 100MB | semaphore 上限；S0 量峰值 |
| R4 | 2026-07-29 研究引用過 Anthropic 對第三方產品使用 claude.ai 登入的限制，ADR-026 也記錄過 OAuth 直連 SDK 遇到 anti-automation 429 | 這是帳號本人自用的內部工具，不是對外提供的產品；把 429 納入 D5 分類器的觀察項 |
| R5 | SDK 版本漂移（F1） | S1 把版本釘死到 patch |
| R6 | 失去 `temperature` 控制（F2） | 列為已知行為改變，由各 slice 驗收輸出品質 |
| R7 | 訂閱模式下成本不可見（U2） | 用量改以 token 數追蹤；只有 OpenRouter 記錢 |
| R8 | 桌機長任務（4h）遇到 token 過期 | 桌機也用一年期 setup-token，不把 `~/.claude/.credentials.json` 裡的短效存取 token 釘進 env（2026-05-16 事故，見 `memory/claude/feedback_oauth_env_pinning_long_batch.md`） |

## Considered Options

| 選項 | 內容 | 結論 |
|---|---|---|
| A. 沿用 ADR-026，只把預設翻成訂閱（c7a72880） | 改動最小 | ✗ 仍有六種入口；`claude -p` 做不了 tool-use；仍靠 `.env` 補洞（違反 D-d）；8/18 的裁決落地一個月都沒合併 |
| **B. 兩條 lane：Agent SDK + OpenRouter** | 本 ADR | ✓ 符合 D-a 到 D-g |
| C. 只用 OpenRouter | 最單純 | ✗ 修修長期付 Max 訂閱，額度閒置、另外按 token 付費 |
| D. 額度用完自動改走 OpenRouter | 不中斷 | ✗ 修修明確要求先問（D-c） |
| E. 用 SDK `fallback_model` 處理額度問題 | 內建 | ✗ 只換 model、不換 lane，而且是靜默切換 |

## 修修裁決（2026-09-24 第二輪）

三題都照建議（修修：「三個都照建議做」）：

1. **Q1 切換範圍 → 全部一起切**。額度用完、修修在 Bridge 核准後，所有 L1 呼叫一起進入 `openrouter_approved`；切回訂閱也是全部一起。D5 的狀態機因此只有一列全域狀態。
2. **Q2 預設 model → 改用別名**。`MODEL_REGISTRY`、`DEFAULT_MODELS`，以及呼叫點寫死的 Claude id，一律改成 `opus` / `sonnet` / `haiku`，自動跟最新版。對應方式：`claude-opus-4-7` → `opus`；`claude-sonnet-4-6`、`claude-sonnet-4-5-20250929` → `sonnet`；`claude-haiku-4-5`、`claude-haiku-4-5-20251001` → `haiku`。這會改變實際使用的 model 版本，屬於已接受的行為改變；每次呼叫實際跑的 model 由 D2 第 6 項記錄。取代 2026-08-19 沒合併的「全面 Opus 5」（eb0cb5bb）。在 S1 核心一併完成。
3. **Q3 高量或低延遲的呼叫點 → 先全部走 L1**（翻譯、意圖分類、記憶抽取）。S0 量出延遲和額度用量後，修修再決定要不要把個別呼叫點改指定 OpenRouter model（D6）。
