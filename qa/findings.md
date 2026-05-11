# N518 Dry-Run QA — findings

**Branch**: `chore/N518-dry-run-qa` (from `origin/main` @ `491f9f2`)
**Mode**: `NAKAMA_PROMOTION_MODE=dry_run` (default)
**Vault**: `E:/Shosho LifeOS`
**Started**: 2026-05-10
**Goal**: Verify the full ADR-024 promotion pipeline end-to-end against real vault data, dry-run mode. No LLM call.

**Status**: ✅ **PASS** — N518 dry-run wiring 端到端可用，14/14 checks 通過（Q13 以 N/A 計）。5 個 finding 全部 non-blocking，可後續獨立 PR 處理。

## Findings 一覽

| ID | Severity | Title | 阻塞 N518？ | 建議 |
|----|----------|-------|------|------|
| F01 | MEDIUM | list / review surface 顯示 source_id 路徑而非 article title | 否 | 獨立 PR + schema bump |
| F02 | LOW (by-design) | inbox row lang 全 unknown | 否 | News Coo S2+ 自然修正；舊檔 backfill 為獨立 work |
| F03 | HIGH UX (non-blocking) | review surface 認知負擔過高 | 否 | **新 worktree** + Claude Design 視覺探索 + 獨立 PR |
| F04 | LOW | bogus source GET 渲染相同 empty-state，未顯示 404 | 否 | 順 F03 PR 一起 |
| F05 | LOW | HTTPException 在瀏覽器顯示 raw JSON | 否 | 順 F03 PR 一起 |
| F06 | HIGH (架構) | ebook lister vs book_storage path 不一致 — list view 永遠看不到任何書 | 對 ebook scope 是；對本次 QA 否（QA 全 inbox）| 獨立 PR；先決定 books 該 vault-relative 還是 cwd-relative |

## Next steps

1. 把這份 findings.md commit 到 `chore/N518-dry-run-qa` branch + push + open PR for review
2. close issue #508
3. 開新 worktree `E:\nakama-promotion-review-redesign` 跑 F03/F04/F05 redesign（user 已選此路徑）

---

## Pre-flight checks

- [x] **PF1** `.env` has `NAKAMA_VAULT_ROOT="E:/Shosho LifeOS"`
- [x] **PF2** `DISABLE_ROBIN` is unset
- [x] **PF3** `E:/Shosho LifeOS/Inbox/kb/` has candidate `.md` files (15)
- [x] **PF4** `E:/Shosho LifeOS/KB/Wiki/Concepts/` exists
- [x] **PF5** uvicorn boots; lifespan logs `nakama.web.promotion_wiring INFO — promotion surfaces wired`

## Smoke (Q1-Q5) — wiring works end-to-end

- [x] **Q1** `GET /promotion-review/` returns **200** — list page renders cleanly (screenshot confirmed)
- [x] **Q2** Inbox candidates surface in list view — 14 entries visible, all `proceed_full_promotion` / `proceed_with_warnings`
- [x] **Q3** Click a candidate → review surface 200，empty-state「尚未建立 manifest」+ Start review 正確顯示 (F01 同樣 bite — H1 也是路徑非 title)
- [x] **Q4** `POST .../start` → 303 redirect 成功；STATUS · NEEDS_REVIEW，3 items 渲染（無 500 / NotImplementedError）
- [x] **Q5** 每個 item 的 reason 都帶 `[DRY-RUN] Deterministic dry-run claim about ...; no anthropic call was made.` fingerprint，N518b dry-run extractor 路徑通

## Manifest persistence (Q6-Q8)

- [x] **Q6** `E:/Shosho LifeOS/.promotion-manifests/aW5ib3g6...md.json` 建好（base64url 編碼 source_id 當檔名）
- [x] **Q7** Manifest JSON `status: "needs_review"`, items=3 (2 source_page + 1 concept), recommender model_name=claude-opus-4-7
- [x] **Q8** source_page items reason 含 `[DRY-RUN]` fingerprint。concept item reason 由 #514 deterministic logic 算（chapter-ref 計數），by-design 不帶 [DRY-RUN]

## Failure surfaces (Q9-Q11)

