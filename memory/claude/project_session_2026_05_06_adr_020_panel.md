---
name: Session handoff 2026-05-06 — ADR-020 multi-agent panel + textbook ingest v3
description: KB Concept 87.5% stub crisis 起手寫 ADR-020 v2 整合 Codex (GPT-5) + Gemini 2.5 Pro panel audit；status Accepted；下次起手 commit 進度 / open PR / 拆 8 slice 成 issue / 動工 S1 / 凍結 multi-agent-panel skill
type: project
created: 2026-05-06
---

## 此 session 跑完的流程

5/6 同日（modular 5 階段）:
1. ✅ 檢討前一階段 KB stub crisis 結論
2. ✅ 用七層架構 anchor 問題到 Stage 3 sub-step C (Concept aggregation)
3. ✅ 解釋 ingest algorithm 白話 + ch5 spot check 75-85% 資訊密度
4. ✅ 探討「全文 copy 進 KB」設計 → Option D 提出
5. ✅ Multi-agent panel: Claude v1 → Codex (GPT-5) audit → Gemini 2.5 Pro audit → Claude v2 整合 → 修修 sign-off

## 凍結決策

**ADR-020 v2 status: Accepted** — `docs/decisions/ADR-020-textbook-ingest-v3-rewrite.md`

核心:
- Three-phase 架構（Lossless Source Ingest + Per-Chapter Sync Concept Aggregation + Concept Maturity Model L1/L2/L3）
- Verbatim body + LLM structured wrapper（取代現有 paraphrase）
- 4-action dispatcher 重啟（既有 `kb_writer.upsert_concept_page:591-786` 活化）
- BGE-M3 + bge-reranker-large + Parent-Child chunking + Hybrid retrieval (non-negotiable per Gemini)
- Bilingual term mapping discipline（concept frontmatter `en_source_terms` 欄位）
- Vision 6-class triage + multi-panel grouping
- LLM-classified hierarchical coverage manifest（取代 regex）
- L3 active stub == ingest fail；L2 stub 是 productive workflow state

## Deliverables 清單（檔案路徑）

```
docs/decisions/
  ADR-020-textbook-ingest-v3-rewrite.md     # v2 Accepted, 25 KB

docs/research/
  2026-05-06-codex-adr-020-audit.md         # Codex 6-section verbatim
  2026-05-06-gemini-adr-020-audit.md        # Gemini 6-section verbatim
  2026-05-06-gemini-adr-020-audit-dispatch.py  # panel pattern reference

memory/claude/
  project_kb_corpus_stub_crisis_2026_05_06.md  # 已 update v2 凍結
  project_multi_agent_panel_skill_todo.md      # skill-creator 待辦
  feedback_context_budget_200k_250k.md         # context budget 自我監控
  project_session_2026_05_06_adr_020_panel.md  # 本檔
  MEMORY.md                                     # index
```

