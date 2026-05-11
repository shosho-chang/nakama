---
name: 5/7 Path B plan 凍結 + Codex critique 套入 — fresh session 接 Stage 1a
description: 5/7 從 5/6 S8 burn 接續，把 Path B plan 寫定（docs/plans/2026-05-07-textbook-ingest-v3-path-b-rewrite.md）+ Codex thread a5c16ef7fd0cd8103 review YELLOW 7 條 critique 全套入；OAuth setup 已完成；plan 共 8 stage 估 $1.10 + 6hr；本 session 217.8k 破 200k 警戒收工，下次 fresh session 從 Stage 1a dispatch 開跑
type: project
created: 2026-05-07
---

## TL;DR

5/7 早段做完三件事：
1. 走 sandcastle issue #191 + CHANGELOG 確認 sandcastle 可走 OAuth (`CLAUDE_CODE_OAUTH_TOKEN`)，**Path C $0 marginal + AFK 成立**
2. 寫定 `docs/plans/2026-05-07-textbook-ingest-v3-path-b-rewrite.md`，含 Stage 1-8 全 stage gate
3. Codex thread `a5c16ef7fd0cd8103` review YELLOW，7 條 critique 全套入修訂版

修修批准，**OAuth setup 已完成**（host `claude setup-token` 跑完、`.sandcastle/.env` 已含 `CLAUDE_CODE_OAUTH_TOKEN`）。

Session 218k 破 200k 警戒，**fresh session 開 Stage 1a**。

## Plan 修訂後重點（差異對 5/6 burn）

| 項目 | 5/6 燒掉做法 | 5/7 修訂做法 |
|---|---|---|
| Phase 1 LLM 角色 | emit frontmatter + body + wrapper（16k cap 崩）| **只 emit JSON** `{frontmatter, sections}`，body 由 Python `_assemble_body()` 從 walker literal concat |
| Verbatim 保證 | LLM 自律 | **100% by construction** + Stage 1.5 凍結 normalize+match 演算法 |
| Acceptance gate | check_claim_in_page 30 LLM call/章 | 砍掉，純看 verbatim_match + section_anchors_match + figures + wikilinks 動態門檻 |
| Vision describe | LLM 從 alt_text 二次寫 fake llm_description | 砍掉，frontmatter 加 `vision_status: caption_only` 留升級槽 |
| 檔名 | walker chunk index（ch3.md 內容是 ch1）| 真章號 `ch{payload.chapter_index}.md` |
| Billing | API key（5/6 燒 $22.23）| **OAuth → Max quota**（Path C，sandcastle Docker 透傳）|
| Stage 1 | 7 檔同 dispatch | **拆 4 sub-dispatch**（1a→1b→1c→1d）|
| L2/L3 stub 防爆 | 沒規則（5/6 87.5% stub crisis 起點）| **L2/L3 hard rules** 寫進 `concept_dispatch.py`：min word ≥ 200 + chapter source paragraph + forbidden_strings 0 hit |
| Stage 4 ship gate | ≤ 2 fail 容忍 | **28/28 PASS after 4.5 reruns** 才上 Stage 6 |
| Stage 4 kill-switch | 無 | 連續 3 fail abort + 單章 wall > 30min abort |
| Stage 4.0 OAuth dry-run | 無 | **新加** 1 章 dry-run 驗 OAuth-in-Docker 真吃 Max + 不踩 anti-automation throttle |
| Stage 6 mv | `-Force` 蓋 | **不 -Force**，verify backup 完整再 explicit rename 舊 dir 進 backup，再 mv 新 dir 過來 |

## Codex thread

`a5c16ef7fd0cd8103`（thread `019dfb59-a721-7200-814e-2ce2bbb51923`），續用 5/6 ADR-020 audit 同條 thread。下次有設計級疑問可 SendMessage 接續：

```
codex:codex-rescue subagent_type, prompt: "--resume\n<question>"
```

## Stage 1a — fresh session 第一個 dispatch

第一個動作 = dispatch sandcastle agent 寫 `_assemble_body()` + tests。

**Prompt outline**（fresh session 完整化用）：
- target: `scripts/run_s8_preflight.py:_assemble_body` 新函式
- input: walker `verbatim_body` + figures list + sections JSON `[{anchor, concept_map_md, wikilinks: [...]}]` + book_id
- output: markdown body（已套 V2 圖檔 transform + 每節後插 `### Section concept map` + `### Wikilinks introduced`）
- 強驗：`section_anchors_match` 對 walker section_anchors（**identity equality 不只 count**）；不對 fail-fast
- tests: `tests/scripts/test_assemble_body.py`，含 V2 byte-equivalent / wrapper 插對位 / verbatim 100% / 多節+單節+section identity mismatch fail-fast / forbidden mid-paragraph wrapper edge case
- Stage 1a 驗收：`pytest tests/scripts/test_assemble_body.py` 全綠

走 sandcastle template B（execute Python，不從 issue 拉）：
```bash
cd E:\sandcastle-test
MSYS_NO_PATHCONV=1 npx tsx --env-file=.sandcastle/.env .sandcastle/main.mts
```

prompt.md 內容寫死「實作上面 outline」並引用本 handoff 路徑 + plan 路徑。

## Fresh session 起手指令

1. 讀 `memory/claude/MEMORY.md`（CLAUDE.md §0 規則）
2. 讀本 handoff
3. 讀 `feedback_context_check_before_multistage.md`（**新規則：開大任務前必先 quote context**）
4. 讀 `feedback_context_budget_200k_250k.md`
5. 讀 `feedback_adr_principle_conflict_check.md`
6. 讀 `docs/plans/2026-05-07-textbook-ingest-v3-path-b-rewrite.md`（修訂版含 Codex 7 條）
7. **第一動作**：報 context budget（fresh session 應 < 50k）
8. 第二動作：dispatch sandcastle Stage 1a

## 起手前確認 4 件事

| 項目 | 怎麼驗 |
|---|---|
| OAuth token 在 .env | `cat E:\sandcastle-test\.sandcastle\.env` 看 `CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...` |
| `ANTHROPIC_API_KEY` 已從 .env 移除 | 同上，確認沒有兩條同存 |
| Docker Desktop 在跑 | `docker ps` 不報 daemon error |
| nakama working tree clean | `git status` 應只有本 plan + memory commits |

## References

- Plan：[docs/plans/2026-05-07-textbook-ingest-v3-path-b-rewrite.md](../../docs/plans/2026-05-07-textbook-ingest-v3-path-b-rewrite.md)
- 5/6 burn handoff：[project_session_2026_05_06_07_s8_burn_handoff.md](project_session_2026_05_06_07_s8_burn_handoff.md)
- ADR-020：[docs/decisions/ADR-020-textbook-ingest-v3-rewrite.md](../../docs/decisions/ADR-020-textbook-ingest-v3-rewrite.md)
- Sandcastle OAuth 證據：sandcastle issue #191 + CHANGELOG c8df3a1
- Codex thread agentId: `a5c16ef7fd0cd8103`，threadId `019dfb59-a721-7200-814e-2ce2bbb51923`
