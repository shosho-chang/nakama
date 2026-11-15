---
name: youtube-book-cover
description: >
  YouTube 照稿影片第 4 類畫面「書封」的 DP 元件：把畫面規劃表（visual_plan.json）上核准的書封列，
  渲成透明 overlay 素材（書封正放＋粗細有變化的細橘框，框從兩角同時畫出來），交件到
  `<episode>/visuals/`。Triggers: /youtube-book-cover、「渲書封」「做書封」「這集的書封做出來」。
  只做這一類；其他類畫面各有自己的 skill。
  不用在：Podcast 長精華的書封卡（舊 `video/compositions/book_cover/` 紙卡系統）、YouTube 封面縮圖
  （thumbnail-brainstorm）。
---

# youtube-book-cover — 書封

**版本：v1.0（2026-11-15）**

YouTube 照稿影片的 DP 由一類一類的小 skill 組成（第一個是 `youtube-concept-title`）。本 skill 只負責把
**已經核准**的書封列做成素材；**哪裡要放**由 Director 提、修修在 Bridge
（`/bridge/video-plan/<episode>`）決定，本 skill 不改時機。

## 設計（修修逐輪定版，不要自己改）

設計系統是 **「張修修的不正常宇宙」**（claude.ai Design System artifact
`https://claude.ai/artifact/KEfVLi769roGThzUkQXj5S`，權威是裡面的品牌識別手冊 PDF）。
起點是《財富階梯》那集的 YouTube 封面書框（`E:\data\AgentOutput\20261112-wealth-ladder-cover\`）。

| 元素 | 定版 | 修修原話／來源 |
|---|---|---|
| 書封 | 正放、不轉角度、不加陰影或光暈 | 「書的封面在這個框框的間隔都要一樣」 |
| 框 | 10px 橘框（#F57331），被「上下兩邊略斜」的四邊形裁切，所以斜邊處線條漸細 | 「細框這樣還 OK」；「你要用上一個粗細有變化的那個框」 |
| 間隔 | 框與書四邊都是 36px | 「目前左邊比較寬、右邊比較窄，很奇怪」 |
| 進場 | 兩筆同時起筆：一筆從右上角往下畫右邊、再往左畫底邊；一筆從左下角往上畫左邊、再往右畫頂邊；0.5 秒畫完。每一筆只從自己那條線開始長 | 「我希望這兩個部分是同時啟動的」；「讓它只從左邊那條線開始長」 |
| 書封落定 | 框畫到一半（0.22 秒）書封拍上，帶一點 overshoot | 設計系統剪紙節奏 |
| 退場 | 書封縮走、框淡掉，約 0.2 秒 | 設計系統 motion token |
| 位置 | 畫面右側，書封高 620px（太寬的書改以寬 560px 為準），右緣離畫面邊 170px | 臉由「畫面搬移」特效讓到左側 1/3（另一個 skill） |

被否決過、不要再提的方向：框外的品牌碎片（「太多了，看起來很雜亂」，減量、加斜度也不行）、斜線、
光暈、書名字塊、等粗的 SVG 描邊（「框就沒有粗細、沒有變化了」）、繞中心掃一圈的畫法（左上角太早出現）、
ImageGen 發想（不守品牌規則）。教訓見記憶 `feedback_design_iteration_converge`：
每輪只動修修點名的那一處，不要往上疊零件。

元件原始檔：`video/compositions/book_cover_frame/compositions/book_cover_frame.html`。
改設計＝改這一份，並在 PR 附渲染截圖給修修看過。repo 裡的 `video/compositions/book_cover/`
是 Podcast 長精華還在用的舊紙卡，**不要動、也不要拿來用**。

## 書從哪裡來

規劃表那一列的「出處」（`source`）：

1. 填**書庫的書名**＝`data/books/<書名>/` 資料夾名（例：`財富階梯`；加不加《》都可以）。
   書庫位置跟 `shared/book_storage.books_root()` 一樣：有設 `NAKAMA_BOOKS_DIR` 就用它，
   否則是 repo 下的 `data/books`——**在 sibling worktree 跑要設 `NAKAMA_BOOKS_DIR=E:\nakama\data\books`**。
2. 書庫沒有的書：填**書封圖檔的完整路徑**（.jpg／.png／.webp）。網路找圖的來源規則見記憶
   `reference_book_cover_sources`（博客來、城邦、Kobo 擋直接下載；不要偽造 Referer）。
3. 「出處」空著時，取「給 DP 的指示」裡第一個《書名》。

都找不到 → 這一列回報「不知道是哪本書」或「書庫沒有《X》」，不渲。

## 時間（暫定）

從這一列蓋住的第一句字幕開始；停留＝那幾句字幕的總長，夾在 **2–6 秒**。
修修還沒定停留多久，第一集實測後再調 `MIN_SEC`／`MAX_SEC`。

## 版面

只有**疊加**（`layout: overlay`）。滿版書封還沒設計，規劃表選了滿版會被擋下並回報。

## 怎麼跑

```bash
python -m agents.brook.youtube_video.visuals.book_cover "G:/Footages/<episode>"
```

- 只渲 `category == book_cover` 且 `status == approved` 的列。
- `--row <id>` 只渲一列；`--force` 收據對得上也重渲。
- 一張約 25–30 秒（hyperframes 0.7.72）。書封圖、停留秒數、元件沒變就沿用，不重渲。

## 交件（所有分類共用的格式，`agents/brook/youtube_video/visuals/delivery.py`）

- `<episode>/visuals/<row_id>.mov`：1920×1080、29.97 fps、ProRes 4444 帶 alpha，
  長度＝停留秒數，第 0 格就是進場第一格。
- `<episode>/visuals/<row_id>.json`：收據。書封圖本身不存進收據（只記長度），但圖的內容算進
  `inputs_sha`——換了書封就會重渲。
- 鋪時間軸是之後的程式的事；本 skill 不碰 Resolve。

## 驗收（每次渲完都做）

1. 指令最後一行是 `N/N 張完成`；有 ✗ 的列回報給修修（通常是找不到書封）。
2. 至少抽一張疊在錄影上看：書封沒有被裁、框四邊間隔一樣、框畫的順序是兩角同時起筆。
3. `ffprobe` 確認 `prores 4444`、`yuva444p`、`30000/1001`。
