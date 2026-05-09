# PRD: monolingual-zh Reader + Annotation Pilot (Phase 1)

**Date:** 2026-05-09
**Status:** Draft for Codex hand-off
**Owner:** shosho-chang
**Worktree note:** spec drafted on `qa/N461-adr-021-e2e-v2` in `E:/nakama-qa-adr021`; implementation should branch from `main` HEAD (`1f9fd72` at draft time)

---

## 0. Context for Codex

This PRD is the **Phase 1 reversible pilot** that came out of:

1. 2026-05-08 grill summary (`docs/plans/2026-05-08-monolingual-zh-source-grill.md`) — 8 design decisions凍結
2. ADR-024 draft (`docs/decisions/ADR-024-cross-lingual-concept-alignment.md`) — **superseded** 2026-05-09
3. Panel review (Claude → Codex GPT-5 → Gemini 2.5 Pro) — both auditors **rejected** ADR-024
4. 修修 (project owner) re-scoped to「reversible pilot, no ingest, no cross-lingual concept alignment」on 2026-05-08

**修修's stated context (固化於 CONTEXT-MAP)**:

- 整個系統永遠只 zh-Hant + en 兩語言（簡體 / 日 / 韓不考慮）
- 教科書永遠英文（不會有中文教科書）；修修不親自閱讀教科書、不做 annotation
- 繁中只出現在「修修親自閱讀的文件 / 書」，且只兩種載體：**bilingual mode**（自有英文檔 + Immersive-Translate 產的雙語檔）+ **monolingual-zh mode**（手上只有單份中文檔，例如台版中譯書 / 中文網路文章 — 此 PRD 範圍）

