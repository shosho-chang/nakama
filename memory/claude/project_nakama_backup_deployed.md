---
name: Nakama state.db 每日備份到 R2（PR #88 已上線）
description: daily 04:00 Taipei snapshot → R2 nakama-backup bucket，retention 30 天，獨立 bucket 避免干擾 Franky backup-verify
type: project
tags: [backup, r2, nakama-state, cron, adr-006-spof]
---
## 狀態（2026-04-24）

PR #88 merged as `6898580`，VPS 部署完成。Daily 04:00 Asia/Taipei snapshot 已排程。

## 設計決策

- **獨立 bucket** `nakama-backup`（跟 xCloud 的 `xcloud-backup` 分開）— 否則 Franky `r2_backup_verify` 會看到 Nakama daily fresh 物件，掩蓋 xCloud 備份 stale 的訊號
- **備什麼**：`state.db`（記憶、approval_queue、publish_jobs、api_calls）+ `nakama.db`（目前 0 bytes 但保留 hook）
- **不備**：`google_*.json` token（敏感可再 OAuth）、`scimago_journals.csv`（可再下載）、`vps_baseline.*`（log）
- **手法**：SQLite `.backup` API → gzip → boto3 PUT；retention 只在所有 upload 成功後才跑（失敗日永遠不吃進前一日 snapshot window）
- **Key 路徑**：`state/YYYY/MM/DD/state.db.gz`，**用 `ZoneInfo("Asia/Taipei")`**（code-review 抓到 UTC bug 修完，見 [feedback_date_filename_review_checklist.md](feedback_date_filename_review_checklist.md)）

## VPS 狀態

- cron `0 4 * * * cd /home/nakama && python3 scripts/backup_nakama_state.py >> /var/log/nakama/nakama-backup.log 2>&1`
- `.env` 已設 `NAKAMA_R2_BACKUP_BUCKET=nakama-backup`（共用 `R2_*` 認證，未做 bucket-scoped token）
- 首次 smoke test：`state.db` 319 KiB → gzip 37 KiB (8.55× 壓縮)，R2 驗到 `state/2026/04/23/state.db.gz`

## 相關

- Restore 步驟：[docs/capabilities/nakama-backup.md](../../docs/capabilities/nakama-backup.md)
- Phase 2 升級：litestream 連續 replication（ADR-006 §SPOF）、Franky 加一條規則檢查 `nakama-backup` bucket fresh

## Phase 2 / 之後待辦

- [ ] Franky `r2_backup_verify` 擴展：多 bucket 檢查（xcloud-backup + nakama-backup 分開算 freshness）
- [x] R2 bucket-scoped token 分離（2026-05-03 完成；CF dashboard cleanup 4 把 token 全留 — 攻擊面沒擴大、純命名 hygiene，修修煩了不想砍。詳見下「CF Token list 現況」）
- [ ] litestream 若 RPO 1 天不夠（目前僅 git + R2 snapshot；狀態遺失窗口 < 24h 即可接受）

## CF Token list 現況（2026-05-03 cleanup 收尾）

| Token name | Permission | Issued | 對應 .env | 狀態 |
|---|---|---|---|---|
| xcloud-backup-read | xcloud Read only | 5/3 | `R2_ACCESS_KEY_ID` 二選一 | 留（不知哪把是 dead） |
| Nakama Franky R2 Reader | xcloud Read only | 4/22 | 同上（另一把） | 留 |
| nakama-backup-write | nakama RW | 5/3 | `NAKAMA_R2_WRITE_*` | 留（write path 在用） |
| nakama-backup | nakama RW | 4/23 | `NAKAMA_R2_*` mode-agnostic fallback | 留（read path 在用） |

**為何 nakama 兩把都留**：沒申請 `NAKAMA_R2_READ_*`，r2_client.py mode-scoped fallback chain 在 read path 走 mode-agnostic（`NAKAMA_R2_*` = 4/23 那把）。砍 4/23 → restore / verify 會炸。要砍 4/23 必須先補申請 `NAKAMA_R2_READ_*`。

**為何 xcloud 兩把都留**：CF token 詳情頁修修點不進去，無法對照 `R2_ACCESS_KEY_ID=e579f8ab...` 是哪把。兩把 permission 完全相同（Object Read only + 限 xcloud-backup），attack surface 沒擴大。煩躁狀態接受。

**未來想清流程**（2 分鐘）：
1. CF Disable 5/3「xcloud-backup-read」（不刪）
2. 跑 `python -m agents.franky backup-verify`
3. 失敗 = 5/3 在用 → re-enable + delete 4/22；成功 = 5/3 是 dead → delete 5/3
