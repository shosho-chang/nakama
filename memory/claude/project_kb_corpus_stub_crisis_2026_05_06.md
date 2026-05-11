---
name: KB Concept corpus 87.5% 是 Phase B stub 空殼 — ingest pipeline 設計性破洞
description: 2026-05-06 發現的戰略級問題 — 622 個 Concept page 中 544 個是 phase-b-reconciliation 創的空殼，僅 78 頁有真實 aggregator body；index.md 只 cover 5%；hybrid retrieval 升級無意義（GIGO）；textbook ingest pipeline 整個 review-rewrite 為下個戰略 priority
type: project
created: 2026-05-06
---

## 硬數據（2026-05-06 掃描）

```
KB/Wiki/Concepts 總頁數                 622
  Phase B reconciliation stub           544 (87.5%)
  有真實內容（>16 行）                    78 (12.5%)

KB/Wiki/Sources                          201 頁
KB/Wiki/Entities                           6 頁

KB/index.md 條目                         45（覆蓋實際 833 頁的 ~5%）
  - Sources 17 條（停在 creatine 那波）
  - Concepts 0 條 ❌（完全沒這個 section）
  - Books 2 條
  - Digests 22 條
  - 其他 4 條

KB/log.md 最後一筆                      2026-04-25
  → 5/3 兩本教科書 ingest（28 章 / ~600 wikilink / 544 stub）全部沒寫進 log
  → sync-conflict 檔案存在（4/9 Obsidian Sync race）
```

## 哪一步出問題（具體 file:line）

破口在 `.claude/skills/textbook-ingest/prompts/phase-b-reconciliation.md` Step 3（line 80-100）：

```yaml
# {slug}

(Stub — auto-created by Phase B reconciliation {ingest_date} to resolve wikilinks
from {book_title} ingest. Will be enriched as Robin processes future ingests
or as 修修 fills in body.)
```

prompt 第 171 行寫 "Quality > speed." 但 Step 3 template 本身就是違反這句的具體實作 — cognitive dissonance baked into prompt。

「Will be enriched」是 deferred TODO 寫在 data 裡，**沒有 trigger / owner / deadline / mechanism**。

## 設計演化的契約破裂

| 日期 | ADR | 與 Concept aggregator 契約的關係 |
|---|---|---|
| 2026-04-25 | ADR-010 | Karpathy aggregator 哲學凍結 |
| 2026-04-26 | ADR-011 | P1 「concept page = cross-source aggregator with merged body」入 stone tablet；§3.3 Step 4 規範 4-action dispatcher: create / update_merge / update_conflict / noop |
| **2026-05-03** | **ADR-016** | **Phase A/B 拆分。§2.1 row B 寫「創 stub Concept pages」— 跟 P1 完全衝突，但沒人 cross-check** |

ADR-016 把 ADR-011 §3.3 Step 4「Concept extract with conflict detection」的語意洗掉了 — concept 創建從「aggregator merge」降級成「讓 wikilink 變藍色不要紅色」。

## 為什麼速度優先壓過品質指導原則

**最諷刺的時序**：

- 2026-05-01 修修 explicit 講「我不求速度也不求省錢，我求品質」→ `feedback_quality_over_speed_cost.md` 寫進 memory
- **2026-05-03 同一天**：修修再次強調「永遠把最高品質當作最高指導原則」→ memory updated
- **2026-05-03 同一天**：ADR-016 寫成，§3 metrics 用 wall time 當 KPI，Phase B prompt 寫死 stub template

四個 root cause：

1. **memory 是 declarative，prompt 是 imperative，後者贏** — phase-b-reconciliation.md 引用 ADR-010/011 但沒 inline `feedback_quality_over_speed_cost`，subagent 看不到最高指導原則
2. **「Quality > speed」變裝飾性 trailer** — prompt 寫了，但 Step 3 template 本身違反它，沒人發現衝突
3. **ADR-016 metrics 沒有 quality column** — wall time 5x speedup 是 KPI，concept page completeness 沒被 measure → 沒被 optimize → silent drop
4. **沒有 cross-ADR contract check** — ADR-016 動 ADR-011 §3.3 流程時沒人 re-read P1，「ADR-conflict-check」不是工程習慣

## 戰略級結論

修修 2026-05-06 拍板「**現在不要再談什麼修修補補了，整個 review 重寫一次**」。

- 整本 Concept corpus 87.5% 是空殼 → 升 BGE-M3 / reranker / API stack 都是 GIGO，浪費工
- Phase A/B 拆分本身是錯誤抽象 — 它假設「concept 創建可以晚點補」，跟 P1 aggregator 哲學根本衝突
- 整個 ingest pipeline metrics 沒有一條測 page body 是否真有 aggregated content

## 修修對 agent 信任受損

修修 2026-05-06 對話末尾講「**我現在開始不信任你的能力了，我需要找另外一個 model 來一起工作**」→ 安裝 codex-plugin-cc + OpenAI Codex CLI 0.128.0。下次 strategic review 應該主動 invoke `/codex:rescue` 拿 GPT-5 獨立評估，避免單一 model bias。

## ADR-020 v2 凍結 (2026-05-06 same day)

走完三家 multi-agent panel:
- Claude v1 draft → Codex (GPT-5) audit → Gemini 2.5 Pro audit → Claude v2 integrate → 修修 sign-off
- v2 status: **Accepted**，文件 [`docs/decisions/ADR-020-textbook-ingest-v3-rewrite.md`](../../docs/decisions/ADR-020-textbook-ingest-v3-rewrite.md)
- 兩份 audit verbatim 存 `docs/research/2026-05-06-{codex,gemini}-adr-020-audit.md`

v2 凍結的 invariants:
- Three-phase: Lossless Source Ingest + Per-Chapter Sync Concept Aggregation (4-action dispatcher 重啟) + Concept Maturity Model L1/L2/L3
- Verbatim body + LLM structured wrapper（不 paraphrase）
- BGE-M3 + bge-reranker-large + Parent-Child chunking + Hybrid retrieval (non-negotiable, Gemini 必修)
- Bilingual term mapping discipline: concept frontmatter 加 `en_source_terms` 欄位，每次 ingest populate
- Vision 6-class triage（Quantitative / Structural / Process / Comparative / Tabular / Decorative + multi-panel grouping）
- LLM-classified coverage manifest (primary/secondary/nuance hierarchical, 不用 regex)
- L3 active stub == ingest fail；L2 stub 是 productive workflow state（Cori cycle 等 niche-but-critical 有歸宿）
- 既有 544 stub cleanup: S8 一次性 re-ingest Sport Nutrition + BSE

## 暫緩的工作（優先級低於 ADR-018）

- **Hybrid retrieval 升級**（BGE-M3 + jieba-tw + bge-reranker-v2-m3）— corpus 健康後再評估，可能根本不需要
- **PR #440** feedback memory squash — 已 open，CI 過後自動進 main
- **4 個 manual smoke 測試** #431-#434 — 已標 in_progress 但 lower priority
- **PRD #430 §S5** engine default swap — 維持 deferred 1-3 個月

## References

- ADR-016 §2.1 row B + §3 metrics（破洞源頭）
- `.claude/skills/textbook-ingest/prompts/phase-b-reconciliation.md` line 80-100, line 171
- `feedback_quality_over_speed_cost.md` — 修修最高指導原則
- `feedback_kb_concept_aggregator_principle.md` — Karpathy aggregator 哲學
- 研究員報告（hybrid retrieval SOTA）— 不在這次 session 動工，放在 conversation context 裡有 BGE-M3 + reranker + jieba-tw 詳細 stack
