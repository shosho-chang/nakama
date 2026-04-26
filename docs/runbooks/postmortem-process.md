# Postmortem Process — Nakama incident response 流程

**Scope:** Nakama 服務 incident 的 detect → mitigate → record → postmortem → retro 完整流程定義。
**Owner:** 修修為主；postmortem 初稿可 dispatch `general-purpose` agent 協助撰寫。
**Cadence:** Incident 隨時觸發；半年 retro 1 月 / 7 月各跑一次。
**對齊:** Phase 3 alert API（`shared/alerts.py`）— `alert("error", ...)` 是 incident 的 canonical trigger surface。

---

## 1. 什麼算 incident（severity tier）

| 級別 | 定義 | 範例 | Postmortem? |
|------|------|------|------|
| **SEV-1** | 服務全停 / 資料損毀 / secret compromise | thousand-sunny 503、`state.db` corrupt、API key 外洩 | ✅ 7 天內 full |
| **SEV-2** | 單一 agent 流程斷 / 用戶可見功能壞 | Robin `/research` 全卡、Usopp 連續 publish fail、Bridge 某頁 500 | ✅ 7 天內 full |
| **SEV-3** | Cron / 背景 job 連續失敗 ≥ 3 次（silent failure） | backup mirror 連 fail、Franky probe 紅 chip 持續、heartbeat stale > 6h | ✅ 7 天內 lightweight 可（Timeline + Root cause + Action items） |
| **SEV-4** | 一次性、低影響、可自我恢復 | 單筆 LLM call timeout 自 retry、單篇 article 排版怪、一次 transient 503 | ❌ 走 `feedback_*.md` 教訓 memory |

**判定原則**：

- 用戶可見（修修 hit 到「壞了」）→ 至少 SEV-2
- Silent（Franky 抓到 / cron 沒跑才知道）→ SEV-3
- 一次性 + 無 pattern + 已自癒 → SEV-4
- 不確定 → 從嚴從寬以高一級登記，事後再降

---

## 2. Trigger 來源

Incident clock 從以下任一 signal 出現開始計時：

| 來源 | 觸發點 | 對應 SEV 起點 |
|------|--------|-------------|
| `shared.alerts.alert("error", ...)` | 任何 error 級 alert（Slack DM `:rotating_light:`） | SEV-3 起跳 |
| Bridge `/bridge/health` 紅 chip | heartbeat stale 或 `last_error` 不為空 | SEV-3 起跳 |
| Bridge `/bridge/franky` probe red | 任一 probe 連續 fail | SEV-3 起跳 |
| 修修 手動發現 | UI 卡 / agent 沒回 / 文章爆 | 依嚴重度判 |
| GH 訪客 / contributor 回報（未來） | issue / DM | 依嚴重度判 |

當任何上述出現，啟動 §3 流程。

---

## 3. Incident response 流程

### 3.1 Detect（0–15 分鐘）

1. 看 alert / chip / Slack DM 內容
2. 判定 SEV（§1 表）
3. 判定真假 incident — 看 dedupe `fire_count`、`last_fired_at`、近期是否同 dedupe_key 已 ack
4. 若是 false positive：寫一行進 `memory/claude/feedback_*.md` 標 alert noise，調 threshold/dedupe，**不算 incident**

### 3.2 Mitigate

| SEV | 第一動作 | 工具 |
|-----|---------|------|
| SEV-1 | 放下手邊事 → restore service | [`disaster-recovery.md`](disaster-recovery.md) |
| SEV-2 | hotfix / revert 上一個 deploy | `git revert` + VPS pull + restart |
| SEV-3 | ack（在 Slack 回 emoji 或 DM 自己一段標記）→ 正常時間修 | — |
| SEV-4 | 直接修 / 觀察是否復發 | — |

**SEV-1 / SEV-2 mitigation 完成的定義**：服務恢復 + 修修能正常用。不是「root cause 找到」。

### 3.3 Stub incident page（24h 內，SEV-1/2/3）

在 vault 開 stub：

```
Incidents/YYYY/MM/incident-YYYY-MM-DD-{slug}.md
```

