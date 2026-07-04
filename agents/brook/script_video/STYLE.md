# foundry editorial rubric

Single source for broll-pipeline editorial taste. Loaded by `planner.py` into every prompt. Grows over time as `edit_log/*.jsonl` entries are reviewed and promoted into examples; *固化*的規則沉澱回這份。

Not a brand document — brand tokens live in `docs/design-system.md` (canonical). Not a behavioral feedback document — that's `memory/claude/feedback_*.md`. This file is **pipeline-specific editorial judgment**.

---

## Hard rules（必遵守）

- **B-roll service 的是 idea，不是 sentence**：一個 beat 是一個 idea unit，可跨多句 SRT。配合 §6 LLM 自然分群。
- **Restraint budget**：10 分鐘影片預期 ~15-25 個 cutaway。連續兩 beat 不用同一個 component（避免視覺重複）。
- **Anti-literal**：B-roll 添加資訊或情緒，不要畫名詞（「成長」→ 上升箭頭是 lazy slop）。
- **Anti-hype**：拒用 money-count 巨大數字效果（除 BigStat fork）、neon caption、glitch RGB、TikTok/IG follow overlay。Brook editorial tone 是 anti-hype, engineering-grounded（見 `docs/design-system.md`）。

## Soft rules（默認，可破除但要寫進 edit_log）

- **數字 > 1000**：傾向用 BigStat（除非該 beat 已有其他視覺強調）
- **長定義 / 列舉句**：傾向 `side_overlay_left` + caption-* component
- **抽象概念**（如「身分認同」「動機」）：留 talking head，**不要強塞 B-roll**
- **章節切換**：用 `TransitionTitle` （Phase 1.5 加 component）

## Phase 1 vocabulary（縮限）

- **layouts available**：`full_aroll` / `full_broll` 兩種
  - Phase 1.5 開放：side_overlay_left/right、pip_corner_br
- **render_target available**：僅 `hyperframes`
  - Phase 1.5 開放：reader-playwright、web-playwright
- **components available**：僅 `bigstat`
  - Phase 1.5 / Phase 2 開放：TransitionTitle、DataChart、Map、Caption 各式

Planner prompt 必須 enforce 這個 vocabulary subset（產出 layout / component / render_target 不在此清單時 hard-fail）。

---

## Long-form 編輯哲學（隨 edit_log 累積長大）

（此節由 edit_log 提煉的固化教訓填入。Phase 1 留空作 placeholder。）
