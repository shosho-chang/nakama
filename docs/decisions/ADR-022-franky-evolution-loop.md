# ADR-022: Franky Evolution Loop V1 — 從 monitor 擴張到 evolution watch

**Status:** Accepted
**Date:** 2026-05-07
**Deciders:** shosho-chang, Claude Opus 4.7, Codex GPT-5.5（panel auditor）
**Related:** ADR-001（本 ADR amend Franky row）、ADR-006、ADR-007、ADR-020（in-flight，dependency 鎖見 §Consequences）
**Provenance:**
- Spec v1：`docs/research/2026-05-07-franky-evolve-v1-spec.md`（grill-with-docs 5 fork 共識）
- Codex audit：`docs/research/2026-05-07-codex-franky-evolve-audit.md`（API mode / gpt-5.5 / reasoning_effort=medium）
- Panel integration matrix：`docs/research/2026-05-07-franky-evolve-panel-integration.md`（2-way Claude + Codex；Gemini skip — `GEMINI_API_KEY` 未設）

---

## Context

Franky 既有 AI news digest（`agents/franky/news_digest.py`，cron 06:30 台北）止於「digest 寫完、Slack 推給修修」。沒有任何結構性 bridge 把：

1. 「這條新聞看起來有用」 → 「它對 Nakama 哪個 agent / pain point 適用」
2. 「這個工具值得試」 → 「該開 issue 進 triage」
3. 「我們試了某個建議」 → 「有沒有真的變強」

進化目標雙焦點（修修明確指定）：

1. **Nakama 專案** — agent 設計、prompt 技巧、新 model 採用、agent framework
2. **Claude Code 開發工作流** — hooks、skills、sandcastle、MCP server、subagent pattern

現有 12 個 source（8 blog RSS + 4 GitHub releases + Anthropic HTML scrape）全是 generic AI news，**無針對這兩個目標的場景化 source**。

Panel critique surfaced 兩件 v1 spec 自己沒看到的 architectural pattern：

- **ADR-001 Franky 職責是 System Maintenance**，沒授權 evolution watch — 任何擴張要正式 amend ADR-001 row（不偷渡）
- **ADR-020 RAG infra 還沒 merge 進 main**（在 `docs/kb-stub-crisis-memory` 分支，5/7 仍在 Stage 1.5），spec v1 引用為 production dependency 是錯的

修修 + Claude 經 push-back 討論後（panel integration matrix §3 + 修修工作量論點）達成：

- **不另立新 agent**。Franky 漫畫設定即「研究 + 升級船」一體；workload 評估後加 +1 weekly LLM call + 1 monthly call 不會過載；他是 reporter 不是 decision-maker，戰略決策仍由修修 + Claude 拍板
- **但** ADR-001 row 必須正式 amend（透明性，不偷渡）

---

## Decision

### 0. ADR-001 amendment（必做，先於 implementation）

`docs/decisions/ADR-001-agent-role-assignments.md` 第 29 行的 Franky row 更新如下：

| Agent | 舊職責（ADR-001 v1） | 新職責（本 ADR amend）|
|-------|--------|--------|
| Franky | System Maintenance：套件更新、CVE 掃描、API key 驗證、系統健康檢查 | **System Maintenance + Evolution Watch**：（既有）套件更新、CVE 掃描、API key 驗證、系統健康檢查；（新增）AI 工具情報蒐集、跨期 synthesis、proposal 草擬 — **Franky 是 analyst 不是 decision-maker，戰略拍板仍由修修 + Claude 主線負責** |

ADR-001 在本 ADR commit 同步更新該 row，並在 row 下方註記 `Amended by ADR-022 (2026-05-07)`。

### 1. Pipeline（三層 cron）

