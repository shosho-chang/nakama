# ADR-021: Annotation Substance Store + Brook Synthesize Workflow

**Status:** Proposed (v2, panel-revised)
**Date:** 2026-05-07
**Deciders:** shosho-chang
**Related:**
- Supersedes ADR-017 v2 schema in part — folds prose substance back into single canonical store using W3C Web Annotation Data Model shape
- **Depends on ADR-022 (multilingual embedding migration)** — Brook 廣搜 cross-lingual recall 是這個 ADR 可運作的前提
- CONTENT-PIPELINE.md 觀察 #3（annotation owner gap），`agents/robin/CONTEXT.md`
- Codex audit `docs/research/2026-05-07-codex-adr-021-audit.md`
- Gemini audit `docs/research/2026-05-07-gemini-adr-021-audit.md`
- Decision table `docs/research/2026-05-07-adr-021-decision-table.md`

---

## Revision history

- **v1 (2026-05-07 morning)** — grill 出來的雙檔（File 1 JSON + File 2 prose）+ Brook two-phase HITL + 寫進 Project page。Panel review（Codex + Gemini）一致給「不要 ship as-is」。
- **v2 (2026-05-07 afternoon)** — 整合 panel 4 個 strategic fork（A/B/C/D）的判斷：A=R 單檔 W3C 風結構化 / B=U sidecar server-side / C=W unified synthesize / D 拆 ADR-022 前置；fold Codex 9 條 tactical amendments。

---

## Context

修修要把閱讀軌跡（highlight + annotation 短 note + chapter 級 reflection）做成可被 Brook compose / Robin retrieve 撈到的素材。ADR-017 v2 已 ship 存放層（`KB/Annotations/{slug}.md` JSON），但 CONTENT-PIPELINE.md 觀察 #3 明寫「annotation 寫到哪、誰讀、什麼時候用 — 完全沒設計」，下游所有 retrieve / Brook compose / KB concept extraction 摸不到。

修修描述 Line 3 文獻科普 Stage 4 atomic content workflow（5 step：開 Project → Zoro keyword → Brook 廣搜 + outline → 改架構 → 寫稿）後，暴露三層架構缺口：

1. ADR-017 v2 既有 schema 對 retrieve 不友善（reflection body 卡在 JSON、indexer 不讀 JSON）
2. Brook 的「KB 廣搜 + outline 生成」（`kb-synthesize-article` skill）從沒設計
3. 寫稿時的 retrieve 入口未定

修修 stated 兩個 design constraint：

- **Vault 簡潔性是 first-class concern** — 新增 vault 物件要過比過去更高的門檻
- **Obsidian access pattern**：只 access 時間軸頁面 + Project 頁面，LLM-maintained 內容期望走 Web UI

ADR-021 v1 提的雙檔分裂 + Brook 寫進 Project page + sequential HITL，跑 panel review 後三家審查一致指出該方案會（a）製造 File 1 / File 2 drift（b）長期把 Project 頁面變 agent graveyard（c）sequential HITL 是 flow-breaker、實務上 user 會 rubber-stamp（d）跨語言 retrieval 根本沒設計、Brook 廣搜在 bilingual corpus 上現在就壞。

## Decision

### 1. Annotation 存放：W3C 風結構化單檔（Fork A = R）

**單檔 canonical**：`KB/Annotations/{slug}.md`（既有路徑），不分 File 1 / File 2。

**Schema 升級**（v3，supersedes ADR-017 v2 partial）：每條 item 同時帶 `target`（位置）+ `body`（內容），對齊 W3C Web Annotation Data Model：

