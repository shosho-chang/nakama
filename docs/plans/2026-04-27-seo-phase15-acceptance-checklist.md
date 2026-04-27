# SEO Phase 1.5 Production Acceptance — 修修瀏覽器手動測試手冊

**Date**: 2026-04-27
**Scope**: 補完 SEO Phase 1.5 三件 production benchmark / smoke / cost-emit 驗證
**Owner**: 修修（手動 browser session）→ Claude（測完回報後分析）

---

## TL;DR

跑完這份 checklist，SEO Phase 1.5 = 真正落地。三件互有依賴：

| # | 任務 | 預估時間 | 依賴 |
|---|---|---|---|
| **T1** | `seo-keyword-enrich` 5-keyword wall-clock benchmark | ~25-40 min | 無（最先跑） |
| **T2** | Brook compose `seo_context` 端到端 smoke | ~10 min | T1 至少 1 份 enriched.md |
| **T3** | `seo-audit-post` + `/bridge/cost` cost-emit 驗證 | ~10 min | 無（可與 T1/T2 並行） |

**順序建議**：T1（最久，先啟動）→ T3（中間 fill）→ T2（最後吃 T1 結果）。

跑完把每段「回報模板」填好回給我，我據此：
- T1 → 更新 `seo-keyword-enrich/SKILL.md` cost section 的 `~15-25s` 估值為實測 P95
- T2 → 確認 Slice C + F 第一次合演 production 健康
- T3 → 確認 A3 follow-up（`ask_claude` wrapper）真的有 wire 到 cost tracking

---

## Pre-flight（先 5 分鐘做完，沒過後面別跑）

### P1. .env keys 齊全

從 repo root 跑：

```bash
.venv/Scripts/activate
python -c "
import os
from dotenv import load_dotenv
load_dotenv()
keys = ['PAGESPEED_INSIGHTS_API_KEY', 'GCP_SERVICE_ACCOUNT_JSON',
        'GSC_PROPERTY_SHOSHO', 'FIRECRAWL_API_KEY', 'ANTHROPIC_API_KEY']
for k in keys:
    v = os.getenv(k)
    print(f'{k}: {\"OK\" if v else \"MISSING\"}')
"
```

5 個全 OK 才繼續。任何一個 MISSING：
- `PAGESPEED_INSIGHTS_API_KEY` / `GCP_SERVICE_ACCOUNT_JSON` / `GSC_PROPERTY_SHOSHO` → `docs/runbooks/setup-wp-integration-credentials.md`
- `FIRECRAWL_API_KEY` → `https://www.firecrawl.dev/` 帳號 dashboard
- `ANTHROPIC_API_KEY` → console.anthropic.com

### P2. quota 心算

- **GSC quota**：200 req/day。本 checklist 用 5 (T1) + 1 (T3) = **6 query**，零壓力
- **firecrawl quota**：free tier 500 credit/月。每 enrich = 1 search + 3 scrape = 4 credit。T1 用 **20 credit**。看一下 firecrawl dashboard 剩多少
- **PageSpeed Insights**：API key free quota（25k/day），1 audit
- **Anthropic**：T1 用 Haiku 4.5（~$0.005-0.011/run × 5 ≈ $0.05），T3 用 Sonnet 4.6（~$0.03/run），T2 走 Sonnet compose（~$0.05-0.10），總計約 **$0.20**。先看 `/bridge/cost` 今日 baseline 數字記下來

### P3. 確認 dev server 沒在 reload 狀態

如果你開著 `uvicorn --reload` 跑 thousand_sunny，T2/T3 期間 git/file 不要動，避免 mtime 觸發 reload（per `feedback_shared_tree_devserver_collision.md`）。

---

## T1 — `seo-keyword-enrich` 5-keyword wall-clock benchmark

**目的**：量真實 P95 wall-clock 取代 SKILL.md 的估值 `~15-25s`。

**5 個 keyword**（health vertical 真實混合：基礎 / 入門 / 高難度 / 競爭激烈 / 廣域）：

