---
name: F slice firecrawl SERP integration — PR #185 opened
description: F 完整實作 + 39 new test + 121 SEO suite green → PR #185 opened 2026-04-26，等 review；下一 chunk E slice 卡修修 DataForSEO 註冊
type: project
created: 2026-04-26
originSessionId: TBD
---
2026-04-26 F slice 完整實作 + PR #185 opened，branch `feat/seo-firecrawl-f`，等 review/merge。

## 交付內容

| 路徑 | 角色 |
|---|---|
| `shared/firecrawl_serp.py` | `fetch_top_n_serp()` two-stage（reuse `firecrawl_search` + `app.scrape(formats=['markdown'])`）；per-page truncation 3000 chars；search 失敗 raise，per-scrape 失敗 silent skip |
| `shared/seo_enrich/serp_summarizer.py` | `summarize_serp()` Haiku 4.5 batch；no-leak / no-copy / no-trust prompt；input + output 兩段 sanitize（regex 與 `agents/brook/seo_block.py:_INJECTION_PATTERNS` 同步）；≤1000 char 截斷標記 |
| `.claude/skills/seo-keyword-enrich/scripts/enrich.py` | `enable_serp` + injectable `serp_runner`；動態 phase label（gsc+firecrawl / serp-skipped / gsc-only）；CLI `--no-serp` flag；dry-run JSON 加 `serp_enabled` |
| `.claude/skills/seo-keyword-enrich/SKILL.md` | Phase 1.5 status + 成本（~$0.01/run、~15-25s）+ references 補 firecrawl_serp + summarizer + Brook sanitize |
| `.env.example` | FIRECRAWL_API_KEY 註解擴充（quota math：500 credits ≈ 125 enrich/month）|
| `tests/shared/test_firecrawl_serp.py` | 10 case（happy / search fail / partial scrape fail / truncation / strict N / blank URL / empty markdown / no API key）|
| `tests/shared/seo_enrich/test_serp_summarizer.py` | 18 case（prompt 結構 / sanitize regex parametrized / LLM fail / empty / model id / missing fields / truncation）|
| `tests/skills/seo_keyword_enrich/test_firecrawl_integration.py` | 11 case（三 phase + CLI --no-serp + dry-run + default-runner graceful）|
| `tests/skills/seo_keyword_enrich/test_enrich_pipeline.py` | autouse fixture stub `_default_serp_runner` 讓既有 60+ test 維持 network-isolated |

**測試成績**：121 SEO + firecrawl tests 全綠（39 new + 既有 82+）；ruff check + format clean；full repo `pytest` 2250 passed（2 unrelated pre-existing Windows path fails on main：`test_doc_index` + `test_log`）。

## Phase label semantics（PR description 摘要）

| Path | Frontmatter `phase` | Trigger |
|---|---|---|
| GSC + firecrawl + Haiku 全 OK | `"1.5 (gsc + firecrawl)"` | default |
| GSC OK，firecrawl/Haiku 失敗 | `"1.5 (gsc + serp-skipped)"` | quota / network / LLM error → graceful fallback (`competitor_serp_summary=None`) |
| `--no-serp` opt-out | `"1 (gsc-only)"` | offline / quota constrained |

下游 consumer（Brook compose Slice C；Phase 2 `seo-optimize-draft`）解析 JSON block 而非 phase string — phase 是 human / log signal。

## Caveats（PR review 該特別看）

- **firecrawl-py 4.22 `country` quirk**：`fetch_top_n_serp()` signature 接受 country，但 SDK `app.search()` 4.22 不吃 country kwarg；目前只 log，留 slot 給未來 SDK 升級。test 明確 assert 當前行為。
- **two-stage vs single-call `search(scrape_options=...)`**：保留 task prompt 兩階段骨架，partial-failure 語意更乾淨（per-page error 不影響 search 結果）+ quota cost 隔離。Single-call 是 Phase 2 perf 候選。
- **wall-clock benchmark deferred**：T1 production benchmark（5-keyword end-to-end）等 merge + 修修瀏覽器 smoke；CI / mocked test 量不到 firecrawl wire latency。SKILL.md 給 ~15-25s 估值（per-scrape ~5s × 3）。
- **L9 SEED caveat 不適用**：F slice 不是 compliance gate；SERP summary 是 editorial。無需醫療詞庫過濾。

## 下個 chunk

**E slice** 卡修修 manual prerequisite：DataForSEO 註冊 + $50 儲值 + `DATAFORSEO_LOGIN/PASSWORD` 進 .env。E 上線後 phase label 會升 `"1.5 (gsc + dataforseo + firecrawl)"`。

D.2 PR #183 + F PR #185 review/merge 平行進行；兩 PR 改不同檔案無 file overlap。

D.1-followup PR #181 已 merged；D.2/E/F unblocked。

## 三大用途映射狀態

| 用途 | 狀態 |
|---|---|
| 1. 內容創作建議（keyword-research）| ✅ production |
| 2. 既有部落格 SEO 體檢（seo-audit-post）| 🟢 D.2 PR #183 open，等 review |
| 3. Brook compose 整合（寫稿吃 SEO 數據）| 🟡 Slice C opt-in 已 merged + F SERP 摘要 PR #185 opened；缺 E (DataForSEO difficulty) |

## 開始之前一定要看

- 本 memo
- [project_seo_phase15_pickup.md](project_seo_phase15_pickup.md) — SEO 軸線完整 pickup
- [project_d2_seo_audit_pr183.md](project_d2_seo_audit_pr183.md) — D.2 PR opened 平行
- [docs/task-prompts/phase-1-5-seo-solution.md](../../docs/task-prompts/phase-1-5-seo-solution.md) §F — 原始 task prompt scope
- PR #185 當下狀態：`gh pr view 185 --json state,mergedAt,reviewDecision`
