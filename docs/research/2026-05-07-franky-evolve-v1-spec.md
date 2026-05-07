# Franky Evolution Loop V1 — Spec Doc

**Status**: Draft（panel triangulation 前）
**Date**: 2026-05-07
**Author**: Claude Opus 4.7（修修 grill 5 fork 後共識）
**Scope**: 把 Franky 從「monitor / news-digest agent」重定義為「evolution agent」 — 把外部 AI 訊號接到 Nakama 內部架構決策的 closed loop
**Purpose**: 此文件**不是 ADR**，是 panel triangulation 的 input artifact。Codex / Gemini 應該對 5 個 fork 的選擇 + open questions 做 push-back，整合後才寫 ADR-022。

---

## 1. Problem statement

### 1.1 現況事實核對

Franky 目前的 AI 新聞 pipeline（`agents/franky/news_digest.py` + `prompts/franky/news_curate.md` + `news_score.md`）：

- **Sources**（`config/ai_news_sources.yaml`，12 個）：
  - 官方 blog RSS × 8：OpenAI、Google Research、DeepMind、Hugging Face、Simon Willison、Latent Space、LangChain Changelog、Together AI
  - GitHub releases atom × 4：vLLM、llama.cpp、transformers、langchain
  - Anthropic 走 HTML scrape（`anthropic_html.py`，因為 RSS 全 404）
- **Pipeline**：每天 06:30 台北跑 → fetch + dedupe → curate prompt 挑 5-8 條（7 類 tag）→ score prompt 跑 4 維度（Signal × 1.5 / Novelty × 1.0 / Actionability × 1.2 / Noise × 1.0，加權除 4.7）→ pick 條件 `overall ≥ 3.5 AND signal ≥ 3` → 寫 `KB/Wiki/Digests/AI/YYYY-MM-DD.md` + Slack DM
- **`weekly_digest.py`** 是**工程進度週報**（VPS health / cron 成功率 / cost），跟 AI 新聞**無關**，僅複用「週報」名稱

### 1.2 Loop 在哪斷掉

止於「digest 寫完、Slack 推給修修」。**沒有任何結構性 bridge** 把：

1. 「這條新聞看起來有用」 → 「它對 Nakama 哪個 agent / pain point 適用」
2. 「這個工具值得試」 → 「該開 issue / spike / ADR」
3. 「我們試了某個建議」 → 「有沒有真的變強」

修修原話：「真正有價值的是在獲取資訊之後，**萃取出跟我們的專案以及開發流程相關的技術，並且建議我們評估，進而實踐**」。

### 1.3 進化目標的雙焦點

修修明確指出兩個進化目標（不是 generic AI awareness）：

1. **Nakama 這個專案** — agent 設計、prompt 技巧、新 model 採用、agent framework
2. **開發 Nakama 的工作流程** — Claude Code hooks、skills、sandcastle、MCP server、subagent pattern

現有 source pool 全是 generic AI news，**無針對這兩個目標的場景化 source**。

---

## 2. 五個關鍵 fork 與凍結結論

### Fork 1：對比基準 — Franky 拿什麼當「Nakama 現況」的判斷依據？

**選擇：C 為主 + A 為輔**（RAG over recent 30d ADR + MEMORY + plans，加 open GitHub issues）

**Rejected**：
- **B（pain-point ledger `docs/pain-points.md`）**：高機率變 stale；修修已有 issues + memory + plans 三套 todo 系統
- **A only（純 issues）**：issues 描述常太短、缺架構脈絡，會錯過「ADR-N 假設可被新工具推翻」這類連結
- **D（all of above）**：ledger 維護負擔不值得邊際訊號

**理由**：修修每次收工、戰略決定都寫進 `MEMORY.md` + ADR + plan，這些檔本身就是高訊號 corpus，零維護成本。

### Fork 2：daily 命中後的下游接什麼？

**選擇：雙軌（daily + weekly synthesis）**