- [x] **Q9** `resolver.resolve('bogus:foo')` → None；route 層 falls to empty-state branch（不 500）；POST start 走到 service 層觸發 ValueError → 400
- [x] **Q10** `nakama.shared.source_resolver WARNING — source_resolver got unrecognized namespace prefix` + `category=source_resolver_unknown_namespace` extra 都打對
- [x] **Q11** `service.start_review('bogus:foo')` → `ValueError: source_id='bogus:foo' did not resolve to a ReadingSource`；route 層轉 HTTPException(400)。Bonus 覆蓋空字串 / 無 namespace / 不存在 inbox 路徑都產生人類可讀 ValueError 無 stack trace 洩漏

## Writing Assist surface (Q12-Q13)

- [x] **Q12** Route wired ✅ — 回 404 `package not found for source_id=...`（非 503 "service not configured"，N518a 之前 unwired 狀態的訊號）
- [~] **Q13** N/A — ReadingContextPackage 是另一條 authoring pipeline 產物，N518 scope 不含 package authoring；vault 連 `.reading-context-packages/` 都還沒建，404 是預期行為。Template 渲染需要先有 package JSON 才能驗

## Cache invalidation (Q14)

- [x] **Q14** mtime-based invalidation 正確：baseline 0 entries → 寫入新 `.md` → re-scan 看到新 entry，alias lookup 即時生效，**不需重啟 server**

---

## Findings (file as discovered)

### Format

```
### Fxx [severity] — short title
**Repro**: <minimal steps>
**Expected**: <what should happen>
**Actual**: <what happened>
**Evidence**: <log lines, screenshots, request/response>
**Root cause**: <if known>
**Fix proposal**: <or "open">
```

