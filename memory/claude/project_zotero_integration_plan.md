---
name: Zotero 整合路線（primary ingest path）
description: Zotero 升 primary ingest path（OA + 訂閱都走）；要蓋的是 Zotero → Obsidian sync 這座橋，不重做 capture
type: project
updated: 2026-05-05
---

修修決定：**Zotero 是 primary ingest path**（OA + 訂閱期刊都走），nakama 不自己造 URL scrape / publisher login。

**2026-05-05 升 primary**（原本只框成「訂閱期刊 only」）：QA 實測 DIY URL scrape quality bar 永遠贏不了 Zotero browser snapshot；見 [feedback_dont_recompete_on_capture_quality.md](feedback_dont_recompete_on_capture_quality.md)。

## Why Zotero（不重做 ingest 的根本原因）

1. **既有工作流順暢**：開文章 → 沉浸式翻譯雙語對照 → Save to Zotero → Zotero annotate
2. **Zotero browser-extension snapshot 四大優勢 DIY 無法複製**：browser session 解 paywall / 全 DOM+CSS+asset / 1000+ community translators / ToS 風險轉嫁
3. **DIY URL scrape 三大不可克服劣勢**：edge case 無止境 / PDF 解析劣化 / HTML scrape 受 paywall+lazy load 影響
4. **訂閱期刊 publisher login 三大障礙**：每家 auth flow 不同（個人 cookie / Shibboleth / OpenAthens）/ ToS 普遍禁自動化 / cookie renew 長期 eng cost

## 真正要蓋的橋（不是 ingest）

修修現存兩大痛點：

- **痛點 A**：Zotero ↔ Obsidian sync 缺口 — 文章 + annotation 卡在 Zotero db 進不了 KB
- **痛點 B**：沉浸式翻譯 inject DOM → Save to Zotero 連污染版本一起存

**解法**：
- 痛點 A → 蓋 Zotero → Obsidian sync agent（本 plan 主軸）
- 痛點 B → workflow 解（先 save 後翻 / 浮窗模式 / 只存 PDF），不靠 code

## Phase 規劃

| Phase | 內容 | 狀態 |
|---|---|---|
| A | 修修裝 Zotero desktop + Connector，手動 Save to Zotero | **已完成**（修修長期使用） |
| B | nakama 讀 Zotero SQLite (`~/Zotero/zotero.sqlite`) 或 Web API (`api.zotero.org/users/{uid}`)，抽 metadata + attached PDF/snapshot 路徑 | **待 grill** |
| C | 扁平化 Zotero storage 進 vault：`KB/Attachments/zotero/{itemKey}.pdf` + frontmatter 帶 DOI / 作者 / Zotero collection | **待 grill** |
| D | annotation sync：Zotero highlight + note → KB（單向 MVP，雙向後續） | **待 grill** |
| E | 既有 Robin PubMed digest 整合：若 PMID 在 Zotero library 找到，優先用 Zotero PDF 而非走 PMC/publisher fallback | **待 grill** |
| F | Robin Reader 對 sync 進來的 clean MD 翻譯產獨立對照頁，**不污染原檔** — 直接 reuse Slice 3 (#354) 翻譯 + 雙語 reader | **架構已就位**（PR #354） |

## 待 grill 議題（下個 session）

- SQLite 直連 vs Zotero Web API（trade-off：local 快但綁機器 / API 跨機但有 rate limit）
- annotation sync 方向：MVP 單向 Zotero → KB 確定，雙向時機
- PDF → MD 轉檔策略（pymupdf4llm 已在 repo 有用）
- collection mapping → KB folder 結構（mirror Zotero collection 還是 KB own taxonomy）
- 增量 sync vs 全量；trigger（cron / 手動 / Zotero hook）
- prior art 評估：Better BibTeX、zotero2md、Obsidian Citations、MDNotes、Zotero Integration plugin

## 既有 Stage 1 ingest 工作重定位

昨天 ship 的 5 slice（PR #352-356）+ 6 fix（#369-374）**不砍**，重定位：

| Slice | 重定位 |
|---|---|
| 1 URL ingest skeleton (#352) | escape hatch：沒入 Zotero 的內容才走 |
| 2 academic 5-layer OA (#353) | 同上；Europe PMC layer 仍可作 PubMed digest 後援 |
| 3 翻譯 + 雙語 reader (#354) | **post-sync keep**：Zotero sync 的 clean MD 在這裡翻譯 |
| 4 image first-class (#355) | escape hatch（Zotero 已含 PDF + snapshot 圖） |
| 5 失敗檔丟棄 (#356) | 仍有用（共用 inbox UX） |
| 6 fix (#369-374) | 全保留（修共用 reader / log / UI） |

## 相關

- [project_session_2026_05_05_zotero_pivot.md](project_session_2026_05_05_zotero_pivot.md) — pivot 決定來源
- [feedback_dont_recompete_on_capture_quality.md](feedback_dont_recompete_on_capture_quality.md) — 戰略 lesson
- PR #94：publisher HTML fallback（只抓 elink Free 標 OA），覆蓋 BMJ/PLOS/eLife 等免費 OA 大站；Zotero 補訂閱 + 不在 elink Free 名單的 OA
- Crossref TDM API 仍可作次要選項（合法 bulk，但需機構授權或個人 key 申請），priority 低
