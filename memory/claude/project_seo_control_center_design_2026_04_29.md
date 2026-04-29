---
name: SEO 中控台 v1 — 2/9 slice merged 2026-04-29 evening；critical path #229→#232→#234→#235；Ralph Loop 不存在要手動派
description: PRD #226 + 9 slice。#227 (foundation) + #228 (GSC cron Phase 2a-min) merged 2026-04-29；4 slice ready-for-agent 等手動派；3 slice blocked
type: project
created: 2026-04-29
updated: 2026-04-29
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

## Issues status (updated 2026-04-29 evening)

| # | Slice | Status |
|---|---|---|
| 226 | PRD parent | ✅ on GitHub |
| 227 | 1 — `/bridge/seo` foundation + chassis-nav × 9 | ✅ MERGED `cdb4558` |
| 228 | 8 — GSC daily cron + gsc_rows db (Phase 2a-min) | ✅ MERGED `c79dd6d` |
| 229 | 2 — article list (WP REST) | 🟢 ready-for-agent |
| 230 | 3 — target keywords section | 🟢 ready-for-agent |
| 231 | 7 — `/bridge/zoro/keyword-research` UI | 🟢 ready-for-agent |
| 233 | 9 — rank change section v1.1 | 🟢 ready-for-agent (after #228) |
| 232 | 4 — audit pipeline + run | ⛔ blocked by #229 |
| 234 | 5 — Y+ review page | ⛔ blocked by #232 |
| 235 | 6 — export to approval_queue | ⛔ blocked by #234 |

**Ralph Loop 不存在**：原 design memo 寫「ready-for-agent → Ralph Loop 接」是 placeholder，nakama 沒實作（grep `docs/ memory/ .github/ scripts/` 皆無）。**4 個 ready-for-agent issue 不會自動有 agent 跑**，下次 session 修修叫「下一步」/grab 一個手動派 worktree agent。

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
| 修修 VPS 部署 #228 cron | 餵 1 個 keyword + 24h smoke：gsc_rows 有真資料 |
| #229 merge | WebUI 看到文章列表（讀側登場）|
| #232 merge | 對某文章跑 audit、後端產 suggestion list（核心初登場、無 review UI） |
| #234 merge | 完整 audit + review session UX 跑得起來 |
| **#235 merge** | **audit → review → approve → export → `/bridge/drafts` 通通通 — 真正 QA milestone** |

## 修修 VPS 部署 slice 8 待辦

- cron line：`0 3 * * * cd /home/nakama && /usr/bin/python3 -m agents.franky gsc-daily >> /var/log/nakama/franky-gsc-daily.log 2>&1`（VPS TZ=Asia/Taipei 不需 prefix）
- 建議跟既有 Franky cron 對齊用 venv activation pattern 而非 system python3
- `.env` 確認 `GCP_SERVICE_ACCOUNT_JSON` + `GSC_PROPERTY_SHOSHO`（fleet 可選）— 跟 Slice B PR #133 用同一把 sa
- 第一次 cron 跑會 status='skipped'（target-keywords.yaml 空，預期）；等 Zoro Phase 1.5 push 第一個 keyword 才進 GSC API real call

## 下一輪建議 dispatch

**Window A**：#229 article list — 解 unblock #232 audit pipeline（critical path 起點）
**Window B（並行）**：#233 rank change v1.1 — cheap win，獨立、剛好 #228 落地有 `rank_change_28d` helper
（或 Window B = #230 keywords，純讀 yaml，更輕但等 Zoro 真 push keyword 才有意義）

## 開始實作前一定要看

- 本 memo
- ADR-012 [docs/decisions/ADR-012-zoro-brook-boundary.md](../../docs/decisions/ADR-012-zoro-brook-boundary.md)
- CONTEXT-MAP `SEO 中控台` + `audit review session` 名詞段
- PRD `docs/plans/2026-04-29-seo-control-center-prd.md`（issue #226 body 同源）
- ADR-009（SEO solution 整體架構）+ ADR-008（觀測中心，Phase 2 待動）
- [reference_bridge_ui_mutation_pattern.md](reference_bridge_ui_mutation_pattern.md) — bridge UI form+303 範式
