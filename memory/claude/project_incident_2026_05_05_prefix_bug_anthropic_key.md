---
name: 5/5 incident — prefix 欄位 bug + Anthropic key 失效雙故障
description: 5/5 早上所有 cron + Franky health 全死 — 兩個 bug 疊（state.db prefix 欄位 + Anthropic API key 401）；ALTER hotfix + key rotation 解
type: project
created: 2026-05-05
---

# 5/5 雙故障 incident

## 症狀

修修早上發現 PubMed digest + Franky AI digest 沒跑、Franky 也沒推 Slack 警報。

## Root cause #1 — `prefix` 欄位 migration 順序 bug

`shared/state.py:_init_tables()` 把 `idx_r2_backup_prefix_time` 索引建在 `executescript` 裡（用 `prefix` 欄位），**早於** ALTER TABLE migration loop。VPS 的 `r2_backup_checks` 表是 prefix 欄位加入前就存在的舊表，`CREATE INDEX` 死在 `OperationalError: no such column: prefix`，整個 `_init_tables` 拋錯，**所有** agent 拿不到 db connection。

PR #371 (`254089f fix(state): run ALTER TABLE migrations before CREATE INDEX`) 已在 main 修好，但 VPS 落後 7 commits 沒拉到。

**Hotfix**（不需 deploy）：
```bash
ssh nakama-vps
python3 -c "import sqlite3; c=sqlite3.connect('/home/nakama/data/state.db'); c.execute(\"ALTER TABLE r2_backup_checks ADD COLUMN prefix TEXT NOT NULL DEFAULT ''\"); c.commit()"
```

等同 migration 008，安全冪等。

## Root cause #2 — Anthropic API key 401

修完 #1 後 dry-run Franky news，curate 階段 401。Haiku 也 401，所有模型都 fail。Diff `.env` 跟 5/3 backup → key 完全相同（不是本地改掉），是 Anthropic 端 revoke。修修 console rotate 新 key 解。

## Why Franky 沒推 Slack 警報

Franky 的 anomaly daemon 自己也死於 prefix bug（`cost_spike` check 走 `_get_conn()` 拋錯），但 daemon 上層 try/except 吞掉 → 記成 `heartbeat success` + `anomalies=0`。**監控系統自己被監控對象的根因 bug 殺死，靜默回報「一切正常」。**

修完 prefix 後 Franky health probe 立刻吐出本來就會偵測到的 cron staleness alert（`cron-stale-robin-pubmed-digest` / `cron-stale-franky-news-digest` 等）—— 所以監控設計沒問題，只是被同 bug 連帶癱瘓。

## How to apply

- VPS schema migration sync 落後是真實風險。**main merge 後 VPS 不一定當天 pull**，cron 突然撞 schema bug 會全面靜默失敗
- Anthropic key 突然 401 不一定是本地改掉。先 diff `.env` vs latest .bak 確認是不是本地問題，否則看 console
- 監控系統自身依賴 state.db 的話，state.db 死掉 → 監控也死，**不要假設「heartbeat success = 系統正常」**，要看 anomaly count 是不是長期 0（疑似 false-green）
- 補跑命令：
  ```bash
  ssh nakama-vps "cd /home/nakama && /usr/bin/python3 -m agents.robin --mode pubmed_digest"
  ssh nakama-vps "cd /home/nakama && /usr/bin/python3 -m agents.franky news"
  ```

## 後續

- VPS pull main（含 PR #371 正本修）—— 同日傍晚已做（連同 PR #415 一起）
- TODO（未做）：Franky anomaly daemon 上層 try/except 吞掉 check 失敗的設計需檢討。一個 check 一直 raise 應該觸發 alert，不是被吞掉繼續報「正常」。
