---
name: project_plugins_installed
description: Claude Code 已安裝的 4 個官方 plugin 及其用途
type: project
---

## 已安裝 Claude Code Plugins（2026-04-12）

來自 `claude-plugins-official` marketplace，scope: user：

| Plugin | 用途 |
|---|---|
| `skill-creator` | Skill 全生命週期開發：Create / Eval / Improve / Benchmark 四模式 |
| `claude-md-management` | CLAUDE.md 品質審計 + 對話學習自動收集 |
| `pyright-lsp` | Python 型別檢查 LSP |
| `code-review` | 多 agent 平行 PR review（CLAUDE.md compliance、bug、git history） |

**Why:** 為 Nakama agent 開發建立正規化工具鏈，skill-creator 是核心（每個 agent 都需要 skills）。
**How to apply:** 開發新 agent skill 時用 `/skill-creator`；PR 前用 `/code-review`；定期用 `/revise-claude-md` 更新 CLAUDE.md。
