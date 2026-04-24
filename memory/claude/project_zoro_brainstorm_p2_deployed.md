---
name: Zoro Brainstorm P2 上線
description: Zoro 自動推題 pipeline 全 slice 合併部署：scout cron 05:00 台北每日 post #brainstorm，Slice D backlog
type: project
originSessionId: 08f9ecf1-0d35-4311-a34c-34bca66b0731
---
Zoro brainstorm P2（step-5 docs/decisions/step-5-zoro-brainstorm-p2.md）全閉環，2026-04-24 上線。

## 全 Slice 進度

| Slice | PR | 內容 |
|---|---|---|
| A | #102 | ZoroHandler Slack registry + persona + fix #103 不自報名字 + runbook troubleshooting #104 |
| B | #105 | 8-step scout pipeline + 4 gates + `shared/pushed_topics` + `prompts/zoro/scout.md` relevance judge |
| C | #106 | Reddit hot wire + cron 05:00 + `scout` subcommand + review follow-ups |
| C1 | #107 | 切 Trends 為 primary source（Reddit 封 VPS IP） |
| C2 | #108 | LLM compose Slack 訊息（`prompts/zoro/compose_message.md`） |

## 部署狀態

- VPS crontab：`0 5 * * * /usr/bin/python3 -m agents.zoro scout >> /var/log/nakama/zoro-scout.log`
- `.env`：`ZORO_SLACK_BOT_TOKEN` / `ZORO_SLACK_APP_TOKEN` / `ZORO_BRAINSTORM_CHANNEL_ID=C0AV0FZ9J9G`
- #brainstorm Slack channel 已建，Zoro + Sanji 為 member
- 第一則真訊息 2026-04-24 14:32（"running point" 假陽性，留作 tune 樣本 + 已 locked 在 `pushed_topics` 防重推）

## 設計 vs 實作的三個偏離（已入 memory + PR doc sync）

1. **Scheduler**：design doc 定 APScheduler，實作改 Linux cron（見 `feedback_cron_vs_apscheduler_for_daily_agent`）
2. **Primary source**：design 假設 Reddit 可匿名，實際 VPS IP 被 Reddit 封 → 切 Trends（見 `feedback_reddit_vps_ip_block`）
3. **訊息格式**：原 Python template 拼接 + `*bold*` 不 render → LLM compose with persona（見 `feedback_llm_compose_agent_messages`）

## Slice D backlog（未開工）

| 項目 | 優先級 |
|---|---|
| Keyword seed tuning — "running"、"nap"、"fit" 太泛會撿影集名/人名，實測 3-7 天後微調 | 高 — 假陽性率收斂前不升 Phase 3 |
| 真 `<@U...>` mention ID（現在純字串 `@Sanji @Robin` 不 notify） | 中 — 需要 `SANJI_SLACK_USER_ID` 等 env |
| Reddit OAuth（client_id/secret 修修建 Reddit app） | 中 — 補回 Reddit 訊號密度 |
| YouTube trending discovery | 低 — Trends 目前夠用 |
| Nami 晨報整合 #brainstorm 24h 摘要（P3） | 低 — Phase 3 範圍 |
| `gather_signals` multi-source 合併（signal 重疊去重） | 低 — 只有一源時不需要 |

## DoD 追蹤（design doc §7，觀察 2 週）

- [ ] 每天平均 0.8–1.5 個 brainstorm 推出
- [ ] 70% 修修覺得「值得推」（觀察中，running point 是第一個假陽性）
- [ ] 單 brainstorm 花費 < $0.30（目前 compose +$0.002，其他成本依參與者反應決定）
- [ ] 每週至少 1 個結論落地（KB / Brook / task）

## 下個 session 如何延續

查 `SELECT topic, pushed_at FROM pushed_topics WHERE agent='zoro' ORDER BY pushed_at DESC` 看累積幾天真訊息，挑最弱的 seed 詞拿掉（或 relevance threshold 0.7→0.8）。`/var/log/nakama/zoro-scout.log` 有每次 tick 的 trends gather 計數。
