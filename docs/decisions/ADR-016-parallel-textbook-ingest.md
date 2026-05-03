# ADR-016: Parallel Textbook Ingest — Phase A subagent fan-out + Phase B serial reconcile

**Date:** 2026-05-03
**Status:** Accepted
**Extends:** [ADR-011](ADR-011-textbook-ingest-v2.md) (textbook-ingest v2)

---

## 1. Context

[ADR-011](ADR-011-textbook-ingest-v2.md) 凍結 textbook ingest v2 的四條原則（P1-P4）+ workflow（vision describe per fig + chapter source compose + 4-action concept dispatcher）。Phase 1 (PR C) 用 *Biochemistry for Sport and Exercise Metabolism* (BSE, MacLaren & Morton, Wiley 2024) ch1-ch2 驗證後，PR D 接手 ch3-ch11 (9 章) 的 ingest 與 ch1 重 ingest（用 ch3-style 結構化英文取代 PR C 中文敘事版）。

PR D 後段（2026-05-03 桌機 session）有兩個 wall-time 觀察：

1. **章與章之間 share-nothing**：每章的 walker output 獨立、attachments 獨立、`ch{N}.md` 寫入獨立檔案 — 沒有 cross-章 mutable state 依賴。
2. **序列 ingest 浪費**：v2 workflow 規範一章一章跑（一個 Claude Code turn 一章），ch5+ch6 兩章手工序列做下來 ~30 min wall time / 章。剩 ch7-ch11 五章預估 ~3-5 hr。

修修在 session 中提出：「能不能用 Sandcastle 讓 Agent 平行去做處理？一個 Agent 處理一章就好。」進而辨認出真正的 conflict 點是 **Phase B 的 Concept page 創建 + `mentioned_in:` backlink 更新 + KB/index.md / log.md append** — 這些都是共享 mutable state（多 agent 同時 read-modify-write 會 race）。

PR D 隨後跑了三輪平行 ingest 驗證，本 ADR 把學到的固化。

**援引原則**：
- ADR-011 §2 P2 (LLM-readable deep extract — 不省 token / 用最強 model)、P3 (圖表 first-class — vision describe per fig)
- [`docs/principles/reliability.md`](../principles/reliability.md) §1 idempotency（重 ingest / retry 不可 double-write）
- Karpathy 跨 source aggregator 哲學（Phase B 維持 cross-source merge / dedup 是序列 invariant）

---

## 2. Decision

### 2.1 Phase split — A 平行 / B 序列

把 ADR-011 §3.3 Step 4 (per-chapter ingest) 拆成兩 phase：

| Phase | 內容 | 並行性 | Owner |
|-------|------|--------|-------|
| **A** | 讀 walker output → vision describe figs → compose `ch{N}.md` 寫到 vault Sources path | **fully parallel**（一章一 subagent，share-nothing） | per-chapter background subagent |
| **B** | 提取 wikilinks → dedupe vs existing Concepts → 創 stub Concept pages → update `mentioned_in:` 全 dedupe → Book Entity status / KB/index.md / KB/log.md | **serial within itself**（共享 mutable state） | 單一 Robin background subagent |

Phase A subagent 的 hard constraint（baked into prompt，違反就是 bug）：
- 只 write 自己的 `ch{N}.md`
- DO NOT touch `KB/Wiki/Concepts/`、`KB/index.md`、`KB/log.md`、Book Entity、sibling `ch{X}.md`、任何既存 Concept page 的 `mentioned_in:`
- DO NOT touch nakama repo
- 不跑 git / install / rm

**為什麼 share-nothing**：每章的 wikilink target 不需要先創 Concept page 才能用 — Obsidian 對 unresolved wikilink 渲染為紅 link，phase B 補完後自然 resolve。Body wikilink 寫 `[[糖解作用]]` 不會因為 `Concepts/糖解作用.md` 不存在而 fail。

### 2.2 Staged-write protocol（chapters with figs ≥ 20）

Pilot 揭露：subagent 讀完 walker + 全部 PNG vision + style ref 後，context 累積 ~80-130K tokens；compose `ch{N}.md` 需要在單一 inference round 生 ~25-35K output tokens，這個生成階段沒有 tool calls（純 model output），會在 600s 後撞 stream watchdog 被 kill — 三章（ch9 26 figs / ch10 44 figs / ch11 36 figs）first attempt 全 timeout。

**Threshold**：figs ≥ 20 章節必須用 staged-write protocol。實測 ch7 (19 figs) / ch8 (17 figs) 單一 Write 安全，ch9-ch11 必 staged。

**Protocol**：

