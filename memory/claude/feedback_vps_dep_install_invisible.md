---
name: VPS deps drift relative to requirements.txt — only visible on first cron exec
description: VPS 用 system python 不是 venv，requirements.txt 加 dep 不自動同步；deps 也可能靜默消失，crash loop 後才被發現
type: feedback
created: 2026-04-26
updated: 2026-05-18
originSessionId: b1956877-1a21-491f-8d1a-62ca555475d0
---
VPS 部署 Franky news Slice B 時 ssh 跑 `python3 -m agents.franky news --dry-run` 直接 `ModuleNotFoundError: No module named 'bs4'`。bs4 自 PR #159（EPUB textbook ingest）就在 requirements.txt，但桌機 ingest path 才用 — VPS 從沒裝過。Slice B 的 anthropic_html.py 是 VPS 第一個需要 bs4 的 path，才暴露。

**Why**：VPS 用 system /usr/bin/python3.12（不是 venv），sync deps 是手動 `pip3 install <pkg>`。沒有 deploy hook 會跑 `pip install -r requirements.txt`，所以 requirements.txt 純粹是給桌機/CI 看的契約，VPS 上是 lazy install。Compute tier split（cf feedback_compute_tier_split.md）把重 ingest 留桌機，所以一堆 dep 永遠不會在 VPS 上需要。

**第二種失敗模式（2026-05-18 新增）**：deps 可以**靜默消失**。Thousand Sunny 從早上 10:20 開始 crash loop，`ModuleNotFoundError: No module named 'langdetect'` — 但 langdetect 在 requirements.txt 從 monolingual-zh pilot 起就有，之前都活著。沒人手動 uninstall。最可能：某次 `pip install <other-pkg>` 解相依時把 langdetect 連帶移除（pip 沒鎖、Debian system python 沒 venv 隔離）。修法簡單：`pip install -r requirements.txt` 補齊（會順便補其他默默不見的）。重點：**deploy 後沒檢查 systemctl is-active = 你不知道服務死了**，這次是 4 小時後 user 問起 nakama.shosho.tw/progress 才發現。

**How to apply**:
- VPS 部署新 agent path 前先 grep `^import|^from` 該 path 的入口 module，對照 VPS `pip3 list` 看少哪個
- 或部署完跑 `--dry-run` 立刻會暴露 missing dep（這次走的路）
- 如果新 dep 是給 VPS 用的，PR 要加 `agents/<name>/README.md` 或 `docs/runbooks/<name>.md` 註明「VPS 部署需 `pip3 install <pkg>`」
- 不必為了補齊弄 venv；VPS 是 single-tenant 自用機，system python 直裝可接受
- **任何 VPS deploy 後 mandatory smoke**：`systemctl is-active thousand-sunny cloudflared && curl -sf http://127.0.0.1:8000/healthz`。沒看到 active + 200 就**還沒 ship**（DoD = PR merged 的延伸 = service active）
- Deploy 撞 crash loop 第一招：`pip install -r requirements.txt` 補齊靜默消失的 deps，再 restart。比 git bisect 快多了
