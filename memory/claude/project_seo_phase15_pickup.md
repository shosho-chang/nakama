---
name: SEO Solution Phase 1.5 — D.1 merged 2026-04-26，D.2/E/F unblocked，先 follow-up bug PR
description: Phase 1 全 merged + D.1 merged cc35218（28 rule + 146 test）；D.2/E/F unblocked，但 D.2 ship 前先修 D.1 follow-up bug
type: project
created: 2026-04-26
originSessionId: d68cbc3c-496f-44e3-b795-24cd48c68d09
---
2026-04-26 17:50 台北 D.1 PR #173 squash merged `cc35218`：`shared/pagespeed_client.py` + `shared/seo_audit/*` 8 module + 146 unit test 全綠 + `.env.example` PAGESPEED_INSIGHTS_API_KEY + runbook §2e。Review verdict MERGE WITH FOLLOW-UP — D.2 ship 前先修 2 correctness + 5 minor。

**Why:** Phase 1 (Slice A/B/C) 全 merged 涵蓋 keyword-research 下游 enrich + Brook compose opt-in；Phase 1.5 補修修原始三大用途中的「現有部落格 SEO 體檢」(D.1+D.2 ship `seo-audit-post`) + 「Brook compose 完整 enrich」(E+F 補 DataForSEO + firecrawl SERP)。task prompt PR #167 fix-up 額外凍結了 ADR §Open Items #1（28 條 deterministic check rule set）+ #2（report markdown 模板）兩個被 ADR 留給「實作階段」的決策，避免實作 PR scope creep。

**How to apply:** 開新 session 讀 MEMORY.md → 讀本 memo →（D.1 merged 後）讀 [docs/task-prompts/phase-1-5-seo-solution.md](../../docs/task-prompts/phase-1-5-seo-solution.md) §0 + Slice D.2 + §附錄 B + §附錄 C，從新 worktree `F:/nakama-seo-d2` 起手 D.2 implementation。

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

## Phase 1.5 狀態（D.1 merged，bug-followup 待開，D.2/E/F unblocked）

| Sub-slice | 範圍 | 預估 | 狀態 |
|---|---|---|---|
| **task prompt** | 4 sub-slice + 28 deterministic + 12 LLM + report 模板凍結 | — | ✅ PR #167 a985d14 |
| **D.1** | `shared/pagespeed_client.py` + `shared/seo_audit/*` 8 module（28 rule + types + html_fetcher）+ 146 unit test | 2-2.5 天 | ✅ PR #173 merged `cc35218` 2026-04-26 |
| **D.1-followup** | 2 correctness bug（M1 get_text + M3 query/case/relative urljoin）+ env doc + 5 test | 半天 | ✅ PR #181 merged `f247184` 2026-04-26 |
| **D.2** | `seo-audit-post` skill + LLM semantic 12 條 + markdown report；reuse gsc_client + 改 kb_search 加 `purpose` 參數 | 2.5-3 天 | 🟢 PR #183 opened 2026-04-26 — 詳見 [project_d2_seo_audit_pr183.md](project_d2_seo_audit_pr183.md)，等 review |
| **F** | firecrawl top-3 SERP + Claude Haiku 摘要 → 填 `competitor_serp_summary` | 1-1.5 天 | 🟢 PR #185 opened 2026-04-26 — 詳見 [project_f_slice_firecrawl_pr185.md](project_f_slice_firecrawl_pr185.md)，等 review |
| **E** | DataForSEO Labs `keyword_difficulty` → `seo-keyword-enrich`（health filter 內建）| 1-1.5 天 | ⬜ 卡修修 DataForSEO 註冊 + $50 + `DATAFORSEO_LOGIN/PASSWORD` |

**並行策略**：D.1-followup → D.2 sequential（D.2 reuse seo_audit modules）；E / F 不依賴 follow-up bug；E / F 互不依賴可並行。三條 dual-window：D.1-followup（半天小修） / E（DataForSEO） / F（firecrawl）。

## 修修 manual prerequisites

| 工作 | Slice unblock | 狀態 |
|---|---|---|
| GSC OAuth + Franky sa reuse + `.env` GSC_PROPERTY_*  | A/B/C ✓ | ✅ 2026-04-25 |
| **PageSpeed Insights API key** | D.1（D.2 端到端驗證需要）| ✅ 2026-04-26（已填 `.env`）|
| **DataForSEO 註冊 + $50 儲值 + `DATAFORSEO_LOGIN/PASSWORD`** | E | ⬜ |

