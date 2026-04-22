---
name: 內容 Repurpose 流程（下一階段實作）
description: 部落格文章重製為 IG 知識圖表等平台內容，Brook + Usopp 協作的下一階段重點
type: project
tags: [brook, usopp, repurpose, ig-carousel, multi-platform]
created: 2026-04-22
---

部落格發布後的「內容重製」是下一階段實作重點。

## 流程概念

```
Blog post（published）
  ↓ Brook repurpose
  ├── IG 知識圖表（carousel 10 張）
  ├── IG Reels 腳本
  ├── FB 貼文
  ├── LinkedIn 貼文
  ├── Newsletter digest
  └── YouTube Shorts 腳本（可能）
  ↓ Bridge approve
Usopp publish（各平台）
```

## 和 Brook 圖片管線的交集

IG carousel = Brook 圖片管線的主要產出之一。兩個 topic 要一起設計。

## 優先級

- Phase 1（當前）：Brook + Usopp blog 發布通路
- Phase 2：Repurpose flow（本 topic）— 等 blog 通路穩定後
- Phase 3：Chopper 社群互動

## How to apply

實作 Phase 1 時，**預留 Brook 的 `repurpose` 接口**（吃已發布 post ID + target platform），但不實作 platform-specific adapter。
Usopp 的 API 設計要能支援未來 FB/IG/LinkedIn，不寫死 WordPress-only。
