# ADR-048 Robin 第二大腦：候選收件匣 + Robin 記憶 + 記憶平台化 + Skill 自薦

- 狀態：Proposed（草案待修修審；除 Phase 0 是當下要修的 bug，其餘尚未實作）
- 日期：2026-06-24
- 決策者：修修
- 關聯：
  - **延續 [ADR-047](ADR-047-agent-memory-v2-self-improving.md)**（`user_memories` v2 反思整合 + bi-temporal + episodic 層）。本 ADR 把 ADR-047 的記憶能力**延伸到 Robin**，並把「記憶 + 自我改進」提升為**全 agent 平台預設**。
  - [ADR-043 / ADR-046]（Centaur KB / 三來源 ingest）— Robin 的知識層。
  - [ADR-045]（Robin KB 角色與 skill family）。

> **命名澄清**（沿用 ADR-047 §命名，再加一個常被混淆的概念）：
> 1. `memory/*.md` ＝ Claude Code 跨 session 記憶。
> 2. `memories` 表（ADR-002 Tier 3）＝ agent 學到的**知識**。
> 3. `user_memories` 表（ADR-047）＝ agent 對**使用者**的記憶。
> 4. **候選收件匣（本 ADR 新增）＝ 每日回顧的待處理佇列**——這是 **work-queue，不是記憶**。修修常把 (4) 跟 (3) 混為一談；D-A 釐清。

## 脈絡

### 問題
1. **點子會消失**：每日回顧是「昨天」單日窗 + 單槽快照、每天覆寫、未處理不結轉（見本 session 診斷）。— 這是 (4) work-queue 問題。
2. **Robin 不懂使用者**：Robin 不在 `user_memories`（系統 3），只累積知識（Wiki）、不累積「對修修的理解」。關係層看過即忘。
3. **重複工作不會自動浮現 skill 機會**：沒有「跨 agent、偵測重複任務 → 建議 skill 化」的機制。
4. **記憶不是平台預設**：`user_memories` 基建共用，但每個 agent 要**手動接線**（read + write）；新 agent 不接就沒有。
5. **Phase 0 阻斷器**：`_ask_p1_llm` / `_ask_p2_llm` 的 `max_tokens=2048` 會**截斷**冗長中文候選 JSON → 無封閉 `]` → `_parse_json_array` 回空 → **每天 0 候選**。沒修這個，下面全部免談。

### 願景（修修，2026-06-24 對話）
> 每個 agent 都有記憶、越來越懂我、會主動建議把重複工作 skill 化；Robin 上 Slack 變成知識庫的對話入口（靈感→fleeting note、好奇→收資料存 vault）。

## 決策

