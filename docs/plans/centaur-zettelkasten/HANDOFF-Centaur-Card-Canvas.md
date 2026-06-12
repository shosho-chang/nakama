# Handoff — Centaur 卡片畫布（散落桌面的實體卡片介面）

> **給規劃的 model（另一視窗）**：這份讓你冷啟動就能規劃，不必讀原始對話。讀完這份你就有完整 context。語言用繁體中文（台灣）。
> **交接時間**：2026-06-12
> **狀態**：功能構想已由修修提出、尚未規劃、尚未拆 task prompt。
> **你的任務**：把這個功能規劃成可執行的 task prompt(s)（六要素），產出交回**執行端（這個 repo 的 Claude Code session）**落地。**先釐清 §4 開放問題、再拆 task。**
> **互動方式**：修修偏好多輪討論、一次推進一塊、重大決定先問再做、精簡直接少廢話。

---

## 0. 一分鐘看懂

修修要把 Centaur「每日回顧」的開卡體驗，從現在的**線性清單**，升級成一個 **「卡片散落在桌面」的空間介面**：候選卡依 Robin 算出的**關係強度分區**（高 / 中 / 低三圈），並且可以把候選卡**拖拉**到正在寫的 Permanent Card 上。目的是讓「數位卡片盒」有**實體卡片盒的手感**。

這是接在已建好的 Centaur pilot（N520–N524）**之後**的增強功能，不是 pilot 的一部分。

---

## 1. 願景（修修原話 + 詮釋）

**修修原話**：
> 「在選擇卡片連結時，能不能做成一個介面，感覺就像很多實體卡片散落在桌面上一樣？如果這些卡片能依照 Robin 幫我做的關聯強度來安排位置，例如：① 高強度關係區 ② 中等關係區 ③ 比較不相干的區域。這個介面也可以讓我把這些候選卡片清單，直接拖拉到目前新增的 Permanent Card 頁面中。這就可以讓我們的數位卡片盒更貼近於實體的感覺。」

**詮釋（待規劃確認）**：
- **空間版面**取代/並存於線性清單——卡片是可擺放的物件，不是列表項。
- **關係強度 → 位置**：愈相關的卡愈靠近中心（正在寫的 Permanent card），分三圈/三區。
- **拖拉建立連結**：把一張卡拖到 Permanent card → 形成某種關係（source_ref？typed edge？見 §4）。
- **實體手感**：散落、可拖、空間記憶——對應 Zettelkasten 的實體卡片盒隱喻。

---

## 2. 系統現況（grounded — 已建好的 Centaur）

整套 Centaur Zettelkasten 的 pilot（永久層地基 → 文獻 writer → 每日回顧 job → 每日回顧 Web UI → 文章 ingest）已實作完成，為 **5 個 stacked PR，draft、尚未 merge**：

| PR | 任務 | 內容 |
|---|---|---|
| #874 | N520 永久層地基 | `shared/permanent_layer.py`（紅線 1 寫入紀律）、`kb_indexer` 索引 Permanent、`kb_typed_edges` 結構表、`kb_hybrid_search` Permanent 排序置頂、tripwire |
| #876 | N521 Literature writer | `shared/literature_writer.py` 三路 render `KB/Literature/` |
| #877 | N522 每日回顧 job | `agents/robin/daily_review.py`（P-1 候選 + P-2 typed-edge）、**輸出契約 `shared/schemas/daily_review.py`** |
| #878 | N523 每日回顧 Web UI | `thousand_sunny/routers/kb_review.py`、`templates/kb/daily_review.html`、`static/kb_review.{css,js}`——**本功能要改造的就是這個 UI** |
| #879 | N524 route C ingest | 文章端到端 + 紅線 5 citation lint |

**每日回顧現況（N523，本功能的起點）**：
- 路由 `GET /kb/review`（讀 N522 的 `DailyReviewBundle`）+ `POST /kb/api/permanent`（全系統唯一 Permanent body 寫入口，帶 `author: human`）+ `POST /kb/api/review/{skip,later}`。
- UI 是**線性三段**：① Fleeting 待處理 ② 候選永久卡（昨日劃線）③ 每週清掃。
- 「開卡」打開一個**側邊 drawer**：檔名（AI 建議可改）+ source_refs（AI 預填）+ 正文（人寫，紅線內側）+ typed-edge 選擇器（Robin 建議 chips 分方向 + 全量兜底 free-text）+ 檔案預覽 + 存入 Vault。
- typed-edge 選擇器目前每個候選各自顯示「相關既有卡」chips（候選 → 既有永久卡），分 支持/反駁/延伸 三向。

