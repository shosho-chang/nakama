---
name: Zotero 整合路線（訂閱期刊全文）
description: Nature/Cell/NEJM 等訂閱期刊全文取得走 Zotero，不自己造 publisher login
type: project
---

修修決定：訂閱期刊（Nature / Cell / NEJM 等付費內容）的全文取得走 Zotero 整合路線，nakama 不自己造 publisher login。

**Why:** 自己做 publisher login 三大障礙：(1) 每家 auth flow 不同（個人訂閱 cookie / 機構 Shibboleth / OpenAthens SSO），(2) ToS 普遍禁止自動化批次存取，cron 12 篇/天必觸發 bot detection 風險訂閱帳號被停用，(3) cookie renew + session 維護長期 eng cost。Zotero 把 login/ToS 風險轉嫁給「用戶 browser session」，nakama 只 consume Zotero local library。

**How to apply:** 
1. 討論到訂閱期刊全文 / Nature / NEJM / BMJ 付費內容 → 指向 Zotero 方案，不要提議 publisher login 自動化
2. 尚未動工，Phase 規劃（等開工時細化）：
   - Phase A：修修裝 Zotero desktop + browser connector，手動點 "Save to Zotero" 進 library
   - Phase B：nakama 讀 Zotero SQLite (`~/Zotero/zotero.sqlite`) 或 Zotero Web API (`api.zotero.org/users/{uid}`)，抽 metadata + attached PDF 路徑
   - Phase C：扁平化 Zotero storage 進 vault（`KB/Attachments/zotero/{itemKey}.pdf`）+ frontmatter 帶 DOI / 作者 / Zotero collection
   - Phase D：既有 Robin PubMed digest 整合 — 若 PMID 在 Zotero library 裡找到，優先用 Zotero 的 PDF 而非走 PMC/publisher fallback
3. Crossref TDM API 可作次要選項（合法 bulk，但需機構授權或個人 key 申請）

**相關**：PR #94 已上線 publisher HTML fallback（只抓 elink Free-標記的 OA 連結），涵蓋 BMJ/PLOS/eLife 等免費 OA 大站。訂閱內容靠 Zotero 補足。

---

## 升 primary + grill 凍結 2026-05-05

原本 framing 是「訂閱期刊 only 走 Zotero、OA 走 DIY URL scrape」。**2026-05-05 早 QA 實測 Stage 1 ingest 五層 OA fallback 全跑通但修修讀起來 quality 不滿意** → 戰略 pivot：**Zotero 升 primary ingest path**（OA + 訂閱都走），DIY URL scrape (PR #352-356) 重定位為 escape hatch。

同日 grill session 把 Phase B-F 全部「待 grill」拍板，10 個 architectural decision (Q1-Q10) 凍結。詳見：

- [project_zotero_integration_grill_2026_05_05.md](project_zotero_integration_grill_2026_05_05.md) — Q1-Q10 凍結 + MVP slice 切法 + Phase 2 backlog
- [project_session_2026_05_05_zotero_pivot.md](project_session_2026_05_05_zotero_pivot.md) — pivot 戰略決定來源
- [feedback_dont_recompete_on_capture_quality.md](feedback_dont_recompete_on_capture_quality.md) — 「不跟 Zotero 在 capture quality 重競」戰略 lesson
- [docs/decisions/ADR-018-zotero-as-primary-ingest-path.md](../../docs/decisions/ADR-018-zotero-as-primary-ingest-path.md) — pivot 凍 ADR
- [docs/decisions/ADR-019-two-file-source-ingest-pattern.md](../../docs/decisions/ADR-019-two-file-source-ingest-pattern.md) — 兩檔 ingest pattern ADR
- [agents/robin/CONTEXT.md](../../agents/robin/CONTEXT.md) — Robin context 詞彙表 lazy-create
