---
name: Quality Uplift 下一輪起點（2026-04-26 清對話 後繼續）
description: 12-sweep + VPS deploy + Phase 1-3/9 全 done；剩 Phase 4-8 + 修修 VPS pull/restart；推薦 Phase 4 起手
type: project
created: 2026-04-26
originSessionId: 74954c3c-fdc6-4064-931b-fe3250a7d804
---
2026-04-26 一個 session 內：5 PR VPS deployed + 12-major sweep 三 PR 全 merged。下次新對話照本 memo「下一步」段挑工作開。

**Why:** 修修當天說「ok go」一路自動執行到「清對話」。下次對話起點需要乾淨的 context（取代過時的 `project_quality_uplift_next_2026_04_25.md`）。

**How to apply:** 開新對話讀完 `MEMORY.md` → 讀本 memo → 直接從 Phase 4 起手（除非修修指定別的）。先問「VPS pull + restart 做了沒？」決定 Phase 5 能不能開。

## 已完成（A-bar 5/9 phase + 12 sweep + VPS）

### Phase 1-9 main work（5 PR merged 2026-04-25）

| Phase | A-bar | merged |
|---|---|---|
| 1 — DR | D+→A | `cd4a2d6` (#146) |
| 2A — Backup multi-tier | B+→A− | `7008ae4` (#147) |
| 2B — Integrity + B2 mirror | A−→A | `b812dff` (#154) |
| 3 — Observability foundation | C→A | `d23dbb2` (#152) |
| 9 — Version control + Doc | C/D→A | `8316da0` (#157) |

### 12 sweep（3 PR merged 2026-04-26）

| PR | Commit | 內容 |
|---|---|---|
| #161 A | `2dd1998` | chassis-nav 統一 + Hub 加入口（4 majors）|
| #162 B | `120b955` | logger lazy load + verify_db dedupe + 4 mock spec= + VPS regression（5 findings）|
| #163 C | `3e7ba92` | Franky read mode + verify/mirror alert+heartbeat（3 findings）|

### VPS deploy（2026-04-26 早完成）

- `git pull` 5 PR + crontab +2 行（mirror 04:30 / integrity Sun 03:30）+ services restart
- B2 endpoint 補 `https://` + B2_KEY_ID 修值（修修 dashboard 找出）
- smoke：integrity ✓ checked=3 ok=3 / mirror ✓ mirrored=3
- Note：sweep PR A/B/C 還沒 deploy（修修要再 pull + restart 一次）

## 待做

### 4 phase 剩餘

| 工作 | 規模 | Dep | 並行？ |
|---|:---:|---|---|
| **Phase 4 postmortem 制度化** | 3 天 | none | ✅ doc-only |
| **Phase 6 test coverage** | 7 天 | none | ✅ 純 local |
| **Phase 5 obs advanced**（dashboard + anomaly + log search FTS5） | 7 天 | 修修 VPS pull/restart | △ 要 VPS |
| **Phase 7 staging + feature flags** | 8 天 | 修修決定 staging 機 | ❌ |
| **Phase 8 CI/CD auto deploy + rollback** | 4 天 | Phase 7 | ❌ |

### 修修 manual（吃到效果）

1. **VPS pull + restart**（PR A/B/C 全要）：
   ```bash
   ssh nakama-vps 'cd /home/nakama && git pull && sudo systemctl restart thousand-sunny nakama-gateway'
   ```
   - PR A：chassis-nav 8 entries
   - PR B：cron log 變 JSON（`journalctl -u thousand-sunny | grep "^{"` 應有命中）
   - PR C：`/bridge/health` 顯示 `nakama-backup-mirror` + `nakama-backup-integrity` heartbeat（下次 cron fire 後）
2. Branch protection（`docs/runbooks/branch-protection-setup.md`，GitHub UI）
3. DR drill 半模擬（`docs/runbooks/disaster-recovery.md` §6）

## Reviewer flagged backlog（不 urgent，沒新 PR）

從 sweep PR C 的 review 出來的 follow-up，未來想做時開新 ticket：

1. **Franky probe 偵測 B2 partial misconfig**：目前 mirror 把 `B2Unavailable` 當 success（為了 VPS 還沒 setup B2 也可裝 cron）。但若 operator 之後 unset B2_* env，cron 會 silently 不再 mirror。應該 Franky probe 偵測。
2. **`@cron_observability` decorator**：`record_success/failure + alert(error)` 三個 cron 重複同 shape，drift 風險，可抽 decorator。

## 推薦下一步（沒問就照這個）

**Phase 4 postmortem 制度化** — 3 天、doc-only、無 dep、最快 close 一個 phase。產出：
- incident postmortem template
- runbook：什麼算 incident / 流程 / 半年 retro
- 跟 Phase 3 alert API 對齊（既已有 `shared.alerts.alert("error", ...)` 可當 incident trigger）

之後序：Phase 6 test coverage → Phase 5 obs advanced（要修修先 VPS deploy 才看得到）→ Phase 7 staging（必先問修修要不要動工）→ Phase 8 CI/CD。

## 不要自己決定的事（必先問修修）

- Phase 7 staging 要不要動工 — 規模大、要錢、要新 VPS，必先問
- 順序 Phase 4 → 6 → 5 → 7 → 8 是不是真的照走
- 12-sweep 已 done 但 VPS 還沒 deploy（修修可能要先 deploy 再做下一輪，避免堆 unmerged 行為差異）

## 開始之前一定要先看

- 本 memo
- [project_quality_uplift_sweep_done_2026_04_26.md](project_quality_uplift_sweep_done_2026_04_26.md) — 12 sweep 細節
- [project_quality_uplift_vps_deployed_2026_04_26.md](project_quality_uplift_vps_deployed_2026_04_26.md) — 5 PR VPS deploy 路徑
- [feedback_logger_init_before_load_config.md](feedback_logger_init_before_load_config.md) — logger init bug 已修 + 同類 anti-pattern 警告
- 原 9-phase plan：`docs/plans/quality-bar-uplift-2026-04-25.md`
