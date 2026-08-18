# annotation_merger → Claude Agent SDK 遷移計畫

**日期**：2026-08-18
**模式**：P9 規劃 — 本文件輸出的是 task prompt，不是 code
**前情**：2026-08-17 Anthropic API 額度耗盡事故 → 全系統訂閱-first 遷移。純文字呼叫已全數
走 ADR-026 CLI 訂閱路徑（env-only，2026-08-18 上線）；tool-use 呼叫是唯一救不到的類別，
本計畫處理其中在 VPS 上、屬於 KB 核心的那一個。

---

## 🚩 從這裡開始（給接手的 session）

1. 讀 `docs/decisions/ADR-026-llm-router-auth-dimension.md` — auth 三態與「tool-use 不能走 CLI 訂閱」的結構限制
2. 讀 `docs/plans/2026-07-29-nami-agent-sdk-migration-plan.md` + `docs/research/2026-07-29-agent-sdk-spike-findings.md` — Nami 遷移是本計畫的 prior art，S0 三題（`tools=[]` 紅線 / defer / VPS 可跑）已驗證過，不用重測
3. **2026-08-18 已實測**：Agent SDK 可走訂閱額度（`CLAUDE_CODE_OAUTH_TOKEN` + 清空子進程
   `ANTHROPIC_API_KEY`）— 見 `memory/claude/reference_agent_sdk_supports_oauth.md` 與
   `gateway/handlers/nami.py` 的 `_sdk_auth_env()`（PR #1173）。**本計畫不需要重新驗證這件事**
4. 下一個動作：S0 探針（三個新未知，見下）

---

## 為什麼要做

`agents/robin/annotation_merger.py` 是卡片盒（Reader annotation → Concept page）的核心寫入路徑，
現況**完全停擺**：

| 事實 | 出處 |
|---|---|
| 兩個 LLM 呼叫點都走 `ask_with_tools`（forced `tool_choice`） | `annotation_merger.py:162`（v1 paper）、`:601`（v2/v3 book） |
| `ask_with_tools` 只支援 anthropic、且 `call_claude_with_tools` 無 OpenRouter/訂閱分支 | `shared/llm.py:230`、`shared/anthropic_client.py:370` |
| ADR-026 明定 tool-use 在 `subscription_*` 下 raise / 軟降 api | ADR-026 §Tool-use 路徑特別處理 |
| Anthropic API 額度已空 → api 路徑 = 死 | 2026-08-17 事故，log 佐證 |
| 唯一 production caller：`POST /robin/sync-annotations/{slug}`（Reader UI 觸發，VPS thousand-sunny） | `thousand_sunny/routers/robin.py:696` |

**關鍵架構事實（決定整份計畫的形狀）**：這裡的 tool-use **不是 agent loop**。
`_MERGER_TOOL` 從未被「執行」— 它是拿 forced `tool_choice` 當**結構化輸出的 schema 約束**
（`annotation_merger.py:150-153` docstring 自述：改 tool_choice 前的純文字 `ask()` 版本
常發生 JSON 解析失敗）。LLM 的工作是單發的：annotations + concept slug 清單 →
`{concept_slug: callout_block}` mapping；後續 vault 寫入全是確定性 Python。

修修 2026-08-18 裁決方向：「改成 Nami 這種 Agent SDK 的模式，比較有彈性」。

## 為什麼是 Agent SDK（而不是更小的改法）

| 方案 | 說明 | 評估 |
|---|---|---|
| A — de-tool-use | 改回純文字 JSON prompt + 解析，立刻吃到既有 CLI 訂閱路徑 | ❌ 倒退回 docstring 記載的已知壞 pattern（JSON 解析失敗正是當年改掉的原因）；且與修修的 SDK 方向相悖 |
| **B1 — SDK 結構化輸出**（本計畫 Phase 1） | `query()` + in-process MCP tool `merge_annotations`：模型呼叫 tool、handler 只捕獲 payload（Nami `answer_box` 同款 capture 模式）+ pydantic 驗證 + 失敗重試一次 | ✅ 訂閱額度 + schema 驗證都拿到；語意與現況等價、blast radius 最小 |
| B2 — 內容感知 agentic merge（Phase 2，另案） | 給 SDK agent 讀工具（讀 Concept 頁**內文**再判斷 match），多輪迭代 | 真正的品質升級——現況 LLM **只看得到 slug 名稱**，看不到頁面內容。但範圍大、互動路由延遲拉長，等 B1 穩定後另開計畫 |

## S0 — 探針（三個新未知；Nami spike 已答的不重測）

1. **目標** — 動生產程式碼前，驗證三個本案特有的未知。任一失敗回頭改設計。
2. **範圍** — `scripts/spikes/merger_sdk_probe.py`（一次性，不進 CI）。不碰 `agents/` `thousand_sunny/`。
3. **輸入** — `claude-agent-sdk`（VPS 已裝）；VPS `.env` 的 `CLAUDE_CODE_OAUTH_TOKEN`；
   `NAKAMA_CLAUDE_CLI`（bundled binary，2026-08-18 model matrix 實測 haiku/sonnet 全過）。
