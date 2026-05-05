---
name: PubMed digest 頂刊 blind spot 結構性原因 + 修法（PR #415）
description: PubMed RSS sort-by-date 必被高發表頻率刊物霸佔；單一 saved search 永遠抓不到 JAMA/Lancet/Nature 等旗艦本刊。修法 = MDPI/Frontiers blocklist + 頂刊白名單 eutils feed
type: project
created: 2026-05-05
---

# PubMed digest 頂刊 blind spot — 結構性原因 + 修法

修修觀察：近 4 週 vault digest 全是 Gut microbes / Nutrients / PloS one / Frontiers in public health 等中型刊物，**JAMA / Lancet / NEJM / Nature / Science / BMJ / Br J Sports Med 等旗艦本刊命中 = 0**。

## 結構性原因（不是 bug，是 RSS 設計）

PubMed saved search RSS = sort by `Date - Publication` desc + limit N（修修原本 120）。NCBI 不允許 RSS 端設 journal tier filter。

實測（2026-05-05）：
- 修修 saved search RSS limit=120 → **52 筆 (43%) 是 Nutrients 一刊**，MDPI 系合計 60%+
- 拉 limit=500 → 仍 **0 頂刊本刊**（Nutrients 52 / Frontiers in public health 26 / IJMS 17 / PloS one 15 / Gut microbes 14 / Medicine 11 / BMJ open 9 …）
- 用 keyword + tier whitelist 直接 esearch 同 4 週 → **頂刊本刊命中 ~800 篇**（JAMA 22 / Lancet 14 / NEJM 8 / Nature 7 / Science 6 / BMJ 17 / Cell 13 / Br J Sports Med 31 / Med Sci Sports Exerc 32 / Sports Med 10）

## 為什麼 MeSH 也不能解

MeSH-based query 會**惡化問題**：JAMA/Nature/Lancet 文章發表後常 1-3 週才被 NLM 完成 MeSH indexing；MDPI 等高發表頻率刊物 indexing pipeline 同樣慢但體量大。所以 MeSH-only saved search 抓到的都是「indexing 完的舊文」+「中型刊新文」，旗艦刊新文永遠在 indexing queue 排隊。

## 修法 = 兩條 orthogonal

**Negative filter（blocklist）**：`config/journal_blocklist.yaml` 列 MDPI 全系（30+ 刊）+ 部分 Frontiers（保留 immunology + endocrinology 修修指定）。`shared/journal_blocklist.py:is_blocked()` 在 dedup 後 hard-filter。Blocked PMID 仍 mark_seen 避免明天重複 fetch 同份垃圾。

**Positive expansion（eutils feed）**：`config/pubmed_feeds.yaml` 加 `type: eutils` feed，term 寫死 22 家頂刊白名單 + 主題 keywords。Code 動態組 last 14 天日期 + esearch (sort=pub_date) + efetch (XML parse abstract)。

兩條 feed 跨 source 去重（state.db scout_seen），LLM curate 看 union。

## 關鍵實作

- `shared/pubmed_client.py:efetch_abstracts()` — 新加；esummary 沒 abstract，eutils feed 必須走 efetch XML 解。處理 structured `<AbstractText Label="X">` / inline `<i>/<sub>` tags / `<CollectiveName>` author / `<MedlineDate>` fallback
- `shared/journal_blocklist.py:_normalize()` — `小寫 + & → and + 只留 a-z0-9`，跟 `journal_metrics._normalize_title()` 一致；不做 substring match（避免 "Sensors" 誤殺 "Sensors and Actuators"）
- `agents/robin/pubmed_digest.py:_fetch_eutils()` — esearch sort=pub_date（不是 default relevance）；retmax 截斷時拿到的是「最新」非「最相關」
- `_fetch_eutils` 動態日期 `since = today - days` Asia/Taipei；TZ vs PubMed UTC indexing 有 ≤1 天 boundary lag，因為 days=14 window 影響 < 0.1%

## How to apply

- 想加新 publisher block：append to `config/journal_blocklist.yaml`，下次 cron pick up（lru_cache 模組級，process restart 才 reload）
- 想加新頂刊：append `[TA]` 到 `pubmed_feeds.yaml` 的 term；要避免子刊已被 broad search 撈到的 dedup 浪費，可不重複加
- Reviewer M2 follow-up（未做）：`_fetch_eutils` 即使 PMID 全 already-seen 也付 esearch+efetch HTTP cost（約 2 req/cron），優化把 dedup 提到 esearch 後 efetch 前
- Reviewer test gap（未做）：CJK-only journal name normalize 成 "" 會 collide / efetch empty `<Abstract>` element / `<Author>` 只有 ForeName 路徑

## 驗收

明早 5:30 cron 看 `/var/log/nakama/robin_pubmed.log`：
- broad RSS 120 + eutils 80 = 200 候選
- blocklist 預期過 ~70-90 筆 MDPI/Frontiers
- 剩 ~110-130 筆進 curate
- selected 12-15 篇，**頂刊本刊應有 1-3 篇**（過去 15 天 = 0 篇）

## 相關 PR / 起點

- PR #415 — feature
- 起點：5/5 incident（prefix bug + Anthropic key 401）修完後修修觀察期刊偏差
