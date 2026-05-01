---
name: Claude Code 權限設定
description: acceptEdits 模式 + allow/deny 規則已設定，rm 改用回收桶，核心規則跨平台共用
type: feedback
originSessionId: 2c7f6b5e-13d0-472f-ad8b-1f3485a54a41
---
已在 `.claude/settings.json` 設定 `acceptEdits` 模式（VS Code 中顯示為 "Edit Automatically"）。

**allow：** WebFetch、WebSearch、pytest、pip、agents.*、git 本地操作（status/log/diff/add/commit/stash）、gh CLI
**deny：** rm、rmdir、sudo、systemctl、git reset --hard、git checkout --、git clean、Edit(.env)、ssh、scp、rsync

**2026-05-01 移出 deny**（GitHub Pro server-side branch protection 已 enforce main 保護後，4 條 push rule + branch -D 變 redundant）：
- `git branch -D` — squash merge 政策下 `-d` 永遠 fail，reflog 90 天保底
- `git push origin main*` / `git push * main` — server `required_pull_request_reviews` 已擋
- `git push --force *` / `git push -f *` — server `allow_force_pushes: false` 已擋 main；feature branch force push 是合法 PR rebase 工作流不該被擋
- 詳見 [feedback_pr_review_merge_flow.md](feedback_pr_review_merge_flow.md) + [feedback_rule_interaction_audit.md](feedback_rule_interaction_audit.md) + [reference_github_plan_branch_protection.md](reference_github_plan_branch_protection.md)

**Why:** 修修每次都要手動 approve 太多操作，希望安全的自動放行、危險的硬擋。
**How to apply:** 刪除檔案時禁止 rm，改用 PowerShell 回收桶（CLAUDE.md 已記載）。settings.json 跨平台共用（git 追蹤），settings.local.json 各機器獨立（gitignore）。

**踩過的坑：**
- deny 永遠蓋過 allow — 要放行子集（例 `ssh nakama-vps *`）必須先從 deny 拿掉寬鬆規則（`ssh *`）再加精確 allow
- Harness 會擋 Claude 自己 Edit `.claude/settings.json` 擴充 allow-list（self-expansion guard）— 必須讓修修手動改
- 新對話才吃得到設定變更，當前對話可能要重啟

**2026-04-19 新增 allow：**
- `Bash(ssh nakama-vps *)`（同時移除 deny `Bash(ssh *)`）
