# ADR-008: SEO 觀測中心 — GSC + GA4 + Cloudflare（Phase 2）

**Date:** 2026-04-22
**Status:** Proposed（Phase 2，blocked by ADR-007 Phase 1 完工）

---

## Context

原屬 ADR-007（pre-multi-model-review 版）的第 2-7 節。三家模型一致指出 ADR-007 scope 過寬，建議將外部 API 整合拆為獨立 ADR（Gemini 明確命名 `ADR-008: External API Integration Strategy — Google (GSC/GA4) + Cloudflare`）。本 ADR 承接此拆分。

**為什麼等 Phase 1 完工再做**：

1. ADR-007 Phase 1 是 SEO 觀測的**基礎設施依賴**（`state.db` schema / cron wrapper / alert dedup / Franky Slack bot / `/healthz`）。Phase 1 未穩，Phase 2 疊加會讓故障歸因困難
2. 三家 API 整合都是「靜默失敗風險」高的工作（token refresh 失敗、UA vs GA4 相容、rate limit 被觸發）。需先有 `cron_runs` + `alert_state` 才能觀察到靜默失敗
3. Google Signals 啟用後 24-48 小時才有 demographics 資料，Phase 1 期間先空著收集

**Zoro-Franky 分工**（維持原決策，在此 ADR 落地介面）：

- **Zoro**：選 target keyword（攻擊戰略，產出 `config/target-keywords.yaml`）
- **Franky**：追蹤 target keyword 排名變化（戰情雷達，讀 GSC）
- 介面：`config/target-keywords.yaml`（schema + ownership 在 §6 定義）

**援引原則**：
- Schema：`docs/principles/schemas.md` §1-§4、§8（外部 API anti-corruption layer）
- Reliability：`docs/principles/reliability.md` §5（retry/backoff）、§7（timeout）、§8（DLQ）
- Observability：`docs/principles/observability.md` §1（structured log）、§6（alert dedup）、§9（secrets 不入 log）

---

## Decision

### 1. 範圍

| # | 整合 | 頻率 | 用途 |
|---|---|---|---|
| 1 | Google Search Console API | 每日 03:00 | 關鍵字排名 / impressions / CTR |
| 2 | Google Analytics Data API（GA4） | 每週一 03:00 | demographics / landing page engagement |
| 3 | Cloudflare GraphQL Analytics | 每 10 分鐘 | requests / threats / attacked paths |
| 4 | Weekly SEO digest 合併 infra digest | 每週一 10:00 | 單一 Franky DM |

### 2. GSC 整合

**SDK**：`google-api-python-client` + Search Console API v1

**Property 格式**：Domain property

```
sc-domain:shosho.tw
sc-domain:fleet.shosho.tw
```

**資料延遲（硬知識，照 Claude 獨到觀點）**：

GSC 資料有 **2-4 天延遲**，不是「前一日」。原 ADR-007 寫的 `end_date = today - 1` **會系統性偏移**。修法：

```python
# 錯的：end_date = today - 1
# 對的：動態查 dataAvailability 端點；保守退路 end_date = today - 4
```

**抓取範圍**：每日跑一次，抓 `end_date = today - 4` 起往前 7 天（含 overlap 重寫，用 UPSERT 保冪等，`reliability.md` §1）。

**Schema**（`shared/schemas/external/gsc.py`）：

```python
class GSCRowV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    site: constr(pattern=r"^sc-domain:[a-z0-9.\-]+$")
    date: date                       # aware-date（無時區）
    query: constr(min_length=1, max_length=200)
    page: constr(min_length=1, max_length=2000)
    country: constr(min_length=2, max_length=3)
    device: Literal["desktop", "mobile", "tablet"]
    clicks: NonNegativeInt
    impressions: NonNegativeInt
    ctr: confloat(ge=0, le=1)
    position: confloat(ge=1)
```

**state.db 表**（migration `003_seo_observability.sql`）：