```python
class HighlightV3(BaseModel):
    type: Literal["highlight"]
    schema_version: Literal[3] = 3
    # target
    cfi: str | None = None          # books only
    text_excerpt: str               # canonical anchor for both books and papers
    book_version_hash: str | None = None
    # body
    text: str                       # the highlighted content (= text_excerpt for fidelity)
    # meta
    created_at: str

class AnnotationV3(BaseModel):
    type: Literal["annotation"]
    schema_version: Literal[3] = 3
    # target
    cfi: str | None = None
    text_excerpt: str
    ref: str | None = None          # paper backward-compat
    book_version_hash: str | None = None
    # body
    note: str                       # user's note tied to span
    # meta
    created_at: str

class ReflectionV3(BaseModel):     # was CommentV2 — renamed to align with user vocabulary
    type: Literal["reflection"]
    schema_version: Literal[3] = 3
    # target
    chapter_ref: str | None = None  # books primarily
    cfi_anchor: str | None = None   # optional fine anchor
    book_version_hash: str | None = None
    # body
    body: str                       # chapter-level long-form reflection
    # meta
    created_at: str
```

關鍵屬性：
- 一檔一真相，**沒有 derived view 在 vault 內**（取消 ADR-021 v1 的 `KB/Wiki/Syntheses/` 路徑）
- target 跟 body 同 record，indexer / retrieve 直接從 JSON 讀 structured items
- ADR-017 v1/v2 檔保留，靠 `schema_version` discriminated union；新 save 一律走 v3
- v1/v2 → v3 遷移：lazy（下次 Reader save 該檔時自動升級）+ 一次性 migration 腳本（既有 3 個檔走腳本）

**Migration 副效果**：legacy aliases (`Highlight`, `Annotation`, `AnnotationSet` from ADR-017 v1) 保留，code 內 `CommentV2` 改名 `ReflectionV3` 但留 alias。Reader UI（paper template + book reader JS）的 `text` / `note` / `body` 渲染路徑改吃 v3 同名欄位，**前端不需重大改**。

### 2. Indexer 直接讀 structured JSON items（不靠 prose）

`shared/kb_indexer.py` 新增 annotation 路徑 indexer：

- 不掃 `KB/Wiki/Syntheses`（不存在）
- 新增掃 `KB/Annotations/*.md`，**從 JSON code block parse items**，每個 `ReflectionV3.body` + `AnnotationV3.note` + `HighlightV3.text` 變一個 chunk
- chunk metadata 記回原 source slug（從 `AnnotationSet.source_filename` 拿）+ item type + chapter_ref（如有）
- 原 `_KB_SUBDIRS` 擴張只動 `Sources/Concepts/Entities`（不加 Syntheses）

**Fold Codex tactical amendments**：
- `_KB_SUBDIRS` 擴張同時要動 `agents/robin/kb_search.py:122` Haiku scan loop + `_SUBDIR_TO_TYPE` mapping + `shared/kb_indexer.py:143` scan loop（all hardcoded paths in code）
- Indexer 改 recursive `rglob("*.md")`（不只 root level）— 既有 `KB/Wiki/Sources/Books/{book_id}/notes.md` 也要納入
- kb_search wrapper 擴回吐 `chunk_text` / `heading` / `chunk_id` / `rrf_score`（既有 hybrid search 已有，wrapper 不該丟）

**Fold Codex amendment #5**：Brook 廣搜要拿到「哪個 highlight / annotation / reflection 命中」需要 wrapper 暴露 chunk 級 metadata，現有 dict shape 不夠。實作時擴 wrapper 不另開 API。

### 3. Brook synthesize 流程：unified + post-generation review（Fork C = W）

取消 v1 的 sequential HITL gate（α/β）。Brook 一次跑：

```
Step 3 Brook unified synthesize（agent backend）：
  廣搜 → evidence pool（30 條候選）
       + draft outline（5-7 段，每段引用 N 條 evidence）
  → 整套輸出寫進 server-side store（見 #4）
Step 4 修修 Web UI 內 in-context review：
  - 對著 outline 結構看 evidence
  - reject 顆粒度：「這條 evidence 從第 3 段拿掉」「整條 evidence 不要」
  - finalize → Brook 重新 generate outline（廣搜結果 cached，不重撈）
Step 5 修修右螢幕寫稿、左螢幕 viewer
```

