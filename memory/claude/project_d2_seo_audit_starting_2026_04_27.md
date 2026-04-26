---
name: D.2 SEO audit-post skill starting checklist (next session)
description: D.1 + follow-up merged → D.2 unblocked；scope/files/dep summary + 修修 prerequisite (DataForSEO E unblock)
type: project
created: 2026-04-26
originSessionId: 4740fd89-5c21-4092-9c1f-04017a25aee8
---
D.1 + D.1 follow-up bug PR merged 2026-04-26。D.2 SEO audit-post skill 解鎖，下次 session 起手。

## D.2 Scope（從 task prompt §D.2 凍結）

預估 2.5-3 天，超過單 session — 建議 dual-window 或 multi-session。

### 新增檔案

| 路徑 | 內容 |
|---|---|
| `.claude/skills/seo-audit-post/SKILL.md` | Skill frontmatter（ADR §D7 已凍結 description 觸發詞）+ workflow 5-step（沿用 `seo-keyword-enrich` 體例：parse / resolve / cost confirm / invoke / hand-off）|
| `.claude/skills/seo-audit-post/scripts/audit.py` | 主流程：fetch HTML → deterministic modules → PageSpeed → LLM semantic → optional GSC → optional KB → render markdown |
| `.claude/skills/seo-audit-post/references/check-rule-catalog.md` | 28 deterministic + 12 semantic rule 人類可讀目錄 |
| `.claude/skills/seo-audit-post/references/output-contract.md` | 下游 consumer 契約 doc |
| `shared/seo_audit/llm_review.py` | LLM semantic check 12 條（§附錄 C）；Sonnet 4.6 single-call batch；輸入 `(soup, fetched_text, focus_keyword, kb_context)`；輸出 `list[AuditCheck]` |
| `docs/capabilities/seo-audit-post.md` | Capability card（沿用 `seo-keyword-enrich.md` 格式）|

### 測試檔

| 路徑 | 範圍 |
|---|---|
| `tests/shared/seo_audit/test_llm_review.py` | Mock Anthropic；驗 12 條 prompt 組裝、JSON parse、LLM 失敗 fallback `status="skip"` 不 raise |
| `tests/skills/seo_audit_post/test_audit_pipeline.py` | E2E mock：fixture HTML + mock PageSpeed + mock LLM + mock GSC → 驗 markdown 結構 |
| `tests/skills/seo_audit_post/test_audit_no_gsc.py` | URL 不屬修修網站 → 跳過 GSC section |
| `tests/skills/seo_audit_post/test_audit_no_kb.py` | KB 失敗/path 不存在 → 跳過 internal link suggestion 不 raise |
| `tests/skills/seo_audit_post/test_audit_smoke.py` | subprocess 跑 `python audit.py --url <fixture-server>` 通 |

### 必要 caveats

1. **`agents/robin/kb_search.py:57` prompt 寫死 YouTube 場景**。reuse 不直接，必須擇一：
   - (a) 加 `purpose: Literal["youtube", "seo_audit", "blog_compose", "general"]` 參數讓 prompt 走分支（推薦）
   - (b) 寫 thin wrapper 自己餵 prompt
2. **`agents/brook/compliance_scan.py` 是 SEED 限制**。`MEDICAL_CLAIM_PATTERNS` 只 6 條（治好 / 99.9% / 肝癌 / 乳癌等），audit 場景假陰性。reuse 走 `scan_publish_gate()` + report 標明「Phase 1 SEED — Slice B 醫療詞庫上線後升級」
3. PageSpeed Insights 預設 mobile，desktop 對照（CLI `--strategy` flag）
4. SEOPress meta_description 在 SEOPress block / focus_keyword 路徑已 wired by Usopp（PR #101）— audit 純 read-only 不寫
5. 12 條 LLM semantic 走 single batch call（成本控制：1 call / audit；Sonnet 4.6）

## 修修 prerequisites（D.2 ship 前）

| 工作 | 狀態 |
|---|---|
| GSC OAuth + Franky sa reuse + .env GSC_PROPERTY_* | ✅ 2026-04-25 |
| PageSpeed Insights API key | ✅ 2026-04-26 |
| Anthropic API key（既有 LLM call 用同一條）| ✅ 既存 |

## 起跑 checklist

1. 確認 PR #181 (D.1 followup) merged：`gh pr view 181 --json state` → `MERGED`
2. 開 worktree：`git worktree add F:/nakama-seo-d2 -b feat/seo-audit-d2 origin/main`
3. 讀以下三段：
   - `docs/task-prompts/phase-1-5-seo-solution.md` §0 + §D.2.1-D.2.6
   - 同檔 §附錄 B（report 模板）
   - 同檔 §附錄 C（12 條 LLM semantic）
4. **先擴 `agents/robin/kb_search.py` 加 `purpose` 參數**（caveat 1） — 獨立 commit / sub-PR 比較乾淨
5. 寫 `shared/seo_audit/llm_review.py` 12 條 + tests（mock Anthropic）
6. 寫 skill scaffolding：`SKILL.md` + `audit.py` + 2 references + capability card
7. 5 個 test 檔分別 covered（pipeline / no_gsc / no_kb / smoke / llm_review）
8. ruff + pytest 全綠 + commit + push + PR

## 平行可動的 chunk（不 block D.2）

- **E**：DataForSEO Labs `keyword_difficulty` → seo-keyword-enrich（health filter 內建）— 1-1.5 天 — **修修 manual prerequisite**：DataForSEO 註冊 + $50 儲值 + `DATAFORSEO_LOGIN/PASSWORD` 進 .env 才能起跑
- **F**：firecrawl top-3 SERP + Claude Haiku 摘要 → 填 `competitor_serp_summary` — 1-1.5 天 — 不依賴 D.1 follow-up 也不依賴 D.2
- **5C**：Quality Uplift FTS5 結構化 log search UI — 2 天 — 不依賴 SEO 軸線

順序建議：D.2（最高 user value）→ F（不需修修 manual 動作）→ E（修修動完 DataForSEO 後）；5C 可任意時點插入。

## 開始之前一定要看

- 本 memo
- [project_seo_phase15_pickup.md](project_seo_phase15_pickup.md) — SEO 軸線完整 pickup
- [docs/task-prompts/phase-1-5-seo-solution.md](../../docs/task-prompts/phase-1-5-seo-solution.md) — 完整 task prompt
- [docs/decisions/ADR-009-seo-solution-architecture.md](../../docs/decisions/ADR-009-seo-solution-architecture.md) — Source ADR
