---
name: SEO Phase 1.5 D.2 + F merged 2026-04-26 — 3 用途 production / follow-up backlog
description: PR #183 (D.2 audit) + #185 (F SERP) auto-merged 2026-04-26；用途 1+2+3 全 production（E DataForSEO 同日決定不接）；12 follow-up 條目 + 4 test gap
type: project
created: 2026-04-26
supersedes: project_d2_seo_audit_pr183.md, project_f_slice_firecrawl_pr185.md
originSessionId: TBD
---
2026-04-26 sweep：D.2 PR #183 + F PR #185 兩個 SEO PR 同時 review + auto-squash merged 並 worktree 清理完成。SEO Phase 1.5 = **完成**：A+B+C+D.1+D.2+F 上線；E DataForSEO 同日評估後決定不接（見 [project_seo_dataforseo_scrap_decision](project_seo_dataforseo_scrap_decision.md)）。

**晚間 follow-up sweep（2026-04-26）**：
- ✅ **PR #191 merged** `21ab18e` — DataForSEO scrap doc：ADR-009 §Addendum / task prompt §E deprecation / SKILL.md description / 5 memory file
- ✅ **PR #192 merged** `f1a542d` — D.2 + F follow-up sweep（A1/A2/A3/A4/A5 + B1/B2/B5/B6 + 4 regression test）；50 affected test 全綠

## Merged commits

| PR | Squash commit | 內容 |
|---|---|---|
| #183 | `23751d9` | D.2 — `seo-audit-post` skill (LLM semantic + markdown report)；`shared/seo_audit/llm_review.py`；`agents/robin/kb_search.py` 加 `purpose` kw-only param + 4 prompt variants；T1 benchmark on `shosho.tw/blog/zone-2-common-questions/` |
| #185 | `6712527` | F — firecrawl top-3 SERP + Haiku 4.5 摘要；`shared/firecrawl_serp.py` two-stage；`shared/seo_enrich/serp_summarizer.py` no-leak/no-copy/no-trust prompt；`enrich.py` `enable_serp` flag + injectable runner + 3 phase label |

`6712527..10e7540` — 含我的 memory rebase commit。

## Review verdicts

兩個 PR 走 dual sub-agent review（按 `feedback_pr_review_merge_flow.md` 標準）。

- **#183**: MERGE WITH FOLLOW-UP — 0 blocker，6 follow-up
- **#185**: READY TO MERGE — 0 blocker，6 polish

## Follow-up 清單（12 條，**已透過 PR #192 收 9 條 / 3 條 deferred**）

### A. D.2 SEO audit (PR #183)

| # | 檔案:行 | 問題 | 嚴重度 | 狀態 |
|---|---|---|---|---|
| A1 | `audit.py:560-561` | `kb_section` 標籤 drift（empty KB → 'included'）| low | ✅ PR #192 — `if kb_results else "skipped (no results)"` |
| A2 | `audit.py:171` | `lstrip("www.")` foot-gun | low | ✅ PR #192 — `removeprefix("www.")` |
| A3 | `llm_review.py:359` + `kb_search.py:113` | 直接 call 跳 cost tracking → 改用既有 `ask_claude` wrapper | medium | ✅ PR #192 — 既有 wrapper 已支援 system + prompt + max_tokens；不需建新 helper |
| A4 | `llm_review.py:_build_system_prompt` | system prompt 沒注入 SEED suffix instruction | low | ✅ PR #192 — system prompt 加 L9 specific instruction |
| A5 | `kb_search.py:_build_purpose_intro` | invalid purpose silent fall through | low | ✅ PR #192 — raise ValueError |
| A6 | tests | `MagicMock()` 沒 `spec=` | minor | ⬜ deferred — 跟 repo 風格一致，留 cleanup |

### B. F firecrawl SERP (PR #185)

