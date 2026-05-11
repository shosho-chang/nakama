---
name: AFK / 並行 dispatch default = Sandcastle
description: 長 AFK / 多並行 sub-agent dispatch 必走 Sandcastle，本機 Agent tool isolation:worktree 不真釘 cwd 會污染主 repo
type: feedback
---

**Default = Sandcastle**，不是「sandcastle 或 local agent worktree 二選一」。

**Why**：
本機 Agent tool 的 `isolation: worktree` 只「名義隔離」— sub-agent 內 Bash shell cwd 不被工具釘住。Sub-agent 跑 `cd <worktree>` 一次，下一個 Bash call cwd 可能已回到主 repo path（parent shell 起始 cwd）。寫入動作落到主 repo working tree，commit 飄到別 branch、checkout 切走別視窗 working state、reset 蓋掉未提交修改。

實際踩到（不是假設）：
- 5/7：S1 (#472) + S5 (#474) sub-agent 同一 quirk — S5 自爆「commit landed on main repo via cd quirk」（cherry-pick 救回但 reset HEAD 留痕）；S1 直接在主 repo working tree 上 `git checkout feat/franky-s1-source-expansion`，切走主 repo 從 `docs/kb-stub-crisis-memory` 到別 branch。修修中段查 `git worktree list` 才發現
- 4/25、5/5、5/6：每次都是「夠忙就會出事」的同 root cause（見 [feedback_dual_window_worktree.md](feedback_dual_window_worktree.md)）

Sandcastle 是 cloud sandbox，不共享檔案系統 — 物理隔離、不可能撞 cd quirk。reflog 上 5/7 上半天 ingest-v3 4 個 PR 全走 sandcastle merge，過程零事故。

**How to apply**：

- **AFK / 多並行任務 / 長跑 batch（>30min）/ 多 sub-agent 連續 dispatch** → **Sandcastle**
  - 觸發樣式：「會 AFK ~Nhr」「同時派 N 個 agent」「分 X 個 stage 連續跑」「整晚跑」「邊吃飯邊跑」
- **本機 Agent tool `isolation:worktree`** 僅在「單一短任務 + 能盯住整段執行」場景使用
  - 派工前心裡問：「這段我會關掉視窗去做別的事嗎？」是 → 走 Sandcastle
- **派工選擇是 hard rule，不是 OR 二選一**：handoff prompt 寫派工建議時，sandcastle 寫前面、local Agent worktree 標明「emergency fallback」不放並列

**Cross-refs**：
- [feedback_dual_window_worktree.md](feedback_dual_window_worktree.md) — 4 次踩到的歷史 + AFK trigger rule
- [CLAUDE.md §AFK / 並行 dispatch 派工規則](../../CLAUDE.md) — 入口規則
