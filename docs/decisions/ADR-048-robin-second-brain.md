# ADR-048 Robin 第二大腦：候選收件匣 + 分層記憶 + Slack 捕捉 + Skill 自薦

- 狀態：Accepted（2026-07-25 修修裁決收斂入 repo；**Phase 0–1 已落地**（#934 / #935），Phase 2–5 為待排序 roadmap）
- 日期：2026-06-24（v2 panel-reviewed）；2026-07-25 更新落地狀態
- 決策者：修修
- Panel：Claude（草稿）→ Codex/GPT-5 → Gemini 2.5 Pro。兩位 auditor 皆判 **approve with modifications**。Audit 全文：`docs/research/2026-06-24-codex-adr048-panel-audit.md`、`docs/research/2026-06-24-gemini-adr048-panel-audit.md`。
- 關聯：延續 [ADR-047](ADR-047-agent-memory-v2-self-improving.md)（user_memories v2 反思整合）；受 [ADR-043] 約束（`KB/Permanent/` 是人寫權威層）；[ADR-046]（三來源 ingest）。

## v1 → v2 變更（panel 整合 + 修修覆寫）

Panel 強烈收斂於：**先修「點子消失」、把學習建在乾淨資料地基上、別在雜訊上硬蓋語意記憶**。v2 採納其地基紀律；修修依人馬卡片盒脈絡與風險偏好做了三項覆寫。

| 項 | v1 | v2 | 來源 |
|---|---|---|---|
| 候選收件匣 | Phase 1 | Phase 1 **+ `candidate_events` 事件表** | Codex#1 / Gemini#1 |
| Robin 學使用者（D-C）| 直接從註解/接受略過寫 user_memories | **分層**：事件 → 行為模式 → 語意記憶（語意只從**穩定模式**導出）| Codex S4 / Gemini S1,S3,S4 |
| Slack bot（D-D）| 「不是另一個專案」 | **照建、提前** — fleeting note 是人馬卡片盒第一級行為，餵既有 fleeting 管線 | **修修覆寫 panel 的「延後」**（Gemini 不懂 Zettelkasten 脈絡）|
| 平台預設（D-E）| 「記憶平台預設」 | **拆兩半**：事件記錄=平台預設；語意抽取=per-agent 政策、非預設 | Codex S4 / Gemini S5 |
| Skill 自薦（D-F）| 靠「episodic 層」 | 先定**結構化 `task_events` schema** 再做 detector | Codex S4 / Gemini S4 |
| 「錯信念汙染下游」 | （隱含永久）| 改為**回饋迴圈**風險 + explore 配額護欄（自我修正即可運作）| 修修質疑、panel 修正 |

**被駁回的 audit 誤報**：Codex 標 D-0（max_tokens）與 episodic 層「未落地」——那是它的 checkout 落後（只到 #929）；#934（max_tokens 修復）、#932（episodic Phase 2a）其實都已合併。但 Gemini「事件 log ≠ 任務 log」的批評**成立**，故 `task_events` schema 仍列為 D-F 前置。

## 命名澄清

三套既有「記憶」同 ADR-047 §命名（1 `memory/*.md`；2 `memories` 表；3 `user_memories` 表）。本 ADR 再加兩個常被混淆的概念：

4. **候選收件匣** = 每日回顧的待處理佇列（**work-queue，不是記憶**）。
5. **行為模式 vs 語意記憶**：行為模式 = 「你做了什麼」的**統計**（deterministic、只餵排序、不會「錯」）；語意記憶 = 「你相信什麼」的 **LLM 詮釋**（會錯、影響互動）。**語意從行為導出，不跳級**。

## 脈絡 / 問題

Robin（KB agent）每日回顧從「昨天」的劃線提候選永久卡，但：(1) **點子會消失**——1 天滑動窗 + 單槽快照覆寫 + 未處理不結轉；(2) Robin 不在 `user_memories`，**不懂使用者**（只累積知識、不累積對你的理解）；(3) 沒有「重複任務 → 建議 skill 化」機制；(4) 記憶不是平台預設，每個 agent 要手接。Phase 0 阻斷 bug（P-1 `max_tokens=2048` 截斷 → 每日 0 候選）**已修**（#934）。

## 願景（修修，2026-06-24）

每個 agent 都有記憶、越來越懂我、重複任務會主動建議 skill 化；Robin 上 Slack 變成知識庫的對話入口（靈感 → fleeting note；好奇 → 收資料存 vault）。

## 決策

### D-0（已完成 #934）：P-1/P-2 輸出別截斷 + 容錯 JSON 解析
新 `shared/llm_json.py`（去 fence + bracket-depth）；`_ask_p1_llm` 8192、`_ask_p2_llm` 4096。

### D-A：候選收件匣 ≠ 記憶；學習分三層、依賴單向
`candidate_events`（事件）→ `user_behavior_patterns`（統計）→ `user_memories`（語意）。上層只讀下層，不從生訊號跳到語意。

### D-B（Phase 1，已完成 #935 `shared/candidate_inbox.py`）：候選收件匣 + 事件表
- 候選**持久化**（body / source_refs / status / first_seen / last_seen / action history）；開卡記 `done`；未處理**結轉**；dedupe；aging。修「點子消失」。
- `candidate_events`（accept / skip / defer / open + timestamp + 可選原因）= 後續所有學習的 **ground truth**。

