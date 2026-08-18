# annotation_merger Agent SDK 遷移 — S0 探針實測結果

**日期**：2026-08-18
**計畫**：`docs/plans/2026-08-18-annotation-merger-agent-sdk-plan.md`
**環境**：VPS（shoshotw）、`claude-agent-sdk` 0.2.128、bundled CLI
（`claude_agent_sdk/_bundled/claude`，經 `NAKAMA_CLAUDE_CLI` 指定）、
訂閱認證 `CLAUDE_CODE_OAUTH_TOKEN`（process env 經 `ClaudeAgentOptions.env` 覆寫）
**探針腳本**：`scripts/spikes/merger_sdk_probe.py`（q1 / q2 / q3 子命令）

## 結論一覽

| 題 | 問題 | 答案 |
|---|---|---|
| Q1a | SDK 有無 `tool_choice` 等價物？ | **無**。`ClaudeAgentOptions` 全部 45 個欄位無任何強制 tool 呼叫機制 |
| Q1b | prompt 指令強制 + 單 tool 白名單的呼叫成功率 | **10/10**（Opus 4.7，真實形狀的註記樣本） |
| Q2 | Opus 走訂閱 CLI 可用嗎？ | **可用**（4.7 與 4.8 都實測通過）→ `annotation_merge` 保持 registry 預設 Opus 4.7 |
| Q3 | `asyncio.to_thread` 內 `asyncio.run(query())` 穩定嗎？ | **20/20 零失敗、零 CLI 進程洩漏**（前後 pgrep 都是 1） |

四題全過 → S1 可以開始，設計不需回頭改。

## Q1b 詳細數據

樣本：6 個 concept slug + 4 條註記（2 條明確匹配、1 條間接匹配、1 條刻意的無關干擾項
「芝加哥學派價格理論」）。指令：「You MUST submit your result by calling the
merge_annotations tool exactly once. Do not reply with plain text.」

10 次全部呼叫 tool 且 mapping 通過型別驗證。**十次選出的 concept 集合完全一致**：
`['attention-residue', 'compound-interest', 'deep-work', 'sleep-debt']` —
正確涵蓋兩條明確匹配、合理納入 deep-work（註記原文明講「深度工作需要 90 分鐘」）、
正確排除干擾項與兩個不相關 slug（zone-2-training、identity-based-habits）。

單次延遲 14–24s（Opus）。對 Reader UI 的 sync 按鈕（低頻、本來就是秒級操作）可接受。

**S2 含義**：單發 prompt 強制的可靠度足夠，「重試一次」機制保留當保險即可，
不需要更重的 forcing 設計。`max_turns=3` 足夠（實測全部一輪內完成 tool 呼叫）。

## Q3 詳細數據

鏡像 `thousand_sunny/routers/robin.py:699` 的呼叫形狀
（async route → `asyncio.to_thread(sync_fn)` → sync fn 內 `asyncio.run(query())`）：
20 次串行、haiku、89 秒總計。零失敗；`pgrep -f _bundled/claude` 前後都是 1（無殭屍）。

## ⚠️ 操作性發現：`env` 覆寫是承重牆（第一輪探針的翻車紀錄）

第一輪探針忘了傳 `ClaudeAgentOptions(env=...)`，把 `.env`（含 `ANTHROPIC_API_KEY`）
整份留在 `os.environ` → 子進程繼承 → **API key 壓過 OAuth token**（2026-08-18 已另行實測
確認的優先序）→ 額度空 → 每次 2 秒內秒死，Q1b 0/10、Q3 crash。

失敗簽名極難診斷：`Exception: Claude Code returned an error result: success`
（SDK「先 yield error ResultMessage 再 raise」的形狀，與 2026-08-17 Nami 額度事故的
log 一模一樣）。

**含義**：任何 SDK call site 忘記 `env=subscription_env()` 不是「退回 API 計費」而是
「在額度空時直接死 + 錯誤訊息無法自我解釋」。S1 的 `shared/agent_sdk.py` helper
必須是唯一取得 auth env 的入口，S2 測試必須鎖住「有傳 env」這件事
（比照 `tests/gateway/test_nami_sdk_loop.py` 的 `_sdk_auth_env` 行為鎖定測試）。
