# ADR-009: SEO Solution Architecture — Skill 家族 + Brook compose 整合

**Date:** 2026-04-24
**Status:** Proposed

---

## Context

修修的內容策略要同時滿足三件事：（1）寫什麼（關鍵字 / 主題建議）、（2）既有部落格 SEO 體檢、（3）寫稿時把 SEO 數據餵進 Brook compose 以提升排行潛力。`keyword-research` skill 已 production（topic → YouTube/Trends/Reddit → core keywords + title seeds），Usopp 已把 `focus_keyword` / `meta_description` 寫進 SEOPress（PR #101 Slice C2a），但中間缺一層：**把關鍵字研究結果 enrich 成可動作的 SEO context，並在 compose 時作為 system prompt 的數據依據**。

prior-art（[docs/research/seo-prior-art-2026-04-24.md](../research/seo-prior-art-2026-04-24.md)）已經盤點工具地景，列出 8 個 open question 並附初步建議。本 ADR 把這 8 題收斂成架構決策，凍結 3 個 skill（`seo-audit-post` / `seo-keyword-enrich` / `seo-optimize-draft`）的邊界、`SEOContextV1` schema、Brook compose 整合介面、Phase 1/2 界線。

**與 [ADR-008 SEO 觀測中心](ADR-008-seo-observability.md) 的分層關係**（本 ADR 所有決策必須與 ADR-008 介面相容）：

| 層 | ADR | 資料方向 | 代表產物 |
|---|---|---|---|
| 觀測（被動讀） | ADR-008 | GSC/GA4/Cloudflare → `state.db` → Franky digest | `gsc_rows` 表、`alert_state`、weekly digest |
| 寫作（主動生） | **ADR-009（本檔）** | keyword research + GSC 查詢 → `SEOContextV1` → Brook compose → DraftV1 | 3 個 skill、`SEOContextV1` schema |

兩層透過以下介面互相 aware：
1. **共用 GSC client**（`shared/gsc_client.py`） — 兩邊都 import；ADR-008 批次落 `gsc_rows`，ADR-009 skill 互動式拉最近 28 天
2. **共用 `config/target-keywords.yaml`** — ADR-008 §6 已凍結 `TargetKeywordV1` schema 與 ownership；ADR-009 `seo-keyword-enrich` 讀它（不寫，ownership 仍歸 ADR-008）
3. **gsc_rows 表為 striking-distance data 來源** — ADR-008 Phase 2 上線後，ADR-009 skill 可直接 query SQL（比即時 API 快、省 quota）；但 Phase 2 未上線時，skill 自行呼叫 GSC API（透過共用 client）

**援引原則**：
- Schema：`docs/principles/schemas.md` §1-§4（contract 先寫、schema_version、extra="forbid"、Literal 取代 enums）、§8（外部 API anti-corruption layer）
- Reliability：`docs/principles/reliability.md` §5（retry）、§7（timeout）
- Observability：`docs/principles/observability.md` §1（structured log）、§9（secrets 不入 log）
- Open-source 原則：[feedback_open_source_ready.md](../../memory/claude/feedback_open_source_ready.md)（每個 skill 皆可獨立開源）
- Skill 三層架構：[feedback_skill_design_principle.md](../../memory/claude/feedback_skill_design_principle.md)（互動式 → skill、確定性 → `shared/*.py`、agent 編排）

---

## Decision

### D1. Skill 家族切法 — 採 **Option A：3 個 skill**

| Skill | 單一責任 | Phase |
|---|---|---|
| `seo-audit-post` | 單篇已發佈 URL 體檢，產 markdown report | 1 |
| `seo-keyword-enrich` | `keyword-research` 結果 → + GSC striking-distance + DataForSEO difficulty + firecrawl SERP 摘要 → `SEOContextV1` | 1 |
| `seo-optimize-draft` | 既有 draft.md + `SEOContextV1` → 重寫 / 改寫建議（內部 call Brook compose） | 2 |

**Trigger phrases**（寫進 skill frontmatter `description`）：

- `seo-audit-post`：「audit 一下 <URL>」、「幫這篇做 SEO 體檢」、「檢查 <URL> 的 SEO」、「SEO audit」
- `seo-keyword-enrich`：「enrich 這份關鍵字研究」、「加上 ranking 數據」、「把 keyword 打 SEO 分數」、「SEO enrich」
- `seo-optimize-draft`：「用這份 SEO 數據重寫」、「優化這篇草稿」、「SEO rewrite」

**非目標**（寫進每個 skill 的 `Do NOT trigger for` 區）：
- `seo-audit-post` 不做 keyword 探索（那是 `keyword-research`）、不改寫內文（那是 `seo-optimize-draft`）
- `seo-keyword-enrich` 不自己做原始 keyword 研究、不跑 audit
- `seo-optimize-draft` 不重跑 keyword 研究、不做 URL 體檢

**為什麼選 A 而非 B（2 skill）/ C（1 big skill）**：
- 單一責任 → 測試、除錯、開源切塊都簡單
- `seo-keyword-enrich` 獨立有 standalone 價值（修修可能只想看 enrichment 再決定要不要寫）
- 符合 [feedback_skill_design_principle.md]「skill 粒度扁平」
- 跨 skill 串接成本主要在 `SEOContextV1` schema drift — 透過 D3 凍結 schema 吸收

**與 `keyword-research` 的解耦**：`keyword-research` 已 production 凍結，不動；SEO solution 是它的下游 enricher，不是替代品。

### D2. 數據源組合

