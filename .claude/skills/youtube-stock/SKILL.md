---
name: youtube-stock
description: >
  YouTube 照稿影片第 6 類畫面「Stock footage」的 DP 元件：把畫面規劃表（visual_plan.json）上核准的 stock 列，
  逐鏡上 Envato 搜尋、下載 16:9 實拍、搬進集數資料夾 `assets/stock/`、寫規格檔（每一鏡用哪個原檔、
  從第幾秒取多長），程式檢查後寫收據；鋪時間軸時**原檔直接放上 Resolve**（不轉檔），只做滿版。
  Triggers: /youtube-stock、「找 stock」「抓實拍素材」「這幾列 B-roll 補齊」「跳接蓋 B-roll」。只做這一類。
  不用在：Podcast 長精華（brook-dp）、短片（shortform-dp）、修修自己的素材（第 9 類）、他人影片引用（第 11 類）。
---

# youtube-stock — Stock footage（第 6 類）

**版本：v1.0（2026-11-26）**

YouTube 照稿影片的 DP 由一類一類的小 skill 組成（總調度與交接在 `youtube-dp`）。stock 列由 Director 提、
修修在 Bridge 核准；本 skill 把**已經核准**的列補齊素材：搜尋 → 下載 → 搬進集數資料夾 → 寫規格檔 → 檢查。

## 修修定的（不要自己改）

| 項目 | 定版 | 修修原話 |
|---|---|---|
| 版面 | **只做滿版**（分類清單 layout 固定 full） | 2026-11-26 選「只做滿版」 |
| 交件 | **原始素材直接上 Resolve**，不先轉成一支成品檔——修修在 Resolve 裡可以直接拖動取用段落、換鏡頭 | 2026-11-26 選「原始素材直接上 Resolve」 |
| 存放 | **下載下來的素材要移動到該專案的 folder 裡面**：`<episode>/assets/stock/`。規格檔只收這裡的檔 | 2026-11-26：「還要再增加一個規則：下載下來的素材要移動到該專案的 folder 裡面」 |
| 下載 | DP 自己開瀏覽器抓，不要先問、不要把候選丟回去 | 「Director、有什麼有關 B-roll、Stock footage 的需求，DP 這個就直接去抓就對了，不用再問我了」（`memory/claude/feedback_dp_acquires_stock_without_asking.md`） |
| 跳接 | 段落開頭露出來的剪點，首選蓋滿版 B-roll | 「首選是那邊就直接用一個 B-roll 來跳過」（規格 §3.6） |

## 選片規則（跟長片 brook-dp「選片鐵則」共通）

1. **畫面＝語意**：不看字幕也要能從畫面讀出那句話的意思，**情緒極性也要對**（整句是壓力、困境時，微笑歡樂就算相反）。
2. **逐名詞、單鏡 ≤3 秒、只蓋那個詞**（`youtube-director`）；一句連發 3–5 鏡是修修過去成片的節奏
   （`docs/research/editing-grammar/2026-07-18-shoshotw-editing-grammar.md`）。
3. **全片一片一用**：同一支原檔整集只出現一次（程式會擋）。
4. **每一鏡針對那一句重新搜**：本地素材庫、其他集下載過的，都不能代替搜尋（修修 2026-10-07）。
5. **負面清單**：浮水印、綠幕、AI 生成、文字疊圖、通用商務擺拍假笑；小孩用平板／3C（修修視為負面）。
6. **抽象概念沒有對應畫面 → 不硬湊**：退回 Director 改成重點概念 title 之類，比硬湊隱喻好。
7. **寧缺勿猜**：找不到語意準的，把這一列標「重排」寫原因，不要塞一支不準的。

## DP 怎麼做

### 1. 讀這一列

規劃表上的「給 DP 的指示」（Director 寫要什麼畫面）、蓋住哪幾句字幕（= 這一列多長）。

### 2. 每一鏡出英文搜尋詞

每一鏡 3–5 組不同切面（字面／視覺隱喻／場景／證據感／情緒），帶景別。

### 3. 上 Envato 搜尋、下載

用 Claude in Chrome（修修的 Envato 已登入），**走 `app.envato.com`，不要走 `elements.envato.com`**（後者的頁面永遠載不完、工具一律逾時）：

```
https://app.envato.com/search?itemType=stock-video&term=<英文>&filter.orientation=Horizontal
```

