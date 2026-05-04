# PRD: Stage 1 Ingest URL 入口升級 — Reader URL 統一走 5 層 OA fallback engine

> 對齊 [CONTENT-PIPELINE.md](../../CONTENT-PIPELINE.md) Stage 1 (Discovery) + Stage 2 (Reading + Annotation) 入口統一。

## Problem Statement

修修在 Reader（本機 web UI）想消化某篇文章時，貼上 URL，期待 Reader 把網頁抓下來、去廣告、萃取主體（標題/內文/圖表），存入待閱讀清單，等他有空讀完並 annotation，按 ingest 完成 Stage 3 流程。

但既有 `/scrape-translate` endpoint 用的是 3 層**笨**抓取（Trafilatura → Readability → Firecrawl 整頁），對學術 JS-heavy 網站撞牆：

- **Lancet** `eclinm/PIIS2589-5370(25)00676-5/fulltext` → Firecrawl `only_main_content=True` 4 種參數組合都只回 **958 字 chrome**
- **BMJ Medicine** `bmjmedicine.bmj.com/content/5/1/e001513` → 半身 content + **圖片 0**

這些 paper 本來就是 OA、本來就在 Europe PMC / publisher HTML 上有完整版可拿。問題不是 scraper bug，是**結構性兩條 ingest path 分裂**：

