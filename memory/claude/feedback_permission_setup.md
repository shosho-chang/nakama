---
name: Claude Code 權限設定
description: acceptEdits 模式 + allow/deny 規則已設定，rm 改用回收桶，核心規則跨平台共用
type: feedback
originSessionId: 2c7f6b5e-13d0-472f-ad8b-1f3485a54a41
---
已在 `.claude/settings.json` 設定 `acceptEdits` 模式（VS Code 中顯示為 "Edit Automatically"）。

**allow：** WebFetch、WebSearch、pytest、pip、agents.*、git 本地操作（status/log/diff/add/commit/stash）、gh CLI
**deny：** rm、rmdir、git push --force、git reset --hard、git checkout --、git clean、git branch -D、Edit(.env)

**Why:** 修修每次都要手動 approve 太多操作，希望安全的自動放行、危險的硬擋。
**How to apply:** 刪除檔案時禁止 rm，改用 PowerShell 回收桶（CLAUDE.md 已記載）。settings.json 跨平台共用（git 追蹤），settings.local.json 各機器獨立（gitignore）。
