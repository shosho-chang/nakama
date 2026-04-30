---
name: Memory Dream consolidation 待辦（mem0 LLM-judge pattern）
description: 給 shared/memory_maintenance.py 加 `dream` subcommand，移植 mem0 LLM-judge UPDATE/DELETE 演算法解 stale memory supersede 問題；觸發點是 Qwen2.5-Omni vs Qwen3-ASR 混淆 bug
type: project
created: 2026-04-30
confidence: high
---

## 為什麼要做（觸發點）

2026-04-30 對話踩到 stale memory bug：

修修問「另一條路 = 安裝 Qwen」時，我抓到 `project_local_multimodal_audio_models.md`（2026-04-17 寫，標 Qwen2.5-Omni 為「首推本地方案」），開始討論 Qwen2.5-Omni 安裝。實際上 ADR-013（2026-04-30）的 D2 替代路徑是 **Qwen3-ASR-1.7B**（2026-01 阿里官方，Mandarin WER 2x 領先 Whisper V3），記在 `docs/research/2026-04-30-asr-engine-prior-art.md` 但**沒進 MEMORY.md 索引**。

根因三層：
1. **索引斷層**：新 research doc 沒進 MEMORY.md，只 grep 到舊 entry
2. **舊 memory 沒退場**：Qwen2.5-Omni 那筆寫於 04-17 評估「仲裁層」用途，後來 04-30 ASR 引擎評估出 Qwen3-ASR 更好，但**舊 memory 框架沒更新**
3. **行為 gap**：用戶說「另一條路」是 ADR 用語，應先讀 `docs/decisions/ADR-*` 找 alternative paths，不靠 grep model name

**現有 `shared/memory_maintenance.py` 只做 TTL 過期 + confidence-based archive — 沒做語意衝突偵測**。Qwen2.5/Qwen3 不是時間到期問題（還沒過 TTL），是新事實 supersede 舊事實。

## 設計需求

新 subcommand：`python -m shared.memory_maintenance dream`

**核心算法**（移植 mem0 paper arxiv 2504.19413, Apr 2025）：

```
For each new memory M:
  1. Embed(M) → vector
  2. Top-K nearest neighbors via cosine similarity (K=5-10)
  3. LLM judge(M, neighbors) → ADD / UPDATE / DELETE_OLD / NOOP
     - ADD: 全新主題，加入
     - UPDATE: 跟既有衝突，rewrite 既有 entry merge new fact
     - DELETE_OLD: 新 fact supersede 舊 fact，舊 archive
     - NOOP: 重複資訊，跳過
  4. Write audit log (decisions/2026-MM-DD-dream.json)
```

**Layer**：
- File-based（match nakama 現有 `memory/claude/*.md` 架構）
- 不引入外部 DB（沿用 `shared/state.db` SQLite agent_memory，但 Claude memory 自己一套）

**觸發**：
- 手動：`python -m shared.memory_maintenance dream`
- Schedule：cron / sandcastle / GitHub Actions（每 24h 跑一次）
- 不掛 Claude Code Stop hook（`grandamenium/dream-skill` 那個 unofficial pattern）

**驗證**：
- 跑在 commit 前的 staging memory（`memory/claude/*.md` 新增 / 改動）
- 每次 LLM judge 出 UPDATE / DELETE 必留 audit trail（被 supersede 的 entry 移到 `memory/claude/archive/YYYY-MM-DD-{name}.md`）
- 修修可看 audit log 反悔（rollback）

## 為什麼不用現成方案

**dream-skill (`grandamenium/dream-skill`, 40★, MIT)** — 不裝：
1. 個人專案、單一 author、3 月才出
2. 自稱「replicate Anthropic Auto Dream」但 **Anthropic 官方 docs 沒這個 feature**（claudefa.st 商業站宣稱「悄悄出貨」，unverifiable）
3. 偵測 path 是 native Claude Code `~/.claude/projects/*/memory/`，nakama 自訂走 repo 內 `memory/claude/`，不一定 detect 到

**MemPalace** — 已 2026-04-19 評估放棄（CJK 只改一半 + 無 auto-extraction + API churn），同類風險

**Letta sleep-time agents** — 太重，要跑 background agent process，不適合 file-based 簡單 case

**Zep / Graphiti** — 要轉 knowledge graph，跟 nakama markdown-based 架構不對位

**結論**：自己刻最合身。mem0 paper 演算法是公開的（arxiv 2504.19413），複用 mem0 OSS 程式碼也可（Apache 2.0）但對 file-based 要改寫不少，不如直接刻。

## 既有 scaffolding 可用

`shared/memory_maintenance.py` 已有：
- `expire_memories()` — TTL 過期清理
- `archive_old_memories(days, confidence)` — 低信心歸檔
- 走 `shared/state.db` agent_memory table

**dream 是第三條 subcommand**，跟 expire / archive 並列。

## 關鍵設計問題（待 PRD）

1. **Embedding model 選擇**：bge-m3（multilingual + CJK）vs OpenAI text-embedding-3-small vs Voyage AI
2. **LLM judge 模型**：Sonnet 4.6（成本敏感 + 量大）vs Opus 4.7（少量但精確）
3. **Top-K**：5 or 10？（論文 mem0 用 5）
4. **Archive 還是 hard delete**：file-based 走 archive 比較安全（rollback）
5. **Conflict 嚴重度分級**：直接 supersede vs 標 `## 文獻分歧` 共存（healthy science 領域常 case）
6. **dream 跑頻率**：每次新 commit 或定期？

## 學術參考

- **mem0 paper** — arxiv 2504.19413 (Apr 2025) — LLM-judge ADD/UPDATE/DELETE/NOOP
- **Letta Sleep-Time Compute** — arxiv 2504.13171 (Apr 2025) — 5x test-time / 2.5x cost reduction
- **Zep / Graphiti** — arxiv 2501.13956 (Jan 2025) — bi-temporal graph
- **LightMem** — arxiv 2510.18866 (Oct 2025) — Atkinson-Shiffrin sleep-time update
- **MemoryBank** — Ebbinghaus forgetting curve

## 優先級

中等。**先做 Line 1 podcast 訪談 + transcribe 第二輪測試 + Qwen3-ASR 安裝**，dream 排在這之後。觸發點是這次踩到的真實 bug，不做的話下次又踩。

預計工作量：~1-2 工作天（PRD 凍結 + 算法實作 + 測試 + 跑一次驗證）