---

## 3. 關鍵技術 gap（規劃必讀——這些是落地前必須處理的依賴）

### 3.1 ⚠️ 目前沒有「關係強度分數」

要「依關係強度分區」，但目前的資料模型**沒有強度欄位**。`shared/schemas/daily_review.py` 的 `TypedEdgeChip` 只有：
```python
edge_type: "support" | "refute" | "extend"
direction: "forward" | "reverse"
target_card: str          # 既有卡 KB path
target_title: str
```
**沒有 strength / score / confidence。** N522 的 P-2 prompt 目前判斷的是「有沒有關係 + 哪個方向」，不輸出強度分數（且依規格 v0.2，P-2 的 internal_rationale 刻意不外露）。

→ **規劃要拍板**：分區的「強度」從哪來？選項：
- (a) P-2 LLM 多輸出一個強度分數（schema 加欄位 + bump `schema_version`，是一條 schema-change 子任務）。
- (b) 程式算（`kb_typed_edges` edge count / FTS BM25 分數 / wikilink 1-hop 距離）——**注意 ADR-042 已移除 embedding，沒有向量相似度可用**。
- (c) 混合。

### 3.2 「分區」是哪兩組東西的關係，語意未定

目前 typed-edge 是「**候選卡 → 既有永久卡**」的建議。但「散落桌面 + 三圈」可能指：
- 候選卡**彼此之間**的關係？（畫成卡片群）
- 候選卡 **↔ 既有永久卡**的關係？（現有 chip 的空間化）
- 以**正在寫的 Permanent card 為中心**，其他卡依與它的相關度排圈？
→ 規劃要先釐清「中心是什麼、圈上是什麼」。

### 3.3 拖拉的落點語意未定

拖一張卡到 Permanent card → 變成什麼？
- 變 `source_refs`（來源錨點）？
- 變 typed edge（`支持::`/`反駁::`/`延伸::`，但**理由是人寫的、AI 不代筆**——拖拉後仍要人補理由，見紅線）？
- 兩者皆可選？

### 3.4 既有約束（不可違反）

- **紅線**（Centaur v0.2 §7）：AI 絕不寫 `KB/Permanent/` 正文與 status；typed edge 的**理由是人的判斷、AI 只給「建議 chips」不代筆**。canvas 拖拉後仍須讓人補理由。
- **design-system**（`docs/design-system.md`，**必讀**）：`--sho-*` token、LINE Seed TW 字型、theme.js、**AI slop 禁用清單**（無紫漸層 / 無 Inter-Roboto / 無均勻 card grid / 無通用 CTA）、states 全設計、`prefers-reduced-motion`（拖拉動畫要尊重）、AAA/AA 對比、keyboard nav（空間 UI 的鍵盤可達性是挑戰）。
- **CSP**：`/kb*` 走 `script-src 'self'`，**禁 inline `<script>` / `onclick`**，JS 一律走 `/static/*.js`。
- **無 embedding**（ADR-042）：檢索只有 FTS5/BM25 + wikilink + `kb_typed_edges`。
- **檢索規模**：個人尺度（永久卡累積到數百張才需要更重的方案），目前不需向量。

---

## 4. 開放問題（規劃要逐一拍板，建議先問修修）

1. **分區的關係語意**：中心是什麼？圈上是候選↔候選，還是候選↔既有永久卡，還是 ↔ 正在寫的卡？（§3.2）
2. **強度分數來源**：LLM 給分 vs 程式算 vs 混合？（§3.1）
3. **三區是離散圈還是連續空間**？卡片位置是「分組到三區」還是「依分數連續定位」？
4. **拖拉落點行為**：拖到 Permanent card → source_ref / typed edge / 可選？（§3.3）拖完要不要彈出補「理由」的輸入（紅線：理由人寫）？
5. **取代 vs 並存**：canvas 是**取代**現有線性 drawer，還是**另一個 view**（線性 + 畫布切換）？修修對線性版整體尚算滿意。
6. **觸控/手機**：桌機優先還是要支援觸控拖拉？
7. **作用範圍**：只在「開卡」當下用（選連結），還是也能當瀏覽既有永久卡關係圖的獨立頁（會碰到 N525 其餘 view）？
8. **動畫/物理感程度**：要到什麼程度（散落隨機微旋轉？拖拉慣性？）——對 `prefers-reduced-motion` 與效能的取捨。

