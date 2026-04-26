---
name: Quality Uplift 5 PR VPS deployed (2026-04-26)
description: PR #146/147/152/154/157 全部上 VPS；解鎖 Phase 5/7/8；剩 12 major sweep + 4 phase
type: project
created: 2026-04-26
originSessionId: 74954c3c-fdc6-4064-931b-fe3250a7d804
---
2026-04-26 早 VPS 部署完成。`project_quality_uplift_next_2026_04_25.md` 標的「修修 manual blocker」項 1-3 全清，項 4-5（branch protection + DR drill）還沒做。

**Why:** 修修早上授權「連 VPS 設定」→ 這次部署 Mac 端直接 ssh 跑全套，路上抓到 2 個既存 .env bug + 1 個新 regression bug。

**How to apply:**
- 之後做 Phase 5 dashboard / Phase 7 staging / Phase 8 CI/CD 不再被 VPS dep 卡住
- 12 major sweep 的 PR B 必須包含 `feedback_logger_init_before_load_config.md` 的修法（Phase 3 JSON log 在 cron 沒生效）

## Done

| 項目 | 狀態 |
|---|---|
| `git pull` 5 PR landed (cd4a2d6/7008ae4/b812dff/d23dbb2/8316da0) | ✅ |
| `.env`：B2_* 4 keys + NAKAMA_R2_* + LOG_FORMAT=json | ✅ |
| `B2_ENDPOINT_URL` 補 `https://` 前綴（修修原本漏） | ✅ sed 修 |
| `B2_KEY_ID` 改成真 keyID（原本是 key 名稱 `nakama-mirror-write`） | ✅ 修修 dashboard 找出後 push |
| crontab +2 行（mirror 04:30 daily / integrity 03:30 Sunday） | ✅ |
| `systemctl restart thousand-sunny nakama-gateway` | ✅ |
| `verify_backup_integrity.py` smoke | ✅ checked=3 ok=3 fail=0 |
| `mirror_backup_to_secondary.py` smoke | ✅ mirrored=3 skipped=0 failed=0 |
| `/bridge/health` + `/bridge/docs` 載到（302→login） | ✅ |

## 路上抓到的 bug（記入 sweep）

1. **`feedback_logger_init_before_load_config.md`** — `shared/log.py` `_initialized` cache 鎖 LOG_FORMAT；3 個 backup script `logger = get_logger(...)` 在 `load_config()` 之前跑 → cron 跑時 JSON 沒生效（功能在，缺 structured field）。修法在 memory，列入 12 major sweep PR B
2. **`B2_ENDPOINT_URL` 漏 `https://`** — runbook 範例值是 `https://s3.us-west-002.backblazeb2.com`，修修當初忘 prefix。已 sed 修 VPS + Mac 留給修修自己手動（sandbox 擋 sed）
3. **`B2_BUCKET_NAME` 158 trailing spaces** — dotenv 自動 strip 不影響功能，沒清

## 修修還沒做

- Branch protection setup（`docs/runbooks/branch-protection-setup.md`，GitHub UI 點擊）
- DR drill 跑一次（`docs/runbooks/disaster-recovery.md` §6 半模擬）

兩個都不擋下一輪工作。

## 桌機 .env 待修

```bash
sed -i.bak 's|^B2_ENDPOINT_URL=\([^h]\)|B2_ENDPOINT_URL=https://\1|' .env
```

修修自己跑（sandbox 擋我）。
