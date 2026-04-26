---
name: D.2 SEO audit-post skill — PR #183 opened
description: D.2 完整實作 + T1 benchmark + 17 test → PR #183 opened 2026-04-26，等 review；下個 chunk 走 F (firecrawl) 不需修修動作
type: project
created: 2026-04-26
supersedes: project_d2_seo_audit_starting_2026_04_27.md
originSessionId: 74b1dda0-888a-4fcc-affd-a88547d84b84
---
2026-04-26 D.2 完整實作 + PR #183 opened，branch `feat/seo-audit-d2`，等 review/merge。

## 交付內容

| 路徑 | 角色 |
|---|---|
| `.claude/skills/seo-audit-post/SKILL.md` | ADR §D7 frontmatter + 5-step interactive workflow |
| `.claude/skills/seo-audit-post/scripts/audit.py` | Orchestrator — fetch → PageSpeed → 28 deterministic → 12 LLM → optional GSC → optional KB → markdown render；所有 collaborators 可 inject |
| `.claude/skills/seo-audit-post/references/{check-rule-catalog,output-contract}.md` | 28+12 rule catalog + frozen output contract |
| `.claude/skills/seo-audit-post/benchmarks/T1-shosho-zone-2-questions-20260426.md` | 真實 production URL benchmark 結果 |
| `shared/seo_audit/llm_review.py` | 12-rule single-call Sonnet 4.6 batch（§附錄 C）|
| `shared/seo_audit/__init__.py` | re-export `LLMLevel` |
| `agents/robin/kb_search.py` | 加 `purpose: Literal["youtube","seo_audit","blog_compose","general"]="general"` kw-only param + 4 prompt 變體 |
| `docs/capabilities/seo-audit-post.md` | Capability card |
| `tests/shared/seo_audit/test_llm_review.py` | 19 case（Anthropic mocked）|
| `tests/skills/seo_audit_post/{conftest,test_audit_pipeline,test_audit_no_gsc,test_audit_no_kb,test_audit_smoke}.py` | 17 case（E2E mock + subprocess smoke）|
| `tests/agents/robin/test_kb_search.py` | 加 5 case purpose dispatch + regression |

**測試成績**：194 D.2 tests + 100 kb_search/router tests 全綠；ruff check + format clean；full repo 2223 passed（3 unrelated pre-existing fails on main）。

## 設計決策（PR description 摘要）

1. **kb_search.purpose default = "general"**：路由 backward-compat（既有 routers 不傳 purpose → 走 neutral prompt，比原本寫死 YouTube 更貼切實際使用情境）
2. **L9 SEED caveat**：LLM prompt 注入 compliance_scan 結果 + report fix_suggestion 模板提到「Phase 1 SEED — Slice B medical vocab upgrade pending」
3. **L10 KB context**：caller 注入 `purpose="seo_audit"` 結果；缺 context 時 L10 仍跑但只看 schema 端
4. **All collaborators injectable**：`audit(pagespeed_runner=..., gsc_querier=..., kb_searcher=..., compliance_scanner=..., llm_reviewer=...)` — fork 友好
5. **section 順序契約**：5 mandatory + 2 optional（§6/§7 heading 永遠寫，body 用「跳過/不適用/錯誤」標）

## T1 benchmark（真實 URL，2026-04-26）

URL: `https://shosho.tw/blog/zone-2-common-questions/`
參數: `--llm-level haiku --no-kb`（PageSpeed 缺 key、GSC 缺 property → 自動降級）

結果：41 checks → 26 pass / 10 warn / 0 fail / 5 skip → grade A
- M2 meta description 89 字（< 150-160）
- I1 9/12 圖缺 alt
- L7 / L11 / L12 LLM 給出 E-E-A-T + medical references + freshness 建議

Pipeline 對缺 PageSpeed/GSC creds 的 graceful degradation 驗證 OK（real-world hardening 通過）。

## Caveats（PR review 該特別看的點）

- `_default_pagespeed_runner` 從 env 讀 key；缺 key 會 raise，audit 抓 except 變空 PageSpeed summary。real-world 用必須先 setup PAGESPEED_INSIGHTS_API_KEY
- `compose_and_enqueue` 不在 D.2 scope（Phase 2 `seo-optimize-draft` 才整合）
- 所有 LLM 失敗（API error / parse error / shape mismatch / partial response / invalid status）都降級 `status="skip"`，**不 raise**，pipeline 一律產出 markdown
- ruff: noqa: E501 加在 2 個 test 檔頂端（CJK 字串 fixture 太長無法縮）
- pytest fixture 跨檔共享走 `conftest.py`（不走 import 避 F811 redefinition warning）

## 下個 chunk

**F slice**（最直接，不需修修 manual prerequisite）：firecrawl top-3 SERP + Claude Haiku 摘要 → 填 SEOContextV1 `competitor_serp_summary`，整合進 `seo-keyword-enrich/scripts/enrich.py`。1-1.5 天。

E slice 卡在修修要先 DataForSEO 註冊 + $50 + `DATAFORSEO_LOGIN/PASSWORD` 進 .env。

D.2 PR review/merge 平行進行；F 與 D.2 無 file overlap。

## 開始之前一定要看

- 本 memo
- [project_seo_phase15_pickup.md](project_seo_phase15_pickup.md) — SEO 軸線完整 pickup
- [docs/task-prompts/phase-1-5-seo-solution.md](../../docs/task-prompts/phase-1-5-seo-solution.md) §F.1-F.6 — F slice 完整 scope
- PR #183 當下狀態：`gh pr view 183 --json state,mergedAt,reviewDecision`