---

## 5. 建議的規劃產出（交回執行端的東西）

- **1–3 份 task prompt（六要素：目標/範圍/輸入/輸出/驗收/邊界）**，編號接在 N524 之後（如 N527+），標明依賴。可能切成：
  - (A) **schema + N522 強度子任務**：P-2 輸出關係強度，`daily_review.py` schema 加欄位 + bump version（若決定走 LLM 給分）。
  - (B) **canvas UI 子任務**：新 view / 改造 drawer，空間版面 + 分區 + 拖拉，走 design-system。
- **一份視覺 prototype（HTML）** 放 `docs/plans/centaur-zettelkasten/`，對齊互動（修修對畫面感很強，建議先 prototype 再實作；可走 Claude Design 迭代後交付）。
- 明確標出**驗收**（含 design-system AI-slop 逐項 + 瀏覽器端到端 + 紅線守住）。

---

## 6. 關鍵檔案 / 文件（規劃可深讀；多在未 merge 的 stacked 分支 + E:\nakama working tree 的 untracked 副本）

**Centaur 規格**（在 `docs/plans/centaur-zettelkasten/`）：
- `Centaur-Zettelkasten-規格-v0.2.md` — 定案總表（§3 Permanent 規格、§5 每日回顧、§7 五條紅線、§10 MOC）
- `Centaur-Zettelkasten-規格書.html` — Literature / Ingest 規格 v0.1 全文
- `Centaur-Zettelkasten-Prompt規格-v0.1.md` — 全 LLM call 模板（P-1 候選、**P-2 typed-edge** ← 強度若走 LLM 在此改）
- `centaur-kb-prototype-v2.html` — 現行每日回顧 UI 的視覺規格（線性版；canvas 是它的演進）
- `SESSION-HANDOFF-Centaur-Zettelkasten.md` — pilot 全程交接（更深背景）

**code（本功能要碰/參考）**：
- `shared/schemas/daily_review.py` — **輸出契約**（`DailyReviewBundle` / `CandidateCard` / `TypedEdgeChip`）← 強度欄位加這裡
- `agents/robin/daily_review.py` — N522 job（P-1/P-2 呼叫）
- `thousand_sunny/routers/kb_review.py` — `/kb/review` + `POST /kb/api/permanent`
- `thousand_sunny/templates/kb/daily_review.html` + `static/kb_review.{css,js}` — **現行 UI，canvas 改造起點**
- `shared/kb_hybrid_search.py`（FTS5/BM25 + `kb_typed_edges` + wikilink lane）、`shared/kb_indexer.py`（`kb_typed_edges` 表）— 程式算強度的料源
- `shared/permanent_layer.py` — 紅線守門（拖拉寫入仍要過 human endpoint）
- `docs/design-system.md` — **美學聖經，必讀**

**決策脈絡**：`docs/decisions/ADR-042`（移除 embedding）、`ADR-043`（Centaur 永久層）、`docs/VAULT-LAYOUT.md`（KB/Permanent 權限）。

---

## 7. 工作慣例（執行端落地時遵守，規劃時知道即可）

- **worktree 紀律**：`E:\nakama` 是 control plane 不直接寫；每個 task 開 sibling worktree。
- **stacked PR**：Centaur 系列疊著、draft、由修修 bottom-up review；非 main-targeted PR **CI 不會自動跑**，執行端用本機全套 pytest + ruff 當閘門（本機 Windows 有 7 個 pre-existing 失敗：`test_log.py` + 6×`test_watchlist_routes::test_post_watchlist_confirm_*`，CI Linux 會綠，非新增）。
- **語言**：頁面內容繁中、frontmatter key 英文、專有名詞保留原文。
- **刪檔**：禁 `rm`，用 PowerShell 回收桶。
- **美學是 first-class**：UI 出手前讀 design-system，拒絕 AI slop，瀏覽器走查 + 修修視覺驗收。

---

*Handoff 結束。規劃端第一步建議：先跟修修確認 §4 的「分區關係語意」與「強度來源」兩題（其餘可順帶），再拆 task prompt。別在這兩題定案前就 over-design 互動細節。*