**Rejected**：
- **單軌（只加強 daily）**：「每天看一條」訊號太碎，趨勢被淹沒（例：三家陸續釋出 streaming tool use 才看得出該 adopt）
- **三軌（加 auto-open issue per high-relevance）**：會雪崩 triage queue；低訊號 issue 比沒 issue 更糟
- **直接 spike（Franky 自跑 sandcastle）**：燒錢 + 違反 HITL 直覺

**設計**：
- daily（既有路徑）：raw 蒐集 + 初篩，加 Relevance 維度
- **weekly synthesis（新增，週日 22:00）**：吃過去 7 天 picks + 當前 ADR/issue snapshot，產 1-3 條 proposal
- **monthly retrospective**（月底最後一個 weekly slot 換成 retro）

### Fork 3：proposal 容器是什麼？

**選擇：GitHub issue + label `franky-proposal` + `needs-triage`**

**Rejected**：
- **純 vault page**：loop 不閉環，看完就忘
- **專屬 surface `/bridge/franky/proposals` + SQLite**：違反 `user_vault_access_pattern.md`（修修只看時間軸 + Project，不主動逛 Bridge）；多一個 stale 風險點
- **混合（vault + 高優 issue）**：兩套狀態難對齊

**設計**：
- proposal = issue with `franky-proposal` + `needs-triage`
- 狀態追蹤靠既有五大 canonical triage label：`needs-triage` → `ready-for-agent` / `wontfix` / `ready-for-human` / `needs-info`
- 閉環：你 ack → 拍 `ready-for-agent` 或 `wontfix` → PR close issue
- **硬上限 3 條/週**，超過進 `KB/Wiki/Digests/AI/Weekly-YYYY-WW.md`
- `franky-proposal` 是輔助 label，不破壞 triage state machine

### Fork 4：source 擴張哲學？

**選擇：少而精，場景化擴張**

**Rejected**：
- **廣撒網 20+（含 Reddit / HN / X）**：訊噪比差、curate LLM 預算暴漲、X scrape 反爬嚴重
- **只信 curator weight（不擴）**：缺 GitHub-native 訊號（新 hook、新 MCP server、新 agent framework 實作）
- **B + Reddit 窄 sub**：邊際成本不值

**新加 3 條 source**（直接對應 §1.3 雙焦點）：
1. **GitHub trending Python**（filter `agent|llm|mcp|claude`）— daily top 10 by stars-this-week
2. **`anthropic/anthropic-cookbook` + `anthropic/claude-code` releases atom** — 單一 source 命中率最高
3. **`awesome-mcp-servers` + `awesome-claude-code` repo activity** — README diff，新增條目 = 社群剛發現的工具

**配套**：daily curate 上限 5-8 → 8-12（新 source 命中率高）；score gate 不變。

### Fork 5：feedback loop？

**選擇：proposal 強制 success_metric + 月度 retrospective synthesis**

**Rejected**：
- **無正式 metric**：看不到 ship 後實際改善
- **Bridge dashboard**：違反 `user_vault_access_pattern`
- **純 Slack 月報**：看完就忘，不落 vault

**設計**：
- proposal frontmatter **5 欄全強制**：
  - `success_metric` — 例「Robin ingest LLM cost 降 30%」「KB stub 率 < 50%」
  - `related_adr` — 例 ADR-020、ADR-016
  - `related_issues` — 例 #383、#420
  - `try_cost_estimate` — 美元 + 小時
  - `panel_recommended` — yes/no（見 §3 Panel hook）
- **月度 retrospective**（每月最後一個週日 slot 替換 weekly synthesis）：列上月 proposal ship/reject/skipped + metric 變化，落 `KB/Wiki/Digests/AI/Retrospective-YYYY-MM.md` + Slack DM 摘要
- 自我評估訊號：上月 5 條 → 3 wontfix 表示下月 synthesis 該更挑剔；3 ship 全達標表示可以更積極

---

## 3. Panel triangulation hook（內建設計）

weekly synthesis 產 proposal 時，若涉及 architectural lock-in（碰某個 ADR 核心假設、改 agent contract、引入新 dependency），自動在 issue body 加 `panel_recommended: yes`。

