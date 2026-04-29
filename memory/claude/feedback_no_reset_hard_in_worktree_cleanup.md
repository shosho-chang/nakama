---
name: 清 worktree leak 不可用 git reset --hard — 會吃掉無關 working-tree 改動
description: 2026-04-29 SEO Slice 1 agent 用 `git reset --hard HEAD` 清自己 leak 進 main tree 的檔案，順便吃掉 ch2 ingest cache reindex 的 working-tree 改動，reflog 救不回
type: feedback
created: 2026-04-29
confidence: high
originSessionId: 94a66141-8b16-4e9a-9762-b9d9c43f1695
---
`git reset --hard` 會 wipe 整個 working tree（不只 reset 你想撤銷的特定檔案）— 所以**任何時候要清「特定檔案」絕對不用 reset --hard**。`git reset --hard` 不留 dangling blob 給 working tree，reflog 也只記 HEAD 移動，不記 working-tree 內容。一旦執行就找不回。

**Why:** 2026-04-29 派 SEO Slice 1 worktree agent，agent 用絕對路徑 leak 13 個檔案進 main tree（[feedback_worktree_absolute_path_leak.md](feedback_worktree_absolute_path_leak.md)），事後用 `git reset --hard HEAD` 清。當下 main tree 還有 `docs/research/2026-04-27-ch2-ingest-cache.json` 一份 figure key reindex（fig-7-N → fig-2-N，ch2 ingest PR D 工作中產物）— 那份 reset 一併吃掉，reflog 確認 `HEAD@{xxx}: reset: moving to HEAD` 是兇手，working tree 內容無法救回。

**How to apply:**

1. 清 specific file 用 `git checkout -- path/to/file` 或 `git restore path/to/file` — 只 affect 那個 file，其他 working-tree 改動不動
2. 清 untracked file 用 PowerShell 回收桶（per CLAUDE.md），不用 `git clean -f`（同樣會吃所有 untracked）
3. 清 leak 前**必先 `git status --short`** 列出 working tree 全部改動，確認你要清的檔案不和其他無關工作交錯
4. 如果 main tree 有未 commit 工作 + 又要清 leak，**先 `git stash -u -- specific-paths`** 把要保留的 stash 起來，再清
5. 不確定就**問使用者**或 commit 一個 WIP commit 再動，commit 留 reflog 可救
6. agent self-report 中如果看到 `git reset --hard` 字樣，立即比對 main tree 跟 session 開始狀態的差異，確認沒順手吃到無關工作

**反例**：sub-agent 的 self-report 寫「`git checkout --` 把 main tree 的我那部分還原」聽起來合理，但實際操作可能 `reset --hard` — 要從 `git reflog` 真實確認，不靠 agent 措辭判斷。

**相關記憶**：
- [feedback_worktree_absolute_path_leak.md](feedback_worktree_absolute_path_leak.md) — 上游問題，清 leak 的根因
- [feedback_git_staging_cross_contamination.md](feedback_git_staging_cross_contamination.md) — 多視窗 git status 交叉污染
