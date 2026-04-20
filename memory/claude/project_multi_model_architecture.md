---
name: 多 Model 多 Agent 架構決策進度
description: 多 LLM provider routing + review panel + Slack brainstorm 三個需求的決策記錄 + 8 步建置順序 + trigger
type: project
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
| 1 | Q1 router skeleton（`shared/llm_router.py` + env 讀 `MODEL_<AGENT>`） | ✅ **已完成 2026-04-20** | [#50](https://github.com/shosho-chang/nakama/pull/50) merged as `e216147` |
| 2 | Q1 第一個新 provider（xAI）+ Sanji wire（第一版人格 prompt） | 進行中 | — |
| 3 | Q3 P1 用戶主導 brainstorm（Sanji 當第二個 Slack bot + 簡單 orchestrator） | 待開 | — |
| 4 | Q1 Gemini provider + Robin ingest 改走 Gemini | 待開 | — |
| 5 | Q3 P2 Zoro 白天推題 | 待開 | — |
| 6 | Q2 panel 小範圍（Brook 長文優先） | 待開 | — |
| 7 | Q3 P3 夜間 async + Nami 晨報整合 | 待開 | — |
| 8 | Q2 擴大評估 trigger（2026-05-18） | 記憶已設 | — |

**步驟 3 更正**：用戶確認**第二個 Slack bot 用 Sanji**（原推薦 Robin，但 Sanji 社群 agent 在 #general 講話更自然）。

**Thousand Sunny 甲板 UI 順序**：先用 Python orchestrator 跑著、功能穩後再加 UI（避免 UI 設計改動頻繁影響邏輯）。

## 實作細節備忘（步驟 1 完成後）

- `shared/llm_router.py` 解析優先序：`MODEL_<AGENT>_<TASK>` > `MODEL_<AGENT>` > `DEFAULT_MODELS[task]`
- `DEFAULT_MODELS`：`default="claude-sonnet-4-20250514"`、`tool_use="claude-haiku-4-5"`（與舊硬寫值完全一致，向下相容）
- `anthropic_client.py` 三函式 `model=None` 時走 router，讀 `_local.agent`（threading.local，由 `set_current_agent()` 設）
- 向下相容：顯式 `model=X` 的 callsite 零改動（gateway/router.py、memory_extractor、translator、transcriber）
- Code review 發現但未 block 的未來改善點（步驟 2 一起做）：
  - Issue C（72 分）：未呼叫 `set_current_agent` 的 thread → router silent 回 default。PR #20 踩過同類，值得在步驟 2 主動加 `logger.debug` 或 strict mode
  - Issue B（62 分）：非 Anthropic model ID 會噴 SDK 錯 retry 3 次。步驟 2 加 xAI 後 router 需擴 provider dispatch，届時處理
