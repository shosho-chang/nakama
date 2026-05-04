# Robin

Knowledge Base agent — 吸收 source（article / paper / book / podcast）→ 抽 concept / entity → 寫 wiki page。Stage 3「資料整合」的 owner（CONTENT-PIPELINE.md）。

## Language

**Highlight**:
讀者在 source 上以 `==text==` 標記的純重點，無附加文字；表達「值得引用 / 值得記得」。
_Avoid_: annotation（語意不同，見下）、underline、mark

**Annotation**:
讀者在 source 上以 `> [!annotation] reftext\n> note...` block 標記的重點 + 個人註解；表達「對此段我有想法、聯想或質疑」，註解內容是讀者本人的話。
_Avoid_: highlight、note、comment（comment 在 git/PR 語境另有義）

**Source page**:
KB/Wiki/Sources/{slug}.md — Robin ingest 後產出的 Source Summary 頁面，frontmatter type=source。
_Avoid_: source document、article page

**Concept page**:
KB/Wiki/Concepts/{slug}.md — cross-source aggregator，依 ADR-011 P1 Karpathy 哲學 merge 多 source 證據。
_Avoid_: topic page、wiki page（wiki page 過於泛指）

## Relationships

- 一個 **Source page** 上可以有多個 **Highlight** 與多個 **Annotation**
- 一個 **Highlight** 與一個 **Annotation** 都嚴格從屬於某個 **Source page**（anchor 在某段落）
- **Annotation** 進 **Concept page** 的獨立 `## 個人觀點` section（明標 author=修修 + `from [[source-X]]` backlink），不與學術 evidence body 主體混合
- **Annotation** 物理 source of truth = `KB/Annotations/{source-slug}.md`（獨立檔，跟原 source 檔的 lifecycle 解耦；解 Inbox ingest 後刪原檔導致 annotation 一起死的問題）
- **Highlight** 物理 source of truth = `KB/Annotations/{source-slug}.md`（與 Annotation 同檔，schema field 區分 type）
- **Highlight** **不直接寫入 Concept page**（沒附加觀點價值）；其角色為 (a) Robin ingest pipeline summary 加權 hint（`prompts/summarize.md:30` 已支援優先 quote 修修標的句子）、(b) Stage 4 心得 derived view 保留

## Pipeline

- **Reader save → KB/Annotations/{slug}.md**：Reader 標記時即時 write，原檔不再 mutate；`base=inbox` 與 `base=sources` 兩條路徑一致處理（原檔保持純 source）
- **Source first ingest** (`/start` flow)：**只跑 source ingest（summarize / concept plan / execute），不含 annotation sync**；跟後續 sync 一致走兩次 trigger
- **Annotation 變更後的 re-sync → KB/Annotations/{slug}.md → Concept page `## 個人觀點`**：獨立輕量 endpoint（Robin），Reader header 加按鈕觸發；只跑 annotation merge，不重跑 summary + concept plan

## Concept page `## 個人觀點` section layout

- **Flat list, 時間順序** — 條目按 timestamp 排，一條 annotation = 一個 callout block
- 每條 callout 結構：`> [!annotation] from [[source-slug]] · YYYY-MM-DD` + `> **段落**: reftext` + `> **修修**: note`
- 多 source 共存於同一 `## 個人觀點` section（不另切 sub-heading by source）
- 衝突（同 concept 跨 source 觀點不一）走 inline，不 promote 獨立 section（ADR-011 P1 `## 文獻分歧` 保留給學術 evidence 衝突）
- **Per-source boundary marker**：每個 source 的 annotation 用 HTML comment 包：
  ```
  <!-- annotation-from: {source-slug} -->
  > [!annotation] ...
  <!-- /annotation-from: {source-slug} -->
  ```
- **Re-sync model = full replace per source**：Robin sync 時把 Concept page 內該 source 的 boundary marker block 整個 replace（其他 source 不動）；KB/Annotations/{slug}.md 是 source of truth，Concept page section 是 derived view（修修不可手編，編了下次 sync 會被 wipe）

## Sync 狀態可見性

- **Reader header badge** — 顯示「N 條 annotation 未 sync」，sync 完變「✓ 全部 sync」
- KB/Annotations/{slug}.md frontmatter 含 `last_synced_at`；每條 annotation 含 `modified_at`；Reader 對比兩個 timestamp 算 unsynced count

## Flagged ambiguities

- 「annotation」原先在 CONTENT-PIPELINE.md 觀察 #3 與口語中混指 highlight + annotation 兩者 — 2026-05-04 grill resolved：**分開為兩個 first-class concept**，因為兩者進 KB 的職責不同（highlight 是 source quote 候選；annotation 是讀者個人觀點 first-class 候選）。Reader code（`reader.html:118-120` mark.hl vs mark.ann）與 summarize prompt（`prompts/summarize.md:30-31`）本來就分，只是命名口語層沒對齊。
