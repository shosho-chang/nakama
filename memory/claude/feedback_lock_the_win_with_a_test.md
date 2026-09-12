---
name: feedback_lock_the_win_with_a_test
description: 人眼驗過的成果要當場用測試鎖住，常數只能有一個真相來源，否則下一輪就 regression
metadata:
  type: feedback
---

**修修人眼驗過、說「這個好」的東西，同一次改動就要用測試鎖住；版本／尺寸這類常數
只能有一個真相來源。**

**Why**：2026-09-09 蘇予昕長片線。修修 review 完 timeline 說「其他都很好，Hero title
以及 Stock footage 都選得不錯」，接著補一句：

> **一定要把這次成功的做法接連地做下去，不要再有出現 regression 的狀態了。**

而當下的實際狀態是：`pytest tests/brook/script_video/` **27 個紅的**。原因全是我自己
前一晚改的東西沒有把測試一起改：

1. **常數裂成兩個真相來源**——版位版本同時寫在 `_engine._LAYOUT_VERSIONS` 與
   `_long_visual_renderer._RECIPES`。把 hero bump 到 v2 只改了一邊的下游，渲染器就
   丟 `long visual geometry does not match its canonical layout`，一路連累 hyperframes／
   visual_assets／long_visual_renderer 三個測試檔。
2. **fixture 各自寫死值**——`"hero_title:v1"` 散在四個測試檔裡，配方一 bump 就全紅。
3. **退役沒有掃乾淨**——`visual_effect` 2026-09-08 退役，但 10 個 fixture 還在造它。
4. **人眼決定沒被鎖住**——修修拿掉「章節」kicker，測試卻還 assert 它存在；等於下一個
   人改回去也不會被擋。

**How to apply**：
- 動到 house 配方（字級、版位版本、文案、退役某個元件）→ **同一次改動**把相關測試改
  成 assert 新契約，並把「這是人眼定的」寫進註解。紅的測試不准留到下一輪。
- 版本／尺寸／閾值這種會被兩處讀的常數，放進契約層（例如
  `_projection.LAYOUT_VERSIONS`）讓兩邊 import，再加一條測試 assert 兩邊一致。
- 測試 fixture 不要寫死版本字串，呼叫 `layout_identity(kind)`。
- 交付前跑完整套，**把數字講出來**（passed/failed），不要只說「測試過了」。
- 相關：[[feedback_human_verified_is_final]]（人眼驗過的不用再改）、
  [[feedback_fix_failures_dont_report_them]]。
