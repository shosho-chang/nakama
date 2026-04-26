---
name: SEO Slice E DataForSEO 不整合決定 2026-04-26
description: 決定不接 DataForSEO Labs；Health vertical fallback 高 + single-blog actionability 低 + GSC+firecrawl 已覆蓋；schema 保留 optional 欄位等未來 revisit
type: project
created: 2026-04-26
originSessionId: 74212ba2-600e-44ba-8bd2-fc0d89a62d6b
---
2026-04-26 SEO Phase 1.5 D.1 + D.2 + F merged 後評估 Slice E 必要性 → 決定**不實作**。

## TL;DR

DataForSEO Labs `keyword_difficulty` 對 Health & Wellness 創作者的決定性好處接近零：
- DataForSEO 自家 help-center 註明 health / financial / gambling 類 keyword 的 search_volume / CPC 會被 Google Ads policy hide
- 修修主要寫作 vertical 是 health → 大部分 query fallback 到「difficulty unknown」
- 替代方案 GSC striking-distance（自家網站真實流量）+ firecrawl SERP 摘要（top-3 競品內容）已 production，覆蓋核心 actionability
- difficulty score 對 single-blog 個人創作者影響極小：流量不靠單篇 #1 ranking，題目選定不會因 difficulty=80 就放棄
- $50 起儲值的 sunk cost 換不到對等價值

詳細決策依據與 What changes 見 [ADR-009 §Addendum](../../docs/decisions/ADR-009-seo-solution-architecture.md#addendum-2026-04-26--dataforseo-slice-e-不整合)。

## 三大用途映射（拿掉 E 後的終態）

| 用途 | 狀態 | 補完路徑 |
|---|---|---|
| 1. 內容創作建議（keyword-research） | ✅ **production** | — |
| 2. 既有部落格 SEO 體檢（seo-audit-post） | ✅ **production**（D.2 merged） | 修修瀏覽器跑 T1 production benchmark |
| 3. Brook compose 整合（寫稿吃 SEO 數據） | ✅ **production**（C opt-in + GSC + firecrawl 三件 ready） | — |

SEO Phase 1.5 = **完成**，無 outstanding sub-slice。

## Schema state

`KeywordMetricV1` 維持 Phase 1 凍結欄位：

```python
keyword: constr(min_length=1, max_length=200)
clicks: conint(ge=0)
impressions: conint(ge=0)
ctr: confloat(ge=0.0, le=1.0)
position: confloat(ge=1.0, le=100.0)
source: Literal["gsc", "dataforseo"] = "gsc"
keyword_en: str | None = None        # 永遠 None
search_volume: int | None = None     # 永遠 None
difficulty: int | None = None        # 永遠 None
```

`source: Literal["gsc", "dataforseo"]` 中的 `"dataforseo"` value 保留為 schema-level reservation（schema 不需升版；未來 revisit 時 zero-overhead 接回去）。

## Future revisit triggers

下列任一條件成真重新評估 DataForSEO（或同類 third-party difficulty 數據源）：

1. 修修跨入非 health 主題為主寫作 vertical（科技 / 商業 / 設計），且 Google Ads policy 對 health 類別解禁
2. 月 keyword 研究次數 > 50 且 GSC + firecrawl 兩源覆蓋顯著不足
3. 引入 `seo-optimize-draft` Phase 2 skill 後，發現 difficulty 數據對「下篇文章選題排序」有實質影響

## 影響的檔案（已同步更新）

- `docs/decisions/ADR-009-seo-solution-architecture.md` — Addendum + §D2 / §D8 / §Consequences inline 標記
- `docs/task-prompts/phase-1-5-seo-solution.md` — §0 表格 + §0.4 並行策略 + §E 開頭 deprecation notice（§E body 保留供歷史 reference）
- `.claude/skills/seo-keyword-enrich/SKILL.md` — description / Phase 1.5 status / cost / 下一步建議段
- `memory/claude/project_seo_phase15_pickup.md` — Slice E 標 cancelled / 三大用途映射
- `memory/claude/project_seo_d2_f_merged_2026_04_26.md` — 「near-production 缺 E」改 production
- `memory/claude/reference_seo_tools_landscape.md` — 加 Decision section
- `memory/claude/project_pending_tasks.md` — 移除 ⬜ E
- `memory/claude/project_seo_solution_scope.md` — append 2026-04-26 decision

## 開始之前一定要看

- 本 memo
- [ADR-009 §Addendum](../../docs/decisions/ADR-009-seo-solution-architecture.md#addendum-2026-04-26--dataforseo-slice-e-不整合)
- [reference_seo_tools_landscape.md](reference_seo_tools_landscape.md) §Health vertical 紅線
