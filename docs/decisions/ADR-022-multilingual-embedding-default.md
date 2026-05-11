# ADR-022: Multilingual Embedding 改為全 KB 預設

**Status:** Proposed
**Date:** 2026-05-07
**Deciders:** shosho-chang
**Related:**
- 為 ADR-021 v2 的 Brook synthesize 提供可運作的 cross-lingual retrieval 底盤
- 延伸 ADR-020 S6（已把 textbook ingest 預設改 BGE-M3，但全 KB index 還沒切）
- Gemini panel audit `docs/research/2026-05-07-gemini-adr-021-audit.md`（指出 CLIR 是 ADR-021 隱形底盤）

---

## Context

修修寫繁中、KB 內 80%+ 是英文 paper、書本是 bilingual。`shared/kb_embedder.py` 目前 default backend 是 `potion-base-8M`（256-dim、單語英文），dense retrieval lane 對「繁中 query → 英文 paper」recall 接近 0。

實況檢驗：

- `data/kb_index.db` 內 `kb_vectors` table 目前 schema 是 `embedding float[256]`（potion）— 全 KB 用單語 model embed
- `shared/kb_embedder.py` 已經有 BGE-M3 (1024-dim cross-lingual) 路徑，但只在 `NAKAMA_EMBED_BACKEND=bge-m3` env var 設定時才走（line 33-35）
- ADR-020 S6 的 textbook ingest 已經切 BGE-M3，但**全 KB hybrid search index 沒有跟著切** — 結果是 textbook ingest 用 1024d、查詢時走 256d hybrid index，dim 不對齊根本不能 cross-search

ADR-021 v2 的 Brook synthesize 假設「廣搜能撈到中英 paper」 — 沒這個底盤就是空中樓閣。

## Decision

1. **`kb_embedder.py` 預設 backend 切 `bge-m3`**：line 33-35 改成 `_DEFAULT_BACKEND = "bge-m3"`，反向設 `NAKAMA_EMBED_BACKEND=potion` 才走 legacy。
2. **`data/kb_index.db` 全量重 embed**：drop `kb_vectors` 表，重建為 `vec0(embedding float[1024])`，re-embed 所有 chunk（目前 3069 chunks）。執行平台：本機 5070 Ti（16GB VRAM）跑 BGE-M3 estimate < 5 分鐘。
3. **hybrid search dim assertion**：`shared/kb_hybrid_search.py` 載入 model 後 assert `model.dim == kb_vectors_dim`，dim 不對直接報錯（不靜默走錯）。
4. **舊 `potion` backend 保留可用**：legacy fallback、跑 micro-bench 比較用，不刪。

## Consequences

### 必做工程任務

- `shared/kb_embedder.py:33-35` flip default
- `shared/kb_indexer.py` re-index 路徑：drop + recreate `kb_vectors`，全量 re-embed；建議寫成 `python -m shared.kb_indexer --rebuild` CLI subcommand
- `shared/kb_hybrid_search.py` 啟動時 dim assertion
- 新增 `tests/shared/test_kb_hybrid_search_multilingual.py` — 用 5 個繁中 query × 5 個英文 paper 驗 cross-lingual recall
- 文件：`docs/agents/domain.md` 標 BGE-M3 為 retrieval 預設

### 跟 ADR-020 / ADR-021 的時序

- **必須先 ship**：ADR-020 textbook ingest 已有 BGE-M3 path，先讓全 KB 也走同 model dim 才能 cross-search
- **ADR-021 v2 解鎖前提**：Brook synthesize 廣搜在 cross-lingual 不可用 → ADR-022 ship 後才有意義
- ADR-021 v2 的「過渡 multi-query」（繁中 + 英文 keywords-based 兩 query 合併）在 ADR-022 ship 後可改回 single-query

### 風險 + 待驗

- BGE-M3 對繁中 vs 簡中的 nuance — 修修內容繁中為主，要驗 model 對繁中 query 的命中質量
- 1024-dim vs 256-dim → kb_index.db 大小 4× 膨脹（目前 ~MB 級，無問題）
- 本機 GPU re-embed 全 KB 是一次性，未來 incremental indexing 應 mtime-based（已 supported in `kb_indexer.py:67-71`，無重大改動）
- BGE-M3 query 模式是否需要 instruction prefix（model 預設不需，但要在 hybrid_search 載入時驗）

### 不做的事

- 不換到其他多語 model（LaBSE / multilingual-e5）— BGE-M3 已 ship、團隊熟、cross-lingual 質量足夠
- 不做 ANN index 升級（FAISS / Pinecone）— 目前 `sqlite-vec` 對 3K-30K chunks 規模夠用
- 不做 query reformulation / HyDE — 留 ADR-021 v2 Brook synthesize 評估時點
