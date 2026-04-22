---
name: Envato Elements 實際無 API（2026-04 查證）
description: 修修有 Envato Elements 年度會員，但官方不提供 API 下載；替代 stock/AI 方案清單
type: project
tags: [envato, stock-photo, image-sourcing, brook]
created: 2026-04-22
---

## 事實

- **Envato Elements（訂閱制）和 Envato Market（build.envato.com/api/，單買制）是兩個不同產品**
- Envato Market 有 API — **但只給賣家/買家查交易紀錄**，Elements 素材無法透過此 API 取得
- **Envato Elements 官方無下載 API**
- 2026-03 官方更新 ToS 明確禁止「scraping / bots / scripts / 任何自動化下載工具」
- 第三方（Browse AI、Tampermonkey 用戶腳本）存在但違反 ToS，有封號風險

## 對 Brook 的影響

修修原先期望 Brook 直接呼叫 Envato API 取圖 — **不可行**。

## 替代方案（合法）

| 路徑 | 優點 | 限制 |
|---|---|---|
| Unsplash API | 免費、畫質佳 | 50 req/hr, 5000/月；需標示作者 |
| Pexels API | 免費、無限 | 200 req/hr；需標示作者 |
| Pixabay API | 免費、寬鬆 | 品質參差 |
| Flux API (fal.ai / Replicate) | AI 生圖、獨特 | 每張 $0.003-0.04 |
| 本地 FLUX.1-dev（RTX 5070 Ti 16GB） | 零成本、隱私 | 要自己設環境 |
| Envato Elements（人工下載） | 品質好、會員免費 | 不能自動化 |

## How to apply

Brook Phase 1 MVP：輸出**圖片 brief 清單**（用途/描述/風格/建議關鍵字），修修**人工**從 Envato Elements 下載後放進 vault / WordPress media library，Usopp 發布時引用。

Phase 2：接 Unsplash / Pexels API 自動搜圖（hero 配圖）。Envato 仍留給需要「特定風格 stock」時人工下載。

Phase 3：接 Flux 做 hero image（獨特性 > stock）。本地 SD 視時機。
