# Brook compose fact-check agent — design

**Date**: 2026-04-27
**Status**: design draft（修修 review 後決定要不要做）
**Trigger**: 修修 2026-04-27 SEO acceptance 看到 Brook compose 的 draft 引用 5 個期刊論文（specific 到年份 + 期刊名 + 數據），但這些**不在 SEO context 內**，是 LLM 從訓練資料憑記憶寫——hallucination 風險高，health vertical 嚴重 compliance 問題。

修修問：「能不能寫個 function 或派 agent 去查？」

答：**可以而且該做**。Building blocks 都在。

---

## TL;DR

`agents/brook/fact_check.py` — post-compose 跑，draft 進 pending 後在背景 fact-check 5 種引用 → 寫進 `approval_queue.fact_check_warnings` JSON 欄位 → `/bridge/drafts` detail 頁顯示「⚠️ N 條可疑引用」。

| 估算 | 值 |
|---|---|
| 工程量 | 1.5-2 day（含 schema migration + UI tweak + tests） |
| Per-draft 成本 | $0.01-0.02（Haiku 抽 + PubMed API free + 偶爾 firecrawl scrape） |
| Per-draft latency | +20-30s（背景跑，不擋 enqueue） |
| 既有依賴 | `shared/pubmed_client.py`（Robin 已有）、firecrawl client、Haiku 4.5 |
| 新依賴 | 無 |

---

## 為什麼會 hallucinate

Brook compose 的 LLM call 拿不到「來源資料」就被要求寫文章：

```python
compose_and_enqueue(
    topic='zone 2 訓練的常見問題',
    seo_context=seo_context,        # GSC ranking 數字 + SERP 摘要 — 沒研究內容
    core_keywords=['zone 2', ...],
    # source_content=??? — 沒餵
    # kb_context=??? — 沒餵
)
```

`source_content` / `kb_context` 是設計上要餵 KB 抽出來的研究筆記、PubMed digest、訪談逐字稿。但實際上：

- 修修這次 acceptance test 沒餵（為了驗 SEO pipeline）
- 平日 production 也 80% 沒餵（KB 沒系統化餵 compose）

LLM 寫到「2022《Journal of Applied Physiology》追蹤了 32 名訓練有素的自行車手 12 週，發現低強度訓練組... VO2 max 提升了 8.1%」時，這個具體數字 **可能存在**（Sonnet 4 訓練資料含真實 paper），**也可能不存在**（hallucination）。

我們無法在 compose 時知道哪個是哪個 → 需要**事後 fact-check**。

---

## 方案：post-compose fact-check agent

### Step 1 — 抽 paper claim（Haiku 4.5）

從 draft plaintext 抽出「具體期刊引用」結構化 list：

```python
# Haiku prompt：
# 從以下文章抽出所有「具體期刊論文」引用（含年份 + 期刊名 + 至少一個 finding）。
# 不要抽純常識（如 220-年齡公式）或 named author 沒附論文的（如「Iñigo San Millán 教授說...」）。

# 輸出 JSON list：
[
  {
    "year": 2022,
    "journal": "Journal of Applied Physiology",
    "claim_summary": "32 自行車手 12 週低強度訓練組 VO2 max +8.1% vs 高強度 +3.7%",
    "specific_findings": ["VO2 max +8.1%", "粒線體酵素 +25%"],
    "draft_excerpt": "...原文 50 字上下文..."
  },
  ...
]
```

成本：~$0.005（4500 in / 800 out）

### Step 2 — PubMed esearch 驗證

對每個 claim：

```python
# 用 shared.pubmed_client.PubMedClient.esearch
query = f'"{journal}"[Journal] AND {year}[Year]'
ids = client.esearch(query, max_results=20)

if not ids:
    # 期刊 + 年完全沒找到 → 高機率 hallucinate
    return "🔴 high — 期刊+年份在 PubMed 查無"

# Step 2b: 用 claim_summary 關鍵詞縮小範圍
query2 = f'"{journal}"[Journal] AND {year}[Year] AND ({extract_keywords(claim)})'
ids2 = client.esearch(query2, max_results=10)

if not ids2:
    return "🟡 medium — 期刊+年存在但找不到匹配 finding 的論文"

# Step 2c: 拉前 3 篇 abstract 驗 finding
abstracts = client.efetch(ids2[:3])
return llm_match(claim, abstracts)  # Haiku 比對是否吻合
```

成本：每 claim 0-2 PubMed call（free）+ 0-1 Haiku call（~$0.002）

### Step 3 — 非 PubMed 的 fallback

非 medical 期刊（《Sports Medicine》, 《Scandinavian Journal of Medicine & Science in Sports》部分文獻不在 PubMed）→ firecrawl scrape Google Scholar：

```python
url = f"https://scholar.google.com/scholar?q={quote(claim_summary)}"
md = firecrawl.scrape(url, formats=['markdown'])
# Haiku 看 scholar 結果有沒有 match
```

成本：~1 firecrawl credit + ~$0.002 Haiku

### Step 4 — 寫回 approval_queue

新增 column：`fact_check_warnings TEXT` (JSON)：

```json
{
  "schema_version": 1,
  "checked_at": "2026-04-27T03:00:00+00:00",
  "warnings": [
    {
      "level": "high",
      "claim": "2022 J Appl Physiol 32 自行車手 +8.1% VO2 max",
      "reason": "期刊+年存在，但 finding +8.1% 在 abstract 沒 match",
      "draft_excerpt": "2022 年《Journal of Applied Physiology》的研究追蹤了..."
    },
    ...
  ]
}
```

### Step 5 — Bridge UI 顯示

`/bridge/drafts` list page 加 column：「Fact-Check ⚠️ 3」(red) / 「✅」(green) / 「-」(skipped)。

