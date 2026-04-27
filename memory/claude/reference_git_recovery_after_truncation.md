---
name: Git multi-file truncation recovery (Windows）
description: 多個 .git/* + working tree 檔在同一秒被截斷成 whitespace/zeros 時的 recovery 步驟；2026-04-27 21:59 撞到一次
type: reference
originSessionId: a16bf522-add0-466b-abcf-6cce2af9d857
---
**症狀**：`git` 任意命令吐 `fatal: not a git repository`、後續又 `error: bad signature 0x00000000 / fatal: index file corrupt`。多個檔案 mtime 完全相同（同一秒）但內容變成全空白或全零。

**2026-04-27 21:59 撞到的具體狀況**：
- `.git/HEAD` 21 bytes 全空白（應為 `ref: refs/heads/<branch>\n`）
- `.git/FETCH_HEAD` 0 bytes
- `.git/index` 89 KB 但 signature 變 `0x00000000`（內部結構壞）
- `memory/claude/MEMORY.md` 35 KB 全空白
- `docs/research/2026-04-27-codebase-architecture-audit.md` 1.7 KB 全空白
- `memory/claude/project_codebase_architecture_audit.md` 1.7 KB 全空白

疑似來源：Windows AV scan / OneDrive sync / unclean shutdown 在 fsync 之間中斷 — 多檔同步 truncate 是 OS-level 事件。**沒丟資料，只是 in-place 寫入 mid-flight 被打斷**。

**Recovery 步驟（順序重要）**：

1. **不要 panic destructive op**（`git reset --hard` / `rm .git/...` 會丟資料）。先看 `.git/logs/HEAD` 拿到正確的 branch ref：
   ```
   tail -10 .git/logs/HEAD
   ```
   最後一行 `<old> <new> ... checkout: moving from X to Y` 或 `commit: ...` 告訴你當下在哪個 branch、HEAD 該指哪個 commit。

2. **重寫 `.git/HEAD`** — 用 `printf` 確保 LF 結尾（git 對 line ending 敏感，**不要用 echo/Write tool 在 Windows 寫 CRLF**）：
   ```
   printf 'ref: refs/heads/<branch>\n' > .git/HEAD
   ```
   驗證：`wc -c .git/HEAD` 應 = `len("ref: refs/heads/<branch>") + 1`。

3. **檢查 branch ref 還在**：`cat .git/refs/heads/<branch>` 應為 40-char SHA。如果 ref 也壞了，從 `.git/logs/HEAD` 拿最後 commit SHA 寫回 ref：`echo <sha> > .git/refs/heads/<branch>`。

4. **如 `git status` 仍 `fatal: index file corrupt`**：
   ```
   mv .git/index .git/index.corrupt-<timestamp>   # 留 forensics
   git reset                                       # 從 HEAD 重建 index, working tree 不動
   ```
   `git reset` 無 args 是 `--mixed HEAD` — reset index, **不碰 working tree**。

5. **Working tree 被截的 tracked 檔**：`git status` 會顯示為 `modified`（因為 working tree 是 garbage、HEAD 是 good）。直接 `git restore`：
   ```
   git restore -- <file1> <file2> ...
   ```
   注意排除合法 in-flight 修改 —— 先看 `git diff <file>` 確認真的是空白污染、不是真實工作。

6. **不需要修 `FETCH_HEAD`** — 那是上次 fetch 的 cache，下次 `git fetch` 會自動覆寫。

**驗證**：`git status` 不再 fatal、`git log -1` 顯示正確的 HEAD commit、`git restore` 完的檔案內容跟最新 commit 一致。

**事後**：保留 `index.corrupt-<timestamp>` 一段時間當 forensics（萬一發現 working tree 還有別處被截）。確認沒事再清。

**根因防範（未測）**：
- Windows: 把 repo 加進 Defender exclusion list
- OneDrive: 確認 repo 不在 OneDrive folder（或排除 .git/）
- VSCode/git GUI 並用：避免兩個 process 同時 hold .git/index lock
