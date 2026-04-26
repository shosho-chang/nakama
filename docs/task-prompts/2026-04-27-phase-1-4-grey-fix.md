# Phase 1 DR drill 實證 + Phase 4 alert→Incident archive 自動化

**Framework:** P9 六要素（CLAUDE.md §工作方法論）
**Status:** 草稿，**待修修凍結 Q1-Q5 後動手**
**Source plan:** [`docs/plans/quality-bar-uplift-2026-04-25.md`](../plans/quality-bar-uplift-2026-04-25.md) §Phase 1 / §Phase 4
**Pickup memo:** [`memory/claude/project_quality_uplift_next_2026_04_28.md`](../../memory/claude/project_quality_uplift_next_2026_04_28.md)

---

## §1. 目標（一句話）

把 Phase 1 / Phase 4 從「文件齊但 A bar 未達」拉到全綠：DR drill 真演練一次量到 RTO、alert 觸發自動寫進 vault `Incidents/`、Franky 月度 digest 加 incident roundup。

## §2. 範圍（精確檔案路徑）

### Phase 1 drill 實證

| 檔案 | 動作 | 理由 |
|---|---|---|
| `Incidents/2026-04-XX-dr-drill-outcome.md` (vault) | **新建** | 演練紀錄：時間軸、wall-clock、問題、改進。本檔本身就是 deliverable |
| `docs/runbooks/disaster-recovery.md` §1 | **改** | RTO 4h 估值用 drill 量到的真實 wall-clock 取代 |
| `docs/runbooks/disaster-recovery.md` §6 drill 步驟 | **改** | drill 過程踩到的坑回灌（runbook 自我強化） |

### Phase 4 alert → Incident 自動歸檔

| 檔案 | 動作 | 理由 |
|---|---|---|
| `shared/incident_archive.py` | **新建** | alert.severity=error 觸發 → 寫 `Incidents/{date}-{rule_id}.md` template stub |
| `shared/alerts.py` | **改** | `alert("error", ...)` Slack DM 路徑後加 archive call（dedup 後做，不重複建 stub） |
| `agents/franky/alert_router.py` | **改**（可能） | AlertV1 path 同樣加 archive hook |
| `tests/shared/test_incident_archive.py` | **新建** | 6+ tests 覆蓋 archive 觸發 / dedup 不重複建檔 / vault path / template fields |
| `tests/shared/test_alerts.py` | **改** | 既有 alert 測試補一條 archive 確實寫入 |

### Phase 4 Franky 月度 incident roundup

| 檔案 | 動作 | 理由 |
|---|---|---|
| `agents/franky/weekly_digest.py` | **改** | 既有 Mon 10:00 digest 加 §incident roundup（過去 30d Incidents/ 統計 + open action items） |
| `tests/agents/franky/test_weekly_digest.py` | **改** | 補測 incident section render |

預估 line delta：~400 added / ~50 modified。

## §3. 輸入（依賴）

- ✅ `Incidents/` vault 目錄 schema 已凍（PR #166 postmortem-process.md）
- ✅ `shared/alerts.py` `alert()` API 既有契約（不改 signature）
- ✅ `agents/franky/weekly_digest.py` 既有 5-section template
- ✅ vault path 已從 LifeOS 環境變數可讀（既有 agent 寫過 vault；參考 `agents/nami/vault_writer.py` 或類似）
- ⚠️ `Incidents/` 目錄目前**不存在於 vault** — drill outcome 是第一份 incident，要先 mkdir
- ⚠️ DR drill 需要決定演練模式（Q1）

## §4. 輸出（交付物）

### 4.1 DR drill outcome 文件

`Incidents/2026-04-XX-dr-drill-outcome.md` 結構：
- header（severity SEV-1 演練 / drill date / participants 修修）
- timeline（每 step 加 wall-clock）
- detect / mitigate / restore steps（哪幾個 runbook 段沿用、哪幾段失靈）
- root cause section: N/A（這是 drill 不是真實 incident）
- 量到的 RTO + RPO + 跟目標 4h / 24h 的差距
- action items（runbook 缺哪些 step、哪些 secret 沒備齊、哪些 dep 沒裝）

