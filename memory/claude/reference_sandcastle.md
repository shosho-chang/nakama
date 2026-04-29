---
name: Matt Pocock Sandcastle — TS library for AFK Claude Code in Docker worktrees
description: github.com/mattpocock/sandcastle；4-agent 架構（Planner / N×Implementation / Reviewer / Merger）；2026-04-29 評估後不裝
type: reference
created: 2026-04-29
---

Matt Pocock 的 AFK runner，**TS library** in Docker，跟 mattpocock/skills repo 是 paired 工具但分開 repo。**不是** Anthropic claude-code-plugins 的 ralph-loop plugin（Matt 自己拒絕後者）。

Repo: https://github.com/mattpocock/sandcastle

## 架構（2026-04 update）

- **Planner agent** — 看 issue backlog 決定哪個先做
- **Implementation agents** × N — 每個 sandboxed in Docker on independent git branch / worktree
- **Reviewer agent** — Opus 把關
- **Merger agent** — 整合 + conflict resolve + patch back to target branch

## Matt 自己的關鍵主張

1. **「Anthropic ralph-loop plugin sucks」** — https://www.aihero.dev/why-the-anthropic-ralph-plugin-sucks。理由：plugin 把所有 iter 塞同 session，context 累積腐壞；bash loop 每 iter fresh context 才在「smart zone」
2. **HITL Ralph + AFK Ralph 兩階段** — 難的部分人類一起做，搞清楚才丟 AFK

## 對 nakama 的 fit

| Gap | 現狀 | 需要 |
|---|---|---|
| 語言 | sandcastle = TS/pnpm | 純外部工具用、不寫進 nakama deps OK |
| Dockerfile | nakama 沒 | 要先補 Python env Dockerfile |
| Docker Desktop | Win 上沒裝 | 要裝 Docker Desktop / WSL2 |
| Reviewer cost | 預設 Opus | config 改 Sonnet 或設預算上限 |

## 2026-04-29 拍板：先不裝

理由：
- 4 個 gap 全要補，setup overhead 比實際 AFK 時間大
- 已實證 single worktree sequential AFK 4 slices 2h50m 完成（PR #260）— 對 nakama 規模夠用
- 等到「issue queue 累積 10+ AFK-ready 而我來不及解」才架

詳見 [feedback_phase3_single_worktree_proven.md](feedback_phase3_single_worktree_proven.md)。