例：`Incidents/2026/04/incident-2026-04-26-r2-mirror-fail.md`

用 [`docs/templates/incident-postmortem.md`](../templates/incident-postmortem.md) 為起點，先填：

- frontmatter 的 `id` / `title` / `severity` / `detected_at` / `trigger`
- `## Summary` 一段話
- `## Timeline` 把目前已知的 alert / mitigation 時間點先記下

剩下 sections（Root cause、Action items、Lessons learned）等 §3.4 一起補。

> **首次使用前**：`mkdir -p "{vault}/Incidents/2026/04"` 並（若用 Templater）把 template 複製到 `Templates/tpl-incident.md`。否則手動 copy `docs/templates/incident-postmortem.md` 內容貼新檔。

### 3.4 Postmortem（7 天內，SEV-1/2/3）

按 template 把所有 sections 填完。SEV-3 可走 lightweight：

| Section | SEV-1/2 | SEV-3 |
|---------|:------:|:-----:|
| Summary | ✅ | ✅ |
| Timeline | ✅ | ✅ |
| Detection | ✅ | optional |
| Mitigation | ✅ | ✅ |
| Root cause（5-why）| ✅ | ✅ 至少一層 why |
| Action items | ✅ | ✅ |
| Lessons learned | ✅ | optional |

寫不出來時，dispatch agent：

```
Agent({
  description: "Draft incident postmortem",
  subagent_type: "general-purpose",
  prompt: "Read Incidents/.../incident-XXX.md (Summary + Timeline filled). Pull alert/journalctl context: SQL query alert_state WHERE dedup_key=..., journalctl -u thousand-sunny --since=...". Fill Root cause (5-why), Action items, Lessons learned. Blameless tone. Under 400 words."
})
```

### 3.5 Action items 收尾

| 類型 | 落點 |
|------|------|
| 工程 fix | GH issue 或直接 PR；issue link 寫回 postmortem |
| 流程 / 教訓 | `memory/claude/feedback_*.md`（依現有命名規則）|
| Runbook 缺漏 | 補進對應 runbook，PR 連結回 postmortem |
| Detection 不足 | 開 GH issue tag `observability` |
| 新 alert / probe | Phase 5 backlog 連結 |

**每個 action item 必有 owner + due date**（template 內表格已強制）。

### 3.6 Close

所有 action items 進到 `done` 或 `cancelled` → frontmatter `status: closed`。

---

## 4. Postmortem 寫作標準

- **Blameless** — 聚焦系統 gap，不寫「修修忘記...」「Claude 漏掉...」。寫「pipeline 缺 X 檢查 → 導致 Y」
- **5-why** — root cause 至少問三層 why；停在「人為失誤」是 anti-pattern
- **Timeline 真實時間** — 從 `alert_state` table、`journalctl`、`agent_runs`、Slack message timestamp 抓，**不要憑印象**
- **Action items SMART** — Specific / Measurable / Assignable / Realistic / Time-bound
- **Single source of truth** — 一個 incident 一份 postmortem。多 incident 互相引用即可，不複製貼

複查：寫完 dispatch `general-purpose` agent「review this postmortem for: blameless tone, 5-why depth, action item SMART-ness」。

---

## 5. 半年 retro

每年 **1 月** 與 **7 月** 各跑一次 incident retro，輸出到 `Case Studies/YYYY-Hx-incident-retro.md`：

```
Case Studies/2026-H1-incident-retro.md
```

內容：

| 段落 | 來源 |
|------|------|
| SEV breakdown（SEV-1 X 件 / SEV-2 Y 件 / ...） | `Incidents/` aggregate |
| 平均 time-to-detect / time-to-mitigate / time-to-resolve | postmortem frontmatter `detected_at` / `mitigated_at` / `resolved_at` |
| Action item 完成率（done / total） | 每份 postmortem `## Action items` table |
| Top 3 recurring root cause patterns | grep / agent aggregate root cause 段 |
| 對應 quality bar 改進建議（是否要新 phase）| 連結回 `docs/plans/quality-bar-uplift-2026-04-25.md` |

