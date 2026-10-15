---
name: multicam-director
description: >
  完整節目／單一段落三機切換：把單機直切的 Resolve timeline，依詞級說話者判定自動切
  CAM1／CAM2／CAM3（全景），全景套 zoom，並偵測聽者的純反應詞（哈/哇）內容驅動切一個
  短反應鏡頭到反應者臉上，聽者插話（重疊講話）切回插話者。Use when 修修 says 「三機切換」「切機位」「把這段變成三機版」
  「加反應鏡頭」「插話沒切回」，指著一條已經在 Resolve 裡剪好（單機或已是三機）的 timeline。
---

# multicam-director

**意圖層在這裡；實現層是 `scripts/run_multicam_full.py`（完整節目一條龍），它再呼叫
`scripts/run_short_director.py`（`build_shots` / `inject_reaction_cuts`）、`shared/speaker_assign.py`
（詞級說話者、機位偏移）、`shared/speaker_timing.py`（token）、`shared/resolve_append.py`（上軌坑）——
本 skill 只呼叫，不重新發明，也不要再每集在 scratchpad 寫一次性 driver。** 20260901 蘇予昕、
20260723 謝伯讓、20260903 程世嘉三集各寫過一次 driver；腳本是程世嘉那兩版的正式化：三機版（462 shots
驗證通過）與插話切回版（含短 shot 守門：54 個插話切回）。同一集 dry-run 重算出跟 driver 相同的
結果：`--no-interject` 133564 frames／462 shots；預設比 `--interject-base-min 0` 只有修修點名的
3 處機位不同（frame 範圍跟定案版逐格一致）；機位 audit 0 處。

## 完整節目：直接跑腳本

```bash
# 1. dry-run：不碰 Resolve，量三機偏移、判說話者、印 shot list 統計
python scripts/run_multicam_full.py "G:/Footages/<episode>" \
    --source-timeline "<episode>" --new-timeline "<episode>（三機）" \
    --wide-zoom 1.4 --cams "0=1_CAMERA 1.mp4,1=2_CAMERA 2.mp4" \
    --wide-cam "3_CAMERA 3.mp4" --mics "1_COMBO-1.wav,2_COMBO-2.wav" --dry-run

# 2. 同一行拿掉 --dry-run：Resolve 必須開著該集專案（專案名預設 = episode 資料夾名，或 --project）
```

dry-run 不讀 timeline，以「完整節目一整段」計算；已剪過的 timeline 實際會切成幾段，要連上 Resolve
才知道（非 dry-run 會先印「剪輯段」）。

腳本做的事（照下方「執行順序」，不用自己串）：讀原 timeline V1、coalesce 回剪輯段 → 三機對 program
feed 偏移（>1 frame 或量不到就 fail）→ token（legacy `words.json` 或 ADR-063 Memo release）→ `Audio/`
偵測 mic（**必須剛好 2 軌**，speaker 0 = 檔名排序第一軌）→ `build_shots` → `inject_reaction_cuts`
（哈/哇）→ 插話切回 → 開場 5s 全景 → 複製原 timeline 成新 timeline（帶 A1、字幕、V2 以上；原
timeline 不動，它就是備份）→ `delete_checked` 清新 timeline 的 V1 → 逐段 × 逐 shot `append_checked`
（`recordFrame = 段.t0 + (f0 - 段.s0)`）→ 全景套 ZoomX/ZoomY → 驗證（V1 每個 clip 的機位／位置／
LeftOffset 跟計畫完全一致、V1 涵蓋範圍跟原 timeline 相同、A1／字幕／V2 以上前後完全一致、原
timeline 未動、全景 zoom 全部正確）→ `SaveProject`。驗證任一項不過就 fail、不存檔；新 timeline
建了一半時錯誤訊息會叫你在 Resolve 刪掉它再重跑（腳本不自動刪）。

**拒絕條件（fail closed，不要繞過）**：`--new-timeline` 已存在（不覆寫）；原 timeline V1 有 program
feed 與這三台機位以外的素材（Intro/Outro 等）；Resolve 開的專案不對；timeline fps 跟 program feed
不同；mic 不是 2 軌、或跟 `--mics` 不符；缺 `normalized.wav`；機位不在 media pool、或 media pool 裡被
改過名；機位 FPS 或長度不夠。

**每集要確認的只有三件事**，都在 CLI 上明講、沒有預設值：
- `--cams`：哪台機位拍誰——抽格看 CAMERA1／CAMERA2 是誰，對上 mic 軌序（dry-run 會印
  `spk 0：mic 1_COMBO-1.wav → 1_CAMERA 1.mp4`，核對說話分鐘數合不合理）。再用 `--mics` 把兩軌
  mic 檔名寫死：`Audio/` 若多一軌夠活躍的 wav（備援錄音機、第二位來賓），自動偵測可能擠掉其中
  一軌、讓說話者跟機位對調；有 `--mics` 就會 fail 而不是默默切錯人。
