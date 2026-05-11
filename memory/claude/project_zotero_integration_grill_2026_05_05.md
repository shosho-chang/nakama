---
name: Zotero 整合 grill 凍結 (Q1-Q10) 2026-05-05
description: 接 pivot grill 把 Phase B-F 全拍 — sync mechanism / 兩檔 ingest / Reader 整合 / MVP scope；ADR-018 + ADR-019 候選
type: project
created: 2026-05-05
---

接續 [project_session_2026_05_05_zotero_pivot.md](project_session_2026_05_05_zotero_pivot.md) 的 pivot 戰略決定，2026-05-05 同日 grill session 把 [project_zotero_integration_plan.md](project_zotero_integration_plan.md) 的 Phase B-F「待 grill」全拍。

`agents/robin/CONTEXT.md` 同期 lazy-create，含 Robin 詞彙表（Zotero item / attachment / snapshot / sync / Working MD / Bilingual sibling / Raw source page / Annotated source page）。

## 凍結決策 Q1-Q10

| Q | 決策 | 重點 |
|---|---|---|
| **Q1** | Sync 單位 = **Zotero item**（不是 attachment） | 一個 item → 一個 Working MD；attachment 多選一（HTML preferred） |
| **Q2** | HTML snapshot **留 Zotero storage**，vault 只 MD | Zotero 是 archival 原檔；vault 是 derived working copy；不雙存 HTML |
| **Q3** | Snapshot `_assets/` **複製進 vault** | `KB/Attachments/zotero/{slug}/_assets/`；vault 自包含、Obsidian + Reader 都可看 |
| **Q4** | **SQLite 直連**讀 `~/Zotero/zotero.sqlite` | copy-to-tmp 規避 lock；不走 Web API；不上 VPS；本機 only（Win 主、Mac 偶用，修修無 cloud sync） |
| **Q5** | Inbox 落地 + **兩檔 ingest 模式** | sync → `Inbox/kb/{slug}.md`（英文 only）；翻譯 → `{slug}-bilingual.md` sibling；ingest fan-out raw + annotated 兩檔 |
| **Q6** | Naming flat、Sources 落點、re-extract raw / weave annotated | `KB/Wiki/Sources/{slug}.md` + `{slug}--annotated.md`；raw 從 snapshot.html re-Trafilatura（zero-trust）；annotated 從 bilingual + annotations 編織 |
| **Q7** | 觸發 = 貼 **`zotero://` link 單篇** | 仿 URL ingest UX；MVP 不批次、不 cron；Phase 2 加 tag-based batch |
| **Q8** | HTML preferred, **PDF fallback** 進 MVP | 兩個都有 → HTML 贏；只 PDF → pymupdf4llm path（reuse PR #71 module） |
| **Q9** | **自家 translator 維持**，不切沉浸式翻譯 / Pro | 單篇 paper 短文無跨章節飄移，Pro Smart Context 邊際提升低；架構鎖死自家 bilingual format；Pro 訂閱是修修 EPUB 軸個人決策（PR #376 grill），跟 nakama Zotero 流程 decouple |
| **Q10** | MVP scope = **兩檔 fan-out only**，concept extraction Phase 2 | 觀察 #3 不在這條 MVP；annotation 暫時只活在 annotated source page，不直接 inform Wiki Concepts |

## Re-sync 默認行為

第二次貼**同 item link** → **skip**，return existing inbox path（仿 `inbox_writer.find_existing_for_url()`，但比對 frontmatter `zotero_item_key` 不是 `original_url`）。Phase 2 加 explicit 「re-sync」按鈕（force-update metadata + re-extract）。

## Annotation slug 天然 merge（不需 amend ADR-017）

`shared/annotation_store.annotation_slug()` (line 55) **frontmatter title 優先 derive**，bilingual sibling 是 source frontmatter 直接 copy（`thousand_sunny/routers/robin.py:713`）→ raw + bilingual 共享 title → 同 annotation slug → annotations 落 `KB/Annotations/{slug}.md`。

## MVP slice 切法

三個 vertical slice：

