---
name: SEO Solution Phase 1.5 dispatch 起跑點（task prompt PR #167 frozen 後）
description: Phase 1 全 merged（5 PR）+ Phase 1.5 task prompt PR #167 frozen 2026-04-26（a985d14）；4 sub-slice (D.1/D.2/E/F) 待 dispatch；推薦序 D.1 → D.2 → E → F
type: project
created: 2026-04-26
---
2026-04-26 SEO Phase 1.5 task prompt 已 frozen，4 sub-slice scope + 28 deterministic + 12 LLM rule + report 模板全凍結進 [docs/task-prompts/phase-1-5-seo-solution.md](../../docs/task-prompts/phase-1-5-seo-solution.md)。下次 fresh session 起手 D.1 implementation。

**Why:** Phase 1 (Slice A/B/C) 全 merged 涵蓋 keyword-research 下游 enrich + Brook compose opt-in；Phase 1.5 補修修原始三大用途中的「現有部落格 SEO 體檢」(D.1+D.2 ship `seo-audit-post`) + 「Brook compose 完整 enrich」(E+F 補 DataForSEO + firecrawl SERP)。task prompt PR #167 fix-up 額外凍結了 ADR §Open Items #1（28 條 deterministic check rule set）+ #2（report markdown 模板）兩個被 ADR 留給「實作階段」的決策，避免實作 PR scope creep。

**How to apply:** 開新 session 讀 MEMORY.md → 讀本 memo → 讀 [docs/task-prompts/phase-1-5-seo-solution.md](../../docs/task-prompts/phase-1-5-seo-solution.md) §0 + Slice D.1 + §附錄 A，從 worktree 起手 D.1 implementation。

## Phase 1 狀態（已完成 — 5 PR + benchmark）

| 項目 | PR | merge 日期 |
|---|---|---|
| Slice A — `SEOContextV1` schema + `gsc_client.py` + `site_mapping.py` + GSC OAuth runbook | #132 | merged |
| Slice B — `seo-keyword-enrich` skill (GSC-only baseline) + 72 tests | #133 | merged |
| Slice B follow-up — F2 cannibalization 白名單 + F4 enrich self-load dotenv | #138 | merged |
| env reuse — `GCP_SERVICE_ACCOUNT_JSON` reuse Franky sa | #135 | merged |
| Slice C — Brook compose `seo_context` opt-in + `_build_seo_block` + sanitization + token budget | #139 | merged |
| ADR-009 multi-model triangulation | #124 | merged |
| T1 benchmark on shosho.tw（zone 2 訓練 5 row 真資料 / fleet 0 row）| — | 2026-04-25 |

## Phase 1.5 狀態（task prompt frozen，實作 0%）

| Sub-slice | 範圍 | 預估 | 狀態 |
|---|---|---|---|
| **task prompt** | 4 sub-slice + 28 deterministic + 12 LLM + report 模板凍結 | — | ✅ PR #167 a985d14 |
| **D.1** | `shared/pagespeed_client.py` + `shared/seo_audit/*` 6 module（28 條 rule）+ unit test | 2-2.5 天 | ⬜ |
| **D.2** | `seo-audit-post` skill + LLM semantic 12 條 + markdown report；reuse gsc_client + 改 kb_search 加 `purpose` 參數 | 2.5-3 天 | ⬜ |
| **E** | DataForSEO Labs `keyword_difficulty` → `seo-keyword-enrich`（health filter 內建）| 1-1.5 天 | ⬜ |
| **F** | firecrawl top-3 SERP + Claude Haiku 摘要 → 填 `competitor_serp_summary` | 1-1.5 天 | ⬜ |

**並行策略**：D.1 → D.2 強制 sequential；E / F 互不依賴可並行；D 全線 vs E vs F 三條完全獨立可丟 dual-window。

## 修修 manual prerequisites

| 工作 | Slice unblock | 狀態 |
|---|---|---|
| GSC OAuth + Franky sa reuse + `.env` GSC_PROPERTY_*  | A/B/C ✓ | ✅ 2026-04-25 |
| **DataForSEO 註冊 + $50 儲值 + `DATAFORSEO_LOGIN/PASSWORD`** | E | ⬜ |
| **PageSpeed Insights API key**（GCP Console → 啟用 PageSpeed Insights API → 建 key，5 分鐘） | D.1 | ⬜ |

