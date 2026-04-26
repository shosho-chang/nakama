---
name: 2026-04-26 四 PR 一次 merged + 三條軸線解鎖 + 6 follow-up bug
description: PR #169/#173/#178/#179 全 squash merged；ingest v2 Step 3 + SEO D.1 unblock 下游；2 個 SEO + 4 個 ingest silent corruption bug 待 follow-up PR
type: project
created: 2026-04-26
originSessionId: 4740fd89-5c21-4092-9c1f-04017a25aee8
---
2026-04-26 sweep：四個 open PR 全 reviewed + squash merged 在 ~10 分鐘內。

## Merged PR 摘要

| PR | Merge commit | 內容 | Verdict |
|---|---|---|---|
| #179 | `bf5b4ed` | dotenv empty-string sweep（5 site：health_check / usopp poll/batch / SMTP_PORT / GEMINI_MAX_WORKERS）+ 112 行 regression test | hygiene → 直接 merge 不派 review |
| #169 | `33f3095` | ingest v2 Step 3 PR A — kb_writer aggregator + Robin v2 4-action dispatcher（ADR-011）9 ultrareview findings 全修 | READY TO MERGE |
| #173 | `cc35218` | SEO Phase 1.5 D.1 — pagespeed_client + seo_audit/* 8 module 28 rule + 146 test | MERGE WITH FOLLOW-UP |
| #178 | `d955af6` | ingest v2 Step 3 PR B — parse_book walker（img/svg/figure/table/math）+ Vision describe + chapter-summary v2 | MERGE WITH FOLLOW-UP |

Pull main: `1d6d30c..d955af6` — 51 files changed, +8657 / -785。

## Follow-up bug 待修（必修）

### A. SEO D.1 (`shared/seo_audit/`) — 2 correctness bug + 5 minor

對應 #173 review。Pre-D.2 不 reach user，但 D.2 ship 前必補。

1. **`<title>` with nested tags reports as missing** — `shared/seo_audit/metadata.py:57-69`。`title_tag.string` 對 `<title>Hi <b>World</b></title>` 回 None，M1 誤報 fail。修：替換成 `title_tag.get_text(strip=True)` (line 58 + 69)
2. **`_normalize_url` drops query string + preserves host case** — `shared/seo_audit/metadata.py:127-134`。三條後果：(a) `Example.COM` vs `example.com` 假 fail M3，(b) `?utm=x` 假過 M3，(c) 相對 canonical (`/post-a`) 永遠 fail。修：lowercase netloc + 保留 query + `urljoin(page_url, href)`
3. minor: `.env.example:242` 寫 `§3` 應 `§2e`
4. minor: `images.py` HEAD 沒帶 User-Agent，部分 CDN 403 → 假 I3/I5 fail
5. minor: SSRF surface — 無 scheme allowlist、無 private-IP block，I3/I5 HEAD 對 169.254.169.254 / localhost 會打到內網（HEAD only / pipeline graceful，低嚴重度）
6. minor: 純數字 token 沒被 count_words 算（latin regex 要 leading letter）
7. minor: empty `<script type="application/ld+json">` block 在 SC4 顯示為「0 個 ld+json block」誤導

### B. ingest v2 PR B (`parse_book.py`) — 4 silent data corruption bug + 4 minor

對應 #178 review。**PR C 重 ingest ch1 前必修**（reviewer 標 silent data corruption）。

1. **`_html_table_to_markdown` 忽略 rowspan/colspan** — `parse_book.py:478`。`<td rowspan=2>A</td><td>B</td>...<td>C</td>` 渲染成 `C` 在錯欄。常見於藥理 / 代謝 / 實驗值表
2. **`_html_table_to_markdown` 遞迴抓 nested `<tr>`** — `parse_book.py:478`。`find_all("tr")` 預設 recursive，巢狀 table 把內層 row 吸到外層。修：filter `tr.find_parents("table")[0] is table_tag`
3. **always treats `rows[0]` as header** — `parse_book.py:491`。無 `<thead>` 的 table（很多 EPUB 的標準寫法）silent 把第一個資料 row 當 header，第一行 data 永遠丟失
4. **`<mfrac>` 無 alttext fallback collapse 數字** — `parse_book.py:526`。`<math><mfrac><mn>1</mn><mn>2</mn></mfrac></math>` 無 alttext → `$$12$$` 而非 `$$\frac{1}{2}$$`。modern EPUB 通常有 alttext 所以 low risk，但 fallback 路徑該補 mfrac/msub/msup/msqrt 走訪

Minor:
5. SKILL.md 重複 `7.` 編號（line 269/273），應該是 `8.`
6. SKILL.md 寫「5 sub-steps」但實際 7 條
7. `vision-describe.md` references nonexistent `figures[].path`（walker `_figure_to_dict` 沒 emit `path` key，需在 walker 補或在 skill driver 算）
8. `_export_chapter_attachments` 沒 validate `ref` for path traversal（今天 walker 只 emit int-based ref 所以 surface=0，未來 deserialize 路徑加防護）

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
- ✅ D.1 merged（PR #173）+ 待 follow-up bug PR
- ⬜ D.2 / E / F 全可起跑（D.1 unblocked）— D.1 follow-up bug PR 應在 D.2 ship 前 land

### 軸線 C：ingest v2 Step 3
- ✅ PR A merged（#169）+ PR B merged（#178）
- ⬜ PR C 重 ingest ch1 — **必先修 #178 4 個 silent data corruption bug**
- ⬜ PR D 批 ingest ch2-11

## 修修 manual todos（不變）

1. PR A 本機 E2E：`python -m agents.robin` 跑一個 KB/Raw source；確認 v2 schema + LLM 真 merge into body 主體（非 ## 更新 block）
2. PR A Web UI E2E：/processing → /review-plan → /execute；4-action badge + conflict topic + referenced bucket
3. Apply broken pages migration（PR #164 已 merged 但 vault 沒 apply）：
   `python -m scripts.migrate_broken_concept_frontmatter --vault "F:/Shosho LifeOS" --apply` 修 4 頁
4. 5D PR #177 VPS deploy：`ssh nakama-vps 'cd /home/nakama && git pull && sudo systemctl restart thousand-sunny nakama-gateway'`
5. 瀏覽器驗 5D：`/bridge/franky` row 3 出現 GSC / Slack · Franky / Gmail · Nami 三 card

## 推薦下一步序

修 D.1 + PR B 共 6 bug 是一個明確、限定 scope 的 follow-up PR（reviewer 全部給了 file:line 引用 + 修法），半天可以 land。然後才能：
- D.2 SEO audit skill（吃 D.1 modules + LLM semantic）
- PR C 重 ingest ch1（吃 PR A dispatcher + PR B walker）

兩條都 unblocked by follow-up bug PR。優先 follow-up bug PR > 任何新 chunk。

## 開始之前一定要先看

- 本 memo
- [project_ingest_v2_step3_in_flight_2026_04_26.md](project_ingest_v2_step3_in_flight_2026_04_26.md) — Step 3 詳細 context
- [project_seo_phase15_pickup.md](project_seo_phase15_pickup.md) — SEO 軸線 pickup 點
- [feedback_pr_review_merge_flow.md](feedback_pr_review_merge_flow.md) — review/merge 全自動規則
