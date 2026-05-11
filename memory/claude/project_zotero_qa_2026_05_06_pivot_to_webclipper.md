---
name: 2026-05-06 evening Zotero QA → 戰略 pivot 到 Obsidian Web Clipper（Zotero 全砍）
description: 三 PR Zotero 整合（Slice 1/2/3）QA 驗 14 finding；finding ⑭ 放棄 PDF 翻譯、finding ⑮ pivot Web Clipper 並全砍 Zotero；修修拍板：(a) 砍 Zotero 相關 code 不動 IngestPipeline / UI 走漸進清理 A / Ingest 一致性進 grill
type: project
created: 2026-05-06
---

延續 5/5 evening 卡掉的 Zotero QA。本場 2.5 小時跑完 Slice 1+2+部分 3，14 個 finding 浮出，**收成兩個戰略決定 + 一場待開 grill**。

## TL;DR

- **Zotero 整合全砍**：ADR-018/019 + Slice 1/2/3 三 PR 全部 revert / 程式碼刪除（修修拍板，喜歡簡潔）
- **Pivot：Obsidian Web Clipper（Chrome plugin）成 canonical paper ingest 路徑** — 已驗證 output 比 Zotero 兩條路徑都乾淨
- **PDF 翻譯路徑放棄**：「現在只要有 PDF，就一定有 HTML」原則，PDF 軸不再投資
- **IngestPipeline (legacy /start) 暫不動**，連同 ingest 行為一致性問題進 grill session
- **Reader UI 走漸進清理（option A）**，等 grill 結束再考慮整套重畫

## 兩個戰略決定

### Finding ⑭ — 放棄 PDF 翻譯路徑

**修修原話**：「現在只要有 PDF，就一定會有可以得到 HTML 的全文。我不認為現在值得在 PDF 上面花時間，這件事情太困難了。」

**現實 PDF 痛點（QA 中觸發）**：
- Page header running title（"Dietary Supplements for Endurance Performance and Core Temperature in the Heat"）插在文章中間 → pdfplumber 當 body 抓
- 頁碼（`2354` / `2361`）散落段落間 → 段落被切碎
- 表格 90 度 rotated landscape → column/row 全亂
- 段落數爆炸（PDF 89K char → 308 段，HTML 等量內容只 30 段）
- Figure 只有 `> picture [WxH] intentionally omitted <` placeholder（finding ④）
- Reference list 翻譯後破壞引用可追溯

**含義**：
- Zotero Slice 2 PDF fallback path（PR #398）剛 ship 即 deprecated
- PDF figure markdown 接合（finding ④）、rotated table 處理（finding ⑥）、translation skip table/refs（finding ⑪）**全部 moot 不開單**
- 未來凡 paper 抓 HTML，**HTML 不可得才回頭考慮 PDF**（待議：Sci-Hub / Unpaywall / publisher HTML fallback）

### Finding ⑮ — Pivot Obsidian Web Clipper，砍 Zotero 整合

**驗證對比**（同一篇 *The Effect of Dietary Supplements on Endurance Exercise...*）：

| 維度 | Zotero PDF (`zotero-uqn9qgu6.md`) | **Web Clipper** (`The Effect of...md`) |
|---|---|---|
| Frontmatter | source_type, fulltext_layer, attachment_path | **title / source URL / author wikilinks `[[Peel]]` / published / tags=clippings** |
| Section 結構 | 308 段散亂 | **`## Abstract → ### Background/Objectives/Methods/Results/Conclusion → ## 1 Introduction → ## 2 Methods`** |
| Reference | 散在 body | **markdown footnote `\[[^1]\]` Obsidian 原生** |
| 化學/數學記號 | 平文字 | **`NO <sub>3</sub><sup>−</sup>` HTML preserved**, italic 保留 |
| 圖 | placeholder marker | 圖原檔 URL（可後處理本地化） |
| Page header / 頁碼污染 | 是 | **無** |
| 翻譯插件 DOM injection | 是（finding ②）| **無**（直接 publisher HTML） |
| 表格 | 88 pipe 但亂 | 缺（兩條都缺，但 Web Clipper 比較不影響閱讀） |

→ Web Clipper 碾壓。

**為什麼當初「捨棄」這條路？根因不是 Web Clipper 不好，而是「Web Clipper 落地的檔案沒人撿」**：

`project_vault_ingest_flow_drift_2026_04_25.md` 寫得很清楚：
- Web Clipper 檔案會落 `Inbox/kb/` ✅
- 但 **Robin pipeline 沒 wire 來自動接這些檔案** ❌
- 修修以為「這條路不行」、轉去做 Zotero 整合（因為 `zotero://` URI 給程式化觸發點）

**真實 root cause**：缺「Inbox/kb 既有檔 → Robin pipeline」這一塊（小工程，估 1 day 級）。