## D.1 起跑 checklist（下次 session 起手）

1. 讀 [docs/task-prompts/phase-1-5-seo-solution.md](../../docs/task-prompts/phase-1-5-seo-solution.md) §0 + §0.1 + Slice D.1 (§D.1.1 - §D.1.6) + §附錄 A 28 條 rule
2. 讀體例參考：`shared/gsc_client.py`（thin wrapper retry pattern）+ `shared/seo_enrich/striking_distance.py`（filter-first-then-build T6 contract）+ `shared/log.py`（get_logger）+ `tests/shared/test_gsc_client.py`（test 體例）
3. 開 worktree（避免踩 textbook v2 視窗 working tree）：`git worktree add F:/nakama-seo-d1 -b feat/seo-audit-d1 origin/main`
4. 寫 module（10 個檔）+ tests（8 個檔，每 rule ≥ 3 unit test）
5. ruff + pytest 全綠
6. commit + push + open PR
7. dispatch review + merge（按 [feedback_pr_review_merge_flow.md] 自動流程）

## 三大用途映射（修修原始定義）

| 用途 | 現狀 | 補完路徑 |
|---|---|---|
| 1. 內容創作建議 | ✅ keyword-research production | — |
| 2. 既有部落格 SEO 體檢 | ⬜ | Phase 1.5 D.1 + D.2 |
| 3. Brook compose 整合（寫稿吃 SEO 數據） | 🟡 半完成 — Slice C opt-in 已就緒，enrich 缺 difficulty + SERP 摘要 | Phase 1.5 E + F |

## 重要 caveats（task prompt 已寫進，實作 PR 必看）

1. **L9 `compliance_scan.py` reuse 是 SEED 限制** — `MEDICAL_CLAIM_PATTERNS` 只 6 條（治好 / 99.9% / 肝癌 / 乳癌等）。直接 reuse 會在 audit L9 給假陰性。D.2 走 `scan_publish_gate()` + 在 audit report 標明「Phase 1 SEED — Slice B 醫療詞庫上線後升級」
2. **L10 `kb_search.py:57` prompt 寫死 YouTube 場景** — 給 SEO audit 用會讓 Haiku 在錯誤 context 排序。D.2 必須擇一：(a) 擴 `search_kb` signature 加 `purpose: Literal["youtube", "seo_audit", "blog_compose", "general"]`（推薦），或 (b) 寫 thin wrapper 自己 prompt
3. SEOPress meta_description 在 SEOPress block / focus_keyword 路徑已 wired by Usopp（PR #101）— audit 純 read-only，不寫
4. PageSpeed Insights API 預設 mobile，desktop 對照（CLI `--strategy` flag）

## Phase 2 backlog（時程未定）

- `seo-optimize-draft` skill（吃 draft + `SEOContextV1` → 重寫；內部 call Brook compose）
- Cron 整站 GSC 體檢（合併 ADR-008 Phase 2 weekly digest）
- `seo-audit-post` full mode（競品對照 / 跨頁 keyword 網絡）
- T7 異步化 / T8 rate limit middleware / T13 GSC quota alert / T3 schema V2 migration playbook
- SurferSEO API 評估 / GEO+AEO 專題 / LLM-based health filter

## 開始之前一定要先看

- 本 memo
- [docs/task-prompts/phase-1-5-seo-solution.md](../../docs/task-prompts/phase-1-5-seo-solution.md) — 完整 4 sub-slice scope + 28+12 rule + report 模板
- [docs/decisions/ADR-009-seo-solution-architecture.md](../../docs/decisions/ADR-009-seo-solution-architecture.md) — Source ADR 援引
- [feedback_reuse_module_inspect_inner_text.md](feedback_reuse_module_inspect_inner_text.md) — 本次學到的 reuse 前 inspect inner text 教訓
- [project_seo_solution_scope.md](project_seo_solution_scope.md) — 三大用途 high-level scope（2026-04-18 凍結）
- [reference_seo_tools_landscape.md](reference_seo_tools_landscape.md) — 工具地景 + API 契約坑
