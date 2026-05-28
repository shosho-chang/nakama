---
name: vps_deploy_always_use_script
description: VPS 部署一律走 scripts/deploy_vps.sh，禁止再建議手動 git pull + systemctl restart
metadata:
  type: feedback
---

VPS 上 deploy nakama 時，**永遠**指引修修跑 `./scripts/deploy_vps.sh`（必要時先 `--dry-run`），不要再叫他手動 `git pull` + `sudo systemctl restart <service>`。

**Why:** 2026-05-28 一連串連環事故：
1. `/bridge/digests` 對外回 404，查出來是 thousand-sunny.service 自 2026-05-21 沒重啟過 — 中間 #690 加了這條 route 但沒重啟，4 天都壞著。
2. 修修手動重啟後，service 立刻 crash loop（restart counter 127 次）：`ModuleNotFoundError: No module named 'bleach'`。bleach 是過去某次加進 `requirements.txt` 的，VPS 從沒跑過 `pip install`，新 code import 時才爆。
3. 等於同一次事件踩到兩個獨立漏洞：「沒重啟對的 service」+「沒裝新 deps」。

**How to apply:**
- 修修問「為什麼 nakama 的 X 壞了 / 怎麼 deploy / 怎麼重啟」→ 第一個答案是 `./scripts/deploy_vps.sh`。
- 修修在 VPS 上手動下 `git pull` 或 `systemctl restart` → 阻止他，叫他用 script。
- Script 行為（PR #755）：preflight（main + clean tree）→ `git pull --ff-only` → **無條件** `pip install -r requirements.txt`（pip 已裝會 skip，~2s） → diff `OLD_SHA..NEW_SHA` 算出 path → service mapping 只重啟需要的 → `systemctl is-active` 驗證 → `http://127.0.0.1:8000/healthz` 確認（**不要**打 `nakama.shosho.tw`，VPS 出口 IP 會被 CF bot challenge 擋 → 拿到 JS challenge HTML 而不是 JSON）。
- Path mapping：`thousand_sunny/` → thousand-sunny；`gateway/` → nakama-gateway；`agents/usopp/` → nakama-usopp；`agents/*` + `shared/` → web + gateway 兩個都；`requirements.txt` → 三個都。

相關：[[feedback_vps_two_services]]（VPS 上三個 service 的職責切分）。
