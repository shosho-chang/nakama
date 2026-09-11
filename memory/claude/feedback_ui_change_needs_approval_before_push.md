---
name: feedback_ui_change_needs_approval_before_push
description: UI 變更（含小調整）先給修修看過、他說 OK 才 push；且必須重用既有元件而非另造一套
metadata:
  type: feedback
---

# UI 變更先過目再 push；元件重用不重造

修修 2026-09-10（專案頁核取方塊）：

> 「我希望把這邊的 check 掉，表現是在格子裡面打勾，並且整個 cross 掉，然後變成灰色，
> 就跟在 weekly dashboard 一樣。**這裡你為什麼要重新發明一個？**我很好奇。右邊這邊，
> 番茄鐘以及日期也都沒有對齊。像這些 **UI 的小變更，你要不要等我說 OK 了再一起 push
> 上去？**」

## 三條規則

**1. UI 變更不自動 push。** 做完 → 本機 dev server 跑起來 → 給修修看截圖／URL → 他說 OK
才開 PR / push。後端與 bug fix 維持既有的自動推進節奏，**只有視覺層要停下來等**。
理由：視覺是主觀的，修修看一眼就知道對不對；讓他在 merge 後才發現，等於白跑一輪
CI（43 分）加一次部署。

**2. 不要加他沒要求的東西。** 2026-09-11 他看到專案頁的階段進度軌：「這一列我沒有跟
你講說我要做。」——那是我在 mockup 裡自己加的，他當時回「你先做出來吧」是對整體計畫
的概括同意，不等於逐項要過。**mockup 裡我自己發明的元素，落地前要單獨點出來讓他確認**，
不能混在「照計畫做」裡面帶過。

**3. 先找既有元件，不要複製一份新的。** 我當時把 `.wk-box`（Weekly 的核取方塊）整段
CSS 抄成 `.pjd-box`，等於同一個元件兩份實作——跟兩天前才修掉的「wikilink 三份 parser」
是同一種錯。正確做法是**抽成共用類別（`bridge.css` 的 `--sho-*` ops 詞彙層），兩邊都用**，
改一次兩個頁面同時受惠。

## How to apply

出手前先 grep 既有 class（`.wk-*` / `.sho-*` / `bridge.css`），確認沒有現成的才新造；
真的需要跨頁共用時放 `bridge.css` 而不是 per-page 的 `bridge-<page>.css`。
完工後**先展示、後 push**。

## 附帶事實

- Weekly 的 done 方塊實際上**只是填滿的深色方塊，沒有打勾**，而且只有任務名劃線、
  右側 meta 不變灰——修修要的「打勾＋整列 cross＋變灰」是兩邊都還沒有的，所以要一起補。
- 表格式對齊在 Weekly 是靠 `.wk-task` 的 `display: grid` + 固定欄寬（N541「隱形表格」），
  不是 flex + gap。任何多欄的任務列都該沿用這招，否則各列的 🍅／日期不會對齊。

相關：[[feedback_ui_preflight_visual_checklist]]、[[feedback_verify_ui_in_real_browser]]、
[[feedback_aesthetic_first_class]]