| | Robin PubMed digest | Reader `/scrape-translate` |
|---|---|---|
| 入口 | PMID（cron） | URL（修修 paste） |
| Fallback | **5 層** OA（efetch → PMC → Europe PMC → Unpaywall → publisher HTML，PR #94 BMJ/PLOS/eLife 友善） | **3 層** 笨（Trafilatura → Readability → Firecrawl） |
| DOI-aware | ✅ | ❌ |
| 圖片 | ✅ pymupdf4llm + `download_markdown_images` (PR #94) | ❌ 完全沒抽 |
| 翻譯 input 品質 | OA PDF / publisher HTML markdown | 前端 chrome / 半身 HTML |

修修 paste BMJ Medicine URL → 跑進笨 path → 拿到半身。但這篇 OA 在 Europe PMC 應該全文可拿。Path 用錯了。

**結構性影響**：Line 2 讀書心得 critical path 上週解了 annotation infra（PRD #337），這週要手跑流程。論文/部落格 ingest 拿不到全文 = annotation 標的是廢內容 = Stage 4 心得 atomic content 沒有 base material。

## Solution

從修修視角，三件事改變：

1. **Reader URL 入口升級到跟 PubMed digest 同等級** — 任何 URL 進來，先 detect 是不是 academic 來源（PubMed / DOI / arXiv / bioRxiv / 主流 publisher domain），是的話 reuse 既有 `agents/robin/pubmed_fulltext.py` 的 5 層 OA fallback engine；不是的話走 publisher HTML / readability / firecrawl
2. **抓完進 inbox 不翻譯，修修真人判斷後按按鈕才翻** — 對齊既有 PubMed reader「按需翻譯」pattern，省不必要的 LLM 成本，而且字數 heuristic 不可靠（BMJ Medicine 半身字數可能 > 1500，但體驗失敗），真人 30 秒掃一眼最準
3. **失敗檔一鍵丟棄** — Reader header + inbox row 雙入口刪除按鈕，連動刪 annotation；< 200 字硬擋直接拒寫（極低 threshold 保險避免廢檔污染清單）

修修日常操作流程：

1. 在外面看到一篇有興趣的論文 / 部落格 → paste URL 進 Reader 首頁
2. 後台抓（5 層 fallback for academic / 3 層 readability for general）+ 圖片下載 + 寫 `Inbox/kb/{slug}.md`
3. Reader 首頁 inbox 顯示「新增 1 篇 (來自 Europe PMC ✓)」
4. 修修點 row 進 reader 看原文 → 30 秒掃一眼
   - **品質 OK** → 按 reader header 「翻譯成中文」鈕 → 後台翻譯 → 跳雙語 reader → annotation → 按 ingest → 完成 Stage 3
   - **品質失敗（半身/廣告殘留）** → 按 reader header 「丟掉這篇」鈕 → 連動刪 annotation → 回 inbox 首頁
5. 之後「按需翻譯」short-circuit：同 URL 再 paste 直接跳 reader（已抓不重抓、已翻不重翻）

對既有 cron 路徑（Robin PubMed digest 每日 06:30）**0 改動 0 風險** — 兩個 use case 互補不重複（cron = 「我不知道有什麼新 paper」自動推薦；Reader URL = 「我已經知道這篇要讀」深讀）。

## User Stories

### 修修（owner）視角

1. As 修修, I want paste 任意 URL 都拿到全文（含學術 JS-heavy 網站如 Lancet / BMJ Medicine），不再撞 chrome only / 半身內容
2. As 修修, I want 抓取是後台 async — paste 完立刻回首頁、繼續做別的事，抓完在 inbox 看到新 row
3. As 修修, I want inbox row 顯示「來源 layer」（OA from Europe PMC ✓ / publisher HTML ⚠️ / scrape last resort ⚠️），讓我一眼判斷品質期望
4. As 修修, I want Reader 內看到原文後，按一個鈕才觸發翻譯（不要抓完直接付翻譯費）
5. As 修修, I want 同一個 URL 之後再 paste 不要重抓 + 不要重翻（既有 PubMed reader short-circuit pattern 一致）
6. As 修修, I want 失敗檔（半身/廢內容）能在 reader header 一鍵丟掉，連動刪 annotation，不留 garbage 在 vault
7. As 修修, I want inbox row 也有刪除按鈕（不點進 reader 也能 batch 清理）
8. As 修修, I want 抓取結果 < 200 字（鐵定不是文章）直接 reject 不寫 inbox + UI 提示「疑似 bot 擋頁」，避免我每次 paste 完都點開看廢頁
9. As 修修, I want 圖片（學術 forest plot / Kaplan-Meier / 部落格內容圖）一律下載到 vault，markdown 改 vault-relative path，跨裝置 sync 都看得到
10. As 修修, I want 5 層全失敗時 inbox row 標 ❌ + 提示「請手動處理」，我自己手動下 PDF / paste markdown 進 `Inbox/kb/`（既有 path）
11. As 修修, I want 既有 PubMed digest cron（每日 06:30 抓 ~12 篇）行為完全不變，這個 PRD 不能 break 任何既有 cron 流程

### Reader / Thousand Sunny 視角

12. As `/scrape-translate` endpoint, I want 改成立刻寫 placeholder + 立刻 redirect 回 inbox 首頁，不再同步等抓完才回應
13. As Reader inbox view, I want 多顯示一欄 status (`processing` / `ready` / `translated` / `failed`)，row icon 對應 (🔄 / ✅ / ✅ 雙語 / ❌)
14. As Reader header, I want 加「翻譯成中文」按鈕（原文模式）+「丟掉這篇」按鈕（兩種模式都有）
15. As Reader render, I want fetch 原檔 + 顯示 frontmatter `fulltext_status` + `fulltext_source`，header 顯示「OA from {layer name}」
16. As reader 翻譯按鈕，I want 觸發後台 task 跑 `translate_document` → 寫雙語版 `Inbox/kb/{slug}-bilingual.md` → 跳新檔 reader（沿用既有 `pubmed-to-reader` short-circuit pattern）

### URL Ingest Engine 視角

17. As `agents/robin/url_dispatcher.py`（新模組）, I want 對任意 URL 判斷 academic source pattern，academic → 抽 PMID/DOI 餵 `fetch_fulltext`，non-academic → 走 readability + image fetch
18. As `fetch_fulltext`（既有），I want 完全不被改動 — URL dispatcher 是 caller adapter，不是 refactor
19. As image pipeline, I want reuse `download_markdown_images` (PR #94) — academic OA HTML 抽到的 markdown 跟 readability 抽到的 markdown 都跑同套圖片下載
20. As status metadata writer, I want 寫 frontmatter `fulltext_status` + `fulltext_source` + `fulltext_layer` + `original_url`，inbox view + reader header 都讀同一份 metadata

### Robin / Stage 3 視角

21. As Robin ingest pipeline (`/start` flow), I want 接 inbox 檔案時跟既有完全一樣 — 不論檔案來源是修修手動丟的、`/scrape-translate` 抓的、還是雙語翻譯版，都走同套 ingest pipeline
22. As Robin annotation merger（PR #343 已 ship）, I want 連動刪 annotation 走既有 `KB/Annotations/{slug}.md` 路徑，不引入新概念
23. As `pubmed_fulltext.fetch_fulltext()`, I want 0 改動 — 既有 cron caller 完全不受 URL dispatcher 影響

### 失敗刪除路徑視角

24. As Reader「丟掉這篇」按鈕, I want POST 到新 endpoint `/discard?file={name}&base={inbox|sources}`，server 端：
   - PowerShell 回收桶 send `_send_to_recycle_bin` (`thousand_sunny/routers/robin.py:45-55`)
   - 連動刪 `KB/Annotations/{slug}.md`（如存在）
   - confirmation prompt：「丟掉「{filename}」**和 {N} 條 annotation**？此操作可從 Windows 回收桶還原」
25. As Inbox view row 刪除按鈕, I want 同 endpoint，但 prompt 用 inline confirm（不需點進 reader）

### 系統 / governance 視角

26. As vault rule governance, I want `Inbox/kb/` 寫入路徑沿用既有規則（不需新增）
27. As CONTENT-PIPELINE.md, I want 這個 feature 明確 anchor 在 Stage 1 (Discovery 的 URL 入口) + Stage 2 (Reading + Annotation 入口統一)，不漂移成獨立 line
28. As future maintainer, I want 留 follow-up issue「v2 evaluate URL dispatcher unify into pubmed_fulltext (E2)」，記錄當前 E1 hybrid 是 v1 incremental 選擇，未來真覺得「兩條 caller 維護痛」再升級

### 未來延伸（deferred but reserved）

29. As Stage 4 心得工具（future, deferred）, I want 從 inbox + KB/Annotations/ 拉「我這次讀完的 source + annotation」derived view（不在這版 scope，等修修手跑 Line 2 痛點浮現再決定）
30. As Zotero integration（future）, I want 訂閱期刊 PDF 走 Zotero local library，跟本 PRD 的 OA fallback 並行（[project_zotero_integration_plan.md](../../memory/claude/project_zotero_integration_plan.md)）

## Implementation Decisions

### 模組結構（4 deep + 3 shallow）

**Deep modules（簡單 interface、複雜實作、值得獨立 unit test）**：

1. **URLDispatcher**（`agents/robin/url_dispatcher.py`，新建） — 入口 `dispatch(url) -> IngestResult`；藏 academic source pattern detection（regex + domain whitelist）、PMID/DOI 抽取、reverse lookup PMID（DOI → efetch → PMID）、layer routing（academic → `fetch_fulltext`、preprint → arxiv/biorxiv API、其他 → readability + firecrawl）
2. **IngestResult schema**（`shared/schemas/`，新建或落 url_dispatcher 內） — Pydantic：`status: ready|failed`、`fulltext_layer: pmc|europe_pmc|unpaywall|publisher_html|readability|firecrawl`、`fulltext_source`（display label）、`markdown: str`、`image_paths: list[str]`、`title: str`、`original_url: str`、`error: str | None`、`note: str | None`（< 200 字 reject 等訊息）
3. **InboxWriter**（`agents/robin/inbox_writer.py`，新建） — `write_to_inbox(result, slug) -> Path`；藏 frontmatter 序列化（含 `fulltext_status` / `fulltext_source` / `fulltext_layer` / `original_url`）、檔名 collision 處理（既有 `/scrape-translate` 的 counter pattern reuse）、< 200 字硬擋邏輯
4. **DiscardService**（`shared/discard_service.py` 或落 robin router） — `discard(file_path, base) -> DiscardReport`；藏 `_send_to_recycle_bin` 呼叫、annotation 連動刪、unsynced annotation 計數（給 confirm prompt 用）

**Shallow modules（薄 wrapper、整合測即可）**：

5. **Reader endpoints**（`thousand_sunny/routers/robin.py`） — 既有 `/scrape-translate` 改成「立刻寫 placeholder + 後台 BackgroundTask + 立刻 redirect」；新增 `POST /translate?file={name}` 觸發後台翻譯（沿用 `/pubmed-to-reader` short-circuit pattern）；新增 `POST /discard?file={name}&base={base}`
6. **Reader UI**（`templates/robin/index.html` + `reader.html`） — inbox view 加 status column；reader header 加「翻譯成中文」按鈕（原文模式）+「丟掉這篇」按鈕（兩種模式都有）；< 200 字 reject UI 提示
7. **Image fetch reuse** — `agents/robin/image_fetcher.py` (`fetch_images`) 既有 `/read` endpoint 已 call，URL ingest path 也走同函式不需改

### 資料 schema

**`Inbox/kb/{slug}.md` frontmatter**（新增欄位）：

```yaml
---
title: "{文章標題}"
source: "{原 URL}"                     # 已有
source_type: article                    # 已有
content_nature: popular_science         # 已有
fulltext_status: ready|translated|failed   # 新增
fulltext_layer: europe_pmc|publisher_html|readability|firecrawl|...   # 新增
fulltext_source: "Europe PMC"           # 新增（display label）
original_url: "https://..."             # 新增（同 source 但語意明確）
bilingual: true                         # 已有（翻譯後設）
---
```

**檔名 collision 處理**：沿用既有 `/scrape-translate` line 316-321 counter pattern（`{slug}.md` → `{slug}-1.md` → `{slug}-2.md`）；URL repeat detect 走 frontmatter `original_url` field（不靠檔名）。

### Pipeline / API

```
[修修 paste URL] → POST /scrape-translate (改) 
                  ↓
                  立刻寫 placeholder Inbox/kb/{slug}.md (status=processing)
                  ↓
                  立刻 redirect 回 / (inbox view)
                  ↓
                  [後台 BackgroundTask]
                  ↓
                  URLDispatcher.dispatch(url) 
                    ├─ academic → fetch_fulltext(pmid/doi) → markdown + image
                    ├─ preprint → arxiv/biorxiv API
                    └─ general → readability → firecrawl fallback
                  ↓
                  if len(markdown) < 200: 改 placeholder 為 status=failed + note="疑似 bot 擋頁"
                  else: InboxWriter.write_to_inbox(result, slug) (status=ready)
                  ↓
                  圖片下載到 KB/Attachments/inbox/{slug}/

[修修點 inbox row] → GET /read?file={name}&base=inbox (既有)
                    ↓
                    顯示原文 + reader header 「翻譯成中文」鈕

[修修按翻譯鈕]      → POST /translate?file={name}
                    ↓
                    後台 BackgroundTask: translate_document(原檔) → 寫 {slug}-bilingual.md
                    ↓
                    redirect 到 /read?file={slug}-bilingual.md&base=inbox
                    ↓
                    更新原檔 frontmatter fulltext_status=translated

[修修按丟掉鈕]      → POST /discard?file={name}&base={base}
                    ↓
                    DiscardService.discard(path, base)
                      ├─ 計算 annotation 數 → confirm prompt
                      ├─ _send_to_recycle_bin(file_path) (檔案進回收桶)
                      └─ delete KB/Annotations/{slug}.md (如存在)
                    ↓
                    redirect 回 / (inbox view)
```

**短路條件**：

- 同 URL 再 paste：透過 `original_url` frontmatter field 反查 inbox 既有檔案 → 已存在直接 redirect 到該檔（不重抓）
- 翻譯按鈕第二次按：`{slug}-bilingual.md` 已存在 → 直接跳 reader（不重翻，沿用 `/pubmed-to-reader` 既有 short-circuit）

### Domain / governance

- **Stage anchor**：本 PRD 對應 [CONTENT-PIPELINE.md](../../CONTENT-PIPELINE.md) Stage 1 Discovery URL 入口 + Stage 2 Reading + Annotation 入口統一
- **Engine reuse**：`fetch_fulltext` (既有 `agents/robin/pubmed_fulltext.py`) 不改動 — URL Dispatcher 是 caller adapter，不是 refactor。E1 (hybrid) 優於 E2 (layer 抽 + 重組) 的理由：cron 0 風險，academic URL 走實戰 1 週已驗證的 engine，未來真覺得 split-brain 痛苦再 promote E2
- **Cron 互補關係**：PubMed digest cron = 「我不知道有什麼新 paper」自動推薦；Reader URL 入口 = 「我已經知道這篇要讀」深讀。兩者 use case 不重複，引擎重複部分透過 reuse `fetch_fulltext` 解
- **失敗 detection 哲學**：字數 heuristic 不可靠（PR #94 1500 字 threshold 對 chrome 有效對半身無效），改靠真人判斷 + < 200 字硬擋當粗篩
- **不裝 Playwright runtime**：Lancet / BMJ Medicine 走 Europe PMC 應該夠（兩者都 OA、都在 PMC）；agent web automation（YouTube 留言等）等 Chopper 真開發再綁 slice 裝
- **Zotero 並行**：訂閱期刊（Nature / Cell / NEJM 付費）走 Zotero 路線（[project_zotero_integration_plan.md](../../memory/claude/project_zotero_integration_plan.md)），跟本 PRD 的 OA fallback 不衝突，互補

## Testing Decisions

### Test 哲學

- **只測外部行為，不測實作細節** — URLDispatcher 測「academic URL 路由到 fetch_fulltext」/「general URL 路由到 readability」，不測 regex 內部；InboxWriter 測「< 200 字 reject」、「frontmatter 欄位完整」，不測序列化細節
- **5 層 fallback engine 既有 test 覆蓋** — `pubmed_fulltext.py` 已有 test，本 PRD 不重測
- **網路 / 第三方 SDK call 用 monkeypatch 假** — 沿用既有 mock 模式，不打真 API

### 哪些模組要 test

| 模組 | 測什麼 |
|---|---|
| URLDispatcher | academic pattern detection（PubMed URL / DOI URL / arxiv URL / publisher domain）路由正確；non-academic URL 走 readability path；malformed URL 噴 ValueError |
| IngestResult schema | round-trip dict ↔ model；required field 缺失噴 ValidationError；`fulltext_layer` enum 限制有效 |
| InboxWriter | < 200 字 reject 寫入；檔名 collision counter；frontmatter 欄位完整；同 URL repeat detection (透過 original_url 反查) |
| DiscardService | `_send_to_recycle_bin` 呼叫；annotation 連動刪（存在/不存在 case）；unsynced 計數正確；檔案不存在噴錯 |
| Reader endpoints (整合) | authenticated cookie + form post；POST /scrape-translate 立刻 redirect 不等翻譯；POST /translate short-circuit (已存在不重翻)；POST /discard confirm 後刪除 |

### Prior art 參考

- `pubmed_fulltext.py` 既有 test pattern — 5 層 fallback 各層 mock + chain 行為
- PR #94 publisher HTML fallback test — `download_markdown_images` mock 模式
- ADR-017 annotation_store + annotation_merger test pattern — slug 為主鍵的 CRUD 測試
- `/scrape-translate` 既有 test — form post + auth cookie + RedirectResponse pattern

### 不寫自動 test 的模組

- Reader UI（inbox status column / 翻譯按鈕 / 丟掉按鈕 / < 200 字 UI 提示）— 修修瀏覽器手動驗收
- BackgroundTask 真實時序 — manual smoke 即可（test 用 sync mock 覆蓋邏輯）

## Out of Scope

- **5 層 engine 砍掉換新（E2 layer 抽 + 重組）** — v1 走 E1 hybrid dispatcher，等真覺得 split-brain 痛苦再 promote
- **PubMed digest cron 路徑改動** — cron 0 改動 0 風險，本 PRD 完全不碰 `agents/robin/pubmed_digest.py` 與 `pubmed_fulltext.py` 內部
- **Inbox PDF 直接進雙語 reader** — 5 層全失敗時修修手動下 PDF / paste markdown 走既有 `Inbox/kb/` 入口（C3 deferred）；等真實使用兩週看 failed 率再決定要不要補
- **付費 scrape service**（ScrapingBee / ScraperAPI 等）— 等 5 層 + 手動 fallback 都不夠用再評估
- **Playwright runtime** — 5 層應該夠 cover 學術，部落格有 readability 兜底；agent web automation 等真 use case 浮現
- **Zotero 整合** — 訂閱期刊另條軌，獨立 PRD（[project_zotero_integration_plan.md](../../memory/claude/project_zotero_integration_plan.md)）
- **字數 threshold 細分 ok/partial/failed** — 字數 heuristic 不可靠（BMJ Medicine 半身字數可能 > 1500），改靠真人判斷；只保留 < 200 字硬擋當粗篩
- **完成 notification**（抓完 / 翻完通知 Slack 之類）— async 後台 task 完成走 inbox view auto-refresh / 修修主動 refresh，不引入 notification infra
- **Reading session 邊界 / 心得 outline 工具** — Stage 4 deferred (對齊 PRD #337 同樣的 Stage 4 不介入原則)
- **重試 / 升級 layer UI** — 失敗檔走「丟掉 → 修修自己想辦法」，不引入「重試上一層」UI（[feedback_avoid_one_shot_summit](../../memory/claude/feedback_avoid_one_shot_summit.md) 簡化原則）

## Further Notes

### Slice 拆法（仿 PRD #337 4 slice 結構）

建議 4 個獨立 slice，可平行 / 串行：

- **Slice A（基底）**：URLDispatcher + IngestResult schema + InboxWriter + < 200 字硬擋
  - 依賴：無
  - 交付：`agents/robin/url_dispatcher.py` + `agents/robin/inbox_writer.py` + tests
  - 驗收：unit test 全綠（academic / preprint / general 三條 path 路由）；BMJ Medicine URL → 走 academic path → fetch_fulltext 拿到 OA HTML markdown；廣告頁 URL → < 200 字 reject

- **Slice B（reader 整合）**：`/scrape-translate` 改 BackgroundTask；inbox status column；reader header「翻譯成中文」按鈕 + `/translate` endpoint
  - 依賴：Slice A
  - 交付：`thousand_sunny/routers/robin.py` 改動 + `templates/robin/index.html` + `reader.html`
  - 驗收：手動 paste BMJ Medicine URL → 立刻回 inbox 首頁 → 後台抓完 row 變 ✅ → 點進 reader 看原文 → 按翻譯鈕 → 跳雙語 reader

- **Slice C（圖片 first-class）**：URL ingest path 套 image fetch (reuse `download_markdown_images`)
  - 依賴：Slice A
  - 交付：URLDispatcher 整合圖片下載；`Inbox/kb/{slug}.md` markdown 圖 path 改 vault-relative
  - 驗收：手動 paste BMJ Medicine URL → 抓完 row 進 reader → 圖片正常顯示（不是外部 hotlink）；vault sync 到 Mac 圖也在

- **Slice D（失敗檔丟棄）**：DiscardService + reader header / inbox row 「丟掉」按鈕 + `/discard` endpoint + annotation 連動刪
  - 依賴：無（可跟 A/B/C 平行）
  - 交付：`shared/discard_service.py` + reader/inbox UI 按鈕
  - 驗收：手動標 2 條 annotation → 按丟掉 → confirm 顯示「2 條 annotation」→ 確認 → 檔案進回收桶 + `KB/Annotations/{slug}.md` 也刪 → inbox 不見

**Slice 順序建議**：A 先（其他 3 個依賴它的 schema）→ B + C 並行 → D 最後（最不關鍵，可獨立 ship）

### 對齊既有架構

- **既有 PubMed reader pattern**：`/pubmed-to-reader` 是 short-circuit + 後台翻譯 + bilingual file write 的範本，B 階段 `/translate` endpoint 應 mirror 同套模式
- **既有 reader homepage**：`thousand_sunny/routers/robin.py:136-141` 的 `index()` 已 render `_get_inbox_files()`，本 PRD 只新增 status column，不改基底結構
- **既有 PowerShell 回收桶**：`_send_to_recycle_bin()` 已實作 + 已驗證 ([feedback_powershell_allow_exact_prefix](../../memory/claude/feedback_powershell_allow_exact_prefix.md))，DiscardService 直接 call
- **既有 annotation_store**：ADR-017 PR #342 已 ship `KB/Annotations/{slug}.md` schema + `annotation_slug()` helper，DiscardService 連動刪走既有 path

### 寫入路徑

| 路徑 | 內容 | 寫入 owner |
|---|---|---|
| `Inbox/kb/{slug}.md` | URL 抓的原文 + frontmatter status | URLDispatcher / InboxWriter |
| `Inbox/kb/{slug}-bilingual.md` | 翻譯後雙語 | `/translate` endpoint |
| `KB/Attachments/inbox/{slug}/` | URL 抓的圖片 | image_fetcher (既有) |
| `KB/Annotations/{slug}.md` | annotation（reader 已 ship） | annotation_store (既有) |

### 不重複的決策（這次 grill 凍結但跟既有 ADR / memory 一致）

- 圖片一視同仁全抓（不分學術/部落格）— Q3 grill 結論
- B2-B：抓完不翻、真人判斷後按按鈕翻 — Q1 reframe 結論
- 字數判斷不可靠改靠真人 + < 200 字硬擋 — Q4a grill 結論
- C3 deferred (inbox PDF 入口) — Q4c grill 結論
- E1 hybrid dispatcher (min reuse, cron 0 風險) — Q5 grill 結論
- 失敗檔連動刪 annotation — Q6b grill 結論

### Follow-up issue（不在本 PRD scope）

- v2 evaluate URL dispatcher unify into pubmed_fulltext (E2 layer 抽)，等 reader URL 入口跑 2-4 週 + 真實 cron split-brain 痛苦案例
- Inbox PDF → 雙語 reader 入口 (C3 deferred 觸發 → 真實 5 層全失敗率 > X% 後啟動)
- 重試 / 升級 layer UI（如果修修真覺得「丟掉重貼」摩擦累積）
