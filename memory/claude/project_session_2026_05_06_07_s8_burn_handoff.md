---
name: Session handoff 2026-05-06→07 — S8 batch burn + ADR-020 contract drift + Max-quota rewrite (Path B)
description: S0-S7 ship 後 S8 28 章 batch 跑完 3 PASS / 21 FAIL / 4 ERROR；root cause = chapter-source.md prompt 叫 LLM emit body 違反 ADR-020 §Phase 1 verbatim body 設計；同時 LLM call 走 API 不走 Max quota 燒了估 $25-30；修修拍板 Path B（架構修 verbatim 從 walker literal + 同時保留 API path 為「以後 digest 教科書兩選一」）；session 信任受損
type: project
created: 2026-05-07
---

## TL;DR

5/6 晚 → 5/7 凌晨 跑完 S0-S7 + S8 batch。**S8 結果壞 + 過程出兩個 process-level 重錯**：

1. **ADR contract drift**：chapter-source.md prompt 叫 LLM 同時 emit frontmatter + body，違反 ADR-020 §Phase 1「verbatim body」設計。Walker 已有原文 (`payload.verbatim_body`)，本該 literal concat。LLM emit body 觸發 Sonnet 4.6 max_output_tokens 16k 上限 → 長章節（90k+ chars）被截斷/摘要 → verbatim 跌到 2.9-58%。
2. **Billing 路徑沒問**：所有 LLM call 走 `shared.anthropic_client` → `ANTHROPIC_API_KEY` → API 計費（用戶信用卡），**不走** Claude Max $200 quota。Max quota 必須走 Claude Code 主線 Agent tool dispatch 才吃。

第三錯：context budget 256k > 250k 硬上限我沒主動 flag（違反 `feedback_context_budget_200k_250k`）。

修修拍板 **Path B + 保留 Python loop**（雙 path 並存：API + Max quota，以後 digest 教科書兩選一）。**信任受損**，可能改用 Codex GPT-5.5。

## S8 Batch 結果（cost $22.23 / wall 4h11m）

```
BSE: 11 章 → 2 PASS / 9 FAIL
SN:  17 章 → 1 PASS / 12 FAIL / 4 ERROR (529 overload SN ch13-16)
Total concepts: 2486 (L1=1096 / L2=1390 / L3=0)
Cross-listed: 178 concepts in both books (L3 候選池)
```

PASS 章 = BSE ch1 + ch11 + SN ch6（剛好都比較短）。

