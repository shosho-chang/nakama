---
name: Disk layout — E: primary, F: retired
description: nakama repo 主要落點 = E:\nakama（企業級 SSD）；F: 因兩三次 git 損毀已退場
type: project
---

repo 目前在 `E:\nakama`（企業級 SSD）。先前在 `F:\nakama`（消費級 SSD），已停用。

**Why:** F: 槽 SSD 出現過 2-3 次 git 檔案損毀（最近一次 2026-04-29），疑似硬體可靠度問題。
2026-04-29 緊急從 F: → C: 暫避，2026-04-30 從 C: → E: 確定落腳，因為 E: 是企業級 SSD。

**How to apply:**
- 講到「nakama repo 路徑」一律用 `E:\nakama`
- 講到「Obsidian vault 路徑」一律用 `E:\Shosho LifeOS\` — F: 上的 vault 是 Syncthing 殘留，**Obsidian 沒打開**（active 確認方法見 `reference_vault_paths_mac.md`）。2026-05-03 textbook ingest 因為寫到 F: vault 整輪白做要重新復原 — 教訓
- 看到 memory / docs / .bat / settings 出現 `F:\nakama` 或 `F:\Shosho LifeOS\`：判斷是「歷史紀錄快照」（保留）還是「active config」（要改成 E:）；不要全域 sed
- `F:\llama.cpp\` 不是 repo，是 llama.cpp 安裝位置，與本紀錄無關
- 如果未來修修又抱怨 git 出現詭異損毀（HEAD/index 截斷類），第一個假設是「磁碟出事了」，不是 git bug — 參考 reference_git_recovery_after_truncation.md
