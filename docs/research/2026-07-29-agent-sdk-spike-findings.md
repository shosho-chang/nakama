# S0 探針實測結果 — Agent SDK 遷移三個必答問題

**日期**：2026-07-29
**狀態**：✅ **三題全過，S1–S3 解除封鎖**
**腳本**：`scripts/spikes/agent_sdk_probe.py`（q1 / q2a / q2b 在 Windows 本機跑，q3 在 VPS）
**相關**：`docs/plans/2026-07-29-nami-agent-sdk-migration-plan.md`、`docs/research/2026-07-29-nami-agent-architecture-decisions.md`

## 版本快照

| 項目 | 值 |
|---|---|
| claude-agent-sdk | **0.2.128**（Windows 與 VPS 同版） |
| model | claude-sonnet-4-6（`SPIKE_MODEL` 預設） |
| Windows | Python 3.14.4，cwd `E:\nakama-nami-time` |
| VPS | Linux 6.8.0-136-generic x86_64 / glibc 2.39 / Python 3.12.3，cwd `/tmp` |
| Auth | `ANTHROPIC_API_KEY`（API credit）。啟動時警告「claude.ai connectors are disabled because ANTHROPIC_API_KEY … is set」— 符合預期，證明走的是 API key 而非訂閱 |

---

## Q1 — `tools=[]` 是否真的移除內建工具：✅ 通過（安全紅線）

| 組 | 結果 |
|---|---|
| `tools=[]` | 模型列出的工具**只有 `mcp__nami__nami_echo`**，三題明確回答：無 Bash、無 Write/Edit、無 Read。cost $0.0352 |
| 對照組（不設 tools） | 列出 Agent / Bash / Edit / Glob / Grep / Read / Write / Workflow… 全套內建 + deferred tools + **本機 playwright plugin**。cost $0.1102 |

**S2 可以照計畫寫 `tools=[]`。**

### ⚠️ 附帶發現：預設 options 會載入本機設定

對照組不只有內建工具，還把**使用者層設定**（playwright plugin、MCP servers）全載進來；連 `tools=[]` 那組的回答都引用了 repo CLAUDE.md 的內容 —— 表示 `query()` 預設會讀 filesystem settings（文件也說 default query() options 啟用 setting sources）。

**S2 影響**：`setting_sources` 不能留預設不管。VPS 上要明確指定（計畫本來就要求白名單 skills），否則 `/root/.claude` 下任何殘留設定、plugin 都會進 Nami 的 context。

---

## Q2 — defer + 跨進程 resume：✅ 通過（ask_user 的命脈）

### 先查證再實作（形狀與原假設不同）

- **defer 不是 `can_use_tool` 的回傳值** —— Python SDK 沒有 `PermissionResultDefer`（reference 全文無 defer）。
- defer 是 **PreToolUse hook** 的 `permissionDecision`：`{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "defer"}}`。
- 出處：hooks 文件 [Defer a tool call for later](https://code.claude.com/docs/en/hooks#defer-a-tool-call-for-later)；user-input 文件明說「使用者回覆時間超過進程能等的長度 → 回 defer hook decision」。

### 實測（跨進程，非同進程）

**q2a**（進程 1）：prompt 要求呼叫 `nami_echo('S0-probe')` → PreToolUse hook 觸發 → 回 defer → tool 不執行、進程正常結束。`ResultMessage` **原生露出**兩個關鍵欄位：

```
stop_reason='tool_deferred'
deferred_tool_use=DeferredToolUse(id='toolu_01TCPn…', name='mcp__nami__nami_echo', input={'text': 'S0-probe'})
```

**q2b**（進程 2，`resume=<session_id>`，同 cwd）：**同一個 tool call 再次觸發 PreToolUse** → hook 這次回 `allow` + `updatedInput` → tool 真的執行 → Claude 回覆包含 `echo: S0-probe`，`stop_reason='end_turn'`。cost：q2a $0.057、q2b $0.011。

**S3 可以照這個迴圈設計**：Slack 提問 → defer 結束進程 → 使用者回覆 → resume + hook 帶答案 allow。

### 文件載明的限制（S3 設計要吃進去）

1. **turn 內單一 tool call 才有效** — 多個並發 tool call 時 defer 被忽略、走正常 permission flow。
2. defer 時 `updatedInput` / `permissionDecisionReason` / `additionalContext` 全被忽略（答案要在 **resume 那次**的 allow 給）。
3. Session 檔受 `cleanupPeriodDays` 管，**預設 30 天**清掉 — 涵蓋現行 24h timeout 有餘。
4. resume 時 MCP server 沒接上 → `stop_reason='tool_deferred_unavailable'` + `is_error=True` — S3 要處理這個失敗態。
5. 答案還沒到可以再 defer，由呼叫方決定何時 break 迴圈。
6. 多 hook 優先序：`deny` > `defer` > `ask` > `allow`。

### ⚠️ 實測怪象：resume 時的 prompt

Python `query()` 的 `prompt` 是必填；q2b 傳空字串可以跑，deferred tool 先解掉（第一個 ResultMessage 就是答案），但空字串仍被當一則 user message，**同一個 stream 又冒出第二個 ResultMessage**（模型回應了 plugin 連線之類的 context noise）。S3 實作時：以第一個 ResultMessage 為 tool resolution 的結果，或改用 `ClaudeSDKClient` 控制訊息流，別把第二個當 Nami 的回覆貼回 Slack。

### 成本記錄的附帶發現

`ResultMessage.model_usage` 是 per-model dict —— q2a 除了 sonnet 還有一筆 `claude-haiku-4-5`（$0.0007，CLI 內部雜務）。S4 接 `llm_observability` 時 `total_cost_usd` 已含全部，但如果要分模型記帳，`model_usage` 有粒度。

---

## Q3 — VPS 環境可跑：✅ 通過

- `pip install claude-agent-sdk` 在 VPS venv（`/tmp/spike-venv`）直接成功，native binary 可執行。
- `query()` 跑通，回覆「正常」，cost $0.0026。
- **Session 檔落點**：`/root/.claude/projects/-tmp/046477cd-….jsonl` —— **cwd-keyed**（`-tmp` = 編碼後的 `/tmp`）。`/root/.claude` 首跑自動建立。
- **S3 影響**：gateway 從 `/home/nakama` 跑 → session 會落在 `/root/.claude/projects/-home-nakama/`。**resume 必須同 cwd**，gateway 的 systemd WorkingDirectory 要固定住（現況即是）。
- VPS RAM 3.9G / 可用約 1.7G，單一 query 進程無壓力；S2 上線後要留意多 thread 併發時每個 query 各起一個 CLI 進程的記憶體量。

VPS 上的探針殘留：`/tmp/agent_sdk_probe.py`、`/tmp/spike-venv`（重開機自動消失，不清理）。

---

## 對後續 slice 的結論

| Slice | 影響 |
|---|---|
| S1 | 無阻礙。`@tool` + `create_sdk_mcp_server` 形狀與腳本一致，照計畫做 |
| S2 | `tools=[]` 紅線通過可寫；**新增要求：`setting_sources` 明確指定，不可留預設**（見 Q1 附帶發現） |
| S3 | 機制改為 **PreToolUse hook defer**（不是 can_use_tool）；設計吃進上面 6 條限制與 resume prompt 怪象；cwd 固定 |
| S4 | `total_cost_usd` / `model_usage` / `num_turns` / `session_id` 全部拿得到，照計畫做 |