```sql
CREATE TABLE gsc_rows (
    site         TEXT NOT NULL,
    date         TEXT NOT NULL,        -- YYYY-MM-DD
    query        TEXT NOT NULL,
    page         TEXT NOT NULL,
    country      TEXT NOT NULL,
    device       TEXT NOT NULL,
    clicks       INTEGER NOT NULL,
    impressions  INTEGER NOT NULL,
    ctr          REAL NOT NULL,
    position     REAL NOT NULL,
    fetched_at   TEXT NOT NULL,
    PRIMARY KEY (site, date, query, page, country, device)
);
CREATE INDEX idx_gsc_site_date ON gsc_rows(site, date DESC);
CREATE INDEX idx_gsc_query ON gsc_rows(query);
```

**告警條件**（target keyword only，避免全體噪訊）：

- **Critical**：target keyword 從 top 10 掉出
- **Warning**：target keyword 排名單週 W/W 掉 > 10 名
- **Warning**：target keyword 搜尋量（impressions）單週掉 > 30%
- **Info**：target keyword 進入 top 10

（複用 ADR-007 §4 的 `alert_state` dedup 機制；dedup_window = 1 天）

### 3. GA4 整合

**SDK**：`google-analytics-data`（v1beta）

**前置條件**（修修手動，不可由 code 繞過）：

1. GA4 property 已建立（非 UA legacy；UA 已 sunset，SDK 不支援）
2. Service account 加到 property → Admin → Property Access Management → Viewer role
3. **Google Signals 手動啟用**（需 property Admin 權限） — runbook 列步驟
4. 啟用後 24-48 小時才有 demographics 資料；Phase 2 開工後首週可能空白

**抓取範圍**：每週一 03:00 抓上週（`today - 8` 至 `today - 1`），因 GA4 資料較即時但避免尚未 final。

**Dimensions × Metrics**：

| Dimension | Metric | 用途 |
|---|---|---|
| `userAgeBracket` | `sessions` | 讀者年齡分佈 |
| `userGender` | `sessions` | 讀者性別 |
| `brandingInterest` / `interestAffinityCategory` | `sessions` | 興趣分類 |
| `landingPage` | `sessions`, `engagementRate`, `userEngagementDuration` | 找破口頁 |

**Schema**（`shared/schemas/external/ga4.py`）：

```python
class GA4AudienceRowV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    site: Literal["shosho.tw", "fleet.shosho.tw"]
    week_start: date                 # Monday of week
    dimension_name: Literal[
        "user_age_bracket", "user_gender",
        "interest_category", "landing_page"
    ]
    dimension_value: constr(min_length=1, max_length=500)
    sessions: NonNegativeInt
    engagement_rate: Optional[confloat(ge=0, le=1)] = None
    user_engagement_duration_s: Optional[NonNegativeInt] = None
    # k-anonymity: GA4 若 sample < threshold 會自動 null dimension value
    # 留 "(other)" 或 "(not set)"，不做後處理；schema allow 這些 literal
```

**state.db 表**：

```sql
CREATE TABLE ga4_audience (
    site                    TEXT NOT NULL,
    week_start              TEXT NOT NULL,  -- YYYY-MM-DD
    dimension_name          TEXT NOT NULL,
    dimension_value         TEXT NOT NULL,
    sessions                INTEGER NOT NULL,
    engagement_rate         REAL,
    user_engagement_duration_s INTEGER,
    fetched_at              TEXT NOT NULL,
    PRIMARY KEY (site, week_start, dimension_name, dimension_value)
);
CREATE INDEX idx_ga4_site_week ON ga4_audience(site, week_start DESC);
```

**隱私 / 合規**（Grok 提出）：

- **只存 aggregate**（GA4 API 預設不給 user id；schema 明示只允許 dimension）
- 個資法 / GDPR：aggregate + k-anonymity 在小流量站仍可能可識別 → 不對外發布 demographics（只用於 Brook 寫作定位）
- 隱私政策揭露：使用 GA4 的事實（runbook 列 cookie policy template 連結）

### 4. Cloudflare 整合

**API**：GraphQL Analytics API（`https://api.cloudflare.com/client/v4/graphql`）

**Token scope**：**read-only**（Account: Analytics:Read + Zone: Analytics:Read），不給 edit

**Zone 模型**：

- shosho.tw 與 fleet.shosho.tw 共用單一 zone（`shosho.tw`）
- GraphQL query 用 `clientRequestHTTPHost` dimension filter 分別兩 hostname

