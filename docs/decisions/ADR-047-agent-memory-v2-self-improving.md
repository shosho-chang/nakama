# ADR-047 Agent Memory v2 — 自我學習的 user_memories（反思整合 + bi-temporal + 路線圖）

- 狀態：Accepted（Phase 1 已落地 + 部署，見 §落地狀態）
- 日期：2026-06-24
- 決策者：修修
- 關聯：
  - **延續 ADR-002**（Tier 3 agent-learned `memories` 表）— 本 ADR 不動那張表；針對的是**另一張**表 `user_memories`（agent 對「使用者」的記憶）。兩者常被混為一談，D-A 釐清。
  - **不影響** `memory/` markdown（`memory/SCHEMA.md`，ADR-028 vault 規則無關）— 那是 **Claude Code 跨 session 記憶**，不是 runtime agent 對 user 的記憶。
  - 研究依據：deep-research（2026-06-24，18 條 adversarially-verified claims）。

> **命名澄清**：本 repo 有**三套**「記憶」常被混淆：
> 1. `memory/*.md`（frontmatter + INDEX）＝ **Claude Code 自己**的跨 session 記憶。
> 2. `memories` 表（`shared.memory` / FTS5）＝ agent 執行任務時學到的**知識**（ADR-002 Tier 3）。
> 3. **`user_memories` 表（本 ADR）＝ Nami/Sanji/Zoro 對「使用者」的記憶**——偏好、事實、決策。
>
> 修修體感「Agent 很笨、不會越來越懂我」指的是 (3)。本 ADR 只處理 (3)。

## 脈絡

### 問題

`user_memories`（`shared/agent_memory.py`）是 runtime 讓 agent「認識使用者」的層，但它只會**長大、不會變聰明**：

- **只 append**：`shared/memory_extractor.py` 每輪對話用 Haiku 抽取 → `agent_memory.add()` 以 `(agent, user_id, subject)` upsert。靠 LLM「重用既有 subject」來避免重複，但 LLM 一旦發明新 subject 邊界（`工作習慣` vs `工作時段`）就產生語意重複，永不收斂。
- **矛盾並存**：沒有任何機制偵測/解決矛盾，兩筆衝突記憶都「還有效」。
- **檢索天真**：`search()`（`shared/agent_memory.py`）排序 `confidence × 1/(1+hours_since_access)`，`format_as_context()` 直接灌 top-20、**完全不看當前訊息**，也無語意檢索。
- **遺忘半殘**：`decay()` / `prune()` 存在，已由 cron 排程（每週 decay、每月 prune），但沒有「主動退役過時事實」的機制。
- **`UNIQUE(agent, user_id, subject)`** 讓同一 subject 的事實變更只能就地覆蓋，無 row 級歷史。

### 研究結論（deep-research，已驗證）

領域共識：**現行記憶系統擅長「存」、不擅長「整合 / 更新 / 遺忘」** —— 正是上面的痛點。