**為什麼 unified > sequential**：sequential HITL 把 review 切到沒 outline context 的 evidence list 上 — 真實使用會 rubber-stamp 全 accept，HITL 失效。Unified 給 outline 後 review，每條判斷都有「該段是否需要這條」的 context。Codex 也 push back sequential 沒證據，Gemini 直指「flow-breaker」。

**Brook 廣搜 query 構成**：
- Input：Project topic（繁中 one-sentence）+ Zoro keywords（中英混）
- 不採 ADR-021 v1 的「concat 單一 query」 — Codex/Gemini 都 push back single query 對 bilingual 不夠
- v2 採 **multi-query**：原繁中 query 跑一次、英文 keywords-based query 跑一次、合併 dedupe — 但**這是過渡方案**
- **長期**：靠 ADR-022 multilingual embedding migration 後，single query 在多語 dense space 即足夠 → Brook 改回 single-query（重新評估時點：ADR-022 ship 後）

**K 取代為 evaluation plan**（fold Codex amendment #6）：
- ADR-021 v1 的「K=30」推理錯了（K 控 output 不控 input、Haiku 看全 corpus）
- v2 改：`top_k` 從 8 → **動態**，依 corpus size + engine 決定
- engine 預設走 **hybrid**（BM25 + dense）不走 Haiku — Haiku ranker 在 corpus > 50 條時 prompt token 會爆
- 評估計畫：實作前先跑 mini-bench（K=8/15/30 × Haiku/hybrid × 5 個歷史 Project），看 recall/precision，再 freeze 預設

**Freeze（2026-05-07，#457 mini-bench HITL）**：

| 設定 | 值 |
|---|---|
| `BROOK_SYNTHESIZE_TOP_K` | **15** |
| `BROOK_SYNTHESIZE_ENGINE` | **hybrid** |

bench 結果見 `docs/research/2026-05-07-brook-synthesize-bench.md`（5 topics × 2 engines × 3 K，corpus = BGE-M3 1024d full re-embed × 13 pubmed sources）。

| Engine | K | Recall | Precision |
|---|---|---|---|
| haiku | 8 | 1.00 | 0.87 |
| haiku | 15 | 0.90 | 0.88 |
| haiku | 30 | 0.90 | 0.95 |
| hybrid | 8 | 0.83 | 0.88 |
| **hybrid** | **15** | **1.00** | **0.76** |
| hybrid | 30 | 1.00 | 0.45 |

**Why hybrid + K=15**：full recall + 仍乾淨（precision 0.76）+ chunk-level metadata（heading / chunk_id）對 outline drafter 有用。Haiku K=8 純數字最優但只回 path，做 synthesize 時下游缺結構。K=30 在 hybrid 降到 0.45 precision，雜訊太多。

**Caveat**：corpus 小（13 papers × 5 topics × 2-3 ground truth/topic）— 樣本脆弱。**長期 plan**：corpus 長大 + 實際 synthesize 跑一段時間後（>10 個 Project synthesize），重跑 bench 並依實況調整。fixture 在 `tests/fixtures/brook_bench_topics.yaml`，需隨 corpus 演化補新 topic。

### 4. Brook 輸出存 server-side sidecar，不寫進 vault（Fork B = U）

Brook synthesize 的 evidence pool + draft outline 存 **Thousand Sunny server-side store**（不在 vault），格式：

```
[VPS] data/brook_synthesize/{project_slug}.json
{
  "project_slug": "creatine-cognitive",
  "topic": "...",
  "keywords": [...],
  "evidence_pool": [{"slug": "...", "chunks": [...], "hit_reason": "..."}, ...],
  "outline_draft": [{"section": 1, "heading": "...", "evidence_refs": ["slug-a", "slug-b"]}, ...],
  "user_actions": [{"timestamp": "...", "action": "reject_from_section", "section": 3, "evidence_slug": "..."}],
  "outline_final": [...],
  "schema_version": 1,
  "updated_at": "..."
}
```

