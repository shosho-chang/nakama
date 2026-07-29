# Nami Agent 架構：調查結果與決策紀錄

**日期**：2026-07-29
**狀態**：決策已定，實作未開始（S0 探針待跑）
**相關**：
- PR [#1107](https://github.com/shosho-chang/nakama/pull/1107) — 跨日日期 bug 修復（已開）
- `docs/plans/2026-07-29-nami-agent-sdk-migration-plan.md` — 六 slice 遷移計畫

> 這份文件記錄的是**為什麼**。實作步驟看上面的 plan。

---

## 0. 起點：2026-07-29 的日期事故

Nami 在 Slack 上把「今天下午兩點」的電子報排到了 **07-28（昨天）**，被追問日期時也堅稱今天是 07-28。

### 根因（已修，PR #1107）

| 位置 | 事實 |
|---|---|
| `gateway/handlers/nami.py:897` | 日期每次現算，但**只在 `handle()`（新對話第一則）注入 `messages[0]`** |
| `gateway/handlers/nami.py:939` | `continue_flow()` 只 append 使用者原話，**完全沒有日期** |
| `gateway/conversation_state.py:34` | `DEFAULT_TIMEOUT_SECONDS = 86400`（24 小時） |
| `gateway/bot.py:186` | DM 無 `thread_ts` 時走 `get_latest_for_user_and_agent()` 撈回最新活躍 conversation |

前一天 12:00 開的 thread，隔天 10:11 續談（22h，仍在窗內）→ 撈回同一個 conversation → `continue_flow()` → 模型讀 `messages[0]`，那裡白紙黑字寫著 07-28。

**它不是幻覺。** 它手上唯一那份日期資料就是昨天的，而且是我們親手塞進去的。逼它呼叫 `list_calendar_events` 才拿到真相 —— 因為那個 tool 走 `datetime.now(ZoneInfo("Asia/Taipei"))`，是 server 端現算的。

### 為什麼選「重寫」而非「附加」

附加新日期會讓 context 同時存在兩個互相矛盾的「今天」，賭模型選對；重寫直接把過期那份換掉，全程只留一個日期事實。

### 為什麼 morning brief 修不了這個

Morning brief 在 05:00 開的是**另一個 conversation**。舊 thread 只要還在 24h 內就會被撈回，日期照樣是舊的。就算 morning brief 剛好變成「最新」而順手蓋過去，那是副作用不是機制 —— 跨午夜的長對話、直接回覆舊 thread（帶 `thread_ts`）、cron 掛掉，全部破功。

**Morning brief 該做，但當獨立功能做，不是當 bug fix。**

---

## 1. 現況盤點

### Nami 的執行機制

**手寫的 manual agent loop**，不是任何 SDK：

- `gateway/handlers/nami.py:_run_loop()` — `for _ in range(15)` → `ask_with_tools()` → 檢查 `stop_reason` → 執行 tool → `tool_result` append → 再一輪
- 底層 `shared/llm.py` → `shared/anthropic_client.py:call_claude_with_tools()` → `client.messages.create()`

用 Anthropic 分類：四種 agent 做法裡**最底層**那一種（Claude API + 自寫 loop）。

- 28 個 tool 手寫 JSON Schema 在 `NAMI_TOOLS`
- 27 個 `_tool_*` 實作，簽章皆為 `(input_: dict) -> _ToolOutcome`
- 業務邏輯在 `shared/lifeos_writer.py` 等共用模組（Bridge 網頁的「新增任務」按鈕走同一條路）
- `ask_user` 是自己疊的特例：不執行、存 messages 進 SQLite、進程結束、下則訊息接回

### Model

原本硬寫 `_MODEL = "claude-sonnet-4-6"`，**繞過 `shared/llm_router.py`** → Bridge `/bridge/models` 面板改 Nami 的 model 不會生效。PR #1107 已改走 router。

Anthropic 沒有 floating alias（沒有 `claude-sonnet-latest`），**無法自動跟版**。能做到的是「單一改動點」，不是「自動升級」。

> repo 內另有 15+ 處硬寫 `claude-sonnet-4-6`（brook renderers、line extractors、`shared/digest_ask.py`、scripts）同樣繞過 router。未處理。

---

## 2. Subscription quota 的真相

### Nakama 現有機制

已經有完整的 auth policy 系統：

- `shared/llm_router.py:get_auth_policy()` — `api` / `subscription_preferred` / `subscription_required`
- `shared/claude_cli_client.py` — 走 `claude --print` subprocess，吃 Max Plan 額度
- `shared/anthropic_client.py:_plan_dispatch()` — 決定走 SDK 還是 CLI

**但 tool-use 用不了這條路。** `call_claude_with_tools` 寫死 `supports_cli=False`，註解：

> CLI subprocess can't carry raw tool-use JSON.

`claude -p` 是「給 prompt、回文字」，tool 迴圈跑在 CLI 進程**內部**，外面拿不到 `tool_use` block。而且 `claude_cli_client.py` 刻意傳 `--tools ""`。

**現況：要 tool use → 只能 API credit；要 subscription → 只能純文字。**

### Claude Agent SDK 也不行

官方文件（overview + quickstart 兩處）明文：

> Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, **including agents built on the Claude Agent SDK**. Please use the API key authentication methods described in this document instead.

列出的 auth 只有 `ANTHROPIC_API_KEY` / Bedrock / Claude Platform on AWS / Vertex / Foundry。**沒有 subscription 選項。**

> ⚠️ 這推翻了討論早期的假設。「改寫成 Agent SDK 來省錢」不成立。

### OpenAI 那邊可以（唯一的例外）

| 路徑 | 能用訂閱額度？ |
|---|---|
| **Codex SDK / `codex exec` / scriptable workflows** | ✅ 官方 pricing 能力矩陣在 Plus / Pro / Business / Enterprise 全標 available |
| Codex cloud | ✅ 且**只能**用 ChatGPT 登入 |
| OpenAI Agents SDK | ❌ 只吃 API key |

官方 CI/CD 指南的保留：

> **The right way to authenticate automation is with an API key.** ... **API keys are still the recommended option for most CI/CD jobs.**

限制：runner 須為可信私有基礎設施、單一 `auth.json` 不可並行、**禁用於公開 repo**。

**價值不在 Nami，在批次工作** —— `subtitle-correct` 現在走 `claude -p` 有訂閱額度但沒 tool；Codex 那條是「訂閱額度 + 有 tool + 有 sandbox」。**列為獨立實驗，暫緩。**

---

## 3. 為什麼選 Claude Agent SDK

### 決定性理由：32 個 Skill

`.claude/skills/` 有 32 個 skill（audio-prep、podcast-pipeline、composer、title-brainstorm、thumbnail-brainstorm、subtitle-*、seo-*…）。

| | 自寫 loop | Agent SDK |
|---|---|---|
| 能用這 32 個 skill | **完全不能**（Skill 是 Claude Code harness 產物） | **原生載入**，`setting_sources=["project"]` |

修修的使用情境是「Slack 呼叫 agent → 用既有 skill 產出東西」。這在自寫 loop 上做不到。**比 compaction、tool search 都更決定性** —— 那些是「省錢、跑得久」，這個是「能不能做」。

### 次要理由

| 缺口 | 現況 | Agent SDK |
|---|---|---|
| Context compaction | 無（24h TTL 在遮這個問題） | 內建 |
| Tool search | 無（28 個 schema 每輪全進 context） | 預設開啟 |
| Hooks | 無 | `PreToolUse` / `PostToolUse` / `Stop` |
| Subagent | 無 | 內建 |
| Budget cap | `_MAX_ITERS = 15` | `max_turns` + `max_budget_usd` |
| pause/resume | 自寫 SQLite | `can_use_tool` + `defer` + session resume |

### 為什麼不是 Managed Agents

Nami 的 tool 綁在**自己的基礎設施**上：Google Calendar OAuth、Obsidian vault 實體檔案、Slack、`state.db`、NAS 反向隧道。CMA 跑在 Anthropic 的 container，這些全要靠 vault 憑證 + custom tool 繞回來，更複雜。

> **理由要記住**：是「tool 綁在自家基礎設施」，不是「SDK 比較新」。將來若有 agent 的 tool 完全在雲上（純 web research、純文件生成），CMA 反而是更好的答案。

---

## 4. 兩陣營對照

### 根本差異

| | Anthropic | OpenAI |
|---|---|---|
| 策略 | **縱向整合** — 同一套 agent loop 貫穿 SDK / 代管 / GUI | **橫向分裂** — API 側（Responses API + Agents SDK）與 ChatGPT 側（Codex → ChatGPT Work → Workspace Agents）兩條互不共用 runtime |

### 四層 + 非開發者層

| 層 | Anthropic | OpenAI |
|---|---|---|
| 手寫 loop | Messages API | Chat Completions / Responses API |
| 薄 tool runner | `messages.tool_runner` | **沒有這一層** |
| 完整 harness | **Claude Agent SDK** | **OpenAI Agents SDK** |
| 代管 agent | Managed Agents（單一產品） | **沒有單一產品**，散成三塊（Responses API hosted shell / Agents SDK sandbox「compute 外包第三方，清單裡沒有 OpenAI 自家」/ Workspace Agents） |
| 非開發者 | **Cowork** | **ChatGPT Work + Workspace Agents** |

功能面（compaction、tool search、session、HITL、hooks、guardrails、subagent、Skills）**沒有一邊碾壓另一邊**。真正差別在下面三點。

### 差異 1：汰換率 —— 對單人維運是最重要的一項

| 產品 | 命運 |
|---|---|
| Agent Builder（no-code canvas） | 2025-10 發表 → 2026-06 宣告退役 → 2026-11-30 關閉。**GA 到死亡 8 個月** |
| Assistants API | **始終沒離開 beta 就被砍**，且不提供自動資料遷移 |
| Evals | 退役，官方建議改用第三方 Promptfoo |
| Agents SDK | 16 個月、110 個 release、**仍是 v0.19.0** |

Anthropic 這邊 Claude Code SDK → Claude Agent SDK 是**改名不是斷代**，harness 有連續血脈。

**這一項本身就足以支持主線押 Anthropic。**

### 差異 2：Skills 是開放標準，資產沒被鎖住

- 標準由 **Anthropic 開發並開源**（agentskills.io），格式 `SKILL.md` + frontmatter
- OpenAI 文件明寫 skills「compatible with the open Agent Skills standard」，Codex 從 `.agents/skills/` 載入

**但可攜的是格式，不是執行。** Nakama 的 skill 大量呼叫 repo 內 Python script、`G:\footages\` 路徑、DaVinci Resolve、本機 GPU。換陣營後 `SKILL.md` 讀得懂，裡面叫的東西未必跑得起來。路徑慣例也不同（`.claude/skills/` vs `.agents/skills/`）。

### 差異 3：非開發者的答案 —— 兩家獨立收斂

OpenAI **砍掉**視覺化 no-code builder，官方遷移建議：

> For workflows that should continue as code, we recommend the **Agents SDK**. For use cases better suited to natural language prompting, we recommend **Workspace Agents in ChatGPT**.

也就是說 OpenAI 認為「非開發者用拖拉介面建 agent」這條路**不成立**。

**兩家獨立收斂到同一個結論：職場／知識工作者應該用聊天產品（Cowork / ChatGPT Work），不要建 agent。**

分界線：

| | Cowork | 自建 Agent |
|---|---|---|
| 你在不在場 | **在**。你委派、它做、你看成果 | **不在**。自己被觸發、自己跑完 |
| 接誰的系統 | 官方連接器（Slack、Drive、本機資料夾） | **你自己的**：DB、內部 API、自寫 pipeline、OAuth |
| 誰維護 | Anthropic | 你 |

用這條線量 Nakama：

- **Nami 排任務** → Slack 觸發、你不在場也要跑、要寫 vault、要動 Calendar、要 rollback → **超出 Cowork，自建 agent 正確**
- **Brook 產文章** → 初步看像是「你在場、你委派、你看成果」，一度以為 Cowork 就夠

### ✅ 已查證：Cowork 不適用（2026-07-29 更新）

原本的假設「Brook 可以走 Cowork，而且 Cowork 跑在桌面正好能讀本機素材」**兩個前提都不成立**：

| 問題 | 查證結果 | 出處 |
|---|---|---|
| 能從 Slack 觸發嗎 | **不能。** 官方「Assign tasks from anywhere」列出的觸發面**只有手機與桌面**，全文無 Slack 觸發或 @-mention | [Assign tasks from anywhere](https://support.claude.com/en/articles/13947068-assign-tasks-from-anywhere-in-claude-cowork) |
| Slack 的角色 | **純連接器** —— 執行中可讀 Slack 資料、可 post 回 channel，但不是入口 | 同上 |
| 在哪執行 | **遠端**：「Cowork runs your tasks remotely (in beta). Claude's work runs on Anthropic's servers, in an isolated environment.」 | [Get started](https://support.claude.com/en/articles/13345190-get-started-with-claude-cowork) |
| 排程能讀本機檔案嗎 | **不能。**「Scheduled tasks cannot access local computer folders — they work with your connectors and the files saved to your Claude account.」需要本機檔案的任務只在本機跑，但那就不能排程 | [Schedule recurring tasks](https://support.claude.com/en/articles/13854387-schedule-recurring-tasks-in-claude-cowork) |
| 排程粒度 | hourly / daily / weekly / weekdays / manually。**沒有 cron**、沒有任意時間 | 同上 |
| 結果送到哪 | 留在 Cowork 自己的 UI（側邊欄 Scheduled），不主動推送 | 同上 |

**「本機檔案」與「排程」在 Cowork 是互斥的** —— 而修修的重媒體 skill 兩者都要。

**❌ 已否決**：「動手寫 Brook agent 之前先花半天試 Cowork」這條建議取消。**Brook 也走 Agent SDK。**

---

## 5. Memory 架構（修修最在意的議題）

### 「memory」這個詞裡塞了三種東西

| 層 | 是什麼 | 例子 |
|---|---|---|
| **L1 對話記憶** | 這輪對話記得上一句 | 「剛剛那個任務」指的是哪個 |
| **L2 跨 session 學習** | Agent **自己寫**的筆記 | 「這個 repo 測試要先跑 X」 |
| **L3 領域事實** | 關於使用者／專案的**結構化**資料 | 「番茄鐘 30 分鐘」 |

### 兩家逐層對照

| 層 | Anthropic | OpenAI |
|---|---|---|
| **L1** | Session：JSONL 存 `~/.claude/projects/<encoded-cwd>/*.jsonl`，可 resume / **fork**；跨機器需 `SessionStore` adapter | Session：**後端可插拔**（SQLite / Redis / SQLAlchemy / MongoDB / Dapr / Encrypted） |
| **L2** | **Auto memory（預設開啟）**：`~/.claude/projects/<project>/memory/`，`MEMORY.md` 索引每 session 載入**前 200 行或 25KB**，topic 檔按需讀。以 **git repo 為 key**，worktree 共用 | **Sandbox Memory**：`memories/` 下 `MEMORY.md` + `memory_summary.md`（run 開始注入）+ `rollout_summaries/` + `raw_memories/`。兩階段：抽取 → 整併（raw 超過 256 汰舊） |
| **L3** | **Memory tool**（`memory_20250818`，client-side，後端自己實作）／**Managed Agents memory stores**（server 端、不可變 version、可 redact、sha256 樂觀鎖） | 僅「結構化 state 物件跨 run 持久化」，**要自己設計** |
| 人寫指令 | **CLAUDE.md**（managed policy → user → project → local，`@import`，`.claude/rules/` 可按路徑條件載入） | AGENTS.md / Skills |

### 兩家獨立收斂到同一個設計

L2 兩邊都是 **`MEMORY.md` 索引 + topic 檔按需載入 + 定期整併**。不是誰抄誰 —— 同一個約束（context 有限、知識要累積）逼出同一個解。

**Nakama 已經在這個形狀上**：`memory/claude/MEMORY.md` + 個別記憶檔 + `_archive/YYYY-MM/` rotate + `memory_maintenance.py reindex`。

### 三個決定性差異

**① 持久性模型 —— Anthropic 明顯穩健**

| | 記憶活多久 |
|---|---|
| Claude auto memory | **磁碟目錄，以 git repo 為 key**。session 結束、進程重啟、換 worktree 都還在 |
| OpenAI sandbox memory | **綁 sandbox 生命週期**。官方原文：「A fresh empty sandbox starts with empty memory」 |

對 24/7 常駐的 Nami，Anthropic 對太多。OpenAI 那個更適合「跑一批任務就收工」。

**② Auto memory 是 machine-local —— 跟 Nakama 直接衝突**

官方：「Auto memory is machine-local. Files are not shared across machines or cloud environments.」

而 CLAUDE.md 已經寫了「所有記憶在 repo 內 `memory/`，git 跨平台共用」—— 修修有 VPS + Windows 兩台機器，machine-local 會直接分裂。**這個判斷是對的。**

**解法存在**：`autoMemoryDirectory` 可指向任意絕對路徑（設在 project settings 時需先接受 workspace trust 對話框）。

**③ L3 Nakama 已經做得比兩家原生方案都深**

`shared/agent_memory.py` + `memory_extractor` / `memory_reflection` / `episodic_memory` / `memory_maintenance` / `memory.py` 共 6 檔：

- confidence × recency 排序（`confidence * (1/(1 + hours_since_access))`）
- decay（30 天未存取 `confidence *= 0.9`）
- **bi-temporal 軟失效**（`superseded_by` / `invalidated_at`，留痕可回溯）
- 重新斷言會**復活**被失效的記憶
- episodic → semantic promotion
- LLM reflection 定期整併

Anthropic 的 memory tool 只給檔案系統介面；Managed Agents memory stores 有版本與稽核但沒有 confidence/decay 語意。OpenAI 這層沒有成品。

### 對遷移的結論

- **L3（`agent_memory` 及周邊 5 檔）完全不動**，繼續走 `_build_context_preamble()` 注入
- **L1** 由 Agent SDK Session 接手（取代 `conversations.db` 存整份 messages）
- **L2 先關掉** —— 見下方決策

---

## 5b. 目標形態：多 bot + 自主排程（修修 2026-07-29 提出）

理想使用情境：**每個 agent 在 Slack 上是獨立 bot，有自己的名字與頭像**；而且 **bot 能自己排 cron job** —— 早上做 brainstorm、agent 之間互相討論。

### Cowork 辦不到（三項硬限制）

| 需求 | Cowork 現實 |
|---|---|
| 每個 agent 是獨立 Slack bot，有名字有頭像 | Cowork 是 Anthropic app 裡的「一個 Claude」。無自訂 bot 身分、無頭像、不在 Slack 呈現成多角色 |
| bot 自己排 cron | 排程由使用者透過 UI 建（`/schedule` 或「Create with Claude」協助）。**agent 沒有程式化建立／修改自己排程的能力** |
| bot 之間互相討論 | 無多 agent 概念 |

加上入口是 Slack 而 Cowork 不從 Slack 進來 → **確認 Cowork 不適用**。

### Nakama 現況已經是這個形狀

| 要素 | 現況 |
|---|---|
| 多 bot 單進程 | `gateway/bot.py`；`conversation_state.py` 註解：「the gateway serves all three bots from one process across Slack-SDK threads」 |
| handler 分家 | `nami.py` / `sanji.py` / `zoro.py` / `orchestrator.py` |
| thread 認 bot | 「其他 bot 的 thread，不要搶」 |
| 晨間排程 | `cron.conf`：Zoro scout 05:00、Robin daily_review 05:15、Robin pubmed 05:30、Franky news 06:30 |
| agent 互相呼叫 | Nami 的 28 個 tool 裡有 `ask_zoro` |

**所以要做的不是新架構，是「換 harness + 補上自主排程」。** Agent SDK 換的是 harness；Slack gateway、bot 身分、cron 這些保留 —— 這降低了整體風險。

### 自主排程：現況落差與設計

| | 現況 | 目標 |
|---|---|---|
| 排程定義 | `cron.conf` 是**靜態文字檔**，要手動 `crontab -e` | agent 執行中自己新增／修改／取消 |
| 誰能改 | 只有修修 | agent 自己 |
| 粒度 | 固定 cron 行 | 動態（「明天早上再想一次這題」） |

> `agents/franky/cron_dispatcher.py` **不是**通用排程器 —— 它只做「週日該跑 synthesis 還是 retrospective」的分支判斷。

要做的話大致四塊：

1. **job store** — `state.db` 開一張表（agent / schedule / prompt / 狀態 / 下次執行時間 / 建立者）
2. **dispatcher** — 一條 cron（例如每分鐘）讀表、到點觸發對應 agent
3. **`schedule_job` / `cancel_job` tool** — Agent SDK 這邊的 in-process MCP tool
4. **護欄（非可選）** — 每個 agent 的 job 數上限、最小間隔、單次 `max_budget_usd`、以及**修修可見可撤銷**的清單（Bridge 一頁）

> ⚠️ 第 4 點不能省。**一個能自己排程、又能在排程裡再排程的 agent，是遞迴燒錢的標準形狀。**

**排期：遷移完成後的獨立 slice，不塞進 S0–S5。** 理由同 auto memory —— 本次目標是「換 harness、行為不變」，同時加新能力會讓出問題時分不清是誰造成的。

---

## 6. 已定決策

| # | 決策 | 理由 |
|---|---|---|
| 1 | **Nami 遷移到 Claude Agent SDK** | 32 個 skill 只有 Agent SDK 能用；compaction / tool search / hooks / subagent 都是現況缺口 |
| 2 | **現在遷移，不等** | 目前只有一個活躍用例（排任務），遷移成本隨使用量增長。修修裁決 |
| 3 | **拓樸先做 A（VPS-only）** | Nami 現在的工作都在範圍內；重媒體 skill 是 Brook 的問題 |
| 4 | **`shared/llm.py` provider facade 保留** | 跨模型單次推論（Gemini / Grok / GPT-5）與 `multi-agent-panel`、`adr_multi_model_review.py` 仍需要。跟 agent 框架是兩件獨立的事 |
| 5 | **不做雙框架生產系統** | 兩個 agent 做不同的事，差異分不清是框架還是任務造成 —— 得不出結論卻付兩套維護成本 |
| 6 | **Codex 訂閱額度實驗獨立、暫緩** | 唯一能回答「不燒 API credit」的路徑，但價值在批次工作不在 Nami |
| 7 | **L3 memory 不動** | Nakama 自己做得比兩家原生方案深 |
| 8 | **Cowork 不適用，Brook 也走 Agent SDK** | 已查證：不能從 Slack 觸發（觸發面只有手機／桌面）；遠端執行；排程任務不能讀本機檔案 —— 而重媒體 skill 兩者都要 |
| 9 | **多 bot + 自主排程是目標形態；自主排程排在遷移之後** | Nakama 現況已是多 bot 形狀，換 harness 即可；自主排程是新能力，需要 job store + dispatcher + tool + 護欄，獨立 slice |

---

## 7. 待裁決（重開機後繼續）

| # | 事項 | 建議 | 狀態 |
|---|---|---|---|
| 1 | **Auto memory 開/關** | ~~先關掉~~ → **修修裁決：打開** | ✅ **已定** |
| 1b | Auto memory 存到哪（預設 machine-local 會讓 VPS 與 Windows 分裂） | 先 `autoMemoryDirectory` 指到 VPS 獨立目錄；S2 觀察一輪再決定要不要搬進 repo `memory/`。**S2 必須明確設定，不可留預設** | ⏳ |
| 2 | `setting_sources` 載入哪些 skill | 白名單，重媒體 skill 排除在 VPS 外 | ⏳ |
| 3 | Session 儲存位置 | 先用 SDK 預設，VPS 上確認 cwd 穩定 | ⏳ |
| 4 | Cutover 方式 | feature flag 並行，實用一週無異常才移除舊路徑 | ⏳ |
| 5 | **S0 探針是否開跑** | 建議開跑 | ⏳ |

---

## 8. 目前進度

| 項目 | 狀態 |
|---|---|
| 跨日日期 bug 修復 | ✅ commit `396d0e1`，PR [#1107](https://github.com/shosho-chang/nakama/pull/1107) 已開，**未 merge** |
| 遷移計畫（六 slice + 六要素 task prompt） | ✅ `docs/plans/2026-07-29-nami-agent-sdk-migration-plan.md` |
| 本文件 | ✅ |
| S0 探針腳本 | ✅ `scripts/spikes/agent_sdk_probe.py`（**已寫、未跑**） |
| S0 探針執行 | ⬜ 未開始 |
| Morning brief | ⬜ 未開始（內容待討論；`agents/nami/__main__.py` 仍是 stub、`cron.conf` 07:00 仍註解） |
| 自主排程 | ⬜ 未開始（遷移後的獨立 slice，見 §5b） |

### 重開機後怎麼接 S0

```bash
cd E:/nakama-nami-time
python -m venv .venv-spike
.venv-spike/Scripts/Activate.ps1          # Windows
pip install claude-agent-sdk
# ANTHROPIC_API_KEY 要在環境變數裡（SDK 不讀 .env）

python scripts/spikes/agent_sdk_probe.py q1     # 安全紅線：tools=[] 是否真的移除內建工具
python scripts/spikes/agent_sdk_probe.py q2a    # 印出 session_id
# 關掉進程，然後：
python scripts/spikes/agent_sdk_probe.py q2b <session_id>   # 必須從同一個 cwd 跑
```

Q3 要在 VPS 上跑（`~/.ssh/config` 有一組 host）。

**兩個已知缺口，開跑前要處理：**

1. **`defer` 決策的 API 形狀沒有查證到**，腳本裡 `_can_use_tool()` 目前先回 `PermissionResultAllow`。跑 q2a 前先讀 [hooks 文件的 "Defer a tool call for later"](https://code.claude.com/docs/en/hooks) 把它補上。在那之前 q2a/q2b 測到的只是「pending 狀態能不能 resume 接回」，不是完整 defer 流程。
2. **auto memory 已裁決為「打開」**（2026-07-29）。剩「存到哪」的子決策，只擋 S2、不擋 S0。

### Worktree

`E:\nakama-nami-time`
- `feat/nami-time-context` — 日期修復，已 push（PR #1107）
- `docs/nami-agent-sdk-migration` — 本文件 + 遷移計畫（目前所在）

---

## 引用來源

**Anthropic**
- [Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview)
- [Agent SDK quickstart（auth）](https://code.claude.com/docs/en/agent-sdk/quickstart)
- [How the agent loop works](https://code.claude.com/docs/en/agent-sdk/agent-loop)
- [Give Claude custom tools](https://code.claude.com/docs/en/agent-sdk/custom-tools)
- [Work with sessions](https://code.claude.com/docs/en/agent-sdk/sessions)
- [Handle approvals and user input](https://code.claude.com/docs/en/agent-sdk/user-input)
- [How Claude remembers your project（CLAUDE.md / auto memory）](https://code.claude.com/docs/en/memory)
- [Memory tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)
- [Managed Agents memory](https://platform.claude.com/docs/en/managed-agents/memory)
- [Building agents with the Claude Agent SDK](https://claude.com/blog/building-agents-with-the-claude-agent-sdk)
- [The evolution of agentic surfaces: Managed Agents](https://claude.com/blog/building-with-claude-managed-agents)
- [Building Effective AI Agents](https://www.anthropic.com/engineering/building-effective-agents)
- [Claude Cowork](https://www.anthropic.com/product/claude-cowork) / [product guide](https://claude.com/blog/the-claude-cowork-product-guide)

**OpenAI**（sub-agent 調查，完整清單 49 條見該次調查輸出）
- [Introducing AgentKit](https://openai.com/index/introducing-agentkit/)
- [Deprecations](https://developers.openai.com/api/docs/deprecations)
- [Agents SDK — sandbox memory](https://openai.github.io/openai-agents-python/sandbox/memory/)
- [Agents SDK — sessions](https://openai.github.io/openai-agents-python/sessions/)
- [Codex pricing / auth](https://learn.chatgpt.com/docs/pricing) / [auth](https://learn.chatgpt.com/docs/auth.md) / [CI-CD auth](https://learn.chatgpt.com/docs/auth/ci-cd-auth.md)
- [Workspace Agents](https://openai.com/index/introducing-workspace-agents-in-chatgpt/)
- [ChatGPT Work](https://openai.com/index/chatgpt-for-your-most-ambitious-work/)
- [Agent Skills 開放標準](https://agentskills.io/)
