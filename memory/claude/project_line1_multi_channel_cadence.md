---
name: project-line1-multi-channel-cadence
description: Line 1 (podcast/訪談 repurpose) 多 channel cadence 長期方向 = Option Z (per-channel publish lifecycle)；2026-05-24 暫時收線 Option X (blog-published terminal)
metadata:
  type: project
---

Line 1（訪談 SRT → blog / FB 4 tonal / IG carousel）的多 channel 狀態管理長期方向是 **Option Z：per-channel publish lifecycle**，每個 channel 有獨立的 approve + publish sentinel + 自己的 publish 通路（FB Graph API / IG cards / WP draft via Usopp）。

**Why:** 修修 2026-05-24 #683 grill 明確表達：「Line 1 我的預期的確是長期發展多 Channel，並且有自己的 Publish 通路」。但同期 priority 是先把 **YouTube 這條路線跑順**，Line 1 多 channel cadence 還在實驗期，現在投資 Y2/Z 都太早。

**How to apply:**

1. 目前 `thousand_sunny/routers/repurpose.py:_run_status()` 採 **Option X**（blog `.published.blog` 即 terminal）— 不要急著改成 Y2 `published-partial` 或 Z 矩陣
2. 未來重新評估 Z 的觸發條件（**任一即觸發**）：
   - FB Graph API adapter 或 IG cards publish 通路要實作上線
   - 修修主動回報「list view chip 跳綠太早，FB/IG 沒做完掉出視線」
   - Line 1 cadence 定型，需要做「campaign 完工率」report
3. PR #685 修完 #682 已堵住「engine 覆寫 approved 內容」最壞情況，list view 跳綠的視覺問題不會造成 data loss
4. 目前 priority：**YouTube 線**（不是 Line 1 的 FB/IG channel）— 開發決策不要 spontaneously 拉回 Line 1 multi-channel 完整度

相關記憶：[[project_content_pipeline_arch]]（七層架構） / [[project_repurpose_flow]] / [[feedback_pipeline_anchored_planning]]
