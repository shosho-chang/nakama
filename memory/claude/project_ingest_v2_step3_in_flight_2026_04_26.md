---
name: ingest v2 Step 3 — PR A + B merged 2026-04-26，PR C 必先修 4 個 silent corruption bug
description: PR #169 (A) merged 33f3095 / PR #178 (B) merged d955af6；4 個 walker silent corruption bug 必修 before PR C
type: project
originSessionId: 211fa78f-698e-45a6-9e46-142599efead2
---
2026-04-26 17:50 台北 sweep：PR A + PR B 都 reviewed + squash merged。PR C/D 解鎖前必修 PR B 的 4 個 silent data corruption bug。

## PR A — #169 (kb_writer + Robin v2 dispatcher) — MERGED 33f3095

- **Branch**：`feat/ingest-v2-step3-schemas-kb-writer`（已刪）
- **Merge commit**：`33f3095`（squash merged 2026-04-26）
- **Final commit on branch**：`4d4ab4e`
- **Review verdict**：READY TO MERGE — 9 findings 全 fixed + 168 test pass
- **2 minor 非 blocker（可順手在 follow-up PR 修）**：
  - noop branch 沒 normalize body 到 v2 H2 skeleton（`shared/kb_writer.py:660-691` 沒 call `_ensure_h2_skeleton(body)`）— v1 → v2 first noop 會 schema_version=2 但缺 canonical sections
  - cosmetic — noop write redundancy on first-noop-after-derive（最多一次冗餘 write per v1 page）

### Ultrareview 9 findings 全修（2026-04-26）

5 normal-severity：

1. **CRITICAL prompt 目錄錯誤** — runtime 從 `prompts/robin/` 讀，PR A 改的是 dead path `agents/robin/prompts/extract_concepts.md`。修法：複製 v2 prompt 到 `prompts/robin/extract_concepts.md` + 5 categories（textbook/research/clinical_protocol/narrative/commentary）；刪 dead 檔。否則第一次 ingest 會 `KeyError: 'existing_pages'` 炸
2. **Path traversal slug** — `upsert_concept_page` slug 沒驗證；LLM-emitted `../../../tmp/poc` 寫到 vault 外。加 `_validate_slug` regex (alphanumerics + CJK + dash + underscore)
3. **update_conflict 不 idempotent** — 重複 call 同 (topic, source_link) body 重複 append。`if new_md.strip() not in body` gate 在 `_append_to_section` 前
4. **`_ensure_h2_skeleton` 丟非 canonical H2** — 第一次 update_merge silently 刪用戶內容。Mirror `_append_to_section` leftover-H2 preservation loop
5. **noop 在 v1 page 沒 strip 舊 ## 更新 block** — schema_version=2 寫回去 body 仍含 v1 block 永遠卡住。noop branch 加 `_strip_legacy_update_blocks` + write whenever migration/strip/new source_link

5 nits：

- bug_003 chapter list lex order ch10<ch2 → sort by extracted int
- bug_020 conflict validation 跑非 conflict actions silently drop create → gate `action == 'update_conflict'`
- merged_bug_016 `aggregate_conflict` / `update_mentioned_in` 漏 v1→v2 migration + `aggregate_conflict` 漏 mentioned_in append
- merged_bug_006 noop 沒進 progress count + `/done` summary → split writes vs referenced bucket + done.html 加 referenced section
- merged_bug_011 confidence migration silent coerce bool/unknown string → exclude bool from int + log unknown-string drops

**Tests**：+18 cases（slug validation / conflict idempotency / h2 preservation / noop strip / chapter sort / conflict gate / aggregate migration + backlink / confidence edge cases）。**279 PR-scope passing**。

### 修修 manual todos（PR A merge 前，仍未跑）

1. 本機 E2E：`python -m agents.robin` 跑一個 KB/Raw 內 source；確認新 concept v2 schema、既有 concept update_merge LLM 真有 merge 進 body 主體（非 `## 更新` block）；`data/kb_backup/` 留 .bak
2. Web UI E2E：`/processing` → `/review-plan` → `/execute`；確認 4-action badge + conflict topic + 新 referenced bucket
3. Apply broken pages migration（PR #164 已 merged 但 vault 沒 apply）：
   `python -m scripts.migrate_broken_concept_frontmatter --vault "F:/Shosho LifeOS" --apply`
   修 4 頁：ATP再合成 / 神經保護作用 / 肌酸代謝 / 膳食補充劑安全性

## PR B — #178 (parse_book walker + Vision + chapter-summary v2) — MERGED d955af6

- **Branch**：`feat/ingest-v2-step3-pr-b-parse-book`（已刪 + worktree 殘留 dir 在 `.claude/worktrees/ingest-v2-pr-b-parse-book/`，PowerShell delete 失敗 — file in use；下次手動清）
- **Merge commit**：`d955af6`（squash merged 2026-04-26）
- **Final commit on branch**：`d8b6e84`
- **Review verdict**：MERGE WITH FOLLOW-UP — 4 silent data corruption bug 必修 before PR C re-ingest

### **🔴 4 個 silent data corruption bug — 必修 before PR C**

1. **`_html_table_to_markdown` 忽略 rowspan/colspan** — `parse_book.py:478`
   - `<td rowspan=2>A</td><td>B</td>...<td>C</td>` 渲染後 `C` 在錯欄
   - 常見於藥理 / 代謝 / 實驗值表 — 高頻打到
2. **`_html_table_to_markdown` 遞迴抓 nested `<tr>`** — `parse_book.py:478`
   - `find_all("tr")` 預設 recursive；巢狀 table 把內層 row 吸到外層
   - 修法：filter `tr.find_parents("table")[0] is table_tag`
