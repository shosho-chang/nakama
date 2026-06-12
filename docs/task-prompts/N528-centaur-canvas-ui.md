# N528 — 卡片畫布 Web UI

> **上游文件（先讀）**：`docs/plans/centaur-zettelkasten/Centaur-卡片畫布-規格-v1.md` + **UI 規格附件 `centaur-canvas-prototype-v4.html`（互動與視覺以此為準，先在瀏覽器玩過再動工）**、`docs/design-system.md`（必讀）。
> **依賴**：N523（#878）、N527。**worktree**：`E:\nakama-N528-canvas-ui`，branch `feat/n528-canvas-ui`。stacked。

## 1. 目標

在每日回顧的開卡流程加入「畫布模式」：工作桌 layout、拖拉建立 typed edge、MOC 疊卡與兜底盒——與線性 drawer 並存、線性為預設（C1/C2）。

## 2. 範圍

- `thousand_sunny/templates/kb/daily_review.html` + `static/kb_canvas.{css,js}`（新檔；**CSP：禁 inline script/onclick**）
- 開卡入口加「畫布模式」切換（記住偏好 `localStorage`）
- 實作 prototype v4 全部互動（規格 §2）：工作桌、三帶卡片場、拖起即現的 2×2 落點格、連結清單（徽章/理由/✕/popover 改型）、MOC 疊卡（僅 related_mocs）+ overlay 攤平 + 拖出收合、MOC 盒、回收盒（session-scoped）、存卡動畫與候選接力、壓力安全（top-k + 疊卡，永不全攤）
- 寫入沿用 `POST /kb/api/permanent`（不新增寫入口）；skip/later 沿用既有 API
- `prefers-reduced-motion`：所有動畫歸零（含微旋轉）；focus ring `--sho-focus`；toolbar/清單/popover 鍵盤可達
- 驗收用 fixture：以《卡片盒筆記》候選 + 真實 MOC 樣本

## 3. 輸入

prototype v4（單檔，token/互動可直接搬）；N527 bundle 契約 + MOC 成員 API；design-system（asset versioning、theme.js）。

## 4. 輸出

畫布模式 + 測試 + 瀏覽器端到端走查紀錄（截圖入 PR）。

## 5. 驗收

- 端到端：切畫布 → 拖卡入「反駁」格 → 理由聚焦必填 → 存入 → vault 卡片含 `反駁:: [[…]] — 理由`、`author: human` → 下一張候選接上
- 疊卡：點開 overlay 攤平 60 張可捲動；拖出一張 overlay 即收；拖整疊入回收盒即收回
- 空正文 / 空理由被擋（422 或前端阻擋＋訊息）
- design-system AI-slop 清單逐項零違規；reduced-motion 驗證；CSP 無 inline script
- 壓力 fixture（600+ 卡）下互動不卡頓（場上 DOM 元素數與示意規模同階）

## 6. 邊界

線性 drawer 不動（仍為預設）；不做瀏覽既有卡關係圖（N525 之後）；不做觸控優化；不新增 Permanent 寫入路徑；回收盒/MOC 盒狀態不寫 vault。