- 縮圖要等 25–35 秒才載入；從 href 取 uuid 直接開品項頁 `app.envato.com/search/stock-video/<uuid>`。
- 品項頁右欄看尺寸：**只收 16:9**（1920×1080、3840×2160）。**DCI 4096×2160 不收**——比例不對，上軌會有黑邊（修修 2026-09-08 抱怨過）。
- 下載鈕右邊選畫質，**優先 1080P**（4K ProRes 動輒數百 MB）。按一次，跳「Automatically licensed」就是授權了。
- 檔案落在 `E:\` 根目錄（瀏覽器預設下載處）。

### 4. 搬進集數資料夾

把檔案從 `E:\` 搬到 `<episode>/assets/stock/`，取看得懂的檔名（例：`coffee-pour.mov`）。**規格檔指到這以外的地方，程式會擋。**

### 5. 寫規格檔 `<episode>/visual_sources/<row_id>.json`

```json
{"shots": [
  {"file": "assets/stock/coffee-pour.mov", "in": 3.2, "dur": 2.4,
   "source": "https://app.envato.com/search/stock-video/<uuid>", "note": "倒咖啡"},
  {"file": "assets/stock/latte-art.mov", "in": 0.5}
]}
```

- 鏡頭照播放順序；`in`＝從原檔第幾秒開始取；`dur`＝這一鏡多長。
- **最後一鏡可以不寫 `dur`**＝剩下的秒數；全部加起來要等於這一列的長度（差一格內，程式自動補齊最後一鏡）。
- `source`（品項頁網址）、`note`（這鏡是什麼）會抄進收據，之後查授權、換鏡頭用。
- 取用段落挑畫面最好的那幾秒（避開開頭結尾的晃動、淡入淡出）。

### 6. 檢查、交件

```bash
python -m agents.brook.youtube_video.visuals.stock "G:/Footages/<ep>" --check   # 只檢查
python -m agents.brook.youtube_video.dp "G:/Footages/<ep>" --category stock     # 寫收據
```

程式檢查：這一列是滿版；檔案在 `assets/stock/`；16:9、至少 1080p；取用段落沒超出原檔；單鏡 ≤3 秒；
加起來等於這一列；同一支原檔沒在這一列或別的 stock 列用過。不合格的訊息直接照改。

stock **不渲染**（DP 報告寫 `delivered`，不是 `rendered`）：收據 `<episode>/visuals/<row_id>.json` 列出每一鏡（原檔、`src_in`、格數、原檔格率），
`media` 是空的。規格檔或原檔大小改了，收據就重寫。

## 鋪時間軸（`layout.py`，所有分類共用）

照收據一鏡一鏡把**原檔**放上 V2（同一時間已經有東西就往上一軌），**只放畫面、不放聲音**。
原檔格率不是 29.97（Envato 很多是 25、24）時，取用的起點與長度換成原檔自己的格數，Resolve 放上去時自己 conform——
換算後的長度會無條件捨去（25 fps 取 50 格只蓋 59 格；24 fps 取 28 格只蓋 34 格）。所以每一鏡取「放上去不超過
該蓋格數」的最多格數，放完讀回實際結束格、下一鏡從那裡接：鏡頭之間不留空隙、不壓到下一列，一列最多早一格結束。
4K 原檔由專案的縮放設定縮成 1920×1080。修修在 Resolve 裡可以直接拖動取用段落（slip）、換鏡頭。

**Resolve 切換專案會當機**：程式從別的專案切到「已經有時間軸」的專案後馬上匯入檔案，Resolve 整個當掉
（2026-11-26 兩次）。`timeline._open_project` 切換後會先等 15 秒；本來就開著那個專案就不切、不等。

## 實跑紀錄（2026-11-26，《財富階梯》流程測試 r596b8ead1e）

第 6–8 句 4.87 秒三鏡，從 Envato 搜尋、下載到鋪進 Resolve 整條走過一次：

| 字幕 | 鏡頭 | 原檔 | 時間軸（格） |
|---|---|---|---|
| 可以不用做討厭的工作啊 | 闔上筆電起身離開 | 1080p 25 fps H.264 | 303–355（52，與收據同） |
| 也可以隨時出發去環遊世界 | 拖行李走向機場 | 1080p 25 fps H.264 | 355–414（59，與收據同） |
| 完全不用擔心錢 | 西裝男躺在海灘躺椅 | 1080p 24 fps Photo JPEG | 414–448（34，收據 35，早一格） |

- 縮圖約 25 秒才載完；Envato 品項頁顯示的就是唯一畫質時，下載鈕直接是「Download xxMB」，按一次就下載。
- 挑取用段落：對每支原檔出每 0.5 秒一格的縮圖表，看動作發生在哪幾秒（例：起身離開在 2.0–3.7 秒）。

## 驗收

1. `--check` 每一列都「可以交件」。
2. 鋪好時間軸後在 Resolve 播一次：每一鏡對得上那個詞、沒有黑邊、沒有浮水印、沒有聲音。

## 還沒做

- 素材取得還沒有寫取得紀錄檔（sha256 等）；目前來源記在規格檔的 `source`、收據裡也有。