**Codex 該知道的歷史包袱**：
- ADR-024 列了 3 個 cross-lingual 決定（concept naming + ingest prompt + annotation_merger sync）— **全部 not shipping**。Phase 1 不碰 ingest 也不碰 annotation_merger sync，純 reader + annotation 落 disk
- panel verdict: filename-as-canonical + `is_zh_native: bool` + lazy-build 都被 reject。Phase 1 直接繞開這層問題（不寫 Concept page、不擴 alias map）
- 既有 EPUB Reader 5 slice (PR #379-#383) 已 ship；本 PRD 是 incremental extension 不是重做

---

## 1. Problem Statement

修修手上有兩本台版中譯 EPUB 待讀 + 偶爾要看純中文網路文章（台灣作者部落格、國健署網頁等）。既有 EPUB Reader (`thousand_sunny/routers/books.py`) + Document Reader (`thousand_sunny/routers/robin.py`) 設計鎖死「英文 source + 中譯 target」bilingual mode，monolingual-zh source path 完全沒實作：

| 卡點 | 證據 |
|---|---|
| EPUB upload form `bilingual` field 是 required | `thousand_sunny/routers/books.py:120` `bilingual: UploadFile = File(...)` |
| EPUB ingest gate 強制 `has_original=True` | `thousand_sunny/routers/books.py:241-242` `if not book.has_original: raise HTTPException(400, detail="book has no original EN file to ingest")` — 純中文書無英文原檔，**結構性擋下** |
| `lang_pair` schema 預設 `"en-zh"` 無 monolingual-zh enum value | `shared/schemas/books.py:44` `lang_pair: str` default `"en-zh"` |
| Reader UI 走雙語 layout 無 single-column 形態 | `thousand_sunny/templates/book_reader.html` (foliate-js dual-page paginate) |
| Translator 結構性 EN→中 | `shared/translator.py:30-32` `def load_glossary() -> dict[str, str]` returning `{英文: 台灣中文}`；line 116-119 prompt 寫死「翻譯成台灣繁體中文」 |
| Document Reader 無 mode awareness | `thousand_sunny/routers/robin.py:208-256` `read_source` 純看 frontmatter `bilingual: true` boolean，無 mode field 概念 |

---

## 2. Solution

**單一 PRD ship 兩件事：(A) 純中文 EPUB 上傳 + 閱讀 + 註記、(B) 純中文 article 閱讀 + 註記。Annotation 走既有 v3 schema 落 `KB/Annotations/{slug}.md`，不 sync 到 Concept page、不 ingest 進 KB Concept namespace。既有英文教科書 ingest 路徑一個字不動。**

### A. EPUB monolingual-zh 路徑

修修上傳台版中譯 EPUB → 系統偵測 metadata.lang → 自動判定 mode=monolingual-zh → 顯示 mode badge with override → 存進 `data/books/{book_id}/`（單份檔，無 EN 原檔副本）→ 進 Reader 用 foliate-js 共用 engine 讀單欄純中文（雙語 toggle / 翻譯按鈕條件 hide）→ 標 highlight / annotation / reflection → 存進 `KB/Annotations/{book_id}.md`（v3 schema 已支援三型 union，零改動）→ BG task 仍跑 `book_digest_writer.write_digest()` 產 `KB/Wiki/Sources/Books/{book_id}/digest.md`（既有機制保留，副作用 see §4）。

### B. Document monolingual-zh 路徑

修修透過 Obsidian Web Clipper（canonical path）或 `/scrape-translate` URL ingest（fallback）落純中文 article 進 `Inbox/kb/{slug}.md` → inbox listing 時 lazy detect mode（langdetect on body）→ 寫 `mode:` frontmatter → inbox UI 顯示 mode badge → 進 Reader（既有 markdown viewer）讀 → 翻譯按鈕條件 hide → 標 H/A/R → 存進 `KB/Annotations/{slug}.md`（v3 schema 同上）。

### 既有機制保留 — 自動取得 retrieval 整合

5/7 PR #455 ship 的 annotation indexer（ADR-021 §2 落地）會自動掃 `KB/Annotations/*.md` 把每筆 `Highlight.text` / `Annotation.note` / `Reflection.body` 變 chunk 進 hybrid retrieval index。**意思是：Phase 1 即使不 sync 到 Concept page、不 ingest，修修標的中文 highlight / reflection 仍進 retrieval index**，下游 Brook synthesize / `kb_search` 撈得到中文 chunks（cross-lingual recall 倚賴 ADR-022 BGE-M3 production rebuild verified — 風險 see §10）。

### 既有 KB Concept namespace 0 改動

- `KB/Wiki/Concepts/*.md` 既有 100+ 英文 page 不增 zh aliases、不改 frontmatter
- annotation_merger LLM-match 路徑對中文 highlight **不啟動**（mode=monolingual-zh 時 sync 端點繞過 cross-lingual judgment）
- textbook-ingest skill / `/start` IngestPipeline 對 monolingual-zh source **拒絕**（顯式 mode check）

### 副作用清單（修修 confirmed acceptable）

| 行為 | Phase 1 表現 |
|---|---|
| 中文書 highlight 進 retrieval | ✅ 自動（既有 indexer） |
| 中文 article highlight 進 retrieval | ✅ 自動 |
| `KB/Wiki/Sources/Books/{id}/digest.md` 自動產 | ✅ 既有 BG task wired |
| digest.md 內 reverse-surface wikilinks | ❌ 永遠空（Concept page 無 `<!-- annotation-from: {book_id} -->` marker，因為 sync 不跑） |
| digest.md 內 KB hits | ⚠️ 看 ADR-022 production rebuild 狀況（見 §10 風險） |
| Brook synthesize 撈中文書 evidence | ✅ chunk-level，via indexer |
| Brook synthesize surface「這個概念有來自《XX書》的讀者註記」整合 | ❌ 不會（沒 sync 到 Concept page section） |
| 既有英文教科書 ingest 行為 | ✅ 0 改動 |

---

## 3. User Stories

### EPUB（書）端

1. 作為修修，我上傳一本台版中譯 EPUB（修修手上沒英文原檔）後，希望系統不要因為「沒 EN 原檔」就擋下我，而是當作「monolingual-zh book」處理
2. 作為修修，我上傳 EPUB 時希望系統自動讀 EPUB metadata.lang 偵測 mode（zh / en），不需我每次手動選
3. 作為修修，當 detection 跟我預期不符時，我希望 upload form 上有 radio 讓我手動 override mode
4. 作為修修，當 mode=monolingual-zh 時，upload form 應隱藏「EN 原檔 EPUB」dropzone（不會誤導我以為要再上傳什麼）
5. 作為修修，monolingual-zh 書進 Reader 時希望看到單欄純中文（不要假裝雙語把同份檔 render 兩次）
6. 作為修修，monolingual-zh Reader 上**不顯示**翻譯按鈕跟「譯文 v / 譯文 x」toggle（沒翻譯目標可開關）
7. 作為修修，monolingual-zh 書的 highlight / annotation / reflection 標記行為跟 bilingual 書一致（三型同套機制、popup UI 共用）
8. 作為修修，標完中文 highlight 後我希望它存進 `KB/Annotations/{book_id}.md`（既有 v3 schema 路徑），自動進 retrieval index
9. 作為修修，monolingual-zh 書的 ingest 按鈕應該**完全隱藏**（不是 disable，是不顯示）— 因為 Phase 1 不跑中文書 ingest
10. 作為修修，既有上傳過的英文書 + bilingual 書行為**完全不變**（零回歸）

### Document（文章）端

11. 作為修修，我用 Obsidian Web Clipper 從中文網頁（台灣作者部落格 / 國健署）clip 進 vault 的檔，希望 inbox 列表自動顯示 mode badge（zh / en）
12. 作為修修，我用 `/scrape-translate` URL ingest 進來的中文 URL（fallback path），希望結果同樣標 mode=monolingual-zh
13. 作為修修，monolingual-zh article 進 Reader 時希望單欄純中文（既有 markdown viewer 行為），翻譯按鈕跟譯文 toggle 條件 hide
14. 作為修修，純中文 article 的 highlight / annotation / reflection 行為跟英文 article 一致
15. 作為修修，inbox 列上 mode badge 是視覺輔助，不是過濾器（不該因為 mode 就藏行）

### Edge case + migration

16. 作為修修，我之前 hack 上傳過的書（同一份中文 epub 上傳兩次當 bilingual + original）系統應該偵測（bilingual.sha == original.sha）並自動切 monolingual-zh + null original
17. 作為修修，既有 N 筆 books table backfill 為 mode=bilingual-en-zh（zero risk default — 既有都是 en-zh）
18. 作為修修，detection 對「譯者前言中文 + 內文英文」這種混雜內容失靈時，我用 UI override 修就好，不期待系統自動處理細粒度

---

## 4. Implementation Decisions

### 4.1 模組架構（cross-cutting，不綁 namespace）

新增深模組 (各自可 unit test、無 LLM 呼叫)：
- **`shared/lang_detect.py`** (新) — `detect_lang(text: str) -> Literal["zh-Hant", "en", "unknown"]`。內部走 `langdetect` Python 套件 + frequency heuristic。
- **`shared/source_mode.py`** (新) — Mode parsing/writing helper. `parse_mode_from_frontmatter(fm: dict) -> Mode | None`、`write_mode_to_frontmatter(text: str, mode: Mode) -> str`。
- **`shared/schemas/source_mode.py`** (新) or 擴 `shared/schemas/books.py` — `Mode = Literal["monolingual-zh", "bilingual-en-zh"]`。

擴既有：
- **`shared/schemas/books.py`** — `Book` 加 `mode: Mode` field, `lang_pair: str` 標 deprecated（不刪、保 backward compat 讀取）
- **`thousand_sunny/routers/books.py`** — upload form: `bilingual: UploadFile | None`（變 optional，但加 server-side 驗證至少一檔）+ `mode: str = Form(...)` (radio override)；ingest gate (line 241-242) 改 condition on mode；mode-aware storage logic
- **`thousand_sunny/routers/robin.py`** — `read_source`/`scrape_translate`/`translate` 端 mode awareness；inbox listing lazy detect
- **`agents/robin/inbox_writer.py`** — 寫 placeholder + final 結果時帶 detected mode 進 frontmatter
- **`thousand_sunny/templates/robin/book_upload.html`** — mode toggle UI（auto-detect on file change + override radio）
- **`thousand_sunny/templates/book_reader.html`** + **`thousand_sunny/static/book_reader.js`** — mode 條件 render（隱藏譯文 toggle / 翻譯 button / ingest button）
- **`thousand_sunny/templates/robin/reader.html`** + **`thousand_sunny/static/reader.js`**(if exists) — mode 條件 render
- **`thousand_sunny/templates/robin/index.html`** — inbox row 加 mode badge

不動：
- `KB/Annotations/{slug}.md` v3 schema (already supports H/A/R three-type union)
- `shared/annotation_store.py`
- `shared/kb_indexer.py` (annotation indexer 已 wire)
- `agents/robin/annotation_merger.py` (Phase 1 對 monolingual-zh 不觸發 sync)
- `agents/robin/book_digest_writer.py` (BG task 仍跑、副作用見 §2 表)
- `shared/translator.py` (Phase 1 對 monolingual-zh 不呼叫)
- `agents/robin/ingest.py` IngestPipeline (`/start` 對 monolingual-zh source 直接拒)
- `KB/Wiki/Concepts/*.md` 全部既有 page

### 4.2 Storage convention（EPUB end）

**現況**：`store_book_files(book_id, bilingual, original, cover)` 寫 `data/books/{book_id}/bilingual.epub` + 可選 `original.epub` + `cover.{ext}`。

**Phase 1 變更**：`bilingual` slot semantically misnamed for monolingual-zh — 短期 keep slot name 不動（避免 storage path migration），mental model = `bilingual.epub` 是「Reader 顯示用的檔」（monolingual-zh 模式下是純中文）。`has_original` field 對 monolingual-zh 永遠 false。

**Phase 2 cleanup candidate**：rename `bilingual.epub` → `display.epub`（PR-level refactor，需 migration）。**不在 Phase 1 scope**。

### 4.3 Detection 機制

**EPUB**:
1. Upload time: client-side JS (`thousand_sunny/static/book_upload.js`) 不解析 epub，pure server-side detection
2. Server-side: `extract_metadata(sanitized_bytes)` 已抓 `meta.lang` (`shared/schemas/books.py:28`)
3. Mapping rule:
   - `meta.lang in {"zh", "zh-TW", "zh-Hant", "zh-CN", "zh-Hans"}` → mode=`monolingual-zh`
   - `meta.lang in {"en", "en-US", "en-GB"}` → mode=`bilingual-en-zh`（既有 default，因為英文書配 bilingual 是修修日常）
   - `meta.lang` 缺值 / 空 → fallback `shared.lang_detect.detect_lang()` on first chapter sample (~5K chars)
   - 不確定（detect_lang 回 unknown） → default `bilingual-en-zh`（保留功能）
4. UI override: radio `<input type="radio" name="mode" value="...">` rendered above dropzone, defaults to detected mode badge

**Document**:
1. Upload time (URL ingest via `/scrape-translate`): `inbox_writer.write_placeholder` 寫進去時暫不 detect（body 還沒到）；BG task 完成 fetch 後 detect on body + write `mode:` frontmatter
2. Web Clipper 落地時不經 server，只有 `Inbox/kb/{slug}.md` 跑出來，frontmatter 只有 `source` URL + tags=clippings；**lazy detect at inbox listing time** — `/robin/` 開啟掃 dir 時對缺 `mode:` 的檔跑 `detect_lang()` on body + 寫回 frontmatter
3. UI override: inbox row 旁有「換 mode」icon button → POST `/robin/source-mode/{filename}` body `{mode: "monolingual-zh"}` → server 改 frontmatter

### 4.4 Ingest gate behaviour change

**`thousand_sunny/routers/books.py:236-243`** `post_ingest_request`:

```python
# Before:
if not book.has_original:
    raise HTTPException(400, detail="book has no original EN file to ingest")

# After:
if book.mode == "monolingual-zh":
    raise HTTPException(400, detail="monolingual-zh book ingest not supported in Phase 1 (see PRD 2026-05-09)")
if book.mode == "bilingual-en-zh" and not book.has_original:
    raise HTTPException(400, detail="bilingual book needs EN original for ingest")
```

**`thousand_sunny/routers/robin.py:939-978`** `start` (Document IngestPipeline trigger):

```python
# After:
fm, _ = extract_frontmatter(read_text(file_path))
if fm.get("mode") == "monolingual-zh":
    raise HTTPException(400, detail="monolingual-zh source ingest not supported in Phase 1")
```

UI 對 monolingual-zh row 直接 hide ingest button (不只 disable)。

### 4.5 Slice 拆分（vertical slices for sandcastle dispatch）

| Slice | Sandcastle vs Manual | Scope | Files |
|---|---|---|---|
| **S1 schema + lang_detect helper** | sandcastle (deep modules) | `Mode` enum、`detect_lang()`、`source_mode` helper、`Book.mode` field、既有 books table backfill migration | `shared/schemas/books.py`, `shared/lang_detect.py` (new), `shared/source_mode.py` (new), `migrations/NNNN_book_mode_field.sql`, tests |
| **S2 EPUB upload mode-aware** | sandcastle | upload form `bilingual` 變 optional + mode form param + storage logic + has_original mode condition + same-sha detect 自動切 monolingual-zh | `thousand_sunny/routers/books.py:118-197`, tests |
| **S3 EPUB upload UI mode toggle** | manual worktree (UI) | `book_upload.html` mode radio + auto-detect from EPUB metadata.lang + override + monolingual-zh hide「EN 原檔」slot | `thousand_sunny/templates/robin/book_upload.html`, `thousand_sunny/static/book_upload.js`, browser smoke |
| **S4 EPUB Reader mode-aware** | manual worktree (UI) | `book_reader.html` mode 條件 render：monolingual-zh 隱藏譯文 toggle / 翻譯 button / ingest button；mode badge 顯示 | `thousand_sunny/templates/book_reader.html`, `thousand_sunny/static/book_reader.js`, browser smoke |
| **S5 Document mode detection at inbox** | sandcastle | `inbox_writer` 寫 `mode:` frontmatter；`/robin/` index lazy detect on missing `mode:` field；`/robin/source-mode/{filename}` POST endpoint for override | `agents/robin/inbox_writer.py`, `thousand_sunny/routers/robin.py:200-256, 200-205`, tests |
| **S6 Document Reader + inbox UI mode-aware** | manual worktree (UI) | `index.html` mode badge per row + override icon；`reader.html` mode 條件 hide 翻譯 button / 譯文 toggle | `thousand_sunny/templates/robin/index.html`, `thousand_sunny/templates/robin/reader.html`, browser smoke |
| **S7 Ingest gate change + acceptance** | sandcastle | books.py:241-242 gate 改 mode condition；robin.py:939 start 加 monolingual-zh reject；acceptance: 上傳 1 中文 EPUB + 標 5 highlights + verify `KB/Annotations/{id}.md` v3 + `digest.md` 產出 + Brook synthesize 撈到中文 chunk | code change + manual smoke + 1 e2e |

**Slice 依賴**:
- S1 blocks S2 (mode field schema)
- S2 blocks S3, S4, S7
- S5 blocks S6
- S7 depends on S2, S5

**並行可能**:
- S3 + S4 + S5 並行（不同 files）
- S6 + S7 並行（不同 routes）

### 4.6 技術選型

- **langdetect**: 走 [langdetect-py](https://pypi.org/project/langdetect/)（pure Python、PyPI、~1MB、no model download）。**理由**：lingua 太重（~80MB）；pycld3 需 protobuf 編譯（Windows 痛）；langdetect 對 zh / en distinction 準度足夠（>95%），剩下走 user override
- **frontmatter library**: 既有 `python-frontmatter`（`agents/robin/inbox_writer.py` already used）
- **Migration**: 既有 books table backfill 為 `mode='bilingual-en-zh'` (zero risk — 既有都是 en-zh 預設)；既有 N 筆 frontmatter `bilingual: true` lazy migration（Slice 5 lazy detect 時順手寫 `mode:`，覆蓋舊 boolean）
- **Storage convention**: keep `bilingual.epub` slot name (Phase 1) — Phase 2 rename refactor

### 4.7 Schema / API contracts

**`shared/schemas/books.py` `Book`**:

```python
Mode = Literal["monolingual-zh", "bilingual-en-zh"]

class Book(BaseModel):
    schema_version: Literal[1] = 1
    book_id: str
    title: str
    author: str | None
    mode: Mode                              # NEW required field
    lang_pair: str                          # DEPRECATED, kept for backward read; equals "zh-zh" or "en-zh"
    genre: str | None
    isbn: str | None
    published_year: int | None
    has_original: bool                      # always False for monolingual-zh
    book_version_hash: str
    created_at: str
```

**Source frontmatter convention**:

```yaml
---
title: "..."
mode: monolingual-zh        # NEW required field; replaces ad-hoc `bilingual: true` boolean
source: "https://..."
fulltext_status: ready
---
```

既有 `bilingual: true` boolean → Slice 5 lazy detect 時 migration 為 `mode: bilingual-en-zh`。

**New endpoints**:
- `POST /robin/source-mode/{filename}` body `{mode: Mode}` → mutate frontmatter `mode:` field; return 200/404
- `POST /api/books/{book_id}/mode` body `{mode: Mode}` → mutate `Book.mode`; return 200/404

**Modified endpoints**:
- `POST /books/upload`: `bilingual` field 變 optional, 加 `mode: str = Form(...)` 參數; server validates「至少有 bilingual 或 original」
- `POST /api/books/{book_id}/ingest-request`: 加 mode-aware 拒絕 logic
- `POST /robin/start`: 加 mode-aware 拒絕 logic

### 4.8 憲法 / 設計原則

- **Reversible pilot**: 任何 Phase 1 動作都不污染既有英文 KB Concept namespace；任何中文書 / 中文 article 完全可從 vault 移除而不影響其他系統 state
- **No LLM in pilot path**: Phase 1 detection 純走 EPUB metadata + langdetect-py rule-based；無任何 LLM API call (translator / ingest / annotation_merger 全部 Phase 1 不觸發)
- **Minimal-change-first**: 既有 EPUB Reader 5 slice (PR #379-#383) ship 過的所有 path 在英文 / bilingual 模式下行為不變
- **Open-source friendliness** (per `feedback_open_source_ready`): `lang_detect` + `source_mode` 兩 helper 是 standalone module，可獨立開源
- **Test fixture path constants** (per `feedback_test_fixture_path_constants`): test fixtures 走 production constant，不複製 hardcoded literal
- **Worktree session hygiene** (per `feedback_worktree_session_hygiene`): implementation 走 worktree，主 tree venv 絕對路徑

---

## 5. Testing Decisions

依 `feedback_test_realism` + `feedback_pytest_monkeypatch_where_used`：只測 external behavior。

| Module | 測試方式 | 緊迫性 | Slice |
|---|---|---|---|
| `shared.lang_detect` | 5 個 zh sample (含繁簡 + 學術 + 口語) + 5 個 en sample → assert lang label；fallback to "unknown" on too-short text (<30 chars) | 🔴 critical | S1 |
| `shared.source_mode` | parse + write frontmatter round-trip；既有 `bilingual: true` boolean → migrate to `mode: bilingual-en-zh` | 🔴 critical | S1 |
| `Book.mode` schema | Pydantic validation：accept 兩個 enum value、reject 第三 value；`lang_pair` deprecated 仍可讀 | 🟡 light | S1 |
| Upload route | mode form param accepted；bilingual + original 任一存在 OK、兩者皆無 reject 400；same-sha auto-switch monolingual-zh | 🔴 critical | S2 |
| Ingest gate | mode=monolingual-zh book → 400 with explicit message；mode=bilingual-en-zh + has_original=False → 既有 400；mode=bilingual-en-zh + has_original=True → 200 | 🔴 critical | S7 |
| Inbox lazy detect | fixture inbox 5 mixed-lang files 缺 `mode:` → list 後 frontmatter 已寫 `mode:`；既有 `bilingual: true` boolean migrate to `mode: bilingual-en-zh` | 🔴 critical | S5 |
| `/robin/source-mode` POST | 200 with valid mode → frontmatter mutated；invalid mode → 422；nonexistent file → 404 | 🟡 light | S5 |
| Reader UI mode awareness | manual browser smoke：upload zh epub → reader 開 → 看不到譯文 toggle / 翻譯 / ingest button；mode badge 顯示 | 🟡 light | S4, S6 |
| **Integration** | 1 e2e: 上傳 1 zh epub fixture → reader 開 → 標 1 highlight + 1 annotation + 1 reflection → POST `/api/books/{id}/annotations` → assert `KB/Annotations/{id}.md` 出現 v3 schema content + `digest.md` 自動產 | 🔴 critical | S7 |

**不測**:
- foliate-js 內部 (vendor)、langdetect-py 內部 (pkg)
- annotation v3 schema migration logic (PR #453 既有測過)
- BG task 競態 (既有 books.py 模式假設 single-process uvicorn)

**Acceptance fixtures**:
- 1 small zh EPUB（~1MB，無 DRM、metadata.lang=zh-TW）— store at `tests/fixtures/books/sample-zh-monolingual.epub`，找 Project Gutenberg 中譯本或修修自製
- 5 mixed-lang inbox markdown fixtures — `tests/fixtures/inbox/{en,zh}-{1,2,3}.md`

**修修人眼 acceptance** (post-S7)：
- 上傳手上其中一本實 EPUB（修修提供）
- 標 5 條 highlight + 2 條 annotation + 1 條 reflection
- 開 vault 確認 `KB/Annotations/{id}.md` 三型內容正確
- 開 vault 確認 `KB/Wiki/Sources/Books/{id}/digest.md` 產出（reverse-surface wikilinks 應為空 `_none yet — run KB sync first_`，KB hits 看 ADR-022 production 狀態）
- 開 Brook synthesize（任意 topic + 中文 highlight 內容相關 keyword）→ 確認撈得到該 highlight chunk

---

## 6. Out of Scope（明列、避免 Codex 越界）

### 6.1 完全不在 Phase 1 scope，全留 Phase 2 grill 後再做

- ❌ `annotation_merger` cross-lingual sync（中文 highlight → Concept page section 的 LLM-match）
- ❌ `textbook-ingest` skill 對中文書的 zh-source prompt variant
- ❌ `agents/robin/ingest.py` IngestPipeline 對中文 article 的 concept extraction
- ❌ `Concepts/*.md` frontmatter `aliases` array 加中文（既有英文 page 的 zh aliases backfill）
- ❌ Concept page 命名規則改變（仍 filename = English canonical）
- ❌ `canonical_id` / structured `labels` block frontmatter（panel 推薦但 Phase 2 才考慮）
- ❌ `shared/concept_canonicalize.py` (PR #441 中) 擴 zh-EN entries
- ❌ Bridge UI proposal-queue review surface
- ❌ Brook synthesize wikilink alias display（[[Concepts/X|alias]]）對 monolingual-zh output

### 6.2 也不在 Phase 1 scope（不同理由）

- ❌ Storage convention rename `bilingual.epub` → `display.epub`（PR-level migration，Phase 2 cleanup）
- ❌ 既有 books table 多 lang_pair backfill（lang_pair deprecated 不刪、`mode` field 是新 truth source）
- ❌ Reverse-translate（中→英）功能（不同 use case，超出 PRD）
- ❌ ADR-022 production rebuild verification（獨立 ops task，已有 audit 提，請開 follow-up issue）
- ❌ 教科書 ingest 端跨語言改造（修修明確：永遠英文教科書）

### 6.3 顯式 NO-OP for Phase 1

- monolingual-zh book → ingest button 顯式 hide（不是 disable）
- monolingual-zh article → start ingest button 顯式 hide
- monolingual-zh source → 翻譯按鈕顯式 hide
- monolingual-zh source → 譯文 v / 譯文 x toggle 顯式 hide

---

## 7. Migration

### 7.1 既有 books table

```sql
ALTER TABLE books ADD COLUMN mode TEXT DEFAULT 'bilingual-en-zh';
-- backfill 既有所有 row 為 'bilingual-en-zh' (zero risk: 既有都是 en-zh)
UPDATE books SET mode = 'bilingual-en-zh' WHERE mode IS NULL;
```

### 7.2 既有 hack 上傳的書

**修修可能之前**：把同份中文 EPUB 上傳兩次當 bilingual + original（hack work-around）。

**Migration logic** (Slice 2)：upload 路徑加 detect — `if bilingual.sha == original.sha and detected_mode == "monolingual-zh"`: store as monolingual-zh, set `has_original = False`, drop original blob, log warning.

不批次跑 — 純新上傳時觸發 (lazy)。既有已上傳 hack 書修修可手動 re-upload。

### 7.3 既有 inbox/kb/*.md frontmatter

Slice 5 lazy detect at inbox listing time：
- 缺 `mode:` field → run `detect_lang()` + write `mode:` frontmatter
- 既有 `bilingual: true` boolean → migrate to `mode: bilingual-en-zh`，刪 boolean
- frontmatter mutation **avoid stomping user edits** — 只在 `mode:` 不存在 + `bilingual:` 為 boolean 時動

### 7.4 既有 KB/Annotations/*.md

Phase 1 **0 改動**。v3 schema 已支援三型，annotation indexer 已 wire。

---

## 8. Cross-references

### Grill / Panel artifacts (本 PRD 上游)

- `docs/plans/2026-05-08-monolingual-zh-source-grill.md` — 8 grill 決定凍結
- `docs/decisions/ADR-024-cross-lingual-concept-alignment.md` — **Superseded** by this PRD
- `docs/research/2026-05-08-codex-adr-024-audit.md` — Codex panel audit (REJECT)
- `docs/research/2026-05-08-gemini-adr-024-audit.md` — Gemini panel audit (REJECT)
- `docs/research/2026-05-08-adr-024-panel-integration.md` — 3-way audit integration matrix
- `docs/research/2026-05-09-digest-md-cross-session-findings.md` — digest.md cross-cutting findings (給另一個視窗的 Claude)

### 既有 ADRs / PRDs (本 PRD 倚賴 / extends)

- `docs/decisions/ADR-017-annotation-kb-integration.md` — annotation 物理獨立檔 + per-source full-replace
- `docs/decisions/ADR-021-annotation-substance-store-and-brook-synthesize.md` — v3 schema, indexer scan KB/Annotations
- `docs/decisions/ADR-022-multilingual-embedding-default.md` — BGE-M3 default; production rebuild verify pending (see §10)
- [PRD #378](https://github.com/shosho-chang/nakama/issues/378) — EPUB Reader (bilingual mode existing implementation, Slice 1-5 ship)
- [PRD #351](https://github.com/shosho-chang/nakama/issues/351) — Stage 1 URL ingest entry升級 (relevant to Slice 5)
- [PRD #430](https://github.com/shosho-chang/nakama/issues/430) — Book Digest + Hybrid Retrieval Engine (digest.md mechanism reuse)

### Code refs (Codex audit verified)

- `thousand_sunny/routers/books.py:118-128, 200-214, 236-243, 340-375` — upload, reader, ingest gate, annotation save
- `shared/schemas/books.py:35-50` — Book schema
- `shared/translator.py:30-32, 116-119` — EN-tuned glossary
- `agents/robin/annotation_merger.py:227-233, 321-323, 390-397` — LLM-match candidate strings
- `shared/annotation_store.py:243-280` — upgrade_to_v3
- `shared/kb_writer.py:716-722` — alias merge dedup
- `agents/robin/ingest.py:60-82` — _build_existing_concepts_blob (grounding pattern)
- `shared/kb_embedder.py:33-35` — BGE-M3 default
- `agents/robin/inbox_writer.py:96-102` — find_existing_for_url + Web Clipper convention
- `thousand_sunny/templates/robin/book_upload.html:55-79` — upload form template

### Memory (Phase 1 implementation should respect)

- `memory/SCHEMA.md` — v2 memory schema (panel-reviewed 2026-05-08)
- `memory/claude/feedback_test_realism.md`
- `memory/claude/feedback_test_fixture_path_constants.md`
- `memory/claude/feedback_worktree_session_hygiene.md`
- `memory/claude/feedback_pytest_monkeypatch_where_used.md`

---

## 9. Phase 2 trigger 條件

不在 Phase 1 ship 範圍但**重要 context**：Phase 2 grill (cross-lingual concept architecture) 的觸發條件由修修判斷：

- 修修讀完 5+ 中文書、annotation 累積到實量
- 寫第一篇 monolingual-zh 來源稿 (中文長壽科普)
- 撞到「Brook synthesize 撈不到中文書 evidence」「中文 highlight 該 sync 到 Concept page 但不能」「既有英文 Concept 在中文搜尋下找不到」等真實 case
- ADR-022 production rebuild verify 完

那時帶實 data 重 grill：filename-as-id vs ID-first multilingual identity model；annotation_merger cross-lingual sync 設計；既有英文 Concept zh aliases backfill 策略；textbook ingest + 中文 source ingest 是否合併。

ADR-024 body 留作 Phase 2 grill input（已標 Superseded）。

---

## 10. 風險

| 風險 | 等級 | mitigations |
|---|---|---|
| **ADR-022 production rebuild 沒 verify** — `data/kb_index.db` 仍 256-dim potion 而非 1024-dim BGE-M3，中文 query dense lane 撈不到英文 KB hits | 🔴 high | Slice 7 acceptance 加 dim assertion (`SELECT length(embedding)/4 FROM kb_vectors LIMIT 1` 應為 1024)；若不對，issue 開到 ops queue 修 ADR-022 落地，**不阻擋 Phase 1 ship**（Phase 1 retrieval degraded 是 known issue） |
| **同一份 中文 EPUB 上傳被 bilingual slot + original slot duplicate** | 🟡 medium | Slice 2 same-sha detect 自動切 monolingual-zh + drop original blob；既有 hack 上傳的書 lazy migrate on next reupload |
| **EPUB metadata.lang 缺值** | 🟡 medium | Fallback `detect_lang()` on first chapter sample；UI override 為 final escape |
| **frontmatter `mode:` 跟 `bilingual:` boolean 並存** | 🟡 medium | Slice 5 lazy detect rule：`mode:` 存在 → trust；缺 `mode:` + `bilingual:` boolean → migrate；缺兩者 → detect + write |
| **Reader UI bilingual layout regress** | 🟡 medium | 既有英文 / bilingual books 行為 zero change；test acceptance Slice 4 manual smoke 同時驗 bilingual book 仍正常 render |
| **Web Clipper 落 `Inbox/kb/*.md` frontmatter convention drift** | 🟢 low | inbox_writer:96-102 已 handle `source` vs `original_url` dual-key；Phase 1 不碰 |
| **既有 100+ Concept page 對中文 highlight 撈不到** | by-design (Phase 1 acceptable) | Phase 1 副作用，文件化於 §2 表 + 修修 confirmed |
| **digest.md reverse-surface wikilinks 永遠空** | by-design (Phase 1 acceptable) | 同上 |

---

## 11. Phase 1 ship 完之後的 follow-up issues 候選

Codex 實作 Phase 1 後 / panel 反饋 / 修修實際使用後可能該開的 issue：

1. **ADR-022 production rebuild verify** — runtime dim assertion + rebuild log; ops scope, 兩個視窗都會卡這條
2. **既有英文 Concept zh aliases batch backfill** — Phase 2 觸發條件之一，先存 issue 不做
3. **Reader UI Phase 2 cleanup** — `bilingual.epub` storage rename → `display.epub`
4. **Edge case multilingual EPUBs** — 譯者前言中文 + 內文英文混雜書 detection (修修：走 user override，不嘗試自動)
5. **Document `/translate` route monolingual-zh noop guard** — 即使 UI 隱藏按鈕，server-side 加 mode check 防直接 POST 攻擊（safety, low risk）

---

## 12. Acceptance for Codex hand-off

Codex pickup 此 PRD 後：

1. **第一個動作**：read this PRD + grill summary + panel integration matrix + 既有 5 個 EPUB Reader Slice PR (#379-#383)，理解 baseline
2. **不確定處 ask 修修**：mode 字串值、storage convention、UI override 的視覺呈現，這三處有歧義就停下問
3. **不該做**：任何 §6 Out of Scope 項目；觸碰既有 100+ Concept page；改 annotation_merger LLM-match prompt；擴 alias map
4. **dispatch pattern**：S1/S2/S5/S7 走 sandcastle (deep modules + tests)；S3/S4/S6 走 manual worktree + Playwright MCP browser smoke
5. **PR 命名**：`feat(books): N-XXX monolingual-zh Slice N — <topic>`，每 slice 一 PR
6. **Ship 順序**：S1 → S2 → S5 並行 → S3+S4+S6 並行 → S7 acceptance gate
7. **每 slice merge 條件**：tests 全綠 + ruff clean + 觀察點 manual smoke pass + 修修 review approve

修修最後 acceptance：用 §5 修修人眼 acceptance 走一輪實 EPUB（修修提供 1 本台版中譯）+ 標一些註記 + 確認三檔（`KB/Annotations/{id}.md` + `digest.md` + Brook synthesize 撈到）。
