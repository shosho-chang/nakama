# ADR-012: Zoro vs Brook 職責分界 — 向外 / 對內

**Date:** 2026-04-29
**Status:** Accepted

---

## Context

ADR-001 line 38 預留了「SEO / Repurpose 功能未來可由 Brook 擴展承擔，或另立 Agent」的選項，當時未決。`CONTEXT-MAP.md` 後續加注「Zoro 承載 SEO 三件套（audit / enrich / 內容建議）」並與 ADR-009 § skill 切法一致；但隨著 SEO solution 落地，**audit / enrich 這兩件「對既有部落格內容做加工」的工作，與 Zoro 「向外偵察新主題」的核心職責產生 framing 摩擦**。實作層也已偷偷反映此摩擦：`shared/seo_audit/llm_review.py` 的 LLM call 已寫 `set_current_agent("brook")`，cost tracking 早已歸 Brook。

2026-04-29 grilling 收斂出哲學分界：

> **Zoro = 向外搜尋**（從外部世界拉情報回來）
> **Brook = 對內加工**（處理既有 / 已知內容並產出寫作素材或新版本）

依此分界：
- **keyword research → Zoro**（探索市場上的新主題與關鍵字 = 向外）
- **audit + enrich → Brook**（對既有部落格文章打分 + 改稿建議；為寫稿備 SEO context = 對內加工既有素材）

## Decision

正式啟動 ADR-001 line 38 預留的 Brook SEO 擴展選項。Zoro / Brook 邊界以「向外 / 對內」為 first-cut 哲學分界。SEO 三件套依此重分配，文檔層面同步對齊：

| Skill / Workflow | Owner | 理由 |
|---|---|---|
| `keyword-research` | Zoro | 從 SERP / Trends / Reddit / YouTube 拉情報 = 向外 |
| `seo-audit-post` | Brook | 對既有 URL 評分 + 改稿建議 = 對內加工 |
| `seo-keyword-enrich` | Brook | 為 compose 備 SEO context（資料給寫稿者用）= 對內加工 |
| `seo-optimize-draft`（Phase 2）| Brook | 既有 draft → 改寫 = 對內加工 |

## Consequences

- **不 supersede ADR-001**，是其開放選項的 activation
- ADR-009 skill 切法（3 個 skill）與 schema 不變；只是 ownership 文檔對齊
- `CONTEXT-MAP.md` 已於同日 inline 更新（Zoro / Brook bullet + 「SEO solution」名詞段）
- Implementation follow-up：`seo-keyword-enrich` 的 LLM call 同步補 `set_current_agent("brook")`（與 audit 一致；目前可能仍掛在預設或 zoro）
- Bridge UI 中控台採 topic-rooted `/bridge/seo`（與 `/bridge/drafts`、`/bridge/memory`、`/bridge/cost` 慣例一致），不掛 `/bridge/brook` 或 `/bridge/zoro` — 中控台會混合 Brook 的 audit 與 Zoro 的 keyword research，agent-rooted 會 mislead

## 未來 boundary discussion 可援引此 framing

當其他 agent 出現職責摩擦時，「向外 / 對內」可作為 first-cut lens：

- Robin（吸收外部 source 寫 wiki）vs Brook（對 wiki 內容寫稿）— Robin 偏向外吸收，Brook 偏對內加工
- Sanji（對外社群互動）vs Brook（產社群素材）— Sanji 偏對外，Brook 偏對內生產
- Franky（對外監控 RSS / news）vs Robin（對內整理 KB）— 同向外/對內對應

此 framing 不是僵化規則，是判斷起點；遇到模糊 case（如 Phase 2 的 `seo-optimize-draft` 結合 SEO 數據與 compose）時仍以個案判斷為準。