### D-C（修訂）：Robin 分層學使用者 — 行為 + 語意「一起做、層次鎖死」
- **行為模式層**：夜間聚合 `candidate_events` → `user_behavior_patterns`（「對來源 X 高親和、對主題 Y 低親和」），deterministic，餵 **P-1 排序**。
- **語意層**：從**穩定**行為模式 + 使用者**親手寫的 note / 明確理由**導出 `user_memories`（低信心、帶 provenance），只影響排序/脈絡、**絕不碰 KB 真相**（ADR-043 紅線）。
- **護欄**（讓「自我修正」真的能運作，破解幻覺偏好回饋迴圈）：① **explore 配額**——信念只微調排序、不獨裁，永遠保留一塊多元/非個人化候選，讓反例進得來；② 從**聚合**導出、非單一動作；③ **zh-TW 否定/modality 防誤讀**（`未必`/`並非` 不可當背書）；④ 騎 ADR-047 夜間反思做 supersede / age-out。

### D-D（修訂）：Robin Slack bot — 照建、提前（修修優先序）
- fleeting note 捕捉（人馬卡片盒**第一級行為**；餵既有 `KB/Fleeting` status:open + 每日回顧 fleeting 區，非另開噪音管）；好奇主題 → 收資料存 vault。
- 對話**命令**（research / make-note）= **高意圖訊號**，是 D-F 最乾淨的原料。

### D-E（修訂）：平台「資料記錄」預設；「語意抽取」非預設
- `candidate_events` + `task_events` 記錄 = **全 agent 平台預設**（便宜、結構化、所有學習的原料）。
- 語意記憶**抽取** = per-agent 政策（`memory_mode: none/read/context/extract`），**不自動預設**（批次 agent 與對話 agent 政策不同）。

### D-F（修訂）：skill 自薦 = 一個共享 detector，前置是結構化 task schema
- 先定 `task_events` schema（`task_id`/`agent`/`user_intent`/`input_artifacts`/`outputs`/`repeat_key`/`success`）。
- 一個**跨 agent** detector 掃 `task_events` 找重複模式 → HITL：「我看到 7 天內 5 筆相似任務 [列出]，是同一個 workflow 嗎？」→ 建議 skill 化。共享服務 → 「每個 agent 重複任務都被提醒」自動成立。

## 路線圖

| Phase | 內容 | 依賴 |
|---|---|---|
| **0** ✅ | max_tokens + 容錯解析（#934）| — |
| **1** ✅ | 候選收件匣 + `candidate_events`（修點子消失 + ground truth）（#935 `shared/candidate_inbox.py`）| 0 |
| **2** | Robin Slack bot（fleeting + research；餵既有管線）| 1（fleeting/inbox 落點）|
| **3** | 行為模式 → P-1 排序 **＋** 語意記憶（從行為導出，含四道護欄）| 1（事件資料）|
| **4** | `task_events` schema（平台預設記錄）| — |
| **5** | 跨 agent skill 自薦（HITL）| 4 |

語意記憶上線後仍受 ADR-047 Phase 3 評測紀律約束：要量化證明排序變準、且不引用過時。

## 後果 / 風險 / Open questions

**正面**：點子不漏；Robin 越來越懂你；團隊共享對你的理解（ADR-047 Phase 2 跨 agent）；重複工作自動浮現 skill；Slack 即時捕捉靈感。

**風險與緩解**：
- *幻覺偏好回饋迴圈*（錯信念壟斷你看到的 → 自我應驗）→ D-C explore 配額 + 從聚合導出 + 反思退役。
- *task「重複」誤判* → D-F 結構化 schema + HITL 確認。
- *zh-TW 抽取誤讀* → D-C 否定/modality 防護。
- *`user_memories` 單表混型*（stated / inferred / behavioral 混一張）→ 先記為技術債，必要時 Phase 6 分層。

**Open questions**：
1. Robin 的語意記憶要不要跟 Nami 共享（ADR-047 Phase 2 跨 agent）——例如 Nami 知道你最近在讀什麼？
2. explore 配額多大（每頁保留幾張非個人化候選）？
3. skill 自薦的「重複」門檻（幾次、多相似才提醒）？
4. 評測 harness（ADR-047 Phase 3 FAMA 式）怎麼涵蓋 Robin 的個人化排序品質？

## 落地狀態（2026-07-25 更新）

- **Phase 0** ✅ #934 — `shared/llm_json.py` + `_ask_p1_llm` 8192（`agents/robin/daily_review.py`）。
- **Phase 1** ✅ #935 — `shared/candidate_inbox.py`（候選持久化 + `candidate_events` 事件流 + 結轉），接進 `daily_review.py` 與 `thousand_sunny/routers/kb_review.py`。
- **Phase 2–5** ❌ 未動工，優先序待修修排。記憶基建面：ADR-047 Phase 1 / 2a / 2b（#927 / #932 / #930，反思整合 + episodic 層 + fleet 共享）已全部合併，D-C / D-F 落地時直接騎在其上；`memory_extractor` 目前僅接 Nami（`gateway/handlers/nami.py`），Robin Slack bot（Phase 2）是它接 Robin 的解鎖點。
