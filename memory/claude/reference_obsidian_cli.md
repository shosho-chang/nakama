---
name: reference-obsidian-cli
description: Obsidian 官方 first-party CLI 能力清單 + 對 Nakama 的 placement（桌機 vs VPS）— eval、search、create、tasks、tags、unresolved、Headless Sync 等，桌機需 Obsidian app 在跑（Headless Sync 例外）
metadata:
  type: reference
---

# Obsidian CLI（官方 first-party）

**Source**: https://obsidian.md/cli
**Status**: Official Obsidian product, stable（無 beta/experimental 標示）
**Slogan**: "Anything you can do in Obsidian you can do from the command line."
**Platforms**: macOS / Windows / Linux

## 安裝 + 註冊

1. 下載 Obsidian installer
2. Settings → General 開「Command line interface」
3. 註冊指令加到 PATH
4. 重啟 terminal

平台細節：
- **macOS**: symlink `/usr/local/bin/obsidian`（需 sudo）
- **Windows**: `Obsidian.com` redirector
- **Linux**: binary copy to `~/.local/bin/obsidian`

## 硬約束

> **「Note that the Obsidian app must be running.」**

= 桌機（Obsidian 開著）才能跑非 Sync command。**VPS 沒裝 GUI、沒跑 Obsidian app，所以這些 command 在 VPS 上不可用**。Headless Sync 例外（見下）。

---

## 對 Nakama 有用的 command（按使用情境分組）

### A. LLM agent 讀資料（核心，補強 LLM-over-vault）

| Command | 用法 | Nakama 用途 |
|---------|------|-------------|
| `obsidian search query="..." format=json` | 全 vault 搜尋，JSON output | LLM-over-vault 的 pre-filter — 用 keyword 篩出候選檔再丟 LLM，省 token |
| `obsidian read` | 讀當前 active file | 桌機端 agent：使用者「在 X 上想 review」時直接拿到內容 |
| `obsidian eval "app.vault.getFiles().length"` | **eval 任意 JS in Obsidian context** | **killer feature** — 可呼叫 `app.plugins.plugins.dataview.api`、Tasks API、Templater 等所有 plugin API |
| `obsidian tags counts` | tag frequency | content audit、找 under-tagged 主題 |
| `obsidian unresolved` | broken / unresolved 連結 | KB hygiene |
| `obsidian diff file=X from=1 to=3` | 版本比對（需 Obsidian Sync 版本史） | 看「過去 N 天 X 文件改了什麼」 |

### B. LLM agent 寫資料（繞 Issue #231 的可能解）

| Command | 用法 | Nakama 用途 |
|---------|------|-------------|
| `obsidian create name="..." template=...` | 從 template 建檔 | **Tier C 解 #231 衝突的候選機制** — Bridge 不直接 FS 寫 vault，而是 call CLI，由 Obsidian 自己當 writer（=跟使用者同一個 writer，無 concurrency 問題） |
| `obsidian daily:append content="..."` | append 到 daily note | 替代 `shared.obsidian_writer.append_to_file` 的潛在改寫對象 |

### C. Plugin runtime access（透過 `obsidian eval`）

`obsidian eval` 是萬用 hook。實際能呼叫的 plugin API 範例：

- `obsidian eval "app.plugins.plugins.dataview.api.pages('#project').where(p => p.status === 'active')"` — 跑 Dataview query
- `obsidian eval "app.plugins.plugins['obsidian-tasks-plugin'].cache.tasks"` — 拿 Tasks plugin cache
- `obsidian eval "app.plugins.plugins.templater-obsidian.templater.create_new_note_from_template(...)"` — 觸發 Templater
- `obsidian eval "app.workspace.activeLeaf.view.editor.getValue()"` — 拿當前編輯器內容

**等同**：LLM 可以 access 所有目前在 vault 內裝的 plugin 的 runtime API。

### D. Tasks / Daily（直接 entry point）