- `--wide-cam`：雙人全景那台。
- `--wide-zoom`：**全景倍率每集量黑邊，修修可指定**。20260901 蘇予昕全景左右各 150px 黑邊，1.185x
  剛好消除，修修選 **1.2x**；20260903 程世嘉同樣左右各 150px 黑邊，修修指定 **1.4x**。換集若
  全景構圖不同要重新量；修修有指定就照他的。

## 前置：這條 timeline 現在長什麼樣

先讀，不要假設。三種可能結構——**腳本都用同一個方法處理**（`coalesce_segments`），這裡寫的是
它為什麼這樣做：

1. **單機直切**（一段或多段 `Default_*.mp4` 直切片段）——最簡單，`(tl0,tl1,src0)` 直接讀 V1 的
   `GetStart()/GetEnd()/GetLeftOffset()`。
2. **已經是三機**（要再套反應鏡頭，或修修事後又手動調整過）——**不能假設你記得的段落結構還有效**。
   20260901 蘇予昕就在這裡出過事：agent 用建立三機版當下記的舊 4 段對照表重建，蓋掉了修修事後在
   Resolve 裡又加的 15 分鐘手動編輯。**正確做法**：從目前 V1 的多機片段，用 source 連續性
   coalesce 回真實剪輯段——三機都跟 program feed 逐格對齊（先用 `shared.speaker_assign._measure_offset`
   驗證這集是不是也 0.0000s，不要假設），所以任一機的 `src_in` 换算下就是同一時刻在完整節目時鐘上的
   frame。相鄰片段 `tl` 連續**且** `src` 連續（`prev.src0 + (prev.tl1-prev.tl0) == this.src0`）才是
   同一段；`src` 跳號＝真的剪過一刀，即使 `tl` 是連續的。開工前用這個方法重新推導，不要用記憶
   ——腳本每次都從**目前**的 V1 重新推導，修修事後的手動編輯會被保留。
3. **重複使用同一段素材**（修修把同一段內容搬到 timeline 兩個不同位置）——詞級 token 用「絕對來源
   時間」查找，同一句話在兩個位置各自獨立投影、各自獨立判斷鏡位，這是正確行為，不用特殊處理。

**原 timeline 就是備份**：腳本 `DuplicateTimeline` 出 `--new-timeline`，只重建新 timeline 的 V1，
原 timeline 從頭到尾不動。要在已三機版上再加插話切回，就拿目前那條三機版當 `--source-timeline`、
取一個新名字（如 `<episode>（三機・插話切回）`）當 `--new-timeline`。重建前後腳本會比對 A1／字幕／
V2 以上的每一個 item，任何不同就 fail。

## 節奏參數（`run_multicam_full.DEFAULT_CFG`，修修已核准）

