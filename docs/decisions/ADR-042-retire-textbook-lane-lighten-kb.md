# ADR-042 退役教科書 ingest lane + KB 輕量化（移除向量搜尋）

- 狀態：Accepted
- 日期：2026-06-05
- 決策者：修修
- 關聯：取代 ADR-020（textbook-ingest v3）；撤銷 ADR-022（multilingual-embedding-default）向量預設；ADR-010（textbook-ingest，原本就否決向量，本 ADR 與其立場一致）

## 脈絡

KB 長出兩種互斥哲學：**教科書 ingest**（參考查詢、需完整 concept 庫）vs **卡片盒**（書/文章/YouTube，寫作素材）。
教科書那條的肥大語料（3000+ chunks，多為運動科學教科書）是 ADR-022 把搜尋升級成 BGE-M3 1024-dim 向量 + sqlite-vec 的**唯一理由**。
修修決定：現在不需要教科書 ingest（日後可能再用），也不需要向量搜尋，要回到輕量 KB。

## 決策

1. **教科書 vault 資料** → 冷備份後移出 live vault（不刪、可還原/重建）。
2. **教科書 lane 程式碼** → 打 git tag 還原點後，從 active tree 移除。
3. **搜尋引擎** → 降級為 FTS5 / BM25 關鍵字（移除 vec0 + embedding + sqlite-vec + reranker，保留 FTS5 全文 lane 與 chunk 級欄位）。

## 決策依據（已驗證 file:line / 事實）

- `search_kb` 預設 `engine="hybrid"`（`agents/robin/kb_search.py:147`）；docstring 言明 hybrid 是因 Brook 廣搜語料 >50 chunks 時 Haiku ranker 撞 prompt 牆。
- **選 FTS5 而非 Haiku ranker**：FTS5 lane 保留 chunk 級欄位（`chunk_text`/`heading`/`chunk_id`），Brook synthesize 證據卡需要；Haiku 只回 page 級欄位會退化。
- ADR-010:273 原本就否決向量（教科書少量靜態內容用向量是 over-engineering）。
- **query_expander / reranker = 死碼**：全 repo 僅自身 + 測試 import，無 production 消費者 → 可直接刪。
- **Concepts/ 教科書 vs 文章可靠判別**：教科書指紋 = `created_by: phase-2-concept-dispatcher` + `maturity_level` + `en_source_terms` + `mentioned_in:[[Sources/Books/...]]`；文章 concept（ADR-011 v2）無這些。

## 哪些移除 vs 保留

### 移除：教科書 lane 程式碼
`.claude/skills/textbook-ingest/`、`shared/concept_classifier.py`（唯一非測試 importer = run_s8_preflight）、`shared/concept_validators.py`、`scripts/run_s8_preflight.py`、`scripts/run_s8_batch.py`、`scripts/cleanup_c5_stubs.py`、`scripts/cleanup_broken_definition_seeds.py`、`scripts/verify_verbatim.py`、`scripts/verify_staging.py` + 對應測試（`test_concept_classifier`、`test_concept_validators`、`test_run_s8_*`、`test_golden_chapter`、`test_run_phase1_source_page`）。

### 移除 / 降級：向量 lane（ADR-022）
`shared/kb_embedder.py`、`shared/reranker.py`、`shared/query_expander.py`、`scripts/eval_bse_kb_search.py`、`scripts/bench_kb_search.py`；`requirements.txt` / `pyproject.toml` 的 `sqlite-vec`；新 migration drop `kb_vectors`。
`shared/kb_hybrid_search.py` → 改 BM25-only（保留 `get_kb_conn`、`kb_chunks` FTS5，移除 dense lane + RRF）。
`shared/kb_indexer.py` → 停寫 vec0、停呼叫 embedder（保留 FTS5 `kb_chunks` + `kb_wikilinks`）。

### ⚠️ 必須保留（共用，動到會壞 route C/E + Brook）
`shared/kb_writer.py`（尤其 `upsert_concept_page` — route C 文章 ingest `agents/robin/ingest.py:505` + `concept_dispatch.py` 在用）、`shared/concept_dispatch.py`、`agents/robin/kb_search.py`（`search_kb` 降級但簽名/回傳欄位不變）、`kb_indexer` FTS5 lane、`kb_chunks`/`kb_wikilinks`/`kb_index_meta` 三表。

## 執行狀態與順序

- **Phase 0 還原後路** ✅ 已完成（2026-06-05）
  - git tag `archive/textbook-ingest-pre-removal`（待打，程式碼還原點）
  - vault 資料冷備份 ✅ `E:\LifeOS-archive\textbook-ingest-2026-06-05`（2927 檔 / 799MB，檔數+抽樣 hash 驗證通過）