**Zotero 整合是繞遠路 + 解錯問題**。

## 修修拍板的執行計畫

### 1. 砍 Zotero 相關 code（一個 PR 收掉）

```
agents/robin/zotero_reader.py        刪
agents/robin/zotero_sync.py          刪
agents/robin/zotero_assets.py        刪
agents/robin/zotero_ingest.py        刪

agents/robin/url_dispatcher.py       移除 parse_zotero_uri import + _dispatch_zotero + zotero_root config
agents/robin/inbox_writer.py         移除 find_existing_for_zotero_item + zotero_* frontmatter 欄位
thousand_sunny/routers/robin.py      移除 /zotero-ingest 端點 + Zotero slug 分支 + is_zotero_ready
thousand_sunny/templates/robin/index.html  移除綠色「Ingest to KB」按鈕區塊

agents/robin/CONTEXT.md              整個 Zotero section 砍，保留 Reader-side bilingual 詞彙
docs/decisions/ADR-018-...           標 superseded（reason: Web Clipper pivot 2026-05-06）
docs/decisions/ADR-019-...           標 superseded（同上）
tests/agents/robin/test_zotero_*.py  全刪
tests/.../test_url_dispatcher_zotero.py  砍 zotero 測例
```

vault 端 QA 殘檔送回收桶：`Inbox/kb/zotero-*.md` + `Inbox/kb/zotero-*-bilingual.md` + `KB/Annotations/...雙語閱讀版.md` + `KB/Attachments/zotero/` 整資料夾。

→ 估 **−1500 LOC**。

### 2. Ingest pipeline 不動（grill 後再決定）

**修修明確**：「(a)，只砍 Zotero 相關。Ingest 流程要詳細統整 grill。」

→ `agents/robin/ingest.py` `IngestPipeline`（chunker + map-reduce + concept_page/entity_page）保留現狀。

### 3. UI option A 漸進清理（與砍 Zotero 同 PR）

修修選 A，**等 grill 結束再考慮 option B（重畫）**。內容：
- 移除 Zotero 「Ingest to KB」綠按鈕（自然，配合砍 code）
- 統一 redirect target — 永遠回 inbox 主頁（finding ① POST /scrape-translate first→/ vs short-circuit→/read 不一致）
- Inbox list sibling collapse — `{stem}.md` + `{stem}-bilingual.md` 並存只列一筆 + 翻譯 badge（finding ⑧）
- Reader toggle 3 state → 2 state「譯文 v / 譯文 x」（finding ⑨b 修修建議）
- 修 `譯文 ✓` CSS 沒隱藏英文 bug（finding ⑨a）

→ 估 1 day。

## 待 grill 清單（合併開一場）

1. **Drop PDF translation 路徑**（finding ⑭ 落地）
   - DOI → HTML 來源策略（Sci-Hub / Unpaywall / publisher HTML fallback）
   - 已 ingest 的 PDF-only item 處理
   - 沒 HTML 的舊 paper 怎麼辦
2. **Pivot Web Clipper**（finding ⑮ 落地）
   - 「Inbox/kb 既有檔 → Robin pipeline」如何 wire（小工程，要設計）
   - Web Clipper 對非 publisher 網頁（部落格 / 新聞）的品質？
   - 表格缺失問題（兩條 ingest 都中）
3. **Ingest 行為一致性** — 修修最早提的「Document Reader 與 EPUB Reader ingest 對齊」
   - EPUB：`KB/Wiki/Sources/Books/{book_id}/digest.md` + `notes.md`（chapter-aware）
   - 文章/paper：要對齊到什麼形態
   - 概念/實體抽取（IngestPipeline）要不要納入新 ingest
4. **IngestPipeline 是否打掉重練** — legacy `/start` map-reduce 跟新時代 ingest 形態落差
5. **Reader UI 重畫**（option B）— grill 結束後配合 pivot 做 Claude Design handoff

## 14 個 finding 全表

### 🔴 Strategic（已決定）

| # | 內容 | 決議 |
|---|---|---|
| ⑭ | 放棄 PDF 翻譯路徑 | 確認 |
| ⑮ | Drop Zotero, pivot Web Clipper | 確認 |

### 🟡 Bug / 設計缺陷（漸進清理納入）