```
[Daily 06:30 台北 — 既有 + 升級]
  Sources：12 既有 + 3 新場景化（見 §2 Source）
  Curate prompt：5-8 → 8-12 picks（仍要修 prompt + tests）
  Score prompt：4 → 5 dim（加 Relevance；初期 1-week shadow mode 不直接 freeze）
  Pick gate（shadow 期）：overall ≥ 3.5 AND signal ≥ 3 AND relevance ≥ 2（寬）
  Pick gate（freeze 後）：relevance ≥ 3
  Output：KB/Wiki/Digests/AI/YYYY-MM-DD.md + Slack DM

[Weekly 週日 22:00 — 新增 module agents/franky/news_synthesis.py]
  Input：過去 7 天 picked items + 內部 context（見 §3 Internal context）
  Pattern detection：(a) 多家做同件事 (b) 某 ADR 假設可能被推翻 (c) backlog issue 跟新聞高匹配
  Output：0-3 條 candidate（不是 1-3，硬下限為 0）
  Hard quality gate before issue creation：
    • ≥2 independent items support same trend
    • OR 1 item 直接 map 到 named open issue / ADR assumption
  Stage 1：所有 candidate 寫進 KB/Wiki/Digests/AI/Weekly-YYYY-WW.md（含 machine-readable frontmatter）
  Stage 2：只有 owner promote (vault 頁 frontmatter `promote: true`) OR ≥2-item rule 命中才 gh issue create
  panel_recommended tag：deterministic positive list（見 §5）+ LLM rationale 補充

[Monthly 月底最後一個週日 — 新增 module agents/franky/news_retrospective.py]
  Input：上月所有 franky-proposal issues + 對應 PR / commit / metric
  Output：KB/Wiki/Digests/AI/Retrospective-YYYY-MM.md + Slack DM 摘要
  retrospective 對 human_judged metric 報 owner verdict 不是 fake number
```

**Module 命名強制分離**：`weekly_digest.py` 是 ADR-007 §10 的 engineering 純模板週報，**不可** overload。新增工作走 `news_synthesis.py` + `news_retrospective.py`（複用 `news_*` 前綴對齊 `news_digest.py`）。

### 2. Source 擴張 — trust tier 分級

新加 3 條場景化 source，**分 trust tier**（不全列同層）：

| Trust tier | Source | 處理 |
|---|---|---|
| **Full trust**（既有 + 新加 anthropic）| 既有 12 source + `anthropic/anthropic-cookbook` releases atom + `anthropic/claude-code` releases atom + awesome-list diff（`awesome-mcp-servers` + `awesome-claude-code` repo activity） | score 全範圍，無分數 ceiling |
| **Experimental low-trust**（新加 trending）| GitHub trending Python，filter `agent\|llm\|mcp\|claude` | (a) score ceiling 4（不能拿 5）；(b) repo sanity check：age ≥ 30d / stars ≥ 100 / has README + license / commit-history 有近期活動 — 不過 sanity 直接砍 |

Daily curate 上限 5-8 → 8-12（curate prompt + 既有 tests 要改）。

### 3. Internal context — RAG vs snapshot 分階段

ADR-020 RAG infra 在 main 分支**尚未 merge**（仍在 `docs/kb-stub-crisis-memory` 分支 Stage 1.5）。Spec v1 把它當 production dependency 是錯的。

**分階段策略**：

| Phase | Internal context source | 條件 |
|---|---|---|
| **Phase 1（pre-RAG）** | 每週 regenerate `agents/franky/state/franky_context_snapshot.md`（含 active priorities / 當前 ADR 假設 / top-N open issues / 最近 30d MEMORY 變動） — 固定 inject 進 score + synthesis prompt | 立即可做，零 dependency |
| **Phase 2（post-RAG）** | 切換到 ADR-020 BGE-M3 + bge-reranker corpus retrieval | **唯一前提**：(a) ADR-020 merged 進 main；(b) S2a 的 cross-domain eval（English news ↔ 繁中 ADR）recall@k 達標 |

ADR-022 Phase 1 即可 ship，**不阻塞**等 ADR-020。

### 4. proposal 5 欄 frontmatter（含 metric_type 拆分）

每條 promoted proposal（vault page Stage 1 + GitHub issue Stage 2）frontmatter 必含：

```yaml
proposal_id: franky-proposal-2026-W18-1
related_adr: [ADR-016, ADR-020]      # 可空陣列
related_issues: [#420, #383]          # 可空陣列
metric_type: quantitative | checklist | human_judged
success_metric: "Robin ingest LLM cost 降 30%"
baseline_source: "shared.pricing.calc_cost over api_calls.where(agent='robin')"
verification_owner: shosho             # 對 human_judged 必填，retrospective 對它 ack
try_cost_estimate: "$1.50 + 2hr"
panel_recommended: yes | no            # deterministic positive list 觸發
panel_recommended_reasons: [...]       # LLM 補充
promote: false                         # 預設 false，修修手動切 true 才開 issue
```

