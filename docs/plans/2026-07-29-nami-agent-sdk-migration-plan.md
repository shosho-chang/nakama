# Nami → Claude Agent SDK 遷移計畫

**日期**：2026-07-29
**模式**：P9 規劃 — 本文件輸出的是 task prompt，不是 code

---

## 🚩 從這裡開始（給接手的 session）

1. 讀 `docs/research/2026-07-29-nami-agent-architecture-decisions.md` —— 為什麼這樣決定、9 項已定決策
2. ✅ **S0 探針已跑完（2026-07-29），三題全過** —— 實測結果與版本號在
   `docs/research/2026-07-29-agent-sdk-spike-findings.md`。重點：defer 是 **PreToolUse hook**
   的 `permissionDecision`（不是 can_use_tool 回傳值）；`tools=[]` 紅線通過；
   `setting_sources` 在 S2 **必須明確指定**（預設會載入本機設定與 plugin）
3. **下一個動作：S1** —— 27 個 tool 包成 in-process MCP server（見下方 S1 六要素）

Morning brief 暫緩，不在本輪範圍。

---

## 為什麼要做

現況：`gateway/handlers/nami.py:_run_loop()` 是手寫的 Messages API agent loop。缺口逐項可驗證：

| 缺口 | 現況 | Agent SDK |
|---|---|---|
| Context compaction | 無。長 thread 單向膨脹，24h TTL 在遮這個問題 | 內建自動壓縮 |
| Tool search | 無。28 個 tool schema 每輪全進 context | 預設開啟，schema 按需載入 |
| **Skills** | **完全不能用**。`.claude/skills/` 32 個 skill 是 Claude Code 產物 | `setting_sources=["project"]` 原生載入 |
| Hooks | 無。要審計/攔截只能改 loop 本體 | `PreToolUse` / `PostToolUse` / `Stop` 等 |
| Subagent | 無 | 內建 |
| Budget cap | `_MAX_ITERS = 15`（土法） | `max_turns` + `max_budget_usd` |
| pause/resume | 自寫 SQLite（`conversations.db`） | `can_use_tool` + `defer` + session resume |

**Skills 是決定性理由**：修修的使用情境是「Slack 呼叫 agent → 用既有 skill 產出東西」。這在手寫 loop 上做不到。

**不是理由**：省錢。Agent SDK 一樣吃 API credit，官方文件明確把 auth 導向 API key，並註明 claude.ai login 不開放給第三方 agent（除非事前核准）。

---

## 拓樸決定：先做 A（VPS-only）

| 方案 | 說明 | 決定 |
|---|---|---|
| **A. 單一 VPS agent** | 全部跑 VPS，重媒體 skill 不在範圍 | ✅ **本計畫做 A** |
| B. 雙 agent | VPS（輕、24/7）+ 本機（GPU / Resolve） | 之後 Brook 上線再處理 |
| C. VPS 派工、本機執行 | 佇列 + worker | 之後 |

