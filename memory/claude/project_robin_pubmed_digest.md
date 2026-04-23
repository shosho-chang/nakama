---
name: Robin PubMed 每日 digest
description: Robin PubMed 每日 digest + OA 全文自動下載 + 本機雙語閱讀整合狀態
type: project
originSessionId: ea82060e-3d51-44bc-a470-e61162514715
---
# Robin PubMed 每日 Digest

**狀態**：✅ 全功能上線（2026-04-23 PR #84 修 OA 誤標 — 加 Europe PMC 鏡像 + digest render 誠實化）

## 做什麼

每日台北 05:30 從 PubMed RSS 抓最新論文 → LLM curation 挑 10-15 篇 → NEJM 編輯 persona 六維度評分 → **對每篇嘗試下載 OA 全文 PDF** → 寫進 Obsidian vault：
- `KB/Wiki/Digests/PubMed/YYYY-MM-DD.md` — 每日精選頁，每篇顯示 📄 / ⚠️ / ❌ 狀態
- `KB/Wiki/Sources/pubmed-{pmid}.md` — 每篇獨立 source 頁，OA 論文附「📖 開啟 Robin reader（本機）」link
- `KB/Attachments/pubmed/{pmid}.pdf` — OA 全文（PMC + Unpaywall 兩層 fallback）
- `KB/Wiki/Sources/pubmed-{pmid}-bilingual.md` — 雙語閱讀版（本機 reader 觸發才產）

## 全文下載（PR #70、PR #84 擴充）

**四層 fallback**（PR #84 2026-04-23 加入 Europe PMC）：
1. NCBI efetch XML → 抓 DOI + PMCID
2. 有 PMCID → PMC NCBI `/pdf/`（**注意**：2024+ 常回 HTML landing page，實務上通常失敗）
3. PMC NCBI 失敗 → Europe PMC 鏡像 `europepmc.org/articles/PMC{id}?pdf=render`（直接回 `application/pdf`，不需過 publisher IdP cookie flow，**VPS 友善**）
4. 還失敗 + 有 DOI → Unpaywall API `best_oa_location.url_for_pdf`（注意：Nature 回的 URL 要過 `idp.nature.com` cookie flow，VPS 固定 IP 常被擋）
5. 都失敗但有 DOI → `needs_manual`（digest 顯示 ⚠️ + DOI link）
6. 無識別碼 OR 兩條 PMC 都掛+無 DOI → `not_found`（note 會區分兩種情況）

Source 頁新增 frontmatter 欄位（Dataview 可查）：`doi` / `full_text_status` / `full_text_source` / `full_text_path` / `read_status`。

關鍵 env：`UNPAYWALL_EMAIL`（fallback 到 `NOTIFY_TO`），`PUBMED_API_KEY` 可把 NCBI rate limit 從 3/s → 10/s。

實測：首日 3 篇 sample 一篇 Unpaywall 成功下載、兩篇 needs_manual — 符合學術 OA 比例。

## 雙語閱讀整合（PR #71）

新 endpoint `/robin/pubmed-to-reader?pmid=XXX`：
- 檢查 `KB/Wiki/Sources/pubmed-{pmid}-bilingual.md` → 已翻直接跳 reader（short-circuit 省 LLM $）
- 沒翻 → `parse_pdf` (pymupdf4llm) → `translate_document` (Claude Sonnet + 台灣術語表) → 寫 bilingual.md → 跳 reader
- 翻譯失敗 fallback raw markdown

reader（`/read`、`/save-annotations`、`/mark-read`）新加 `base=inbox|sources` 白名單參數，讓雙語版和原 source 頁並列同一目錄。

**設計 deviation**：原 design doc 寫 Docling，實作時改用現有 pymupdf4llm（shared/pdf_parser.py 已在用）— VPS 3.8GB RAM 撐不住 Docling 的 torch + transformers 重 deps。Docling 保留為未來品質升級選項。

## 關鍵檔案

- `agents/robin/pubmed_digest.py` — pipeline 主體
- `agents/robin/pubmed_fulltext.py` — PMC + Unpaywall 全文下載
- `shared/pdf_parser.py` — pymupdf4llm + pdfplumber 表格
- `shared/translator.py` — Claude Sonnet + 台灣術語表
- `thousand_sunny/routers/robin.py` — `/pubmed-to-reader` + `/read base=`
- 入口：`python -m agents.robin --mode pubmed_digest [--dry-run]`
- Cron：`30 5 * * *` 於 VPS（Asia/Taipei TZ）

## 成本

- Digest：Sonnet 4.6，120 候選 → 12 精選 ≈ **$0.35/日 ≈ $10/月**
- 全文下載：免費（NCBI / Unpaywall API）
- 雙語閱讀：Claude Sonnet 每篇 5-15 萬字 ≈ **$0.3-1/篇**（按需觸發，已翻不重翻）

## 相關 PR

- #66 feature（digest）
- #67 filename timezone fix（Asia/Taipei 而非 UTC）
- #68 cron TZ fix（VPS Asia/Taipei）
- #69 fix：pubmed_digest 改用 `llm.ask()` 支援 Gemini model（VPS `MODEL_ROBIN=gemini-2.5-pro`）
- #70 feat：OA 全文自動下載
- #71 feat：雙語閱讀整合（本機 Robin reader）
- #84 fix：OA 誤標為「無法取得」— 加 Europe PMC fallback + render 誠實化 + log 升 warning（2026-04-23，用 PMID 42014402 Nature Commun 實測驗證）

## 下個迭代

- Phase 2：annotation 結束後自動濃縮回 `pubmed-{pmid}.md` 「我的筆記」區塊
- 品質觀察：若 pymupdf4llm 對多欄/公式 PDF 走樣，升級 Docling 或 BabelDOC
- 週報 / 電子報由 Brook 消化 vault digest（依賴 Brook style extraction）