**抓取頻率**：每 10 分鐘一次，抓最近 10 分鐘的：

- `httpRequests1mGroups` → requests_per_hostname
- `firewallEventsAdaptiveGroups` → threats + top attacked path
- `httpRequestsAdaptiveGroups` → bot score distribution

**Schema**（`shared/schemas/external/cloudflare.py`）：

```python
class CloudflareSnapshotV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    zone: Literal["shosho.tw"]
    hostname: Literal["shosho.tw", "fleet.shosho.tw"]
    window_start: AwareDatetime
    window_end: AwareDatetime
    requests: NonNegativeInt
    bytes: NonNegativeInt
    threats_blocked: NonNegativeInt
    top_attacked_paths: list[AttackedPathV1]  # max 5
    bot_score_buckets: BotScoreBucketsV1
```

**state.db 表**：

```sql
CREATE TABLE cloudflare_snapshots (
    hostname         TEXT NOT NULL,
    window_start     TEXT NOT NULL,
    window_end       TEXT NOT NULL,
    requests         INTEGER NOT NULL,
    bytes            INTEGER NOT NULL,
    threats_blocked  INTEGER NOT NULL,
    payload          TEXT NOT NULL,   -- 完整 JSON（含 top_attacked_paths, bot buckets）
    PRIMARY KEY (hostname, window_start)
);
CREATE INDEX idx_cf_hostname_time ON cloudflare_snapshots(hostname, window_start DESC);
```

**Baseline 定義**（Claude 獨到觀點 R8 — 原 ADR-007 缺失）：

「threat rate > baseline 10x」的 baseline 定義：

- **Rolling 7-day median**（忽略 top 5% outlier，排除已攻擊日污染）
- SQL：`SELECT median(threats_blocked) FROM cloudflare_snapshots WHERE hostname=? AND window_start >= now - 7d`
- 小流量站點（threats/hr < 5）**豁免告警**（正常爬蟲 spike 會誤報）

**告警**（複用 `alert_state` dedup）：

- **Critical**：threats_blocked 10-min window > max(baseline × 10, 50)，且 requests 增長 > 5x（真 DDoS 跡象）
- **Warning**：threats_blocked > baseline × 3（但非 DDoS）
- dedup_window：Critical 15 min、Warning 60 min

### 5. Rate Limit 預算

三家 API 每日呼叫次數與免費 quota 對照，驗證 Phase 2 設計在預算內：

| API | Free quota | 本設計呼叫次數 | 餘裕 |
|---|---|---|---|
| GSC | 1200 QPD / 10 QPS | 2 sites × 1/day = 2 QPD | 充裕 |
| GA4 | 10k tokens/day / 50 concurrent | 2 sites × weekly deep query ≈ 500 tokens/week | 充裕 |
| Cloudflare GraphQL | 300 QPM | 144 calls/day (10 min × 2 hostname filter) | 充裕 |

**結論**：免費 quota 足夠，但必須：

- GSC：若 query count > 5000/day（例如追蹤全站關鍵字）要改 batch 一天一次 + `dimensionFilterGroups`
- GA4：單一 request 多 dimension 組合會吃 token 倍數，用 `requests` list 拆多個 small request 控量
- Cloudflare：實際 query 數 = hostnames × query types，超出再考慮合併（單 GraphQL query 多欄位，一次搞定）

### 6. `config/target-keywords.yaml` 三方共用 schema

**動機**：Zoro（寫）+ Usopp（append）+ Franky（讀）共用，沒 schema 會出 race / drift（三家 review 共識）。

**Schema**（`shared/schemas/seo.py` — 新檔）：

```python
class TargetKeywordV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    keyword: constr(min_length=1, max_length=100)       # 統一繁中（台灣）
    keyword_en: Optional[constr(max_length=100)] = None # Brook/Usopp 可加英文對照
    site: Literal["shosho.tw", "fleet.shosho.tw"]
    added_by: Literal["zoro", "usopp", "shosho"]
    added_at: AwareDatetime
    goal_rank: Optional[PositiveInt] = None             # Zoro 戰略目標排名
    source_post_id: Optional[int] = None                # Usopp 來源文章 id

class TargetKeywordListV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    updated_at: AwareDatetime
    keywords: list[TargetKeywordV1]
```