**Vault 完全不增物件**：

- Project 頁面在 Obsidian 內保持瘦：frontmatter（topic + Zoro keywords）+ 修修手寫 outline 改寫
- Brook 不寫 evidence section / outline section / `brook_reject` frontmatter 進 Project 頁面
- Web UI route `GET /projects/{slug}` 從 server-side store 拉 evidence + outline 渲染；review/finalize 透過 POST 改 store

**為什麼 server-side > vault sidecar > Project page body**：

- Project page body：5 年後 Brook 加 N 個新功能 → Project 頁面變 agent graveyard，違反「vault 簡潔」+「Obsidian 不看 LLM 內容」
- Vault sidecar (`Projects/{slug}.brook.json`)：違反「不增 vault 檔」preference
- Server-side：Obsidian 完全乾淨；LLM-maintained 物件跟 Web UI 唯一耦合；vault Sync 不負擔 derived 內容

**Reject 機制**：per-Project sticky list 改成 server-side store 內的 `user_actions` array，**降權而非永久 hide**（Gemini push back：永久 hide = 「naughty list」censorship 阻止 serendipitous rediscovery）。下次 re-run 廣搜時，已 reject 過的 slug 排序時降權但不消失。

### 5. 取消 File 2 prose 同步 regenerate（Fork A 副效果）

ADR-021 v1 的「Reader UI save 同步 regenerate ~50-200ms」整段取消 — 沒有 File 2 prose 要產。

Reader UI save 路徑回到既有行為：寫 JSON store、return `unsynced_count`。Book route 既有的 BackgroundTasks `book_digest_writer` pattern 保留，**但 digest 寫入路徑要重審**（既有寫 `KB/Wiki/Sources/Books/{book_id}/digest.md`、跟新 indexer 不衝突；保留為 optional view，不再是 retrieve canonical）。

### 6. 跨語言 retrieval 拆獨立前置：ADR-022

跨語言 retrieval 是 ADR-021 整個前提的隱形底盤 — `kb_search` 沒 CLIR、繁中 query 撈英文 paper recall 近 0、Brook 廣搜在 bilingual corpus 現在就壞。修這個是 embedding model 切換 + re-index 的架構性決定，不該夾在 ADR-021 內。

**ADR-022 範圍**：dense vector lane 從單語 embedding 改 multilingual（候選：`BAAI/bge-m3`、`sentence-transformers/LaBSE`），`data/kb_index.db` 全量重 embed。本機 GPU（5070 Ti 16GB VRAM）跑得起。

**ADR-021 跟 ADR-022 的時序**：
- 實作上 **ADR-022 必須先 ship**（不然 ADR-021 的 Brook 廣搜不能用）
- 但 ADR-021 v2 schema 改動（W3C 風 v3）跟 ADR-022 embedding 切換 decoupled、可平行
- Brook synthesize 的 query 構造現階段走過渡 multi-query，ADR-022 ship 後改回 single-query

## Considered Alternatives（panel forks）

完整討論在 `docs/research/2026-05-07-adr-021-decision-table.md`，這裡只列結論：

| Fork | 選擇 | 拒絕的選項 |
|---|---|---|
| A — Annotation 存放 | (R) W3C 風單檔結構化 | (P) 雙檔分裂 = ADR-021 v1；(Q) 單檔 + prose view |
| B — Brook 輸出 | (U) Server-side sidecar | (S) Project page body + marker；(T) Vault sidecar |
| C — HITL 順序 | (W) Unified Synthesize+Outline | (V) Sequential HITL 廣搜→review→outline |
| D — 跨語言 retrieval | 拆 ADR-022 multilingual embedding | (X) LLM 翻譯 query；(Z) 雙 query 過渡（v2 過渡用） |

## Consequences

### 必做工程任務