## D.1 PR #173 摘要（已完成、待 merge）

- 8 module：`pagespeed_client.py` / `seo_audit/{__init__,types,html_fetcher,metadata,headings,images,structure,schema_markup,performance}.py`
- 28 rule 全套對齊 §附錄 A：M1-M5 / O1-O4 / H1-H3 / I1-I5 / S1-S3 / SC1-SC5 / P1-P3
- 146 unit test 全綠（每 rule ≥3 case 涵蓋 pass / warn / fail / edge）
- HEAD network call 全 mock；retry sleep 走 monkeypatch lambda
- httpx + self-rolled retry（**沒**用 tenacity；對齊 `gsc_client.py` 既有風格）
- `.env.example` 加 `PAGESPEED_INSIGHTS_API_KEY` + runbook §2e GCP Console 步驟
- CJK-aware word count（「磷酸肌酸系統」=6 字 not 1）；子網域算 external

## D.2 起跑 checklist（D.1 merged 後）

1. `gh pr view 173 --json state` 確認 D.1 已 merge（merged → 進行；open → 暫停等 review）
2. 開 worktree：`git worktree add F:/nakama-seo-d2 -b feat/seo-audit-d2 origin/main`
3. 讀 [docs/task-prompts/phase-1-5-seo-solution.md](../../docs/task-prompts/phase-1-5-seo-solution.md) §0 + Slice D.2 (§D.2.1 - §D.2.6) + §附錄 B（report 模板）+ §附錄 C（12 條 LLM semantic）
4. **必看 caveat**：L9 `compliance_scan.py` 6 條 SEED 詞庫不夠 / L10 `kb_search.py:57` prompt 寫死 YouTube 場景（D.2 要擴 `purpose` 參數）— 詳見下方
5. 寫 skill (`audit.py` 主流程 + LLM semantic + markdown report) + tests
6. ruff + pytest 全綠 + commit + push + PR

## 三大用途映射（修修原始定義）

| 用途 | 現狀 | 補完路徑 |
|---|---|---|
| 1. 內容創作建議 | ✅ keyword-research production | — |
| 2. 既有部落格 SEO 體檢 | 🟡 D.1 PR #173 open | D.1 merge → Phase 1.5 D.2 |
| 3. Brook compose 整合（寫稿吃 SEO 數據） | 🟡 半完成 — Slice C opt-in 已就緒，enrich 缺 difficulty + SERP 摘要 | Phase 1.5 E + F |

## 重要 caveats（task prompt 已寫進，D.2 PR 必看）

1. **L9 `compliance_scan.py` reuse 是 SEED 限制** — `MEDICAL_CLAIM_PATTERNS` 只 6 條（治好 / 99.9% / 肝癌 / 乳癌等）。直接 reuse 會在 audit L9 給假陰性。D.2 走 `scan_publish_gate()` + 在 audit report 標明「Phase 1 SEED — Slice B 醫療詞庫上線後升級」
2. **L10 `kb_search.py:57` prompt 寫死 YouTube 場景** — 給 SEO audit 用會讓 Haiku 在錯誤 context 排序。D.2 必須擇一：(a) 擴 `search_kb` signature 加 `purpose: Literal["youtube", "seo_audit", "blog_compose", "general"]`（推薦），或 (b) 寫 thin wrapper 自己 prompt
3. SEOPress meta_description 在 SEOPress block / focus_keyword 路徑已 wired by Usopp（PR #101）— audit 純 read-only，不寫
4. PageSpeed Insights API 預設 mobile，desktop 對照（CLI `--strategy` flag）
5. **D.1 已知 stale wording**：task prompt §D.1.6 邊界寫「reuse tenacity 與 gsc_client.py 對齊」是錯的（gsc_client.py 不用 tenacity，docstring 明示）。D.1 PR #173 採真實 codebase pattern（httpx + self-rolled retry）— D.2 接續沿用
6. **D.1 已知 stale rule count**：§D.1.1/D.1.2 寫「25 條 rule」是 stale，§附錄 A + §D.1.5 驗收凍結 28 條 — 以 §附錄為準

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