```python
DEFAULT_CFG = {
    "zoom_base": 1.0,
    "min_shot": 2.0,          # 短於此的說話者 run 不切鏡（併入前一 shot）
    "reaction_every": 20.0,   # 同人講超過這麼久，插一個聽者反應鏡頭
    "reaction_sec": 2.0,
    "reaction_style": "alternate",  # 聽者特寫／全景交替
    "opener_sec": 5.0,        # 開場（第一個剪輯段來源起點的頭 5 秒）用全景
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

## 插話切回：聽者插一句話，切回插話者（預設開，`--no-interject` 關）

修修 2026-10-15（20260903 程世嘉）：開場來賓講梗圖時修修插「我也是阿伯」，鏡頭沒切回給他。兩個原因：
(1) 那句跟來賓重疊，Memo 句級 token 整句判給來賓；(2) 就算判對，也短於 `min_shot` 2s 被併掉。所以
插話事件取**兩個來源的聯集**（`interjection_events`）：

- **token**：聽者（≠ 當下 talk shot 的 spk）被判定說了一句 0.5–2.5s 的話
- **mic 能量**（`load_envelopes` 的 50ms frame）：聽者 mic > -36 dB **且** ≥ 說話者 mic − 3 dB
  （重疊講話；說話時對方 mic 的 bleed 約 -45 ~ -60 dB）。hot frame 之間停頓 ≤ 0.3s 視為同一段
  （「我也是阿伯」頭兩個音中間只停 0.15s，沒接上的話切點晚 0.6s），長度 ≥ 0.9s，事件最長 2.5s

只處理落在 `kind=="talk"` 且無 `cam` 覆寫的 shot；同一人相鄰 < 0.6s 的事件合併。**合併後事件中點
所在的 base talk shot 短於 4.0s 就不插**（`--interject-base-min`）——短 talk shot 是一來一往的對話，
不是長篇講話；插話鏡頭補前後 pad、再把 < `min_residual` 的碎片併進去，會把說話者整支 shot 吃掉。
程世嘉：來賓「謝謝 真的是太捧場了」(228.30–230.94s，2.6s) 整支被換成修修鏡頭，3537.92–3541.24、
3997.88–4001.40 同型；加上這條後全集只有這 3 處機位改變，用 token 說話者與 mic 主導核對過都是改對。插入沿用
`inject_reaction_cuts` 的切分邏輯（觸發字用佔位字元「插」，`reaction_trigger_min_sec` 1.5、
`pad_lead` 0.15、`pad_lag` 0.45、`min_residual` 1.0），切到插話者**臉部特寫**；切出來的 shot 標
`kind="interject"`，跑完會印「插話切回 N（spk 0：…／spk 1：…）」。

**能量事件最短長度是調出來的**：0.5s → 114 個太碎、0.7s → 80、**0.9s → 57 個且修修指出的兩處都抓到**
（程世嘉；之後再加 4s 守門剩 54 個）。全部參數都有 CLI 旗標（`--interject-token-min/-max`、
`--interject-loud-db`、`--interject-near-db`、`--interject-event-min/-cap/-bridge`、
`--interject-merge-gap`、`--interject-base-min`），預設就是上面這組定案值；修修說太碎／漏抓再調，
一次調一個方向。

## 機位 audit：鏡頭在 A 臉上、B 卻明顯在講（只報告，不擋）

dry-run 與正式跑都會印「機位 audit：… N 處」（`audit_dominance`）。對最終 shot list 裡 `kind=="talk"`
且無 `cam` 的 shot，以 50ms mic 主導（某支 mic > -36 dB 且 ≥ 另一支 + 8 dB）找「另一人主導連續 ≥ 2s
（容許 0.5s 空白、主導比例 ≥ 50%）」的區間。全景、開場、反應鏡頭、插話切回都是刻意的，不看。
程世嘉最終版 **0 處**；把每支 talk shot 的說話者對調當反向對照會抓到 **358 處**，證明它看得到切錯人。
不是 0 就逐處列給修修看（印出的時間是節目來源秒數），不要自己調參數把它壓到 0。

## 執行順序（腳本已實作；這裡留給讀懂它在做什麼、或處理腳本拒絕的情況）

1. 從目前 V1 推導真實剪輯段（見上）。V1 只能有 program feed 與這三台機位；有其他素材就 fail——
   不要為了讓它跑過去而改原 timeline，先問修修那段素材要怎麼處理
2. 備份——腳本用 `DuplicateTimeline` 複製出新 timeline，原 timeline 從頭到尾不動
3. `shared.speaker_assign` 判定詞級說話者（含文字）
4. `build_shots([(0, 節目長)], words_tuple, CFG)` → `inject_reaction_cuts(shots, words_text, CFG)` →
   插話切回 → 開場全景（shot 用節目來源時間算，再逐剪輯段取交集）
5. 清空 V1（**先 `project.SetCurrentTimeline(timeline)` 再 `DeleteClips`**——Resolve 對非 current
   timeline 呼叫 `DeleteClips` 會靜默回 `False`，不報錯也不做事；`shared.resolve_append.delete_checked`
   已包好這個坑）
6. 逐段 × 逐 shot 算來源 frame，`AppendToTimeline`（`mediaType:1` 純視訊、帶 `recordFrame`；缺
   `startFrame`/`endFrame` 在 Resolve 21 有時會回 `[None]`，`shared.resolve_append.append_checked`
   已包好重試）
7. 全景片段套 `--wide-zoom`
8. 驗證（腳本自動）：V1 每個 clip 跟計畫一致、涵蓋範圍跟原 V1 相同、A1／字幕／V2 以上前後一致、原
   timeline 未動、全景 zoom。回報的 LeftOffset 容許 Resolve 的一格捨入（剪輯段 coalesce 同理），
   機位與 timeline 位置必須完全一致。腳本做不到的
   要人工補：抽驗幾個時間點的畫面與字幕是否對得上（說話者身份可用逐字稿自證，如「叫修修的名字」
   「叫來賓的名字」的句子）

## 已知限制（腳本不擋，跑之前心裡要有數）

- **開場全景放在來源時間**：第一個剪輯段來源起點的頭 5 秒。後面的段若重用同一段來源，那段也會是全景；
  第一段若是短的 cold-open 預告，全景會落在預告上，正片開頭沒有。
- **重建 V1 會丟掉原 V1 上每個 clip 的手動調整**（個別 zoom／pan／變速）——只有全景 zoom 會重套。
  有變速的 clip 也會讓 coalesce 的來源連續性失準。原 timeline 若有這類手動調整，先問修修。
- **V1 若跟音軌 linked**，清 V1 會連 A1 一起刪；驗證會抓到（A1 不同 → 不存檔），但要等全部上軌完。

## 怎麼問（如果真的要問）

節奏／全景倍率這類參數修修已經核准過一次基準值，**不用重新從零問**。真的要問，只在他明確表示「這版
節奏不對」之後，問清楚方向（太碎／太慢）與幅度，一次調完重跑，不要每個參數分開問。
