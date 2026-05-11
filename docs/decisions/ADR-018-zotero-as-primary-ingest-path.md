# ADR-018: Zotero as Primary Ingest Path

**Status:** Superseded (2026-05-06) — Web Clipper pivot
**Superseded reason:** 2026-05-06 evening QA 14 finding 後，Zotero 整合三 PR (Slice 1/2/3) 全砍。Pivot 到 Obsidian Web Clipper（Chrome plugin）作為 canonical paper ingest 路徑 — 直接 publisher HTML 抓進 `Inbox/kb/`，無翻譯插件 DOM injection、structure 保留、化學記號 (`<sub>` / `<sup>`) verbatim。Zotero `zotero://` URI 路徑（含 attachment vs parent itemKey UX gotcha + Zotero 7 SingleFile 模式 `_assets/` 不存在等問題）放棄。詳見 `memory/claude/project_zotero_qa_2026_05_06_pivot_to_webclipper.md`.
**Date:** 2026-05-05
**Deciders:** shosho-chang
**Related:** ADR-019, PR #352-356 (Stage 1 ingest 重定位為 escape hatch), `agents/robin/CONTEXT.md`

---

## Context

Robin needs to ingest academic papers (Nature / Cell / Science / preprints) and long-form web articles (Substack / Medium / blog) into the KB. Two paths existed:

1. **DIY URL scrape** (PR #352-356, 5-layer OA fallback) — paste URL → trafilatura / readability / Europe PMC / publisher HTML / PDF parse → MD → Inbox.
2. **Zotero browser-extension** — 修修 saves to Zotero locally via browser-extension snapshot, then nakama syncs the snapshot into vault.

QA on Path 1 (2026-05-05 早) revealed quality gap: Stage 1 ingest 五層 fallback 全跑通了（Nature 文章從 Europe PMC 抓 PDF 解析 112K 字元，pipeline `status=ready`），但**修修讀起來 quality 不滿意**。PDF 解析劣化 + paywall + lazy load + JS render edge case 永遠補不完。

## Decision

**Zotero is primary ingest path** for academic papers and long-form articles. nakama does **NOT** compete with Zotero on web capture quality.

DIY URL scrape (PR #352-356) 重定位為 **escape hatch**（沒 Zotero 入口的內容才走，例如 YouTube transcript / podcast / 完全無法存 Zotero 的社群 post），不再投資擴充。

Sync mechanism 凍結為**直接讀 `~/Zotero/zotero.sqlite`**（copy-to-tmp 規避 lock），sync agent 落本機（Zotero desktop 那台）；不走 Zotero Web API（修修無 cloud sync，attachment library 進不了 free tier 300MB）。

## Considered Options

- **Path 1 only (DIY URL scrape)** — rejected. Quality bar 永遠贏不了 browser-extension snapshot：(1) browser session 解 paywall / (2) 全 DOM+CSS+asset / (3) 1000+ community translators / (4) ToS 風險轉嫁。DIY 三大不可克服劣勢：edge case 無止境 / PDF 解析劣化 / HTML scrape 受 paywall+lazy load 影響。
- **Path 2 with Zotero Web API** — rejected. 修修無 cloud sync，attachment library 進不了 free tier 300MB；rate limit 1 req/sec；Web API 「跨機器」優勢在 nakama 跑本機的場景下 0 ROI。
- **Hybrid: Zotero for subscription / DIY for OA** — rejected. 維持兩條 ingest path 的 cognitive cost 與 dispatcher 複雜度高，且 DIY 的 OA quality 仍劣於 Zotero snapshot。
- **沉浸式翻譯 Pro 取代自家 translator** — rejected (子 decision, grill Q9)。單篇 paper 短文無跨章節飄移，Pro Smart Context 邊際提升低；PR #354 bilingual format + ADR-017 annotation ref 對位鎖在自家輸出格式；自家 user_terms 自學 glossary 控制權無可替代。Pro 訂閱是修修 EPUB 書翻譯軸個人決策（PR #376 grill），跟 nakama Zotero 流程 decouple。

## Consequences

- **PR #352-356 (Stage 1 ingest 5 slice)** 不砍，重定位為 escape hatch；不再投資擴充。
- **PR #94 PubMed publisher HTML fallback** 仍是 PubMed digest cron 的後援（已自動化、不需 Zotero 介入）。
- **Sync agent 跑本機**（Mac/Windows，主 Win），不上 VPS。Syncthing 把 sync 完的 vault MD 推上去。
- **修修 Zotero workflow 不變**：browser-extension save / annotate / collection 結構修修自管，nakama 不污染。
- **`agents/robin/CONTEXT.md`** 詞彙表 freshly created with Zotero terms（lazy creation per CONTEXT-MAP.md 規定）。
- **MVP 觸發 = 貼 `zotero://select/...` link 單篇**（仿既有 URL ingest UX）；Phase 2 加 tag-based batch 與 cron 全 library auto-sync 都列 backlog。
