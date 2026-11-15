---
name: youtube-person
description: >
  YouTube 照稿影片第 3 類畫面「人物彈入」的 DP 元件：把畫面規劃表（visual_plan.json）上核准的人物列，
  渲成透明 overlay 素材（去背頭肩照從畫面下方跳上來、縱向 motion blur，名字放白色字牌），交件到
  `<episode>/visuals/`。Triggers: /youtube-person、「渲人物彈入」「人物照片跳出來」「做這集的人物」。
  只做這一類；其他類畫面各有自己的 skill。
  不用在：長精華的人物貼紙（舊 `video/compositions/sticker_pair/`）、YouTube 封面的來賓去背（thumbnail-brainstorm）。
---

# youtube-person — 人物彈入

**版本：v1.0（2026-11-15）**

YouTube 照稿影片的 DP 由一類一類的小 skill 組成（已有 `youtube-concept-title`、`youtube-book-cover`、
`youtube-reframe`）。本 skill 只負責把**已經核准**的人物列做成素材；**哪裡要放**由 Director 提、
修修在 Bridge（`/bridge/video-plan/<episode>`）決定。

## 設計（修修逐輪定版，不要自己改）

起點是修修點名的《財富階梯》成片（`G:\Footages\20261027 財富階梯 AI Agent 版\20261112 財富階梯.mp4`）
49–54 秒，剪輯夥伴介紹作者 Nick Maggiulli 的那段。修修：「剪輯夥伴做得還不錯，就是從下方出現」。

| 元素 | 定版 | 修修原話／來源 |
|---|---|---|
| 大小與比例 | **人物的臉跟主持人的臉一樣大**：眼睛到下巴 218px，眼睛落在 (262, 607)；只露頭肩，肩膀與胸口以下被畫面邊緣切掉。從參考版 50.5s 用 mediapipe 量的 | 「你去比照《財富階梯》那個版本，它的人物 cutout 的大小以及比例」 |
| 左右 | 預設左下；「給 DP 的指示」寫「右側」就放右下（左右對稱，眼睛在 (1658, 607)）。參考版 Nick 在左、Naval Ravikant（655s）在右 | 參考版 |
| 進場 | 從畫面下方整張跳上來，0.3 秒、略過衝後落定 | 「跳出來的效果會比較好」 |
| Motion blur | 上下移動時有**縱向** motion blur，模糊長度＝當下速度 × 1/60 秒（180° 快門）；到定位就清楚 | 「從下往上跳出來，所以要有縱向的 Motion Blur，下去的時候也是」 |
| 退場 | 0.2 秒加速掉出畫面，同樣有縱向 motion blur | 同上 |
| 名字 | 白色字牌黑字（思源黑體 Black、52px，同重點概念 title 的字牌），照片靠畫面中間那一側、字幕上方，照片快到位時拍上 | 品牌設計系統（張修修的不正常宇宙） |

被否決過、不要再提的方向：照片縮小塞進角落（臉只有主持人的 1/3，「跟它的差有點多」）、
畫面同時放大讓位的大張版、照參考版的淡入加均勻模糊（改成跳出＋縱向 motion blur）。

元件原始檔：`video/compositions/person_pop/compositions/person_pop.html`。改設計＝改這一份，
並在 PR 附渲染截圖給修修看過。**每一格的照片位置與模糊量都寫成 `tl.set` 關鍵影格**——
HyperFrames 逐格 seek，GSAP 的 seek 不觸發 onUpdate，用回呼更新會漏格、閃格（2026-11-15 踩過）。

## 照片

規劃表那一列的「出處」填**本機圖檔路徑**。找照片照規格 §3.8：

1. Wikimedia Commons，只用允許商業使用的授權。
2. 官方來源：本人官網、出版社或公司新聞資料。
3. 本人公開大頭照（X、LinkedIn）。

禁止直接從搜尋引擎結果抓圖；擋下載（403）不要偽造 Referer 繞過。三個都找不到 → 標「等你提供素材」。
（2026-11-15 實測：Nick Maggiulli 在 Commons 沒有照片、官網 403。）

- 已經去背的 PNG（帶透明）直接用；沒去背的自動跑 `hyperframes remove-background`，
  結果存 `<episode>/visuals/people/<row_id>.png`，可以打開檢查。
- 程式用 mediapipe 量照片裡的臉（虹膜中點、下巴），算縮放與位置。照片裡要**剛好一張臉**。
- 要是**頭肩照、到胸口以下**：放大到臉跟主持人一樣大之後，下緣要到得了畫面底邊，否則擋下來回報。

## 名字

「畫面上的字」（`onscreen_text`），一行、約 14 個全形字以內（半形算 0.55）。空的就不放字牌。

## 時間（暫定）

從這一列蓋住的第一句字幕開始；停留＝那幾句字幕的總長，夾在 **2–6 秒**（參考版 Nick 約 4.5 秒）。
第一集實測後再調 `MIN_SEC`／`MAX_SEC`。

## 怎麼跑

```bash
python -m agents.brook.youtube_video.visuals.person "G:/Footages/<episode>"
```

- 只渲 `category == person` 且 `status == approved` 的列；`--row <id>` 只渲一列；`--force` 收據對得上也重渲。
- 需要：臉部模型 `E:\data\models\face_landmarker.task`（`FACE_LANDMARKER_MODEL` 可改）、mediapipe。
- 一張約 30 秒；要去背的再加幾秒。照片、名字、左右、秒數、元件都沒變就沿用。

## 交件（所有分類共用的格式，`agents/brook/youtube_video/visuals/delivery.py`）

- `<episode>/visuals/<row_id>.mov`：1920×1080、29.97 fps、ProRes 4444 帶 alpha，長度＝停留秒數。
- `<episode>/visuals/<row_id>.json`：收據。照片本身不存進收據，但內容算進 `inputs_sha`——換照片就重渲。

## 驗收（每次渲完都做）

1. 指令最後一行是 `N/N 張完成`；有 ✗ 的列回報給修修（通常是缺照片、照片太短、照片裡不只一張臉）。
2. 至少抽一張疊在錄影上看：人物的臉跟主持人差不多大、去背邊緣乾淨、名字沒有壓到主持人的臉。
3. `ffprobe` 確認 `prores 4444`、`yuva444p`、`30000/1001`。

## 兩個人（修修 2026-11-15「直接做」）

- 同一列介紹兩個人：「出處」寫**兩行**照片路徑、「畫面上的字」寫**兩行**名字（順序對應；要嘛都寫、要嘛都空）。
- 第一個人在左下、第二個人在右下，**晚 0.4 秒**跳出來；兩個人一起退場。兩張照片都照同一個比例
  （臉跟主持人一樣大）。
- 兩個名字字牌往各自的照片靠（離畫面邊緣 300px，一個人時是 560px），中間才不會撞在一起；
  兩個名字加起來超過約 22 個全形字就擋下來。
- 兩個人在稿子裡**相隔很久**才提到：拆成兩列，第二列「給 DP 的指示」寫「右側」，各自在講到名字時出現。
- **三個人以上不用人物彈入**（三張頭肩照會把主持人的臉擠住、太雜亂），擋下來回報；改用滿版畫面或一次一個人。
- 參考版裡沒有同一段介紹兩個人的畫面（逐秒掃過整集：只有 49s Nick 在左、655s Naval 在右，相隔 10 分鐘），
  所以 0.4 秒是建議值，第一集實測後再調 `PAIR_DELAY_SEC`。