### 4.2 `shared/incident_archive.py`

```python
def archive_incident(
    *,
    rule_id: str,
    severity: str,
    title: str,
    message: str,
    fired_at: datetime,
    context: dict,
) -> Path | None:
    """Write `Incidents/{date}-{rule_id}.md` stub (or skip if exists for today).
    
    Returns the written path, or None if dedup'd / vault unavailable.
    """
```

Stub template fields（mirror postmortem-process §3 incident stub schema）：
- frontmatter: `severity`, `rule_id`, `fired_at`, `dedup_key`, `status: open`
- §detect / §mitigate / §root cause / §action items（空 placeholder）
- 自動填：alert title + message + context dict

Dedup：同一個 (date, rule_id) 一天只建一份。多次同 rule fire 在同一份 stub 內 append `## Repeat fires` log（不重新覆寫主要 narrative）。

### 4.3 Franky 月度 incident roundup

加在 weekly_digest 第 6 section（5 section 在前已有：VPS / cron / backup / alerts / cost）：

```
§6 Incidents (last 30d)
- Total: 4 SEV-1, 2 SEV-2
- Open action items: 3 (見 Incidents/2026-04-XX-foo.md §action-items)
- Top recurring: cron_staleness × 7 (機率 dedup window 沒到 1 週？)
```

統計 source：scan vault `Incidents/*.md` frontmatter `severity` + `status` + `fired_at`。

## §5. 驗收（Definition of Done）

| # | 條件 | 驗證 |
|:---:|---|---|
| 1 | DR drill 真做完，wall-clock 量到，outcome 文件寫了 | `Incidents/2026-04-XX-dr-drill-outcome.md` 存在 + RTO 數字 |
| 2 | `disaster-recovery.md` §1 RTO 估值用 drill 真實量值更新 | runbook diff |
| 3 | 觸發 `alert("error", "test", "...")` 後 vault `Incidents/2026-04-XX-test.md` 出現 | manual + unit test |
| 4 | 同一 rule 同一天第二次 fire 不重覆建 stub | unit test dedup case |
| 5 | Franky digest 跑出來有 §6 Incidents section（即使是 0 incidents 也 render「last 30d clean」） | manual + unit test |
| 6 | full suite pytest 0 fail | CI |
| 7 | VPS deploy 後 alert path 確實寫 vault（手動觸發 alert 或等真 alert） | ssh + ls Incidents/ |

## §6. 邊界（明確不能碰）

**不可碰**：
- ❌ `shared/alerts.py` `alert()` signature 不改（既有 callers 多）
- ❌ `Incidents/` schema（PR #166 已凍）— 只能補 frontmatter field，不重寫 sections
- ❌ Bridge UI 不加 incident timeline 頁面（Phase 4 plan 寫了「alert→Bridge incident timeline 自動歸檔」— 這輪只做檔案寫入，UI 留下一輪）
- ❌ 不改 backup / cron / heartbeat 機制
- ❌ 不重命名 vault 路徑（用既有約定）

**可選 / 留下次**：
- Bridge UI `/bridge/incidents` 頁面 — Phase 4+1 候選（grey 洗綠後做）
- 自動指派 action item 給 Franky monthly review — 太提前
- Incident severity 分級 algorithm（SEV-1/2/3 自動分） — 第一輪人工標即可

## §7. 推薦執行序

### Day 1（半天）— DR drill 實證

1. 開 VPS sandbox（**Q1 決定哪種模式** — 看選項）
2. 照 `disaster-recovery.md` §6 drill steps 跑：state.db restore from R2 → smoke check
3. 計時每一步、記下哪些 step runbook 寫得不清楚
4. 寫 `Incidents/2026-04-XX-dr-drill-outcome.md`（template 沿用 postmortem-process §incident schema）
5. 回灌 runbook（fix 不清楚的步驟、更新 RTO 估值）
6. commit `feat(runbooks): DR drill 1st outcome + RTO calibration` 

### Day 2（1 天）— Incident archive 自動化