| 數據源 | 用途 | 成本 | Fallback 策略 |
|---|---|---|---|
| **Google Search Console API** | 自己網站 striking-distance keyword、cannibalization 偵測、真實 impressions/CTR | $0 | auth 失敗 → skill 報錯 + 建議走 `/seo-keyword-enrich --no-gsc` fallback mode |
| **PageSpeed Insights API** | `seo-audit-post` 的 CWV + Lighthouse SEO category | $0 | 失敗 → 標記「CWV unavailable」繼續跑其他 check |
| **DataForSEO Labs API** | 非 health 類 keyword 的 difficulty / search_volume（health 類會被 anonymize） | $50 起儲值、~$0.005/audit | 失敗或 health 關鍵字 → 省略此欄位，Claude synth 時標註「difficulty unknown」 |
| **firecrawl plugin（已裝）** | 競品 top-3 SERP 頁面結構爬取 | 免費 quota 內 | 失敗 → `competitor_serp_summary = None` |
| **既有 `keyword-research` skill frontmatter** | core_keywords / trend_gaps / title_seeds | $0.05/run（已計在 keyword-research） | 必要輸入；缺則 `seo-keyword-enrich` 報錯提示先跑 `keyword-research` |
| **既有 Robin KB search** | LLM semantic check 時查作者既有觀點（E-E-A-T + internal link） | $0.01/audit | 失敗 → 省略 internal link 建議 |

**為什麼 DataForSEO 不是主源**：prior-art §1.1 — DataForSEO 自己 help-center 寫明 Health & Wellness 屬 Google Ads 受限類別，`search_volume` / `CPC` 會被 hide。GSC 是 Health vertical 唯一可靠數據源（自己網站，不受廣告政策影響）。

**為什麼不走 Ahrefs / Semrush / SurferSEO**：見 §Alternatives Considered。

### D3. `SEOContextV1` schema 凍結

**位置**：`shared/schemas/publishing.py`（與 `DraftV1` / `PublishRequestV1` 同檔，維持 Brook → Usopp 契約集中）。

**Phase 1 schema**（Pydantic v2）— **Slice A PR #132 實作版（2026-04-25 更新）**：

> 原始 ADR 草案包含 DataForSEO / firecrawl 等 Phase 1.5 欄位。Slice A 實作時簡化為
> GSC-only baseline；Phase 1.5 新增的欄位走 optional `| None = None` 加入，符合 D8
> 升版策略「增加 optional 欄位 → minor change」。以下為**凍結的 V1 實作版**。

```python
from typing import Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, confloat, conint, constr


class KeywordMetricV1(BaseModel):
    """單一關鍵字的 ranking 指標快照。

    Phase 1 聚焦 GSC 來源：GSC 每行必有 clicks/impressions/ctr/position 四欄位，
    因此這四欄為非 nullable。Phase 1.5 加入 DataForSEO 時，新欄位（keyword_en /
    search_volume / difficulty）走 optional 加入。
    """
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1

    keyword: constr(min_length=1, max_length=200)
    clicks: conint(ge=0)
    impressions: conint(ge=0)
    ctr: confloat(ge=0.0, le=1.0)
    avg_position: confloat(ge=1.0, le=200.0)
    source: Literal["gsc", "dataforseo"] = "gsc"

    # ── Phase 1.5 預留（加入時為 optional，不破 V1 消費端）──
    # keyword_en: constr(max_length=100) | None = None
    # search_volume: NonNegativeInt | None = None
    # difficulty: confloat(ge=0, le=100) | None = None


class StrikingDistanceV1(BaseModel):
    """11-20 排名邊緣關鍵字 — push 一下就能上第一頁。

    業界「striking distance」慣用 11-20，但 GSC 平均 position 會給小數
    （如 10.8 / 20.3），schema 留緩衝區間由 enrich skill 的 filter logic
    決定實際收錄範圍，避免 edge value 直接被 schema reject。

    **實作契約（triangulation T6）**：GSC raw rows 必須在 skill 層先 filter
    才建 `StrikingDistanceV1` 物件；不符合 [10.0, 21.0] range 的 row 用 `drop`
    處理，**絕不**以 try/except ValidationError 當 filter（浪費算力 + 錯誤訊號污染）。
    """
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1

    keyword: constr(min_length=1, max_length=200)
    url: constr(min_length=1, max_length=2048)
    current_position: confloat(ge=10.0, le=21.0)
    impressions_last_28d: conint(ge=0)
    suggested_actions: list[str] = Field(default_factory=list)


class CannibalizationWarningV1(BaseModel):
    """多個 URL 在同一關鍵字互相競爭的警告。"""
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1

    keyword: constr(min_length=1, max_length=200)
    competing_urls: list[constr(min_length=1, max_length=2048)] = Field(min_length=2)
    severity: Literal["low", "medium", "high"]
    recommendation: constr(min_length=1, max_length=500)


class SEOContextV1(BaseModel):
    """`seo-keyword-enrich` 產出；`Brook compose` 可選輸入；`seo-optimize-draft` 消費。

    語意：給 Brook 寫稿時「該篇文章應該打什麼關鍵字組、競品長怎樣、自家既有內容
    在這個關鍵字上的歷史位置」— 全部 aggregate 在一個 frozen contract，
    跨 skill 傳遞不怕 drift。
    """
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1

    target_site: TargetSite  # app-name（非 GSC host 字串）
    primary_keyword: KeywordMetricV1 | None = None
    related_keywords: list[KeywordMetricV1] = Field(default_factory=list)
    striking_distance: list[StrikingDistanceV1] = Field(default_factory=list)
    cannibalization_warnings: list[CannibalizationWarningV1] = Field(default_factory=list)
    competitor_serp_summary: str | None = None
    generated_at: AwareDatetime
    source_keyword_research_path: str | None = None

    # ── Phase 1.5 預留（加入時為 optional，不破 V1 消費端）──
    # title_seed_hints: list[constr(max_length=200)] = Field(default_factory=list)
    # sources_available: list[Literal["gsc", "dataforseo", ...]] = Field(default_factory=list)
```