4. **輸出** — `docs/research/2026-08-18-merger-sdk-spike-findings.md`，逐項附實測輸出。
5. **驗收** — 三題都有明確答案：
   - **Q1 SDK 有沒有 `tool_choice` 等價物？** 翻 `ClaudeAgentOptions` 全部欄位實測。若無（預期如此）：
     測「prompt 指令強制 + 單 tool 白名單」下，模型呼叫 `merge_annotations` 的成功率
     （10 次真實 annotation 樣本，容忍 retry 一次後 ≥ 9/10）。
   - **Q2 `claude-opus-4-7` 走訂閱 CLI 可用嗎？** registry `annotation_merge` 預設是 Opus
     （`shared/llm_router.py` MODEL_REGISTRY）。2026-08-18 matrix 只測過 haiku/sonnet。
     若訂閱 tier 不含 Opus → 降 `claude-sonnet-4-6` 並記錄理由（訂閱下成本歸零，模型選擇
     只剩品質考量，需 A/B 一輪 mapping 品質）。
   - **Q3 `asyncio.to_thread` 內 `asyncio.run(query())` 在 thousand-sunny 環境穩定嗎？**
     現行 route 用 `asyncio.to_thread(merger.sync_source_to_concepts, slug)`
     （`robin.py:699`）— worker thread 無 event loop，`asyncio.run` 理論上安全
     （Nami 在 gateway 同 pattern），但要在 VPS 上實跑 20 次確認無 loop 洩漏 / zombie CLI 進程
     （`ps` 前後對照，Nami PR #1121 review B1 的教訓）。
6. **邊界** — 不改任何生產檔；探針只用測試 slug 的 annotation 副本，不寫真實 vault。

## S1 — shared 層：SDK helper + merge tool server

1. **目標** — 把 Nami 專屬的訂閱認證覆寫泛化成共用 helper；建立 merger 的 in-process MCP server。
2. **範圍** — 新增 `shared/agent_sdk.py`、`agents/robin/merger_tools.py`、對應測試。
   **不改** `gateway/handlers/nami.py`（Nami 沿用自己的 `_sdk_auth_env`，S4 收斂）。
3. **輸入** — `gateway/handlers/nami.py` 的 `_sdk_auth_env()` 實作與測試
   （`tests/gateway/test_nami_sdk_loop.py` 的兩個 auth env 測試是行為契約範本）；
   `_MERGER_TOOL` schema（`annotation_merger.py:60-81`）；`create_sdk_mcp_server` / `@tool` API。
4. **輸出** —
   - `shared/agent_sdk.py::subscription_env() -> dict[str,str]`：讀 `CLAUDE_CODE_OAUTH_TOKEN`
     → 回 `{"CLAUDE_CODE_OAUTH_TOKEN": tok, "ANTHROPIC_API_KEY": ""}`；未設回 `{}`。
     語意與測試比照 `_sdk_auth_env`（含「不准拿掉清空 API key」的鎖定測試）
   - `agents/robin/merger_tools.py::build_merger_server(capture_box)`：單一 tool
     `merge_annotations`，input schema 與 `_MERGER_TOOL` 逐欄位等價；handler 把 payload 寫進
     `capture_box` 後回 ok（不執行任何業務邏輯）
5. **驗收** — schema 對照測試（欄位名 / required / additionalProperties 不得漂移）；
   `subscription_env` 行為測試全綠；`ruff` 乾淨；既有測試不動全綠。
6. **邊界** — 不動 `annotation_merger.py` 本體（S2 才接）；不動 vault 寫入 helpers；
   不動 `shared/llm.py` / `anthropic_client.py`（ADR-026 路徑原封保留）。

## S2 — 換引擎：兩個 LLM boundary 函式改走 SDK（flag-gated）

1. **目標** — `_ask_merger_llm` / `_ask_merger_llm_v2` 內部改走 `query()`，函式簽章與回傳
   語意不變，行為等價，flag 預設 off。
2. **範圍** — 只改 `agents/robin/annotation_merger.py` 兩個函式的內部 + module 頂部 flag 判斷；
   `tests/agents/robin/test_annotation_merger.py` 補 SDK 路徑測試。
3. **輸入** — S1 的 helper 與 server；S0 的 Q1 結論（prompt 強制寫法）與 Q2 結論（model）。
4. **輸出** —
   - `ROBIN_MERGE_USE_AGENT_SDK=1` 時走新路徑：`ClaudeAgentOptions(tools=[],
     mcp_servers={"merger": ...}, allowed_tools=["mcp__merger__merge_annotations"],
     setting_sources=[], max_turns=3, max_budget_usd=cap, env=subscription_env())`
     — `tools=[]` 安全紅線與 Nami S2 相同
   - capture_box 拿到 payload → 沿用現行的 `{k: v for ... isinstance ...}` 淨化 → 回傳；
     模型沒呼叫 tool → 帶更強指令重試一次 → 仍失敗 raise `MergerLLMError`（現行例外語意不變）
   - flag off → 原 `ask_with_tools` 路徑逐位元不變