1. `shared/incident_archive.py` `archive_incident()` 函式 + 6+ tests
2. `shared/alerts.py` 在 dedup 確認非抑制後 call archive_incident（同處 _record_fired 之後）
3. `agents/franky/alert_router.py` AlertV1 path 同樣 hook
4. Manual smoke：跑 `python -c "from shared.alerts import alert; alert('error', 'test', 'test fire', dedupe_key='test1')"`，看 vault 出檔
5. `agents/franky/weekly_digest.py` §6 Incidents section + 統計函式
6. commit `feat(incidents): alert → vault Incidents/ archive + Franky monthly roundup`

### Day 3（半天）— PR + ultrareview + VPS

7. Ruff + 全 suite + 開 PR
8. Ultrareview（Q5 default = 跑）
9. Squash merge + VPS pull + smoke
10. 收尾 pickup memo（5/9 + 2/9 grey → 7/9 全綠 + 2/9 未開始）

## §8. 風險

- **DR drill 實際操作有風險**：如果用既有 VPS sandbox 路徑做 restore，操作不慎可能影響 production state.db。要在獨立目錄做、用 `NAKAMA_DATA_DIR` env override 隔離。Q1 待決。
- **Vault 寫入路徑跨平台**：Mac shosho LifeOS 是 `/Users/shosho/Documents/Shosho LifeOS/`，VPS 沒有 vault（agents/nami/vault_writer 等只在 Mac 跑）。Incident archive **要在 VPS 端跑**（alert 觸發點是 VPS cron），所以 archive 寫入路徑要走 vault sync 機制 — 或寫進 repo `data/incidents-pending/` 等 Mac sync 過去後再 move 進 vault。**Q2 待決**。
- **Vault `Journals/` 禁寫規則**：CLAUDE.md 寫「`Journals/` 完全禁止寫入」— `Incidents/` 不在 Journals/ 下，但要確認 LifeOS vault CLAUDE.md 沒有對 Incidents/ 設限。
- **既有 alert 量未知**：alert path 加 archive 後，是否會每天炸出 N 個 Incidents/ 檔？看現役 dedup 60min 應該還好（最多 24/24*N rules）— 但要看 rate。Q3 待決：要不要只 archive severity=critical 的，warn 不archive。
- **Drill 演練若失敗**：drill 跑到一半發現 runbook 漏 step / 某個 secret 沒備到，drill 可能要中止+補後再來。outcome 文件要老實記錄這種情況。

## §9. 待決定（凍結前 user 拍板）

| # | 題目 | 預設 | 替代 |
|:---:|---|---|---|
| Q1 | DR drill 實際模式 | **VPS sandbox 路徑隔離（用 `NAKAMA_DATA_DIR=/tmp/dr_drill/` restore，不影響 prod）** | A. 開新 Vultr VPS 真做（要錢，~$10）／ B. Docker container 模擬（不真實，省事） |
| Q2 | Incident stub 寫入路徑 | **repo `data/incidents-pending/`，Mac 端 vault sync hook 把它 move 進 vault `Incidents/`** | A. 直接寫 vault（VPS 沒 vault → 失靈）／ B. R2 bucket（過頭）／ C. 不 archive 只發 Slack（退回現狀） |
| Q3 | 哪些 alert 進 archive | **severity=error 全部 archive**（Phase 4 A bar 要求 incident 制度化） | A. 只 critical／ B. error + warn 都進 |
| Q4 | Franky monthly roundup 時點 | **沿用 weekly digest（每週一 10:00）擴 §6**（reuse infra） | A. 獨立 monthly cron 第 1 號 10:00／ B. 每天 brief（noise） |
| Q5 | ultrareview | **是**（alert path + vault write 是高 leverage、改 production alert flow） | 跳過 |

修修簽核 = 全 default 即可動手；不夠決定就在 chat 問。

## §10. 完工後 follow-up

- 更新 pickup memo（grey 洗綠 → 7/9 完成；下一個 chunk = Phase 6 test coverage 補齊）
- Phase 6 task prompt 凍結（task prompt §6 plan 寫的 4 個 deliverable：thousand_sunny SSE / agent E2E golden / FSM property test / schema round-trip）
- 1-2 週後看 alert → Incidents/ 出檔頻率，回頭看 Q3 是否要降到 critical-only
