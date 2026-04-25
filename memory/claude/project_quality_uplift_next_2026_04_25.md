---
name: Quality Uplift 下一輪起點（2026-04-25 compact 後繼續）
description: 5/9 PR merged，剩下 4 phase + 12 major sweep；recommended 工作順序、各項 dep、修修 manual blocker 狀態
type: project
created: 2026-04-25
originSessionId: 74954c3c-fdc6-4064-931b-fe3250a7d804
---
5 個 quality uplift PR (#146/147/152/154/157) 全部 squash-merged 進 main。Compact 後接著做 Phase 4-8 + 12 major sweep。

**Why:** 修修在 5 PR merged 後說「我在此處 compact 之後繼續好了，把剩下的 phase 走完」。下次對話起點需要明確的「下一步是什麼 + 不能踩什麼 dep」。

**How to apply:** 開新對話讀完 MEMORY.md → 讀本 memory 直接照「下一步」段挑工作開。修修 manual VPS deploy 是隱形 blocker（除非他先說做完了），優先選不依賴 VPS 的工作。

## 已完成（A-bar 5/9）

| Phase | A-bar | merged commit |
|---|---|---|
| 1 — DR | D+→A | `cd4a2d6` (#146) |
| 2A — Backup multi-tier | B+→A− | `7008ae4` (#147) |
| 2B — Backup integrity + B2 mirror | A−→A | `b812dff` (#154) |
| 3 — Observability foundation | C→A | `d23dbb2` (#152) |
| 9 — Version control + Doc | C/D→A | `8316da0` (#157) |

## 待做（4 phase + 12 major sweep）

| 工作項 | 規模 | Dep | 適合並行？ |
|---|:---:|---|---|
| **12 major sweep**（清單見 [project_quality_uplift_review_2026_04_25.md](project_quality_uplift_review_2026_04_25.md)） | 1-2 天 | none | ✅ 純 local |
| **Phase 6 test coverage** | 7 天 | none | ✅ 純 local |
| **Phase 4 postmortem 制度化** | 3 天 | none（#152 alert API 已有） | ✅ doc-only |
| **Phase 5 observability advanced**（dashboard + anomaly + log search FTS5） | 7 天 | #152 alert API（已有）| △ 需 VPS 看效果 |
| **Phase 7 staging + feature flags** | 8 天 | none（但需新 VPS 環境） | ❌ 修修要先決定 staging 機 |
| **Phase 8 CI/CD auto deploy + rollback** | 4 天 | Phase 7 | ❌ |

## 修修 manual blocker（merge 後待做）

下面這些做完才會「實際吃到」#146-#157 的好處；沒做完功能還是上線，但 VPS 沒在用：

1. `scp` VPS `.env` — diff 後 append（不要整份覆蓋 — `feedback_env_push_diff_before_overwrite`）
   - 新增 keys：`NAKAMA_R2_WRITE_*` / `NAKAMA_R2_READ_*` / `B2_*` / 三個 retention vars
2. VPS crontab 加 2 行（`docs/runbooks/backup-secondary-setup.md`）
   - `30 4 * * * .../scripts/mirror_backup_to_secondary.py`
   - `30 3 * * 0 .../scripts/verify_backup_integrity.py`
3. `systemctl restart thousand-sunny nakama-gateway`（兩個獨立 service — `feedback_vps_two_services`）
4. Branch protection setup（`docs/runbooks/branch-protection-setup.md`）
5. DR drill 跑一次（`docs/runbooks/disaster-recovery.md` §6 半模擬版）

下次開對話前先問一句「VPS deploy 做了沒？」，沒做就避開 Phase 5/7/8。

## 推薦下一步（沒問就照這個順序）

1. **12 major sweep**（1-2 天）— 一次性收尾 review 抓到的 12 條 major + 5 條 nit。最值得先做，因為這些都是 5 PR 體外傷口、修完才算真正落地。建議拆成 3 個小 PR：
   - PR A：`/bridge/health` + `/bridge/docs` 的 chassis-nav 統一 + Hub 加入口（#152 + #157 共 4 條 major）
   - PR B：3 個 mock spec= 修補（`feedback_mock_use_spec` 又踩 #146/#152/#154）+ `verify_db` dedupe（#146 inline → import shared）
   - PR C：Franky `health_check.py` 改 `mode="read"` + verify/mirror 接 alert+heartbeat（#147 + #154 各 1 major）
2. **Phase 4 postmortem**（3 天）— doc-only，沒 VPS dep。建立 incident postmortem template + runbook + 半年 retro 機制
3. **Phase 6 test coverage**（7 天）— 純 local，可拆多 sub-PR。先掃 `coverage report`，然後針對 < 60% 的模組補
4. **Phase 5 observability advanced**（7 天）— 等修修先 VPS deploy #146-#157 才做（不然新 dashboard / anomaly 在 Mac 看不到生產資料）
5. **Phase 7 staging**（8 天）— 修修要先決定 staging 機放哪（可選：另開一台 VPS / Vultr 較便宜的 plan / 用 Docker Compose local stack）
6. **Phase 8 CI/CD**（4 天）— Phase 7 完才開

## 開始之前一定要先看

- [project_quality_uplift_review_2026_04_25.md](project_quality_uplift_review_2026_04_25.md) — 12 major + 5 nit 完整清單（修法都寫好了）
- [feedback_subagent_shared_worktree.md](feedback_subagent_shared_worktree.md) — 並行 sub-agent 必開 worktree，這次踩坑教訓
- 原 9-phase plan：`docs/plans/quality-bar-uplift-2026-04-25.md`

## 不要自己決定的事（必先問修修）

- Phase 7 staging 要不要動工 — 規模大、要錢、要新 VPS，必先問
- 12 major 是不是一次掃完 vs 邊做新 phase 邊修 — 修修偏好可能不一樣
- 順序 1→6 是不是真的照走 — 修修可能想先看到 Phase 5 dashboard 或 Phase 4 doc