執行：

```
Agent({
  description: "H1 incident retro",
  subagent_type: "general-purpose",
  prompt: "Read all files in vault Incidents/2026/01-06/. Aggregate per-section in Case Studies/2026-H1-incident-retro.md. Keep blameless. Flag any recurring root cause pattern (≥2 incidents same root cause) explicitly."
})
```

---

## 6. 與 Phase 3 alert API 對齊

`shared/alerts.py` 是 incident trigger 的 single source of truth：

```python
from shared.alerts import alert
alert("error", "backup", "R2 mirror failed", dedupe_key="backup-mirror-fail")
```

**約定**：

1. 每個 `alert("error", ...)` call site 都對應一個潛在 SEV-3+ incident
2. `dedupe_key` 命名要可辨識來源（前綴用 category，例：`backup-mirror-fail`、`publish-wp-401`、`agent-robin-stuck`）
3. Postmortem frontmatter `trigger:` 欄填寫該 dedupe_key（或 `manual` / `franky-probe-{name}` / `bridge-health-{job}`）
4. `alert_state` table（schema 見 ADR-007 §4）已記錄 `last_fired_at`、`fire_count`、`last_message`、`suppress_until` — 寫 postmortem Timeline 時直接 query：

```sql
SELECT last_fired_at, fire_count, last_message
FROM alert_state
WHERE dedup_key = 'backup-mirror-fail'
ORDER BY last_fired_at DESC;
```

---

## 7. Future enhancements（不在本次 PR 範圍）

| # | Item | 規模 | 何時做 |
|---|------|------|------|
| 1 | Franky 月報加 incident roundup（extend `agents/franky/weekly_digest.py` `AlertSummary`，加 SEV-aggregated 段）| 0.5 天 | Phase 5 順便 |
| 2 | Bridge `/bridge/incidents` 頁列出 vault 所有 stub + open action item count | 1.5 天 | Phase 5 / Phase 9 polish |
| 3 | 自動 stub：`shared/alerts.py` error fire 時 INSERT 一筆到新 `incidents` table，Bridge UI 一鍵 export 成 vault stub markdown | 2 天 | Phase 5 |
| 4 | Action item tracker：feedback memory + GH issue 兩面 sync（`scripts/sync_incident_actions.py`）| 1 天 | 累積 ≥ 5 incidents 後才值得 |
| 5 | `Incidents/` 目錄 frontmatter dataview query（Obsidian dashboard 顯示 open incidents） | 0.5 天（vault 端 snippet） | 隨時可做 |

開 ticket 時請 link 回本 runbook §7 編號。

---

## 8. Quick reference

| 場景 | 第一動作 |
|------|---------|
| Slack `:rotating_light:` error alert | §3.1 判 SEV → §3.2 mitigate → §3.3 stub（24h） |
| Bridge `/bridge/health` 紅 chip | 看 `last_error` + `journalctl -u {service}` → 判 SEV → 同上 |
| Franky probe red | `/bridge/franky` 看 probe row → 判 SEV → 同上 |
| 修修 直接 hit「壞了」 | 直接判 SEV → §3 |
| 半年到了（1 月 / 7 月）| §5 retro |
| 寫 postmortem 卡住 | §3.4 dispatch agent |

---

## 9. 相關文件

- [`disaster-recovery.md`](disaster-recovery.md) — SEV-1（VPS / state.db）的 restore playbook
- [`secret-rotation.md`](secret-rotation.md) — Secret compromise 的 emergency rotation
- [`branch-protection-setup.md`](branch-protection-setup.md) — incident-driven hotfix 的 PR / merge 規範
- [`docs/templates/incident-postmortem.md`](../templates/incident-postmortem.md) — 本流程 §3.3 的起點 template
- [`docs/plans/quality-bar-uplift-2026-04-25.md`](../plans/quality-bar-uplift-2026-04-25.md) §Phase 4 — 本 runbook 對應的計劃條目
- `shared/alerts.py` — Phase 3 alert API（trigger surface）
- ADR-007 §4 / §10 — `alert_state` schema + Franky weekly digest 結構
