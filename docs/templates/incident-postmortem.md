---
id: YYYY-MM-DD-slug                  # 例：2026-04-26-r2-mirror-fail
title: 一句話描述                       # 例：R2 mirror cron 連續 3 次 fail
severity: SEV-3                       # SEV-1 / SEV-2 / SEV-3 / SEV-4
status: detected                      # detected / mitigated / resolved / postmortem-pending / closed
detected_at: 2026-04-26T10:30:00+08:00
mitigated_at:                         # 留空待填
resolved_at:                          # 留空待填
postmortem_due: 2026-05-03            # detected_at + 7 天
trigger: backup-mirror-fail           # shared.alerts dedupe_key / manual / franky-probe-{name} / bridge-health-{job}
owner: 修修
tags:
  - incident
  - nakama
  - backup                            # category：backup / publish / agent / infra / secret
---

# {{ title }}

> 用法：複製本檔到 `vault/Incidents/YYYY/MM/incident-{id}.md`，把 `{{ ... }}` 與 `<!-- TODO -->` 區塊填掉。
> 流程定義見 [`docs/runbooks/postmortem-process.md`](../runbooks/postmortem-process.md)。

## Summary

<!-- TODO 一段話：發生什麼、誰受影響、影響多久、結果。
範例：2026-04-26 10:30 起 R2 mirror cron 連續 3 次 fail，原因是 B2 endpoint URL 漏 https:// 前綴。
無資料遺失（primary R2 backup 仍正常），mirror 落後 24h。11:30 sed 修 .env 後 smoke 通過。 -->

## Timeline

| 時間（Asia/Taipei）| 事件 |
|---|---|
| HH:mm | 第一個 alert fire（`alert_state.last_fired_at`） |
| HH:mm | 修修 ack（Slack reaction / DM 標記） |
| HH:mm | 起 mitigation 動作 X |
| HH:mm | 動作 X 完成 / 驗證 |
| HH:mm | resolved（`/bridge/health` 變綠 / smoke 通過） |

時間從以下抓，不憑印象：

```sql
-- 從 alert_state 抓 alert 真實時間
SELECT last_fired_at, fire_count, last_message
FROM alert_state
WHERE dedup_key = '{{ trigger }}'
ORDER BY last_fired_at DESC;
```

```bash
# 從 journalctl 抓 service 行為
ssh nakama-vps 'journalctl -u thousand-sunny --since "2026-04-26 10:00" --until "2026-04-26 12:00" | grep -i error'
```

## Detection

- **怎麼發現**：<!-- TODO Slack alert / Bridge UI / 修修 hit -->
- **發生 → 偵測延遲**：<!-- TODO 例：silent 至少 24h（因為 cron 04:30 才跑） -->
- **偵測管道是否充分**：
  - alert threshold 是否該調 → <!-- TODO -->
  - 是否該加新 probe / heartbeat → <!-- TODO -->
  - 是否該縮 dedupe window → <!-- TODO -->

## Mitigation

| # | 動作 | 結果 | Commit / SQL / Config |
|---|------|------|------|
| 1 | <!-- TODO --> | <!-- TODO --> | <!-- TODO link / sha / file --> |
| 2 | | | |

**Mitigation 完成定義**：服務恢復 + 修修能正常用。不是 root cause 找到。

## Root cause

5-why（**至少三層**；SEV-3 lightweight 可只一層）：

1. 為什麼 X 壞？— <!-- TODO -->
2. 為什麼 X 會壞？— <!-- TODO -->
3. 為什麼這個 trigger 會發生？— <!-- TODO -->
4. 為什麼這個 trigger 沒被防住？— <!-- TODO -->
5. 為什麼系統沒設計成防止？— <!-- TODO -->

**最終 root cause（一句話）**：<!-- TODO -->

> Blameless：寫「pipeline 缺 X 檢查」「runbook 漏 Y 步驟」「test fixture 沒 cover Z」，**不寫**「修修忘記」「Claude 漏掉」。

## Action items

| # | Action | Owner | Due | Status | Output |
|---|--------|-------|------|--------|--------|
| 1 | <!-- TODO 例：runbook 加 https:// 前綴 check --> | 修修 | YYYY-MM-DD | pending | [`docs/runbooks/...`](...) |
| 2 | <!-- TODO 例：scripts/backup_*.py startup smoke 加 endpoint URL parse --> | Claude (next session) | YYYY-MM-DD | pending | GH issue #XXX |
| 3 | <!-- TODO 例：feedback memory 寫「endpoint URL 必含 scheme」--> | Claude | 隨手 | pending | `memory/claude/feedback_*.md` |

**SMART check**：每個 action 應 Specific（明確改什麼）/ Measurable（怎麼算 done）/ Assignable（owner 不是「someone」）/ Realistic / Time-bound（具體日期，不是「ASAP」）。

## Lessons learned

- **系統設計層面**：<!-- TODO 什麼 invariant 該加 / 什麼測試該補 / 什麼 design 假設錯了 -->
- **流程層面**：<!-- TODO 什麼 detection 該強化 / 什麼 runbook 該更新 / deploy 流程哪步該補 -->
- **連結**：
  - 對應 `memory/claude/feedback_*.md`：<!-- TODO -->
  - 對應 ADR / runbook 改動：<!-- TODO -->
  - 對應 Phase（quality-bar-uplift plan）：<!-- TODO -->