Detail page 加 section「Fact-Check Warnings」：每條 warning 顯示 `level / claim / reason / draft_excerpt`（高亮原文位置）。Reviewer 可以直接 reject 或 edit。

---

## Schema migration

```sql
ALTER TABLE approval_queue ADD COLUMN fact_check_warnings TEXT;
ALTER TABLE approval_queue ADD COLUMN fact_check_status TEXT;  -- 'pending' | 'completed' | 'skipped' | 'error'
ALTER TABLE approval_queue ADD COLUMN fact_check_completed_at TEXT;
```

走 `shared/state.py` 既有 migration pattern。

---

## 觸發時機

兩條路：

**A — sync 在 compose_and_enqueue 內**（簡單）：
```python
result = compose_and_enqueue(...)
# enqueue 完直接 fact_check（同 process，~30s overhead）
fact_check_draft_async(draft_id=result['draft_id'])
```
- ✅ 簡單
- ❌ Brook compose latency 從 ~100s 變 ~130s（修修等 LLM 完已經很久）

**B — async via daemon**（complex）：
```python
# 1. compose_and_enqueue 寫 draft + status='pending'
# 2. 一個 fact-check daemon（systemd）每 1 min poll：
#    SELECT * FROM approval_queue WHERE fact_check_status IS NULL LIMIT 5
# 3. 對每個 draft 跑 fact_check + UPDATE
```
- ✅ 不擋 compose latency
- ❌ 需要新 systemd unit + cron / daemon loop（沿用 Usopp / Franky daemon pattern）

**推薦 A 先**（修修 review draft 通常是分鐘級延遲，+30s 不痛；B 等 daemon framework 成熟再升級）。

---

## Edge cases

1. **LLM 引用 named expert 但沒附 paper**（「Iñigo San Millán 教授說...」）— Step 1 prompt 寫明不抽，因為查不到也不算 hallucinate
2. **非英文期刊**（如《體育學報》）— PubMed 不收 → 走 Step 3 firecrawl scholar
3. **Generic claim**（「研究指出 80% 的人...」沒指期刊）— 不在 fact-check 範圍，但要 reviewer 警覺。可選 Step 0 用 Haiku 標記「unsourced quantitative claim」。
4. **PubMed quota** — 預設 3 req/sec，本機自帶 1 sec sleep。每 draft 平均 5 claim × 2 query = 10 req，沒壓力。
5. **Firecrawl quota** — Google Scholar fallback 才用，每 draft 平均 0-2 claim 走這條 → ~0-2 credit。free tier 500/月夠用。

---

## 開發順序（如果修修點頭做）

1. `shared/schemas/fact_check.py` — `FactCheckWarningV1` Pydantic schema（30 LOC）
2. SQLite migration — add 3 columns（10 LOC）
3. `agents/brook/fact_check.py` — `fact_check_draft(draft_id)` 主流程（150 LOC）
   - `_extract_claims_from_draft(plaintext)` — Haiku
   - `_verify_via_pubmed(claim)` — Robin pubmed_client reuse
   - `_verify_via_scholar(claim)` — firecrawl fallback
   - `_persist_warnings(draft_id, warnings)` — SQLite UPDATE
4. `agents/brook/compose.py` — `compose_and_enqueue` 結尾 call `fact_check_draft`（5 LOC）
5. `thousand_sunny/routers/bridge.py` — drafts list + detail 加 warnings column / section（80 LOC）
6. `thousand_sunny/templates/drafts.html` + `draft_detail.html` — render warnings（30 LOC）
7. Tests — `test_extract_claims` / `test_verify_pubmed_hit` / `test_verify_pubmed_miss` / `test_verify_scholar_fallback` / `test_persist_warnings` / `test_compose_triggers_fact_check`（150 LOC）

預估 ~500 LOC 工程 + ~200 LOC test = **1.5-2 day**。

---

## 跟既有 SEO compliance 的關係

`agents/brook/compliance_scan.py` 是不同的東西：擋台灣 pharma 雷區（治好 / 99.9% / 肝癌）。fact-check 是擋**錯誤的科學引用**，正交層次。compose 寫「醫師說維他命 D 治好憂鬱症」會被 compliance 擋；寫「2022《JAMA》研究 4500 人雙盲試驗發現 5000 IU 維他命 D 改善 PHQ-9 score 8.1 分」**不會**被 compliance 擋（沒踩 SEED），但**會**被 fact-check 擋（如果這個 paper 不存在）。兩層都需要。

---

## Open questions（修修 review 用）

1. **Sync vs async（A vs B）** — 推 A 先，後面要拉 daemon 再升 B
2. **要不要 catch generic claim**（「研究指出 X% 的人」沒指期刊）— 推不要先做（落到 reviewer 人眼），有需要再加 Step 0
3. **Fact-check fail 時的 default 行為** — 目前設計「flag 但不擋 publish」，reviewer 看到 warnings 自己決定。要不要改成 `level=high` 強制擋 publish？
4. **要不要做 Phase 2** — Phase 2: pre-compose 從 KB 抽真實研究數據餵 `source_content` → 從根源避免 hallucinate（治本，但工程更大）

修修點頭哪個 / 哪些做就開工。

---

## 開始之前一定要看

- 本 doc
- [docs/plans/2026-04-27-seo-phase15-acceptance-results.md](2026-04-27-seo-phase15-acceptance-results.md) — 觸發這份 plan 的 acceptance 結果
- [agents/brook/compose.py](../../agents/brook/compose.py) — compose pipeline reference
- [shared/pubmed_client.py](../../shared/pubmed_client.py) — Robin 既有 PubMed client（reuse）
- [agents/brook/compliance_scan.py](../../agents/brook/compliance_scan.py) — 不同層次的 SEED compliance 對照
