---
name: Quality Uplift 9-phase final state — 6/9 ✅ + Phase 7+8 deferred + Phase 9 still 2 clicks
description: 2026-04-27 final state：Phase 1-6 全 ship + Phase 7+8 修修拍板 deferred + Phase 9 程式 ship 但 branch protection 還缺 2 click 跟 browser smoke
type: project
created: 2026-04-26
updated: 2026-04-27
originSessionId: 2026-04-27-final
---

2026-04-27 收尾：本 session 推完 Phase 6 Slice 2/3/4（PR #194/#195/#196 全 merged）+ 修修升 GitHub Pro + 設 branch protection（partial）+ 修修拍板 Phase 7+8 deferred。**取代之前所有 quality_uplift_next memo**。

**Why:** Phase 6 收尾後重 frame 整個 plan：Phase 7 staging + Phase 8 auto deploy 對 solo dev / pre-revenue / 個人 tooling 階段是 over-engineering。CI 2400+ test + Franky probe + git revert ~5 min rollback 已 cover staging 95% 的 value。修修同意，並要求把「一次攻頂」反射列為要主動提醒的 feedback ([feedback_avoid_one_shot_summit.md](feedback_avoid_one_shot_summit.md))。

**How to apply:** 任何「要不要做完整 staging / auto-deploy / 大採購」之類問題，default 答案是「先確認真實 bottleneck，沒驗證的 ceiling 提升不買」。

---

## 9-phase final state

| # | Phase | 狀態 | 對應 PR / commit | 註 |
|---|---|:---:|---|---|
| 1 | DR drill + secret rotation | ✅ | #146 + #187 | — |
| 2 | Backup A 升級 | ✅ | #147 + #154 | — |
| 3 | Observability foundation | ✅ | #152 | — |
| 4 | Incident postmortem | ✅ | #166 + #187 | — |
| 5 | Observability advanced | ✅ | 6 sub-PR | — |
| **6** | **Test coverage** | ✅ | #190/#194/#195/#196 | 4 slice 全 merged 2026-04-27 |
| 7 | Staging + feature flags | ❎ **Deferred** | — | 修修拍板 over-eng for solo |
| 8 | CI/CD auto deploy | ❎ **Deferred** | — | blocked by 7 + 同 over-eng |
| **9** | **版控 polish + Doc A+** | 🟡 | #157 + 本 session | 程式 ship；branch protection 設好但有 2 漏洞 |

**整體：6/9 ✅ + 1/9 🟡 + 2/9 ❎ Deferred**

「Quality bar A」實質達成 — Phase 7+8 deferred 不算缺口（是 enterprise team 才需要的 infra）。

---

## Phase 9 全綠 ✅（2026-04-27 收尾）

修修升 GitHub Pro + 補完 branch protection 兩 click（enforce_admins + required status checks）+ 瀏覽器 smoke `/bridge/docs`。

API state 確認：
```
enforce_admins: True
required_status_checks.contexts: ["lint-and-test", "lint-pr-title"]
required_linear_history: True
allow_force_pushes: False
allow_deletions: False
```

Push test 驗證：直接 push main 真擋
```
remote: error: GH006: Protected branch update failed for refs/heads/main.
- Changes must be made through a pull request.
- 2 of 2 required status checks are expected.
```

**整體：7/9 ✅ + 2/9 ❎ Deferred = 該 ship 的全 ship**。

## 結案條件（2026-04-27 修修拍板）

**整個 quality-bar-uplift 案 closed 條件**：

1. Phase 7+8 deferred ✅ 已記
2. **Bridge UI 大修正完成** — 修修專門撥時間統一整個 Bridge 美學體系
   - 一併處理 `/bridge/docs` 兩個 friction：(a) result title clickable → markdown viewer route, (b) stats line 視覺凌亂
   - 對齊 `feedback_aesthetic_first_class.md` + `docs/design-system.md`
   - 範圍：所有 `/bridge/*` page
3. UI 修完後 → 整個 quality-bar-uplift project 標 closed

**Bridge UI 大修正未啟動，等修修分配時間。** 不在本 plan tracking 範圍 — 是 design 級工作不是 quality bar 補洞。

---

## Phase 7+8 deferred 決策（2026-04-27）

**決策：deferred until further notice**