| Step | 動作 | 為何 bounded |
|------|------|-------------|
| **W1** | Write `ch{N}.md` = 完整 frontmatter（all figs `llm_description` 全填）+ body skeleton（每節 H2 heading + `TODO-<id>` placeholder body） | frontmatter 是 transcribe 已知 vision 結果，~5-10K tokens 一次 Write |
| **W2..Wn** | 對每節做一次 Edit：找 `TODO-<id>` placeholder，replace with rendered section content | 每 Edit 1-3K tokens 生成，watchdog 看見 tool call 就 reset |
| **W-final** | grep `\`\`\`mermaid` 數實際 block 數，與 frontmatter `mermaid_diagrams:` 比對；不 match 一次 Edit 修正 | 1 個小 Edit |

Subagent 收到的指令明確標註「previous attempt stalled at compose-write — staged-write is mandatory」+ 預期 step count 11-15 calls / 章。

### 2.3 Batching（避 API rate limit）

Anthropic API 有 RPM/TPM 限制。同帳號多 session 共用 quota；同 parent 多 background subagent 在 dispatch 後幾秒內全部 fire 會撞 rate limit。

**規則**：
- 一個 session 內同時跑 ≤ 8 個 background subagent
- 跨 session 並行（兩個 window 各 dispatch）需算總和 ≤ 10-12
- 17 章書建議拆 batch 1 = ch1-ch8（8 章），等 batch 1 完成 50%+ 再 dispatch batch 2 = ch9-ch17

### 2.4 Subagent prompt template（self-contained）

每個 phase A subagent 開新 1M context 從零起跳，沒有 parent session 歷史。Prompt 必須 self-contained 包含：
1. Mission statement（這章的 chapter_index、title、預期 fig/table 數）
2. Inputs：walker output 路徑 + style reference path（既存 ch5.md / ch6.md，cross-book reference 也 OK）+ attachments dir
3. Style spec（frontmatter schema + body section pattern + idiom 列表）
4. Phase A hard constraints
5. Walker artifact precedent（ch6 fig-6-12 / ch8 fig-8-2 to fig-8-8 / ch11 fig-11-28 三個案例 reference）
6. Staged-write protocol（如果 figs ≥ 20）
7. DoD report format

Prompt size 5-8K tokens / agent。**不要假設 subagent 有 SKILL knowledge** — 連最基本的 frontmatter schema 都要 inline。

### 2.5 Subagent 自我修正

Pilot 觀察到的 emergent 行為（ch7 / ch8 / ch9 / ch10 / ch11 全展示）：
- 主動辨認 walker pipeline artifact 並依 precedent 處理（不需明確指示）
- 主動 grep mermaid block 數 + 校正 frontmatter（W-final）
- ch11 subagent 在 W-final 抓到自己的 duplicate fig embed 並 Edit 修正

→ Prompt 應 invite self-verification（明列「W-final mermaid count verify」「after composition, sanity-check frontmatter matches actual」），不要假設 subagent 第一輪輸出 perfect。

---

## 3. Pilot metrics（BSE ch5-ch11）

| Chapter | Figs | Tables | Walker LOC | Output lines | Wall time | Mermaid (FM/actual) | Verbatim quotes | Wikilinks | Notes |
|---------|------|--------|-----------|--------------|-----------|---------------------|-----------------|-----------|-------|
| ch5 | 38 | 2 | 622 | 851 | (manual, ~30 min) | 6 / 6 | ~7+ | ~14 | baseline |
| ch6 | 25* | 0 | 363 | 560 | (manual, ~30 min) | 5 / 5 | ~5+ | ~10 | *fig-6-12 = stray arrow，frontmatter notes |
| ch7 | 19 | 2 | 597 | 877 | 11.2 min | 3 / 3 (self-corrected 4→3) | 14 | 74 | first parallel pilot — single Write OK |
| ch8 | 17 | 3 | 303 | 445 | 8.2 min | 3 / 4 (cosmetic — 修修 fix later) | 7 | 33 | walker artifact: fig-8-2 to fig-8-8 inline equations rasterized |
| ch9 | 26 | 2 | 1224 | 787 | 14.5 min (retry, staged) | 3 / 3 (self-corrected 5→3) | 15 | 52 | first attempt watchdog timeout @ compose |
| ch10 | 44 | 0 | 1370 | 965 | 16.9 min (retry, staged) | 4 / 4 (self-corrected 5→4) | 16 | 78 | largest chapter — staged 12 calls |
| ch11 | 36 | 3 | 1442 | 716 | 19.6 min (retry, staged) | 4 / 4 | 25 | 93 | walker artifact: fig-11-28 sprinter clipart |

**Wall time reality**：
- 序列 (ch5+ch6 manual): 60 min for 2 chapters → 270 min projected for 9 chapters
- 平行 ch7+ch8 (single-Write OK): 11.2 min wall (parallel max)
- 平行 ch9+ch10+ch11 (staged-write retry round): 19.6 min wall (parallel max, bottlenecked by ch11)
- Total ch7-ch11: ~31 min wall vs ~150 min sequential = **~5x speedup**
- Including the 10-min initial-attempt waste for ch9-ch11 retries: still ~3.5x net speedup

