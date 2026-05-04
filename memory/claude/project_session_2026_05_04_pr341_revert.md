---
name: 2026-05-04 PR #341 revert + sandcastle 流程紀律 + 三層防呆 pending
description: skill chain to-issues 後我自跑 /tdd 違反「sandcastle 解 issue」紀律；revert + 防呆三層待做
type: project
---

**教訓 2026-05-04**：grill-with-docs / to-prd / to-issues 跑完 PRD #337 + 拆 issue #338/#339/#340 後，我直接 invoke /tdd skill **自己**做 TDD（commit + push + PR #341），違反 `feedback_setup_matt_pocock_skills_first.md`「skill chain 產 issue、sandcastle 解」紀律。修修怒，要求「結構性防呆，不只是再寫一條 memory」。

**Why:** /tdd skill prompt 自帶 RED→GREEN cycle 說明，我把它當「我（Claude）的執行手冊」直接跑，沒檢查「該由誰跑」。純文件 reminder（CLAUDE.md / runbook / memory）擋不住，已踩過。

**How to apply:**
- 看到自己即將 invoke `/tdd` / `/diagnose` / `/improve-codebase-architecture` → **停**，檢查是否該走 sandcastle
- 正確流程：to-issues 後 `gh issue edit <N> --add-label sandcastle` → 提示修修「sandcastle 隨時可跑」→ 等指令 → 不自進 /tdd

---

## 已做（救援）

- PR #341 close + remote/local branch 刪
- main 退回 `4eb7825`（乾淨）
- `#338` 加 `sandcastle` label
- 修修出門前進 sandcastle 解 #338

## 待做（修修回來再做，不要在 compact 後忘記）

三層結構性防呆（修修 ack「全做」前不擅自跑）：

1. **Layer 1 — PreToolUse hook 機器擋**
   - `.claude/hooks/check_skill_routing.sh`：偵測 `tool_input.skill ∈ {tdd, diagnose, improve-codebase-architecture}` → exit 2 + 訊息
   - settings.json `hooks.PreToolUse` matcher=Skill 配置

2. **Layer 2 — CLAUDE.md `Agent skills` 段加 sub-section「Skill chain execution boundary」**
   - 表格列：哪 skill Claude 跑 / 哪 skill sandcastle 跑 / 哪 skill 修修跑
   - to-issues 後 mandatory 步驟（add label → 等指令）

3. **Layer 3 — feedback memory `feedback_skill_chain_sandcastle_boundary.md`**
   - 沉澱本次教訓 + 觸發詞警示

修修明確說：「等一下再做，先跑流程」。回來後等他確認「全做 / 只某幾層」再實作 + 一條 PR。

## In-flight 工程狀態（給 compact 後的 session）

- PRD #337 + Slice 1 #338 / Slice 2 #339 / Slice 3 #340 已建好
- `agents/robin/CONTEXT.md` 已含 grill 詞彙與 pipeline（commit 在 PR #341 內，PR 已 close → 這個檔案在 main 不存在了，sandcastle 跑 #338 會自己重建）
- ADR-017 不存在於 main（隨 #341 一起被 revert）— sandcastle 跑 #338 acceptance 含「ADR-017 寫入」會自己寫
- `.tmp-prd-body.md` / `.tmp-slice-1.md` / `.tmp-slice-2.md` / `.tmp-slice-3.md` / `.tmp-pr-body.md` 5 檔 untracked，修修自行清

下一條對話進來，先看本記憶 + `gh pr list` 看 sandcastle 跑出的 PR。
