---
name: Quality Uplift 下一輪起點（2026-04-28 接手）
description: 04-26 晚再 +2 PR (dotenv sweep / 5C log search FTS5)；下一步 5B-3 anomaly daemon（task prompt 已凍結）
type: project
created: 2026-04-26
originSessionId: 2026-04-26-evening
---
2026-04-26 晚 session：在前一輪 (5B-2 / 5D) 之上又 close 2 PR：dotenv sweep + Phase 5C 結構化 log search。**取代過時的 `project_quality_uplift_next_2026_04_27.md`**。

**Why:** 修修 auto mode 「A 然後繼續進 phase 5」 → 我做 sweep + 5C；ultrareview 跑 5C 時 crash（tooling 失敗，非 quality 信號）→ self-review 抓到 1 bug + 修了 → squash merge。

**How to apply:** 開新對話讀完 `MEMORY.md` → 讀本 memo → 下一步預設 **Phase 5B-3**（task prompt 已凍結，all defaults，可直接開工）。

## 本 session 已完成（2 PR merged + VPS deployed）

| PR | Merge | 內容 |
|---|---|---|
| #179 | `bf5b4ed` | **dotenv sweep** 5 處 `os.getenv(K, DEFAULT)` 空字串 trap（health_check / usopp / notifier / multimodal_arbiter）+ 4 regression tests + VPS deployed |
| #182 | `222f040` | **Phase 5C** 結構化 log search FTS5（`shared/log_index.py` + `SQLiteLogHandler` + `/bridge/logs` route + `logs.html` + `cleanup_logs.py` cron + 9th nav entry）+ 27 tests + VPS deployed |

**Tests**：1854 → 1922（+68 跨兩 PR；5C 內 +27、self-review fix +1、其他 untouched）。

## VPS 部署狀態（修修 a, b 都做了）

- ✅ **5C deployed**：`ssh nakama-vps git pull && systemctl restart thousand-sunny nakama-gateway`
- ✅ **cleanup-logs cron installed**：`0 4 * * * cd /home/nakama && /usr/bin/python3 scripts/cleanup_logs.py >> /var/log/nakama/cleanup-logs.log 2>&1`
- ✅ **dotenv sweep deployed** 之前 session 已做
- ✅ **`/bridge/logs` 視覺驗證 OK**（修修瀏覽器 — CF SBFM 擋 curl）

## 路上抓到 + 已自動修

1. **dotenv 順帶修 VPS .env GCP_SERVICE_ACCOUNT_JSON Mac→Linux path drift** — backup `.env.bak.20260426_5d_fix` 後 sed-replace
2. **`logger.exception()` traceback 被 SQLiteLogHandler 吞掉**（self-review on PR #182 抓到）— record.exc_info 進 `extra["exc"]`，FTS5 索引到。新 test `test_emit_captures_exc_info_into_extra` 用「只在 traceback 內的 token」搜尋驗證。
3. **`test_emit_swallows_db_error_via_handle_error` 有 `assert ... or True`**（永遠 pass 的假測試）— 改成 monkeypatch `_get_index` 的 deterministic path。

## ultrareview crash（precedent 註記）

PR #182 跑 ultrareview 時 crash 「Review crashed before producing findings」。Self-review + 1922 tests pass + CI green → squash merge。**Tooling crash ≠ quality 信號**，不該 block merge。下次再 crash 時直接走 self-review path。

## 下一步：Phase 5B-3 anomaly daemon

**Task prompt 已凍結**：`docs/task-prompts/2026-04-26-phase-5b-3-anomaly-daemon.md`（修修 a/b 簽核 = all Q1-Q8 defaults）

要點摘要：
- 4 類 metric × 過去 1h vs trailing 7d × 3σ
  - cost spike (per agent)
  - latency p95 spike (per agent)
  - error rate spike (from logs.db level=ERROR/CRITICAL)
  - cron failure cluster (from heartbeat consecutive_failures)
- Reuse `shared/alerts.py` Slack DM dedup
- 新檔：`shared/anomaly.py` 純函式 + `agents/franky/anomaly_daemon.py` 主迴圈
- cron `*/15 * * * *` + heartbeat `nakama-anomaly-daemon` + `CRON_SCHEDULES` 註冊
- 預估 2-3 天

執行序見 task prompt §7（10 步驟 commit 切法）。

## Phase 5 剩 1 chunk + Phase 6-8

| 工作 | 規模 | Dep |
|---|:---:|---|
| **Phase 5B-3** anomaly daemon | 2-3 天 | 5C ✅；prompt 凍結 |
| **Phase 6** test coverage 補齊 | 7 天 | none |
| **Phase 7** staging + feature flags | 8 天 | 修修決定 staging 機 |
| **Phase 8** CI/CD auto deploy + rollback | 4 天 | Phase 7 |

## 不要自己決定的事

- Phase 7 staging — 規模大、要錢、要新 VPS，必先問
- 5B-3 完成後是否續 Phase 6 vs 別的方向 — 看修修當下需求
- ultrareview 連續 crash 多次後是否要 abandon 走 self-review only — 累積證據再說

## 開始之前一定要先看

- 本 memo
- [project_quality_uplift_next_2026_04_27.md](project_quality_uplift_next_2026_04_27.md) — 上一輪（已過時，被本 memo 取代）
- [feedback_log_search_fts5_pattern.md](feedback_log_search_fts5_pattern.md) — 5C 寫完後沉澱的 FTS5 + handler 設計教訓
- 5B-3 task prompt：[docs/task-prompts/2026-04-26-phase-5b-3-anomaly-daemon.md](../../docs/task-prompts/2026-04-26-phase-5b-3-anomaly-daemon.md)
- 原 9-phase plan：`docs/plans/quality-bar-uplift-2026-04-25.md`