| # | Keyword | 為什麼選 | 已知 GSC 狀態 |
|---|---|---|---|
| 1 | zone 2 訓練 | 修修主力寫過、有真實 GSC 資料 | shosho.tw 有 ranking |
| 2 | 慢跑入門 | 大眾入門詞、SERP 競爭激烈 | 不確定 |
| 3 | 重訓 | 廣域詞、SERP 內容類型混合 | 不確定 |
| 4 | 睡眠 | 大眾搜尋詞、health vertical 旗艦 | 不確定 |
| 5 | 蛋白質 | 補充劑邊緣議題、Taiwan compliance 風險 | 不確定 |

### 步驟（每個 keyword 重複）

每個 keyword 兩步：先 `keyword-research` 產 base markdown，再 `seo-keyword-enrich` 加上 GSC + SERP。

#### Step 1 — keyword-research（產 base markdown）

```bash
# 例：zone 2 訓練
python -m scripts.run_keyword_research "zone 2 訓練" \
    --content-type blog \
    --out "KB/Research/keywords/zone-2-訓練-2026-04-27.md"
```

預期：~30-60s，產一份 markdown，stderr 印 `sources_used=N sources_failed=M`。

#### Step 2 — seo-keyword-enrich（量這步 wall clock）

```bash
# 注意 time 是計時的關鍵
time python .claude/skills/seo-keyword-enrich/scripts/enrich.py \
    --input "KB/Research/keywords/zone-2-訓練-2026-04-27.md" \
    --output-dir "KB/Research/keywords/"
```

`time` 在 git-bash / bash 都會印 `real Xm Y.Zs`。記下 `real` 那欄。

或直接看 stdout，enrich.py 自己會印 `完成！耗時 X.Xs`。**這個數字就是 T1 要的 wall-clock**。

### 紀錄欄位（5 row）

跑完 5 keyword，填這張表回給我：

```
| # | keyword       | wall_clock_s | phase                       | striking | canniba | serp_chars | 失敗原因 |
|---|---------------|-------------:|-----------------------------|---------:|--------:|-----------:|----------|
| 1 | zone 2 訓練   |              | 1.5 (gsc + firecrawl)       |          |         |            |          |
| 2 | 慢跑入門      |              |                             |          |         |            |          |
| 3 | 重訓          |              |                             |          |         |            |          |
| 4 | 睡眠          |              |                             |          |         |            |          |
| 5 | 蛋白質        |              |                             |          |         |            |          |
```

欄位來源（在 enriched-*.md 檔案內找）：
- `wall_clock_s` — `time` 結果或 stdout 的「完成！耗時 X.Xs」
- `phase` — frontmatter `phase:` 欄位
- `striking` — JSON block `striking_distance` 陣列長度
- `canniba` — JSON block `cannibalization_warnings` 陣列長度
- `serp_chars` — JSON block `competitor_serp_summary` 字串字數（沒摘要的話寫 `null`）
- `失敗原因` — phase 不是 `1.5 (gsc + firecrawl)` 的話寫一句話原因（quota/network/parse error）

### 驗收

- ✅ 5 個都跑完（即使 phase 降級到 `serp-skipped` 也算過）
- ✅ 至少 3/5 phase = `1.5 (gsc + firecrawl)`（少於 3 個 → firecrawl 端有問題，要追）
- ✅ wall clock 中位數 ≤ 30s（>30s 異常，stderr 找原因）

### 失敗排查

| 症狀 | 可能原因 | 怎麼處理 |
|---|---|---|
| `phase: "1.5 (gsc + serp-skipped)"` | firecrawl quota 用光 / network / Haiku 失敗 | 看 stderr，繼續跑下一個（不擋整個 T1） |
| `phase: "1 (gsc-only)"` 但沒下 `--no-serp` | enrich.py 內部判斷 firecrawl 不可用 | 同上 |
| GSC 0 row | shosho.tw 該 keyword 沒 ranking 過 | 預期；只有 zone 2 訓練保證有 |
| stderr `PageSpeedCredentialsError` | 不應該（enrich 不用 PageSpeed） | 看是不是 import error 跑錯 script |
| stderr `auth.AuthorizedHttp not found` | google-auth 版本問題 | `pip install -U google-auth-httplib2` |

---

## T2 — Brook compose `seo_context` 端到端 smoke

