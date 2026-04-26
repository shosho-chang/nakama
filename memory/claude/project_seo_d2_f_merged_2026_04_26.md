---
name: SEO Phase 1.5 D.2 + F merged 2026-04-26 — 3 用途 production / follow-up backlog
description: PR #183 (D.2 audit) + #185 (F SERP) auto-merged 2026-04-26；用途 1+2 production / 3 等 E (DataForSEO)；12 follow-up 條目 + 4 test gap
type: project
created: 2026-04-26
supersedes: project_d2_seo_audit_pr183.md, project_f_slice_firecrawl_pr185.md
originSessionId: TBD
---
2026-04-26 sweep：D.2 PR #183 + F PR #185 兩個 SEO PR 同時 review + auto-squash merged 並 worktree 清理完成。SEO Phase 1.5 進度：4/5 sub-slice 上線（A+B+C+D.1+D.2+F），剩 E 卡修修 DataForSEO 註冊。

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

## Follow-up 清單（12 條，全 non-blocker）

### A. D.2 SEO audit (PR #183)

| # | 檔案:行 | 問題 | 嚴重度 | 修法 |
|---|---|---|---|---|
| A1 | `.claude/skills/seo-audit-post/scripts/audit.py:560-561` | `kb_section` 標籤 drift：`kb_results = ... or []` + `is not None` check → 空 KB 結果 frontmatter 標 `included` 但 body 寫「KB 內無相關頁面」 | low（只 empty case） | `if kb_results else "skipped (no results)"` 一行修；output-contract 「kb_section prefix 可分支」契約破 |
| A2 | `.claude/skills/seo-audit-post/scripts/audit.py:171` | `host.lstrip("www.")` 經典 char-set foot-gun → `wfleet.shosho.tw` → `fleet.shosho.tw` 誤分類 `wp_fleet` | low（adversarial 仍 graceful） | `host.removeprefix("www.")` (Python 3.10+) |
| A3 | `shared/seo_audit/llm_review.py:359-369` | 直接 call `client.messages.create(...)` 跳過 `shared.anthropic_client` wrapper → `record_api_call` 不發 → audit 的 $0.025-0.035 不出現在 `/bridge/cost` | medium（同類 pre-existing in `kb_search.py:113-119`） | 建 `shared.anthropic_client.messages_create_with_tracking()` helper 一次解兩處 |
| A4 | `shared/seo_audit/llm_review.py:201-210` | system prompt 沒注入「Phase 1 SEED — Slice B medical vocab upgrade pending」suffix；PR description + `references/check-rule-catalog.md:116-117` claim 對不上 | low（doc drift） | 二擇一：(a) prompt 加「For L9 fix_suggestion, suffix with '(SEED scan; vocab upgrade pending)'」(b) 文件改成「caller 在報告下方加 caveat box」實作 |
| A5 | `agents/robin/kb_search.py:23-45` | invalid `purpose` 字串 silently fall through 到 `"general"` branch（`Literal[...]` 只 static check） | low | raise `ValueError` 或至少 log warning |
| A6 | tests/{shared/seo_audit,skills/seo_audit_post}/* | `MagicMock()` 沒 `spec=` / `autospec=True`；`client.messages.creates(...)` typo 不會被抓 | minor（跟 repo 風格一致） | 對齊 `feedback_mock_use_spec.md`，但低優先 |

### B. F firecrawl SERP (PR #185)

| # | 檔案:行 | 問題 | 嚴重度 | 修法 |
|---|---|---|---|---|
| B1 | `shared/firecrawl_serp.py:35` | comment `# 單篇 scrape timeout（秒）` 但值 `_SCRAPE_TIMEOUT_MS = 20000` ms（行為正確、註解錯） | trivial | 改「毫秒」或 rename const |
| B2 | `shared/seo_enrich/serp_summarizer.py:78` | `_format_pages_block` sanitize `title` + `content_markdown` 但**漏 `url`**；攻擊者控 SERP URL 可塞 prompt-injection 字串 | low（URL 編碼通常中和；但 defense-in-depth 不完整） | wrap line 78 with `_sanitize(...)` |
| B3 | tests/{shared,skills/seo_keyword_enrich}/* | `MagicMock()` 沒 `spec=`（同 A6） | minor | 同 A6 |
| B4 | `shared/firecrawl_serp.py` | author 註解「firecrawl 4.22 不接 country」是 partial-true：SDK `Location(country=...)` 物件存在於 `scrape()`，可不 bump SDK 接上 | enhancement | 加 `from firecrawl.v2.types import Location`；單行 `location=Location(country=country)` |
| B5 | `shared/seo_enrich/serp_summarizer.py:26` | cost comment `$1/$5/MTok` 高估；`shared/pricing.py:68-73` 實 $0.80/$4 | trivial | 對齊 pricing source-of-truth |
| B6 | `shared/firecrawl_serp.py:88-90` | API key 二次 check（`firecrawl_search()` 已先 raise on missing key），dead code | trivial | 移除 |

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
| 3. Brook compose 整合（寫稿吃 SEO 數據） | 🟢 **near-production**（C opt-in + F SERP 摘要 ready） | 缺 E (DataForSEO difficulty)；可先用 GSC + firecrawl 兩源跑端到端 smoke |

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

- E slice — 卡修修 manual：DataForSEO 註冊 + $50 + `DATAFORSEO_LOGIN/PASSWORD` 進 .env
- broken pages migration — 4 頁待修修瀏覽器手動 apply

## 開始之前一定要看

- 本 memo
- [project_seo_phase15_pickup.md](project_seo_phase15_pickup.md) — Phase 1.5 整體狀態
- [docs/task-prompts/phase-1-5-seo-solution.md](../../docs/task-prompts/phase-1-5-seo-solution.md) — 4 sub-slice scope（E 還在）
- 兩 PR review reports（in conversation transcript only — 無 PR comment）