1. `shared/schemas/annotations.py`：新增 v3 models（HighlightV3 / AnnotationV3 / ReflectionV3）+ discriminated union 加 v3；保留 v1/v2 alias
2. `shared/annotation_store.py`：寫入路徑預設 v3；讀取支援 v1/v2/v3
3. **遷移腳本**：既有 3 個 v1 檔升 v3（一次性、log 結果）
4. `shared/kb_indexer.py`：
   - 新增 `KB/Annotations/*.md` indexer（parse JSON items 為 chunks）
   - scan loop 改 recursive `rglob`（既有 `KB/Wiki/Sources/Books/` nested 也要 index）
   - `_KB_SUBDIRS` 擴張 + scan loop hardcoded paths 同步改
5. `agents/robin/kb_search.py`：
   - Haiku scan loop hardcoded 改吃 `_KB_SUBDIRS`
   - Hybrid wrapper 擴回吐 `chunk_text` / `heading` / `chunk_id`（不丟 metadata）
   - `_SUBDIR_TO_TYPE` 加 annotation type
   - 預設 engine 改 hybrid（Haiku 在大 corpus 不可行）
6. `agents/brook/synthesize.py`（新檔）：unified Synthesize+Outline，產 evidence pool + outline draft，寫 server-side store
7. **Server-side store**（新元件）：`data/brook_synthesize/{slug}.json` reader/writer + Thousand Sunny route `/api/projects/{slug}/synthesize`（GET/POST）
8. `thousand_sunny/routers/projects.py`（新檔）：`/projects/{slug}` Web UI route，evidence + outline review/writing 兩態 panel
9. `thousand_sunny/templates/`：projects panel UI（review mode reject 按鈕、writing mode read-only）
10. Reader UI save：**取消** v1 規劃的 prose regenerate hook；保留既有 BackgroundTasks digest writer pattern
11. ADR-022 multilingual embedding migration（前置）

### 重要副作用

- **ADR-017 v2 schema 部分 supersede**：v1/v2 paper/book 檔保留可讀，新 save 走 v3
- **既有 `book_digest_writer.py` / `book_notes_writer.py` / `annotation_merger.py`** 不在 retrieve canonical 路徑（fold Codex 觀察）— 保留為 optional view，不再是 indexer source；後續實作評估是否棄用
- **Brook over-load 加劇**（CONTENT-PIPELINE.md 觀察 #2）：synthesize.py 是 Brook 第 6 個子職責，建議先按 sub-context 切目錄 `agents/brook/synthesize/`
- **Web UI Stage 4 surface 從此 hot path**：之前 CONTENT-PIPELINE.md 把 Web UI Stage 4 視為長期目標，本 ADR 把它拉成 Line 3 MVP 的一部分
- **Vault 真正乾淨**：KB/Annotations 一檔 / KB/Wiki/Syntheses 不存在 / Project 頁面瘦 / Obsidian 完全不負擔 LLM-maintained 內容（達成修修 stated ideal）

### 不做的事

- 不在 vault 新增任何 derived 檔（沒 File 2 prose、沒 Project sidecar）
- 不做 Line 2 的 Brook synthesize（讀書心得 = 修修手寫，agent 不介入 Stage 4）
- 不做 Robin concept extraction 讀 annotation（保持 ingest 純粹；之後若要當獨立 lazy pass 再評估）
- 不為 vault 整體簡潔重整（root 24+ 項分組）— 獨立議題另開
- 不採用 block-based addressability（Roam/Logseq pattern）— Gemini 提及但工程量大、defer

### 待驗證假設（要在實作前 mini-bench 驗）

- `top_k` 動態值 + hybrid engine 對 Brook 廣搜實際 recall/precision（評估計畫見 #3）
- ADR-022 ship 後跨語言 query 在 bge-m3 / LaBSE 的真實命中率
- Reader UI v3 schema 切換對前端 callout / sidebar 渲染的兼容性
- Server-side store schema v1 對未來 Brook 子功能擴張的可演化性
