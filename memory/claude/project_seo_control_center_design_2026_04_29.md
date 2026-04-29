---
name: SEO 中控台 v1 design grilling 2026-04-29
description: /grill-with-docs SEO 中控台 vision 凍結；ADR-012 zoro/brook 邊界落地；中控台 v1 + ADR-008 Phase 2a-min sequencing；PRD #226 + 9 slice 已建（#227 #228 ready-for-agent）
type: project
created: 2026-04-29
confidence: high
originSessionId: TBD
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
| Q8 schema | `audit_results` 表，主鍵 `(target_site, wp_post_id)` + url secondary index；`suggestions_json` 整包 list[AuditSuggestionV1]|
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

## Issues filed (2026-04-29)

| # | Slice | Status |
|---|---|---|
| 226 | PRD parent | ✅ on GitHub |
| 227 | 1 — `/bridge/seo` foundation | 🟢 ready-for-agent (brief posted) |
| 228 | 8 — GSC daily cron + gsc_rows db | 🟢 ready-for-agent (brief posted) |
| 229 | 2 — article list (WP REST) | needs-triage (blocked by #227) |
| 230 | 3 — target keywords section | needs-triage (blocked by #227) |
| 231 | 7 — `/bridge/zoro/keyword-research` UI | needs-triage (blocked by #227) |
| 232 | 4 — audit pipeline + run | needs-triage (blocked by #229) |
| 233 | 9 — rank change section v1.1 | needs-triage (blocked by #228) |
| 234 | 5 — Y+ review page | needs-triage (blocked by #232) |
| 235 | 6 — export to approval_queue | needs-triage (blocked by #234) |

當 blocker merge → `/github-triage` 該 issue → ready-for-agent → Ralph Loop 接。

## 開始實作前一定要看

- 本 memo
- ADR-012 [docs/decisions/ADR-012-zoro-brook-boundary.md](../../docs/decisions/ADR-012-zoro-brook-boundary.md)
- CONTEXT-MAP `SEO 中控台` + `audit review session` 名詞段
- PRD `docs/plans/2026-04-29-seo-control-center-prd.md`（issue #226 body 同源）
- ADR-009（SEO solution 整體架構）+ ADR-008（觀測中心，Phase 2 待動）
- [reference_bridge_ui_mutation_pattern.md](reference_bridge_ui_mutation_pattern.md) — bridge UI form+303 範式
