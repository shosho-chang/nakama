---
name: 重要架構細節先問用戶再動手
description: UX / 產品架構決策（面向用戶直接可見的差異）要先跟用戶對齊，不能用「我覺得這樣設計 OK」自行決定
type: feedback
---
重要的設計細節（尤其是面向用戶直接可見的 UX 差異）要先問用戶再實作，不能自己決定。

**Why**：2026-04-20 Sanji Slack bot 實作走了「一個 Slack app 內多 persona、用 keyword routing」路線（PR #52），但修修其實要的是「每個 agent 獨立 Slack bot，有自己的名字 / avatar / @mention」。這個差異直到 VPS 部署當下才被發現，要重做 gateway/bot.py 架構。

**How to apply**：
- 遇到「產品上用戶會直接感受到的差異」時，先列出 2-3 個方向 + trade-off 給修修選，不要自己拍板
- 判斷標準：「這個決定會不會讓 Slack / UI / 對外產出長得不一樣」— 會的話就要問
- 純內部實作（DB schema、錯誤處理、測試結構）不用問，直接做
- 有疑慮時，寧願多問一句「你希望 X 還是 Y」也不要直接動手