| Command | 用法 |
|---------|------|
| `obsidian daily` | 開今日 daily |
| `obsidian daily:append content="text"` | append 內容 |
| `obsidian tasks daily` | 列 daily 內的 task |

### E. 開發 / 維運

`obsidian devtools`、`plugin:reload`、`dev:errors`、`dev:screenshot`、`dev:css`、`dev:dom` — 主要給 plugin developer。

### F. Headless Sync（**唯一 VPS 可用的 CLI 能力**）

> "Run Obsidian Sync without a GUI. All the speed, privacy, and end-to-end encryption."
> "Provide agentic tool vault access without full computer access."

**官方明確點名 AI agent use case**。可能替代 Syncthing 當 VPS ↔ desktop 同步機制（需 Obsidian Sync 付費訂閱）。

**但**：修修已經有 Syncthing tri-sync（VPS + Win + Mac）跑得好好的，**沒急著換**。Headless Sync 主要優勢是省一個 Syncthing daemon + e2e 加密原生，但要付 Obsidian Sync 月費。**deferred**。

---

## 對 Nakama 架構的關鍵 implication

### 1. `obsidian eval` 解 Tier B 部分問題

原本說「Tier B Dashboard 要把 dataviewjs queries 用 Python 重寫」— **未必**。透過 `obsidian eval "app.plugins.plugins.dataview.api..."`，LLM agent 可以**直接跑現成 dataviewjs**，把結果 JSON 回 Bridge。

**但**：這只在 Obsidian app 開著的桌機跑得通。VPS Bridge 要嘛 (a) 接受「桌機 agent 跑 query 回 push 到 VPS state.db」的拓撲，要嘛 (b) Python 重寫。Tier B grill 時要拍。

### 2. `obsidian create` 是 Issue #231 的合法繞道

Tier C「Bridge 寫 task / project」直撞 Issue #231（Bridge 禁止寫 vault）。`obsidian create` 走 Obsidian 本身為 writer，**等同使用者按按鈕 = 使用者建檔**，不違反 #231 的「dataviewjs path is the only writer」精神（reviewer 立場可能不同，要 grill）。

### 3. 桌機 ↔ VPS 能力切分變明確

| 能力 | VPS（Bridge live） | 桌機（Obsidian 開著） |
|------|--------------------|----------------------|
| FS-direct read `.md` | ✅ | ✅ |
| LLM-over-vault concat | ✅ | ✅ |
| `obsidian search` JSON | ❌ | ✅ |
| `obsidian eval` plugin API | ❌ | ✅ |
| `obsidian create` from template | ❌ | ✅ |
| Headless Sync | ✅（換掉 Syncthing 才用） | n/a |

**架構含意**：未來 LLM agent 設計可能分「VPS-resident agent」（純 FS + LLM）跟「desktop-resident agent」（含 Obsidian CLI）兩種 dispatch — Bridge 可 enqueue 任務、桌機 agent 線上時 pull 並執行 plugin-required step。**這是 future option，不是 Tier A 範圍**。

### 4. Pushback #1 的修正

我先前說「Obsidian 強大功能 headless 不可用」是**部分錯**：
- ✅ Dataview/Bases/dataviewjs 確實不能 100% headless
- ❌ 但 `obsidian eval` 在桌機 Obsidian 開著時可呼叫所有 plugin API — **等同**有 headless interface
- 修正：「**VPS 上**無法呼叫 plugin runtime；**桌機**透過 `obsidian eval` 完全可以」

---

## 不在 Nakama scope 的 command

- DevTools / plugin:reload / dev:* — plugin development 用，跟 content workflow 無關
- `obsidian diff` — 需 Obsidian Sync 訂閱才有版本史，使用者目前用 Syncthing 沒這層

## 相關 memory

- [[feedback_compute_tier_split]] — 重 ingest 桌機 / 輕 query VPS 分工原則
- [[reference_vps_paths]] — VPS path 不含 Obsidian app
- [[reference_vault_paths_mac]] — vault 路徑
