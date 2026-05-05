---
name: epub_reader_prd_2026_05_05
description: 翻頁版雙語 EPUB Reader + Annotation/Comment + 教科書 ingest 串接 — PRD #378 + 5 slice issues #379-#383 ship with sandcastle execution plan；下次起手 /tdd #379
type: project
---

**EPUB Reader 全規劃凍結 2026-05-05**

修修要看英文書（Kahneman / Ericsson 類）；現有 markdown scroll reader 不適合。整套 plan 已 grill → PRD → to-issues 三段走完。下次 session 起手 `/tdd #379` HITL 寫 failing tests，然後 sandcastle/manual 收。

## Why
Line 2 讀書心得 critical path 卡 Stage 2 翻譯品質，已決定**外包 Immersive Translate**（速度 + 成本可接受）；剩下卡點是閱讀 + ingest 串接 — 砍下這塊 Line 2 完整跑通。

## How to apply
**下次 session 第一句**：「跑 `/tdd #379` 從 Slice 1 起手」。我跟修修一起定 4 個 deep modules 的 public interface + 寫第一個 failing test，commit `tdd-prep/slice-1` branch，然後 sandcastle 收 1A/1B/1C，manual subagent 收 1D。

---

## 凍結的關鍵決策（grill 9 題）

1. **Reader engine**：[foliate-js](https://github.com/johnfactotum/foliate-js)（MIT，活躍維護）+ 讀 [readest FoliateViewer.tsx](https://github.com/readest/readest/blob/main/apps/readest-app/src/app/reader/components/FoliateViewer.tsx) 當 reference（**不嵌入 readest，AGPL 污染**）；epub.js [社群正在出走](https://github.com/futurepress/epub.js/issues/1406)
2. **雙頁版面**：A1 書本式對開 paginate + 段落 EN+ZH 堆疊（每英文段後接 `> blockquote` 中譯）
3. **書來源結構**：直接 render bilingual EPUB（不轉 markdown）；ingest 吃另一份 EN 原檔
4. **Annotation 三型**：Highlight（純螢光）+ Annotation（短 footnote 錨 CFI）+ Comment（章級 500-2000+ 字 prose 錨 chapter_ref）
5. **Annotation 儲存**：1 file per book = `KB/Annotations/{book-slug}.md` schema_version=2；Comment 跟 H/A 同檔（items list 三型 union）；frontmatter `book_version_hash` + 每 item 也記 hash（雙寫）；ADR-017 直接擴不立新 ADR
6. **Page memory**：DB（state.db `book_progress` table）不 vault；5s debounce + visibility hidden flush；多欄（last_cfi / last_chapter_ref / last_spread_idx / percent / total_reading_seconds）；CFI 失效降級 chapter ref；localStorage 後援
7. **Ingest 串接**：兩份 EPUB（`data/books/{id}/original.epub` + `bilingual.epub`）；reader 按鈕寫 `book_ingest_queue` row；修修 Mac 跑 `/textbook-ingest --from-queue` skill 自動讀 queue + 取 EN 原檔；無 EN 原檔 ingest 鈕 disabled
8. **KB sync**：annotation → Concept page `## 讀者註記` section（attribution「from {book_id}」）；comment → `KB/Wiki/Sources/Books/{book_id}/notes.md` 按章分組；comment **不** sync Concept page（避免 aggregator 污染）
9. **Slice 拆解**：5 vertical slice tracer-bullet；Slice 1 無 blocker / 2 / 3 / 4 並行依賴 1 / 5 依賴 2+4

## Out of scope（已凍結不做）
- 翻譯品質升級（grill prep doc Q1-Q12 全 superseded — 外包 Immersive Translate）
- Multi-user / websocket sync / mobile UX polish / cover 抽取進階 / fuzzy re-anchor 演算法 / annotation export / multi-lang pair / inline CSS sanitize 進階

## 三大 footgun（Slice 1 必處理）
1. **CSP `script-src 'self'`** 對 `/books/*` route 強制 — EPUB 是任意 HTML+JS、`<script>fetch('/api/me/secrets')</script>` 偷 cookie 攻擊面
2. **EPUB sanitize**（`<script>` + `on*` event handler）upload 時強制
3. **foliate-js git submodule pin commit hash** — 不准 `--remote` auto track（README 自承 expect to break）；`vendor/foliate-js-patches/` 目錄存救命包

---

## GH issues ship 結構

| Issue | Title | 狀態 |
|---|---|---|
| [#378](https://github.com/shosho-chang/nakama/issues/378) | PRD parent | ready-for-human |
| [#379](https://github.com/shosho-chang/nakama/issues/379) | Slice 1 — Reader tracer bullet | ready-for-agent，無 blocker，**下次起手** |
| [#380](https://github.com/shosho-chang/nakama/issues/380) | Slice 2 — Annotation/Highlight/Comment | blocked by #379 |
| [#381](https://github.com/shosho-chang/nakama/issues/381) | Slice 3 — Page memory | blocked by #379 |
| [#382](https://github.com/shosho-chang/nakama/issues/382) | Slice 4 — Ingest queue | blocked by #379 |
| [#383](https://github.com/shosho-chang/nakama/issues/383) | Slice 5 — KB sync | blocked by #380 + #382；先解 #367 |

每 issue body 含 `## Sandcastle execution plan` 細表，sub-step 清楚標 sandcastle vs single-worktree。

## Sandcastle execution 細部（每 slice 內）

| Slice | Sandcastle 步（≤300 LOC / 1-3 files） | Manual single-worktree 步 |
|---|---|---|
| 1 | 1A epub_sanitizer / 1B epub_metadata / 1C book_storage+schemas+books migration | 1D Web layer + foliate-js submodule + CSP middleware + chassis-nav + browser smoke |
| 2 | 2A schemas/annotations v2 / 2B annotation_store v1+v2 dispatch / 2C GET/POST API | 2D Reader popup UI 三型輸入 + render annotations on load |
| 3 | 3A book_progress migration+schema / 3B GET/PUT progress API | 3C Reader JS debounce+restore+localStorage（含 multi-tab simulation） |
| 4 | 4A book_queue+migration / 4B POST ingest-request / 4C queue_processor.py CLI + SKILL.md | 4D Reader ingest 鈕 + 書架 status badge |
| 5 | 5A annotation_merger v2 dispatch / 5B book_notes_writer + vault_rules | — 無 UI step |

**Sandcastle 13 步 × ~$0.30 ≈ $4**
**Manual single-worktree 4 步 × ~$1.5 ≈ $6**
**Total ~$10-12**

## Workflow per slice（重複適用）

```
1. HITL /tdd #<slice-id>
   - 跟修修一起定 module public interface
   - 寫 failing tests for 全部 sandcastle 步
   - commit tdd-prep/slice-N branch

2. AFK sandcastle（每 sandcastle 步一輪）
   gh issue edit <step-id> --add-label sandcastle
   cd E:/sandcastle-test && MSYS_NO_PATHCONV=1 npx tsx --env-file=.sandcastle/.env .sandcastle/main.mts
   → agent 拉 issue → 讀 failing tests → 寫 prod code 直到 GREEN → commit + auto-PR (Closes #N)
   → 主機 squash merge

3. Manual single-worktree（UI 步）
   git worktree add ../nakama-slice-N-ui
   → 我接手 worktree-isolated subagent + Playwright MCP browser smoke
   → open PR + /review + squash merge

4. parent issue 全 step PR merged → close → 進下一 slice
```

## Pre-req for Slice 5
**先解 [#367](https://github.com/shosho-chang/nakama/issues/367) P0 sync invalid JSON bug** — annotation_merger 既有 bug 在 v2 path 同樣會踩。Slice 5 開動前 unblock。

## 模組架構（深 vs 淺）

**深模組**（pure function / IO + side effects、簡單介面、testable in isolation）：
- `shared/schemas/annotations.py`（擴 ADR-017 v2）+ `shared/schemas/books.py`
- `shared/book_storage.py` / `shared/annotation_store.py`（擴）/ `shared/book_queue.py`
- `shared/epub_sanitizer.py` / `shared/epub_metadata.py`

**薄 wrapper**（業務邏輯 delegate 給深模組）：
- `thousand_sunny/routers/books.py`
- `book_reader.html` / `books_library.html` / `book_upload.html`

**Vendor**：`vendor/foliate-js/` git submodule + `vendor/foliate-js-patches/`

**KB sync layer**：`agents/robin/annotation_merger.py`（擴）+ `agents/robin/book_notes_writer.py`（新）

**Skill 改動**：`.claude/skills/textbook-ingest/SKILL.md` `--from-queue` mode + `scripts/queue_processor.py`

## 相關連結
- Pre-grill plan：[docs/plans/2026-05-05-epub-book-translation-grill-prep.md](../../docs/plans/2026-05-05-epub-book-translation-grill-prep.md)（翻譯軸線，superseded）
- ADR-017：[docs/decisions/ADR-017-annotation-kb-integration.md](../../docs/decisions/ADR-017-annotation-kb-integration.md) — 待擴 v2 amendment
- ADR-011：[docs/decisions/ADR-011-textbook-ingest-v2.md](../../docs/decisions/ADR-011-textbook-ingest-v2.md) — Slice 4 引用
- Sandcastle runbook：[docs/runbooks/sandcastle.md](../../docs/runbooks/sandcastle.md)
- 現有 reader template：`thousand_sunny/templates/robin/reader.html`（markdown reader，**不重用**；新建 `book_reader.html`）

## 遺留小事
- repo 根目錄 6 個 `.tmp-*.md` 暫存檔（PRD + 5 slice）；下次 session 修修方便時用 PowerShell 回收桶清掉
