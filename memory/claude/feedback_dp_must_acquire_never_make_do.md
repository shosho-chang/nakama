---
name: DP 一定要去下載素材，絕對不准硬挑
description: 「絕對不要硬挑，一定要去下載」——修修講過十幾次；成因是 packet 寫死 only-catalog，且該 cut 從沒跑過自己的採購
type: feedback
---

**絕對不要從既有素材目錄硬挑一支來湊。配不上就去取得新素材。**

**Why:** 修修從 2026 年中反覆講過十幾次（「每一個影片都要去經過 director 跟 DP 去下載新的 stock footage」「絕對不要硬挑，一定要去下載」），但它一直復發，因為根因不在人、在 packet：

1. `_worker_packet.py` 把 DP 的 `stage_instruction` 寫死成
   `implement_current_events_using_only_catalog_references`——**字面上叫 DP 只准用目錄**。
2. 同一份 packet 又寫死 `dp_catalog_references_only: True`（而且全 repo 沒有任何程式讀它，
   純粹是餵給回答者的指令）。
3. 收件端 `resolve_dp_reference()` 要求 `asset_ref` 存在於素材庫——這一條是對的
   （provenance），但配上前兩條就變成「只能在別人買剩的東西裡挑」。

**決定性的證據**：2026-09-17 蘇予昕長3（`punch-L02`），`acquisitions/` 只有
`20260901-punch-L03` 與 `20260901-punch-L04`，**punch-L02 從來沒跑過自己的採購**。
它那 31 支目錄整份都是另外兩支買剩的，所以 DP 不管怎麼挑都會挑到「為別句話買的」：

- 「我今天同學欺負我」→ 女孩坐空教室（那支是為「老師當著同學的面誤會我」買的）
- 「效忠家庭的連結感」→ 沙發對談（那支是為「我老婆幫我接話」買的）

同一支長片一晚抓到兩次。

**How to apply:**

- 接到 DP packet 時，先看 `constraints.acquire_when_catalog_cannot_serve_intent`。
  目錄裡沒有一支扣得回這個 event 自己的意圖 → **去取得新素材**，不要退而求其次。
- 搜尋順序照舊：先看 `E:\data\stock footage\asian man`（修修的化身），再上 Envato
  （走 `app.envato.com`，見 [[reference_envato_download_automation]]）。
- 取得的素材放進 `acquisitions/<episode>-<cut>/`，活目錄會在 `_RunState` 建構時重讀，
  所以買完重新派工就引用得到。
- 開場那種「十秒內要給具體畫面」的位置**不能用 `intentional_aroll` 代替採購**——
  那是把問題藏起來，不是解決。
- 素材下載後要搬到素材庫並更新 INDEX（修修 2026-09-15 指示）。

**我自己的違規（2026-09-17）**：我當晚就查出第 2 條，跟修修報告了，然後**把它排在待辦
最後一件，還說「拆掉之前要先想清楚換成什麼規則」**。那是在拖延一個本來就沒有歧義的
指令。他的反應是「這件事情已經發生過大概快 10 次了吧，為什麼都還沒有解掉？」——
**指令夠明確時不要用「先研究一下」當緩衝**。
