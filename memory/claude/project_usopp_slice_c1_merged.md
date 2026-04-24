---
name: Usopp Slice C1 merged — daemon loop + signal + follow-ups
description: PR #97 2026-04-24 squash merged；daemon + 3 件 code-review follow-up 一併進 main；剩 Slice C2 staging + VPS 部署
type: project
tags: [usopp, phase-1, pr-97, slice-c1, publisher, daemon]
---
## 現況（2026-04-24 session end）

PR #97 `feature/usopp-slice-c1` squash merged at `05d35a4` on main（2 commits squashed → 1）。剩下：Slice C2（staging E2E + LiteSpeed Day 1）和 VPS 部署（systemd unit，修修手動）。

## 產出清單

| 項目 | 檔案 | 備註 |
|---|---|---|
| daemon loop | `agents/usopp/__main__.py` UsoppDaemon class | poll `approval_queue` → `Publisher.publish()`，batch/poll env-controlled |
| signal handling | 同上 | SIGTERM/SIGINT → `_shutdown` flag，`_sleep_interruptible` 1s 粒度 |
| action 推論 | 同上 `_dispatch` | `scheduled_at is not None → "schedule"`，否則 `"publish"` |
| reviewer lookup | 同上 `_lookup_reviewer` | 讀 `approval_queue.reviewer`（`approve()` 寫入），NULL fallback `"unknown"` |
| update_post fail-closed | 同上 | Phase 1 scope 外，直接 `mark_failed` 不進 publisher |
| unexpected crash 兜底 | 同上 `_dispatch` | `except Exception` + `mark_failed(increment_retry=True)`，daemon 不死 |
| site_id 公開化 (C6) | `shared/wordpress_client.py` | `_site_id` → `.site_id`，2 call sites（publisher.py:536 + test_publisher.py:153）跟著改 |
| env knobs 文檔 | `.env.example` | `USOPP_TARGET_SITE` / `_WORKER_ID` / `_POLL_INTERVAL_S` / `_BATCH_SIZE` 四行 |
| load_config() fix | `agents/usopp/__main__.py` `main()` | Franky PR #87 同類 bug（`feedback_explicit_load_dotenv_for_non_db_paths.md`） |
| op_id in warning log | 同上 `_lookup_reviewer` | observability.md §2；`reset_stale_claims()` claimed→approved 不 re-set reviewer，這條路徑會 fire |
| capability card | `docs/capabilities/wordpress-publisher.md` | Roadmap 拆 Slice C1 + C2，daemon 段、env 表、不做事項補齊 |
| README | `agents/usopp/README.md` | Phase 1 Slice C1 狀態 + env 表 + ADR-005b line 417 superseded 註解 |
| tests | `tests/agents/usopp/test_daemon.py` | 17 tests：run_once / reviewer lookup / update_post skip / unexpected crash survival / signal shutdown / env factory / op_id in log |

## 兩次 commit（squashed 為 `05d35a4`）

| SHA | 內容 |
|---|---|
| `5b70213` | 初版 Slice C1：daemon + C6 rename + capability card + README + 16 tests |
| `81c8604` | code-review follow-up：load_config + .env.example + op_id（+ handoff doc） |

## Code-review 結果（5 Sonnet + 6 Haiku scorer）

| 候選 | Score | 判定 |
|---|---|---|
| load_config() missing in main() | **100** | 必修 — Franky PR #87 同款 bug |
| USOPP_* env vars 缺 `.env.example` | 75 | 未達 80 門檻但修修裁量一起修 |
| `_lookup_reviewer` warning 缺 operation_id | 75 | 同上 |
| naive `scheduled_at` 傳入 crash | 0 | Pydantic `AwareDatetime` 在 `ApprovalPayloadV1Adapter.validate_python()` 早擋掉 |
| isinstance dict vs Pydantic payload | 0 | `claim_approved_drafts` 已 deserialize 成 Pydantic union member |
| Test `_get_conn()` identity 風險 | 0 | state.py module-level singleton |

## ADR 偏離

**ADR-005b line 417「`/healthz` 加 WP 連線檢查」→ superseded by ADR-007 §4 `probe_wp_site`**

理由：/healthz ADR-007 §3 SLO 是 p95 < 200ms + no DB/LLM/Slack（給 UptimeRobot 外部 probe）；WP 往返可能 >200ms，不該擋 hot path。Franky 5-min cron 的 `probe_wp_site` 直接打 `wp_shosho` + `wp_fleet`，out-of-band 架構更正確，已 cover 原始意圖。PR 描述 + capability card + README 都有標註 supersedence。

## 相關記憶

- [project_usopp_slice_a_merged.md](project_usopp_slice_a_merged.md) — PR #73 上游
- [project_usopp_slice_b_pr77.md](project_usopp_slice_b_pr77.md) — PR #77 上游（C6 borderline 就是這裡留的）
- [feedback_explicit_load_dotenv_for_non_db_paths.md](feedback_explicit_load_dotenv_for_non_db_paths.md) — load_config() fix 正典
- [feedback_env_push_diff_before_overwrite.md](feedback_env_push_diff_before_overwrite.md) — 下次 `.env` push 到 VPS 別整份覆蓋
- [project_pending_tasks.md](project_pending_tasks.md) — Slice C2 + VPS 部署仍 pending

## 下一步（記給自己）

1. **Slice C2**（獨立 PR）：
   - Docker WP 6.9.4 + SEOPress 9.4.1 staging（fixtures 資料夾 `tests/fixtures/wp_staging/` 已在）
   - `tests/e2e/test_phase1_publish_flow.py`（`@pytest.mark.live_wp`）
   - LiteSpeed Day 1 實測 → `docs/runbooks/litespeed-purge.md` 定稿
   - `LITESPEED_PURGE_METHOD` 從 `noop` 改為實測決定的 method
2. **VPS 部署**：
   - 建 `nakama-usopp.service` systemd unit（EnvironmentFile=/home/nakama/.env）
   - `.env` push：只 append 新 `USOPP_*` keys，別覆蓋（`feedback_env_push_diff_before_overwrite.md`）
   - 啟動 + `journalctl -u nakama-usopp -f` 觀察幾個 claim 週期
