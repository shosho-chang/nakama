---
name: Ralph Loop plugin 真實樣貌（claude.com/plugins/ralph-loop）
description: Ralph Loop 是「single-prompt 反覆 iter 直到 DONE」runner，不是 issue queue runner；2026-04-29 修修評估後決定先不裝，手動派 worktree agent 並行更快
type: reference
created: 2026-04-29
confidence: high
originSessionId: 60cca1e4-d0f1-42ac-a88c-43bda11c12ce
---
修修在 2026-04-29 SEO 中控台 session 中問「下一個 feature 要不要裝 Ralph Loop」。WebFetch `https://claude.com/plugins/ralph-loop` 確認：

| | 修修預期 | Ralph Loop 實際 |
|---|---|---|
| 觸發 | issue queue 自動撈 `ready-for-agent` | 手動 `/ralph-loop "<prompt>" --max-iterations N --completion-promise "DONE"` |
| 跑什麼 | 把 issue 一個一個解掉 | **同一個 prompt 反覆 re-feed 直到 Claude 輸出 "DONE" 或達 iter 上限** |
| 在哪跑 | 想像中外面有 GitHub Action / cron | 在當前 Claude Code session 內，stop hook 攔截 session exit 自動再進來 |
| 輸出 | 多 PR | 對單一任務迭代收斂 |

**結論**：它不是「issue queue runner」，是「single-prompt iteration runner」。

**對 nakama 的意義**：
- 可拿來解單個 issue（prompt 寫「實作 issue #X 直到 acceptance 全綠就 say DONE」），但**不能批次**
- 並行多 issue 走手動 worktree agent dispatch（修修現在的做法）會比 Ralph Loop 快
- 如果想要真正「issue queue 自動清」runner，需要自己蓋（GitHub Action cron + claude-code agent invocation + dependency graph + cost gate + conflict 處理），是一個獨立 PRD 等級的 feature

**2026-04-29 拍板**：先不裝 Ralph Loop，繼續手動派 worktree agent。session 內 7 PR 兩天內全清完，pattern 證實夠快。

**ready-for-agent label 的真實角色**：是 `github-triage` skill 的 state machine 標籤（"Fully specified, ready for AFK agent"），意圖是「外面有 AFK runner 來撈」— 但 nakama repo 沒實作那個 runner。光標 label 不會自動有人解，要手動派。
