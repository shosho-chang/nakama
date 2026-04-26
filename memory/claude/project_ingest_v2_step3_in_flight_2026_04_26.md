---
name: ingest v2 Step 3 — PR A/B/180/186 + PR C 全部完成 2026-04-26；PR D 唯一剩 backlog
description: PR #169 (A) / #178 (B) / #180 (walker fix-1) / #186 (walker fix-2) + PR C 重 ingest ch1 全完成；唯一 backlog 是 PR D 批 ingest ch2-ch11
type: project
originSessionId: 211fa78f-698e-45a6-9e46-142599efead2
---
2026-04-26 21:30 台北 sweep：PR A + B + 兩波 walker fix（#180 + #186）+ PR C 全部完成。**唯一 backlog 是 PR D 批 ingest ch2-ch11**（10 章重複 v2 流程，~1.5M token / ~5-8 hr wall time）。

## PR A — #169 (kb_writer + Robin v2 dispatcher) — MERGED 33f3095

- **Branch**：`feat/ingest-v2-step3-schemas-kb-writer`（已刪）
- **Merge commit**：`33f3095`（squash merged 2026-04-26）
- **Final commit on branch**：`4d4ab4e`
- **Review verdict**：READY TO MERGE — 9 findings 全 fixed + 168 test pass
- **2 minor 非 blocker（可順手在 follow-up PR 修）**：
  - noop branch 沒 normalize body 到 v2 H2 skeleton（`shared/kb_writer.py:660-691` 沒 call `_ensure_h2_skeleton(body)`）
  - cosmetic — noop write redundancy on first-noop-after-derive

### Ultrareview 9 findings 全修（2026-04-26）

5 normal-severity：
1. **CRITICAL prompt 目錄錯誤** — runtime 從 `prompts/robin/` 讀，PR A 改的是 dead path
2. **Path traversal slug** — `upsert_concept_page` slug 沒驗證
3. **update_conflict 不 idempotent** — 重複 call 同 (topic, source_link) body 重複 append
4. **`_ensure_h2_skeleton` 丟非 canonical H2** — 第一次 update_merge silently 刪用戶內容
5. **noop 在 v1 page 沒 strip 舊 ## 更新 block**

5 nits：bug_003 / bug_020 / merged_bug_016 / merged_bug_006 / merged_bug_011（全修）

**Tests**：+18 cases，**279 PR-scope passing**。

## PR B — #178 (parse_book walker + Vision + chapter-summary v2) — MERGED d955af6 + follow-up #180 + #186

- 4 silent corruption bug（rowspan/colspan / nested tr / headerless / mfrac）— PR #180 修
- 2 figure-wrapped table + h2/h3 markdown markers bug — PR #186 修
- ADR-011 deviation：`mathml2latex>=0.0.5` PyPI 包 abandoned，改走 alttext-first（不加 dep）
- 22 walker tests + +6 fix-2 tests + 全 walker 45 tests pass

## PR C — 重 ingest Wiley *Biochemistry for Sport and Exercise Metabolism* Ch.1（v2 完整流程）— DONE 2026-04-26 ~21:30 台北

### 執行範圍

針對書本：`F:/Shosho LifeOS/Biochemistry for Sport and Exer - Don MacLaren.epub`
- book_id: `biochemistry-sport-exercise-2024`（沿用 v1 ingest 既有 slug，不改名）
- attachments: `F:/Shosho LifeOS/Attachments/Books/biochemistry-sport-exercise-2024/ch1/`（13 PNG + 2 markdown table；driver 從 walker `ch6/`（nav#6）reindex 到 human `ch1/`）
- chapter source: `KB/Wiki/Sources/Books/biochemistry-sport-exercise-2024/ch1.md`（v1 既有 ch1.md 整份覆蓋 v2 schema）

### 11 個 concept actions（4-action plan）

| Action | 數量 | Pages |
|--------|------|-------|
| **create** | 2 | `葡萄糖丙胺酸循環`（§1.7 anchor）、`肌內三酸甘油酯`（§1.6 anchor，IMTG） |
| **update_merge**（v1 → v2 lazy migrate） | 4 | `ATP再合成`、`肌酸代謝`、`磷酸肌酸能量穿梭`、`肌酸激酶系統` — strip 全部 `## 更新` block + 重組 body + 加 `schema_version: 2` + 加 `mentioned_in:` |
| **update_conflict** | 1 | `磷酸肌酸系統`（教科書 1-10s vs 既有 page 10-15s）— 加 `## 文獻分歧 / Discussion` Topic 1，含分歧根源解釋 + KB anchor 立場 + 訓練設計含意 |
| **noop** | 4 | `能量連續體` / `糖解作用` / `有氧能量系統` / `無氧能量系統`（皆已 v2 shape + 已含 ch1 mentioned_in，零變動） |