**目的**：第一次讓 Slice C（compose 整合）+ Slice F（SERP 摘要）合演，confirm `seo_block` 渲染 + `competitor_serp_summary` 進到 system prompt + DraftV1 寫入 approval_queue。

**前置**：T1 至少 1 份 enriched.md 完成（建議用 #1 zone 2 訓練，因為有真實 GSC 數據）。

### 步驟

#### Step 1 — 從 enriched.md 解 SEOContextV1

進 Python REPL（從 repo root）：

```bash
.venv/Scripts/activate
python
```

```python
from pathlib import Path
import re
from shared.schemas.publishing import SEOContextV1

enriched_path = Path("KB/Research/keywords/enriched-zone-2-訓練-20260427.md")
text = enriched_path.read_text(encoding="utf-8")

# 抽 ## SEOContextV1 (JSON) 區段
m = re.search(r"## SEOContextV1 \(JSON\)\s*\n```json\n(.*?)\n```", text, re.DOTALL)
assert m, "找不到 SEOContextV1 JSON block"

seo_context = SEOContextV1.model_validate_json(m.group(1))
print(f"target_site={seo_context.target_site}")
print(f"primary={seo_context.primary_metric.keyword if seo_context.primary_metric else None}")
print(f"striking={len(seo_context.striking_distance)} canniba={len(seo_context.cannibalization_warnings)}")
print(f"serp_summary chars={len(seo_context.competitor_serp_summary or '')}")
```

預期 print 4 行（target_site / primary / counts / serp_summary chars）。

#### Step 2 — compose_and_enqueue

接續同個 REPL：

```python
from agents.brook.compose import compose_and_enqueue

result = compose_and_enqueue(
    topic="zone 2 訓練的常見問題",
    category="science",
    target_site="wp_shosho",
    seo_context=seo_context,
    core_keywords=["zone 2", "心率訓練", "有氧訓練"],
)
print(f"queue_row_id={result['queue_row_id']}")
print(f"draft_id={result['draft_id']}")
print(f"operation_id={result['operation_id']}")
print(f"title={result['title']}")
print(f"compliance_flags={result['compliance_flags']}")
print(f"tag_filter_rejected={result['tag_filter_rejected']}")
```

預期：
- 不 raise
- queue_row_id / draft_id / operation_id 都有值
- title 包含「zone 2」或同義詞
- compliance_flags 多半 false（zone 2 不碰 compliance 雷區）

#### Step 3 — Bridge UI 驗

打開瀏覽器：

1. **`/bridge/drafts`** — 看 pending 區應該多一個 `zone 2 訓練的常見問題` row（status=`pending`，剛剛 enqueue 的）
2. 點進該 row 看 detail 頁：
   - **payload_pretty** 應該有完整 DraftV1（AST blocks）
   - **看內文有沒有捏造數字**：例如「研究指出 87% 的人...」這種沒在 SEO context 提供的數字。LLM 應該主要用 striking-distance keyword 做語意 anchor，**不該自己編造統計**

### 紀錄欄位

填這張回我：

```
T2 結果：
- queue_row_id: ___
- draft_id: ___
- title: ___
- compliance_flags 有 true 的嗎: yes/no（哪幾個）
- detail 頁 payload 看起來像「真的吃了 SEO 數據」嗎: yes/no（一句話描述）
- 有看到捏造數字嗎: yes/no（有的話貼一段）
- enriched.md 用的是哪一份: filename
```

### 驗收

- ✅ compose_and_enqueue 不 raise
- ✅ /bridge/drafts 看得到新 row
- ✅ payload 內 striking-distance 有體現在文章 anchor / 段落主題（看到至少 2 個 striking keyword 出現在內文或標題）
- ✅ 沒捏造未提供的統計數字

### 失敗排查