retrospective 對 `quantitative` metric 跑數字驗證，對 `checklist` 跑 ✓/✗ 統計，對 `human_judged` 報 verification_owner verdict。

### 5. panel_recommended deterministic positive list

LLM 不能是唯一 classifier。以下 trigger 任一者成立 → 強制 `panel_recommended: yes`，LLM 補充 reasons：

- proposal 提及任何已 accept ADR 編號
- proposal 涉及改 agent 公開 contract（agents/<name>/__main__ 行為、shared API）
- proposal 引入新 persistent dependency（pyproject / requirements 新增）
- proposal 改 storage schema（state.db migration）
- proposal 改 HITL boundary（ADR-006 approval queue 行為）
- proposal 改 Slack/GitHub automation 權限

LLM 在以上任一觸發後可加 reasons；**也可單獨**判定 `yes`（捕捉 list 漏的 case）但不能單獨判定 `no`（list 命中強制 yes）。

### 6. proposal_metrics table（新加 state.db schema）

retrospective 需要 lifecycle 持久層。新增 `proposal_metrics` table（migration 隨 S5）：

```sql
CREATE TABLE proposal_metrics (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id         TEXT NOT NULL UNIQUE,
    issue_number        INTEGER,
    week_iso            TEXT NOT NULL,        -- '2026-W18'
    related_adr         TEXT,                  -- JSON array
    related_issues      TEXT,                  -- JSON array
    metric_type         TEXT NOT NULL CHECK (metric_type IN ('quantitative','checklist','human_judged')),
    success_metric      TEXT NOT NULL,
    baseline_source     TEXT,
    baseline_value      TEXT,                  -- 量化 metric 才填
    post_ship_value     TEXT,                  -- retrospective 時填
    verification_owner  TEXT,
    try_cost_estimate   TEXT,
    panel_recommended   INTEGER NOT NULL CHECK (panel_recommended IN (0,1)),
    status              TEXT NOT NULL CHECK (status IN ('candidate','promoted','triaged','ready','wontfix','shipped','verified','rejected')),
    created_at          TEXT NOT NULL,
    promoted_at         TEXT,
    triaged_at          TEXT,
    shipped_at          TEXT,
    verified_at         TEXT,
    related_pr          TEXT,
    related_commit      TEXT,
    source_item_ids     TEXT                   -- JSON array of original news item IDs
);
```

### 7. Implementation slices（v1 4 slice → v2 6 slice）

| Slice | 工作 | 工時粗估 |
|---|---|---|
| **S1** | Source 擴張（3 新 source + trust tier + curate prompt 5-8→8-12 + repo sanity check + tests） | 2-3h |
| **S2a** | Retrieval substrate（pre-RAG snapshot 路徑 — `franky_context_snapshot.md` regen 腳本 + cron + score prompt inject）| 2-3h |
| **S2b** | Scoring change（news_score.md 加 Relevance dim + 5-dim 權重 + shadow mode + pick gate + tests）| 2-3h |
| **S3** | Weekly synthesis（`news_synthesis.py` + prompt + hard quality gate + Stage 1 vault page + Stage 2 conditional issue create + panel_recommended deterministic list）| 3-4h |
| **S4** | Monthly retrospective（`news_retrospective.py` + prompt + metric_type 三類處理 + retro vault page + Slack 摘要）| 2-3h |
| **S5** | proposal_metrics table（migration + frontmatter extractor + status FSM + retrospective 對接）| 2-3h |

Total estimate: **13-19 hours**（v1 估 10-16h 漏估 retrieval 跟 metrics table）。每 slice 含 unit + integration tests，跨 slice 共用 fixture。

### 8. Cost claim — 不 freeze，跑 dry-run telemetry

v1 spec §7 `~$3/月` marginal cost claim **撤回**。實作時 weekly synthesis call 必須 log 進 `api_calls`（model / input_tokens / output_tokens / cache_read / cache_write / operation_id）。S3 ship 後跑 1 週 dry-run，retrospective 階段才能寫 SLO claim。

---

## Considered Options

### Rejected: 立即 auto `gh issue create` per high-relevance proposal（v1 spec §2 Fork 3 原案）

**Reject 理由**：(a) 違反 ADR-006 HITL spirit（自動建 work 不能等於 owner approved）；(b) Franky 偏向「shape pressure 必出 3 條」可能週週擠 mediocre proposal 雪崩 triage queue；(c) Codex pushback 6.1：first production mode 應該是 passive newsletter，issue creation 是 owner-promoted 後才觸發。