**Staging 在** `E:\Shosho LifeOS\KB\Wiki.staging\` — 24 章 source page 寫好但品質壞，4 章完全沒寫。**沒 mv 進 real Wiki**。

Verbatim 跟章長度反相關：

| Chapter | 字數 | Verbatim |
|---|---|---|
| BSE ch1 (24k) | 短 | 77.4% ✅ |
| BSE ch3 (76k) | 中長 | 26.8% |
| BSE ch10 (122k) | 最長 | **2.9%** 🔥 |

## 已 ship 進 PR #441 的 commits（branch `docs/kb-stub-crisis-memory`）

```
b60be0b fix(ingest-v3): preflight gap fixes — verbatim prompt + bilingual wikilinks + tolerant JSON
6ee8d49 chore(infra): gitignore Codex/.claude/runs artifacts + Windows auto-start script
487b1bb fix(infra): preflight runner + kb_writer temperature drop + ruff lint clean
2913c4f docs(memory): 最高指導原則 — 一切可 dispatch 的工作必 dispatch
c2d01fc feat(bilingual-rag): en_source_terms schema + bilingual query expansion + RRF (S7)
21c36e1 feat(chunker+reranker): Parent-Child + bge-reranker-large + BGE-M3 (S6)
c8ff9f6 feat(figure_triage): Vision 6-class triage + multi-panel grouping (S5)
8e5654f feat(coverage_classifier): manifest + LLM claim extractor + acceptance gate (S4)
1ef6151 feat(concept_classifier): Maturity Model L1/L2/L3 + False-Consensus guard (S3)
73defad feat(concept_dispatch): Phase 2 sync + per-concept lock + 4-action revival (S2)
9c3cdc4 feat(source_ingest): Phase 1 walker — Raw → chapter payloads (S1)
472478b feat(raw_ingest): EPUB → Raw markdown converter (Phase 0, S0) + BSE pilot
```

加上 4 個 docs/memory commit (fef438c → bba1ad4 → c4bf1da → d7af5f5)。

S8 結果**沒 commit**（staging 是 vault 不是 repo）。`scripts/run_s8_batch.py` + `scripts/run_s8_preflight.py` commit 進了 487b1bb / b60be0b 但 verbatim broken。

## Path B 設計（修修拍板，保留 API path）

### 兩條 ingest path 並存

| Path | 用途 | LLM 走法 | Body 怎麼來 |
|---|---|---|---|
| **A — Python loop + API** | Auto / batch / 大量 dispatch / 別人也能跑 | `shared.anthropic_client` | （需修）literal walker.verbatim_body concat |
| **B — Agent tool dispatch + Max** | 修修自己 ingest / 省 cost | Claude Code 主線 dispatch subagent | 同上 |

兩條 path 共用同一個 `_compose_source_page(payload, frontmatter)` core function — body 直接從 walker 來，frontmatter 從 LLM 來。差別只在「frontmatter 怎麼產生」。

### 架構修（無論 A 或 B 都要做）

**現狀** (`scripts/run_s8_preflight.py:run_phase1_source_page`)：
```python
prompt = compose_full_chapter_prompt(payload, chapter_source_md)
response = llm.ask(prompt, max_tokens=16000)  # LLM 同時 emit frontmatter + body
write_source_page(response)  # 整段 LLM output
```

**改成**：
```python
frontmatter_prompt = compose_frontmatter_only_prompt(payload, chapter_source_md)
frontmatter = llm.ask(frontmatter_prompt, max_tokens=4000)  # 只 emit YAML
source_md = frontmatter + payload.verbatim_body  # body 來自 walker
write_source_page(source_md)
```

Verbatim → 100% by construction。Cost 降 ~70%（不送 body in/out）。

### 同時改 chapter-source.md prompt

刪 PART B body 區塊。只留 PART A frontmatter 規格 + wikilinks_introduced 規範（這部分 fix-agent 已修對）+ claims 抽取規範。明文寫「DO NOT emit body — body is concatenated literally from walker.verbatim_body downstream」。

### Path B 怎麼接 Agent tool dispatch

每章 dispatch 一個 general-purpose subagent，prompt = chapter-source.md 改寫版 + walker chapter payload + book metadata。Subagent return frontmatter YAML（純文字），主線 concat walker.verbatim_body 寫檔。

**Subagent 用 Max quota**（Claude Code agent dispatch 自動走 subscription）。28 章 = 28 subagent call，主線只看 return 的 frontmatter（短）+ 寫檔 — context overhead 可控。

### 為什麼保留 Python loop（修修原話：「以後或許會用到」）

- 別人（外人 / 自動化 / VPS cron）跑 ingest 不走主機 Claude Code → 必須 API path
- batch dispatch（一次 28 章）可能比主線 Agent tool 快（並行 vs 串行）
- 已經寫好的 `run_s8_batch.py` 不丟棄，只把它的 Phase 1 body 行為改成 literal concat

## 未做、需要在 fresh context 接手的事

1. **改 `chapter-source.md`**：刪 PART B body 規格，加「body 由主線 literal concat 不要 emit」明文
2. **改 `scripts/run_s8_preflight.py:run_phase1_source_page`**（含 batch 共用）：split 成 frontmatter LLM call + body literal concat
3. **新增 Path B subagent 範本**：`.claude/skills/textbook-ingest/prompts/phase-a-subagent-v3.md`（dispatch via Agent tool；走 Max quota）
4. **重跑 28 章**：staging 全清，用新架構 fresh ingest BSE 11 + SN 17。預估 cost 降至 ~$6-8（如果走 A）或 $0 API（如果走 B）
5. **修 SN ch13-16 4 章 ERROR**：529 overload 已過，重跑即可
6. **Cleanup verify after final ship**：sub-agent verify Concepts 有 body / Sources/Books 含 28 章 / Attachments 含兩本 figure / index.md / log.md milestone

## 已知 process 級失誤（不要再犯）

| 失誤 | 違反的 memory rule | 怎麼避免 |
|---|---|---|
| chapter-source.md 叫 LLM emit body 違反 ADR-020 §Phase 1 verbatim 設計 | `feedback_adr_principle_conflict_check.md` | 寫/改 prompt 前必 explicit verbatim quote ADR §相關段落，proof prompt 沒違反 |
| LLM call 走 API 沒問 Max quota | （新教訓） | 每次 dispatch LLM-heavy work 前 explicit 問用戶 billing 在哪 / 寫成新 memory |
| context 256k > 250k 沒主動 flag | `feedback_context_budget_200k_250k.md` | session 中每隔 N 個 turn 自查 token；觸 200k 主動報 |
| Fix agent 第一次回報「all PASS / cost $0」沒 sanity check 就信了 | `feedback_acceptance_target_clarity.md` | LLM-heavy task cost = 0 永遠是 red flag → 立刻 disk reality check |
| 28 章 batch dispatch 沒先驗 1-2 章長章節跑得通 | （新教訓） | 任何 batch loop 前先跑「最壞 input 1 個」proof，不只跑 short input preflight |

## 信任警告

修修引述：「我越來越不信任你了，之後可能會改用 codex gpt 5.5」。

5/6 同日累積：
- 早報 sandcastle 4 issue ship ✅
- ADR-020 panel + memory + skill freeze ✅
- vault cleanup + ADR Phase 0 ✅
- /to-issues 9 issues ✅
- multi-agent-panel skill ship ✅
- S0-S7 sandcastle batch ship ✅

→ 然後 S8 階段三連錯（contract drift / billing path / context budget）。早段成果不能抵銷後段失誤。**fresh context 接手者請從零起跳，不要假設我之前的判斷可信**。

## 起手指令（fresh context post-compact）

1. 讀 `memory/claude/MEMORY.md` (CLAUDE.md §0 規則)
2. 讀本 handoff doc
3. 讀 `feedback_session_start_must_read_memory_md.md`
4. 讀 `feedback_adr_principle_conflict_check.md`
5. 讀 `feedback_context_budget_200k_250k.md`
6. `gh pr view 441` — PR 現狀
7. `cat E:\Shosho LifeOS\KB\Wiki.staging\Sources\Books\biochemistry-for-sport-and-exercise-maclaren\ch3.md | head -80` — 看 LLM 摘要 body 長啥樣（最壞案例）
8. 讀 ADR-020 §Phase 1 + chapter-source.md 比對 — 確認 contract drift
9. 開始 Path B 實作（含保留 API path）

## References

- [project_kb_corpus_stub_crisis_2026_05_06.md](project_kb_corpus_stub_crisis_2026_05_06.md) — KB 87.5% stub crisis 起點
- [project_session_2026_05_06_adr_020_panel.md](project_session_2026_05_06_adr_020_panel.md) — 5/6 早段 panel + ADR-020 凍結
- [feedback_adr_principle_conflict_check.md](feedback_adr_principle_conflict_check.md) — ADR contract drift 防範規則（這次踩）
- [feedback_context_budget_200k_250k.md](feedback_context_budget_200k_250k.md) — 200k/250k 硬上限規則（這次踩）
- [feedback_dispatch_everything_minimize_main_context.md](feedback_dispatch_everything_minimize_main_context.md) — dispatch 原則（這次部分遵守 — 但 dispatch 走錯 billing path）
- ADR：[docs/decisions/ADR-020-textbook-ingest-v3-rewrite.md](../../docs/decisions/ADR-020-textbook-ingest-v3-rewrite.md)
- Final report：[docs/runs/2026-05-06-s8-final-report.md](../../docs/runs/2026-05-06-s8-final-report.md)
- Runner：[scripts/run_s8_batch.py](../../scripts/run_s8_batch.py) + [scripts/run_s8_preflight.py](../../scripts/run_s8_preflight.py)