**Franky 不自跑 panel**（成本高 + scope creep）。標記後修修看 issue 時才手動 invoke `/multi-agent-panel`。

對應已有的 `multi-agent-panel` skill（5/6 ADR-020 panel 後凍結）+ `feedback_adr_principle_conflict_check.md`。

---

## 4. Pipeline 整體流程

```
[Daily 06:30 台北]
  ├─ Sources（12 既有 + 3 新場景化）
  ├─ Fetch + dedupe
  ├─ Curate prompt（5-8 → 8-12 picks）
  ├─ Score prompt（4 → 5 dim：加 Relevance）
  │    └─ Relevance corpus = recent 30d ADR/MEMORY/plans + open issues（embedding 檢索）
  ├─ Pick gate：overall ≥ 3.5 AND signal ≥ 3 AND relevance ≥ 3
  └─ Output：KB/Wiki/Digests/AI/YYYY-MM-DD.md + Slack DM

[Weekly 週日 22:00]
  ├─ Input：過去 7 天所有 picked items（已 score ≥ 3.5）+ ADR/issues snapshot
  ├─ Synthesis prompt：找 3 種 pattern
  │    a) 多家做同件事的趨勢
  │    b) 某 ADR 假設可能被新工具推翻
  │    c) backlog issue 跟某新聞高度匹配
  ├─ 產 1-3 條 proposal（硬上限 3，超過進 vault）
  ├─ 每條 proposal 強制 5 欄 frontmatter
  ├─ Architectural lock-in 自動標 panel_recommended: yes
  └─ Output：gh issue create --label franky-proposal,needs-triage
            + KB/Wiki/Digests/AI/Weekly-YYYY-WW.md（含未開單的 overflow）
            + Slack DM 摘要

[Monthly 每月最後一個週日，替換 weekly synthesis]
  ├─ Input：上月所有 franky-proposal issues + 對應 PR / commit / metric 變化
  ├─ Retrospective prompt：ship/reject/skipped 統計 + metric 達標檢驗 + 哲學調整建議
  └─ Output：KB/Wiki/Digests/AI/Retrospective-YYYY-MM.md + Slack DM
```

---

## 5. Open questions（panel 重點咬點）

以下為 Claude 在草擬時 confidence 不夠 / 邊界模糊 / 可能有 confirmation bias 的點，請 panel 重點 push-back：

### Q1：RAG corpus 設計細節

- **問題**：「recent 30d ADR + MEMORY + plans」用什麼 embedding？哪個 chunk 策略？要不要 reranker？
- **Claude 預設**：複用 ADR-020 §S6 的 BGE-M3 + bge-reranker-large。但 Franky 是異質場景（外部 news vs 內部 ADR），可能需要不同 retrieval 策略
- **panel 看點**：BGE-M3 在 cross-domain（英文 news ↔ 繁中 ADR）retrieval 是否 robust？要不要走 LLM-as-retriever 直接讀 ADR/MEMORY 全文（context 預算容許）？

### Q2：Weekly synthesis 是否會成為「為了開 issue 而開 issue」的儀式

- **問題**：硬上限 3/週的反面是 minimum 0/週。但 LLM 有偏向「總要產出點什麼」的傾向，可能週週硬擠 3 條 mediocre proposal
- **Claude 預設**：靠 prompt explicit 寫「寧缺勿濫，0 條也合法」+ retrospective 階段監控「ship rate < X% 時降頻」
- **panel 看點**：這個自我節制機制是否 robust？需不需要硬性 quality gate（如 relevance ≥ 4 才能進 synthesis 候選池）？

### Q3：Proposal 「architectural lock-in 自動標」的判斷邊界

- **問題**：哪些 proposal 算 architectural lock-in？由 LLM judge 還是 rule-based（碰特定關鍵字 / 引用既有 ADR 編號）？
- **Claude 預設**：LLM judge，prompt 給 3 條判準（碰 ADR 假設 / 改 contract / 新 dep），保守傾向（疑似就標 yes）
- **panel 看點**：LLM judge confirmation bias 高發區。要不要做雙層 gate（LLM 判定 + 觸發特定 keyword 也強制標 yes）？

