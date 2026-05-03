---
name: Shosho LifeOS vault 路徑（Mac + Windows）
description: Mac + Windows 桌機 Obsidian vault 路徑；Windows 已 2026-04-30 從 F: 搬到 E:，F: 還有 Syncthing 殘留要避開
type: reference
originSessionId: 64ccfe1b-b7a7-4f86-8964-5a458e6eba6f
---
## Mac

`/Users/shosho/Documents/Shosho LifeOS/`

## Windows（桌機）

**Active vault**: `E:\Shosho LifeOS\`（2026-04-30 從 F: 遷移到 E: 之後的 canonical 位置）

**已停用 stale path**: `F:\Shosho LifeOS\` — Syncthing 殘留還在但**不是 active vault**。Obsidian 不打開它。

**Why E: not F:**: F: 槽 SSD 出現過 2-3 次 git 檔案損毀（最後一次 2026-04-29），跟 `project_disk_layout_e_primary.md` 紀錄的 repo 搬遷同源。Repo 跟 vault 同步搬到 E: 企業級 SSD。

## 怎麼確認 Windows active vault（避免再寫錯地方）

讀 `C:\Users\Shosho\AppData\Roaming\obsidian\obsidian.json` —— `"open":true` 的那條 vault 路徑就是 active。

當前狀態（2026-05-03 確認）：

```json
{
  "vaults": {
    "eaf57b5db3854b28": {"path": "F:\\Shosho LifeOS"},        // stale
    "748b6ab0fce79bbe": {"path": "F:\\nakama"},                // stale (repo 也搬走了)
    "9e80bc5ea44b2875": {"path": "E:\\Shosho LifeOS", "open": true}  // ← active
  }
}
```

## How to apply

- 寫 vault 檔案前永遠用 `E:\Shosho LifeOS\`，**不要寫到 `F:\Shosho LifeOS\`** — F: 上的修改不會被 Obsidian 看到、也不會被 Syncthing 推送（因為 F: 那個 vault Obsidian 沒打開、Syncthing 配置可能不一致）
- 如果 memory / docs / config 出現 `F:\Shosho LifeOS\` 路徑：判斷是「歷史 snapshot」（保留）或「active 路徑」（要改 E:）
- 跨平台 share 的 agent 用 `shared/config.py:get_vault_path()` 解 `LIFEOS_VAULT_PATH` env，不要 hardcode 路徑
- 真不確定？跑 `cat /c/Users/Shosho/AppData/Roaming/obsidian/obsidian.json | jq` 找 `"open":true`

## 主要目錄結構（兩平台同）

- `KB/Wiki/Sources/` — Source pages（papers, book chapters, podcasts）
- `KB/Wiki/Concepts/` — Concept pages（cross-source 主題）
- `KB/Wiki/Entities/` — Entity pages（books, people, organisations）
- `KB/Raw/` — 原文（PDFs / EPUBs / etc.）
- `Attachments/Books/{book_id}/ch{n}/` — 教科書 figure binaries
- `Projects/` — Project files
- `TaskNotes/Tasks/` — Task files
- `Templates/` — Templater templates

## 備註

此路徑用於 session 要直接讀 / 寫 vault 檔案時（e.g., textbook ingest, template sync, case study archive）。不是 Nakama repo，repo 在 `/Users/shosho/Documents/nakama/`（Mac）或 `E:\nakama\`（Windows，per `project_disk_layout_e_primary.md`）。

## 教訓事件（2026-05-03 textbook ingest）

桌機 session 寫 ch1.md / ch4.md / Book Entity / 重命名 ch9→ch4 attachments **全寫到 stale F: vault**，使用者在 E: 上看不到任何更新。事後 `cp F:/.../*.md E:/.../` + 重做 E: 上的 attachments rename 才補救。memory 原本只說 repo 搬到 E:，沒說 vault 也搬了 → 此檔修正。