- **Vault 資料清理** ✅ 已完成（2026-06-05）
  - 移出：2762 concept 頁、5 本教科書 source、69 coverage.json、_alias_map.md、6 Raw 原文、8 EPUB
  - 回收：AgentOutputs、測試殘留檔/manifest、`財富階梯.md` 的 `"test"` 畫線、.DS_Store
  - `KB/index.md` 已更新；KB/Wiki 3324→481 md、92.5→60.9MB
- **Phase 1 搜尋降級 FTS5 + Phase 2 移除向量 lane** ✅ 已完成（2026-06-05，合併執行）
  - 註：degrade `search()` 會立刻讓 vec/embedder 測試變紅 → Phase 1/2 在「保持測試綠」前提下不可分，故合併為一個連貫單元。
  - `kb_hybrid_search.py` → BM25 + wikilink RRF（drop sqlite_vec/kb_embedder import、vec0 schema、dim 斷言函式；`search()`/`SearchHit` 形狀不變，legacy `"vec"` lane name 接受但 inert）
  - `kb_indexer.py` → 停寫 vec0/embedder；`rebuild_index` drop 舊 `kb_vectors` vtab 不再重建
  - consumers 更新：`closed_pool` lanes→`("bm25",)`、Brook `_ENGINE_LANES["hybrid"]→("bm25",)`、`search_kb` 預設 `hybrid`=BM25
  - 刪除死碼：`kb_embedder.py`/`reranker.py`/`query_expander.py`/`eval_bse_kb_search.py`/`bench_kb_search.py` + 4 個向量測試檔；`requirements.txt`/`pyproject.toml` 移除 `sqlite-vec`/`model2vec`/`FlagEmbedding`
  - **gate 全綠**：287 個 KB/Brook/robin 測試通過；ruff clean；阻擋 `sqlite_vec` import 後 `thousand_sunny.app` 仍能 boot；`gather_evidence` 證據卡含 `chunk_text`/`rrf_score`/`heading`/`chunk_id`
- **Phase 3 移除教科書 lane 程式碼**（待做）— 確認 `upsert_concept_page` / route C 測試綠
- **收尾**：`python -m shared.kb_indexer --rebuild` 重建純 FTS 索引（清掉已刪教科書 chunk 的死連結）

## 風險與緩解
- **R1 Brook 搜尋退化**：FTS5 無語意。緩解：語料縮小（2762→~480 頁）後 BM25 命中率回升；Phase 1 gate 實測 Brook 證據卡。
- **R2 stale index 死連結**：教科書頁已刪但索引未重建 → 搜尋回死連結。緩解：收尾 rebuild（綁 Phase 1/2 後做一次）。
- **R3 還原成本**：見下還原指南。

## 還原指南（日後復用教科書 lane）
1. `git checkout archive/textbook-ingest-pre-removal -- <textbook + vector paths>`（或 revert 對應 PR）
2. 還原 `E:\LifeOS-archive\textbook-ingest-2026-06-05` 的 vault 資料回 `KB/`
3. `pip install sqlite-vec`、重建 `kb_vectors`、`python -m shared.kb_indexer --rebuild`
4. `search_kb` 預設切回 hybrid

## 附帶決策：PubMed digest 簡化（route D）

修修流程：只看 Bridge 當日 digest 列表，值得研究的點外部 PubMed 連結用 News Coo 抓回 → **不需要每篇獨立 Source 頁 + 全文快取**。

- 已驗證：Bridge 當日 digest 只讀 `KB/Wiki/Digests/PubMed/{date}.md`（`thousand_sunny/CONTEXT.md`），digest 自帶內容；`bridge_digests.py:78` 還把 `[[pubmed-xxx]]` 自動改寫成外連 → 刪 Source 頁不影響 Bridge。
- 程式碼改動（`agents/robin/pubmed_digest.py`）：
  - `run()` 不再呼叫 `_fetch_fulltext_for_all` + `_write_source_page`（只寫每日 digest）
  - `_render_digest_entry` 移除「全文」行與本機 `[[pubmed-xxx]]` wikilink，只留 `[PubMed](外連)`
  - 驗證：14 pubmed + 88 digest/bridge 測試綠、ruff clean
- 資料清理：375 個 `Sources/pubmed-*.md` + KB/Attachments PMID 全文快取已冷備份（`E:\LifeOS-archive\pubmed-source-pages-2026-06-05`）→ 待回收（round-3 腳本）。KB/Attachments 的文章圖（route C，kebab 命名）保留。

## VAULT-LAYOUT 影響
教科書相關路徑（`KB/Wiki/Sources/Books/{textbook}/`、`KB/Wiki/Concepts/`、`_alias_map.md`）改為 dormant；需同步更新 `docs/VAULT-LAYOUT.md`。