### D-0（Phase 0，當下 bug，獨立先修）：P-1/P-2 輸出別被截斷 + parser 容錯
- `_ask_p1_llm` / `_ask_p2_llm` `max_tokens` 2048 → 8192（實測：06-20 在 2048 截斷回 0、在 8192 完整吐 7 張）。
- `_parse_json_array` / `_parse_json_object` 改 bracket-depth + 去 ` ```json ` fence——**重用 `shared/memory_reflection._extract_array`** 既有做法（ADR-047 已驗證「容忍 reasoning 前言、從第一個 `[` 做深度配對」）。
- 驗收：06-20 / 06-21 跑出非空候選；單元測試覆蓋「fenced」「truncated」「leading prose」三種 LLM 輸出。

### D-A：候選收件匣 ≠ 記憶，是兩層（互餵）
- **收件匣（work-queue）**：候選結轉、開卡/略過才消失、狀態持久 → 修「點子消失」。
- **記憶（`user_memories`）**：Robin 對使用者的理解 → 修「不懂我」。
- **互餵**：收件匣的 accept/skip 是記憶最好的學習訊號；記憶讓收件匣排序更準。兩者**不同 store、不同用途**，但一起構成第二大腦。

### D-B：候選收件匣（取代單日窗單槽）
- 候選**持久化**（不只 `latest` 單槽）；每天 = 新候選 ∪ 未處理舊候選（以 `candidate_id` dedupe）。
- **開卡時記錄 `candidate_id` 為 done**（現在 `create_permanent` 收了 id 卻沒記帳）；略過 = done；之後再說 = 延後。
- 掃描窗可加寬（最近 N 天未處理），但核心是**不覆寫、會結轉**。

### D-C：Robin 進 `user_memories`（延伸 ADR-047）
- Robin 成為第 4 個寫 `user_memories` 的 agent。
- **抽取來源不是對話**（Robin 是批次）：① 註解/筆記裡的穩定主張、② 你在回顧裡 accept/skip 的傾向 → 學「修修在乎哪些主題、什麼算值得開卡」。
- **餵回**：P-1 候選排序、ingest 摘要角度個人化。
- 騎現有**每晚反思整合**（cron `--all` 已涵蓋；Robin 一旦有記憶就自動被整合/退役/promote）。

### D-D：Robin Slack bot = 對話介面 = 記憶抽取的自然來源
- 用途（修修）：靈感 → fleeting note；好奇 → 收資料存 vault。
- 一旦 Robin 有 gateway 對話 handler（鏡 `gateway/handlers/nami.py`），現成的 `memory_extractor`（對話結束背景 Haiku 抽取）**直接套用** → Robin 的對話自動變記憶。
- **結論：bot 不是另一個專案，是讓 Robin 記憶變自然的那一步。**

### D-E：記憶平台化 —「會自動套用到每個 agent 嗎？」
- **現況：不會自動。** `user_memories` 基建共用，但每個 agent 要明確接（read：`format_as_context`；write：extractor 或 `agent_memory.add`）。反思 cron `--all` 只整合「已經有記憶」的 agent。新 agent 不接 → 沒記憶。
- **決策：把「會記憶」做成 agent 鷹架的預設**——對話型 agent 走共用 gateway handler scaffold（read+write 標配）；批次型 agent（Robin/Franky）走「非對話抽取」那條。之後任何新 agent 自動有記憶，不用每次手接。

### D-F：Skill 自薦 = 跨 agent 的**共享**反思（不是每個 agent 各做）
- 每個 agent 把「使用者請求/任務」記成 **episodic 事件**（時間戳）——**直接用 ADR-047 Phase 2a 剛落地的 episodic 層（#932）**。
- 一個**共享的週期性偵測**（鏡反思 pass）掃所有 agent 的 episodic 任務日誌 → 找重複模式（「修修這週請 Robin 收 X 主題 5 次、流程都一樣」）→ 主動建議「要不要做成 Skill?」。
- 因為是**共享服務**,「每個 agent 的重複任務都會被提醒 skill 化」**自動成立**——不必每個 agent 各自實作。這直接回答修修的問題：要「每個 agent 都會 skill 自薦」，正確做法是**一個共享偵測 + 全 agent 記 episodic**，而非逐一手接。

## 路線圖（順序可調；Phase 0 必先，Phase 1 獨立有價值）

| Phase | 內容 | 依賴 |
|---|---|---|
| **0** | D-0：P-1/P-2 max_tokens + parser 容錯（**當下 bug，獨立 PR**）| 無 |
| **1** | D-B：候選收件匣（結轉 + 開卡記 done）| 0 |
| **2** | D-C：Robin `user_memories`（註解/回顧訊號抽取 + 餵回 P-1）| 1（accept/skip 訊號）|
| **3** | D-D：Robin Slack bot（fleeting / research）→ 對話記憶 | 2 |
| **4** | D-F：episodic 任務日誌 + skill 自薦（共享）| ADR-047 Phase 2a episodic |
| **5** | D-E：記憶平台化（memorable-agent 預設）| 2/3 驗證後 |

## 後果 / 風險 / Open questions

**正面**：點子不漏；Robin 懂你；團隊共享對你的理解（ADR-047 Phase 2 跨 agent）；重複工作自動浮現 skill 機會。

**風險與緩解**：
- *記憶過度個人化 → 候選同溫層* → 保留 explore 配額（不是全照偏好排序）。
- *skill 自薦誤報* → dry-run、你確認才動（鏡 ADR-047 反思的保守紀律）。
- *episodic 任務日誌體積/隱私* → 單使用者、本機 SQLite、可稽核，可接受。

**Open questions**：
1. 收件匣上限 / 老化策略（候選堆太多怎麼辦）？
2. Robin 記憶要不要跟 Nami 記憶共享（ADR-047 Phase 2 跨 agent）——例如 Nami 知道你最近在讀什麼？
3. Skill 自薦的「重複」門檻（幾次、多相似才提醒）？
4. 評測（ADR-047 Phase 3 FAMA 式）怎麼涵蓋 Robin 的個人化候選品質？

## 落地狀態
- 草案。**唯一可立即執行的是 Phase 0**（D-0 bug 修復），其餘待修修審完 ADR、排優先序後分階段開 PR。
