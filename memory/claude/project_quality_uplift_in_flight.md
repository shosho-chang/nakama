---
name: Quality bar uplift — 5 PRs in flight, 4 phases remain
description: 9-phase plan 5/9 完成（PR open）；A-bar 進度表；修修 manual after merge；Phase 4-8 ordering + dep；同 1-line defensive fix 跨 PR；Phase 2B 補完小事
type: project
created: 2026-04-25
originSessionId: 74954c3c-fdc6-4064-931b-fe3250a7d804
---
Plan：[`docs/plans/quality-bar-uplift-2026-04-25.md`](../../docs/plans/quality-bar-uplift-2026-04-25.md)（在 PR #146 merge 後落地到 main）。9 個 phase，5 個 OPEN PRs 飛行中，4 個待開。

## 5 PRs OPEN（all target main, 2026-04-25 同日批量）

| PR | Phase | Effort | A-bar move | 主要 deliverable |
|---|---|:---:|---|---|
| #146 | 1 — DR | 5 天 | DR D+→A | `docs/runbooks/{disaster-recovery,secret-rotation}.md` + `scripts/restore_from_r2.py` + `docs/plans/quality-bar-uplift-2026-04-25.md` |
| #147 | 2A — Backup multi-tier | 3 天 | Backup B+→A−（與 #154 一起到 A）| `scripts/backup_nakama_state.py` 改 daily/weekly/monthly + `R2Client.from_nakama_backup_env(mode="write"\|"read")` |
| #152 | 3 — Observability foundation | 5 天 | Obs C→B+ | `shared/log.py` JSON mode + `shared/heartbeat.py` + `shared/alerts.py` + `/bridge/health` |
| #154 | 2B — Backup integrity + B2 | 3 天 | Backup A 完成 | `shared/sqlite_integrity.py` + `shared/secondary_storage.py`(B2) + `scripts/{verify_backup_integrity,mirror_backup_to_secondary}.py` |
| #157 | 9 — 版控 polish + Doc A+ | 3 天 | 版控 A−→A + Doc A→A+ | `.github/workflows/conventional-commits-lint.yml` + `scripts/{release,prune_old_memories}.py` + `shared/doc_index.py` + `/bridge/docs` + `docs/runbooks/branch-protection-setup.md` |

## A-bar 進度表

| 維度 | 起始 | 目標 | 進度 |
|------|:---:|:---:|:---|
| Disaster Recovery | D+ | A | ✅ #146 |
| Backup | B+ | A | ✅ #147 + #154 |
| Observability | C | B+ | ✅ #152（A 待 Phase 5）|
| Incident Postmortem | C+ | A | ⏸ Phase 4（dep #152）|
| Test Coverage | B+ | A | ⏸ Phase 6（無 dep，可隨時開）|
| Staging | C− | A | ⏸ Phase 7（無 dep）|
| 版控 | A− | A | ✅ #157 |
| CI/CD | A− | A | ⏸ Phase 8（dep Phase 7）|
| Documentation | A | A+ | ✅ #157 |

5/9 phases 完成（待 merge）。

## 衝突狀態（git rebase 處理）

**`scripts/backup_nakama_state.py` 同 1-line 防禦修在 #147/#152/#154/#157 都加**：

```python
retention_days = int(os.environ.get("NAKAMA_BACKUP_RETENTION_DAYS") or "30")
```

理由：empty-string env value（.env 寫 `KEY=`）通過 `os.environ.get` default-arg check 但 `int("")` 炸。4 個 PR 內容一致，git auto-merge。

**`thousand_sunny/routers/bridge.py`** 在 #152（health route）和 #157（docs route）各加一 route + 一行 import。不同 hunk 但 import 行需 1-line manual reconcile。

`.env.example` 各 PR 加不同段，純 append，零衝突。

## 修修 manual after 5 PRs all merged

```bash
ssh nakama-vps && cd /home/nakama && git pull
```

