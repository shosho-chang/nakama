---
name: 2026-04-27 codebase architecture audit
description: 12 deepening 候選 + ROI 排序 + 哪些 done 哪些 pending；下次想做架構改善先看這份再決定要不要重跑 skill
type: project
originSessionId: 3d901e7b-183e-450a-a0bf-2f06311b6452
---
2026-04-27 第一次跑 `/improve-codebase-architecture`（mattpocock skill）對 nakama repo 完整掃過，產出 12 個 deepening 候選。

**完整清單 + 細節**：[docs/research/2026-04-27-codebase-architecture-audit.md](../../docs/research/2026-04-27-codebase-architecture-audit.md)

**已完成**：
- ① LLM facade shallow + ⑤ anthropic/gemini 樣板重複 — PR #208 merged 2026-04-27 為 `e043e2a`
- ⑨ doc_index — PR #211 merged 2026-04-27 為 `9362cfe`（**audit 描述誤判** — 不是 shallow pass-through，是 Windows path bug；`as_posix()` 一行 fix 修綠 `test_stats_returns_per_category_counts`）
- ⑪ seo_enrich re-export — PR #212 merged 2026-04-27 為 `a095ad8`（`seo_audit/__init__.py` 早就 OK，只有 `seo_enrich` 是空的）
- ④ sanitizer 收編 — PR #214 merged 2026-04-28 為 `97fe5b2`（**audit framing 誤判** — 不是「三套 sanitizer 統一」，而是兩個 compliance scanner 重複；只 migrate Brook compose + seo-audit skill 到 Slice B 完整 vocab，`gutenberg_validator` / `kb_writer` slug regex 是不同關注點留原處；net −57 LOC）

**Pending ROI 排序**：
- **② approval FSM + schema 鎖死** — Chopper Phase 2 blocker
- **③ KB writer interface 收緊** / **⑥ memory tier interface** / **⑦ prompt loader 顯式 dep** / **⑩ anomaly 抽得不夠廣** — 中優先
- **⑧ Usopp publisher 600 行 monolith** — 等真要做 Chopper 留言 publisher 才動

**audit skill 教訓（2 次驗證後）**：候選 ⑨ + ④ 都吃過 audit framing 誤判 — ⑨ 寫「shallow pass-through」結果是 FTS5 真實邏輯 + Windows path bug；④ 寫「三套 sanitizer 統一」結果是兩個 compliance scanner deprecation 沒收尾、第三方完全不該被綁進來。下次跑 skill **要把候選 deletion test + module shape 都對 file verify**，不要只看 audit 描述當真，跑 grilling step 3 時直接挑戰 audit 自己的 framing。

**Why**：audit 的價值不在「找一次」，在「baseline 對照下次掃」。下次跑 skill 時拿這份比，看新候選浮現 / 老候選是否還在。

**How to apply**：要動架構任務前先讀這份。已 done 的不重新分析；pending 的按 ROI 排。決定動哪一個之後，再跑 grilling loop（skill step 3）。重跑整個 audit 的觸發條件：sprint boundary、加新 agent、完成 backlog ≥3 項。