### v2 改進（vs v1 ingest 2026-04-25）

- 13 張 figure 全 vision-described（Opus 4.7 multimodal Read in-session；密度高、含 LaTeX 數學公式 + 雙語術語 + 圖表元素逐一對應）
- frontmatter `figures[]` 含 `llm_description` 給 retrieval 反查
- 2 張 table 用 markdown content splice 入 chapter source body
- 每節 verbatim quote 1-2 句（含原書頁碼），方便日後 conflict detection
- 每節 `### Section concept map`（mermaid / nested bullet 三選一）
- chapter source 新增 `## 章節重點摘要 / Chapter takeaways` + `## 關鍵參考數據 / Key reference values`（22 條量化 anchor）+ `## 跨章 / 跨書 連結建議`

### Acceptance smoke-check

- 7 改動 page 全 `schema_version: 2`、0 個 legacy `## 更新` block 殘留、1 個 `## 文獻分歧 / Discussion` section 建立 ✓
- 4 noop page mentioned_in 仍指 ch1（count=2，frontmatter + Sources block） ✓
- query「PCr 主導時間多長」會命中 `磷酸肌酸系統.md` line 68-89 ## 文獻分歧 / Topic 1，含「教科書 anchor 1-10 秒 + 10 秒交棒」vs「綜述 10-15 秒 fade-out 上限」雙立場 ✓
- VPS-side `/kb/research` live 驗證需等 Obsidian Sync 推到 VPS（2-5 min）

### 殘留待清

- worktree dir `F:/nakama/.claude/worktrees/ingest-v2-pr-b-parse-book/`（PowerShell delete 失敗 file in use）— 修修下次手動清

## PR D — 批 ingest ch2-ch11（唯一剩 backlog）

- 10 章重複 v2 pipeline（每章 ~150k token / 30-60 min wall time，總 ~1.5M token / ~5-8 hr）
- 預期：每章新建 0-3 concept、lazy migrate 0-5 既有 concept、可能 0-2 update_conflict
- Book Entity 結束 status: `complete` + `chapters_ingested: 11`

## 完整 reference

- ADR-011：[`docs/decisions/ADR-011-textbook-ingest-v2.md`](../../docs/decisions/ADR-011-textbook-ingest-v2.md)
- Plan：[`docs/plans/2026-04-26-ingest-v2-redesign-plan.md`](../../docs/plans/2026-04-26-ingest-v2-redesign-plan.md)
- Workflow inventory：[`docs/research/2026-04-26-workflow-inventory.md`](../../docs/research/2026-04-26-workflow-inventory.md)
- 4 原則 + bug status：見 `project_textbook_ingest_v2_design.md` / `project_robin_aggregator_gap.md` / `feedback_kb_concept_aggregator_principle.md`

## PR C 執行學到的

- **「Driver reindex」 = walker nav_index → 人類視角 chapter index**：walker 不負責內容語意（nav#6 = 真章 1），driver 在 ingest 時把 attachments folder + figure refs 一致重命名（`ch6/` → `ch1/`、`fig-6-N` → `fig-1-N`），維持 `{base}/ch{chapter_index}/{ref}{ext}` 路徑解析語義
- **Re-ingest 場景的 book_id 沿用**：v1 既有 slug `biochemistry-sport-exercise-2024` 不改（避免 orphan 既有 mentioned_in 連結）；attachment folder slug 對齊 book_id（不是用戶 task brief 隨手寫的 file slug）
- **lazy migrate 4 個 v1 page 的轉換規則**：保留所有 source_refs 進 mentioned_in 不漏；strip 所有 `## 更新` block 但抽出實質內容整合進 body 主體；保留爭議 → `## 主要爭議`，新建衝突 → `## 文獻分歧 / Discussion`
- **Vision describe 在 Claude Code 互動 session 是用 Opus 4.7 多模態 Read（即 in-session）做的**，雖然 ADR-011 §3.4 / Q4 預設 Sonnet 4.6（cost-driven），但 Max 200 quota 走 Opus 品質更高、單章 ~150k token 仍在預算內
- **`## 文獻分歧` section 結構**：每個 Topic 列具名 source（含 page reference）+ 各自 claim 原文 + **討論段**（分歧根源 + KB anchor 立場 + 訓練/臨床含意）— retrieval 端能直接呈現完整脈絡