### Q4：Source 擴張的 GitHub trending 過濾品質

- **問題**：GitHub trending Python（filter `agent|llm|mcp|claude`）每天 top 10 — 但 trending 演算法本身受短期星數影響，常含 vibecoded demo / star-farming repo
- **Claude 預設**：靠 score gate 過濾。每天 +10 候選，curate 預算上漲 ~20%
- **panel 看點**：是否該加額外 sanity check（如 repo created_at > 30 天 / stars_count > 100 / 有 README 描述）？或者放棄 trending 改用更窄的 source（如特定 user 的 starred 列表）？

### Q5：Retrospective 的 metric 驗證可行性

- **問題**：success_metric 寫「Robin ingest LLM cost 降 30%」聽起來精確，但要驗證需要 baseline + post-ship 量測。修修現有 instrumentation 是否覆蓋？
- **Claude 預設**：靠 `state.db` 的 `api_calls` 表（已有 token cost 記錄）+ 手動補 metric 收集腳本
- **panel 看點**：success_metric 的可驗證性是否被高估？大部分 proposal 的 metric 可能是 qualitative（「KB stub 率」「prompt 品質」），retrospective 階段如何處理 non-quantitative metric？

### Q6：跟 Nakama 內既有 Franky 職責的相容性

- **問題**：Franky 現有職責 = 套件更新 / CVE 掃描 / health check / news digest / weekly engineering report。加 evolution loop 後，Franky scope 是否過載？
- **Claude 預設**：新增是「news digest 的下游 synthesis」，不影響其他職責。但 Slack DM 流量會從每天 1 條變每天 1 條 + 每週 1 條 + 每月 1 條
- **panel 看點**：是否該拆成新 agent（如 `Sengoku` evolution agent）？還是保留在 Franky 但開新 cron + 新 prompt 區塊？ADR-001 agent 邊界是否要更新？

### Q7：Daily Relevance 維度跟既有 4 維度的衝突

- **問題**：加 Relevance 維度後，5 維度加權公式如何重設？目前 `signal × 1.5 + novelty × 1.0 + actionability × 1.2 + noise × 1.0` ÷ 4.7
- **Claude 預設**：`relevance × 1.3` 加進分子，分母改 6.0。但這個權重是猜的
- **panel 看點**：權重 calibration 該基於什麼？需不需要先跑 1 週 dry-run 跟修修對齊「什麼分數該 pick」再 freeze 權重？

---

## 6. Implementation skeleton（先列，不展開 — ADR-022 ship 後 to-issues 拆 slice）

預估 4 個 vertical slice：

- **S1**：Source 擴張（3 新 source + curate 上限調整 + dry-run 驗證 fetch 成功）
- **S2**：RAG corpus + Relevance dimension（embedding infra 複用 + score prompt 改版 + pick gate 升級）
- **S3**：Weekly synthesis pipeline（新 cron + 新 prompt + gh issue 開單 + vault overflow + Slack DM）
- **S4**：Monthly retrospective（替換 weekly slot 邏輯 + retro prompt + metric 收集腳本）

每 slice 含 unit + integration tests，跨 slice 共用 fixture。

---

## 7. 預算估計（粗估）

- **Daily 額外成本**：5 維度 score（從 4 維） + Relevance corpus retrieval ≈ +30% LLM cost on score 階段，每天額外 ~$0.05
- **Weekly synthesis**：1 LLM call/週，含 30d corpus context（~50k tokens）+ 7 天 picks + ADR/issues snapshot ≈ ~$0.40/週
- **Monthly retrospective**：類似 weekly ≈ ~$0.50/月
- **Total marginal cost**：~$3/月（相對 Nakama 整體 LLM 月成本可忽略）

實作工時粗估：4 slice × 2-4 小時 = 10-16 小時（單修修 + Codex 並行）。

---

## 8. Decisions log（panel 整合後寫進 ADR-022）

待 panel 跑完，把 panel 採納的修正 + reject 的 critique 整合，再寫 ADR-022。
