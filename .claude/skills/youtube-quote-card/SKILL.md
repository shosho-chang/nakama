---
name: youtube-quote-card
description: >
  YouTube 照稿影片第 8 類畫面「金句卡」的 DP 元件：把畫面規劃表（visual_plan.json）上核准的金句列，
  渲成透明 overlay 素材——錄影壓暗、橘色引號、白色大字一行一行滑入、橘色出處、右下角探進品牌手冊
  p24 的橘色碎片——交件到 `<episode>/visuals/`。不插入時間。
  Triggers: /youtube-quote-card、「渲金句卡」「做這集的金句卡」「名言卡」。只做這一類。
  不用在：長精華的舊 `video/compositions/quote_card/`（LINE Seed 紙卡）、重點概念 title（第 2 類）、短片字卡。
---

# youtube-quote-card — 金句卡

**版本：v1.0（2026-11-16）**

YouTube 照稿影片的 DP 由一類一類的小 skill 組成（已有 `youtube-concept-title`、`youtube-book-cover`、
`youtube-person`、`youtube-reframe`、`youtube-chapter-card`、`youtube-section-title`）。
金句卡由 Director 提、修修在 Bridge（`/bridge/video-plan/<episode>`）核准；本 skill 只負責把
**已經核准**的列做成素材，不改時機、不改字。

## 設計（修修逐輪定版，不要自己改）

| 元素 | 定版 | 修修原話／來源 |
|---|---|---|
| 底 | 整個錄影壓暗 `rgba(40,37,37,0.84)`，先不用 stock 底 | 三款（手冊橘色字牌疊錄影／錄影壓暗／品牌灰底逐字打出）選「錄影壓暗」；來自修修過去成片的金句卡配方「畫面壓暗＋白色大字逐行滑入」（`docs/research/editing-grammar/2026-07-18-shoshotw-editing-grammar.md`） |
| 字 | 左上橘色引號；白色思源黑體 Black 96px 靠左（x=240），**一行一行從左滑入**；下方 40px 橘色出處 | 同上 |
| 品牌元素 | 品牌手冊 `brand-identity.pdf` 第 24 頁「影片開頭」右下角貼著底邊的 3 塊橘色碎片（章節卡 POLYS 第 4、5、6 塊，原樣不重畫），從畫面下方探進來，往下推 110px 只露一截 | 「我想要增添一點點品牌的元素」→ 三款（出處橘色字牌／底邊碎片／品牌碎片細條）選底邊碎片 →「底下的 pattern 再排幾個，目前有點醜」→ 三種排法選「只在右下」 |
| 動作 | 壓暗 0.24s 淡入、碎片 0.3s 探進來、字一行接一行（每行 0.36s）；停留結束前 0.4 秒起字淡掉、碎片縮回、壓暗淡出 | — |

被否決過、不要再提的方向：錄影上每行一塊橘色字牌（手冊重點字卡）、品牌灰底逐字打出；
出處放橘色字牌、金句下一條品牌碎片細條；底邊碎片照 p24 原排（「有點醜」）、左右對稱、整排重複。

元件原始檔：`video/compositions/quote_dim/compositions/quote_dim.html`（多邊形座標已內嵌）。
改設計＝改這一份，並在 PR 附渲染截圖給修修看過。

## 卡上放什麼

- **金句**＝「畫面上的字」（`onscreen_text`）：整句原話。
  - 可以自己用換行分行；沒分的長句自動斷行，盡量斷在標點後面，標點不放行首。
  - 每行約 15 個全形字（半形算 0.55），**最多 3 行**；再長就擋下，回 Bridge 縮短或拆成兩張。
- **出處**＝「出處」（`source`）：一行，例 `Morgan Housel《致富心態》`；畫面上顯示成「— 出處」。
  - 可以留空（不顯示）。
  - 不能是網址、不能換行、約 30 個全形字以內——它會直接印在畫面上。

## 時間

從這一列蓋住的第一句字幕開始；停留＝那幾句字幕的總長，夾在 **4–9 秒**
（修修過去成片的金句卡 p50 5.4–8.6 秒，讀字要駐留）。要更長或更短，是改規劃表蓋住的字幕範圍。

## 怎麼跑

```bash
python -m agents.brook.youtube_video.visuals.quote_card "G:/Footages/<episode>"
```

- 只渲 `category == quote_card` 且 `status == approved` 的列；`--row <id>`、`--force` 同其他元件。
- 一張約 25–35 秒（元件固定渲 9 秒再截）。字、出處、秒數與元件沒變就沿用。

## 交件（所有分類共用的格式，`agents/brook/youtube_video/visuals/delivery.py`）

- `<episode>/visuals/<row_id>.mov`：1920×1080、29.97 fps、ProRes 4444 帶 alpha，長度＝停留秒數。
- `<episode>/visuals/<row_id>.json`：收據；變數（分好的行、出處、秒數）、`start_sec`、`duration_sec`。

## 驗收

1. 指令最後一行是 `N/N 張完成`；有 ✗ 的列回報給修修（通常是太長、出處是網址或空的金句）。
2. 抽一張疊在錄影上看：分行斷得自然（不在詞中間斷、標點不在行首）、字沒碰到右下碎片、退場後回到錄影。
3. `ffprobe` 確認 `prores 4444`、`yuva444p`、`30000/1001`、長度對。

## 還沒定

- stock 底：修修過去的主配方是 stock 底壓暗；等第 6 類 stock footage 做好再談要不要換底。
- 自動斷行只看標點，不懂詞；斷得不好就在 Bridge 自己加換行。