**schema 設計決策說明**：

- `schema_version: Literal[1]` + `extra="forbid"` + `frozen=True` → 遵守 `schemas.md` §1-§4
- **Phase 1 GSC-only**：GSC 每行必有 `clicks`/`impressions`/`ctr`/`position` 四指標，因此 `KeywordMetricV1` 這四欄非 nullable。Phase 1.5 加 DataForSEO 時，新欄位走 `| None = None` optional 加入（符合 D8 升版策略 minor change）
- `source: Literal["gsc", "dataforseo"]`（單值 default `"gsc"`）→ Phase 1 每筆 metric 來自單一來源；如果未來同一 keyword 有多源，考慮升版
- `striking_distance.current_position: confloat(ge=10.0, le=21.0)` → 業界「striking distance」慣用 11-20，schema 層留 ±1 緩衝區間吸收 GSC 小數 position（avg 10.8 / 20.3 也算）；實際「收進來的算不算 striking distance」由 `seo-keyword-enrich` 的 filter logic 決定
- `cannibalization_warnings.competing_urls: min_length=2` → 定義上就是 2+ URL 競爭
- `CannibalizationWarningV1` 加 `severity` + `recommendation` → 提供 actionable 資訊，不只列 URL
- `target_site: TargetSite`（app-name）→ **對齊 `DraftV1.target_site`，不是** ADR-008 `TargetKeywordV1.site` 的 GSC host 字串。跨層傳遞策略：
  - `seo-keyword-enrich` 讀 `config/target-keywords.yaml`（ADR-008 schema）→ 依 host 呼叫 GSC → 產出 `SEOContextV1` 時把 host 對回 app-name（mapping 由 **`shared/schemas/site_mapping.py`** 提供純函式 `host_to_target_site("shosho.tw") → "wp_shosho"`；**不放在** `shared/gsc_client.py` 以保 client 為 thin wrapper — triangulation T5）
  - Brook compose 與 Usopp 消費 `SEOContextV1` 時只看 `target_site`（app-name），不碰 host 字串
  - 此 mapping 為 Slice A PR 驗收條件：(1) 檔案位置正確（`shared/schemas/site_mapping.py`），(2) 窮舉 test `set(HOST_TO_TARGET_SITE.values()) == set(TargetSite.__args__)` 確保新增 target site 時 Literal 同步

### D4. Phase 1 / Phase 2 界線

**Phase 1（先做，2-3 週）**：

- ✅ `seo-audit-post` 基本版
  - Script 層 ~25 條 deterministic check（metadata / image alt / heading 結構 / internal-external links / schema 存在）
  - LLM 層 ~10 條 semantic check（用 Claude Sonnet：focus keyword 語義在 H1/第一段、E-E-A-T signals、schema vs 內容一致性、藥事法 SEO 不衝突）
  - PageSpeed Insights API 整合（CWV + Lighthouse SEO category）
  - Markdown report 輸出
- ✅ `seo-keyword-enrich`
  - 讀 `keyword-research` frontmatter
  - GSC API 拉最近 28 天 query × URL（striking distance + cannibalization 偵測）
  - DataForSEO Labs keyword_difficulty（非 health 關鍵字）
  - firecrawl top-3 SERP 爬取 + Claude Haiku 摘要
  - 輸出 `SEOContextV1`（寫成 markdown + frontmatter，下游 skill 可 parse）
- ✅ `SEOContextV1` schema 落 `shared/schemas/publishing.py`
- ✅ Brook compose opt-in 整合
  - `compose_and_enqueue(..., seo_context: SEOContextV1 | None = None)`
  - `_build_compose_system_prompt(profile, seo_context)` — seo_context 非 None 時，system prompt 尾端接 SEO block
  - `None` = fallback 到現狀，不破既有對話式 flow
- ✅ Cannibalization detection（prior-art §6 第 8 點建議含；~50 行 Python）
- ✅ Shared GSC client（`shared/gsc_client.py`）— ADR-008 Phase 2 可 import 此 client 做批次（避免兩份實作）

**Phase 2（之後，時程由修修決定）**：

- `seo-optimize-draft` skill（吃既有 draft + `SEOContextV1` → 產重寫建議或呼叫 compose 重生）
- Cron-driven 整站 GSC 體檢報告（與 ADR-008 Phase 2 weekly digest 合併交付）
- SurferSEO API 評估（content score 反饋迴路；中文支援驗證後再決定）
- GEO / AEO optimization（Answer Engine / Generative Engine 專題）
- `seo-audit-post` full mode（加競品對照、跨頁關鍵字網絡分析）

### D5. Brook compose 整合契約（精準整合點）

**目標檔**：[agents/brook/compose.py](../../agents/brook/compose.py)