| 症狀 | 可能原因 | 怎麼處理 |
|---|---|---|
| `model_validate_json` 報 SchemaError | enriched.md 格式異常 | 看 JSON block 內容；T1 重跑 |
| `compose_and_enqueue` raise `無法自動判斷文章類別` | detect_category 對 zone 2 沒 match | 強制 `category="science"`（已寫在範例） |
| `compose_and_enqueue` raise tag_filter | profile 預設 tag 全被擋 | 把 tag_filter_rejected 貼來，不擋 T2 整體驗收 |
| /bridge/drafts 看不到 row | enqueue 沒成功 / dev server 沒 reload | 重新整理頁面；ssh VPS 跑 `journalctl -u thousand-sunny -n 20` |
| 內文捏造數字 | LLM hallucination | 記下；這是 Slice C+F 第一次合演的觀察項，不擋 T2 通過，但要回報 |

---

## T3 — `seo-audit-post` + `/bridge/cost` cost-emit 驗證

**目的**：跑一次 audit，驗 PR #192 fix（A3 — `llm_review.py` + `kb_search.py` 改用 `ask_claude` wrapper）真的有 wire 到 cost tracking。

**Why**：之前（PR #183 merge 時）這兩個檔案直接 call Anthropic SDK 跳過 wrapper → cost 沒 emit。PR #192 改成 `ask_claude` 後，**audit 跑完 `/bridge/cost` 應該看到 today total 增加 ~$0.03-0.04**。如果還是 0 → A3 fix 沒生效，要再追。

### 步驟

#### Step 1 — 記下 baseline

打開 `/bridge/cost`，記下：

```
T3 baseline (跑 audit 前):
- timestamp: ___
- today total: $___
- by-agent: brook=$___, robin=$___, ... （主要看 brook 因為 audit 走 brook agent）
```

#### Step 2 — 跑 audit

```bash
.venv/Scripts/activate
python .claude/skills/seo-audit-post/scripts/audit.py \
    --url "https://shosho.tw/blog/zone-2-common-questions/" \
    --output-dir "KB/Research/seo-audit/" \
    --focus-keyword "zone 2 訓練"
```

預期：
- ~30-60s wall clock
- stdout 印 `完成！耗時 X.Xs`
- 報告寫到 `KB/Research/seo-audit/audit-zone-2-common-questions-20260427.md`

#### Step 3 — 看報告完整性

打開報告檔（用 Obsidian 或編輯器），確認 7 個 section 都在：

1. ✅ **§1 Metadata 檢查（M1-M5 / O1-O4）**
2. ✅ **§2 Headings 結構（H1-H3）**
3. ✅ **§3 Images alt 與壓縮（I1-I3）**
4. ✅ **§4 內部結構（S1-S3）**
5. ✅ **§5 Schema markup（SC1-SC4）**
6. ✅ **§6 PageSpeed（P1-P3）**
7. ✅ **§7 LLM 語意檢查（L1-L12）**
8. ✅ **§8 GSC ranking 摘要**（28 day）— shosho.tw 應該有資料
9. ✅ **§9 KB internal-link 建議**（Robin 跑 search_kb purpose=seo_audit）

每個 rule 都有 `status: pass/warn/fail/skip` + `fix_suggestion`。

#### Step 4 — 看 /bridge/cost increase

跑完 audit 等 ~30s（cost log 寫入有 buffer），重新整理 `/bridge/cost`：

```
T3 after audit:
- timestamp: ___
- today total: $___（baseline + ?）
- delta: $___
- brook (or robin?) 增加多少: $___
```

**A3 fix 驗收**：
- ✅ today total 有看到 increase ≥ $0.02 → A3 fix 生效（Sonnet 12-rule batch + Haiku KB ranker 都有 emit）
- ❌ today total 沒變 / 增加 < $0.005 → A3 fix 沒生效，要追：`shared/seo_audit/llm_review.py` 跟 `agents/robin/kb_search.py` 是不是真的走 ask_claude wrapper

### 紀錄欄位

填這張回我：

```
T3 結果：
- 報告 grade (overall): A/B+/B/C+/C/D/F
- pass/warn/fail/skip 數: P=__ W=__ F=__ S=__
- critical fail rule_id: [...]
- §8 GSC: included / skipped (原因)
- §9 KB: included with N suggestions / skipped (原因)
- 報告 wall clock: __ s

A3 cost emit 驗證：
- baseline today total: $___
- after audit today total: $___
- delta: $___
- brook agent delta: $___
- 結論: A3 fix 生效 / 沒生效（一句話）
```

### 驗收

