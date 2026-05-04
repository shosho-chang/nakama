# ADR-017: Annotation → KB integration（Line 2 critical path）

**Date:** 2026-05-04
**Status:** Accepted
**Extends:** [ADR-011](ADR-011-textbook-ingest-v2.md)（Concept page = cross-source aggregator 哲學延伸到讀者個人觀點）

---

## 1. Context

CONTENT-PIPELINE.md 觀察 #3 指出：雙語 Reader 已支援 `==highlight==` + `> [!annotation]` markup（`thousand_sunny/templates/robin/reader.html`），Robin ingest summarize prompt 已會消費 annotation（`agents/robin/prompts/summarize.md:27-36`），但「annotation 寫到哪、誰讀、何時用」完全沒設計。

Line 2（讀書心得）這週要手跑一次。心得 atomic content 的唯一 input 是 annotation。兩個 gap 是 critical path blocker：

1. **annotation 跟 source 一起死**：Inbox 路徑 ingest 完原檔送回收桶（`thousand_sunny/routers/robin.py:629`），標記跟著消失
2. **跨 source aggregate 不存在**：修修讀 5 本書都對「肌酸代謝」標 annotation，散在 5 檔無法集中

**援引原則**：
- ADR-001 line 38（Brook 擴展選項預留 — 本 ADR 不開新 agent，落 Robin Stage 3 owner）
- ADR-011 §2 P1（Karpathy aggregator）+ P4（conflict detection）
- CONTENT-PIPELINE.md Stage 2 → Stage 3 boundary（修修明確要求 Stage 4 手寫不介入、Stage 3 整合可動）

## 2. Decision

10 題 grill 凍結（grill log 與 PRD 見 GH issue #337）：

### 2.1 詞彙：Highlight vs Annotation 兩個 first-class concept（Q1）

- **Highlight** = `==text==`，純重點，無附加觀點 — source-level signal
- **Annotation** = `> [!annotation]` block，重點 + 修修個人觀點 — KB first-class evidence

兩者進 KB 的職責不同；口語層原本混用，2026-05-04 grill 拍板分開（`agents/robin/CONTEXT.md` glossary 凍結）。

### 2.2 Annotation 進 Concept page 獨立 `## 個人觀點` section（Q2）

不混進學術 evidence body 主體，用獨立 section 標 author=修修 + `from [[source-X]]` backlink。延伸 ADR-011 P1 哲學：個人觀點與學術 evidence 是兩個並行的 cross-source aggregator，各自 merge 不互相覆蓋。

### 2.3 物理落點 = 獨立檔 `KB/Annotations/{source-slug}.md`（Q3 + Q6）

annotation lifecycle 跟 source 檔解耦 — Inbox 送回收桶不影響 annotation。`base=inbox` 與 `base=sources`（PubMed 雙語版）兩條路徑一致處理，原檔保純不再 mutate；Reader 渲染時 fetch 原檔 + AnnotationStore.load 兩份 overlay。

### 2.4 Highlight 不直接寫進 Concept page（Q4，asymmetric with Annotation）

Highlight 物理跟 Annotation 同住 KB/Annotations/{slug}.md（schema 用 `type: hl|ann` discriminator）；但對 Concept page 不寫入 — 其角色為 (a) Robin ingest summary 加權 hint（既有 summarize.md 行為保留）+ (b) Stage 4 心得 derived view 來源（保留結構，這版不做工具）。

### 2.5 Sync trigger = 獨立輕量 endpoint，不自動（Q5 + Q7）

Source 第一次 ingest（`/start` flow）跟 annotation sync **完全分離**，永遠走兩次明確 trigger。修修在 Reader header 按「同步到 KB」按鈕，呼叫 Robin `/sync-annotations/{slug}` endpoint；只跑 KB/Annotations/{slug}.md → Concept page section merge，不重跑 summary + concept plan。

不採自動 sync（黑盒違反修修「要手控」原則）；不採 ADR-006 HITL approval queue（KB 是個人 vault 寫入，不該套對外發布 gate）。

