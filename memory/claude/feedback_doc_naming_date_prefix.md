---
name: 時間性 doc 檔名走 YYYY-MM-DD 前置（排序用）
description: docs/plans · research · task-prompts 的時間性檔案用 YYYY-MM-DD-{topic}.md 前綴；runbooks · diagrams · principles · ADR 不加日期；phase-N-* 系列保留 phase 標識
type: feedback
originSessionId: 70b01225-94d6-4548-a285-53270c088e26
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
| `docs/runbooks/{action-target}.md` | 純 topic | **長期 reference runbook**（如 `cf-waf-skip-rules.md` 列出所有規則 + 加新規則 SOP）— 常駐查閱，不是時間紀錄 |
| `docs/diagrams/` | `{topic}.md` | 常駐技術圖 |
| `docs/principles/` | `{principle}.md` | 常駐原則 |
| `docs/decisions/` (ADR) | `ADR-NNN-{topic}.md` | ADR 用編號排序 |
| `docs/capabilities/` | `{capability}.md` | 常駐能力卡 |

### Runbook 兩種：long-term reference vs one-shot task

**這是 2026-04-27 修修要求補的細節**：runbook 不是一刀切「不加日期」，要分兩種：

| 種類 | 檔名 | 例子 | 何時用 |
|---|---|---|---|
| **Long-term reference** | `{action-target}.md`（不加日期） | `cf-waf-skip-rules.md` / `deploy-usopp-vps.md` / `setup-wp-integration-credentials.md` | 列出所有同類規則 / SOP / 常駐查閱資訊 |
| **One-shot task instruction**（要修修這次跑的） | `YYYY-MM-DD-{verb-target}.md` | `2026-04-27-add-nakamabot-cf-skip-rule.md` / `2026-05-02-davinci-import-smoke.md` | 一次性 task — 修修跑完就不會再回頭看；連結到 long-term reference 表格更新 |

**⚠️ Default = 加日期，反覆教訓（2026-04-27 / 2026-05-02 兩次踩同坑）**：

寫 runbook 時若想到「未來也會跑」、「這是 acceptance gate template」、「概念上 evergreen」就跳 long-term reference 軌 = **反射性誤判**。判斷標準是「**修修現在拿到要做嗎**」：

- 「修修現在 PR #320 ship 了，要他跑 smoke」→ **one-shot，加日期** ← 即使概念上每次 ship 都會跑
- 「列出所有 CF skip rule 表格供查閱」→ long-term reference，不加日期
- 「DaVinci smoke 通用 SOP（不綁特定 PR）」→ long-term reference，不加日期 — **但這是另一份 doc**，one-shot 要 link 進去

寫完落檔前的 self-check：「**這份是不是綁特定 PR / Slice / 日期事件？**」是 → date prefix。修修不會去 grep evergreen 找「我這次要做的事」。

**配對規則**：one-shot task doc 要 link 到 long-term reference doc（如 task doc 開頭寫「rule 加進 cf-waf-skip-rules.md 表格」）。Long-term reference 表格 row 也要 link 回 setup task doc 路徑（per cf-waf-skip-rules.md 表格新增「Setup task doc」column 範例）。

**Why 修修要求**：要他執行的 runbook 跟 long-term reference 混在 `docs/runbooks/` 沒日期，他**不知道哪份是「現在要他做」哪份是「過去做完的紀錄」**。Date prefix 一眼看出 `2026-04-27-...` 是新的待做 task，純 topic 是常駐 reference。

**配套規則**：要修修執行的 task doc **必須在主 worktree 立刻可見**（不能只在 PR worktree / feature branch 上等 PR merged）— 修修不會 git checkout 別人 branch 看 task doc。做法：
- 寫主 worktree path（`F:/nakama/docs/runbooks/...`）讓修修立刻能在 Cursor / Obsidian 開
- 同時 cherry-pick / 同步到 PR worktree commit 進 PR（兩邊內容一致；PR merge 後主 tree pull 自然 dedupe）

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
