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

## 下次 session 起手 4 件（修修拍板順序: a→b→d→c）

**(a) Commit + push 全部 artifacts** — 進當前 PR #441 或開新 branch；files 已列上方
**(b) `/to-issues` 把 8 個 slice 拆成 GitHub issue**（S1-S8 順序 dependency 標清楚）
**(d) `skill-creator` 凍結 `multi-agent-panel` skill**（趁這次經驗 fresh）— ref project_multi_agent_panel_skill_todo
**(c) 動工 S1**（Phase 1 walker → verbatim body + LLM wrapper prompt 取代 chapter-summary.md）

## ADR-020 v2 8 個 implementation slice 摘要

| Slice | What | Effort |
|---|---|---|
| S1 | Phase 1 walker → verbatim body + LLM wrapper prompt | 1-2 days |
| S2 | Phase 2 in-chapter sync concept dispatch + per-concept lock | 2-3 days |
| S3 | Concept Maturity Model classifier + L1/L2/L3 routing | 2-3 days |
| S4 | Coverage manifest LLM classifier + acceptance gate | 2-3 days |
| S5 | Vision 6-class triage classifier + multi-panel grouping | 2 days |
| S6 | RAG infrastructure (BGE-M3 + reranker + Parent-Child chunking) | 3-4 days |
| S7 | Bilingual term mapping (`en_source_terms` extraction + query expansion) | 1-2 days |
| S8 | Cleanup re-ingest Sport Nutrition 4E + BSE | 1 day |

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