**併發寫入**：YAML 檔非 DB，用 `filelock` 保護：

```python
with filelock.FileLock("config/target-keywords.yaml.lock", timeout=10):
    current = TargetKeywordListV1.model_validate(yaml.safe_load(f.read()))
    current.keywords.append(new)
    f.write(yaml.safe_dump(current.model_dump()))
```

**Ownership 規則**：

- Zoro：可 add / remove / update goal_rank
- Usopp：只 append（publish 時以 focus_keyword 自動加入），不 remove
- Franky：唯讀
- 修修：手動任何操作（via CLI）

**語言規範**：`keyword` 欄位統一繁中（match GSC 繁中 query），英文對照放 `keyword_en`，避免 Franky 去 GSC 查時 match 失敗（Claude 獨到觀點 4.8）。

### 7. Weekly SEO digest 格式

併進 ADR-007 §10 的 infra digest（同一則 DM），新增 SEO 區塊：

```markdown
## SEO 排名（Top 10 target keywords W/W 變化）
| Keyword | 本週 | 上週 | 變化 | impressions |
|---|---|---|---|---|
| 肌酸 功效 | #4 | #6 | ↑2 | 1,240 |
| 睡眠 神經科學 | #12 | #14 | ↑2 | 420 |

## 讀者 persona（shosho.tw 本週）
- 年齡：25-34 (42%) / 35-44 (31%) / 18-24 (15%)
- 性別：男 58% / 女 42%
- 興趣 top 3：Technology, Health, Lifestyle
- 停留最久頁面：/blog/running-brain-exercise (avg 4'23")
- 破口頁：/blog/creatine-women（engagement 18%，建議 Brook 重寫）

## 安全（Cloudflare）
- 本週 blocked threats: 87（baseline median 64）
- Top attacked path: /wp-login.php（58 attempts）
- Bot score p50: 32（human-leaning）
```

### 8. Credentials 管理

**Google service account JSON**：

- 存放：`/home/nakama/secrets/gsc-ga4-sa.json`
- 權限：`chmod 600`，owner `nakama` user
- 不進 git（`.gitignore` 已含 `secrets/`，`.env.example` 只記路徑）
- Service account 需分別在 GSC property + GA4 property 加權限（runbook 分兩步）

**Cloudflare API token**：

- 走 `.env`：`CLOUDFLARE_API_TOKEN=...`
- Scope：最小權限（Account: Analytics:Read + Zone: Analytics:Read on shosho.tw only）

**Token refresh 靜默失敗告警**（Gemini b2 觀點）：

- 每個 cron 失敗後 → `cron_runs.error_msg` 記錄完整錯誤
- `alert_router` 規則：單一 API cron 24 小時內失敗率 > 50% → Warning
- 首次 auth failure（401 / 403）→ 立即 Warning（不 dedup，修修要知道）

### 9. 依賴隔離（Gemini 獨到觀點）

`google-api-python-client` + `google-analytics-data` 是巨型 package（數十 MB 依賴）。Phase 2 開工前：

- 跑 `pip install --dry-run` 檢查與 Nakama 現有依賴衝突（尤其 `protobuf`、`grpcio`、`requests` 版本）
- 若衝突無解 → 用獨立 venv `agents/franky/.venv-seo`（僅 Franky SEO cron 使用）
- ADR-008 通過後第一步就是依賴衝突掃描，寫入 `docs/runbooks/phase2-seo-deps-check.md`

---

## Consequences

### 正面
- 每週 SEO 數據 + 讀者 persona 餵給 Brook 寫作定位
- Cloudflare 流量異常可自動告警（非只能看 dashboard）
- GSC target keyword 排名變化即時知道（補 Zoro 戰略的量化回饋）

### 負面
- 依賴三家外部 API，任一家 schema drift / auth 過期都會斷鏈
- GA4 demographics 在小流量站可能大量 null（k-anonymity）
- 新增依賴增加 VPS RAM 使用（runbook 會驗 baseline 是否仍在 headroom 內）

