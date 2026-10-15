---
name: multicam-director
description: >
  完整節目／單一段落三機切換：把單機直切的 Resolve timeline，依詞級說話者判定自動切
  CAM1／CAM2／CAM3（全景），全景套 zoom，並偵測聽者的純反應詞（哈/哇）內容驅動切一個
  短反應鏡頭到反應者臉上。Use when 修修 says 「三機切換」「切機位」「把這段變成三機版」
  「加反應鏡頭」，指著一條已經在 Resolve 裡剪好（單機或已是三機）的 timeline。
---

# multicam-director

**意圖層在這裡；實現層是 `scripts/run_multicam_full.py`（完整節目一條龍），它再呼叫
`scripts/run_short_director.py`（`build_shots` / `inject_reaction_cuts`）、`shared/speaker_assign.py`
（詞級說話者、機位偏移）、`shared/speaker_timing.py`（token）、`shared/resolve_append.py`（上軌坑）——
本 skill 只呼叫，不重新發明，也不要再每集在 scratchpad 寫一次性 driver。** 20260901 蘇予昕、
20260723 謝伯讓、20260903 程世嘉三集各寫過一次 driver；腳本是程世嘉那版（462 shots 驗證通過）的
正式化，同一集 dry-run 重算出完全相同的 133564 frames／462 shots。

## 完整節目：直接跑腳本

```bash
# 1. dry-run：不碰 Resolve，量三機偏移、判說話者、印 shot list 統計
python scripts/run_multicam_full.py "G:/Footages/<episode>" \
    --source-timeline "<episode>" --new-timeline "<episode>（三機）" \
    --wide-zoom 1.4 --cams "0=1_CAMERA 1.mp4,1=2_CAMERA 2.mp4" \
    --wide-cam "3_CAMERA 3.mp4" --dry-run

# 2. 同一行拿掉 --dry-run：Resolve 必須開著該集專案（專案名預設 = episode 資料夾名，或 --project）
```

腳本做的事（照下方「執行順序」，不用自己串）：token（legacy `words.json` 或 ADR-063 Memo release）
→ `Audio/` 偵測 mic（**必須剛好 2 軌**，speaker 0 = 檔名排序第一軌）→ 三機對 program feed 偏移
（>1 frame 或量不到就 fail）→ `build_shots` → `inject_reaction_cuts` → 開場 5s 全景 → 複製原 timeline
（帶 A1 與字幕，原 timeline 不動）→ `delete_checked` 清 V1 → 逐 shot `append_checked`（帶
`recordFrame`）→ 全景套 ZoomX/ZoomY → 驗證（V1 空隙=0、機位順序=shot list、A1／字幕前後完全一致、
原 timeline 未動、全景 zoom 全部正確）→ `SaveProject`。驗證任一項不過就 fail、不存檔。

**拒絕條件（fail closed，不要繞過）**：`--new-timeline` 已存在（不覆寫）；原 timeline 不是單機直切
（V1 不是一整段 program feed）；Resolve 開的專案不對；mic 不是 2 軌；機位不在 media pool；機位 FPS
或長度跟節目不符。

**每集要確認的只有三件事**，都在 CLI 上明講、沒有預設值：
- `--cams`：哪台機位拍誰——抽格看 CAMERA1／CAMERA2 是誰，對上 mic 軌序（dry-run 會印
  `spk 0：mic 1_COMBO-1.wav → 1_CAMERA 1.mp4`，核對說話分鐘數合不合理）。
- `--wide-cam`：雙人全景那台。
- `--wide-zoom`：**全景倍率每集量黑邊，修修可指定**。20260901 蘇予昕全景左右各 150px 黑邊，1.185x
  剛好消除，修修選 **1.2x**；20260903 程世嘉同樣左右各 150px 黑邊，修修指定 **1.4x**。換集若
  全景構圖不同要重新量；修修有指定就照他的。

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

## 節奏參數（`run_multicam_full.DEFAULT_CFG`，修修已核准）

```python
DEFAULT_CFG = {
    "zoom_base": 1.0,
    "min_shot": 2.0,          # 短於此的說話者 run 不切鏡（併入前一 shot）
    "reaction_every": 20.0,   # 同人講超過這麼久，插一個聽者反應鏡頭
    "reaction_sec": 2.0,
    "reaction_style": "alternate",  # 聽者特寫／全景交替
    "opener_sec": 5.0,        # 開場（tl=0）用全景
}
```

`min_shot`／`reaction_every` 是從長片參數（`run_short_director.FORMAT_CFG["long"]`）起跳、再放慢調出來
的——完整節目篇幅遠長於精華片，原節奏會嚴重 over-edit。這組數字修修已核准，**當作起點直接用**，不用
每集重新發想；修修事後看過覺得節奏不對再依他的回饋調整（哪個方向調、調多少，見下方「怎麼問」），
改的是這個常數，不是另寫 driver。16:9 源進 16:9 timeline，機位滿幀直上（不 reframe、不用 `face_x`）。

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

## 執行順序（腳本已實作；這裡留給讀懂它在做什麼、或處理腳本拒絕的情況）

1. 確認/推導這條 timeline 的真實段落結構（見上）。**腳本只接受結構 1 的「一整段 program feed」**；
   結構 2（已剪過／已是三機）或多段直切會被拒絕——要先照上面的 coalesce 規則推導段落，那部分
   還沒進腳本，不要為了讓它跑過去而改原 timeline
2. 備份——腳本用 `DuplicateTimeline` 複製出新 timeline，原 timeline 從頭到尾不動
3. `shared.speaker_assign` 判定詞級說話者（含文字）
4. `build_shots(tl_segs, words_tuple, CFG)` → `inject_reaction_cuts(shots, words_text, CFG)` → 開場全景
5. 清空 V1（**先 `project.SetCurrentTimeline(timeline)` 再 `DeleteClips`**——Resolve 對非 current
   timeline 呼叫 `DeleteClips` 會靜默回 `False`，不報錯也不做事；`shared.resolve_append.delete_checked`
   已包好這個坑）
6. 逐 shot 算來源 frame，`AppendToTimeline`（`mediaType:1` 純視訊、帶 `recordFrame`；缺
   `startFrame`/`endFrame` 在 Resolve 21 有時會回 `[None]`，`shared.resolve_append.append_checked`
   已包好重試）
7. 全景片段套 `--wide-zoom`
8. 驗證（腳本自動）：V1 涵蓋範圍與空隙、A1／字幕前後一致、原 timeline 未動、全景 zoom。腳本做不到的
   要人工補：抽驗幾個時間點的畫面與字幕是否對得上（說話者身份可用逐字稿自證，如「叫修修的名字」
   「叫來賓的名字」的句子）

## 怎麼問（如果真的要問）

節奏／全景倍率這類參數修修已經核准過一次基準值，**不用重新從零問**。真的要問，只在他明確表示「這版
節奏不對」之後，問清楚方向（太碎／太慢）與幅度，一次調完重跑，不要每個參數分開問。
