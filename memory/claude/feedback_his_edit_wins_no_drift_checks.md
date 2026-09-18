---
name: feedback_his_edit_wins_no_drift_checks
description: 封存後他又動 timeline 就直接 overwrite 重封存；不要加 uid／長度／hash 的分岔偵測——他動過什麼是最高指導原則
metadata:
  type: feedback
---

**他手上那條 timeline 就是最高指導原則。封存之後他再動（改字幕、調鏡位），做法是直接
overwrite 舊的封存、重封存一次——不是先偵測分岔再問他。**

他 2026-09-18 的原話：

> 如果我又動了 Timeline（例如改字幕），那就直接 overwrite 之前的結果，重新封存一次。
> 反正我動過什麼都是最高指導原則，不要再檢查。

同一天他對整條發布線也講過一次：**「這個路線不要有太多無謂的檢查。」**

**Why**：我當時的規劃裡提了一個「比一次 timeline uid」的偵測（snapshot 已經記了 uid，
`_timeline_uid()` 現成，兩個 API 呼叫、零 hash），成本確實很低。他還是砍掉——因為那道
檢查能做的只有「擋住他、然後問他要不要繼續」，而答案永遠是繼續。**成本低不等於該加；
一道永遠得到同一個答案的閘，價值是負的。**

同一個判準也砍掉了另一條：publish 端不要重驗 Editorial Master 的 hash／lineage。
seal 的時候已經驗過，這裡只讀。（而且 `verify_editorial_master` 會 sha256 整份 8–10 GB
的 master.mp4，board 每 5 秒重整一次的話等於每次重讀十 GB。）

**How to apply**：
- 設計任何閘之前先問：**它擋下來之後，修修的下一步是什麼？** 如果答案是「還是照做」，
  那就不要加。
- 已經在 seal／gate 驗過的東西，下游只讀，不重驗。
- 例外是**資訊會靜默消失**的那種檢查（人名被遮、章節整份不生效、CC 上錯字幕）——
  那些不是擋他，是替他看見，照留。
- 相關：[[feedback_editorial_master_sealing_is_mine]]、[[feedback_dont_ask_permission_at_every_step]]
