---
name: project_plugins_installed
description: Claude Code 已安裝的 6 個 plugin + 2 個自建 skill 及其用途
type: project
originSessionId: ecac2e9b-d409-4922-b30f-4270e46d6df0
---
## 已安裝 Claude Code Plugins（2026-04-14 更新）

Plugins（來自 marketplace）：

| Plugin | 來源 | 用途 |
|---|---|---|
| `skill-creator` | `daymade-skills` | Skill 全生命週期開發（fork 版）：9 個結構化檢查點、Prior Art 搜尋、Inline vs Fork 決策、Security Scan |
| `claude-md-management` | `claude-plugins-official` |CLAUDE.md 品質審計 + 對話學習自動收集 |
| `pyright-lsp` | `claude-plugins-official` | Python 型別檢查 LSP |
| `code-review` | `claude-plugins-official` | 多 agent 平行 PR review（CLAUDE.md compliance、bug、git history） |
| `playwright` | `claude-plugins-official` | 瀏覽器自動化 MCP — 網頁操作、截圖、表單填寫、E2E 測試 |
| `firecrawl` | `claude-plugins-official` | 網頁爬蟲 MCP — scrape/crawl/search/map，JS 渲染 + 反偵測（免費額度） |

**Why:** 為 Nakama agent 開發建立正規化工具鏈，skill-creator 是核心（每個 agent 都需要 skills）。
**How to apply:** 開發新 agent skill 時用 `/skill-creator`；PR 前用 `/code-review`；定期用 `/revise-claude-md` 更新 CLAUDE.md。

**2026-04-14 變更：** skill-creator 從官方版換成 daymade fork 版。原因：結構化檢查點、Prior Art 搜尋協議、Inline vs Fork 決策指南。Context 成本 33KB→66KB，但開發時才觸發，可接受。

## 已安裝 Skills（~/.claude/skills/）

| Skill | 來源 | 用途 |
|---|---|---|
| `prior-art-research` | 自建（2 輪 eval 迭代，95% pass rate） | 開發前 6 通道搜尋：本地→skills.sh→Marketplaces→MCP→GitHub→PyPI，產出 adopt/extend/build 報告 |
| `find-skills` | vercel-labs/skills（手動安裝） | 搜尋 skills.sh 市場（環境無 Node.js，用 WebFetch 替代 npx） |

## 評估後決定不裝的 Plugins

| Plugin | 原因 |
|---|---|
| `pr-review-toolkit` | 6 個 agent 有 3 個與現有 code-review/simplify 重疊 |
| `superpowers` | 行為規範 prompt 集合，我們已有六 Phase 流程替代 |
| `code-simplifier` | 與內建 `/simplify` skill 完全重疊 |
| `security-guidance` | 偵測 pattern 偏基礎，不覆蓋我們的實際風險面（Path Traversal、認證） |
| ~~`firecrawl`~~ | ~~付費服務~~ → 有免費額度，已安裝（2026-04-12） |
