---
name: youtube-inset
description: >
  YouTube 照稿影片第 10 類「自家舊片導流」與第 13 類「梗圖」的 DP 元件：兩類共用一張角落卡
  （書封同款的細橘框、臉輕輕讓到另一側；舊片導流多一塊「延伸觀看」字塊）。DP 找圖 → 搬進集數資料夾
  `assets/inset/` → 寫規格檔 → 程式檢查、渲透明 overlay、寫收據。
  Triggers: /youtube-inset、「舊片導流」「放我之前那支影片的縮圖」「梗圖」「這裡放個梗圖」。只做這兩類。
  不用在：書封（youtube-book-cover）、人物照（youtube-person）、他人影片引用（youtube-others-clip）。
---

# youtube-inset — 舊片導流（第 10 類）＋梗圖（第 13 類）

**版本：v1.1（2026-11-27）**

YouTube 照稿影片的 DP 由一類一類的小 skill 組成（總調度與交接在 `youtube-dp`）。
這兩類由 Director 提、修修在 Bridge 核准；本 skill 把**已經核准**的列補齊素材並渲出小卡。

## 修修定的（不要自己改）

| 項目 | 定版 | 修修原話／依據 |
|---|---|---|
| 設計 | **B：沿用書封的細橘框**（粗細有變化、兩角同時起筆畫框、圖在框畫到一半時拍上），縮成角落小卡 | 2026-11-26 問「如果要讓設計語言是一致的話，選哪一個比較好？」→ 選 B、「直接做」 |
| 舊片導流 | 框左上多一塊橘色斜角字塊「延伸觀看」（品牌標籤的斜角形狀） | 同上 |
| 梗圖 | 純框，不加字 | 同上 |
| 位置 | 上方角落（預設右側） | 過去成片 4/4 都是角落小卡、不蓋臉（`docs/research/editing-grammar/2026-07-18-shoshotw-editing-grammar.md` 第 1、9 條） |
| 大小、搬移 | 圖寬 **720**（原本 560）；臉**輕移**到另一側（`nudge`：1.3 倍、臉到 40%／60%），幅度比書封（1.4 倍、1/3）小 | 2026-11-27「把人稍微移到側邊、挪出空間，然後把這個格子放大…調整的幅度或許就不用這麼大」→「直接做」（手機上 560 寬的縮圖字看不清） |
| 停留 | 蓋住的字幕總長，夾在 **3–4 秒**（原本 2–4） | 同上：臉讓開、退回各 0.5 秒，停 2 秒會有一半時間在動 |
| 存放 | 找到的圖**搬進這一集的資料夾** `<episode>/assets/inset/`，規格檔只收這裡的檔 | 同 stock：「下載下來的素材要移動到該專案的 folder 裡面」 |


## DP 怎麼做

### 舊片導流

1. 讀那一列蓋住的字幕與前後文：講的是哪個主題（例：「納瓦爾的四大槓桿……我之前有做過幾支影片詳細的解釋」）。
2. 在修修頻道找那支影片：

   ```bash
   python -m agents.brook.youtube_video.clips search "張修修 <主題關鍵字>"
   python -m agents.brook.youtube_video.clips info <video_id>
   ```

   頻道是「張修修的不正常人生 Shosho's Abnormal Life」。候選不只一支、又看不出哪支最相關時，
   在規格檔的 `note` 寫出其他候選，交件時跟修修說——**不要假裝確定**。
3. 縮圖：`https://i.ytimg.com/vi/<video_id>/maxresdefault.jpg`（1280×720），存成 `<episode>/assets/inset/<看得懂的名字>.jpg`。

### 梗圖

- 梗圖從網路來源下載**要先問修修**（來源、檔名、大小）——梗圖、影劇截圖有版權顧慮，
  沒有像 Envato 那樣的長期授權。修修自己提供的圖直接用。
- 負面清單同 stock：浮水印、AI 生成、跟那句話情緒相反的圖。

### 規格檔 `<episode>/visual_sources/<row_id>.json`

```json
{"image": "assets/inset/naval-get-rich-thumb.jpg",
 "source": "https://youtu.be/btxEXuD-wu0",
 "note": "如何用《納瓦爾寶典》來獨自升級；另一支相關：KbAtFK6BSZI",
 "side": "right"}
```

- `image` 必填，要在 `assets/inset/` 裡、是圖檔（jpg/png/webp）、短邊至少 320 px。
- `side` 選填：`right`（預設）或 `left`——臉偏右的畫面改放左；臉會讓到另一側。
- `source`、`note` 會進收據，之後查來源、換圖用。

### 檢查、渲染

```bash
python -m agents.brook.youtube_video.visuals.inset "G:/Footages/<ep>" --check   # 只檢查
python -m agents.brook.youtube_video.dp "G:/Footages/<ep>" --category old_video --category meme
```

交件：`<episode>/visuals/<row_id>.mov`（1920×1080 ProRes 4444 帶 alpha）＋收據。圖或規格改了就重渲。
鋪時間軸疊在 V2 以上；收據帶 `side`，`layout.py` 依它排輕移（卡在右 → `nudge_left`）。

## 實跑紀錄（2026-11-26，《財富階梯》流程測試）

- 舊片導流 r6e51eb0d16（「我之前有做過幾支影片詳細的解釋」）：修修頻道〈如何用《納瓦爾寶典》來獨自升級〉
  縮圖，3.57 秒；疊回原始錄影看過，卡在臉右側不蓋臉。另一支候選〈訂閱數已死？…槓桿力 x 當責力〉，
  兩支都抓不到字幕、無法確認哪支講四大槓桿最詳細 → 待修修挑。
- 梗圖 ra3769aeffa（「比起 Tech_Job 板或者股板上的神人們，實在魯到不行啊」）：規劃表要 PTT 神人 vs 魯蛇梗圖，
  來源待修修同意；測試先用 2024 舊片用過的海綿寶寶梗圖截圖當替身（檔名 `STANDIN-…`）。

- 2026-11-27 改版（卡 720、臉輕移、最短 3 秒）重渲兩列、疊回錄影：兩段眼睛都在 x≈0.52，
  1.2 倍下臉最多到 43%（不露黑邊的極限）→ 修修「試試看 1.3 倍」，臉到得了 40%。

## 驗收

1. `--check` 每一列都「可以交件」。
2. 鋪好時間軸後在 Resolve 播一次：臉輕輕讓開、卡不蓋臉、圖對得上那句話、舊片導流有「延伸觀看」。

## 還沒做

- 動態梗圖（GIF、影劇片段）：目前只收靜態圖。
