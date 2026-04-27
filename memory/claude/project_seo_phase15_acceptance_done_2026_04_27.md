---
name: SEO Phase 1.5 production acceptance done — D→B+ real grade after CF rule
description: 2026-04-27 修修跑完三件 acceptance + F5-B CF rule，re-audit grade D→B+；PR #200 含 4 條 follow-up（F2/F4/F5-C + SKILL benchmark）
type: project
created: 2026-04-27
supersedes_partial: project_seo_d2_f_merged_2026_04_26.md（修修待辦三件已 done）
originSessionId: 70b01225-94d6-4548-a285-53270c088e26
---
2026-04-27 SEO Phase 1.5 真正落地：三件 acceptance test 全綠（T1 5-keyword wall-clock benchmark / T2 Brook compose seo_context smoke / T3 audit + cost emit 驗證 PR #192 A3 fix）+ F5-B CF rule 加完 → audit re-run 真實 grade **B+**（之前 firecrawl fetcher 假 D）。

## TL;DR

- **PR #200 merged**：F2 enrich set_current_agent("brook") + F4/F5-C `fetch_html_via_firecrawl` + audit() `html_fetcher` injection + CLI `--via-firecrawl` flag + SKILL benchmark refresh + 5 個 docs（acceptance / fact-check plan / CF runbook split）
- **F5-B done**：CF dashboard 加 `NakamaBot/1.0` skip rule，VPS curl shosho.tw 從 403 → 200
- **Re-audit B+**：default httpx 從 VPS 直接通，27 pass / 9 warn / 1 fail / 4 skip / 0 critical
- SEO Phase 1.5 三大用途 1+2+3 production live：keyword-research / seo-keyword-enrich / seo-audit-post / Brook compose 整合

## Wall clock benchmarks

`seo-keyword-enrich` 5-keyword acceptance（zone 2 訓練 / 慢跑入門 / 重訓 / 睡眠 / 蛋白質）：
- enrich-only: min=21s, **median=26s**, mean=31.2s, max=58s, P95~58s
- SKILL.md 估值 `~15-25s` 改成「median 26s / P95 ~58s」

## F1/F3 撤回分析

- **F1**：cannibalization schema 真名是 `competing_urls`，沒 `competing_pages` 欄位 — 我前面 inspection script 寫錯
- **F3**：`shared/seo_audit/llm_review.py:364` 的 `set_current_agent("brook")` + comment「SEO audit 暫掛 brook，cost tracking 一致」是有意設計

## 三大用途終態

| 用途 | 狀態 | tool |
|---|---|---|
| 1. 內容創作建議 | ✅ production | `keyword-research` skill |
| 2. 既有部落格 SEO 體檢 | ✅ production（real grade B+ on zone 2 文章）| `seo-audit-post` skill (default httpx 從 VPS 通 + `--via-firecrawl` fallback) |
| 3. Brook compose 整合（寫稿吃 SEO 數據） | ✅ production（queue_row=1 demo 通過）| `compose_and_enqueue(seo_context=, core_keywords=)` |

## CF skip rule landscape

| UA | 用途 | 加入時 |
|---|---|---|
| `nakama-external-probe/1.0` | GH Actions external uptime probe | PR #115 |
| `NakamaBot/1.0` | seo-audit-post `fetch_html` | F5-B 2026-04-27 |

詳見 [docs/runbooks/cf-waf-skip-rules.md](../../docs/runbooks/cf-waf-skip-rules.md)（long-term reference）+ [docs/runbooks/2026-04-27-add-nakamabot-cf-skip-rule.md](../../docs/runbooks/2026-04-27-add-nakamabot-cf-skip-rule.md)（task doc）。

## Backlog（不在這個 PR 範圍）

- **fact-check agent**（修修問「能不能查 LLM hallucinate 引用」）— 設計凍結在 [docs/plans/2026-04-27-fact-check-agent-design.md](../../docs/plans/2026-04-27-fact-check-agent-design.md)，1.5-2 day 工程，等修修點頭做
- **shosho.tw zone 2 文章 SEO improvement**（按 audit §3 Warnings 9 條 + L11 1 fail）— 修修內容工作
- **未來 Robin / Brook self-fetch CF rule** — `cf-waf-skip-rules.md` 留下空格，加新 UA 時走 SOP

## Race condition 殘留（修修自己處理）

主 worktree (F:/nakama) 在 `docs/ingest-v2-ch2-triangulate` branch 上有我誤 commit 的 5cbb5e9（SEO docs，已 cherry-pick 到 PR #200 b2ffed5）。修修可以：
- `git checkout main && git pull`（main 會自動拉 PR #200 + 92ef9dd 含 ch2 spot-check）
- `git branch -D docs/ingest-v2-ch2-triangulate`（清掉那條 orphan branch）

5cbb5e9 commit hash 在 PR #200 merge 後變 unreachable，下次 git gc 自動清。

## 開始之前一定要看

- 本 memo
- [docs/plans/2026-04-27-seo-phase15-acceptance-checklist.md](../../docs/plans/2026-04-27-seo-phase15-acceptance-checklist.md) — 驗收計畫
- [docs/plans/2026-04-27-seo-phase15-acceptance-results.md](../../docs/plans/2026-04-27-seo-phase15-acceptance-results.md) — 完整結果（含 first-run vs re-run 對照）
- [docs/plans/2026-04-27-fact-check-agent-design.md](../../docs/plans/2026-04-27-fact-check-agent-design.md) — fact-check backlog