**修改點 1** — `compose_and_enqueue` signature（目前在 [compose.py:431](../../agents/brook/compose.py#L431)）：

```python
# 現狀（Phase 1 落 ADR-005a 時凍結的 signature）
def compose_and_enqueue(
    *,
    topic: str,
    category: Category | None = None,
    kb_context: str = "",
    source_content: str = "",
    target_site: TargetSite = "wp_shosho",
    scheduled_at: AwareDatetime | None = None,
    primary_category_override: str | None = None,
    model: str | None = None,
    max_tokens: int = 8192,
) -> dict[str, Any]:

# Phase 1 (ADR-009) 加 seo_context 參數（向後相容）
def compose_and_enqueue(
    *,
    topic: str,
    category: Category | None = None,
    kb_context: str = "",
    source_content: str = "",
    target_site: TargetSite = "wp_shosho",
    scheduled_at: AwareDatetime | None = None,
    primary_category_override: str | None = None,
    model: str | None = None,
    max_tokens: int = 8192,
    seo_context: SEOContextV1 | None = None,  # 新增
) -> dict[str, Any]:
```

**修改點 2** — `_build_compose_system_prompt` signature（目前在 [compose.py:322](../../agents/brook/compose.py#L322)）：

```python
# 現狀
def _build_compose_system_prompt(profile: StyleProfile) -> str:

# 新
def _build_compose_system_prompt(
    profile: StyleProfile,
    seo_context: SEOContextV1 | None = None,
) -> str:
    # 舊內容（風格 + 輸出規範）維持不變
    ...
    # seo_context 非 None 時，在最後接 SEO block（放「輸出規範」之後，
    # 避免覆寫格式硬規則）
    if seo_context is not None:
        sections.append(_build_seo_block(seo_context))
    return "\n\n".join(sections)
```

**`_build_seo_block` 合約**（新 helper，同檔）：
- 輸入 `SEOContextV1`；只讀非 None 欄位
- 輸出一段繁中 system prompt 片段，包含：
  - 目標 focus keyword + 關聯關鍵字的自然使用建議
  - Striking-distance 機會（如有）→ 明確引導 LLM 在內文呼應這些關鍵字
  - Cannibalization 警告（如有）→ 提示 LLM 避免與 `competing_urls` 的主題重疊
  - 競品 SERP 摘要（如有）→ 引導差異化角度
- **絕對不覆寫** `DraftV1.focus_keyword` / `meta_description` 的輸出格式規則（那是 ADR-005a 凍結的）— SEO context 只是給 LLM **寫作時的數據依據**，LLM 仍自己產出這兩欄位

**為什麼 opt-in（`seo_context=None` 是合法且預設）**：
- 修修現有對話式 flow（Slack `@Brook 幫我寫 XXX`）沒改；Nami / chat caller 不知道 `seo_context` 的存在不影響 Phase 1 行為
- `seo-optimize-draft` skill（Phase 2）會顯式傳入 `seo_context`
- 互動式 skill 階段（如 `seo-keyword-enrich` 後直接寫稿）可由呼叫端決定是否傳入

### D6. LLM 模型選擇（對齊 [feedback_cost_management.md]）

| 場景 | 模型 | 單次成本 | 理由 |
|---|---|---|---|
| `seo-audit-post` LLM semantic check（~10 條） | Claude **Sonnet** 4.6 | ~$0.02/audit | Semantic judgment 質量重要（E-E-A-T / 藥事法 / keyword 語義）；$0.02 vs Haiku 的 $0.002 差 10×，但 audit 頻率低（~5 次/週），月成本 <$0.5 |
| `seo-keyword-enrich` synth（merge / rank / 摘要） | Claude **Haiku** 4.5 | ~$0.005/enrich | 純結構化 merge + ranking，無需 deep judgment |
| `seo-keyword-enrich` 的 firecrawl top-3 SERP 摘要 | Claude **Haiku** 4.5 | 同上（合併計） | 摘要任務，Haiku 足夠 |
| `seo-optimize-draft`（Phase 2）內部 call compose | 沿用 Brook compose 既有 **Sonnet**（llm_router 決定） | 同現狀 ~$0.05-0.15 | 已是生產配置 |

**月成本估算**（沿用 prior-art §5.1）：$3/月 + 一次性 $50 DataForSEO 儲值，vs Ahrefs $129/月。

### D7. Skill frontmatter `description` 草擬（避免與 `keyword-research` 觸發詞衝突）

**`seo-audit-post/SKILL.md` frontmatter**：

```yaml
---
name: seo-audit-post
description: >
  On-page SEO audit for a single published blog URL —
  fetches HTML, runs PageSpeed Insights, checks ~25 deterministic rules
  (metadata / headings / image alt / schema / internal links) + ~10 LLM
  semantic rules (E-E-A-T, focus keyword semantics, Taiwan pharma compliance),
  produces a markdown report with fix suggestions. Use this skill when the
  user says "SEO audit <url>", "幫這篇做 SEO 體檢", "檢查 <url> 的 SEO",
  "audit 一下 <url>". Do NOT trigger for keyword research (use
  keyword-research) or draft rewriting (use seo-optimize-draft when Phase 2
  lands).
---
```

**`seo-keyword-enrich/SKILL.md` frontmatter**：

```yaml
---
name: seo-keyword-enrich
description: >
  Enrich a keyword-research frontmatter report with on-site GSC data
  (striking-distance keywords, cannibalization warnings), DataForSEO Labs
  difficulty (non-health terms only), and firecrawl top-3 SERP summary.
  Produces a SEOContextV1 block consumable by seo-optimize-draft and
  Brook compose. Use when the user says "enrich 這份關鍵字研究",
  "加上 ranking 數據", "SEO enrich", or hands you a keyword-research
  markdown and asks for SEO context. Do NOT run raw keyword research
  (use keyword-research) or audit a URL (use seo-audit-post).
---
```

**`seo-optimize-draft/SKILL.md` frontmatter**（Phase 2 才建，此處先凍結觸發詞避免未來衝突）：

```yaml
---
name: seo-optimize-draft
description: >
  Rewrite or refine an existing draft (markdown or DraftV1) using a
  SEOContextV1 block — calls Brook compose internally with seo_context
  kwarg. Use when the user says "用這份 SEO 數據重寫", "優化這篇草稿",
  "SEO rewrite", or hands you a draft + enriched keyword context.
  Do NOT trigger for fresh composition without SEO context (use
  Brook compose / chat) or for URL audit (use seo-audit-post).
---
```

### D8. Schema 升版策略 + Secrets 管理

**`SEOContextV1` 升版策略**（現凍結 V1）：

- 增加 **optional** 欄位（`field | None = None`）→ minor change，消費端不需要改
- 刪除 / 重命名 / 改 type → **major change**，必須升 `schema_version: Literal[2]`；`seo-keyword-enrich` 雙寫（V1 + V2）過渡期至少一週；consumer（`compose.py` / `seo-optimize-draft`）支援 N-1，看 `schema_version` dispatch
- 欄位語義變更（同名但規則不同）→ 同 major change 處理
- 所有升版同步修 `shared/schemas/publishing.py` + 對應 fixture test + 本 ADR 更新「schema change log」段落（後續 ADR 補）

**Secrets 管理**（與 ADR-008 §8 一致，在此補齊 ADR-009 專屬條目）：

| Key | 位置 | Scope | 備註 |
|---|---|---|---|
| GSC service account JSON | `/home/nakama/secrets/gsc-ga4-sa.json`（chmod 600）| 與 ADR-008 共用同一份 service account | ADR-009 Phase 1 若先於 ADR-008 Phase 2 上線，此 setup 必須先完成（見 Open Items #3） |
| `DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD` | `.env` | `seo-keyword-enrich` 用 | Basic auth；儲值 credits 不過期 |
| `PAGESPEED_INSIGHTS_API_KEY` | `.env` | `seo-audit-post` 用 | 免費；無 key 可跑但 rate limit 緊，programmatic 必備 |
| `GSC_PROPERTY_SHOSHO` / `GSC_PROPERTY_FLEET` | `.env` | skill 讀哪個 property | 格式 `sc-domain:shosho.tw`（domain property）|

所有 secrets 遵守 [feedback_no_secrets_in_chat.md] — 不入 log、不在 Slack 訊息、不在 ADR / PR / commit 展示實值；`.env.example` 只記 key name 與註解。

### D9. 模組 / 檔案規劃（ADR 層鎖邊界，不凍結內部實作）

**新增**：
- `.claude/skills/seo-audit-post/SKILL.md` + 內部 script
- `.claude/skills/seo-keyword-enrich/SKILL.md` + 內部 script
- `shared/gsc_client.py` — GSC OAuth + query wrapper（ADR-008 Phase 2 共用）
- `shared/pagespeed_client.py` — PageSpeed Insights thin wrapper
- `shared/dataforseo_client.py` — DataForSEO Labs thin wrapper（health filter 內建）
- `shared/seo_audit/` — deterministic check 模組（可拆 `metadata.py` / `headings.py` / `images.py` / `schema_markup.py`）

**修改**：
- `shared/schemas/publishing.py` — 加 `KeywordMetricV1` / `StrikingDistanceV1` / `CannibalizationWarningV1` / `SEOContextV1`
- `agents/brook/compose.py` — 加 `seo_context` 參數 + `_build_seo_block` helper

**不動**（明確劃界，避免 scope creep）：
- `.claude/skills/keyword-research/` — 已 production 凍結
- `agents/usopp/publisher.py` + `shared/seopress_writer.py` — SEOPress 寫入已 PR #101 完工
- `shared/schemas/external/gsc.py`（ADR-008 §2 凍結）— SEO solution 只讀它的 schema，不修改

---

## Consequences

### 正面

- **Health vertical 最佳化** — GSC 為主數據源，繞過 DataForSEO 對 Health 類別的 `search_volume` 隱藏限制
- **月成本 <$3 + 一次性 $50**（vs Ahrefs Lite $129/月 省 ~95%）
- **3 個 skill 皆可個別開源** — 符合 [feedback_open_source_ready.md]（輸入/輸出皆走 schema，無 Nakama 內部 coupling）
- **Brook compose opt-in** — `seo_context=None` 時與現有行為 byte-identical，不破對話式 flow
- **與 ADR-008 clean separation** — 觀測層（ADR-008）/ 寫作層（ADR-009）介面清楚，透過 `shared/gsc_client.py` + `config/target-keywords.yaml` 互相 aware 但不耦合
- **SEOContextV1 pydantic frozen** → schema drift 早期 fail fast（跨 skill 串接最大風險點吸收掉）

### 負面 / Risk

- **GSC OAuth 設定一次性成本** — 修修手動步驟（property verify + service account + scope 授權），runbook 已在 ADR-008 §8 定義，ADR-009 沿用
- **DataForSEO $50 sunk cost** — credits 不過期但屬 sunk cost；若月用量極低 credits 可能用好幾年
- **跨 skill schema drift 風險** — `SEOContextV1` 任何變更都影響 3 個 skill + Brook compose。緩解：schema 走 `schema_version: Literal[1]` 並在 change 時同步升版 + 新增 fixture test
- **3 個 skill 觸發詞需維護** — 與既有 `keyword-research` 邊界要清楚；緩解：D7 `Do NOT trigger for` 區明確列出
- **Phase 1 GSC client 與 ADR-008 Phase 2 可能相互等待** — 緩解：ADR-009 Phase 1 自行在 `shared/gsc_client.py` 實作 interactive query；ADR-008 Phase 2 實作時 import 此 client 加 batched wrapper

### 風險 — 明確列出

1. **GSC API schema 變更**（Google 歷史多次微調） → `shared/schemas/external/gsc.py`（ADR-008 §2）已做 anti-corruption layer，ADR-009 透過此層消費，不直接解析 raw response
2. **DataForSEO Health restriction policy change**（可能變寬或變嚴） → 客戶端無法感知；緩解：收到 `search_volume=None` 時 log + 告警（不阻斷 skill 執行）
3. **Claude Sonnet API 成本飆升** → `seo-audit-post` 的 LLM semantic check 可配置 `--llm-level=haiku|sonnet|none`；skill frontmatter 預設 sonnet，CLI flag 可降級
4. **keyword-research frontmatter schema 未來變更** → 凍結當前 frontmatter 為 ADR-009 的合約輸入；若 keyword-research 要升版，需同步升 `SEOContextV1.schema_version`
5. **`_build_compose_system_prompt` 修改可能破壞 compose 測試** → Phase 1 實作 PR 必須跑 `tests/agents/brook/test_compose*.py` 全綠；為 `seo_context=None` 路徑加 regression test：固定 `StyleProfile` fixture → 兩次呼叫（一次 `seo_context=None`，一次 Phase 0 HEAD 的程式碼）→ 比對 system prompt exact string 必須 byte-identical。`seo_context` 非 None 路徑另加 snapshot test（固定 `SEOContextV1` fixture → 輸出 prompt 包含必要 SEO block 標記）。

---

## Alternatives Considered

| 替代方案 | Reject 理由 |
|---|---|
| **Ahrefs / Semrush subscription（$129-499/月）** | Sticker shock；大部分功能（backlinks / 競品 ranking tracking）對 single-blog use-case 溢出；Health vertical 的 health-restricted keyword 同樣被隱藏（主流工具皆受 Google Ads policy 影響） |
| **SurferSEO API 納入 Phase 1 內容 score 迴路** | 中文支援存疑、$99/月仍偏高、黑箱 score 不利 LLM-driven 優化；列 Phase 2 評估（在有更明確 A/B 測試需求前不納入） |
| **1 big skill `seo`（subcommand 風格）** | 違反 [feedback_skill_design_principle.md]「skill 粒度扁平」；內部 logic 混雜 testing 難；開源時需額外拆；三個 skill 觸發成本其實很低（frontmatter 自動路由） |
| **2 skill（audit + compose-with-data 混一起）** | `seo-compose-with-data` 變很重（enrichment + composing 混）；純 enrich（不寫稿）沒地方去；違反 single responsibility |
| **把 `keyword-research` 重做成 SEO research 一體化** | `keyword-research` 已 production 凍結且已被 Zoro / `agents/zoro` 消費；重做 = scope creep + 破壞向後相容；SEO solution 應為它的下游 enricher |
| **Subprocess 整顆 SEOmator（Node.js）** | 中文 / SEOPress / Health 客製需求高，subprocess 反而綁手；~25 條 Python check 更乾淨；開源時零 Node.js 依賴更輕量 |
| **全站 crawler（Unlighthouse / SiteOne 全量）** | 修修 use-case 是「單篇 audit」+「定期看 GSC」；全站 crawl overkill；ADR-008 Phase 2 的 weekly GSC digest 已覆蓋整站視角 |
| **直接裝 AgriciDaniel/claude-seo plugin** | 泛用設計，無 SEOPress / 中文 / Health vertical 整合；prior-art §1.4 已檢視 — 架構參考 OK、不直接使用 |
| **Phase 1 不含 cannibalization 偵測** | prior-art §6 第 8 點建議含（~50 行 Python，GSC 最高 ROI insight）；延遲無 justification |

---

## Open Items（留給實作階段）

以下項目在 ADR 層保持彈性，實作 PR 階段再決定：

1. **`seo-audit-post` 的 25 條 deterministic check 具體 rule set** — 實作 PR 從 SEOmator 251 rules 抽出相關子集 + 3-5 條台灣在地化（zh-TW 字型、繁簡混用、台灣藥事法 compliance SEO 不衝突）
2. **`seo-audit-post` output markdown 模板樣式** — 實作者決定；但必須有「pass/warn/fail 三色狀態」+「每條 check 的 actual vs expected vs fix」
3. **GSC service account 前置作業（修修手動，phase 1 blocker）**
   - **Reuse 既有**（2026-04-25 cleanup）：ADR-007 Franky 已建 `nakama-monitoring` GCP project + `nakama-franky@nakama-monitoring.iam.gserviceaccount.com` service account，授權 `sc-domain:shosho.tw` + `sc-domain:fleet.shosho.tw` 兩個 GSC property。env key `GCP_SERVICE_ACCOUNT_JSON` 既有 convention。runbook：[setup-wp-integration-credentials.md §2](../runbooks/setup-wp-integration-credentials.md)。**不要新建 GCP project / service account**（見 [feedback_prior_art_includes_internal_setup.md](../../memory/claude/feedback_prior_art_includes_internal_setup.md) 教訓）
   - SEO 專用 env key（修修在 `.env` 補）：`GSC_PROPERTY_SHOSHO=sc-domain:shosho.tw` / `GSC_PROPERTY_FLEET=sc-domain:fleet.shosho.tw`
   - ADR-009 skill 第一次啟動時 health check：呼叫 GSC API `sites.list()`，失敗則明確報錯指向 runbook（不默默 fallback）
4. **`shared/gsc_client.py` retry / rate-limit 策略具體實作** — 遵守 `reliability.md` §5（exponential backoff with jitter）；具體次數由實作 PR 測試決定
5. **Skill PR 切分順序** — 建議 Slice A: `SEOContextV1` schema + `shared/gsc_client.py` + GSC OAuth runbook；Slice B: `seo-keyword-enrich`；Slice C: `seo-audit-post`；Slice D: Brook compose `seo_context` opt-in。但此順序不在 ADR 凍結範圍
6. **Multi-model triangulation review — 已完成 2026-04-24（桌機）** — 跑了 Claude Sonnet 4.6 / Gemini 2.5 Pro / Grok 4 三家獨立 review。原始 artifacts 在 [docs/decisions/multi-model-review/ADR-009-seo-solution-architecture--{claude-sonnet,gemini,grok}.md](multi-model-review/)。整體可行性評分：Gemini 4/10「退回重寫」、Grok 6/10「修改後通過」、Claude Sonnet「修改後通過」。findings 彙整見下方「Multi-Model Triangulation Findings」新節

### prior-art §6 open questions 回答狀態（供交叉檢查）

| # | 原題 | ADR-009 決定 | §位置 |
|---|---|---|---|
| 1 | Skill 家族切法選 A/B/C？ | A（3 skill） | D1 |
| 2 | DataForSEO $50 儲值起步？ | 是（phase 1） | D2 |
| 3 | GSC API OAuth 先做？ | 是（phase 1 blocker） | D2 / Open Items #3 |
| 4 | `seo-audit-post` LLM 用 Sonnet/Haiku？ | Sonnet | D6 |
| 5 | `seo-optimize-draft` standalone 或 Brook mode？ | Standalone skill，內部 call compose | D1 / D5 |
| 6 | `SEOContextV1` phase 1 凍結？ | 是 | D3 |
| 7 | Cron-driven 整站 GSC 體檢 phase 1？ | 否，Phase 2（且合併 ADR-008 Phase 2 weekly digest） | D4 |
| 8 | Cannibalization 偵測 phase 1？ | 是（~50 行 Python，GSC 最高 ROI） | D3 / D4 |

---

## Multi-Model Triangulation Findings (2026-04-24)

桌機跑完 Claude Sonnet 4.6 / Gemini 2.5 Pro / Grok 4 三家。下面是 **actionable** 部分 — noise（客套話、一家獨吹且風險低）已濾掉。

### 三家共識 Blockers（兩家以上點名）

| # | Finding | 來源 | 建議處理 |
|---|---|---|---|
| T1 | **VPS 資源與延遲未 benchmark** — 2vCPU/4GB 跑 `seo-keyword-enrich`（GSC + DataForSEO + firecrawl + LLM 摘要 chain）的 P95 延遲 / OOM 風險沒實測數據 | Gemini、Grok、Claude (pitfall) | 先推進 Slice A（只 GSC，最輕量），Slice A 完工後 VPS dry-run 量 P95；> 30s 則啟動異步化評估（見 T7） |
| T2 | **Prompt injection via `competitor_serp_summary`** — firecrawl 爬的外部內容未消毒直接注入 compose system prompt | Claude、Gemini | 實作 `_build_seo_block` 時加 sanitization step（strip `<system>` / instruction pattern）；這是 Slice D Brook 整合的 review 必查項 |
| T3 | **Cross-skill schema drift 緩解不足** — `schema_version: Literal[1]` 只防 V2 物件被 V1 讀，不防 consumer 讀到 `None` 欄位後邏輯炸；三 skill + Brook compose 同時要改的 major change 風險被低估 | Claude、Gemini、Grok（三家共識）| Slice D 加「consumer defensive check」pattern（`if seo_context and seo_context.field is None: fallback`）；Open Items 新增「V2 migration playbook」（ADR Phase 2） |
| T4 | **Phase 1 範疇過大** — `seo-audit-post` + `seo-keyword-enrich` + Brook 整合三件事 2-3 週不現實 | Gemini（最嚴，「退回重寫」主因）、Claude、Grok | 縮範：**Phase 1 只做 `seo-keyword-enrich`（GSC only）+ Slice D Brook 整合**；`seo-audit-post` 與 DataForSEO 移 **Phase 1.5**（同一 ADR，不新開）；這個調整已反映到實作 Slice 順序（見下方 §Revised Slice Order） |
| T5 | **`host_to_target_site` mapping 放錯層** — business logic 混進 `shared/gsc_client.py` 違反 thin wrapper 原則 | Claude (D3 pitfall)、Gemini (D3 pitfall)、Grok | 搬到 `shared/schemas/site_mapping.py`，加窮舉 test：`set(map.keys()) == TargetSite.__args__`。Slice A 就要做對 |
| T6 | **`StrikingDistanceV1.current_position confloat(ge=10.0, le=21.0)` 容易誤用** — ADR 說「由 filter logic 決定」但沒明文規定 filter 順序，工程師容易先建 schema 後 filter → ValidationError | Claude (Pitfall 5)、Grok (實作坑 #1) | ADR D3 補一句：「GSC raw rows 必須在 skill 層先 filter 才建 `StrikingDistanceV1`；不符合 range 的 row 用 `drop` 而非 `ValidationError` retry」|

### 單家獨吹 — 值得記 Follow-up（非 Slice A blocker）

**Gemini 獨吹：**
- T7. `seo-keyword-enrich` 異步化策略（job_id + Slack 通知）— Phase 2 評估，依 T1 benchmark 結果觸發
- T8. 集中 rate limit / quota middleware — Phase 2，skill 數量 > 3 再做
- T9. `CannibalizationWarningV1` 的「什麼叫競爭」業務規則 ~50 行 Python 嚴重低估 — Slice B 實作時擴預估至 150-200 行 + threshold config

**Claude 獨吹：**
- T10. `SEOContextV1` → frontmatter 序列化的 float/datetime 往返精度問題 — Slice D 決定「存單一 `seo_context_json` 欄位 via `model_dump(mode="json")`」而非攤平 frontmatter
- T11. `_build_seo_block` 缺 token budget 控制（滿載 ~1500 tokens 放 system prompt 尾端）— Slice D 定義優先截斷順序（striking_distance > related_keywords > competitor_serp_summary）
- T12. `keyword-research` 輸出未定義 `KeywordResearchOutputV1` Pydantic schema — 獨立 Issue，不在 ADR-009 scope，記 backlog
- T13. GSC API quota 與 ADR-008 batch cron 共用 GCP project — ADR-008 Phase 2 實作 PR 時要設 quota alert

**Grok 獨吹：**
- T14. 3 個新 skill 觸發詞與既有 agent（Zoro / Brook chat）是否衝突 — Slice A/B/C 開 PR 前 grep `.claude/skills/*/frontmatter` 交叉檢查
- T15. `secrets/*.json` 權限 chmod 600 — 已在 ADR §Secrets 寫明，Grok 點出是 reminder 無新 signal

### Revised Slice Order（依 T4 調整）

原 Open Items #5 的順序（Slice A → B → C → D）保留 A/B/D，C 延到 1.5 階段：

| 原 | 改 | 內容 | 理由 |
|---|---|---|---|
| Slice A | Slice A（不變）| `SEOContextV1` schema + `shared/gsc_client.py` + `shared/schemas/site_mapping.py`（T5）+ GSC OAuth runbook | 核心基建 |
| Slice B | Slice B（內容收斂）| `seo-keyword-enrich`（**只 GSC 來源**；DataForSEO 延後）+ `CannibalizationWarningV1`（T9 增預估）| 先驗證核心 pipeline 延遲（T1） |
| Slice C | **Slice 1.5**（延後）| `seo-audit-post` + DataForSEO 整合 | T4 共識 — Phase 1 範疇太大 |
| Slice D | Slice C（序號前移）| Brook compose `seo_context` opt-in + sanitization（T2）+ token budget（T11）+ serialization（T10）| 延遲評估依賴 Slice B 完成才能量 |

### Grok review 品質評估

Grok 回應 33 行（Claude 228 / Gemini 129），六個 section 結構完整、評分清楚，點出 2 個共識 blocker + 1 個反向信號（DataForSEO $50 sunk cost 被高估，是三家唯一對 ADR 樂觀的角度）。深度不及 Claude/Gemini，但作為「第三票確認」有效。**不重跑**。

### ADR 層改動 vs 實作層處理

- **ADR 現在要改**：T5（mapping 搬家）、T6（filter 順序）→ 在 D3 / Open Items #3 補說明
- **Slice A PR 要注意**：T5 + T6（Slice A 就會動到）
- **Slice B PR 要注意**：T1 benchmark（Slice B 完成後量 P95）、T9（擴 Cannibalization 預估）
- **Slice C（原 D）PR 要注意**：T2 sanitization + T10 serialization + T11 token budget
- **Phase 2 backlog**：T3 V2 migration playbook、T7 異步化、T8 rate limit middleware、T13 quota alert

**這個 triangulation 的結論沒有 overriding 的 blocker 要求 ADR 退回重寫** — Gemini 4/10 的主因 T4（Phase 1 範疇）已由上方 Revised Slice Order 吸收，剩下 5 個共識 blocker 都能在 ADR 補說明 + 實作 PR review 覆蓋。

---

## References

### Internal

- [docs/research/seo-prior-art-2026-04-24.md](../research/seo-prior-art-2026-04-24.md) — 工具地景 + capability cards + §6 open questions
- [docs/decisions/ADR-008-seo-observability.md](ADR-008-seo-observability.md) — 觀測層（GSC / GA4 / Cloudflare）與本 ADR 共享 GSC client + target-keywords.yaml
- [docs/principles/schemas.md](../principles/schemas.md) — 所有 schema 決策援引
- [docs/principles/reliability.md](../principles/reliability.md) — retry / timeout 策略援引
- [docs/principles/observability.md](../principles/observability.md) — 日誌 / 告警援引
- [memory/claude/project_seo_solution_scope.md](../../memory/claude/project_seo_solution_scope.md) — 專案動機與三大用途
- [memory/claude/reference_seo_tools_landscape.md](../../memory/claude/reference_seo_tools_landscape.md) — 工具 API 契約坑備忘
- [memory/claude/feedback_skill_design_principle.md](../../memory/claude/feedback_skill_design_principle.md) — 三層架構 + skill 粒度
- [memory/claude/feedback_open_source_ready.md](../../memory/claude/feedback_open_source_ready.md) — 每個可開源單位要有 capability card

### Code integration points

- [agents/brook/compose.py:322](../../agents/brook/compose.py#L322) — `_build_compose_system_prompt`（D5 修改點 2）
- [agents/brook/compose.py:431](../../agents/brook/compose.py#L431) — `compose_and_enqueue`（D5 修改點 1）
- [agents/brook/compose.py:360](../../agents/brook/compose.py#L360) — `_build_user_request`（unchanged，SEO block 放 system prompt 不擠 user msg）
- [agents/usopp/publisher.py](../../agents/usopp/publisher.py) — 只讀；SEOPress 寫入不動
- [shared/schemas/publishing.py](../../shared/schemas/publishing.py) — `SEOContextV1` 落地位置
- [shared/schemas/external/gsc.py](../../shared/schemas/external/gsc.py)（ADR-008 §2） — 只讀，GSC row schema
- [.claude/skills/keyword-research/SKILL.md](../../.claude/skills/keyword-research/SKILL.md) — frontmatter schema 是 `seo-keyword-enrich` 的輸入合約

### External

- DataForSEO Help — [Health/Wellness SV/CPC restriction](https://dataforseo.com/help-center/sv-cpc-cmp-with-dataforseo-api)
- Google Search Console API — [searchanalytics.query reference](https://developers.google.com/webmaster-tools/v1/searchanalytics/query)
- PageSpeed Insights API — [v5 get-started](https://developers.google.com/speed/docs/insights/v5/get-started)
- Keyword cannibalization — [jcchouinard 實作範例](https://www.jcchouinard.com/keyword-cannibalization-tool-with-python/)
- JeffLi1993/seo-audit-skill — [Script + LLM 兩層架構 reference](https://github.com/JeffLi1993/seo-audit-skill)
