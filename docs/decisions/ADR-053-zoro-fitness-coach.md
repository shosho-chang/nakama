# ADR-053: Zoro 對內健身教練（Garmin 連動）

> **改號註記**：原編號 ADR-050，與 main 上 [ADR-050 Video Production Line 歸 Brook](ADR-050-video-production-line-brook-ownership.md)、ADR-052（原亦編 050，Robin promotion package）撞號，2026-07-25 改為 ADR-053。

**Date:** 2026-06-29
**Status:** Accepted
**Relates to:** ADR-012（Zoro/Brook 向外/對內 — 本 ADR 為 carve-out addendum）、ADR-001（Zoro 角色）、ADR-006（HITL 審批佇列 — payload 擴充）

---

## Context

修修要一個依 Garmin 訓練紀錄做「監控訓練量 + 科學化漸進負荷」的數位健身教練，並把課表寫進手錶（Fenix 8）與行事曆。這是 **Owner 個人健身領域的能力擴充，與內容七層 pipeline 正交、非醫療建議**。

歸屬決策：維持 **Zoro**（建 `agents/zoro/coach/` 子套件 + `coach-sync` subcommand），不另立新 agent。理由：既有 argparse 分發 / 成本歸因 / heartbeat toolchain 可直接複用；新 agent 的 framing 成本（ADR、ARCHITECTURE、CONTEXT、cron、cost label）對單人自用是 over-investment。代價是「Zoro = 向外」的純粹性被打破 → 由 ADR-012 addendum 明確 carve-out（對內教練**不**套用向外/對內 framing）。

Phase 0 spike（見 `docs/research/2026-06-29-zoro-coach-implementation-plan-v2.md` §10.0）已對真 Fenix 8 驗證關鍵契約：garth 已死、garminconnect 0.3.x 原生 DI OAuth（Python ≥3.12）、`exerciseSets` 欄位/null 行為、`get_activities_by_date` 不可傳 sub-type、Fenix 8 支援預載 target weight。

## Decision

1. **角色**：Zoro 多領域 = 對外情報（scout / keyword research）+ 對內健身教練。後者 carve-out，不套 ADR-012 framing。
2. **資料**：新增 `strength_sets` 表（canonical DDL `migrations/018_strength_sets.sql`，bootstrap 副本在 `shared/state.py:_init_tables`），owner `shared/strength_sets_store.py`。自然鍵 `(activity_id, set_index = Garmin messageIndex / array position)` → 冪等 re-sync（INSERT OR IGNORE）。
3. **讀回**：`python-garminconnect`（optional extra `coach`，Python ≥3.12）。`get_activity_exercise_sets` 是裸 passthrough → schema-validation adapter，未知結構即 raise + `alert`（不靜默算錯）。
4. **HITL**：擴 ADR-006 `ApprovalPayloadV1` union（`WriteGarminWorkoutV1` / `ScheduleTrainingBlockV1`），帶 `CoachComplianceV1`（WP3 guardrail 結果，claim 端可再驗）。DB `target_platform`/`action_type` 無 CHECK，免 migration。
5. **寫入**：重訓走 Garmin 原生 Strength Builder 規格（Zoro 出規格、修修一鍵建）；耐力（Phase 2）走 Tredict HTTP API + 手動 apply gate。MVP 不做逆向自動寫入。
6. **科學依據**：ACSM 2026 阻力訓練 position stand（容量 + RIR 框架，覆蓋舊 8–12 教條）+ NSCA 2-for-2；E1RM 僅 ≤10 reps；deload 用客觀代理（completed-rep 下滑 / MRV 逼近 / 時間 fallback）。
7. **成本歸因**：MVP 掛 `zoro`（`set_current_agent("zoro")`）；如需與 scout 分離再用獨立 label。
8. **vault 報告**（Phase 2）：訓練週報落 `KB/Wiki/Digests/Training/`（不碰 `Journals/` 紅線）；該 writer 落地時同步更新 `docs/VAULT-LAYOUT.md`。

## Consequences

- 同步更新：`agents/zoro/README.md`（重寫）、`agents/zoro/CONTEXT.md`（lazy-create）、ADR-001 Zoro row、ADR-012 addendum。
- **ARCHITECTURE.md 的 web-routers 清單暫不加 coach**：目前無 coach Bridge router（UI 屬 WP4 表層 / WP6，尚未落地）；待 `/bridge` coach surface 上線的 UI batch 再補，避免文件記錄不存在的 router。
- `coach-sync` 為純函式 cron 路徑（繞過 BaseAgent），自接 heartbeat（job `zoro-coach-sync`）。
- 資安：Garmin token 走 `data/garmin/`（gitignored、0600，沿用 `shared/google_calendar.py` 本機登入→搬 VPS pattern）；標明流向 LLM 的欄位。
- 分期：Phase 1 = WP1–WP6（讀回 / 漸進 / 課表+guardrail / Builder 規格 / HITL / 可視化）；Phase 2 = Tredict 單車 + 恢復 + 排程整合 + 週日自動化 + Slack 對話/readiness。

## Status note on ADR-006

ADR-006 header 仍 `Proposed` 但 code 已 shipped；本 ADR 以 **code 為 SoT** 擴 payload union，不阻於 ADR-006 狀態。