### 風險（Phase 2-specific）
- Google 帳號 2FA token 過期 → service account JSON 失效 → 靜默失敗 → 靠 §8 告警規則攔截
- Cloudflare GraphQL schema 變更（歷史有先例） → anti-corruption layer（`schemas.md` §8）捕捉
- 小流量站 demographics 仍可識別個人 → 不對外發布、僅 Brook 內部
- Franky cron 疊加後 VPS headroom 可能收緊 → Phase 2 開工前重跑一次 baseline（同 ADR-007 §6 方法）

---

## SLO

| 指標 | 目標 |
|---|---|
| SEO weekly digest 產出成功率 | > 98%（月） |
| GSC 每日 fetch 成功率 | > 95% |
| GA4 weekly fetch 成功率 | > 98% |
| Cloudflare 10-min fetch 成功率 | > 99% |
| SEO 整合新增的 cron 平均耗時 | p95 < 60 秒 |

---

## 開工 Checklist

### A. Phase 1 完工前置（不可繞過）

- [ ] ADR-007 Phase 1 上線並通過 72 小時 soak test
- [ ] `cron_runs` / `alert_state` 表運作正常
- [ ] Franky Slack bot 至少已發送 1 次 weekly digest

### B. 修修端準備（已於 `project_phase1_infra_checkpoint.md` 記錄完成，複檢）

- [ ] GSC property 已 verify（`shosho.tw` + `fleet.shosho.tw` 為 domain property）
- [ ] GA4 property 存在且非 UA
- [ ] Google Signals 手動啟用 + 等 48 小時
- [ ] Google service account JSON 取得，上傳至 VPS `/home/nakama/secrets/`（chmod 600）
- [ ] Service account email 加入 GSC + GA4 property 的 Viewer role（兩邊各加一次）
- [ ] Cloudflare API token 產出（read-only scope）並寫進 `.env`
- [ ] 隱私政策頁更新 GA4 揭露（法規）

### C. Phase 2 開工前（Claude Code 端）

- [ ] 依賴衝突掃描 → `docs/runbooks/phase2-seo-deps-check.md`
- [ ] 跑 VPS baseline 壓測（同 ADR-007 §6 方法）確認 Phase 1 + Phase 2 負載仍在預算
- [ ] `config/target-keywords.yaml` 初始檔 + schema 驗證腳本
- [ ] `shared/schemas/external/{gsc,ga4,cloudflare}.py` + `shared/schemas/seo.py`

### D. Phase 2 實作順序（3 週）

- [ ] week 1：migration `003_seo_observability.sql` + GSC integration + alert rules（以 Zoro target keyword 為唯一告警維度）
- [ ] week 2：GA4 integration + persona digest section
- [ ] week 3：Cloudflare integration + attack baseline + 整合 weekly digest

### E. Phase 2 完工驗收

- [ ] SEO digest 於第 1 個週一成功送出
- [ ] 故意把某 target keyword 從 top 10 下架（用 sandbox property）→ 10 分鐘內 Critical DM
- [ ] GSC / GA4 auth 全失 → §8 告警規則 1 小時內 Warning
- [ ] Cloudflare baseline rolling 計算正確（單元測試 + 一週實測對照 CF dashboard）

---

## 不做的事

- ❌ SERP API / SerpAPI / DataForSEO 即時排名查詢（Phase 3 評估，先用 GSC）
- ❌ iPaaS（Airbyte / Fivetran）（Phase 3 評估；自寫 client 夠小先扛）
- ❌ GA4 user-level 資料（只 aggregate）
- ❌ Cloudflare 自動 firewall rule 調整（token read-only，不給 edit 權限）
- ❌ LLM 生成 SEO 建議（那是 Zoro 的職責；Franky 只觀察）

---

## Notes

- 本 ADR 援引 `docs/principles/` 三份硬規則，違反即 reject
- 與 ADR-005 publishing 共生：Usopp publish 後 append `focus_keyword` 到 `config/target-keywords.yaml`（照 §6 ownership 規則）
- 與 ADR-007 共用 Franky Slack bot + `alert_state` + `cron_runs` 基礎設施
- 未來若拆 ADR 更細：`ADR-008a Zoro-Usopp-Franky target keyword config schema`（目前先併在本 ADR §6）
