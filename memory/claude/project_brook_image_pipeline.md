---
name: Brook 圖片生成管線（下一輪專題）
description: Brook 不只寫文字，還要產出 blog 配圖、IG carousel 知識圖表、YouTube 縮圖；下一輪深入討論
type: project
tags: [brook, image, envato, ig-carousel, yt-thumbnail]
created: 2026-04-22
---

Brook 的「圖片生成」是下一輪要深入討論的專題，涵蓋：

1. **Blog 文章配圖**（hero + inline）
2. **Instagram 知識貼文 carousel**（多張圖，文字 + 視覺）
3. **YouTube 影片縮圖**（可能交給 Brook）

## 已知資源

- 修修有 **Envato Elements 年度會員**（可無限下載 stock photo/video）— 但**僅限人工下載**（見 project_envato_api_reality）
- 開發機 RTX 5070 Ti 16GB VRAM — 本地跑 FLUX.1 可行
- Claude Design（claude.ai/design）可做視覺設計迭代，但目前無 public API

## 要討論的決策點

1. Stock photo vs AI-generated 的比例與分工
2. IG carousel 視覺 template 來源（Claude Design / Canva / 自刻）
3. YT 縮圖設計語言（中英雙語？字型？品牌色？）
4. 每種圖片的生成 pipeline：誰提 brief、誰選圖、誰合成、誰 approve
5. Brook 要不要自己生圖（Flux API / 本地 SD），還是只出 brief 讓人手執行

## How to apply

本輪暫不實作。Phase 1（Brook MVP）先用**「Brook 輸出圖片 brief 清單 → 人工從 Envato 下載 → 放 WordPress media library」**的 loop。
API 整合與 AI 生圖排到 Phase 2/3，下一輪專題討論後定設計。