3. **always treats `rows[0]` as header** — `parse_book.py:491`
   - 無 `<thead>` 的 table（很多 EPUB 的標準寫法）silent 把第一個資料 row 當 header
   - 第一行 data 永遠丟失 — 高頻打到
4. **`<mfrac>` 無 alttext fallback collapse 數字** — `parse_book.py:526`
   - `<math><mfrac><mn>1</mn><mn>2</mn></mfrac></math>` 無 alttext → `$$12$$`（應 `$$\frac{1}{2}$$`）
   - modern EPUB 通常有 alttext 所以 low risk，但 fallback 路徑該補 mfrac/msub/msup/msqrt 走訪

### Minor 也可順手修

5. SKILL.md 重複 `7.` 編號（line 269/273），應該是 `8.`
6. SKILL.md 寫「5 sub-steps」但實際 7 條
7. `vision-describe.md` references nonexistent `figures[].path`（walker `_figure_to_dict` 沒 emit `path` key）
8. `_export_chapter_attachments` 沒 validate `ref` for path traversal（今天 surface=0 但 future-proof 加防護）

### PR B 改了什麼

- `parse_book.py` `_walk_epub_html` walker：`<img>` / `<svg>` / `<figure>` → `ChapterFigure`，`<table>` → `ChapterTable` markdown，`<math>` → inline `$$LaTeX$$`
- `_pdf_chapter_markdown` 用 `pymupdf4llm.to_markdown(with_tables=True)` (A-9)
- `_pdf_chapter_figures` 用 xref dedup + `extract_image()`，append `## Figures` 區塊到章末
- 新 CLI flag `--attachments-base-dir` → 寫 `{base}/ch{n}/{ref}{ext}` + `{base}/ch{n}/{ref}.md`
- Outline JSON 含 `figures: [{ref, extension, alt, caption, tied_to_section, placeholder}]` + `tables: [{ref, caption, tied_to_section, placeholder}]`
- `chapter-summary.md` v2：拿掉「每節 300-500 字」字數上限（A-4）+ 強制 verbatim quote + 強制 Section concept map
- 新 `vision-describe.md` prompt skeleton（domain-aware system role；Sonnet 4.6 default）
- SKILL.md Step 4 拆 7 sub-step（4b Vision describe + 4d/4e v2 4-action dispatcher）
- 移除 `_truncate_at_boundary(content, 30000)` no-op call (A-10)

### Tests

22 walker tests + full suite 1843 passing（3 pre-existing failures 跟 PR B 無關）

### 一個 ADR-011 deviation

ADR §3.4.1 寫 `mathml2latex>=0.0.5` PyPI dep — 那 package 0.1.0 是 abandoned（`__init__.py` 只有 `__version__ = '0.1.0'`，無 public API）。改走 alttext-first：`<math alttext="\\frac{1}{2}">` → fallback to text content。不加 dep。`_html_math_to_latex` docstring + PR description 都標 deviation。Signature 不變，未來可換 maintained converter（見 `feedback_mathml2latex_abandoned.md`）。

## PR C / D backlog（PR A + B merged，但 PR C 必先修 4 corruption bug）

### PR C：重 ingest ch1 + retrieval acceptance

- 重 ingest *Biochemistry for Sport and Exercise Metabolism* Ch.1（用 v2 walker + Vision describe + 4-action dispatcher）
- chapter source page frontmatter 含 `figures: [...]` + 每張圖 Vision-described
- 4 ch1 新 concept 全 v2 schema
- 6 既有 concept page lazy migrate v1 → v2、body 末尾無 `## 更新` block
- **至少一個 concept**（推薦 `磷酸肌酸系統`）展示 `## 文獻分歧 / Discussion` section（PCr 主導時長 1-10s vs 既有 10-15s）
- **Acceptance**：kb-search 對「PCr 主導時間多長？」query 拿到 `肌酸代謝` page 並命中 `## 文獻分歧` section

### PR D：批 ingest ch2-ch11

- 10 章批 ingest，每章用 v2 pipeline
- Book Entity status: `complete` + `chapters_ingested: 11`

## 完整 reference

- ADR-011：[`docs/decisions/ADR-011-textbook-ingest-v2.md`](../../docs/decisions/ADR-011-textbook-ingest-v2.md)
- Plan：[`docs/plans/2026-04-26-ingest-v2-redesign-plan.md`](../../docs/plans/2026-04-26-ingest-v2-redesign-plan.md)
- Workflow inventory：[`docs/research/2026-04-26-workflow-inventory.md`](../../docs/research/2026-04-26-workflow-inventory.md)
- 4 原則 + bug status：見 `project_textbook_ingest_v2_design.md` / `project_robin_aggregator_gap.md` / `feedback_kb_concept_aggregator_principle.md`

## 學到的 / 教訓

- **Ultrareview 對 PR A 抓出 5 normal-severity blockers** — 「prompt 目錄錯誤」是修修明顯沒注意到的 critical（runtime 載入路徑 vs 設計文件提到的路徑分歧）
- **Stacked PR 安全做法**：PR B base on main 而非 PR A 分支，避免 squash merge 後 PR B 變 unmergeable（feedback_stacked_pr_squash_conflict）；只要 hunks 不重疊就能各自 merge
- **多 worktree 並行修法**：PR A worktree 動 fixes，PR B worktree 動 walker，互不干擾；修完 PR A worktree 用 `git worktree remove` 清掉
- **mathml2latex PyPI 包 abandoned** — 動 dep 前 `pip install` + 實測 API 才能信任 ADR 列的版本（見 `feedback_mathml2latex_abandoned.md`）
