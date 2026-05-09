# digest.md — Cross-Session Findings 給另一個視窗

**Date:** 2026-05-09
**Source session:** 2026-05-08 monolingual-zh source path grill + ADR-024 panel review (Codex + Gemini)
**Audience:** 另一個視窗在處理「教科書 Digest」大問題的 Claude
**Worktree this was written in:** `E:/nakama-qa-adr021` (branch `qa/N461-adr-021-e2e-v2`)

---

## TL;DR

這場 grill 沒新建 digest 機制 — 既有 `book_digest_writer.py`（PRD #430 / Slice 2 / PR #432 ship 過）保留不動。但**有 6 個跟 digest 相關的細節是另一個視窗該注意的**：

1. ADR-021 §5 對 digest.md 的「重審」 unfinished item
2. Codex audit 抓到 annotation_merger 目前**不傳 frontmatter aliases** — 影響 reverse-surface wikilinks 跨語言
3. monolingual-zh book 的 digest.md 退化模式（reverse wikilinks 空 + cross-lingual hits 看 ADR-022 production status）
4. Phase 1 切法（修修 2026-05-08 拍板）對 digest 機制的副作用
5. ADR-024 已 superseded — 不會擴 aliases context
6. ADR-022 production rebuild 沒 verify — digest 跨語言 search_kb 不可用

---

## 1. 既有 digest.md 三檔模式（context recap）

修修在 grill 時對「Books 端的 annotation 儲存」誤以為是單檔（`KB/Annotations`）。實際是**三檔**：

| 路徑 | 角色 | 觸發 | Schema |
|---|---|---|---|
| `KB/Annotations/{book_id}.md` | Canonical 主檔，JSON 三型 union | Reader save | v3 (ADR-021) |
| `KB/Wiki/Sources/Books/{book_id}/digest.md` | 整合檔給人讀 | annotation save → BG task `_write_digest_in_background` | v1 (book_digest) |
| `KB/Wiki/Sources/Books/{book_id}/notes.md` | 章級反思檔（只放 reflection） | annotation_merger sync 觸發 | v1 (book_notes) |

**重要：indexer 從主檔撈 chunks，digest.md 是 derived view**（ADR-021 §5）。修改 digest.md 不影響 retrieval；indexer 只掃 `KB/Annotations/`。

---

## 2. ADR-021 §5 unfinished review item

ADR-021 §5 原文：

> Reader UI save 路徑回到既有行為：寫 JSON store、return unsynced_count。Book route 既有的 BackgroundTasks `book_digest_writer` pattern 保留，**但 digest 寫入路徑要重審**（既有寫 `KB/Wiki/Sources/Books/{book_id}/digest.md`、跟新 indexer 不衝突；保留為 optional view，不再是 retrieve canonical）。

「重審」沒實際發生。Current code (`thousand_sunny/routers/books.py:340-375`) 仍走 BG task 觸發 `_write_digest_in_background`。

**不是 bug**，但如果你要動 digest 邏輯，這個「unfinished review」要看是不是該收掉。

---

## 3. Codex audit Section 1 — annotation_merger 不傳 aliases

Panel review (2026-05-08) Codex audit 抓到的 finding，影響 digest.md 的 `_surface_wikilinks` 路徑：

**Current behavior** (`agents/robin/annotation_merger.py:227-233, 321-323, 390-397`)：
- LLM-match prompt 只收 `concept_slugs = ", ".join(concept_slugs)` 字串
- `_list_concept_slugs` 只回 `p.stem` (filename without extension)
- **不傳 frontmatter aliases、definition、source count 等 candidate metadata**

**對 digest.md 的影響**：
- `book_digest_writer._surface_wikilinks` (line 92-108) 找 Concept page 內的 `<!-- annotation-from: {book_id} -->` marker
- 這個 marker 是 annotation_merger v2 sync 時寫進 Concept page section 的
- 如果 annotation_merger 因為 cross-lingual mismatch 沒能 sync 進去（中文 highlight match 不到英文 Concept），marker 不存在 → digest.md 顯示 `_none yet — run KB sync first_` 在 wikilinks 那行

