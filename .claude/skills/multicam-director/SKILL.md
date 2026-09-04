---
name: multicam-director
description: >
  完整節目／單一段落三機切換：把單機直切的 Resolve timeline，依詞級說話者判定自動切
  CAM1／CAM2／CAM3（全景），全景套 zoom，並偵測聽者的純反應詞（哈/哇）內容驅動切一個
  短反應鏡頭到反應者臉上。Use when 修修 says 「三機切換」「切機位」「把這段變成三機版」
  「加反應鏡頭」，指著一條已經在 Resolve 裡剪好（單機或已是三機）的 timeline。
---

# multicam-director

**意圖層在這裡；實現層在 `scripts/run_short_director.py`（`build_shots` / `inject_reaction_cuts`）與
`shared/speaker_assign.py`（詞級說話者判定）——本 skill 只呼叫，不重新發明。** 20260901 蘇予昕全集
（195235 frames）與其 last section 段落已驗證跑通，以下是那次驗證出的**標準做法與預設值**，換集不用
從零重問，除非修修主動要求調整。

## 前置：這條 timeline 現在長什麼樣

先讀，不要假設。三種可能結構，處理方式不同：

1. **單機直切**（一段或多段 `Default_*.mp4` 直切片段）——最簡單，`(tl0,tl1,src0)` 直接讀 V1 的
   `GetStart()/GetEnd()/GetLeftOffset()`。
2. **已經是三機**（要再套反應鏡頭，或修修事後又手動調整過）——**不能假設你記得的段落結構還有效**。
   20260901 蘇予昕就在這裡出過事：agent 用建立三機版當下記的舊 4 段對照表重建，蓋掉了修修事後在
   Resolve 裡又加的 15 分鐘手動編輯。**正確做法**：從目前 V1 的多機片段，用 source 連續性
   coalesce 回真實剪輯段——三機都跟 program feed 逐格對齊（先用 `shared.speaker_assign._measure_offset`
   驗證這集是不是也 0.0000s，不要假設），所以任一機的 `src_in` 换算下就是同一時刻在完整節目時鐘上的
   frame。相鄰片段 `tl` 連續**且** `src` 連續（`prev.src0 + (prev.tl1-prev.tl0) == this.src0`）才是
   同一段；`src` 跳號＝真的剪過一刀，即使 `tl` 是連續的。開工前用這個方法重新推導，不要用記憶。
3. **重複使用同一段素材**（修修把同一段內容搬到 timeline 兩個不同位置）——詞級 token 用「絕對來源
   時間」查找，同一句話在兩個位置各自獨立投影、各自獨立判斷鏡位，這是正確行為，不用特殊處理。

**動手前一定先複製一份備份**（`DuplicateTimeline`），原timeline名稱加後綴（如
`<name> (加反應鏡頭前)`）。V1 全部清空重建前，**先確認 A1／字幕軌的數量與涵蓋範圍**，重建只碰 V1，
事後要能對比確認 A1／字幕沒被波及。

## 機位配置（換集校準一次；沒有 `director.json` 覆寫就用這組）

```python
CFG = {
    "cams": {"0": "1_CAMERA 1.mp4", "1": "2_CAMERA 2.mp4"},  # 0=主持人 1=來賓，換集核對麥克風分軌
    "wide_cam": "3_CAMERA 3.mp4",
    "zoom_base": 1.0,
    "min_shot": 2.0,          # 短於此的說話者 run 不切鏡（併入前一 shot）
    "reaction_every": 20.0,   # 同人講超過這麼久，插一個聽者反應鏡頭
    "reaction_sec": 2.0,
    "reaction_style": "alternate",  # 聽者特寫／全景交替
    "opener_sec": 5.0,        # 開場（tl=0）用全景
    "face_x": {"0": 880, "1": 1165},  # 換集要重新量
}
WIDE_ZOOM = 1.2  # 全景 1.2x——20260901 蘇予昕實測全景左右各 150px 黑邊，1.185x 剛好消除，
                 # 修修選 1.2x（多裁一點，人物更大）。換集若全景構圖不同要重新量黑邊。
```

`min_shot`／`reaction_every` 是從長片參數（`run_short_director.FORMAT_CFG["long"]`）起跳、再放慢調出來
的——完整節目篇幅遠長於精華片，原節奏會嚴重 over-edit。這組數字修修已核准，**當作起點直接用**，不用
每集重新發想；修修事後看過覺得節奏不對再依他的回饋調整（哪個方向調、調多少，見下方「怎麼問」）。

## 反應鏡頭：內容驅動（哈/哇），跟節奏驅動的聽者反應鏡頭是兩回事

`inject_reaction_cuts(shots, words_with_text, cfg)`——在 `build_shots` 之後呼叫，`words` 需要帶
`"word"` 文字欄位（`build_shots` 本身只吃 `(start,end,spk)` tuple，不夠）。

**觸發規則**（已用 3 個獨立 TA persona 驗證過，不是隨口定的）：
- 詞內容去除裝飾字元後**只剩**「哈」「哇」才算純反應（`REACTION_TRIGGER_CHARS`）。「哇這個非常常見」
  「然後他就覺得 哇」這種夾在正常句子裡的**不算**——那是說話者自己講話的內容，不是聽者反應。
- 反應詞的說話者必須跟當下 shot 的 spk **不同**才觸發。同一人自己講到一半笑出來，鏡頭本來就在他臉
  上，不算「聽的人反應」。
- 已有 `cam` 覆寫的 shot（開場全景、既有節奏驅動反應鏡頭）不動，避免打架。
- 反應鏡頭一律切反應者**臉部特寫**（不設 `cam`，由 spk 決定），不會跑去全景——修修要的是「切到我的
  畫面」，不是全景。

**這是機率性的，不是每集都會觸發。** 20260901 蘇予昕全集 683 鏡頭裡切了 2 個；last section 那段
95 鏡頭裡是 0 個（7 個候選全數因「反應者=當下鏡頭上的人」或「已有全景覆寫」被正確排除，不是漏檢）。
跑完直接報實際切了幾個、為什麼，不用先預告一定會有效果。

## 執行順序

1. 確認/推導這條 timeline 的真實段落結構（見上）
2. 備份
3. `shared.speaker_assign` 判定詞級說話者（含文字）
4. `build_shots(tl_segs, words_tuple, CFG)` → `inject_reaction_cuts(shots, words_text, CFG)`
5. 清空 V1（**先 `project.SetCurrentTimeline(timeline)` 再 `DeleteClips`**——Resolve 對非 current
   timeline 呼叫 `DeleteClips` 會靜默回 `False`，不報錯也不做事；`shared.resolve_append.delete_checked`
   已包好這個坑，直接用）
6. 逐 shot 算來源 frame，`AppendToTimeline`（`mediaType:1` 純視訊；缺 `startFrame`/`endFrame` 在
   Resolve 21 有時會回 `[None]`，用 `shared.resolve_append.append_checked`，不要自己重寫重試邏輯）
7. 全景片段套 `WIDE_ZOOM`
8. 驗證：V1 涵蓋範圍、空隙數（跟修剪前比對，不該多出新空隙）、A1／字幕數量前後一致、抽驗幾個時間點
   的畫面與字幕是否對得上（說話者身份可用逐字稿自證，如「叫修修的名字」「叫來賓的名字」的句子）

## 怎麼問（如果真的要問）

節奏／全景倍率這類參數修修已經核准過一次基準值，**不用重新從零問**。真的要問，只在他明確表示「這版
節奏不對」之後，問清楚方向（太碎／太慢）與幅度，一次調完重跑，不要每個參數分開問。
