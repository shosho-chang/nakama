---
name: 時間性 doc 檔名走 YYYY-MM-DD 前置（排序用）
description: docs/plans · research · task-prompts 的時間性檔案用 YYYY-MM-DD-{topic}.md 前綴；runbooks · diagrams · principles · ADR 不加日期；phase-N-* 系列保留 phase 標識
type: feedback
---
**規則：給修修看的時間性 doc 檔名走 `YYYY-MM-DD-{topic}.md` 前置；常駐 doc 走純 topic；ADR 走 `ADR-NNN-{topic}.md`；phase 標識走 `phase-N-{topic}.md`。**

**Why:** 修修明確指令（2026-04-25 PR #150 後）：「可以把給我看的檔案，像是 Plan 或是 Runbook 的日期放到前面嗎？這樣有個排序的效果，我也知道那時候做了什麼事情。」

VS Code Explorer / Obsidian 內檔案是字典序排序。日期後置時 `textbook-ingest-design-2026-04-25.md` 跟 `phase-1-brook-usopp-franky.md` 混在一起無法看出時間軸；日期前置 `2026-04-25-textbook-ingest-design.md` 則同類自動排成時間順序。

**How to apply:**

### 日期前置（YYYY-MM-DD）

| 資料夾 | 為何要日期 |
|--------|----------|
| `docs/plans/` | 設計提案、決策 questionnaire — 對應某天的工作 |
| `docs/plans/*-decisions-*` | 配對 design 提案的拍板檔 — 同日 |
| `docs/research/` | prior-art / 調研 snapshot — 那天那個結論 |
| `docs/task-prompts/` | session handoff / 多機分工 — 那天那次任務 |
| `Case Studies/` (vault) | 案例紀錄 |

格式：`YYYY-MM-DD-{kebab-case-topic}.md`，例：
- `docs/plans/2026-04-25-textbook-ingest-design.md`
- `docs/plans/2026-04-25-textbook-ingest-decisions.md`
- `docs/task-prompts/2026-04-25-dual-window-allocation.md`

### 不加日期（純 topic 或既有編號）

| 資料夾 | 命名規則 | 為何不加 |
|--------|---------|---------|
| `docs/runbooks/` | `{action-target}.md` | 操作手冊常駐查閱，不是時間紀錄 |
| `docs/diagrams/` | `{topic}.md` | 常駐技術圖 |
| `docs/principles/` | `{principle}.md` | 常駐原則 |
| `docs/decisions/` (ADR) | `ADR-NNN-{topic}.md` | ADR 用編號排序 |
| `docs/capabilities/` | `{capability}.md` | 常駐能力卡 |

### Phase 標識

`phase-N-*.md` 系列（例：`phase-1-brook-usopp-franky.md`）保留 phase 標識不加日期 — phase 本身已是時序錨點。

### 例外處理

- **歷史檔案**：尾置日期的歷史檔（task-prompts / research）不主動 rename — rename 會 invalidate memory references + PR description link，報酬低風險高；只在大規模整理 PR 內統一改。
- **memory 檔**：保持現有命名（`feedback_*.md` / `project_*.md` / `reference_*.md` / `user_*.md`）— memory 用 type prefix 排序，不適合日期前置。
- **Vault `KB/Wiki/Digests/PubMed/YYYY-MM-DD.md`**：本來就日期前置，沿用。

### 命名 review 時機

寫新 doc → 落檔前自問：「這檔案是時間性的還是常駐的？」
- 時間性 → 日期前置
- 常駐 → 純 topic

寫完後檔名 grep 一下確認沒重複既有 doc 的命名。
