---
name: 多 Model 多 Agent 架構決策進度
description: 多 LLM provider routing + review panel + Slack brainstorm 三個需求的決策記錄 + 8 步建置順序 + trigger
type: project
originSessionId: f2ea9d48-f32a-4c33-bf30-c54837e598ec
---
# 多 Model 多 Agent 架構決策（2026-04-20 起）

調研來源：2026-04-20 三個平行 subagent 深度調研（LiteLLM vs 直 SDK、MoA/panel review、Slack multi-agent）。

## Q1 — Per-agent 模型路由：Hybrid 方案

- Production agents 走直 SDK（anthropic + google-genai + xai + openai）
- Bench / eval 腳本走 LiteLLM
- **Why**：LiteLLM Anthropic cache cost 計費有 bug（#9812、#17201），會污染 Bridge cost panel 準確度。Production 4 家 provider 邊際成本不高（~80 行/家 wrapper），不值得為 LiteLLM 犧牲計費準確度。但 bench 時用 LiteLLM 能讓新 model 出來時一行改動就測。
- **How to apply**：新增 agent 寫 provider wrapper 進 `shared/`，production 路由走 `shared/llm_router.py`，bench 腳本參考既有 `scripts/ab_ingest_bench.py` 模式延伸到雲端模型。

Provider 目標範圍：Anthropic + Google + xAI + OpenAI 四家（用戶確認）。

## Q2 — Review panel 範圍：A（小範圍）

觸發條件限定三類產出：
1. Brook 長文（部落格、YouTube 腳本）
2. P9 task prompt（跨檔任務拆解）
3. 架構決策 / ADR

- **Why**：sycophancy 風險最大的場景集中在長篇/高影響產出，一週 3-5 次成本可控。高頻低價值產出（Robin KB 頁面、Nami morning brief、Sanji 社群回覆）走 panel 成本收不回來。
- **How to apply**：`shared/llm_panel.py` fan-out ~60 行，Claude 作者 + Gemini/GPT/Grok critics 並行，Claude Opus synthesizer 保留異議。Code review 另外掛 `religa/multi_mcp` 或 `/ask-council` plugin 零開發。

**⏰ 用戶要求 trigger：2026-05-18 前後（上線 4 週後）提醒評估是否擴大到 B 範圍**（A + Zoro 關鍵字 report + PR code review）。評估時問用戶：
- Brook 長文有沒有真的從 panel 抓到盲點？
- 三家 critic 意見是否實質差異 or 只是換句話說？
- 成本/延遲是否可接受？

如果 panel 對 Brook 沒有顯著價值 → 退回單 model，別擴 B。

## Q3 — Slack brainstorm：設計凍結（2026-04-20 定案）

Use case：
- Zoro 社群監控發現熱門主題 → 推到 Slack #general（agent-initiated）
- 其他 agents 加入討論（可能發生在用戶睡覺時，async 夜間 OK）
- 每天早上晨報（Nami 負責）呈現討論結論
- 用戶也能主動發起（`@nakama brainstorm <主題>`）

**6 個設計決策**：

1. **Zoro 觸發閾值**：四道濾網 — velocity（mentions/hour）+ relevance（四大領域命中）+ novelty（KB 14 天內未處理）+ cooldown（48h 同題不重推）。起手保守**一天 1-2 次**。
2. **Participant selection**：topic routing + self-interest 混合。Orchestrator @ 相關 2-3 個 agent，其他 agent 自由 opt-in。
3. **Turn order**：Orchestrator 決定（**不**讓 agents 自由搶話）。Orchestrator 剛好對應規劃中的 Thousand Sunny 甲板控制台。
4. **Stop conditions**（四道全開）：`max_turns=10`、單 thread budget > $0.50 強制收斂、近 3 輪語意相似度閾值、`🛑` reaction 或 `收斂` 訊息。
5. **Summary owner**：Nami 晨報延伸加「過去 24h brainstorm threads」section，含建議 action items（寫 KB / Brook 起草 / 排任務），Nami 可用既有 task/calendar tool 落地。
6. **夜間 cost cap**：daily brainstorm budget ~$3-5、22:00-07:00 per-agent rate limit、超過則 orchestrator 暫停等早上。掛 Bridge cost panel 監控。

**分階段落地（P1 → P2 → P3）**：
- P1：用戶主導 brainstorm（`@nakama brainstorm`）
- P2：Zoro 白天 agent-initiated（1-2 次/天）
- P3：夜間 async + budget cap + Nami 晨報整合

每階段跑 1-2 週再推進。

## Q4 — Sanji LLM wire + 建置順序（2026-04-20 定案）

Sanji agent 目前只是骨架（`raise NotImplementedError`），Q4 實則是**三大系統 Q1/Q2/Q3 的建置順序**討論。

**依賴鏈**：Q1 router skeleton → Q1 provider 增量 + Q3 多 bot 並行 → Q2 panel 最後（等 router stable）

**8 步建置順序**（總長約 3-4 週）：

