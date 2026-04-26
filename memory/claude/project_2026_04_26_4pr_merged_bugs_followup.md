---
name: 2026-04-26 六 PR 全 merged，D.2 + PR C 解鎖
description: PR #169/#173/#178/#179 + #180/#181 — 4 PR review + 2 follow-up bug fix；三條軸線下游全 unblock
type: project
created: 2026-04-26
originSessionId: 4740fd89-5c21-4092-9c1f-04017a25aee8
---
2026-04-26 sweep：6 個 PR 全 squash merged。早段四 PR (review + merge)、晚段兩 follow-up bug fix PR (#180 walker corruption / #181 D.1 metadata)。三條軸線下游全部 unblock。

## Merged PR 摘要

| PR | Merge commit | 內容 | Verdict |
|---|---|---|---|
| #179 | `bf5b4ed` | dotenv empty-string sweep（5 site：health_check / usopp poll/batch / SMTP_PORT / GEMINI_MAX_WORKERS）+ 112 行 regression test | hygiene → 直接 merge 不派 review |
| #169 | `33f3095` | ingest v2 Step 3 PR A — kb_writer aggregator + Robin v2 4-action dispatcher（ADR-011）9 ultrareview findings 全修 | READY TO MERGE |
| #173 | `cc35218` | SEO Phase 1.5 D.1 — pagespeed_client + seo_audit/* 8 module 28 rule + 146 test | MERGE WITH FOLLOW-UP |
| #178 | `d955af6` | ingest v2 Step 3 PR B — parse_book walker（img/svg/figure/table/math）+ Vision describe + chapter-summary v2 | MERGE WITH FOLLOW-UP |
| #180 | `7f77c3a` | PR B walker corruption follow-up（rowspan/nested/headerless/mfrac + path traversal validation + SKILL.md doc fix）+ 17 test | bug fix → 自動 merge |
| #181 | `f247184` | D.1 metadata follow-up（M1 nested-tag title fix + M3 canonical normalization：case + query + relative）+ 5 test | bug fix → 自動 merge |

Pull main: `b67377f..f247184` — 累計 +9173 / -822 across 6 PR。

## Follow-up bug — 已修

### A. SEO D.1 (`shared/seo_audit/`) — 2 correctness bug — ✅ PR #181 merged `f247184`

對應 #173 review。Pre-D.2 不 reach user，但 D.2 ship 前必補。**已 land**。

剩 5 minor 不在 #181 scope 內，留 D.2 觀察是否真打到 real WP page 才修：

~~1. **`<title>` with nested tags reports as missing**~~ — ✅ 修法 `get_text(strip=True)`。**Note**：reviewer 原 claim 在 html.parser 下其實不會 fire（RCDATA），但 lxml/html5lib 下 `.string` 對 multi-child 回 None — 修法 defensive
~~2. **`_normalize_url` drops query string + preserves host case**~~ — ✅ lowercase netloc + 保留 query + `urljoin(page_url, href)`
~~3. minor: `.env.example:242` 寫 `§3` 應 `§2e`~~ — ✅
4. **minor (deferred)**: `images.py` HEAD 沒帶 User-Agent，部分 CDN 403 → 假 I3/I5 fail
5. **minor (deferred)**: SSRF surface — 無 scheme allowlist、無 private-IP block，I3/I5 HEAD 對 169.254.169.254 / localhost 會打到內網（HEAD only / pipeline graceful，低嚴重度）
6. **minor (deferred)**: 純數字 token 沒被 count_words 算（latin regex 要 leading letter）
7. **minor (deferred)**: empty `<script type="application/ld+json">` block 在 SC4 顯示為「0 個 ld+json block」誤導

### B. ingest v2 PR B (`parse_book.py`) — 4 silent data corruption + 4 minor — ✅ PR #180 merged `7f77c3a`

對應 #178 review。PR C 重 ingest ch1 前必修。**已 land**。

✅ 全部修完 + 補 17 test：
1. `_html_table_to_markdown` rowspan/colspan 修法：新 `_expand_rows_to_grid` honoring 兩 attr
2. `find_all("tr")` recursive filter：`tr.find_parent("table") is table_tag`
3. Header detection 三層：explicit `<thead>` > 第一個有 `<th>` 的 `<tr>` > synthesised empty header（GFM-compliant，不損 row 0 data）
4. 新 `_walk_mathml` 對 `mfrac`/`msup`/`msub`/`msubsup`/`msqrt`/`mroot` 走訪；alttext 仍 take priority
5. SKILL.md `7.` → `8.`
6. SKILL.md 「5 sub-steps」 → 「7 sub-steps」
7. `vision-describe.md` `figures[].path` 改 documents the derivation
8. `_export_chapter_attachments` 加 `_validate_attachment_ref` + `_validate_attachment_extension` regex（surface=0 但 future-proof）

### C. ingest v2 PR A — 2 minor (非 blocker)

review 結論 READY TO MERGE，但 2 minor 可順手修：

1. noop branch 沒 normalize body 到 v2 H2 skeleton（`shared/kb_writer.py:660-691` 沒 call `_ensure_h2_skeleton(body)`）— 不算 regression，但 v1 → v2 first noop 會 schema_version=2 但缺 canonical sections
2. cosmetic — noop write redundancy on first-noop-after-derive（最多一次冗餘 write per v1 page）

## 三條軸線最新狀態

### 軸線 A：Quality Uplift
- ✅ dotenv sweep merged（PR #179）— 5 site 全清
- ❌ 5D PR #177 VPS 未部署（修修 manual）
- ⬜ 5C FTS5 log search / 5B-3 anomaly daemon — 待修修決定

### 軸線 B：SEO Phase 1.5
- ✅ D.1 merged（PR #173）+ ✅ D.1 follow-up merged（PR #181）
- ⬜ D.2 unblock — 詳見 [project_d2_seo_audit_starting_2026_04_27.md](project_d2_seo_audit_starting_2026_04_27.md)
- ⬜ E（DataForSEO） / F（firecrawl SERP）獨立可並行

### 軸線 C：ingest v2 Step 3
- ✅ PR A merged（#169）+ PR B merged（#178）+ ✅ walker corruption fix merged（PR #180）
- ⬜ PR C 重 ingest ch1 — **Mac-side（textbook-ingest skill 走 Mac Claude Code Opus 1M）— 修修 driver**
- ⬜ PR D 批 ingest ch2-11 — 同上 Mac-side

## 修修 manual todos（不變）

1. PR A 本機 E2E：`python -m agents.robin` 跑一個 KB/Raw source；確認 v2 schema + LLM 真 merge into body 主體（非 ## 更新 block）
2. PR A Web UI E2E：/processing → /review-plan → /execute；4-action badge + conflict topic + referenced bucket
3. Apply broken pages migration（PR #164 已 merged 但 vault 沒 apply）：
   `python -m scripts.migrate_broken_concept_frontmatter --vault "F:/Shosho LifeOS" --apply` 修 4 頁
4. 5D PR #177 VPS deploy：`ssh nakama-vps 'cd /home/nakama && git pull && sudo systemctl restart thousand-sunny nakama-gateway'`
5. 瀏覽器驗 5D：`/bridge/franky` row 3 出現 GSC / Slack · Franky / Gmail · Nami 三 card

## 推薦下一步序

下一個 session 起手點建議（依 user-value 序）：

1. **D.2 SEO audit-post skill**（2.5-3 天）— 詳見 [project_d2_seo_audit_starting_2026_04_27.md](project_d2_seo_audit_starting_2026_04_27.md)。最高 user-value（修修原始三大用途之一：「現有部落格 SEO 體檢」）
2. **5C FTS5 log search**（2 天）— Quality Uplift 內部工具，獨立軸線可任意插入
3. **F firecrawl SERP**（1-1.5 天）— 不依賴修修 manual prerequisite（DataForSEO 還沒 setup）
4. **E DataForSEO**（1-1.5 天）— 修修需先註冊 + $50 儲值 + .env keys

PR C / D（重 ingest）走 Mac-side 由修修 driver，不在我這邊接。

## 開始之前一定要先看

- 本 memo
- [project_ingest_v2_step3_in_flight_2026_04_26.md](project_ingest_v2_step3_in_flight_2026_04_26.md) — Step 3 詳細 context
- [project_seo_phase15_pickup.md](project_seo_phase15_pickup.md) — SEO 軸線 pickup 點
- [feedback_pr_review_merge_flow.md](feedback_pr_review_merge_flow.md) — review/merge 全自動規則