**Why deferred**：
- nakama 是 solo dev / pre-revenue / 個人 tooling，沒有 multi-team 協作或 SLA 客戶
- 已有的 quality net 已涵蓋 staging 95% value：CI 2400+ test、critical-path coverage gate、Franky probe 5min、live_wp Docker E2E、git revert ~5min rollback
- staging setup 5 工作天 + 1-2h/週 維護 → 投入 Chopper community / 內容生產 ROI 高 50-100x
- DS918+ 4GB RAM 跑得起來但 marginal；要升 8GB 才舒適

**Trigger 升回 active**（同時成立才考慮）：
1. 自由艦隊月活 100+ 付費會員
2. Production touchpoint > 100/天
3. 出現雲端 / 現有 quality net 解不了的具體 bottleneck

**改做的事**（lightweight production safety net，半天工作量、Phase 9 收完後可做）：
- Manual deploy checklist（10 行 markdown）：「pull → restart service → curl smoke → 驗 alert」
- Rollback runbook（10 行）：「git revert HEAD → push → restart」
- Franky probe 已自動跑（已有）

**DS918+ 三題答案**（給未來參考）：
- DSM 7.1.1 ✓ Container Manager 可裝
- RAM 4GB（要做 staging 要升 8GB 才舒適）
- 上行 300Mbps（充裕）

DS918+ 留著做家用 NAS / 媒體櫃 / 個人 vault sync — 不浪費。

---

## Open follow-up（不阻塞，下次決定要不要做）

### Phase 6 follow-up
- **Slice 4b — Robin full IngestPipeline E2E**（~0.5-1 天）：本次 Slice 4 Robin 只 cover orchestration layer，full pipeline 6 個 LLM call site + 5+ vault page write 留 follow-up
- **`UpdateWpPostV1.patch: dict` schema contract gap**：Slice 3 reviewer 抓到 `patch` 是無約束 dict 但實作只支援 JSON-primitive；獨立 PR 改 schema 加約束或 doc as contract

### Phase 1-5 留下（從前 memo 帶過來）
- **A5**：現役 VPS `apt install sqlite3` — 修修手動 ssh
- **A6**：`verify_db()` table count -1 — low pri
- **incident archive #3**：`list_pending_incidents` mtime → 改讀 frontmatter `detected_at`
- **incident archive #4**：`_archive_alert` 用 dispatch `now` → 改 `alert.fired_at`
- **Mac vault sync hook**：repo `data/incidents-pending/` → vault `Incidents/YYYY/MM/`
- **Bridge UI `/bridge/incidents`**：候選看頻率決定
- **5B-3 anomaly daemon**：1-2 週後寫 `feedback_anomaly_3sigma_pattern.md`

### PR #187 service restart 決策（修修選）
nakama-usopp alert path 改了 `_archive` hook，systemd service 沒重啟 = sticky import；下次 publish 失敗 alert 不走 archive。修修決定立刻 restart 還是等下次 deploy 順手帶。

---

## 不要自己決定的事

- **Phase 7 重啟（staging）** — trigger 條件全滿足前不主動建議
- **大採購（Pro 5000 / 6000）** — 套用 [feedback_avoid_one_shot_summit.md](feedback_avoid_one_shot_summit.md)
- **production 完全自架** — 預設 Hybrid（cloud production + dev/batch 自架）

---

## 本 session 推進摘要

| 項目 | 動作 |
|---|---|
| PR #190 | squash merged 6109f9b |
| PR #194 (Slice 2) | open + review LGTM + merged dee19f4 |
| PR #195 (Slice 3) | open + review +amend (discriminated union + all-optionals) + merged 1465753 |
| PR #196 (Slice 4) | open + review + amend (Robin parametrize + Zoro dispatch + Brook nit) + merged 769a8e3 |
| VPS cron | memory-prune cron 加進 crontab（每月 1 號 03:00 Asia/Taipei） |
| GitHub Pro | 修修升完，branch protection API 真實 enforce |
| Branch protection 設定 | 修修設好主框架 / 還缺 enforce_admins + required_checks |
| Phase 7+8 拍板 | deferred until trigger conditions |

**Tests: 2367 → 2422（+55）**。Critical-path coverage 8/8 全綠。

---

## 開始之前一定要先看

- 本 memo
- [feedback_avoid_one_shot_summit.md](feedback_avoid_one_shot_summit.md) — 修修自陳的「一次攻頂」反射要 reframe
- [project_hardware_purchase_evaluation.md](project_hardware_purchase_evaluation.md) — GPU 升級 ladder + Hybrid 框架
- 原 9-phase plan：`docs/plans/quality-bar-uplift-2026-04-25.md`
- [feedback_pr_review_merge_flow.md](feedback_pr_review_merge_flow.md) — PR review/merge 全自動流程
