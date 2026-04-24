---
name: Mac 上 Shosho LifeOS vault 路徑
description: Mac 開發機的 Obsidian vault 路徑（跨平台對照 Windows F:\Shosho LifeOS）
type: reference
originSessionId: 64ccfe1b-b7a7-4f86-8964-5a458e6eba6f
---
## Mac

`/Users/shosho/Documents/Shosho LifeOS/`

主要目錄：
- `Projects/` — Project files（gold standard: `肌酸的妙用.md`）
- `TaskNotes/Tasks/` — Task files（managed by TaskNotes plugin）
- `Templates/` — Templater templates（`tpl-project.md` dispatcher + `tpl-project-body-*.md` partials + `tpl-action.md`）

## Windows（桌機）

`F:\Shosho LifeOS\`（同 vault 跨平台同步，通常走 iCloud / Syncthing）

## 怎麼找

`mdfind -name "LifeOS"` on Mac；Nami / Brook 的 vault writer 用 `shared/config.py:get_vault_path()` 解環境變數 `LIFEOS_VAULT_PATH`，不要 hardcode。

## 備註

此路徑用於 Mac session 要直接讀 / 寫 vault 檔案時（e.g., template sync, case study archive）。不是 Nakama repo，repo 在 `/Users/shosho/Documents/nakama/`。
