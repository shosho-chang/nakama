# route C — 文章 / 論文分支

route C = 單篇文章或論文（或一本書的**單一章節**當一篇）。一次讀完、一次 ingest，
不像 route B（書）要分多天 re-ingest、也不像 route E（影片）有時間軸。

## 兩步 CLI（`scripts/ingest_steps.py`）

### plan
```
python .claude/skills/ingest/scripts/ingest_steps.py plan \
    --raw "<path>" --source-type article|paper \
    [--annotation-slug <slug>] [--content-nature popular_science] \
    [--guidance "..."] --out plan.json
```
做的事（對齊 `IngestPipeline.ingest` 前半段 + Web UI summarizing/planning 步驟）：
1. 若給 `--annotation-slug` → `_render_literature(slug, source_type)` 寫 `KB/Literature/{slug}.md`
   （idempotent；找不到 annotation set 不中斷，pipeline log）。
2. `_generate_summary(全文)` → Source Summary body（≤30000 字 pass-through；超過 map-reduce）。
3. 寫 `KB/Wiki/Sources/{slug}.md`（`status: draft`、`author: agent_robin`、`original_author`、
   `source_refs: [raw]`）。
4. `_get_concept_plan(summary_body, ...)` → 計畫，輸出到 `--out`。

### execute
```
python .claude/skills/ingest/scripts/ingest_steps.py execute --plan-file plan.json
```
`_execute_plan(plan, summary_path)` 寫 Concept/Entity（過紅線 5 lint）+ `_update_index`。

## plan JSON schema
```json
{
  "title": "...", "slug": "...", "source_type": "article",
  "content_nature": "popular_science",
  "summary_path": "KB/Wiki/Sources/{slug}.md",
  "summary_excerpt": "...(摘要前 1200 字，供你展示)",
  "plan": {
    "concepts": [
      {"slug": "...", "action": "create|update_merge|update_conflict|noop",
       "title": "...", "domain": "...", "candidate_aliases": [],
       "extracted_body": "...", "reason": "...",
       "conflict": {"topic": "...", "existing_claim": "...", "new_claim": "..."}}
    ],
    "entities": [
      {"title": "...", "entity_type": "person|org|...", "reason": "...", "content_notes": "..."}
    ]
  }
}
```

## concept action 語意（4-action dispatcher，`shared/kb_writer.upsert_concept_page`）
- **create** 🆕：新概念頁。
- **update_merge** 🔀：併進既有概念頁（Opus diff-merge）。優先於亂建新頁。
- **update_conflict** ⚠️：既有主張與新來源衝突 → 顯示「既有 vs 新」給修修判。`conflict` 欄必帶。
- **noop** 🟢：已涵蓋，僅補 source 引用，不改正文。

## HITL：accept / defer / exclude（在對話裡）
- **accept**：留在 plan，execute 會寫。
- **defer**（之後再說）：本次從 plan 移除；記一筆到 taste log，下次可再提。
- **exclude**（不要）：從 plan 移除；**記理由**到 `style/taste-profile.md` raw 區（最強的負面品味訊號）。

## 錨點（CJK 注意）
Literature Note 用 `^p-N` 段落錨（render-time 1-based 序位 exact-copy）。中文無詞界，
Obsidian 區塊錨改句即可能靜默斷鏈——這是已知風險，未來交 `kb-lint` 做錨點健檢（ADR-045 風險表）。
route C ingest 本身不修這個，只是別假設錨永遠穩。

## 邊界
- 整本書 → route B（每日迴圈，未上線）；影片 → route E（未上線）。先擋並告知。
- 一本書的單章可以當 route C 一篇（eval #1 ✅）；整本 epub 不行（eval #10 ❌）。
