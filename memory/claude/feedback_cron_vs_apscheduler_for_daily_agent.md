---
name: Daily agent 工作用 Linux cron，不用 APScheduler
description: Nakama 既定 pattern 是 cron + `python -m agents.<x> <sub>`（Franky / Robin / backup 都這樣）；APScheduler 設計 doc 是錯的選擇
type: feedback
originSessionId: 08f9ecf1-0d35-4311-a34c-34bca66b0731
---
Zoro brainstorm P2 設計 doc §8 Q5 凍結 APScheduler，但實作時發現 VPS 上**既有 4 條 cron 線**（Franky 3 slice + Nakama backup），沒人裝 APScheduler。改走 cron 是正確決定（PR #106），設計 doc 同步更新（`docs/decisions/step-5-zoro-brainstorm-p2.md:5`）。

**Why**：
- Nakama 的 repo convention 是 cron — 新 agent 沿用省依賴 + ops 一致
- 無新 Python dep（APScheduler 要裝）
- 獨立 log 檔 `/var/log/nakama/<agent>.log` 比 `journalctl -u <service>` 易讀
- `systemctl restart thousand-sunny`（web）或 `nakama-gateway`（Slack）不影響 cron 排程 — 穩定性優勢
- Linux cron 的 TZ 本來就是 VPS TZ（Asia/Taipei），不用 APScheduler 自己 timezone 處理

**How to apply**：新 agent 要排程日跑 / 時跑時：
1. 直接在 `cron.conf` 加一行 `<schedule>  cd /home/nakama && /usr/bin/python3 -m agents.<agent> <subcommand> >> /var/log/nakama/<agent>-<sub>.log 2>&1`
2. Agent `__main__.py` 走 argparse subcommand（鏡 `agents/franky/__main__.py`）
3. 部署時 `crontab -e` 手動 apply（VPS 有 crontab，不是 /etc/cron.d 自動同步）
4. 第一次部署 `touch /var/log/nakama/<agent>-<sub>.log` 確保存在
5. **絕對不要**為了「in-process」這個模糊好處去裝 APScheduler，除非該 task 需要 sub-minute 觸發或狀態在 process 內共享

## 反例（什麼情況 APScheduler 才有意義）

- Thousand Sunny web 內需要「每 N 秒」check 一個 in-memory state（但這類需求目前 Nakama 沒有）
- 真正需要在 FastAPI process 內共享連線 pool / cache 的背景任務
- 以上情況依然 90% 可以用 `asyncio.create_task + asyncio.sleep` 不裝 APScheduler
