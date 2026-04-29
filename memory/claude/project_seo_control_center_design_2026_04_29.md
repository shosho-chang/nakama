---
name: SEO 中控台 v1 — 9/9 slices DONE 2026-04-29 deep night；audit→review→approval_queue→publish loop end-to-end 收束
description: PRD #226 完成。9 PR merged：#227/#228/#243/#244/#246/#247/#248/#249/#250。#245 a11y follow-up + 多個 review concerns deferred
type: project
created: 2026-04-29
updated: 2026-04-29
confidence: high
originSessionId: 2026-04-29-seo-中控台-完工
---

2026-04-29 /grill-with-docs SEO solution → Nakama bridge web UI 收斂結果。

## TL;DR

- **ADR-012 寫好**：Zoro = 向外搜尋（keyword research）/ Brook = 對內加工（audit + enrich）；落實 ADR-001 line 38 預留的 Brook SEO 擴展選項
- **CONTEXT-MAP 更新**：Zoro / Brook 邊界改寫；新增 `SEO 中控台` + `audit review session` 名詞
- **SEO 中控台 v1 + ADR-008 Phase 2a-min sequencing 拍板**（5 階段，見下表）
- **PRD 待寫**

## Sequencing pinned

| 階段 | 內容 |
|---|---|
| 1 | SEO 中控台 v1：文章列表 + lazy audit + 攻擊關鍵字（讀 yaml）+ #3 排名變化 placeholder |
| 2 | ADR-008 Phase 2a-min：migration `003_seo_observability.sql` + GSC daily cron + bridge query helper（不含 alert / digest）|
| 3 | SEO 中控台 v1.1：#3 排名變化接 db |
| 4 | ADR-008 Phase 2a-full：alert rules + weekly digest GSC 段 |
| 5 | ADR-008 Phase 2b/c：GA4 + Cloudflare（跟中控台無直接關係） |

## 拍板決策表

| Q | 決策 |
|---|---|
| Q2 agent 歸屬 | audit + enrich → Brook；keyword research → Zoro（ADR-012）|
| Q3 audit 更新策略 | lazy（個別觸發 + 落表，無 cron）|
| Q4 文章列表 source | WP REST live pull `/wp/v2/posts?per_page=100` + 1h server cache |
| Q5 排名變化 | c — 等 ADR-008 Phase 2a-min（gsc_rows db）落地後 v1.1 接 |
| Q6a review 粒度 | per-rule（一條 fail/warn = 一張卡片）|
| Q6b approve 副作用 | γ — review session → export 進 ADR-006 `approval_queue` → `/bridge/drafts` HITL → publish；**不直接寫 WP** |
| Q6 UX tier | Y+ — 左 textarea + 右建議卡片 + `[在左側顯示]` 跳段按鈕；vanilla JS（無 SPA / htmx）|
| Q6 source | WP REST raw HTML（Gutenberg blocks 醜但可寫回；歷史文章無原 markdown 是 hidden constraint）|
| Q7 audit kick-off UX | Background task + redirect → `/bridge/seo/audits/{id}` polling status + auto redirect |
| Q8 schema | `audit_results` 表，**PK = `id INTEGER PRIMARY KEY AUTOINCREMENT`**（支持 multi-row per post，每跑 audit 新增一筆，2026-04-29 verify `migrations/006_audit_results.sql:34` 修正先前誤記為 composite PK）；index `(target_site, wp_post_id, audited_at DESC)` 取 latest + url secondary index；`suggestions_json` 整包 list[AuditSuggestionV1]|
| Q9 review 持久化 | 直接寫進 `audit_results.suggestions_json` 各 suggestion 的 status / edited_value；無 session 表 |
| Q10 export trigger | 至少 1 條 approved/edited 才 enable；reject + pending 不收 |
| Q11 chassis-nav | 加 `<a href="/bridge/seo">SEO <span class="zh">優化</span></a>`，位置在 DRAFTS 後面 |
| K1-K5 keyword research | LifeOS Project 觸發點不動（已 production via `tpl-project-body-blog.md` dataviewjs 按鈕）；UI 觸發點 = SEO 中控台「攻擊關鍵字」section 旁 button → `/bridge/zoro/keyword-research` 新 surface（為 Zoro 將來 trends/SERP namespace 預留）|
| Round 3 Brook compose 整合 | γ — 不做新 UI（現 chat / skill chain 已 cover）|