### 2.6 Concept page section layout = flat list 時間順序（Q8）

每條 annotation = 一個 callout block（`> [!annotation] from [[source]] · YYYY-MM-DD` + 段落 + 修修註解）；多 source 共存於同一 `## 個人觀點` section 不切 sub-heading by source（保留 cross-source pattern 對比）。

衝突走 inline，不 promote `## 文獻分歧` section（後者保留給 ADR-011 P4 學術 evidence 衝突，與個人觀點性質不同）。

### 2.7 Re-sync model = full replace per source（Q9）

Concept page 內每個 source 的 annotation 用 HTML comment boundary marker 包：

```
<!-- annotation-from: {source-slug} -->
> [!annotation] ...
<!-- /annotation-from: {source-slug} -->
```

Robin sync 時找到對應 boundary block 整塊 wipe + 重 render；其他 source 的 block 不動。LLM merge prompt 不需算 diff（簡化）；KB/Annotations/{slug}.md 是 source of truth，Concept page section 是 derived view（修修不可手編，編了下次 sync 會被 wipe）。

### 2.8 Sync 狀態可見性 = Reader header badge（Q10）

KB/Annotations/{slug}.md frontmatter 含 `last_synced_at`；每條 mark 含 `modified_at`。Reader header 顯示「N 條未 sync」徽章（count > 0 警示色 / count == 0 success 色），sync 完即時更新。

## 3. Consequences

### 3.1 新增 vault 寫入路徑

`KB/Annotations/` 是新 vault directory（不在 CLAUDE.md 既有 `KB/Raw` / `KB/Wiki` / `KB/index.md` / `KB/log.md` 規則內）。`shared/vault_rules.py` 與 CLAUDE.md vault 寫入規則段需更新加入 KB/Annotations/ 寫入授權（Robin / Reader handler）。

### 3.2 Robin 取得新 endpoint + sub-pipeline

- 新 deep module `shared/annotation_store.py`（schema 版本獨立於 ADR-011 KB schema v2）
- 新 deep module `agents/robin/annotation_sync.py`（ConceptPageAnnotationMerger）
- 新 endpoint `/sync-annotations/{slug}`（Robin namespace）

### 3.3 Reader save 行為改變

`POST /save-annotations` 從寫回原檔改成寫 AnnotationStore；`GET /read` 從只 load 原檔改成 load 原檔 + AnnotationStore.load 兩份；Reader UI overlay 渲染由前端 JS 處理（既有 marked.js + regex pattern 改動較小）。

### 3.4 Reading session 不 first-class

annotation 是 source-anchored set，CRUD in-place，沒「次」邊界。未來若需要 session 概念可加 `session_id` field 不破壞 schema（schema_version=2 migration）。

### 3.5 Stage 4 心得工具留 backlog

LLM 拉「我這次標的 annotation」list 給修修寫心得前參考 — 修修明確要求 Stage 4 手寫過程不介入；先手跑 Line 2 一次再決定。Highlight derived view + KB/Annotations/ 結構為這個 future feature 保留 hooks（不需另改 schema）。

### 3.6 KB retrieval 路徑單一

LLM retrieval（Brook compose / Robin retrieval）拉個人觀點走 Concept page `## 個人觀點`，不直接 grep KB/Annotations/。Sync pipeline 是 retrieval pre-condition — 修修必須按 sync 才能讓 LLM 看到。

## 4. Implementation

vertical slice 拆 3 ticket（PRD #337 → issues #338 / #339 / #340）：

- **Slice 1（#338）** — AnnotationSet schema + AnnotationStore + compute_annotation_slug + Reader endpoints + vault rule + 本 ADR
- **Slice 2（#339）** — ConceptPageAnnotationMerger + Robin `/sync-annotations/{slug}` + Reader sync 按鈕
- **Slice 3（#340）** — Reader header badge UX

Slice 1 先 ship 解 annotation 不消失問題（MVP），Slice 2 解 cross-source aggregate，Slice 3 polish UX。
