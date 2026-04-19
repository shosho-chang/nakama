---
name: Windows 上 POSIX 絕對路徑會默默 resolve 到當前磁碟根
description: 本機 Windows 測試不炸但 CI Linux 炸 /home/nakama/data，查 config.yaml 硬編碼路徑 + autouse fixture
type: feedback
tags: [testing, ci, windows, debugging]
---
本機 Windows 測試不炸但 CI Linux 炸 → 懷疑「config.yaml 硬編碼 POSIX 絕對路徑」。

**Why:** `config.yaml` 的 `db_path: /home/nakama/data/state.db` 在 Windows 上被 `Path(...)` 解成當前磁碟根（例：`F:\home\nakama\data\state.db`），測試會默默寫到本機 vault 附近 — 完全沒察覺。CI Linux runner 的 `/home/nakama` 是 root-owned 不存在，直接 `PermissionError` / `FileNotFoundError`。

**How to apply:**
- 看到「本機過、CI 炸」且 error 涉及 `/home/...` 或類似絕對路徑 → 先 grep 硬編碼
- 對全專案測試都會觸發的 state.db / vault path 類資源，`conftest.py` 裡開 `autouse=True` 的 fixture 把 `get_db_path` / `get_vault_path` monkeypatch 到 tmp_path — 不要當 opt-in
- 2026-04-19 conftest.isolated_db 改 autouse（PR commit 2a9eac7），修好 7 個 gateway_handler 測試
