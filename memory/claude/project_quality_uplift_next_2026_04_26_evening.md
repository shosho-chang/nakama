---
name: Quality Uplift 下一輪起點（2026-04-26 晚清對話 後繼續）
description: 本 session +3 PR (Phase 4 doc / 5A latency / 5B-1 cron staleness)；下一步 5B-2 instrument 4 cron 或 5B-3 anomaly daemon
type: project
created: 2026-04-26
originSessionId: 74954c3c-fdc6-4064-931b-fe3250a7d804
---
2026-04-26 evening session：在 morning pickup（postmortem-process Phase 4）基礎上又 close 三個 PR。**取代過時的 `project_quality_uplift_next_2026_04_26.md`**。

**Why:** 修修連續說「你決定」+ auto mode → 一路自動 close 三個 PR。下次新對話從本 memo 接手。

**How to apply:** 開新對話讀完 `MEMORY.md` → 讀本 memo → 下一步預設 **Phase 5B-2**（除非修修指定別的）。先問「VPS pull + restart + 瀏覽器看 cron_freshness card」做了沒。

## 本 session 已完成（3 PR merged）

| PR | Merge | 內容 |
|---|---|---|
| #166 | `51bdd7a` | **Phase 4** postmortem-process runbook + incident-postmortem template + DR cross-link |
| #168 | `92717f2` | **Phase 5A** LLM latency p50/p95/p99：`api_calls.latency_ms` 欄位 + 3 LLM client instrument + `/bridge/cost` 加 LLM LATENCY 段 |
| #170 | `7387505` | **Phase 5B-1** cron staleness meta-probe：`probe_cron_freshness()` + CRON_SCHEDULES (3 entries) + `/bridge/franky` 加 cron_freshness card；reviewer 抓到 BLOCKER 6/9 false-green，trim 至已驗證 3 個 |

**Tests**：1772 → 1794（+22）。

## 路上抓到 + 已自動修復

- **04-26 04:00 backup pre-pull 舊版 silently failed**：`int(os.environ.get("NAKAMA_BACKUP_RETENTION_DAYS", "30"))` 撞空字串 `.env`。VPS code 已是 `or "30"` defensive pattern（修修 pull 後就修了），smoke 11:17 跑通。**這是 Phase 4 postmortem 的真實 dogfood case**，未寫 vault stub（修修 hit 到才填）。

## 待做

### Phase 5 剩 4 chunk

| 工作 | 規模 | Dep | 並行？ |
|---|:---:|---|---|
| **Phase 5B-2** instrument 4 cron 加 record_success | 1 天 | none | ✅ |
| **Phase 5B-3** anomaly daemon（cost / error rate / latency 3σ）| 2-3 天 | 5A latency 已有 | ❌ |
| **Phase 5C** FTS5 structured log search UI | 2 天 | none | ✅ |
| **Phase 5D** external probes GSC/Slack/Gmail | 1 天 | none | ✅ |

### Phase 6-8

| 工作 | 規模 | Dep |
|---|:---:|---|
| **Phase 6** test coverage 補齊（thousand_sunny SSE / robin router → 80%）| 7 天 | none |
| **Phase 7** staging + feature flags | 8 天 | 修修決定 staging 機 |
| **Phase 8** CI/CD auto deploy + rollback | 4 天 | Phase 7 |

### 修修 manual

1. **VPS pull + restart**（PR #168/#170 才會吃到效果）：
   ```bash
   ssh nakama-vps 'cd /home/nakama && git pull && sudo systemctl restart thousand-sunny nakama-gateway'
   ```
2. 瀏覽器驗證：
   - `/bridge/cost` 頁底「LLM LATENCY · 模型延遲分布」段（等幾筆 LLM call 觸發後才有資料）
   - `/bridge/franky` probe-row2 第 4 個出現「Cron · freshness」card（部署後 5 min 內 Franky cron tick 才會建立 state row）
3. Branch protection setup（pending 自上輪）
4. DR drill 半模擬（pending 自上輪）

## Phase 5B-2 細節（推薦下一步）

把 4 個 cron 加 `record_success`/`record_failure`，然後加進 `CRON_SCHEDULES`：

| Job | 入口 script | 預估 LOC |
|---|---|---|
| `robin-pubmed-digest` | `agents/robin/pubmed_digest.py` 或類似 | ~5-10 |
| `zoro-brainstorm-scout` | `agents/zoro/brainstorm/...` | ~5-10 |
| `franky-weekly-report` | `agents/franky/weekly_digest.py` | ~5-10 |
| `franky-r2-backup-verify` | `agents/franky/r2_backup_verify.py` | ~5-10 |

Skip：
- `franky-health-probe` — self-deadlock（probe 自己跑 probe，不能監測自己死）
- `external-uptime-probe` — GH Actions 沒法直接 record_success；需要 webhook 架構，defer

每個 instrument pattern：
```python
from shared.heartbeat import record_failure, record_success
_JOB_NAME = "..."

def main() -> int:
    try:
        # ... 原邏輯
        record_success(_JOB_NAME)
        return 0
    except Exception as exc:
        record_failure(_JOB_NAME, str(exc)[:200])
        raise
```

加進 CRON_SCHEDULES 時 grace 慎選（原 reviewer 指出 5+5min 太緊）。

## 推薦下一步序

**5B-2**（unblock 5B-1 promise）→ **5C** 或 **5D**（並行可，看修修選哪個）→ **5B-3** anomaly daemon → **Phase 6 test coverage**。

## 不要自己決定的事

- Phase 7 staging — 規模大、要錢、要新 VPS，必先問
- 順序 Phase 5 → 6 → 7 → 8 是不是真的照走（vs. 先做 Phase 6 test）
- 5B-2 instrument 是否要連帶把 vault `Incidents/2026/04/incident-2026-04-26-r2-mirror-fail.md` 寫掉（dogfood Phase 4 template）

## 開始之前一定要先看

- 本 memo
- [project_quality_uplift_next_2026_04_26.md](project_quality_uplift_next_2026_04_26.md) — 上一輪 morning pickup（已過時，被本 memo 取代）
- [feedback_probe_registry_verify_producer.md](feedback_probe_registry_verify_producer.md) — 5B-1 reviewer 抓到的 BLOCKER 教訓
- [feedback_alert_dedup_window_per_interval.md](feedback_alert_dedup_window_per_interval.md) — alert dedup window 必對齊 expected interval
- 原 9-phase plan：`docs/plans/quality-bar-uplift-2026-04-25.md`
- Phase 4 runbook：`docs/runbooks/postmortem-process.md`