5. **驗收** —
   - 既有測試全綠（monkeypatch `_ask_merger_llm` 的測試 seam 不變 — 這是本 slice 的硬約束）
   - 新測試：fake query 注入下，capture / 淨化 / 重試 / MergerLLMError 四條路徑
   - VPS 上用真實 slug 手動各跑一次 v1 paper 與 v2 book 路徑，SyncReport 與 vault diff 人工比對
6. **邊界** — `sync_source_to_concepts` 流程、idempotency 短路、marker upsert、
   `SyncReport` shape、route 契約全部不碰；不動 `shared/annotation_store.py`。

## S3 — 觀測：session 成本進 api_calls

1. **目標** — SDK 路徑的成本與行為可觀測程度不低於現況（ADR-026 的 auth_actual 稽核鏈不斷）。
2. **範圍** — `annotation_merger.py` SDK 分支的 result 處理；必要時 `shared/llm_observability.py`。
3. **輸入** — `ResultMessage.total_cost_usd / num_turns / session_id`；`record_call` 簽章
   （已有 `auth_requested/auth_actual/fallback_reason` 三欄）。
4. **輸出** — 每次 merge 一筆 record：`agent=robin`、model、`auth_requested=subscription_preferred`、
   `auth_actual="subscription"`；session_id 進 log（失敗時進 `SyncReport.errors` 尾註）。
5. **驗收** — 跑一次 merge 後 `api_calls` 查得到該筆；Bridge cost panel 顯示正常。
6. **邊界** — 不還原 per-call 粒度（SDK 不給，Nami S4 同取捨）；不動 Bridge UI。

## S4 — Cutover + 收斂

1. **目標** — VPS 切換、觀察一週、退場舊路徑，並收斂重複的 SDK 認證程式碼。
2. **範圍** — VPS `.env`（flag）；觀察期後：刪 `_MERGER_TOOL` + `ask_with_tools` 分支、
   `gateway/handlers/nami.py` 的 `_sdk_auth_env` 改 delegate 到 `shared/agent_sdk.py`。
3. **輸入** — S2/S3 完成；修修 UAT（Reader UI 實際按一次 sync）。
4. **輸出** — flag on；一週無異常後 cleanup PR（含 nami 收斂）；ADR-026 補註
   「tool-use 限制的 Agent SDK 出口」段落。
5. **驗收** — 修修 UAT 通過；一週 log 無 `MergerLLMError` 週期性失敗；cleanup 後測試全綠。
6. **邊界** — 觀察期內舊路徑不刪；`NAMI_SDK_OAUTH_TOKEN` 保留（Nami 既有部署契約，
   收斂只動程式碼不動 env）。

## 未決事項（修修裁決，均附建議預設）

| # | 事項 | 建議預設 |
|---|---|---|
| 1 | Phase 1 只做 B1（結構化輸出等價遷移），B2（內容感知 merge）另案 | ✅ 建議照此——B2 是品質升級不是修復，混進來會拖慢卡片盒復活 |
| 2 | S0-Q2 若 Opus 訂閱不可用，`annotation_merge` 降 Sonnet 4.6 | ✅ 建議接受（訂閱下無成本差，僅品質考量，S0 附 A/B 樣本） |
| 3 | flag 命名 `ROBIN_MERGE_USE_AGENT_SDK`（鏡像 `NAMI_USE_AGENT_SDK`） | ✅ |
| 4 | 認證 token 統一用 process-wide `CLAUDE_CODE_OAUTH_TOKEN`（已在 VPS `.env`） | ✅ |

## 明確不做

- **B2 內容感知 agentic merge** — 等 B1 穩定，另開 P9（觸發條件：修修對 mapping 品質不滿，或想要「讀過內文再 match」）
- **Robin ingest 全線 SDK 化** — ingest 是純文字 pipeline，已走 CLI 訂閱，SDK 化只有彈性收益沒有帳務必要；等 B2 一起評估
- **Brook `replan_agent`（另一個 tool-use 站點）** — 跑本機不在 VPS、非 KB 核心；Brook 上線排程時再說
- **`DEFAULT_AUTH` 預設翻成 `subscription_preferred`** — 獨立小 PR + ADR-026 修訂，不混入本案
- **音訊多模態（Gemini 依賴）** — 另案（修修 2026-08-17 停用 Gemini 裁決的遺留工作）
- 不動 `shared/llm.py` facade、不動 ADR-026 的 CLI 訂閱路徑、不動 vault 寫入規則（無新資料夾、無 marker convention 變更，`docs/VAULT-LAYOUT.md` 不需更新）