**Cost reality** (Opus 4.7, Max 200 quota):
- Subagent retries cost extra (re-do vision describe — no cross-run cache)
- ch10 re-vision of 44 PNGs ~80K vision tokens twice = ~160K wasted
- 未來：pre-stage vision cache to `/tmp` 給 retry reuse（見 §4 Open questions）

---

## 4. Consequences / Open questions

### Mitigated risks

- **Style drift across subagents**: pilot 證明每個 subagent 用 ch5/ch6.md 當 style reference + explicit body idiom 列表，產出與 baseline 同質量。Wikilink 密度 / verbatim quote / mermaid 用法都一致。
- **Phase A constraint violation**: 五個 subagent (ch7/8/9/10/11) 全 confirm Phase A clean，沒有出現「subagent 偷偷 update 了 KB/index.md」這類 race。Hard constraint 在 prompt 內 baked 有效。

### New risks

- **Vision re-cost on retry**: 失敗的 subagent 已花 vision token，retry 重做。建議 SKILL 加「subagent W0 step：把 vision describes write to `{tmp}/vision-cache-{ch}.json`，retry 從 cache load」。
- **Concept page 重複創建 race**: phase A 完全不創 concept page 是 invariant；若未來放鬆此 constraint（例如允許 phase A 自動創 stub），race 會回來。要在 SKILL 強調 phase split 是 load-bearing。
- **Cross-book Phase B 序列化 bottleneck**: 多本書平行 ingest 時，每本各自跑 phase B（serial within），但兩本書同時跑 phase B 會競爭 KB/index.md / log.md。短期 OK（手動排程兩本書 phase B 不重疊）；長期需要 KB-level write coordinator。

### Open questions

1. **Sandcastle 適用性**: 本 ADR 用直接 `Agent({run_in_background:true})`，沒用 Sandcastle isolation。Sandcastle 適合需要 git/build/test 隔離的 destructive 操作；textbook ingest 沒有這種需求，subagent 同 filesystem 寫不同檔案天然不衝突。**未來**若 ingest pipeline 加上「跑 markdown lint / yaml validation」這類 destructive verification step，Sandcastle 才有用。
2. **Phase A token budget per subagent**: pilot 章 80-200K input + 25-35K output。1M context 還有大量餘裕。**未來**章節更大 (200+ pages biology 章節) 可能撞 1M context — 屆時要再拆「per-section subagent」更細粒度。
3. **Phase B agent 自身的 watchdog 風險**: phase B subagent 要讀 11 chapters + 50+ existing Concept pages + write 30-100 files；如果它自己的 compose 也撞 600s watchdog，要套 staged-write 同樣 protocol。BSE phase B 還在跑（dispatched 2026-05-03 session 末），結果出爐後本 ADR 補入 phase B metrics。

### Cost vs benefit

- **Benefit**: 整本書 wall time 從 ~5-8 hr 壓到 ~1-1.5 hr（包含 phase B serial 收尾），quality 不退化。
- **Cost**: subagent retry 浪費 vision tokens（~10-20% overhead）；prompt engineering overhead（每章 5-8K token prompt 要寫，但 template 化後 fan-out cheap）；rate limit 調度需要手動 batching。
- **Net**: 大幅正向，特別是 cluster 多本書 ingest（如修修週末「灌一打教科書」use case）。

---

## 5. Implementation

完成於 2026-05-03 session（同一輪 pilot）：

- ✅ `.claude/skills/textbook-ingest/SKILL.md` 三處 minimal edit：Workflow Overview 加 parallel callout、Pitfalls 加 staged-write watchdog bullet、References 加 ADR-011 + ADR-016 row
- ✅ `.claude/skills/textbook-ingest/prompts/phase-a-subagent.md` parameterized template（self-contained subagent prompt + variables table + domain hint guidance + pitfalls）
- ✅ `.claude/skills/textbook-ingest/prompts/phase-b-reconciliation.md` parameterized template（serial Robin reconcile prompt + variables table + cost/wall-time benchmark）

下次 textbook ingest 流程：driver 讀兩個 template、parameterize、fan-out N 個 phase A subagent，待全完成後 dispatch 一個 phase B subagent。SKILL.md driver 步驟（Step 1-3 walker / outline / confirm）不變。

---

## 6. References

| When | Read |
|------|------|
| v2 ingest 原則 | [ADR-011](ADR-011-textbook-ingest-v2.md) |
| v1 ingest 設計 | [ADR-010](ADR-010-textbook-ingest.md) |
| Skill | `.claude/skills/textbook-ingest/SKILL.md` |
| Pilot session memory | `memory/claude/project_textbook_ingest_2026_05_03_ch1_ch4.md` |
| Karpathy KB Wiki 哲學 | [karpathy gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) |
| Vault path memory | `memory/claude/reference_vault_paths_mac.md` |
| Disk layout | `memory/claude/project_disk_layout_e_primary.md` |
