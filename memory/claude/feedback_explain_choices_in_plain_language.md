# 給修修選項時必須詳細白話解釋，不可用短標籤逼選

**修修 2026-07-05 Director grill 明確 push back**：「我不太喜歡你用這樣短短的敘述就讓我選。我希望你在讓我做每一個選擇的時候，都能詳細解釋並且用白話說明，我才比較有辦法判斷。」

**Why**：修修對系統內部技術細節（pipeline vs skill、schema、worker）沒有即時 context；AskUserQuestion 的 option label + 兩行 description 承載不了判斷所需資訊，等於逼他在不理解後果下賭。

**How to apply**：AskUserQuestion 之前先用正文把每個選項白話展開 — 它是什麼、選了之後日常長什麼樣、技術上牽動什麼、失敗成本在哪 — 用類比（自動販賣機 vs 照手冊做事的人）勝過術語。選項卡片只當「投票按鈕」，論述放正文。推薦選項要講清楚為什麼推薦，而不只是標「（推薦）」。同次 grill 採用此格式後修修連續 10 題順利裁決。

相關：[[feedback_grill_then_panel_for_big_adr]]