## 前端 stack 確認

當前：FastAPI + Jinja2 + vanilla JS（無 build step / 無 TS / 無 framework）。bridge mutation 範式（PR #140）：cookie auth + form post + 303 + native `<dialog>`。SEO 中控台 v1 stay vanilla（Y+ tier 用 textarea + JS scroll-to-anchor 即可）。

## Schema preview

```python
class AuditSuggestionV1(BaseModel):
    rule_id: str
    severity: Literal["fail", "warn"]
    title: str
    current_value: str
    suggested_value: str
    rationale: str
    status: Literal["pending", "approved", "edited", "rejected"] = "pending"
    edited_value: str | None = None
    reviewed_at: AwareDatetime | None = None

class AuditReviewSessionV1(BaseModel):
    audit_id: int
    post_url: str
    target_site: TargetSite
    audited_at: AwareDatetime
    overall_grade: Literal["A", "B+", "B", "C+", "C", "D", "F"]
    suggestions: list[AuditSuggestionV1]
    exported_to_approval_queue: bool = False
    approval_queue_id: int | None = None
```

## Issues status (2026-04-29 完工)

| # | Slice | PR | Commit |
|---|---|---|---|
| 226 | PRD parent | — | ✅ on GitHub |
| 227 | 1 — `/bridge/seo` foundation | #237 | `cdb4558` |
| 228 | 8 — GSC daily cron + gsc_rows db | #238 | `c79dd6d` |
| 244 | 2 — article list (WP REST) | #244 | `209ac64` |
| 246 | 3 — target keywords section | #246 | `dc800f0` |
| 231 | 7 — keyword-research UI | #243 | `9dc38a7` (+我加 XSS DOMPurify + auth test) |
| 232 | 4 — audit pipeline + run | #247 | post-merge |
| 233 | 9 — rank change v1.1 | #248 | post-merge |
| 234 | 5 — Y+ review page | #249 | post-merge |
| 235 | 6 — export to approval_queue | #250 | post-merge |

**End-to-end**：`/bridge/seo` 看文章 → 跑 audit → review per-rule cards → export 進 ADR-006 `approval_queue` → `/bridge/drafts` HITL → publish via Usopp。

**Ralph Loop**：見 `reference_ralph_loop_plugin.md` — 是 single-prompt iter runner 不是 issue queue runner。手動派 worktree agent 並行更快。

**Worktree leak 防線**：見 `feedback_worktree_leak_prevention_prompt.md` — 5 連勝零 leak。

## Follow-up issues from PR #237/#238

- #239 `feat(gsc-client)`: expose `dimensionFilterGroups` kwarg on `GSCClient.query`（收 PR #238 `_get_service()` private API breach）
- #240 `test(gsc-daily)`: add 5xx retry coverage（429 + 403 已測，500/502/503 分支沒測）

## Critical path 還剩 4 條 sequential PR

```
#229 article list → #232 audit pipeline → #234 review UI → #235 export → /bridge/drafts
```

獨立 parallel 3 條：#230 / #231 / #233。

## QA milestone 分層（決定何時做 end-to-end QA）

| 里程碑 | 可驗什麼 |
|---|---|
| ✅ #228 cron 部署 | 已 sync 回 cron.conf（commit 85a399c），4-30 03:00 首次跑 — 餵 keyword + 24h smoke 看 gsc_rows |
| ✅ #235 merge | audit → review → approve → export → `/bridge/drafts` 通通通 — **真正 QA milestone**，等修修瀏覽器走一輪 |

## 下一輪 QA + follow-up

