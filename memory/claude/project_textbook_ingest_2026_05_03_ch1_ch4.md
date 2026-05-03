---
name: BSE textbook ingest 完成 2026-05-03 — parallel pilot success + ADR-016
description: BSE textbook (biochemistry-sport-exercise-2024) ch1-ch11 fully ingested 2026-05-03 via parallel Phase A subagents (Opus 4.7 background, staged-write for figs ≥ 20) + Robin Phase B reconciliation; 104 new Concept stubs / Book Entity status complete; ADR-016 + SKILL.md 固化；同期啟動 Sport Nutrition 4E (Jeukendrup) 17 章平行 ingest（separate window, Phase A 8/17 by reset）
type: project
---

2026-05-03 桌機 session 把 textbook ingest 軸線推到階段性大關 — BSE 全書完成 + parallel architecture 驗證 + ADR-016 固化 + 同期啟動下一本書（Sport Nutrition 4E）。

## End-state — BSE (biochemistry-sport-exercise-2024)

| 階段 | 動作 | 結果 |
|------|------|------|
| Phase A 序列（前 session） | ch1-ch4 ingest | ch1 PR C 中文敘事版（2026-04-13）+ ch2 PR C；ch1 重 ingest 2026-05-03 用 ch3-style 結構化英文 (702 行)；ch4 用 cached vision 跳過 vision call (1053 行) |
| Phase A 序列（本 session 前段） | ch5 + ch6 manual in main session | ch5 851 行 / 38 figs / 2 tables / 6 mermaid；ch6 560 行 / 25 figs (fig-6-12 stray arrow) / 0 tables / 5 mermaid |
| Phase A 平行 pilot | ch7 + ch8 background subagents (Opus 4.7) | ch7 877 行 / 19 figs / 11.2 min wall；ch8 445 行 / 17 figs (含 fig-8-2..8-8 inline equations rasterized) / 8.2 min wall — 單 Write OK，無 staged-write |
| Phase A 平行 large（first attempt） | ch9 + ch10 + ch11 single-Write | **三章全 watchdog timeout @ compose phase** — figs 數 26/44/36 都超過 20 threshold |
| Phase A 平行 large（retry, staged-write） | 加 W1 skeleton + W2-Wn Edit + W-final mermaid 校正 protocol | ch9 787 行 / 14.5 min；ch10 965 行 / 16.9 min；ch11 716 行 (fig-11-28 sprinter clipart artifact) / 19.6 min — 全 success |
| Phase B Robin reconcile | wikilink extract → Concept dedup → create stubs → mentioned_in append → Book Entity / index / log | 594 wikilink occurrences / 117 unique slugs / **104 new Concept stubs** / 6 existing updated / Book Entity 4→11 + status:complete / KB/index.md + KB/log.md 寫入 / wall time 10 min / **idempotent** |

**詳細 architecture / pilot metrics / staged-write protocol 全進 [ADR-016](../../docs/decisions/ADR-016-parallel-textbook-ingest.md)** — SKILL.md 三處 minimal edit (Workflow Overview parallel callout + Pitfalls staged-write bullet + References table)。

## 同期啟動：Sport Nutrition 4E (Jeukendrup & Gleeson, Human Kinetics)

- EPUB 拷到 `E:\textbook-ingest\new-book\Sport Nutrition, 4E_ Sport Nutr - Asker Jeukendrup, Msc, PhD.epub`
- 17 章（Chapter 1-17，walker nav = human ch，**不用 rebase**）
- Slug：`sport-nutrition-jeukendrup-2024`
- New window dispatch prompt 在 `E:\textbook-ingest\sport-nutrition-jeukendrup-2024\NEW-WINDOW-PROMPT.md`
- **Status by reset**：walker 17/17 ✓，Phase A 8/17（batch 1 = ch1-ch8 done）；batch 2 = ch9-ch17 還沒 dispatch；Phase B 沒開始
- Style ref：用 BSE ch5.md（cross-book ref，format 同 / domain 不同 OK）
- Phase B 待 Phase A 全完成後另起 session 跑（可參考此次 BSE Phase B 的 Robin prompt — 概念 dedup 邏輯通用）

## 教訓

1. **Vault 在 E: 不是 F:**（避免 stale 路徑） — SOP：寫前查 `obsidian.json` `"open": true`
2. **Python Windows native 不認 /tmp** — 用 `r'C:\Users\Shosho\AppData\Local\Temp\...'` + `encoding='utf-8'`；2026-05-03 改 staging 到 `E:\textbook-ingest\` 對使用者更友善
3. **Cached vision 是巨大省時** — ch1 (PR C frontmatter cache) + ch4 (`/tmp` json cache) 跳過 vision call → ~30 min/章 → 兩章 30 min total。subagent retry 沒 cache 是真痛點 — 未來 SKILL 改 W0 step：subagent vision describe 完寫 cache 到 `/tmp/vision-cache-{book}-{ch}.json`，retry 從 cache load
4. **Parallel subagent architecture works** — Phase A share-nothing fan-out + Phase B serial reconcile = 5x wall time speedup（7-章 BSE batch 31 min wall vs 序列 150 min；詳 ADR-016 §3 pilot metrics）
5. **Stream watchdog 600s threshold** — figs ≥ 20 章節必 staged-write protocol（W1 skeleton + W2-Wn Edit per section + W-final mermaid count fix），否則 compose phase generation 超過 600s 沒 tool call 被 kill。pilot 三章 first-attempt 全 fail 才發現
6. **Subagent 自我修正 emergent** — 五個 phase A subagent 全主動：辨認 walker artifact 並依 precedent 處理 / grep mermaid count 校正 frontmatter / W-final 抓自己的 duplicate fig embed 並修。Prompt 應 invite self-verification 不要假設第一輪 perfect

## Reference

- ADR：`docs/decisions/ADR-011-textbook-ingest-v2.md` (v2) + `docs/decisions/ADR-016-parallel-textbook-ingest.md` (parallel architecture, this session)
- Skill：`.claude/skills/textbook-ingest/SKILL.md`（更新含 parallel callout）
- Prior session memory：`project_ingest_v2_step3_in_flight_2026_04_26.md`
- Vault path memory：`reference_vault_paths_mac.md`
- 桌機 disk layout：`project_disk_layout_e_primary.md`
- Walker pipeline：`.claude/skills/textbook-ingest/scripts/parse_book.py`