Severity: BLOCKER (can't proceed) / HIGH (functional bug) / MEDIUM (UX issue) / LOW (cosmetic)

---

## Findings log

### F01 [MEDIUM] — list view 第一欄顯示 source_id 路徑而非 article title

**Repro**: 開 `GET /promotion-review/`，看 source_id 欄位
**Expected**: 主行顯示文章 title（例如 "AI and the New Rules of Corporate Competition"），source_id 路徑當 mono 副行（debug / 工程用）
**Actual**: 主行就是完整 source_id，例如 `inbox:Inbox/kb/Ch1 AI and the New Rules of Corporate Competition.md` — 視覺上是路徑不是文章
**Evidence**: 14 行全部一樣模式；截圖確認
**Root cause**: `PromotionReviewState` schema 沒搬 `title` 過去（[shared/schemas/promotion_review_state.py:51](shared/schemas/promotion_review_state.py#L51) 只有 `source_id` + `primary_lang` + preflight 欄位）。`ReadingSource.title` 早在 #509 registry 就已經 derive 好（inbox 的 fallback 是 logical_original 的 stem，[shared/reading_source_registry.py:347](shared/reading_source_registry.py#L347)）— 是 service 層 `_build_state` ([shared/promotion_review_service.py:475](shared/promotion_review_service.py#L475)) 沒帶過去
**Fix proposal**: schema 加 `title: str` → `_build_state` 帶 `rs.title` → [thousand_sunny/templates/promotion_review/list.html:51-53](thousand_sunny/templates/promotion_review/list.html#L51-L53) 改成 title 主行 + source_id 副行（mono 縮小）。獨立 PR，scope 小，應該 ≤30 LOC + schema bump

---

### F02 [LOW, by-design 但有 future work] — 全部 inbox row 的 lang 欄都是 unknown

**Repro**: 看 list view 的 lang 欄
**Expected**: 走過正式 ingest 流程的檔應該有 BCP-47 lang（`en` / `zh-Hant`）
**Actual**: 14 個全部 `unknown`
**Evidence**: 截圖 + peek `Ch1 AI and the New Rules...md` 整份零 frontmatter
**Root cause**: 兩條入口都不寫 `lang:`：
  - 舊 Robin URL pipeline（[agents/robin/inbox_writer.py:249-262](agents/robin/inbox_writer.py#L249-L262) `_serialise_frontmatter` 不含 lang field）
  - 手動貼 / Obsidian Web Clipper — 完全沒 frontmatter
  `ReadingSource.primary_lang` 對 inbox 取自 frontmatter `lang:`，缺則 `"unknown"` (closed-set, 永不 fallback `"en"`，N509 Q1 拍板)
**Downstream 影響**: #511 preflight 設計上會用 `primary_lang_confidence` 做 bilingual-only inbox defer 判斷，但 `unknown` + `evidence_reason=None` 路徑下不降級。**不阻塞 N518 dry-run**
**Fix proposal**:
  - 新檔：今天 import 的 News Coo PRD §5.2 已把 `lang: en` 列為 required frontmatter — News Coo S2+ 上線後新檔自動有 lang
  - 既存舊檔：需要 backfill（langdetect on body）— 獨立工作項，**非 N518 scope**

---

### F03 [HIGH UX, non-blocking] — review surface 認知負擔過高，使用者看不懂在做什麼決定

**Repro**: POST start 後，`/promotion-review/source/{id_b64}` 渲染 review items
**修修原話**: 「這個頁面有三個 session，我都截圖給你看了，但我實在看不懂這個作用是什麼」

**問題拆解**:

1. **兩種異質 item 平鋪**：`SourcePageReviewItem`（寫去 `KB/Wiki/Sources/{slug}/`）與 `ConceptReviewItem`（寫去 `KB/Wiki/Concepts/`）是**完全不同 destination 的決策**，但目前 UI 把它們當同一個列表渲染。看不出「我在審 Source 頁 vs 我在審 Concept 升格」這個根本差異。

2. **item_id 當識別主行**：例如 `Ch1-AI-and-the-New-Rules-of-Corporate-Competition::index` — slug + `::` + 內部 segment kind，工程觀感，使用者要做語意 inference 才知道「喔這是目錄頁」。

3. **action enum 直接當按鈕 label 渲染**：
   - SourcePageAction: `CREATE` / `DEFER` / `update_merge` / `update_conflict` / `noop`
   - ConceptAction: `KEEP_SOURCE_LOCAL` / `INCLUDE` / `create_global_concept` / ...
   這些是 schema 內部值，不是 UI label。`KEEP_SOURCE_LOCAL` / `INCLUDE` 並列在同一 row 視覺上像兩個對等選項，但實際上一個是 LLM 的 action，一個是人類的 review 動作（approve）。

4. **三組 affordance 重疊不清**：
   - 上方 LLM action button（CREATE / DEFER）— 不可改
   - 右下 human decision button（approve / reject / defer）— 真正可按
   使用者不知道哪個是「我點的」。

5. **三個分數沒 context**：`confidence 0.50 / source_importance 0.50 / reader_salience 0.00` 沒解釋意義 + dry-run 全是固定假值，更增加 noise。

6. **defer / include / exclude 三個 recommendation 動詞與 approve / reject / defer 三個 decision 動詞 overlap**：兩組都有 `defer`，但語意不同。

**Redesign 方向**（建議獨立 PR）:

- **頂部 1 段話 explainer**：「審核這份來源要在 KB 留下什麼。Source 頁 = 這份文章本身的 KB 紀錄；Concept = 從文章抽出的全域概念」
- **分兩個 section 渲染**：
  ```
  📄 Source 頁（這份文章在 KB 的紀錄）
    ├ 目錄頁  · 建議：先 defer，等其他章節資訊
    └ 整篇頁  · 建議：建立此頁  ✓ 1 段引文
  
  🏷️ 全域 Concept（從文章抽出的概念候選）
    └ The HBS Insider · 建議：留在 Source 內，不升格全域概念  · 1 chapter 提及
  ```
- **item 標題用人話**：「目錄頁 / 整篇頁 / Concept: {label}」，slug 與 `::index` 等 internal id 移到 mono 副行（debug 用）
- **LLM 推薦只是文字陳述，不渲染成按鈕**：「LLM 建議：`建立此頁`，理由：...」
- **真正的 action 只剩 3 個按鈕**：採納建議 / 拒絕 / 先 defer。tooltip 解釋三者
- **分數預設 collapse**：點開「details」才展開三個分數 + 解釋
- **dry-run 模式的固定假分數加標示**：例如 reader_salience 0.00 旁邊加 `(dry-run)` 標籤，避免使用者誤判

**Repro path 不阻塞 N518**: 目前 UI 雖然難懂，但功能性正確（按鈕能按、決策能存）。redesign 是 UX 提升而非 functional bug。

**Fix proposal**: 獨立 PR，touch [thousand_sunny/templates/promotion_review/review.html](thousand_sunny/templates/promotion_review/review.html) + [_item_card.html](thousand_sunny/templates/promotion_review/_item_card.html) + [promotion_review.css](thousand_sunny/static/promotion_review.css)。建議走 Claude Design 視覺探索後再落地。預估 ~200-300 LOC + design tokens。

---

### F04 [LOW] — GET /promotion-review/source/{bogus_b64} 沒區分「source 不存在」vs「source 未審核」

**Repro**: GET `/promotion-review/source/Ym9ndXM6Zm9v`（base64url 編碼的 `bogus:foo`，未知 namespace）
**Expected**: 404 + 「source not found」UI
**Actual**: 200 + 與「合法 source 但未開始 review」相同的 empty-state UI。使用者按下 Start review 才會在 POST 階段拿到 400 ValueError
**Evidence**: 直接走 service 層確認 `state_for` / `load_review_session` 都回 None；route ([thousand_sunny/routers/promotion_review.py:177-192](thousand_sunny/routers/promotion_review.py#L177-L192)) 對 manifest=None 一律 fallback 到 empty-state template，不檢查 state 是否也 None
**Root cause**: GET handler 對 `state is None` 沒做區分；只用 manifest 的 None 來判斷要不要顯示 "Start review"
**Fix proposal**: 在 [promotion_review.py:181](thousand_sunny/routers/promotion_review.py#L181) 後加 `if state is None: raise HTTPException(404, "source not found")`。獨立 PR，~5 LOC。可順便併進 F03 redesign PR。

---

### F05 [LOW] — HTTPException 在瀏覽器顯示成 raw JSON `{"detail":"..."}`

**Repro**: GET `/writing-assist/{any_id_b64}`（任何 source 都行，因為 N518 scope 沒人產 package），瀏覽器顯示 `{"detail":"\"package not found for source_id='inbox:...'\""}`
**Expected**: HTML error page，「找不到此來源的 reading context package」+ 返回連結
**Actual**: FastAPI default JSON exception handler 直接吐 raw JSON 進瀏覽器
**Root cause**: 沒掛 HTML-accept 的 exception handler；route 用 `response_class=HTMLResponse` 只對 success path 生效
**Fix proposal**: 在 [thousand_sunny/app.py](thousand_sunny/app.py) 加 `@app.exception_handler(HTTPException)`，看 `Accept` header 走 HTML template 或 JSON。獨立 PR，~30 LOC + 一張 error template。可併進 F03 redesign PR

---

### F06 [HIGH (架構)] — ebook lister vs book_storage path 不一致，list view 永遠看不到任何書

**Repro**: ingest 一本書（不論 Chinese/English/bilingual）到 `book_storage.store_book_files(...)` → 它落到 `{cwd}/data/books/{book_id}/`；GET `/promotion-review/` 卻看不到該書

**Root cause**: 兩處對 `data/books/` 的根路徑不一致：
  - [shared/book_storage.py:51-52](shared/book_storage.py#L51-L52) `_books_root() = os.environ.get("NAKAMA_BOOKS_DIR", "data/books")` — **project-cwd 相對**（fallback `data/books`）
  - [thousand_sunny/promotion_wiring.py:172](thousand_sunny/promotion_wiring.py#L172) `books_root=config.vault_root / "data" / "books"` — **vault 相對**

**結果**:
  - `_list_books()` 走 vault 路徑 → 看不到 cwd 下實際的書 → list view 沒 ebook 條目
  - 但如果使用者透過已知 `book_id` 直接 POST 到 `/promotion-review/source/{base64(ebook:book_id)}/start`，registry 走 `book_storage` 的 cwd 路徑找得到 → start 會成功
  - Lister-vs-resolver 不一致：「列出所有候選」vs「已知 id 解析」走不同 root

**為何 N518 dry-run QA 沒抓到**: 本次 QA 14 個來源全是 `inbox:Inbox/kb/*.md`，零個 ebook。修修的 vault 連 `data/books/` 都還沒建，舊有的 EPUB 散落在 `E:/Shosho LifeOS/{,Inbox/}*.epub`，不在 book_storage layout 內

**Fix proposal**:
  - **先決定 source of truth**：books 要 vault-relative（跟 manifest / package 一樣）還是 cwd-relative（既有 book_storage 假設）？關係到 backup scope、Obsidian sync 範圍、跨機器是否共享
  - 統一後修對應的另一邊
  - 順便把 `NAKAMA_BOOKS_DIR` env override 文檔化
  - 預估獨立 PR ~50 LOC + 文檔說明 + 1 個 integration test 確認 lister + resolver 看到同一目錄

---