→ 跨語言場景 digest.md 退化（見 §4），**不是 bug 是 by-design**，但 user 不會知道。

---

## 4. Phase 1 monolingual-zh 切法（修修 2026-05-08 PM 拍板）對 digest 的副作用

修修拍板 Phase 1 = **monolingual-zh reader + annotation pilot, 不做 ingest 也不做 cross-lingual annotation_merger sync**。具體：

- 上傳純中文 EPUB / 純中文 article → 標 H/A/R → 存進 `KB/Annotations/{slug}.md`
- BG task 仍會跑 `write_digest()` 產 digest.md（因為 wired 在 `books.py:340-375` 的 `post_annotations` endpoint 邊界）
- **不做** annotation_merger sync 中文 highlight → Concept page section
- **不做** textbook-ingest 跑中文書

**對 digest.md 的具體退化**：

| digest.md 元素 | 中文書情境表現 |
|---|---|
| 章節分組 H2 | ✅ 正常 — chapter_ref derive from CFI 不分語言 |
| H/A/C body 渲染 | ✅ 正常 — verbatim 中文內容 |
| 🔗 wikilinks (reverse-surface) | ❌ 永遠空 `_none yet — run KB sync first_` — 因為 sync 不跑 |
| 📚 KB 相關 hits (`search_kb`) | ⚠️ 看 ADR-022 production status（見 §6） |
| 📖 Reader deep link | ✅ 正常 |
| 👍/👎 feedback persistence | ✅ 正常 — 不依賴語言 |

**user-visible effect**：修修標完一本中文書 → 開 digest.md → 看到自己 H/A/C 整齊排列 + Reader deep link 都 work，但 wikilinks 那行永遠空、KB hits 可能 0。

---

## 5. ADR-024 superseded — annotation_merger 不擴 aliases context

我這場 grill 寫的 ADR-024 提了「annotation_merger prompt 微調 + 帶 aliases 進 candidate context」（會修上面 §3 的 limitation）。

Panel verdict: **REJECT** + 修修拍板 Phase 1 不做 = ADR-024 標 superseded。

**所以另一個視窗如果在 #430 PRD scope 內想擴 annotation_merger 的 candidate context，這條 unblock**（沒被 ADR-024 預定）。但要做的話要對齊 panel review 的 push-back：

- candidate object 該包 slug + display title + aliases + definition + source count（Codex audit Section 4）
- 不只是 prompt 微調，是新 wiring
- 跨語言 LLM-match 需要 acceptance test (sample 5 條 highlight 測 precision/recall)

---

## 6. ADR-022 production rebuild 沒 verify — 影響 digest search_kb 跨語言

ADR-022 (`docs/decisions/ADR-022-multilingual-embedding-default.md`) 切 BGE-M3 multilingual 為 default backend。Code 已 ship (`shared/kb_embedder.py:33-35`)。

**但 production index 還沒 verify**（Codex audit Section 4 抓到）：
- `data/kb_index.db` 內 `kb_vectors` table dim 是 256d (potion) 還是 1024d (bge-m3)? **未 verify**
- 如果 production 沒 rebuild → 中文 query 進 search_kb dense lane 失敗
- 影響 digest.md 每筆 H/A/C 的 KB hits 撈取

**對另一個視窗的提示**：如果 #430 PRD 後續 slice 要動 digest.md retrieval 品質，先 verify ADR-022 production state（runtime dim assertion + rebuild log），不然測試會 false-negative。

---

## 7. Token budget for grounding pool（如果擴 ingest 路徑要小心）

Codex audit Section 3 量化：

> existing Robin grounding blob in `agents/robin/ingest.py:60-80` includes body excerpts up to 800 chars per page. At 100 Concepts that adds ~80,000 chars; at 1,000 Concepts it reaches ~800,000 chars.

