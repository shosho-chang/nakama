---
name: feedback-verify-clock-before-measuring
description: 量音檔證據前先確認自己在哪個時鐘上——master／source／tight 三個時鐘差幾十秒
metadata:
  type: feedback
---

2026-08-30 我兩次拿「量到的數據」下錯結論，根因都是**用錯時鐘**：

1. 複審 noise 候選時只看「結束於候選前／開始於候選後」的詞，WhisperX 把「是」
   對成 1.74s 整個把候選包住，那個字在畫面上消失 → 判成「ASR 漏字，所以是語音」。
   同一個誤判在兩支短片發生 6 次。
2. 查 punch-S07 的切鏡對不對，把 **master 時間**直接套到 **source 音檔**上量 mic
   能量（兩者差 ~46s），量出「來賓在講、鏡頭卻切主持人」，宣告是 bug。用正確的
   source 時間重量，主持人的 mic 高 25–30 dB——鏡頭是對的。

**Why**：這條產線同時存在 source（原始音檔）、master（成片）、tight（剪過的短片）
三個時鐘，conform map 在它們之間投影。拿甲的座標去查乙的檔案，數字看起來很像
證據，其實在量別的地方。

**How to apply**：
- 量任何音檔證據前，先講清楚「這個秒數是哪個時鐘的」，需要時用
  `source_to_master_sec` / `tight_to_feed` 投影過去
- 對照用的欄位要把**足以推翻自己**的資訊也印出來（`enclosed_by` 就是為此加的）
- 宣告「這是 bug」之前先反問：我的量測跟工具用的是同一個座標系嗎
