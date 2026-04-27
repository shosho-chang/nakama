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
- ② approval payload helpers push-down — PR #217 merged 2026-04-28（**audit framing 第三次誤判** — audit 寫「FSM `ALL_STATUSES` 跟 payload `action_type` 兩個 namespace 沒鎖、漏寫 silent fail」全錯：兩 namespace 是不同維度根本不該鎖；silent fail 不存在因 Pydantic union + isinstance else-raise 已 fail-fast。real shape 是 4 個 helper 是 isinstance ladder anti-pattern → 把 `target_platform / title / diff_target_id` push down 成 PublishWpPostV1 / UpdateWpPostV1 的 `@property`，刪 4 helper；net +1 LOC，重點在 cohesion shift；不重開 ADR-006 因 FSM/DB schema/DoD 全沒動）

**Pending ROI 排序**：
- **③ KB writer interface 收緊** / **⑥ memory tier interface** / **⑦ prompt loader 顯式 dep** / **⑩ anomaly 抽得不夠廣** — 中優先（需先 verify framing）
- **⑧ Usopp publisher 600 行 monolith** — 等真要做 Chopper 留言 publisher 才動

**audit skill 教訓（3 次驗證後）**：候選 ⑨ + ④ + ② 都吃過 audit framing 誤判 —
- ⑨ 寫「shallow pass-through」結果是 FTS5 真實邏輯 + Windows path bug
- ④ 寫「三套 sanitizer 統一」結果是兩個 compliance scanner deprecation 沒收尾、第三方完全不該被綁進來
- ② 寫「FSM 跟 payload 沒鎖、會 silent fail」結果兩件事都不成立，real shape 是 OOP polymorphism 漏寫

**規律**：audit 看到 textual coupling 就反射說「沒鎖」，但實際上 Pydantic Literal + import-time assert + 顯式 raise 已經把 fail-fast 處理好；audit 真正能找到的是 cohesion 錯位（屬於 OOP 課題不是 type system 課題）。下次跑 skill **必須**對每個候選跑 deletion test + grep 真實 fail path（不只看 module 名）。

**Why**：audit 的價值不在「找一次」，在「baseline 對照下次掃」。下次跑 skill 時拿這份比，看新候選浮現 / 老候選是否還在。

**How to apply**：要動架構任務前先讀這份。已 done 的不重新分析；pending 的按 ROI 排。決定動哪一個之後，再跑 grilling loop（skill step 3）。重跑整個 audit 的觸發條件：sprint boundary、加新 agent、完成 backlog ≥3 項。
