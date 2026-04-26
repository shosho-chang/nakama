---
name: Quality Uplift 下一輪起點（2026-04-28+ 接手 — 5B-3 已落地）
description: dotenv sweep / 5C log search / 5B-3 anomaly daemon 三 PR 全 merged + VPS deployed；Phase 5 結束，下次接手挑 Phase 6 vs 別軸線
type: project
created: 2026-04-26
updated: 2026-04-26
originSessionId: 2026-04-26-night
---
2026-04-26 跨三輪 session 一口氣推完 Phase 5 三大塊：dotenv sweep + 5C log search + 5B-3 anomaly daemon。**取代 `project_quality_uplift_next_2026_04_27.md` 跟更早所有同名 memo**。

**Why:** 修修 auto mode「A 然後繼續進 phase 5」+ 後續授權「VPS 部署」→ 我做 sweep / 5C / 5B-3 + 直接 merge + 部署。三輪都靠 self-review path（5C 的 ultrareview tooling crash 只是 cloud 端錯誤、非 quality 信號）。

**How to apply:** 開新對話讀完 `MEMORY.md` → 讀本 memo → Phase 5 已結束，下一步看修修當下優先順序：Phase 6 test coverage / Phase 7 staging / 別軸線（SEO D.2、ingest v2 PR C 等）。

## 三 PR merged + VPS deployed

| PR | Merge | 內容 |
|---|---|---|
| #179 | `bf5b4ed` | **dotenv sweep** 5 處 `os.getenv(K, DEFAULT)` 空字串 trap（health_check / usopp / notifier / multimodal_arbiter）+ 4 regression tests |
| #182 | `222f040` | **Phase 5C** log search FTS5（`shared/log_index.py` + `SQLiteLogHandler` + `/bridge/logs` route + `cleanup_logs.py` cron + 9th nav entry）+ 27 tests |
| #184 | `41193ea` | **Phase 5B-3** anomaly daemon（`shared/anomaly.py` 純函式 + `agents/franky/anomaly_daemon.py` 4 metrics × 1h vs 7d × 3σ）+ 29 tests |

**Tests**：1854 → 2242（跨三 PR；includes 別 session 的 +320，5B-3 自身貢獻 +29）。

## VPS 部署狀態

- ✅ 5C deployed（`/bridge/logs` 視覺驗證 OK，`cleanup-logs` cron `0 4 * * *`）
- ✅ dotenv sweep deployed
- ✅ **5B-3 deployed 2026-04-26 12:35 UTC**：cron `*/15 * * * *` + `cron.conf` 同步加；manual smoke tick `duration_ms=3 anomalies=0`（cold start 正常，要 24h 才有 baseline）；`heartbeats.nakama-anomaly-daemon = success` 已寫入

## 路上抓到 + 已自動修

1. **dotenv 順帶修 VPS .env GCP_SERVICE_ACCOUNT_JSON Mac→Linux path drift** — backup `.env.bak.20260426_5d_fix` 後 sed-replace
2. **`logger.exception()` traceback 被 SQLiteLogHandler 吞掉**（self-review on PR #182）— record.exc_info 進 `extra["exc"]` 讓 FTS5 索引到
3. **`test_emit_swallows_db_error_via_handle_error` 有 `assert ... or True`**（永遠 pass 的假測試）— 改成 monkeypatch deterministic path
4. **5B-3 task prompt §4.4 漏了 `severity` positional arg** — 實作補回 `alert("error", "anomaly", ...)`，否則 dedup + Slack DM 都不會發
5. **5B-3 cron cluster 閾值** — 設 `MIN_CRON_CLUSTER_SIZE=2`（單 cron streak 已被 `probe_cron_freshness` 蓋，daemon 抓的是「同時多 cron 失敗」的 systemic mode）

## ultrareview crash 紀錄 + 修修疑問釐清（2026-04-26）

PR #182 跑 ultrareview crash「Review crashed before producing findings」。不是 GitHub Actions、是 Anthropic 端 cloud agent 的錯誤，本地拿不到 trace。Self-review + tests pass + CI green → squash merge。**Tooling crash ≠ quality 信號**，不該 block。

修修疑問「之前不是都 review skill 嗎？哪時加進 ultrareview？」釐清：兩個一直並存。
- `/review` = 內建 skill（系統提示「Review a pull request」），跑本地 session
- `/ultrareview` = 內建 slash command，跑 Anthropic 雲端 multi-agent，**只有修修能觸發**（計費）
- 從 `~/.claude/history.jsonl` 看，這 repo 第一次 `/ultrareview` 是 PR #132/#133（2026-04-23 左右）；`feedback_dual_review_complementarity.md` 寫過 PR #77 實證兩者抓不同類 bug 零重疊
- 修修過去印象「都是 review skill」可能因為 local review 我自己呼叫沒 UI 痕跡，ultrareview 必須他自己打 `/ultrareview <PR>` 才有

## Phase 6-8 + 別軸線

| 工作 | 規模 | Dep | 狀態 |
|---|:---:|---|---|
| **Phase 6** test coverage 補齊 | 7 天 | none | 沒凍結 task prompt |
| **Phase 7** staging + feature flags | 8 天 | 修修決定 staging 機 | 規模大要錢，必先問 |
| **Phase 8** CI/CD auto deploy + rollback | 4 天 | Phase 7 | blocked |
| **D.2 SEO audit** | — | PR #183 開了（別 session）| `project_d2_seo_audit_pr183.md` |
| **ingest v2 Step 3 PR C** | — | 4 silent corruption bug 必先修 | `project_ingest_v2_step3_in_flight_2026_04_26.md` |

## 不要自己決定的事

- Phase 7 staging — 規模大、要錢、要新 VPS，必先問
- 連續 phase 6 vs 切去別軸線（SEO / ingest / Chopper） — 看修修當下需求
- ultrareview 連續 crash 多次後是否要 abandon 走 self-review only — 累積證據再說

## 開始之前一定要先看

- 本 memo
- [feedback_log_search_fts5_pattern.md](feedback_log_search_fts5_pattern.md) — 5C 沉澱的 FTS5 + handler 教訓
- 原 9-phase plan：`docs/plans/quality-bar-uplift-2026-04-25.md`

## Open follow-up（5B-3 自身）

- **1-2 週後寫 `feedback_anomaly_3sigma_pattern.md`**：誤報多寡 / 真實抓到 issue 機率（task prompt §10）。現在沒實證資料，寫了會空泛。
- **`/bridge/anomaly` 歷史頁面**：5B-3+1 候選，看是否要 dashboard 化現在進 Slack DM 的 anomaly。