| # | 內容 | 處理 |
|---|---|---|
| ① | POST /scrape-translate redirect first→/ vs short-circuit→/read 不一致 | UI option A |
| ⑧ | Inbox list 重複列同篇 raw + bilingual sibling | UI option A |
| ⑨a | Reader「譯文 ✓」CSS 失效，沒隱藏英文 | UI option A |
| ⑨b | Reader toggle 3 state → 2 state（修修觀點 UX）| UI option A |
| ⑬ | 兩個 ingest 按鈕並陳（"Ingest to KB" 綠 + 灰「開始 Ingest」）UX 混淆 | 砍 Zotero 自然解掉 |
| ⑤/③ | `find_existing_for_url` 對 failed row 過度保守（同 URL retry 被擋）| Phase 2 ticket（小） |
| ⑦ | Translator 序列 batch（asyncio.gather 並發機會 — 16 batch 串行 ~25 sec/batch）| Phase 2 ticket |
| ⑩ | annotation slug 含中文「雙語閱讀版」後綴（bilingual frontmatter title 帶這字串）| 看 Web Clipper pivot 後是否還存在 |

### ⚫ Moot — finding ⑭ 後不開單

| # | 原內容 |
|---|---|
| ④ | PDF figures 抽到 disk 但 markdown 沒 reference（pymupdf4llm 與 extract_pdf_figures 沒 wire） |
| ⑥ | 90 度旋轉 mega-table 處理策略（A-F 6 方案）|
| ⑪ | 翻譯應跳過表格 + reference list |

### 🔴 Source pollution（grill 中討論）

| # | 內容 |
|---|---|
| ② | Zotero HTML snapshot 已含瀏覽器翻譯插件注入的中文（違反 ADR-019 zero-trust） |
| ⑫ | 註解對齊問題（reader weave，跨多 paper 的舊 bug）|

### 🟢 Silver lining — 驗證可靠的元件

- Slice 1 wiring (`_dispatch_zotero` short-circuit dispatcher routing) ✅
- Slug derivation `zotero-{itemkey}.md` 新邏輯 ✅
- Placeholder + failed write fail-safe ✅
- Slice 2 PDF parser（pymupdf4llm + 表格抽取 + extract_pdf_figures 抽 disk）✅
- Translator JSON parse fallback（`_translate_one_by_one`）✅

## QA 過程教訓 — dual-window worktree race 重現

`feedback_dual_window_worktree.md` + `feedback_shared_tree_devserver_collision.md` 既有規則沒攔住這次：
- 5/5 evening 是 dual-window git checkout race
- 今天是 dual-window worktree race（另一視窗 worktree HEAD 沒 Zotero wiring，server 從那 cwd 啟，但主 repo 看 file 有 wiring 誤判）

**強化 runbook**（待寫 feedback memory）：
- `netstat` 確認 port 已被佔住時，不能只看 process command line（看不到 cwd）
- 必須額外 verify import path：`python -c "import inspect, agents.robin.url_dispatcher as m; print(inspect.getfile(m))"` 對照預期路徑
- dual-window 開發時 **永遠用獨立 port**（QA option B 走 8001，不動 8000，標準做法）

## 重要 artifacts

- 新 inbox 比對檔（**展示用，pivot 後保留作 reference**）：
  - Web Clipper 版：`E:/Shosho LifeOS/Inbox/kb/The Effect of Dietary Supplements on Endurance Exercise Performance and Core Temperature in Hot Environments A Meta-analysis and Meta-regression.md`
  - Zotero PDF 版：`E:/Shosho LifeOS/Inbox/kb/zotero-uqn9qgu6.md`（隨砍 Zotero PR 一起刪）
- 直接前置 memory：[project_session_2026_05_05_evening_zotero_ci_qa_blocked.md](project_session_2026_05_05_evening_zotero_ci_qa_blocked.md)（QA 卡 race）+ [project_session_2026_05_06_overnight_4issue_sandcastle_ship.md](project_session_2026_05_06_overnight_4issue_sandcastle_ship.md)（早報）
- 被本場 supersede：[project_zotero_integration_plan.md](project_zotero_integration_plan.md) + [project_zotero_integration_grill_2026_05_05.md](project_zotero_integration_grill_2026_05_05.md)
- 觸發 pivot 的 4-25 baseline：[project_vault_ingest_flow_drift_2026_04_25.md](project_vault_ingest_flow_drift_2026_04_25.md)（已寫「Inbox/kb 自動 → Robin pipeline 缺口」， 5/6 才 connect 到 Web Clipper pivot）

## 下次 session 起手

1. **第一個 PR**：砍 Zotero code + UI option A（同 PR） — 範圍清單在「修修拍板的執行計畫」§1+§3
2. **開 grill session** — 議題清單在「待 grill 清單」§1-5；建議用 multi-agent-panel skill（高 stakes 戰略決定，需 Codex/Gemini push-back）
3. ADR-018/019 加 superseded note + 新 ADR draft（grill 結束後寫）

## 主對話 token 用量

最高 ~160K / 1M（16%）— 健康。沒有大量 file Read，主要 spot grep + log tail + SQLite 直查。QA 對話本身就是 diagnose + finding 收集的高價值輸出。
