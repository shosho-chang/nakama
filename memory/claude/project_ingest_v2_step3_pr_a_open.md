---
name: Step 3 PR A #169 open + ultrareview 跑中 + 下次 pickup 點
description: PR A 已 push + ultrareview 在跑；修修 manual todo（E2E + Web UI flow + apply broken page migration）；PR B/C/D 範圍與起跑順序
type: project
originSessionId: 27c0b340-d612-4f47-aba4-b4f3727267fd
---
2026-04-26 EOD pickup 點：Step 3 PR A 開好，剩 PR B/C/D + 修修 manual。

## PR A 狀態

- **PR #169** `feat(ingest): Step 3 — kb_writer aggregator + Robin v2 4-action dispatcher (ADR-011)`
- **Commit**：`26ec74f`
- **Branch**：`feat/ingest-v2-step3-schemas-kb-writer`
- **Tests**：186 PR-scope tests 全綠（38 schemas + 41 kb_writer + 29 lifeos + 61 ingest + 17 sse + 65 router + 1 long-wikilink regression）；full suite 1856 passed（3 pre-existing failures，跟此 PR 無關，main 也壞）
- **Ultrareview**：launched（session_01QuaoYTonuXTNEeiiRwHFM1）— findings 還沒回來；下次新 session 開頭應 check Slack / GitHub PR notification

## 修修 manual todos（PR A merge 前）

1. **本機 E2E**：`python -m agents.robin`（互動模式）跑一個 KB/Raw 內 source；確認新 concept page = v2 schema；既有 concept update_merge 真的把新內容 merge 進 body 主體（非 `## 更新` block）；`data/kb_backup/` 留 .bak
2. **Web UI E2E**：`python -m thousand_sunny` → 瀏覽器走 `/processing` → `/review-plan` → `/execute`；確認 4-action badge 顯示對；衝突 concept 額外顯示 topic + claims 對照框
3. **Cost warning**：每個 update_merge 是一次 Opus 4.7 LLM call (~5-15k token)；先用 1-2 source 試，不要批量
4. **Apply broken pages migration**（獨立 task，已 merged 但未跑）：
   `python -m scripts.migrate_broken_concept_frontmatter --vault "F:/Shosho LifeOS" --apply`
   會修 4 頁：ATP再合成 / 神經保護作用 / 肌酸代謝 / 膳食補充劑安全性

## 注意事項：working tree state 驗證

session 結束時 system reminder 顯示 `review_plan.html` / `robin.py` / 兩組 router tests 內容是 **v1 schema**，但實際 commit `26ec74f` 是 **v2**。下次 session 開頭應跑 `git status` + `git log -1` + `git diff origin/main..HEAD --stat` verify working tree 跟 PR 一致；reminder 內容疑似 cache stale，git 才是 authoritative。

## Step 3 剩餘工作

### PR B：parse_book walker + Vision describe + chapter-summary（獨立 scope，跟 PR A 無檔案重疊）

按 ADR-011 §3.3 / §3.4 / §6 Acceptance：

1. **改 `.claude/skills/textbook-ingest/scripts/parse_book.py:302-319` `_epub_html_to_text` walker**
   - 處理 `<img>` → 抽 binary 寫 `Attachments/Books/{book_id}/ch{n}/fig-N.{ext}` + text 內占位符 `<<FIG:fig-{ch}-{N}>>`
   - 處理 `<table>` → markdown table walker → 寫 `tab-N.md` + 占位符
   - 處理 `<math>` → MathML → LaTeX inline `$$...$$`（新 dep `mathml2latex>=0.0.5`）
2. **PDF 路徑改用** `pymupdf4llm.to_markdown(with_tables=True)`（A-9）
3. **拿掉** `.claude/skills/textbook-ingest/prompts/chapter-summary.md` 字數上限 + 強制 verbatim quote + 強制 `### Section concept map`
4. **新增 Vision describe step**（Sonnet 4.6 + domain-aware prompt）+ `prompts/vision-describe.md` skeleton（§3.4.3）
5. **改 `IngestPipeline._truncate_at_boundary` (`agents/robin/ingest.py:25`)** 30000 char 一律截斷的限制（A-10）
6. **dep 同步**：`pyproject.toml` + `requirements.txt` 加 `mathml2latex>=0.0.5`（feedback_dep_manifest_sync）
7. **tests**：parse_book walker / Vision describe / chapter-summary 改後

### PR C：重 ingest ch1 + retrieval acceptance

- 重 ingest *Biochemistry for Sport and Exercise Metabolism* Ch.1
- chapter source page (`Sources/Books/biochemistry-sport-exercise-2024/ch1.md`) frontmatter 含 `figures:` list + 每張圖 Vision-described
- 4 ch1 新 concept (`能量連續體` / `糖解作用` / `有氧能量系統` / `無氧能量系統`) page 全 v2 schema
- 6 既有 concept page lazy migrate v1 → v2、body 末尾無 `## 更新（date）` block
- **至少一個 concept**（推薦 `磷酸肌酸系統`）展示 `## 文獻分歧 / Discussion` section（PCr 主導時長 1-10s vs 既有 10-15s）
- **Acceptance**：kb-search 對「PCr 主導時間多長？」query 拿到 `肌酸代謝` page 並命中 `## 文獻分歧` section

### PR D：批 ingest 剩 10 章

- ch2-ch11 批 ingest，每章用 v2 pipeline
- Book Entity status: `complete` + `chapters_ingested: 11`

## 設計依據（不變）

- ADR-011：[`docs/decisions/ADR-011-textbook-ingest-v2.md`](../../docs/decisions/ADR-011-textbook-ingest-v2.md)
- Plan：[`docs/plans/2026-04-26-ingest-v2-redesign-plan.md`](../../docs/plans/2026-04-26-ingest-v2-redesign-plan.md)
- Workflow inventory：[`docs/research/2026-04-26-workflow-inventory.md`](../../docs/research/2026-04-26-workflow-inventory.md)
- 4 原則 + bug status：見 `project_textbook_ingest_v2_design.md` / `project_robin_aggregator_gap.md` / `feedback_kb_concept_aggregator_principle.md`

## 學到的 / 教訓

- **Web UI plan schema 改要連帶改的範圍比 single-file 大**：router (5+ 處) + template + 兩組 tests，要一起 PR 才完整；單獨改 ingest.py 會 break Web UI
- **`git checkout` deny by default**：要 revert tracked file 換 `git restore` 或 PowerShell 回收桶（CLAUDE.md 規則）
- **system reminder 「intentional modified」內容可能是 stale snapshot**：`git diff` 才是 authoritative
- **frozen=True 適用 immutable value object**（FigureRef / ConflictBlock / ConceptAction）；可變 page model（ConceptPageV2 / ChapterSourcePageV2 / MigrationReport）用 `extra="forbid"` + 預設 mutable