| # | 任務 | 狀態 | PR |
|---|---|---|---|
| 1 | Q1 router skeleton（`shared/llm_router.py` + env 讀 `MODEL_<AGENT>`） | ✅ 2026-04-20 | [#50](https://github.com/shosho-chang/nakama/pull/50) `e216147` |
| 2 | Q1 第一個新 provider（xAI）+ Sanji wire（第一版人格 prompt） | ✅ 2026-04-20 | [#51](https://github.com/shosho-chang/nakama/pull/51) `0e82fe9` |
| 3 | Q3 P1 用戶主導 brainstorm（原以為 Sanji 當第二個 Slack bot，實為 handler）| ✅ 2026-04-20 | [#52](https://github.com/shosho-chang/nakama/pull/52) `40acad8` |
| 4 | Q1 Gemini provider + Robin ingest 改走 Gemini | ✅ 2026-04-20 | [#53](https://github.com/shosho-chang/nakama/pull/53) `b015775` |
| 4.1 | 步驟 4 borderline fix（Gemini 3 bug） | ✅ 2026-04-20 | [#54](https://github.com/shosho-chang/nakama/pull/54) `221e3bd` |
| 4.2 | Multi-bot Slack gateway（每個 agent 獨立 bot）| 🔄 **PR #55 OPEN** | [#55](https://github.com/shosho-chang/nakama/pull/55) |
| 5 | Q3 P2 Zoro 白天推題 | 設計 doc 凍結，7 Q 全決定 | [docs/decisions/step-5-zoro-brainstorm-p2.md](../../docs/decisions/step-5-zoro-brainstorm-p2.md) |
| 6 | Q2 panel 小範圍（Brook 長文優先） | 待開 | — |
| 7 | Q3 P3 夜間 async + Nami 晨報整合 | 待開 | — |
| 8 | Q2 擴大評估 trigger（2026-05-18） | 記憶已設 | — |

**步驟 3 架構修正**（2026-04-20 session 尾）：PR #52 原本把 Sanji 做成「一個 Slack app 內多 persona，keyword routing」路線，修修實際要**每個 agent 獨立 Slack bot**。PR #55 重寫 `gateway/bot.py` 成 multi-bot registry，token 統一命名 `<AGENT>_SLACK_*`（Nami 也 rename）。見 [project_slack_multi_bot_architecture.md](project_slack_multi_bot_architecture.md)。

**Thousand Sunny 甲板 UI 順序**：先用 Python orchestrator 跑著、功能穩後再加 UI（避免 UI 設計改動頻繁影響邏輯）。

## 步驟 4 borderline 處理結果（PR #53 + #54）

PR #53 code review 抓到 5 個 borderline（全 score < 85），分兩批處理：

**PR #53 同 branch 修掉**（trivial 兩個）：
1. `ingest.py` 兩處 inline 註解「直接用 Sonnet」→ facade/MODEL_ROBIN 語言
2. `shared/llm.py` 模組 docstring 加 Google

**PR #54 follow-up 修掉**（實質三個）：
3. `ask_gemini_multi` 抽出 `role="system"` 併進 `system_instruction`
4. `thinking_budget > max_tokens // 4` 自動縮 + warning（保留 None/≤0 特殊語義）
5. facade `ask()` / `ask_multi()` 加 `thinking_budget` 參數，只對 Gemini forward

## 步驟 5（Q3 P2 Zoro brainstorm）7 個 open questions 決定（2026-04-20）

1. **頻道**：新建 `#brainstorm`
2. **觸發時間**：台北早上 05:00（單次，後續若不夠再加 14:00）
3. **Participant selection**：先用 P1 keyword routing（`_PARTICIPANT_PROFILES`），實測幾週真的常選錯人再升級 LLM
4. **Relevance 濾網**：A+B 混合（keyword pre-filter → LLM 判準，每天 < $0.01）
5. **排程**：APScheduler in-process（跟 thousand-sunny 同 service，重啟一起走）
6. **Novelty 儲存**：agent_memory DB 新 type `"pushed_topic"`
7. **Zoro persona prompt**：實作時一起寫，照新 Nami persona 模板（push-back 文化 + 聲音 anchor + 情境模式）

**實作 blocker**：Zoro 要有獨立 Slack bot（走 [runbook](../../docs/runbooks/add-agent-slack-bot.md) Phase 1），主動推訊息時 Slack 只認 bot ID。

## 實作細節備忘

- `shared/llm_router.py` 解析優先序：`MODEL_<AGENT>_<TASK>` > `MODEL_<AGENT>` > `DEFAULT_MODELS[task]`。`get_provider(model)` 依 prefix 推 provider（claude-/grok-/gemini-/gpt-/o1-/o3-）
- `DEFAULT_MODELS`：`default="claude-sonnet-4-20250514"`、`tool_use="claude-haiku-4-5"`
- `shared/llm.py` facade `ask()` / `ask_multi()` 跨 provider dispatch（Claude + Grok + Gemini；OpenAI 待 wire）
- Thread-local agent 統一由 `shared.anthropic_client._local` 主管；xai_client / gemini_client `from anthropic_client import _local` 共用一個物件，跨 provider cost tracking agent 欄位一致
- 三個 provider 都有對稱的 `_require_<X>_model` fail-fast guard，避免 wrong model ID 被 SDK retry 3× 浪費時間
- Per-agent MODEL_ env 現況：`MODEL_SANJI=grok-4-fast-non-reasoning`（社群口吻），`MODEL_ROBIN=gemini-2.5-pro` 或 `-flash`（KB ingest）— 兩個都要在 VPS `.env` 設才會生效