對 digest.md 沒直接影響（write_digest 走 search_kb hybrid retrieval，不走 grounding pool）。但**如果你的視窗要擴 textbook-ingest 加新 prompt 帶 grounding** — 注意 pool shape：

- ✅ Label-only grounding: 100 Concepts × 20 chars name + 4 aliases × 12 chars = ~6,800 chars / ~1,700-6,800 tokens — Sonnet 200k handles easily
- ❌ Body-excerpt grounding (per existing pattern): 100 Concepts × 800 chars = 80,000 chars / 20-80k tokens — scales poorly past 500 Concepts

→ 在 prompt 裡明確 spec pool shape，避免 implementation 直接 copy `_build_existing_concepts_blob` 把 body 也帶進來。

---

## 8. 5/5 evening Slice 5 wiring bug 已修（context note）

從 5/5 evening QA memory：

> #419 Slice 5 wiring | annotation_merger v2 + book_notes_writer 都沒接到 router；comments 從沒寫進 notes.md；v2 concept path 寫死 `KB/Concepts/` 而真實 path `KB/Wiki/Concepts/` — Layer-2 sync **完全沒在動**

這個 bug fix 影響 digest.md 內 search_kb 撈出來的 wikilinks path — 之前 search 出來會找錯目錄、現在對。**已 ship 在 PR #419，不是另一個視窗該處理的事**，純 context。

---

## 9. 修修對 Phase 2 的 stated context (2026-05-09 confirm)

修修明確收斂語言維度：

- 以後**永遠**只 zh-Hant + en 兩語言（簡體不考慮、日韓不考慮）
- 教科書**永遠英文**（不會有中文教科書）
- 繁中只出現在「修修親自閱讀的文件 / 書」，且只有「雙語對照」+「純繁體中文」兩種載體
- 教科書修修不親自閱讀、不做 annotation

**對另一個視窗的 implication**：
- 如果你的「教科書 Digest 大問題」涉及多語言考量 — 修修明確收斂為單純 EN-source
- 如果涉及「教科書 ingest 出來的 Concept 之後要支援中文搜尋」— 那是 Phase 2 範圍（修修 stated 等讀完 5+ 中文書再 grill）
- 教科書本身**永不**會產 digest.md（沒 annotation source、沒 BG trigger 觸發）

---

## 10. 相關 cross-references

- **本 session ADR draft**: `docs/decisions/ADR-024-cross-lingual-concept-alignment.md`（標 superseded、留 reference）
- **本 session grill summary**: `docs/plans/2026-05-08-monolingual-zh-source-grill.md`
- **本 session panel integration**: `docs/research/2026-05-08-adr-024-panel-integration.md`
- **本 session Codex audit**: `docs/research/2026-05-08-codex-adr-024-audit.md`
- **本 session Gemini audit**: `docs/research/2026-05-08-gemini-adr-024-audit.md`
- **PRD #430**: Line 2 讀書心得 — Book Digest + Hybrid Retrieval Engine（既有 ship 的 digest 機制 spec）
- **PR #432**: Slice 2 ship — book_digest_writer + Reader sync background trigger
- **PR #419**: 5/5 evening Slice 5 wiring fix — v2 concept path 修對
- **ADR-017**: annotation KB integration（v2 schema）
- **ADR-021 §2/§5**: indexer 從主檔撈 + digest.md 重審 unfinished
- **ADR-022**: multilingual embedding default（production rebuild verify pending）

---

## 給另一個視窗的具體 ask

讀完之後三件事可以 cross-check：

1. 你的「教科書 Digest 大問題」topic 是不是 **PRD #430 既有 ship 的 `book_digest_writer.py`**，還是另一個 ingest-pipeline 級的 digest（例 textbook chapter summary）？兩者是不同檔
2. 如果是前者，§3 的 annotation_merger candidate context 擴展是不是你 scope 的一部分
3. ADR-022 production rebuild verify 是不是該開個 follow-up issue（兩個視窗都會卡這條）