→ 改採 **two-stage proposal inbox**（vault page 先 + owner promote OR ≥2-item rule 才開 issue）。

### Rejected: 拆出新 agent（Sengoku / Mihawk / Rayleigh）

**Reject 理由**（修修 ack）：

- Workload 比較：Franky 加 +1 weekly + 1 monthly LLM call 後，工作量還是船員裡偏輕的（Robin / Brook 都更重）
- ADR-007 類比過頭：那次砍是因為加進來的 SEO observability + 外部 uptime probe **需要根本不同基礎設施**（OAuth API quota、外部物理位置）；本次加的「+1 LLM call/週」操作模式跟既有 news digest 一模一樣
- 漫畫設定：Franky 一直在「研究 + 升級 Sunny」一體，「情蒐 + 升級」是 canonical 設定
- Authority 切割清楚：Franky 是 reporter / analyst，戰略拍板仍由修修 + Claude 主線負責

→ 改採 **保留在 Franky + 正式 amend ADR-001 row**（透明性）。

### Rejected: 直接複用 ADR-020 RAG infra 為 production dependency（v1 spec §3 + §5 Q1 原案）

**Reject 理由**：ADR-020 在 main 分支尚未 merge（5/7 仍在 Stage 1.5）。把 in-flight infra 當 production dependency 是 spec drift（Codex Section 1）。

→ 改採 **分階段策略**：Phase 1 用 fixed snapshot（立即可做），Phase 2 切 ADR-020（前提：ADR-020 merged + cross-domain eval 達標）。

### Rejected: weekly 硬下限 1 條 proposal

**Reject 理由**：LLM satisfy shape pressure 強。硬下限 1 → 弱週硬擠 mediocre。

→ 改採 **0-3 條 + hard quality gate**（≥2 supporting items OR direct ADR/issue mapping）。

### Rejected: 純 LLM judging architectural lock-in

**Reject 理由**：LLM 對自己 context 的 architectural 程度判斷是 confirmation bias 高發區。

→ 改採 **deterministic positive list + LLM rationale 補充**（list 命中強制 yes，list 沒命中 LLM 可以加判 yes 但不可單獨判 no）。

---

## Consequences

### Positive

- Franky daily news digest 從「raw 蒐集 + Slack 推」進化到「raw 蒐集 + score by Nakama relevance」
- Weekly synthesis 把累積訊號變 1-3 條具體建議；月度 retrospective 把 ship 結果回灌
- two-stage inbox 守住 ADR-006 HITL spirit；deterministic positive list + dry-run shadow mode 守住 confirmation bias
- proposal_metrics table 讓「我們有沒有變強」第一次有 lifecycle 持久層
- ADR-001 row 透明 amend，不偷渡

### Negative / risks

- Franky 變胖（從輕排第 6 → 第 5）。下次再想塞工作要再走一次 grill + panel
- weekly synthesis cron 多一個失敗點（mitigated：vault Stage 1 寫成功就有歷史紀錄，issue Stage 2 失敗不致命）
- Phase 1 snapshot 機制比 RAG 弱，跨 7 天 ADR/MEMORY 大變動時 score 可能滯後（mitigated：每週 regenerate snapshot；Phase 2 切 RAG 後解）

### Lock-in & dependencies

- **ADR-001 row 已 amend** — 未來再改 Franky scope 必引本 ADR
- **ADR-020 dependency 鎖**：本 ADR 不引用 ADR-020 為 production dependency；Phase 2 切換另起 ADR（暫稱 ADR-022b）
- `proposal_metrics` table migration 一旦 ship 即進入 schema 凍結（schema_version=1，未來改加 `__v2` 欄位不破壞）

### Open questions for Phase 2（不阻塞 Phase 1 ship）

- ADR-020 merged 後，cross-domain eval（English news titles ↔ 繁中 ADR/MEMORY snippets）的 labeled fixture 怎麼建
- BGE-M3 vs LLM-as-retriever 的 gate metric（recall@k 多少才 ship）
- 補跑 Gemini audit（panel triangulation 完整 3-way）— `GEMINI_API_KEY` 設好後再決定

---

## Changelog

- **2026-05-07**：Accepted。修修 + Claude 經 grill-with-docs 5 fork + Codex panel audit + 修修工作量 push-back 後共識凍結。

