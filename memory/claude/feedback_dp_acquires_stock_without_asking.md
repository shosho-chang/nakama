---
name: feedback_dp_acquires_stock_without_asking
description: DP 需要 B-roll／stock footage 就直接去抓，這是常設授權，不准再問
metadata:
  type: feedback
---

**Director／DP 流程中任何 B-roll、stock footage、素材採購的需求，直接去抓，不要問。**
這是常設授權，不是每次要重新確認的東西。用登入中的 Browser Computer Use 進 Envato
取得、授權、下載，然後接回 deterministic script 驗收上軌。

**Why**：2026-09-07 蘇予昕長片線，DP 產出 3 個要素材的事件（2 stock video + 1 photo），
我把「要不要去抓素材」做成 A/B/C 三選一丟回去問。修修回：
「**B，這件事我講過很多次了。Director、有什麼有關 B-roll、Stock footage 的需求，
DP 這個就直接去抓就對了，不用再問我了。不要再問我，講三次，不要再問我了。**」

`highlight-cut` SKILL.md 本來就寫著「素材採購可用登入中的 Browser Computer Use」——
授權早就在流程裡，我把「動到帳戶」當成需要逐次確認的不可逆操作，判斷錯了。
真正需要回頭問的只有：UAT、設計分叉、以及**破壞性**操作。下載授權素材不是其中任何一種。

**How to apply**：
- DP 說要素材 → 直接去 Envato 抓，抓完回報抓了什麼，不要先問。
- 素材庫撐不住 Director 要的密度時，才停下來問——那是「素材不夠不要硬湊」的既有規則，
  跟「要不要去抓」是兩件事。
- 相關：[[feedback_dont_ask_permission_at_every_step]]（他授權過的方向自己推進）、
  [[feedback_semantic_work_runs_on_host_agent]]（語意工作由當下 agent 做）。

**採購的實際操作路徑（2026-09-09 補）**：
- 走 **`app.envato.com`**，不是 `elements.envato.com`。elements 的 item 頁永遠不進
  `document_idle`，`find`／`read_page`／`screenshot` 一律 45 秒 timeout——那不是「抓不到」，
  是走錯門。
- 搜尋 URL：`https://app.envato.com/search?itemType=stock-video&term=<query>`。
  縮圖是 lazy-load，**進頁後要等 25–35 秒**才有圖，太早截圖只會看到灰方塊。
- 點卡片開 modal → 綠色 Download 按鈕；右邊 chevron 可選畫質，**優先挑 1080P**
  （4K 檔動輒數百 MB，而且有些片源只有 4096×2160 DCI，那是 1.896:1 不是 16:9，
  上軌會上下留黑邊——修修 2026-09-08 明確抱怨過）。**只收 16:9**。
- 檔案落在 `E:\` 根目錄（見 [[reference_browser_download_path]]），再搬進
  `data/finished-cut-runtime/acquisitions/<episode>-<cut>/`、寫 forensic JSON、
  `ActiveAssetStore.publish`。範本：scratchpad `publish_batch3.py`。
- Chrome extension 會偶發 disconnect，**重試就好**；那不是死路。

**再犯一次（2026-09-09）**：我在 elements 頁卡住一次，就把五支素材的清單丟回去要修修自己下載。
修修回「為什麼又要我下載素材？」——同一條紅線，第二次。**一次頁面卡住不是升級成「你去做」的理由，
是換一條路的理由。**