**Slice 1 — HTML happy path 端到端**
- 新模組：`agents/robin/zotero_reader.py`（SQLite 讀 item + attachment metadata + URI 解析）
- 新模組：`agents/robin/zotero_assets.py`（`_assets/` 複製 + image src rewrite 為 vault 相對）
- 擴 `agents/robin/url_dispatcher.py`：detect `zotero://` URI → Zotero path
- 擴 `agents/robin/inbox_writer.py`：Zotero frontmatter（`zotero_item_key` / `zotero_attachment_path` / `attachment_type`）
- 擴 Inbox UI form：accept `zotero://` link
- **Acceptance**：貼 Nature paper `zotero://` link → Inbox 出英文 MD + 圖（vault 相對路徑可見）；翻譯按鈕產 bilingual sibling；annotation works

**Slice 2 — PDF fallback**
- 擴 `zotero_reader.py`：HTML missing fall back to PDF
- 擴 `zotero_assets.py`：pymupdf 圖檔抽取到 `_assets/`
- **Acceptance**：貼只有 PDF attachment 的 item link → 走 pymupdf4llm path → Inbox MD + 抽出的圖

**Slice 3 — 兩檔 ingest fan-out**
- 新 endpoint `/zotero-ingest/{slug}` 或 extend 既有 ingest button
- Re-extract from `snapshot.html`（or PDF）for raw source page
- Weave annotations into bilingual for annotated source page
- Write both to `KB/Wiki/Sources/`，frontmatter cross-link
- **Acceptance**：annotate 完按 ingest → `Sources/{slug}.md` + `{slug}--annotated.md` 兩檔出現，frontmatter 互指；Wiki Concepts/Entities **不**自動更新（Phase 2 才接）

## ADR 候選（grill 後落地）

- **ADR-018 — Zotero as Primary Ingest Path**：戰略性 pivot 凍結（hard-to-reverse + surprising-without-context + real-trade-off 全中）
- **ADR-019 — Two-file Source Ingest Pattern**：raw + annotated 兩檔 fan-out 模式（適用 Zotero + 將來 EPUB + 其他 source）

## Phase 2 backlog（不在 MVP）

- **觀察 #3 真正解**：concept/entity extraction 改吃 annotated 當 input — 等修修跑過 3-5 篇 Zotero paper 真實流程後再 grill
- **Tag-driven batch sync**：Zotero tag `to-read-nakama` → sync many；Q7 alternative
- **Inbox lifecycle**：30 天 auto-archive policy
- **Force re-sync 按鈕**：覆寫 metadata + re-extract
- **Annotation 回沖 Zotero**：plan 原列「MVP 單向」，但修修 annotate 在 Reader 不在 Zotero，回沖 ROI = 0；除非 Phase 2 想「Zotero PDF reader 內 annotate 也帶回 KB」才開
- **Pro 評估**：MVP 跑 3-5 篇 Zotero paper 後若品質不滿意，grill「自家升 EPUB plan §4 級別 2」vs「評估 Pro 對單篇 paper 的邊際提升」

## 相關

- [agents/robin/CONTEXT.md](../../agents/robin/CONTEXT.md) — Robin 詞彙表（Zotero terms）
- [project_zotero_integration_plan.md](project_zotero_integration_plan.md) — pivot 後 plan
- [project_session_2026_05_05_zotero_pivot.md](project_session_2026_05_05_zotero_pivot.md) — pivot 決定來源
- [feedback_dont_recompete_on_capture_quality.md](feedback_dont_recompete_on_capture_quality.md) — 戰略 lesson
- [project_bilingual_reader_design.md](project_bilingual_reader_design.md) — Reader 端設計（PR #354 已 ship）
- [docs/decisions/ADR-017-annotation-kb-integration.md](../../docs/decisions/ADR-017-annotation-kb-integration.md) — annotation store schema
- [docs/plans/2026-05-05-epub-book-translation-grill-prep.md](../../docs/plans/2026-05-05-epub-book-translation-grill-prep.md) — EPUB 軸 grill prep（decouple 軸線）
