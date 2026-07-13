# N527 — 卡片畫布資料配套（schema + daily job 擴充）

> **上游文件（先讀）**：`docs/plans/centaur-zettelkasten/Centaur-卡片畫布-規格-v1.md`（§1 C3/C8/C13、§3 資料契約）、`Centaur-Zettelkasten-Prompt規格-v0.1.md`（P-2）。
> **依賴**：N522（#877）。**worktree**：`E:\nakama-N527-canvas-data`，branch `feat/n527-canvas-data`。stacked 在 N522 分支之上。

## 1. 目標

讓 `DailyReviewBundle` 載齊卡片畫布需要的三層資料：高（既有）、中（FTS pool）、MOC 相關判斷。

## 2. 範圍

- `shared/schemas/daily_review.py`：`CandidateCard` 新增 `related_pool: list[RelatedCard]`、`related_mocs: list[RelatedMoc]`（欄位定義見規格 §3）；**bump `schema_version`**，舊欄位不動
- `agents/robin/daily_review.py`：
  - `related_pool`：kb_hybrid_search（FTS5/BM25）top-k（k=8），**排除**已進 TypedEdgeChip 的卡，帶 `bm25_rank`
  - `related_mocs`：**P-2 prompt 擴判**——輸入追加 MOC 清單（名稱＋分組標題，來自 `KB/MOCs/*` frontmatter/headings），輸出與候選相關的 MOC（上限 2，寧缺勿濫，沿用 P-2「表面相似 ≠ 關係」過濾原則）
- MOC 成員清單 API：供前端 lazy load 疊卡內容（`kb_indexer` 既有資料即可，回 `{card_path,title,status}[]`）
- 測試：schema 向後相容（舊 bundle 可讀）；`related_pool` 排除邏輯；P-2 MOC 擴判以《卡片盒筆記》樣本驗證——「主題不是選出來的」候選應命中「創作與選題」、**不**命中「長壽與健康」

## 3. 輸入

規格 v1 §3；N522 的 `daily_review.py` 與 P-2 呼叫點；`shared/kb_hybrid_search.py`；`KB/MOCs/` 結構（VAULT-LAYOUT）。

## 4. 輸出

schema + job 擴充 + MOC 成員 API + 測試，單一 PR（stacked）。

## 5. 驗收

- bundle JSON 含三層資料且 schema_version 已 bump
- 負面測試：wingate 類表面相似不進 related_pool 高位、不相關 MOC 不進 related_mocs
- 舊線性 UI（N523）讀新 bundle 不壞（向後相容）

## 6. 邊界

不動 UI（N528）；不加任何強度分數欄位（C3：分層即強度）；不動 P-1；不寫 vault。