- ✅ 報告 7 section 都在（§8 / §9 可能 skip 但要有 skip 原因 line）
- ✅ /bridge/cost today total 增加 ≥ $0.02
- ✅ 沒看到 stderr ERROR

### 失敗排查

| 症狀 | 可能原因 | 怎麼處理 |
|---|---|---|
| stderr `PageSpeedCredentialsError` | `.env` 沒 PAGESPEED_INSIGHTS_API_KEY | P1 沒做完 |
| §7 LLM 全 skip | ANTHROPIC_API_KEY 沒設 / `--llm-level=none` | 拿掉 flag 重跑 |
| §8 GSC skipped (non-self-hosted) | URL host 不在 site_mapping | 預期外（shosho.tw 應該 in） |
| /bridge/cost 沒看到 increase | A3 fix 沒生效 | **這是要回報的關鍵異常**，貼 stderr 全文 |
| 報告 grade=F | 文章本身 SEO 結構差 | 預期可能；報告本身能跑就算 T3 通過 |

---

## 完整回報模板

跑完 T1+T2+T3 把這份貼回對話：

```markdown
# SEO Phase 1.5 Acceptance — 跑測結果

## T1 (5-keyword benchmark)

| # | keyword       | wall_clock_s | phase                       | striking | canniba | serp_chars | 失敗原因 |
|---|---------------|-------------:|-----------------------------|---------:|--------:|-----------:|----------|
| 1 | zone 2 訓練   |              |                             |          |         |            |          |
| 2 | 慢跑入門      |              |                             |          |         |            |          |
| 3 | 重訓          |              |                             |          |         |            |          |
| 4 | 睡眠          |              |                             |          |         |            |          |
| 5 | 蛋白質        |              |                             |          |         |            |          |

mean=__s median=__s P95=__s range=__s-__s

## T2 (Brook compose smoke)

- enriched.md 用: ___
- queue_row_id: ___
- draft_id: ___
- title: ___
- compliance_flags 有 true: yes/no (哪幾個: ___)
- detail 頁吃 SEO 數據像不像: yes/no (描述: ___)
- 捏造數字: yes/no (例: ___)

## T3 (seo-audit + cost emit)

報告：
- grade: ___
- P/W/F/S: __/__/__/__
- critical fail: ___
- §8 GSC: ___
- §9 KB: ___ (N suggestions: ___)
- wall clock: __s

A3 cost emit 驗證：
- baseline today total: $___
- after audit: $___
- delta: $___
- brook delta: $___
- 結論: ___

## 整體
- 三件全綠 / 哪件失敗（描述）
- 異常觀察：___
```

---

## 完成後我會做的事

| 結果 | 我會 |
|---|---|
| T1 mean/P95 數字 | 改 `seo-keyword-enrich/SKILL.md` `~15-25s` 為實測值 |
| T1 phase 降級率 | 若 >40% 降級 → 開 firecrawl 排查 follow-up |
| T2 compose 端到端 OK | 把 `project_seo_d2_f_merged_2026_04_26.md` 修修待辦 #2 標 ✅ |
| T2 捏造數字 | 開 follow-up：tighten compose prompt 約束 LLM 不編造數字 |
| T3 cost emit 有 increase | 把待辦 #3 標 ✅，PR #192 A3 fix confirm production |
| T3 cost emit 沒 increase | 開緊急 follow-up：grep `ask_claude` import + cost_tracker call 路徑 |
| 三件全綠 | 更新 MEMORY.md SEO 段落 → SEO Phase 1.5 終態 ✅ |

---

## 開始之前一定要看

- 本 doc
- [.claude/skills/seo-keyword-enrich/SKILL.md](../../.claude/skills/seo-keyword-enrich/SKILL.md) — T1 工具
- [.claude/skills/seo-audit-post/SKILL.md](../../.claude/skills/seo-audit-post/SKILL.md) — T3 工具
- [agents/brook/compose.py](../../agents/brook/compose.py) line 444 `compose_and_enqueue` signature — T2 API
- [memory/claude/project_seo_d2_f_merged_2026_04_26.md](../../memory/claude/project_seo_d2_f_merged_2026_04_26.md) — 修修待辦三件事原文