`.env` append（依各 PR §修修 manual）：
- `NAKAMA_LOG_FORMAT=json`（#152）
- `B2_BUCKET_NAME` / `B2_KEY_ID` / `B2_APPLICATION_KEY` / `B2_ENDPOINT_URL` 已在修修本機 .env，要 scp 到 VPS（#154；修修已開好 B2 帳號 + bucket `nakama-backup-mirror` + write key）
- `NAKAMA_BACKUP_WEEKLY_RETENTION_WEEKS=12` / `_MONTHLY_RETENTION_MONTHS=12`（#147，可選；defaults already 12）
- `SLACK_FRANKY_BOT_TOKEN`（#152 alerts 用；如尚未設）

`crontab -e` append 三條：
- `30 4 * * *  python3 scripts/mirror_backup_to_secondary.py >> /var/log/nakama/backup-mirror.log 2>&1`（#154）
- `30 3 * * 0  python3 scripts/verify_backup_integrity.py >> /var/log/nakama/backup-integrity.log 2>&1`（#154）
- `0 3 1 * *  python3 scripts/prune_old_memories.py --apply >> /var/log/nakama/memory-prune.log 2>&1`（#157）

`systemctl restart thousand-sunny nakama-gateway nakama-usopp`

GH console branch protection（#157 [`docs/runbooks/branch-protection-setup.md`](../../docs/runbooks/branch-protection-setup.md)）。

DR drill 半量 30 min（#146 [`docs/runbooks/disaster-recovery.md`](../../docs/runbooks/disaster-recovery.md) §6.1）— Phase 1 task 不算完成直到 drill 跑過。

Smoke：開 `https://nakama.shosho.tw/bridge/health` 看 heartbeat、`/bridge/docs?q=R2 backup` 看 FTS5 search。

## Phase 4-8 ordering + dep

| Phase | Effort | Dep | 可開時機 |
|---|:---:|---|---|
| 4 — Incident postmortem 制度 | 3 天 | #152 merge | #152 後 |
| 5 — Obs advanced（dashboard + anomaly + log search FTS5）| 7 天 | #152 merge | #152 後 |
| 6 — Test coverage 補齊 | 7 天 | 無 | 隨時（5 PR 飛行期間平行做也 OK）|
| 7 — Staging + feature flags | 8 天 | 無 | 隨時 |
| 8 — CI/CD auto deploy | 4 天 | Phase 7 | Phase 7 後 |

**下一個建議**：Phase 6（test coverage）— 跟 in-flight 5 PR 零檔案衝突，可平行做。

## Phase 2B 補完小事（#146 merge 後）

`shared/sqlite_integrity.py`（在 #154）和 `scripts/restore_from_r2.py`（#146）的 `verify_db()` 內聯各一份。#146 merge 後開小 follow-up PR：

- `scripts/restore_from_r2.py` 改 `from shared.sqlite_integrity import verify_db`
- 刪 inline copy
- 1 個 test 引用更新

~5 line patch。

## 共用教訓（已寫進對應 PR description / 不另開 feedback memory）

1. **同 1-line defensive fix 跨 4 PR**：寫測試只在 isolated 通過、full suite fail，根因是 `load_config` 載 .env 把空字串注入 os.environ。`int(env.get(K) or default)` 比 `int(env.get(K, default))` 安全。
2. **Phase PR 之間用平行 branch + diamond merge**（per `feedback_stacked_pr_squash_conflict.md`）：每個 phase 獨立 branch off main，target main，不 stack。同檔 1-line 衝突走 git rebase auto。

## 下次 session reload checklist

如果新 session 接手：
1. `gh pr list` 看 #146/#147/#152/#154/#157 狀態
2. 讀 `docs/plans/quality-bar-uplift-2026-04-25.md` 看 Phase 4-8 deliverable
3. 看本 memory 的 A-bar 進度 + 修修 manual + ordering 表
4. 若 PR 已 merge → 標 task list 對應 phase completed；確認 VPS pull + .env append + cron
5. 若修修指 Phase 6（test coverage）→ `pytest --cov=. --cov-report=term-missing` 找低 coverage 模組（thousand_sunny SSE / robin router 已知 <80%）+ 補 E2E per agent + FSM property-based test