- **Memora 2026（FAMA 指標）**：記憶 agent 擅長 remembering（avg 119.45）但拙於 reasoning over memory（27.55），且在避免**過時資訊**上僅略勝裸 LLM（[arxiv 2604.20006](https://arxiv.org/html/2604.20006v1)）。
- **Survey**：LoCoMo / LongMemEval「largely overlook... consolidation, updating, forgetting, and selective retention」（[arxiv 2505.00675](https://arxiv.org/pdf/2505.00675)）。
- **Episodic-memory paper**：RAG/抽取「store chunks without relational metadata、no mechanism to generalize」——只檢索不整合（[arxiv 2502.06975](https://arxiv.org/pdf/2502.06975)）。
- 領先做法的核心機制：**memory evolution**（A-MEM, [github](https://github.com/agiresearch/a-mem)）、**sleep-time compute**（Letta, [blog](https://www.letta.com/blog/sleep-time-compute)）、**bi-temporal KG**（Zep/Graphiti, [arxiv 2501.13956](https://arxiv.org/abs/2501.13956)）。

## 決策

### D-A：強化目標鎖定 `user_memories`，不是 markdown、不是 `memories` 表

「不懂我」的是 (3)。本 ADR 全部改動落在 `shared/agent_memory.py` + 新 `shared/memory_reflection.py` + `cron.conf`。markdown / `memories` 表不動。

### D-B：反思整合 pass（自我學習迴圈）— Phase 1，已落地

新 `shared/memory_reflection.py`：週期性讓 **Sonnet**（reasoning 重，非 extractor 的 Haiku）複習某 user 的 **active** 記憶，輸出保守、結構化、可稽核的操作：

| op | 作用 | 對應研究 |
|---|---|---|
| `merge` | 合併重複 subject（content 不可遺失資訊）| A-MEM memory evolution |
| `supersede` | 矛盾退役（bi-temporal，見 D-C）| Zep/Graphiti |
| `promote` | 多處佐證 → 提高 confidence（檢索排序上浮）| — |
| `drop` | 噪音 → 軟失效 | FAMA forgetting |

紀律：**預設 dry-run**（`--apply` 才寫）；**LLM 幻覺 id** 一律對照 active id set 擋掉；parser 容忍 reasoning model 的前言（從第一個 `[` 做 bracket-depth 配對）。每天台北 04:45 由 cron 跑（sleep-time compute）。

### D-C：bi-temporal 軟失效，絕不硬刪

`user_memories` additive 遷移加三欄（`_ensure_schema`）：`superseded_by` / `invalidated_at` / `last_reflected_at`。

- **active = `superseded_by IS NULL AND invalidated_at IS NULL`**。`search` / `list_subjects*` / `format_as_context` 一律 active-only —— 退役記憶不再被注入、也不再被 extractor 拿去 merge。
- merge / supersede / drop **都走軟失效**（`agent_memory.supersede`），row 留著、`superseded_by` 記來源 → 壞 pass 可回溯、每筆變更留痕。
- **復活語意**：使用者再次講到某 subject → `add()` upsert 時清除退役旗標（再次主張 = 復活）。
- **Phase-1 已知限制（誠實記錄）**：`UNIQUE(agent, user_id, subject)` 保留，所以**同一 subject 的事實變更仍是就地覆蓋**，無完整 row 級歷史；bi-temporal 只在 merge（N→1）、跨-subject supersede、drop 三種情況留歷史。完整 row 級歷史 = Phase 2 拔 UNIQUE。

### D-D：DIY-on-SQLite，先不上框架

維持 SQLite + 自建迴圈，不接 Mem0 / Letta / Zep。理由：單一使用者、要 git/SQL 透明可稽核、已有 cron + extractor + state.db 基建。**只有當「跨數十專案的多跳關聯檢索」成為瓶頸時**，才評估把 Zep/Graphiti 當檢索層掛上（代價：DB 後端、透明度、抽取成本）。廠商 SOTA benchmark（Mem0 vs Zep）是行銷戰，本研究無法驗證其自報數字，不作採用依據。

### D-E：分階段路線圖

- **Phase 1（本 ADR，已完成）**：反思整合 pass + bi-temporal + active-only 檢索 + nightly cron。
- **Phase 2**：① **episodic 層**（帶時間戳的「今天觀察到修修…」，與穩定 semantic 事實分層，呼應 2502.06975）；② **跨 agent 共享**（Nami 學到的 Zoro/Sanji 也看得到 —— 現在三 agent 各自為政）；③ **relevance-aware 檢索**（把當前訊息餵進排序）+ 視需要 **FTS5 語意檢索**（借 `shared/doc_index` 既有 FTS5 基建）；④ 拔 `UNIQUE(subject)` 換完整 bi-temporal row 歷史。
- **Phase 3**：**FAMA 式評測 harness** —— 量化證明記憶真的變準（remembering + 不引用過時），而非自我感覺良好。先有評測，再談更激進的改動或框架。

## 後果

**正面**：記憶會收斂（不再無限堆重複）；矛盾與過時可退役且可回溯；穩定事實會上浮；全程 SQL 可稽核、可回滾；零新基建。

**風險與緩解**：
- *過度整合 / 誤合併* → 保守 prompt + 軟失效可回溯 + dry-run 先看；Phase 3 評測把關。
- *nightly LLM 成本* → 單一使用者、一次 Sonnet call/agent，可忽略。
- *prompt drift（reasoning model 不照格式）* → parser 容忍前言 + prompt 硬化（#928，實機 dry-run 抓到並修）。
- *cross-agent 尚未共享* → Phase 2；現階段每 agent 獨立記憶可接受。

## 落地狀態

- **Phase 1 已 merge + 部署**：[#927](https://github.com/shosho-chang/nakama/pull/927)（反思 pass + bi-temporal + cron）、[#928](https://github.com/shosho-chang/nakama/pull/928)（parser 硬化，實機 dry-run 抓到）。
- **實機驗證**：對 `nami/U05F841H127` 真實 29 筆記憶跑 `--apply` → **29 → 22 active**（2 merges、4 drops；含自動退役我們在 #925 已修的「Calendar API 搜尋範圍」過時記憶）。退役筆軟失效可回溯。
- **排程**：VPS crontab 已裝 `45 4 * * * ... -m shared.memory_reflection --all --apply`。