### 已收
- **PR #252 merged 2026-04-29** `af84253` — `WordPressClient` 加 stable UA `nakama-wordpress-client/1.0` + CF zone WAF skip rule（修修 console 設定 + curl 200 驗證 + VPS deploy）。解 `/bridge/seo` Section 1「共 0 篇」根因 = CF SBFM 擋 datacenter IP UA。docs/runbooks/2026-04-29-add-wp-client-cf-skip-rule.md 是 one-shot task doc，cf-waf-skip-rules.md 表格新增第 3 條 row。教訓進 [feedback_cf_bot_challenge_403_html.md](feedback_cf_bot_challenge_403_html.md)
- **PR #253 merged 2026-04-29** `3e252f9` — 三 UX bug 一起出：(1) `seo_audit_progress.html` `.error-box[hidden] { display: none }` 1 行 CSS 修「audit failed 假警報」（`.error-box { display: flex }` shadow 掉 `[hidden]`，教訓進 [feedback_css_hidden_shadow.md](feedback_css_hidden_shadow.md)）；(2) `seo_audit_result.html` `<pre>` → `<div id="report-md">` + ~150 LOC inline vanilla-JS minimal markdown 渲染（heading/list/table/blockquote/code/link/bold/italic/hr）+ prose CSS 用既有 `--nk-*` token；(3) `shared/wp_post_raw_fetcher.py` 加 `sanitize_review_html()` BS4 helper + `sanitize=True` default 砍 `notion-*/discussion-*/brxe-*` class + `data-token-*` attr + unwrap attributeless `<span>`，保留 Gutenberg `<!-- wp:...-->` + `wp-block-*` + href/src/alt（slice #235 `update_post` 用 `sanitize=False` opt-out）

### 第一篇 audit milestone（2026-04-29 16:08 台北）
**`https://shosho.tw/blog/7-ways-reduce-inflammation/` audit_id=1, grade=C, suggestions=12**：integration 路徑 `/bridge/seo → POST /audits → background_task → polling → /result → /review → approve SC2 → /export → approval_queue#2`。完成路徑 30s wall clock，approve 1 條 export 進 `/bridge/drafts` queue。**Usopp publish 段沒走（daemon DB lock 阻塞，見 Issue B）**。修修 PR #253 後要繼續測下一篇。

### Open 設計問題（修修拍板）
- **L9 grade 設計衝突**：`shared/compliance/medical_claim_vocab.py` SEED 詞庫 6 大類 45+ 詞 substring match `title + AST text`，severity=`critical`，一條 fail 拉 grade A→C 或更低（`audit.py:228-244` `_grade()` 規則）。但 L9 是台灣藥事法 legal compliance，**Google ranking 不掃中文藥事法詞庫** — 把它放進 SEO grade 是設計誤判（語意 conflate compliance + SEO）。三選項：(a) L9 `severity` 從 `critical` 降為 `warn`（最小，1 行）/ (b) 從 grade 計算拿掉、保留為 advisory note / (c) 拆 `seo_grade` + `compliance_grade` 兩個分數（最完整、需 ADR addendum + schema migration）。修修原話：「如果只是會讓我違反《藥事法》的廣告規範，那就完全不用擔心」→ 傾向 (a) 或 (b)，但等真實第二篇 audit 的 grade 觀感再拍

### Issue B — Usopp daemon `database is locked` 規律性炸
- `/var/log/nakama-usopp` traceback 規律 cluster：05:30-05:32（Robin pubmed digest cron `30 5 * * *`）+ 06:30（Franky AI news cron `30 6 * * *`）。`approval_queue.py:256` `claim_approved_drafts` 走 `BEGIN IMMEDIATE` 撞鎖
- 不阻塞 today QA（cron 時段以外可正常 claim），但 export 後遇上 cron 時段會卡，Usopp publish 完整 end-to-end 還沒驗證
- 修法候選：busy_timeout 拉長 / 加 retry-with-backoff / Robin / Franky cron 改寫成 short transactions
- TODO：開 GH issue 追

### 既有 follow-up
- **GSC cron 24h smoke**：4-30 03:00 後檢查 `/var/log/nakama/franky-gsc-daily.log` + `gsc_rows` table（target-keywords.yaml 空時 status='skipped' 是預期）
- #245 a11y follow-up
- #239/#240 GSC client API breach + retry coverage
- 修修第二篇 audit 待跑（驗 PR #253 三 fix UX）

## 開始實作前一定要看

- 本 memo
- ADR-012 [docs/decisions/ADR-012-zoro-brook-boundary.md](../../docs/decisions/ADR-012-zoro-brook-boundary.md)
- CONTEXT-MAP `SEO 中控台` + `audit review session` 名詞段
- PRD `docs/plans/2026-04-29-seo-control-center-prd.md`（issue #226 body 同源）
- ADR-009（SEO solution 整體架構）+ ADR-008（觀測中心，Phase 2 待動）
- [reference_bridge_ui_mutation_pattern.md](reference_bridge_ui_mutation_pattern.md) — bridge UI form+303 範式
