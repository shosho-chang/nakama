# Reference — 自由艦隊 gamification（已遷往 shared）

**Type:** reference
**Created:** 2026-08-24；**Superseded:** 2026-08-30
**Confidence:** high

## 這份記憶已經搬家

遊戲化的操作事實現在住在**跨 agent 記憶**：

> **[`memory/shared/reference/fleet_gamification_stack.md`](../shared/reference/fleet_gamification_stack.md)**

**為什麼搬**：`memory/SCHEMA.md:37` 定義 Codex 只讀 `memory/shared/**` 與 `memory/codex/**`。
這些事實留在 `memory/claude/` 等於 Codex 讀不到，交接時它會把我們踩過的坑重踩一次
（LSCache 快取 REST、Cloudflare 擋自家域名、`merge ≠ deployed`）。

**不要在這裡再補內容**——兩份會分岔。所有更新寫進 shared 版。

## 相關

- 交接總表（做到哪裡／未驗證／待決策）：`docs/plans/2026-08-30-gamification-handoff.md`
- 逐條技術裁決與 vendor 縫隙：`agents/sanji/CONTEXT.md`
- 營運方案 v1.2：`docs/plans/fleet-gamification-master-plan.md`
- 站台存取：`memory/shared/reference/fleet_community_stack.md`
