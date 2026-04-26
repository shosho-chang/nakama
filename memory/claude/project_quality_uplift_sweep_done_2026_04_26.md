---
name: 12-major sweep 完成（2026-04-26）
description: 3 PR (A/B/C) 清完 12 major + bonus logger regression；剩 Phase 4-8 + 修修 manual
type: project
created: 2026-04-26
originSessionId: 74954c3c-fdc6-4064-931b-fe3250a7d804
---
2026-04-26 一個 session 內三個 sweep PR 全 merged，12 major review findings 清空。

**Why:** 修修早授權 VPS deploy → 路上抓到 logger init regression → 順勢做完整 sweep。三個 PR 各自 bring up sub-agent reviewer，全 MERGE OK 沒 blocker。

**How to apply:** `project_quality_uplift_next_2026_04_25.md` 標的「12 major sweep」項清掉。剩 Phase 4-8 + 修修 manual。

## Sweep tracker

| PR | Merge commit | Closed | Tests added |
|---|---|---|---|
| #161 PR A chassis-nav unify | `2dd1998` | 4 majors（taxonomy + Hub 缺入口 + memory/cost 漏 DRAFTS + draft_detail 漏 nav cover） | 8 parametrize regression |
| #162 PR B logger lazy + verify_db dedupe + spec= | `120b955` | 5 findings + VPS logger regression（`feedback_logger_init_before_load_config`） | 1 lazy-load regression |
| #163 PR C Franky read mode + verify/mirror alert+heartbeat | `3e7ba92` | 3 findings（#147 mode=write→read + #154 silent failure × 2） | 7 alert/heartbeat wiring |

Total +16 regression tests / 1765 → 1772 passed.

## 路上抓到的 follow-up（reviewer flagged，新 ticket 級別）

1. **B2-misconfig silent drift detection** — 目前 mirror 把 `B2Unavailable` 當 success（讓 VPS 還沒 setup B2 也可裝 cron）。但若 operator 之後 unset B2_* env，cron 會 silently 不再 mirror。應該由 Franky probe 偵測「B2_* 部分 set」warn。
2. **`@cron_observability` decorator** — `record_success/failure + alert(error)` 三個 cron 重複同 shape，drift 風險，可抽 decorator。

兩條都列入 backlog（不 urgent，沒新 PR）。

## 修修待辦（merge 後吃到效果）

1. **VPS pull + restart**（PR A/B/C 全要）：
   ```bash
   ssh nakama-vps 'cd /home/nakama && git pull && sudo systemctl restart thousand-sunny nakama-gateway'
   ```
   - PR A 影響：chassis-nav 8 entries 顯示在所有 /bridge/* 頁
   - PR B 影響：cron log 從下一輪起用 JSON format（`journalctl -u ... | grep "^{"` 應有命中）
   - PR C 影響：mirror/integrity cron 下次 fire 後 `/bridge/health` 應顯示新 `nakama-backup-mirror` + `nakama-backup-integrity` heartbeat
2. Branch protection setup（GitHub UI / `docs/runbooks/branch-protection-setup.md`）— 未做
3. DR drill 半模擬（`docs/runbooks/disaster-recovery.md` §6）— 未做

## 還剩 4 phase

- Phase 4 postmortem 制度化（3 天，doc-only，無 dep）
- Phase 6 test coverage 補齊（7 天，純 local）
- Phase 5 obs advanced（7 天，要 VPS deploy 先）
- Phase 7 staging（8 天，修修要先決定 staging 機）
- Phase 8 CI/CD auto deploy + rollback（4 天，dep Phase 7）

推薦序仍是 Phase 4 → Phase 6 → Phase 5 → Phase 7 → Phase 8。
