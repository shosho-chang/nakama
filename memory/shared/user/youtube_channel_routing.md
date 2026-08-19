---
type: user
visibility: shared
agent: shared
confidence: high
created: 2026-08-19
expires: permanent
tags: [youtube, podcast, publishing, channel-routing, carousel]
name_zh: YouTube 發布頻道分流
name_en: YouTube publishing channel routing
description_zh: Podcast 長片、Shorts、Carousel／社群貼文一律發布到 Podcast 頻道；@shoshotw 僅作為 55 萬訂閱主頻道使用
description_en: Podcast long videos, Shorts, and Carousel or Community posts must go to the Podcast channel; @shoshotw is the 550K-subscriber main channel
---

YouTube 發布前必須依內容類型選擇正確頻道：

- Podcast 內容（長片、Shorts、Carousel／Community 貼文）：
  - 頻道：《張修修的不正常人類研究所》
  - handle：`@abnormal-human-research`
  - channel ID：`UCvipegP35x3-OcAs--PgAig`
- 主頻道內容：
  - 頻道：張修修的不正常人生 Shosho's Abnormal Life
  - handle：`@shoshotw`
  - channel ID：`UC7_BNdimJrNLPDeectTg6Ig`
  - 約 55 萬訂閱

禁止因 Chrome 當前登入或選中的頻道而直接發布。任何 YouTube 發布動作前，必須先在 UI 或 API token identity 中確認目標頻道；Podcast Carousel 必須顯示 Podcast 頻道身分後才可排程或發布。
