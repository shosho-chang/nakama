---
name: feedback-agent-downloads-stock-itself
description: 素材自己去 Envato 抓，不要把候選丟回去等修修點頭——他明說過不會自己下載
metadata:
  type: feedback
---

修修 2026-08-31：「你就去開 Envato，把素材補齊啊。**我已經說過很多次了，我不會
自己去下載 Envato 的素材。**」

同一天稍早他也講過一次：「如果你要下載 Envato 的素材的話，你就直接去瀏覽器下載。」
我卻在收工報告裡把三支缺的素材列成「需要你的 Envato 登入」丟回去。

**Why**：Chrome 擴充連的就是他已登入的瀏覽器，訂閱是吃到飽——抓錯一支的成本只有
硬碟空間。把候選頁丟回去等他點頭，換來的是整支短片卡住不動。這跟
[[feedback-run-dont-ask]]（命令列／Python／benchmark 全部 agent 端做）是同一條，
只是這次的介面是瀏覽器。

**How to apply**：
- 需要 stock／音效／音樂 → 自己開 `mcp__claude-in-chrome__*` 去抓，抓完寫
  acquisition receipt，直接接下去跑
- 判斷標準自己過（直式、語意對到那句話、負面清單），不合格就換一支；
  真的沒有合格的就**寧缺勿猜**留 talking head，把意圖寫進 `_wanted`
- **驗收點在 preview，不在候選頁**——他要看的是成片，不是縮圖清單
- 下載格式選 1080×1920，不要抓 4K ProRes（時間軸就是 1080×1920）