當前 branch: `docs/kb-stub-crisis-memory` (PR #441 open，CI clean / mergeable)

## 進度狀態 (compact 前最後狀態)

✅ **(a) Commit + push 全部 artifacts** — PR #441 commit chain `2e076f8 → 8ca98dc → c4bf1da → bba1ad4`
✅ **(額外) Vault cleanup** — 828 files + 4 dirs → RecycleBin（622 Concept + 200 paper Source + 28 chapter Source + 6 entity + 754 attachments），保留 freedom-fleet entity / KB/Raw / Digests / Outputs；script 在 `E:/temp/cleanup-textbook-2026-05-06.ps1`，manifest 在 `E:/temp/cleanup-2026-05-06-manifest.txt`
✅ **(額外) ADR-020 v3 Phase 0 修正** — 修修指出 vault 分層原則 (Raw immutable, Wiki LLM 加工)；ADR Title `Three-phase` → `Four-phase`；§Phase 0 NEW + §Phase 1 改從 Raw 讀；S0 slice 加上（總 9 slice）
✅ **(b) `/to-issues` 拆 9 個 issue** — S0-S8 全 sequential chain：#442 → #443 → #444 → #445 → #446 → #447 → #448 → #449 → #450
✅ **(d) `skill-creator` 凍結 `multi-agent-panel` skill** — user-level at `C:/Users/Shosho/.claude/skills/multi-agent-panel/`（SKILL.md + 3 references + Python dispatch template）

## 下次 session 起手指令

**(c) 動工 S0** = Issue **#442** = EPUB → Raw markdown converter (pandoc / ebooklib / Calibre spike + writer)。Effort 1 day。

reload 順序：
1. 讀 `memory/claude/MEMORY.md` (CLAUDE.md §0 規則)
2. 讀本 handoff doc
3. 讀 `feedback_session_start_must_read_memory_md.md` (compact reload 規則)
4. `gh issue view 442` 看 issue 完整 acceptance criteria
5. 走 P9 模式（multi-file task），先寫六要素 Task Prompt，再 dispatch subagent

S3/S4/S8 動工時記得用 `/multi-agent-panel` skill (新 freeze 的) 跑 ground truth panel。

## HITL 觸點（auto mode 下唯一 contact point）

- **#445 S3**: 完全 AFK（panel ground truth 自動，閾值 spike 自動建議）
- **#446 S4**: 完全 AFK（panel ground truth 自動，inter-run consistency 自動驗）
- **#450 S8**: HITL final ship 拍板 (~10-15 min)，sub-agent 跑全 28 章 staging + 5 章 spot check + final report，修修看 report yes/no `mv staging → real Wiki/`

## ADR-020 v3 9 個 implementation slice 摘要

| Slice | Issue | What | Effort | Type |
|---|---|---|---|---|
| **S0** | #442 | EPUB → Raw markdown converter (spike + writer) | 1 day | AFK |
| **S1** | #443 | Phase 1 walker (read Raw) + verbatim body + LLM wrapper | 1-2 days | AFK |
| **S2** | #444 | Phase 2 sync concept dispatch + per-concept lock + 4-action revival | 2-3 days | AFK |
| **S3** | #445 | Concept Maturity Model classifier + L1/L2/L3 routing | 2-3 days | AFK (panel) |
| **S4** | #446 | Coverage manifest LLM classifier + acceptance gate | 2-3 days | AFK (panel) |
| **S5** | #447 | Vision 6-class triage + multi-panel grouping | 2 days | AFK |
| **S6** | #448 | RAG infra: BGE-M3 + reranker + Parent-Child chunking + Hybrid | 3-4 days | AFK |
| **S7** | #449 | Bilingual term mapping + query expansion | 1-2 days | AFK |
| **S8** | #450 | Cleanup re-ingest BSE + SN (staging + final ship) | 1 day | HITL |

## 兩個未解 housekeeping

- **`.agents/` 跟 `AGENTS.md`** untracked 出現在 nakama repo — 不是這次 session 創的，下次 session 起手 `git status` 看一下源頭再決定 commit / 砍 / ignore
- **`.claude/settings.json`** modified 是 codex auth dev env，不 commit（每次 session 都會 dirty）

S1-S7 sequential（avoid context drift）；S8 一次性 cleanup。

## Multi-agent panel 教訓（5/6 實證）

- Claude self-bias: 自己分析的盲點不容易自抓
- Codex (GPT-5): code grounding + 數字驗證強，抓到 Claude 漏看的 3 件 implementation drift
- Gemini 2.5 Pro: 抓到 Claude+Codex **兩家都漏的多語言維度**，並提出最 actionable 的 RAG architecture spec
- 三家強項互補有實證價值，未來複雜決策走 panel 預設路徑

## References

- [project_kb_corpus_stub_crisis_2026_05_06.md](project_kb_corpus_stub_crisis_2026_05_06.md) — 起點 + v2 凍結狀態
- [project_multi_agent_panel_skill_todo.md](project_multi_agent_panel_skill_todo.md) — pattern 雛形
- [feedback_context_budget_200k_250k.md](feedback_context_budget_200k_250k.md) — 本 session 學到的 context budget 規則
- [docs/decisions/ADR-020-textbook-ingest-v3-rewrite.md](../../docs/decisions/ADR-020-textbook-ingest-v3-rewrite.md) — 主交付物
