---
name: reference_ui_preview_tailscale
description: 調 Bridge UI 時，修修用手機透過 Tailscale 直接看桌機上的預覽版（100.92.250.116:8765）——迭代不走 PR/CI/deploy，定案才發一次 PR
metadata:
  type: reference
---

**UI 迭代預覽**：手機（`oppo-find-x9-ultra`）與桌機（`pc`）在同一個 Tailscale 網路（帳號 bonvoyage），2026-11-09 設定完成並實測可用。

- 桌機 Tailscale IP：`100.92.250.116`；預覽網址 `http://100.92.250.116:8765/bridge/...`
- 啟動：**必須獨立程式＋指定真資料路徑**（PowerShell，在預覽用 sibling worktree 例 `E:\nakama-ui-preview`）：
  `$env:DB_PATH='E:/nakama/data/state.db'; $env:VAULT_PATH='E:/Shosho LifeOS'; $env:FOCUS_MUSIC_DIR='E:/data/music'; Start-Process C:\Python314\python.exe -ArgumentList '-m','uvicorn','thousand_sunny.app:app','--host','100.92.250.116','--port','8765' -WorkingDirectory E:\nakama-ui-preview -RedirectStandardOutput E:\nakama-ui-preview\logs\preview.out.log -RedirectStandardError E:\nakama-ui-preview\logs\preview.err.log -WindowStyle Hidden`
  **只綁 Tailscale IP**——區網 192.168.x 與 127.0.0.1 都連不到（已驗）。不要綁 0.0.0.0。
  - 不能用 Bash `run_in_background` 跑：2026-11-09 兩次都在回合結束後被直接終止（無 shutdown log）。
  - 不能漏 `DB_PATH` / `VAULT_PATH`：worktree 沒有 `.env`（複製 .env 被 deny），會退回 config.yaml 的 `/home/...` → 讀到 `E:\home` 影子舊資料（見 [[reference_nakama_local_db_path]]）。log 不再出現 `env var ... not set` 才算對。
  - 預覽版讀寫的是**真** vault 與 state.db，跟 8000 本機版一樣；修修 2026-11-09 同意。
  - 預覽網址是 `http://` 加 Tailscale IP，瀏覽器不算安全連線（`isSecureContext=false`）→ 本地資料夾 API（`showDirectoryPicker`）被停用，番茄鐘的「♪ 音樂資料夾」按鈕在預覽版**一定不會出現**。這不是 UI bug；正式站 https 與 127.0.0.1 照常顯示。預覽版靠 `FOCUS_MUSIC_DIR` 走伺服器播放（按 ▶ 就有配樂）。修修 2026-11-10 以為改版把它弄丟了。
- Windows 把 Tailscale 網卡歸「私人網路」；防火牆放行要修修本人按（只勾私人）。
- 8000 是正式本機版，別佔（見 [[reference_bridge_dev_server_port]]）。

**Why**：2026-11-01 手機 nav 改版，一個小 UI 改動要等 CI（那次 lint-and-test 跑 71 分鐘）＋手動 VPS deploy（分類器擋 Claude 跑）才看得到。修修問怎麼讓 UI 變動不用每次跑 CI。

**How to apply**：調 UI 時流程是「改 → 修修手機重新整理看預覽版 → 定案才 PR → CI → merge → deploy_vps.sh」。修修人不在桌機旁也能看。Tailscale 沒開時退回 Playwright 手機尺寸截圖 + SendUserFile。
