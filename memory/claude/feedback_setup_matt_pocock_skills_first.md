---
name: Matt Pocock engineering skills 跑前必先 setup-matt-pocock-skills
description: skills 自己不知道你的 issue tracker / triage labels / domain layout，沒跑 setup 結果就是每次 skill 都自己探勘+猜+用詞飄
type: feedback
created: 2026-04-29
---

裝了 Matt Pocock 的 8 個 engineering skill 之後，必須先跑 setup-matt-pocock-skills（落地 3 檔到 `docs/agents/` + 1 段到 CLAUDE.md `## Agent skills`）才能用順。

**Why:** 2026-04-27 跑 `/improve-codebase-architecture` 出 6/12 誤判（[project_codebase_architecture_audit.md](project_codebase_architecture_audit.md)）正是 skill 沒讀到 ADR 就憑表面結構亂猜重構候選。Setup skill 的存在就是把「issue tracker 在哪、triage label 用哪些 string、ADR/CONTEXT 路徑（nakama 是 `docs/decisions/` 不是 `docs/adr/`）」沉澱進 routing 區塊，避免每次 skill 都自己探勘。

**How to apply:**
- 新 repo 裝 Matt skills 第一件事跑 setup-matt-pocock-skills
- 已裝過的 repo（nakama 在 commit `1853465` setup 完）— 平時不用碰，但若改 issue tracker / 重命名 ADR 目錄等要重跑或手動更新 `docs/agents/*.md`
- nakama 的 setup 三檔：`docs/agents/issue-tracker.md`（GitHub via gh CLI）、`triage-labels.md`（5 default labels 已 provision）、`domain.md`（multi-context + ADR 在 `docs/decisions/`）
- skills 之間 chain：`/grill-with-docs → /to-prd → /to-issues → /tdd|/diagnose → /improve-codebase-architecture`，前 3 個合稱「燃料生產線」給 AFK 解 issue
- skills repo 跟 sandcastle 是兩個獨立 repo（前者免費 / 後者也免費但要 Docker 設定），skill chain 產 issue、sandcastle 解（詳見 [reference_sandcastle.md](reference_sandcastle.md)）
