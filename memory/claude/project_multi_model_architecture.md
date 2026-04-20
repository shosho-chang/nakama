---
name: 多 Model 多 Agent 架構決策進度
description: 多 LLM provider routing + review panel + Slack brainstorm 三個需求的決策記錄 + 後續 trigger
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

## Q3 — Slack brainstorm：討論中（2026-04-20 進行中）

Use case 已釐清：
- Zoro 社群監控發現熱門主題 → 推到 Slack #general（agent-initiated）
- 其他 agents 加入討論（可能發生在用戶睡覺時，async 夜間 OK）
- 每天早上晨報（Nami 負責？）呈現討論結論
- 用戶也能主動發起

待決定：trigger 閾值、誰 join、turn 控制、stop 條件、summary owner。

## Q4 — Sanji LLM wire + 順序：未討論
