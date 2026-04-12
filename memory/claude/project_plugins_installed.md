---
name: project_plugins_installed
description: Claude Code 已安裝的 4 個官方 plugin 及其用途
type: project
originSessionId: ecac2e9b-d409-4922-b30f-4270e46d6df0
---
## 已安裝 Claude Code Plugins（2026-04-12）

來自 `claude-plugins-official` marketplace，scope: user：

| Plugin | 用途 |
|---|---|
| `skill-creator` | Skill 全生命週期開發：Create / Eval / Improve / Benchmark 四模式 |
| `claude-md-management` | CLAUDE.md 品質審計 + 對話學習自動收集 |
| `pyright-lsp` | Python 型別檢查 LSP |
| `code-review` | 多 agent 平行 PR review（CLAUDE.md compliance、bug、git history） |
| `playwright` | 瀏覽器自動化 MCP — 網頁操作、截圖、表單填寫、E2E 測試 |
| `firecrawl` | 網頁爬蟲 MCP — scrape/crawl/search/map，JS 渲染 + 反偵測（免費額度） |

**Why:** 為 Nakama agent 開發建立正規化工具鏈，skill-creator 是核心（每個 agent 都需要 skills）。
**How to apply:** 開發新 agent skill 時用 `/skill-creator`；PR 前用 `/code-review`；定期用 `/revise-claude-md` 更新 CLAUDE.md。

## 評估後決定不裝的 Plugins

| Plugin | 原因 |
|---|---|
| `pr-review-toolkit` | 6 個 agent 有 3 個與現有 code-review/simplify 重疊 |
| `superpowers` | 行為規範 prompt 集合，我們已有六 Phase 流程替代 |
| `code-simplifier` | 與內建 `/simplify` skill 完全重疊 |
| `security-guidance` | 偵測 pattern 偏基礎，不覆蓋我們的實際風險面（Path Traversal、認證） |
| ~~`firecrawl`~~ | ~~付費服務~~ → 有免費額度，已安裝（2026-04-12） |