| # | 檔案:行 | 問題 | 嚴重度 | 狀態 |
|---|---|---|---|---|
| B1 | `firecrawl_serp.py:35` | comment 單位錯（秒 → 毫秒）| trivial | ✅ PR #192 |
| B2 | `serp_summarizer.py:78` | `_format_pages_block` 漏 sanitize `url` | low | ✅ PR #192 — wrap `_sanitize` |
| B3 | tests/* | MagicMock 無 spec | minor | ⬜ deferred 同 A6 |
| B4 | `firecrawl_serp.py` | firecrawl `Location(country=...)` 可接上 | enhancement | ⬜ deferred — 需 SDK 實測 |
| B5 | `serp_summarizer.py:26` | cost comment $1/$5 → $0.80/$4 | trivial | ✅ PR #192 |
| B6 | `firecrawl_serp.py:88-90` | redundant API key check | trivial | ✅ PR #192 — 移除 + clarifying comment |

### C. Test gap（兩 PR 累計）

- D.2: 缺 `kb_section` empty-results test（會抓 A1）
- D.2: 缺 `lstrip("www.")` host edge case test（會抓 A2）
- D.2: 缺「L9 SEED 字串實際出現在 fix_suggestion」assertion（會抓 A4）
- D.2: 缺 `record_api_call` invoke assertion（or TODO marker）— 會抓 A3
- F: `firecrawl_serp.py:140-142` dict fallback path uncovered（dead-or-defensive 二擇一決策）
- F: `summarize_serp` 沒測 `pages` 含非 dict 元素（low surface）
- F: `test_summarize_uses_haiku_model` 只 prefix match；typo `claude-haiku-4-5-20251002` 過得了 → T1 wall-clock benchmark 會抓
- F: 缺 firecrawl 402/429 quota 失敗 explicit test（general Exception 已涵蓋，doc value 高）

## 三大用途映射狀態

| 用途 | 狀態 | unblock 條件 |
|---|---|---|
| 1. 內容創作建議（keyword-research） | ✅ **production** | — |
| 2. 既有部落格 SEO 體檢（seo-audit-post） | ✅ **production**（D.2 merged） | 修修瀏覽器跑 T1 production benchmark（5-keyword end-to-end）量 P95 wall-clock 補進 SKILL.md 取代 ~15-25s 估值 |
| 3. Brook compose 整合（寫稿吃 SEO 數據） | ✅ **production**（C opt-in + GSC + F SERP 摘要 ready；E DataForSEO 同日決定不接）| — |

## Closeout 已完成

- ✅ PR #183 squash merged remote
- ✅ PR #185 squash merged remote
- ✅ 主 worktree pull main + rebase memory commit `10e7540` push
- ✅ Worktree 清理：`F:/nakama-seo-d2` + `F:/nakama-seo-f` 全 remove
- ✅ Local branch `feat/seo-audit-d2` + `feat/seo-firecrawl-f` 刪除
- ✅ Stash 兩條 PR #185 dup（內容已 in origin）drop

## 修修待辦（瀏覽器手動）

1. **T1 production benchmark on `seo-keyword-enrich`**：跑 5 keyword end-to-end（zone 2 訓練 / 慢跑入門 / 重訓 / 睡眠 / 蛋白質）量 P95 wall-clock；取代 SKILL.md `~15-25s` 估值
2. **Brook compose 端到端 smoke**：`compose_and_enqueue(seo_context=<enriched.md path>)` 驗 `seo_block` 渲染 `competitor_serp_summary`（Slice C + F 第一次合演）
3. **`/bridge/cost` 觀察**：跑一次 audit 確認 LLM 成本「沒出現」— 印證 follow-up A3 的 cost tracking gap

## 不要碰

- ~~E slice — 卡修修 manual：DataForSEO 註冊 + $50 + `DATAFORSEO_LOGIN/PASSWORD` 進 .env~~ → **不做**（2026-04-26 決定，見 [project_seo_dataforseo_scrap_decision](project_seo_dataforseo_scrap_decision.md)）
- broken pages migration — 4 頁待修修瀏覽器手動 apply

## 開始之前一定要看

- 本 memo
- [project_seo_phase15_pickup.md](project_seo_phase15_pickup.md) — Phase 1.5 整體狀態
- [docs/task-prompts/phase-1-5-seo-solution.md](../../docs/task-prompts/phase-1-5-seo-solution.md) — 4 sub-slice scope（E 還在）
- 兩 PR review reports（in conversation transcript only — 無 PR comment）
