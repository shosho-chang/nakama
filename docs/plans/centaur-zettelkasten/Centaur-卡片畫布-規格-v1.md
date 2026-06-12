# Centaur 卡片畫布 — 規格 v1（定案）

> **這份是什麼**：HANDOFF-Centaur-Card-Canvas.md 的規劃產出。與修修經 4 輪互動 prototype 收斂的定案，供執行端（Claude Code）落地。
> **UI 規格附件**：`centaur-canvas-prototype-v4.html`（**互動與視覺以此為準**，含壓力測試模式）。
> **task prompts**：`docs/task-prompts/N527-centaur-canvas-data.md`、`N528-centaur-canvas-ui.md`。
> **日期**：2026-06-12

---

## 1. 定案總表

| # | 決定 | 備註 |
|---|------|------|
| C1 | **只做開卡畫布**，候選桌面模式砍掉 | canvas 是「寫卡時選連結」的空間介面 |
| C2 | **與線性 drawer 並存，線性為預設**；開卡時可切「畫布模式」 | 修修對線性版尚算滿意；線性版同時是鍵盤/小螢幕 fallback |
| C3 | **強度＝既有訊號分層**（零新 LLM 成本、零分數欄位） | 高＝P-2 判定有 typed-edge 關係；中＝FTS5/BM25 字面命中；外＝MOC 疊卡 |
| C4 | **工作桌 layout**：左側全高寫作桌（約 40vw），右側卡片場三直欄帶，**離桌越近越相關** | 取代同心圓（v1–v3 試過，空間利用差、主體不突出） |
| C5 | **落點＝桌內 2×2 四大格**（支持/反駁/延伸/來源），**拖起卡片即浮現**、放手即消失 | 邊條與小落點區已試過並否決（目標太小、遮標題）。格子不遮標題列 |
| C6 | **連結記錄住寫作桌內清單**：彩色關係徽章＋`[[目標]]`＋理由欄＋✕ 解除；點徽章改類型 | 浮動縮小卡方案已否決（連結多會互相遮擋） |
| C7 | **理由必填才能存**；正文空白不能存 | 紅線：關係與理由是人的判斷，AI 不代筆 |
| C8 | **MOC 疊卡只列 Robin 判定相關的**（✦ 徽章）；其餘收進右下角「MOC 盒」（盒子圖示、實線、可點開索引）| D-13 原則的延伸：永不裸列全量 |
| C9 | **疊卡點開＝全螢幕 overlay 攤平**（灰底、網格、可捲動）；**拖出一張的瞬間 overlay 收合**，該卡落在場上跟手 | 「從一堆卡裡挑中一張，其餘收起來」的實體手感 |
| C10 | **回收盒**（右下、虛線）：單卡或整疊拖入即從桌面收回 | session-scoped dismissal，只記 UI 狀態、不寫 vault |
| C11 | **存卡動畫**：寫作桌縮小飛出 → 「已寫 n」計數 → 下一張候選自動接上；全程尊重 `prefers-reduced-motion` | 寫卡是有節奏的循環 |
| C12 | **規模策略**：場上永遠只有 top-k 單卡 + 相關 MOC 疊 + 兜底盒；**永不全攤** | 壓力測試 600+ 張體感不變（prototype 內建驗證） |
| C13 | **P-2 範圍擴大**：增判「候選卡 ↔ MOC」相關性 | 餵 C8 的篩選；語料＝MOC 標題＋分組標題，成本低 |
| C14 | 觸控：pointer events 天然支援，桌機優先、不特別優化 | |
| C15 | 作用範圍只在「開卡」情境；瀏覽既有卡關係圖屬 N525 之後另議 | |

---

## 2. 互動規格（細節以 prototype v4 為準）

**寫作桌（左）**：標籤列（永久卡草稿 — 正文由你寫｜今日候選 i/n · 已寫 m）→ 標題（20px，可改）→ 正文（撐滿）→ 連結清單（≤38vh 捲動）→ 底欄（狀態字 + 存入 Vault）。

**卡片場（右）**：三直欄帶各有頂部標籤與虛線分隔——「高關聯 · Robin 判定」（卡上帶 ✦ Robin + 建議方向 chip）／「中關聯 · 字面相關」／「MOC 疊卡 · Robin 篩選」。卡片微旋轉 ±2.2°（reduced-motion 歸零）。

**連結流程**：拖起任何卡 → 桌內浮現四格（19px 標題＋一行白話說明：支持＝本卡支持它、反駁＝不能同時為真、延伸＝帶到新脈絡、來源＝source_ref）→ 丟入 → 卡從場上消失、清單新增一列、理由欄自動聚焦。重複連結同一張卡會被擋。

**修改/解除**：清單列點徽章 → popover 換類型；✕ → 解除、卡回到場上。

**存卡**：擋空正文、擋空理由（toast 指出缺幾條）。成功 → 飛出動畫 → 下一張候選 → 場上重排。全部寫完顯示完成空狀態。

**方向語意**：一律「本卡 → 對方」。

---

## 3. 資料契約（N527 落地）

`shared/schemas/daily_review.py` 的 `CandidateCard` 新增（**bump schema_version**）：

```python
related_pool: list[RelatedCard]   # 中圈：FTS5 命中、未經 P-2 判斷
  # RelatedCard: {card_path, title, status, bm25_rank}
related_mocs: list[RelatedMoc]    # P-2 擴判：與候選相關的 MOC
  # RelatedMoc: {moc_path, name, card_count}
```

既有 `TypedEdgeChip`（P-2 判定 + 方向）＝高圈，不變。低圈疊卡內容＝MOC 成員清單（由 kb_indexer / MOC marker section 取得，前端 lazy load）。

---

## 4. 紅線（不變，列出供驗收）

- 寫入唯一入口 `POST /kb/api/permanent`（human-authoring，`author: human`）；canvas 只是 authoring surface 的另一個皮。
- 關係類型由人選（拖入哪格）、理由由人寫（必填）；Robin 的建議方向只是 chip 標示。
- 回收盒、MOC 盒、疊卡攤平都是讀側 UI 狀態，不寫 vault。
- CSP：`/kb*` 禁 inline script，JS 走 `/static/*.js`。
- design-system：`--sho-*`、LINE Seed TW、AI-slop 禁用清單、`prefers-reduced-motion`、focus ring。鍵盤可達性由線性 drawer fallback 承擔（C2），canvas 本身保證 toolbar/清單/popover 可鍵盤操作。