理由：Nami 現在的工作（排任務、行事曆、Gmail、vault）本來就在 A 範圍內。`podcast-pipeline` / `subtitle-gen` / `resolve-project` 需要 DaVinci Resolve、GPU、`G:\footages\`，VPS 上都沒有 —— 那是 Brook 的問題，不是 Nami 的。

---

## Memory 決策（必須在 S1 前定案）

三層拆解與歸屬：

| 層 | 內容 | 遷移後歸屬 |
|---|---|---|
| **L1 對話記憶** | thread 內的對話歷史 | Agent SDK Session（取代 `conversations.db` 的 messages 欄位） |
| **L2 agent 自寫筆記** | 「修修常在週三下午排寫稿」這類 operational 觀察 | Agent SDK auto memory — **需決策，見下** |
| **L3 領域事實** | 「番茄鐘 30 分鐘」「課程推薦文會親自深入研究」 | **`shared/agent_memory.py` 原封不動**，繼續走 `_build_context_preamble()` 注入 |

### ✅ 已裁決（2026-07-29，修修）：auto memory **打開**

修修裁決要開。以下原本的三選一保留作紀錄，並標註**還剩一個子決策**。

**還沒定的子決策：存到哪。** Auto memory 預設寫 `~/.claude/projects/<project>/memory/`，官方明說
**machine-local，不跨機器共享**。Nami 跑 VPS、修修的機器是 Windows —— 用預設會**分裂成兩份**。

| 子選項 | 做法 | 評估 |
|---|---|---|
| **B. 導向 repo** | `autoMemoryDirectory` 指到 repo 內 `memory/nami-auto/` | 符合 CLAUDE.md「記憶在 repo 內 git 共用」；但 VPS 上要處理 git 寫入與 push，且可能與 `memory_maintenance.py reindex` 打架 |
| **C. 導向獨立目錄** | 指到 VPS 上非 git 的固定路徑 | 不污染 repo、實作簡單；但仍 machine-local，Windows 端看不到 |

**建議先 C，S2 上線觀察一輪再決定要不要升級成 B。** 理由：先確認 auto memory 實際會寫出什麼、
量有多大、有沒有價值，再決定要不要付「跨機同步」的複雜度。C → B 之後搬目錄的成本很低。

> ⚠️ S2 實作時**必須明確設定** `autoMemoryDirectory`，不可留預設 —— 留預設就是選了「分裂」。

<details>
<summary>原始三選一（已被上述裁決取代，保留紀錄）</summary>

Auto memory **預設開啟**，寫到 `~/.claude/projects/<project>/memory/`，且官方明說 **machine-local，不跨機器共享**。

Nami 跑在 VPS。若不處理，VPS 家目錄會長出一份跟 repo `memory/` 不同步的記憶 —— 直接違反 CLAUDE.md「所有記憶在 repo 內 `memory/`，git 跨平台共用」。

三個選項：

| 選項 | 做法 | Pros | Cons |
|---|---|---|---|
| **A. 關掉** | `autoMemoryEnabled: false` 或 `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` | 零風險、行為與現況一致、記憶單一來源仍是 `agent_memory` + repo `memory/` | 放棄 L2 這個新能力 |
| **B. 導向 repo** | `autoMemoryDirectory` 指到 repo 內 `memory/nami-auto/` | 拿到 L2 且 git 共享；符合既有紀律 | 需 commit 才跨機同步；VPS 上要處理 git 寫入與 push；可能與 `memory_maintenance.py` 的 reindex 打架 |
| **C. 導向獨立目錄** | `autoMemoryDirectory` 指到 VPS 上非 git 的固定路徑 | 拿到 L2、不污染 repo | 仍是 machine-local，Windows 端看不到 |

~~建議 A（先關掉）~~ —— **已被修修裁決推翻，改為打開。**

</details>

---

## Slice 拆解

### S0 — 探針（spike，不進生產）✅ 完成（2026-07-29，三題全過，見 findings）

1. **目標** — 在動任何生產程式碼前，用最小可執行範例驗證三個關鍵未知，任一失敗就回頭重新設計。
2. **範圍** — 新增 `scripts/spikes/agent_sdk_probe.py`（一次性，不進 CI）。不碰 `gateway/`。
3. **輸入** — `claude-agent-sdk` (Python ≥3.10)；Agent SDK 文件的 sessions / user-input / custom-tools 三頁。
4. **輸出** — 一份 `docs/research/2026-07-XX-agent-sdk-spike-findings.md`，逐項記錄實測結果與版本號。
5. **驗收** — 三題都有明確答案且附實測輸出：
   - **Q1 `tools=[]` 是否真的移除所有內建工具？** 用 `tools=[]` + 一個 MCP tool 跑一輪，確認 Claude 拿不到 `Bash`/`Write`/`Read`。**這是 VPS 安全紅線，文件說法必須被實測驗證。**
   - **Q2 `defer` + session resume 能否跨進程？** 觸發 `can_use_tool` → 回傳 defer → **殺掉進程** → 新進程用 `resume=session_id` 接回 → 確認上下文完整、pending tool call 仍可回覆。
   - **Q3 VPS 環境可跑？** SDK 自帶 native binary，在 VPS 的 Linux 環境安裝並跑通一次 `query()`。
6. **邊界** — 不改 `gateway/handlers/nami.py`；不改 `requirements.txt`（用獨立 venv）；不部署。

---

### S1 — 27 個 tool 包成 in-process MCP server

1. **目標** — 把既有 `_tool_*` 實作原封不動地暴露成 Agent SDK 可用的 tool，業務邏輯零改動。
2. **範圍** — 新增 `gateway/handlers/nami_tools.py`。**不改** `gateway/handlers/nami.py` 的 `_tool_*` 函式本體、不改 `shared/lifeos_writer.py` / `shared/google_calendar.py` / `calendar_scheduler`。
3. **輸入** — 現有 `NAMI_TOOLS` 的 28 個 JSON Schema；27 個 `_tool_*` 方法（簽章皆為 `(input_: dict) -> _ToolOutcome`）；`create_sdk_mcp_server` / `@tool` API。
4. **輸出** — `create_sdk_mcp_server(name="nami", tools=[...])`，每個 tool 是薄 wrapper：呼叫既有 `_tool_*`，把 `_ToolOutcome` 轉成 `{"content": [...], "is_error": bool}`。
5. **驗收** —
   - 27 個 tool 全部有對應 wrapper（`ask_user` 除外，走 S3）
   - 新增 schema 對照測試：每個 tool 的 input schema 與原 `NAMI_TOOLS` 語義等價（欄位名、required、enum 不得漂移）
   - `_ToolOutcome.event` 的 emit 行為保留（`shared/events.emit` 不能斷）
   - 既有 96 個測試全綠
6. **邊界** — 不動 loop、不動 `handle()` / `continue_flow()`、不動 dispatch 鏈（S2 才拆）。`readOnlyHint` 只標在確定無副作用的 tool（`list_*` / `read_*` / `*_lookup`），其餘不標。

---

### S2 — loop 換成 `query()`，單輪對話跑通

1. **目標** — 「新增任務」這條主路徑在 Agent SDK 上跑通，行為與現況等價。
2. **範圍** — `gateway/handlers/nami.py` 的 `_run_loop()` / `handle()`。
3. **輸入** — S1 的 MCP server；`prompts/nami/agent_system.md`；`_build_context_preamble()`。
4. **輸出** — `_run_loop()` 改為 `query()` / `ClaudeSDKClient`，並設定：
   - `tools=[]` ← **安全紅線，S0-Q1 驗證過才可寫**
   - `mcp_servers={"nami": nami_server}`、`allowed_tools=["mcp__nami__*"]`
   - `setting_sources=["project"]`（載入 skills — 但先確認載入哪些、不要意外把重媒體 skill 帶進 VPS）
   - `max_turns` 對齊現行 `_MAX_ITERS=15`；加 `max_budget_usd`
   - auto memory 依上方裁決處理
5. **驗收** —
   - 「排一個任務 + 建行事曆事件」端到端跑通，vault 檔案與 calendar event 與現況一致
   - **rollback 路徑必須實測**：模擬 task 寫入失敗，確認 calendar event 被回收（對應既有 `test_create_calendar_event_rollback_on_task_write_failure`）
   - `_build_context_preamble()` 的日期注入仍生效（今天修的 bug 不能回歸）
   - 既有測試全綠或有明確、經說明的調整
6. **邊界** — 不處理 `ask_user`（S3）；不改 Slack 層 `gateway/bot.py`；不動 `conversation_state.py`。

---

### S3 — `ask_user` 的 pause / resume

1. **目標** — 用 **PreToolUse hook 的 `defer`** + session resume 取代自寫的 SQLite pause/resume，行為等價：Slack 提問 → 進程可結束 → 數小時後使用者回覆 → 接回原上下文。
2. **範圍** — `gateway/handlers/nami.py` 的 `continue_flow()`；`gateway/conversation_state.py`（改存 session_id 而非整份 messages）；`gateway/bot.py` 的 continuation 註冊。
3. **輸入** — **S0-Q2 實測結論（`docs/research/2026-07-29-agent-sdk-spike-findings.md`，含 6 條文件限制與 resume prompt 怪象）**；現行 `NAMI_AGENT_FLOW` continuation 契約；`AskUserQuestion` 的 questions/answers 格式。
4. **輸出** — `conversations.db` 的 `state_json` 由「整份 messages」改為 `{"session_id": ..., "pending_tool_use_id": ...}`；**PreToolUse hook** 攔下提問 tool：把問題送回 Slack、回 `permissionDecision: "defer"` 結束進程；使用者回覆後 `resume=<session_id>`（同 cwd），hook 對再次觸發的同一 tool call 回 `allow` + 答案放 `updatedInput`。resume 收到 `tool_deferred_unavailable` 或多 tool call 導致 defer 被忽略時要有明確 fallback。
5. **驗收** —
   - **VPS 重啟後仍能接回**（不是只在同一進程內測）
   - 24h idle timeout 行為保留
   - DM 路徑（`get_latest_for_user_and_agent`）仍正確
   - 飛行中的舊 conversation 有明確處理策略（migration 或優雅失效），不可靜默壞掉
6. **邊界** — 不改 Slack 訊息格式；不動 `gateway/formatters.py`。

---

### S4 — 觀測與成本

1. **目標** — 成本與行為可觀測程度不低於現況。
2. **範圍** — `shared/llm_observability.py` 接 Agent SDK；Bridge 的 model 面板。
3. **輸入** — `ResultMessage.total_cost_usd` / `usage` / `num_turns` / `session_id`；現行 per-call 記錄 schema。
4. **輸出** — 每個 session 一筆成本記錄；粒度變粗（session 而非 per-call）的說明文件。
5. **驗收** — 跑一輪對話後能查到成本；`num_turns` 有記；session_id 可回溯到 Slack thread。
6. **邊界** — 不追求還原 per-call 粒度（SDK 不給）。`shared/llm_router.py` 的 Nami 條目要標註「已改由 Agent SDK options 指定」，避免面板誤導。

---

### S5 — Cutover

1. **目標** — 安全切換，可快速回滾。
2. **範圍** — 部署設定；`gateway/handlers/__init__.py` 的 handler 註冊。
3. **輸入** — S1–S4 完成。
4. **輸出** — env flag（例如 `NAMI_USE_AGENT_SDK=1`）控制走新舊哪條路；兩條路並存一段時間。
5. **驗收** — flag 關掉即回到現行行為；修修實際用一週無異常後才移除舊路徑。
6. **邊界** — 舊 `_run_loop()` 在觀察期內**不刪**。

---

## 未決事項（需修修裁決）

| # | 事項 | 我的建議 |
|---|---|---|
| 1 | ~~Auto memory 開/關~~ | ✅ **已裁決：打開**。剩子決策「存到哪」——建議先用 `autoMemoryDirectory` 指到 VPS 獨立目錄，S2 觀察一輪再決定要不要搬進 repo |
| 2 | `setting_sources=["project"]` 要載入哪些 skill | 白名單，先只放 Nami 用得到的；重媒體 skill 排除 |
| 3 | Session 儲存位置（SDK 預設 JSONL on disk，cwd-keyed） | 先用預設；VPS 上確認 cwd 穩定 |
| 4 | Cutover 用 feature flag 還是直接切 | **feature flag**（S5） |

---

## 明確不做

- 不改 `shared/agent_memory.py` 及其周邊 5 個檔（L3 領域記憶，與本次無關）
- 不改 `shared/llm.py` provider facade（跨模型單次推論仍需要）
- 不碰 Zoro / Robin / Brook / Sanji 的呼叫路徑
- 不做 OpenAI / Codex 相關實驗（獨立議題）
- 不追求用 subscription quota（文件已確認 Agent SDK 不支援）
