# Secondary Off-Site Backup Setup — Backblaze B2

**Scope:** 把 R2 nakama-backup 的內容鏡像到 Backblaze B2，作為跨 vendor 災難備援。
**Why:** R2 已有 multi-region replication，但 Cloudflare account-level 失聯 / 計費封鎖 / 主動下架 仍是 single-vendor 風險。B2 月成本 < $0.01，邊際成本可忽略。
**Owner:** 修修一次性帳號設定 + 環境變數。
**測試節奏:** 每月看一次 B2 dashboard 確認最新 mirror 落地。

---

## 1. 開 Backblaze 帳號 + bucket

[backblaze.com/cloud-storage](https://www.backblaze.com/cloud-storage) → Sign up（用同一個 email）。需要信用卡驗證但 free tier 10 GB 永久免費，nakama 用量 < 10 MB 不會收錢。

**Bucket 設定**：

- Name: `nakama-backup-mirror`
- Files: Private
- Default Encryption: Disable
- Object Lock: Disable
- Region: 隨便（all regions 同價），建議 `us-west-002`

## 2. 建 Application Key

B2 dashboard → App Keys → Add a New Application Key:

- Name: `nakama-mirror-write`
- Allow access to Bucket: 限定 `nakama-backup-mirror`
- Type of Access: Read and Write
- Allow List All Bucket Names: ❌ no
- File name prefix: 留空
- Duration: 留空（永久；走 secret-rotation runbook 排程季度 rotation）

按 Create New Key → 拿到 keyID + applicationKey + endpointURL（**只顯示一次**）。

## 3. VPS `.env` 加四個變數

```bash
# 桌機端先加進本機 `.env`，跑 verify smoke：
B2_BUCKET_NAME=nakama-backup-mirror
B2_KEY_ID=<key id>
B2_APPLICATION_KEY=<application key>
B2_ENDPOINT_URL=https://s3.us-west-002.backblazeb2.com

python -c "from shared.secondary_storage import B2Client; print(B2Client.from_env().list_objects(max_keys=3))"
# 預期空列表（[]）— bucket 剛開沒東西
```

## 4. 上 VPS：append 四個 keys（diff-then-append，per [feedback_env_push_diff_before_overwrite.md](../../memory/claude/feedback_env_push_diff_before_overwrite.md)）

```bash
# 1. 從桌機 diff 找需要 append 的 keys
grep -E '^(B2_BUCKET_NAME|B2_KEY_ID|B2_APPLICATION_KEY|B2_ENDPOINT_URL)=' .env > /tmp/b2_keys.txt

# 2. 備份 VPS .env
ssh nakama-vps 'cp /home/nakama/.env /home/nakama/.env.bak.$(date +%Y%m%d_%H%M%S)'

# 3. Append
cat /tmp/b2_keys.txt | ssh nakama-vps 'cat >> /home/nakama/.env'

# 4. 確認落地
ssh nakama-vps 'grep "^B2_" /home/nakama/.env'
```

## 5. 安裝 mirror + verify cron

```bash
ssh nakama-vps 'crontab -e'
# 加兩行：
30 4 * * *  cd /home/nakama && /usr/bin/python3 scripts/mirror_backup_to_secondary.py >> /var/log/nakama/backup-mirror.log 2>&1
30 3 * * 0  cd /home/nakama && /usr/bin/python3 scripts/verify_backup_integrity.py >> /var/log/nakama/backup-integrity.log 2>&1
```

時間表：

| Cron | 排程 | 用途 |
|------|------|------|
| `backup_nakama_state.py` | 04:00 daily | 主 R2 snapshot |
| `mirror_backup_to_secondary.py` | 04:30 daily | R2 → B2 mirror（30 min buffer 給主 backup 完成）|
| `verify_backup_integrity.py` | 03:30 Sunday | 週日驗最近 7 daily / 4 weekly / 3 monthly snapshot |

## 6. 首次 smoke

```bash
# Manual run on VPS（不等 cron）
ssh nakama-vps 'cd /home/nakama && python3 scripts/mirror_backup_to_secondary.py'
# 預期 log:
# nakama.backup_mirror INFO — mirror complete mirrored=N skipped=0 failed=0
# 看 B2 dashboard → nakama-backup-mirror bucket 應該有 state/YYYY/MM/DD/state.db.gz

ssh nakama-vps 'cd /home/nakama && python3 scripts/verify_backup_integrity.py'
# 預期 log:
# nakama.backup_integrity INFO — verify ok key=state/2026/04/25/state.db.gz tables=12 rows=15234
# nakama.backup_integrity INFO — integrity verification complete checked=N ok=N fail=0
```

## 7. Restore from B2（when R2 is unavailable）

DR runbook §3 / §4 預設從 R2 還原。R2 整體失聯時 fallback：

```bash
# 桌機本機，臨時切到 B2 endpoint：
ssh nakama-vps
cd /home/nakama

# 用 aws CLI（boto3 同 endpoint）拉 B2 snapshot
aws s3 cp s3://nakama-backup-mirror/state/2026/04/25/state.db.gz /tmp/state.db.gz \
    --endpoint-url $B2_ENDPOINT_URL \
    --region us-east-1 \
    # AWS_ACCESS_KEY_ID = $B2_KEY_ID
    # AWS_SECRET_ACCESS_KEY = $B2_APPLICATION_KEY
gunzip /tmp/state.db.gz
# 然後接 disaster-recovery.md §3 的 restore 流程（systemctl stop / mv / cp / start）
```

未來如果 B2-as-source 變成常用 path，會擴展 `scripts/restore_from_r2.py` 加 `--source b2` flag。目前手動 aws CLI 即可。

## 8. 預期成本

| 項目 | 規模 | 月成本 |
|------|------|------:|
| Storage（10 MB total） | 永久保留 | $0.00005 |
| Class B / Class C calls（mirror 寫入 + verify 讀取） | ~30 上傳 + ~15 下載 / 月 | $0.000 |
| Egress（restore 用）| 災難時偶發 | $0.01 / GB |

實際每月 < $0.001。Free tier 10 GB 完全 cover。

## 9. Rotation（per [secret-rotation.md](secret-rotation.md)）

每季 rotation：

1. B2 dashboard → Add a New Application Key → 新 key
2. 桌機 `.env` 改 `B2_KEY_ID` / `B2_APPLICATION_KEY`，跑 `python -c "from shared.secondary_storage import B2Client; B2Client.from_env().list_objects(max_keys=3)"` 確認新 key 通
3. VPS append 新值（覆蓋舊行），systemctl 不需重啟（cron job 下次跑時讀新 .env）
4. B2 console revoke 舊 key（保留 7 天再刪保險）

## 相關

- [`docs/capabilities/nakama-backup.md`](../capabilities/nakama-backup.md) — 主 R2 backup 機制
- [`secret-rotation.md`](secret-rotation.md) — B2 token rotation 流程（PR #146 once merged）
- [`disaster-recovery.md`](disaster-recovery.md) — DR 主 runbook（PR #146 once merged）
- [`docs/plans/quality-bar-uplift-2026-04-25.md`](../plans/quality-bar-uplift-2026-04-25.md) — Phase 2B
