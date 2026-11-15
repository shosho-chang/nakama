---
name: youtube-chapter-card
description: >
  YouTube 照稿影片第 1 類畫面「章節卡」的 DP 元件：把畫面規劃表（visual_plan.json）上核准的章節卡列
  （稿上每個 `##` 自動產生），渲成 2 秒滿版插入素材——品牌手冊 p24「影片開頭」的碎片框從外圍衝進來、
  標題逐字浮現、最後炸開露出下一段錄影——交件到 `<episode>/visuals/`。
  Triggers: /youtube-chapter-card、「渲章節卡」「做這集的章節卡」「章節轉場」。只做這一類。
  不用在：長精華的轉場卡（舊 `video/compositions/transition_title/`）、`###` 小標題遮罩、Outro 轉場。
---

# youtube-chapter-card — 章節卡

**版本：v1.0（2026-11-15）**

YouTube 照稿影片的 DP 由一類一類的小 skill 組成（已有 `youtube-concept-title`、`youtube-book-cover`、
`youtube-person`、`youtube-reframe`）。章節卡由稿上的 `##` 自動產生（規格 §3.4），Director 不判斷；
本 skill 只負責把**已經核准**的章節卡列做成素材。

## 設計（修修逐輪定版，不要自己改）

| 元素 | 定版 | 修修原話／來源 |
|---|---|---|
| 框 | 品牌手冊 `brand-identity.pdf` 第 24 頁「影片開頭」的**向量原稿**：18 個橘色多邊形（#F57331），原樣放大到 1920×1080（手冊座標 [320,70]–[755,315]），中間灰底 #E1E1DF。橘色大長方形從畫面四邊往內放射，連成一整圈 | 「這個框框是由很多的矩形形成的」「你仔細看我 T 恤上面的樣式」「一整個矩形將畫面框起來」 |
| 進場 | 每一塊沿「畫面中心 → 它的位置」方向從畫面外衝進來，順時針一塊接一塊（每塊 0.24s、略過衝），約 0.5 秒圍好 | 「讓這些 pattern 從外圍加到畫面的邊緣形成類似框框的樣子」 |
| 標題 | 黑色思源黑體 Black，正中一行，**一個字一個字浮現**（每字 0.05s，從下 24px 浮上） | 「然後 title 依序浮現」 |
| 退場 | **炸開**（最後 0.3 秒）：標題放大淡掉，每一塊同時往外飛出畫面，灰底淡掉，露出下一段錄影 | 三種退場（炸開／整張推走／原路退回）裡選了「炸開」 |
| 長度 | 2 秒（規格 §3.4「插入約 2 秒」）；安靜、不配音效 | 規格 |

被否決過、不要再提的方向：舊 LINE Seed 紙卡、成片的深藍金色光絲（不是品牌）、灰底上下碎片帶、
滿版碎片＋白字牌、深色底碎片條、冷灰底章節編號、碎片原圖裁成長條（朝內被切成直線、形狀變了）、
碎片團一團一團散在四周（「一區一區雜亂的形狀」）、滿版碎片＋中間大面板。

元件原始檔：`video/compositions/chapter_card/compositions/chapter_card.html`（多邊形座標已內嵌）。
改設計＝改這一份，並在 PR 附渲染截圖給修修看過。

## 標題

「畫面上的字」（`onscreen_text`）＝稿上 `##` 的文字，一行。字級最大 136px，字多時自動縮小讓整行
放進框內（寬 1240px）；縮到 88px 還放不下就擋下來，請修修縮短 `##`。標題裡的空格會保留。

## 時間（鋪時間軸要照這個擺）

- 插在那一節第一句字幕（`before_cue`）之前。
- 素材 2 秒、**帶 alpha**：前 1.7 秒滿版（把錄影往後推 1.7 秒），**最後 0.3 秒疊在下一段錄影上**
  （炸開時露出來的就是下一段錄影的開頭）。
- 收據多記：`insert_sec: 1.7`、`overlap_next_sec: 0.3`。

## 怎麼跑

```bash
python -m agents.brook.youtube_video.visuals.chapter_card "G:/Footages/<episode>"
```

- 只渲 `category == chapter_card` 且 `status == approved` 的列；`--row <id>`、`--force` 同其他元件。
- 一張約 20 秒。標題與元件沒變就沿用。

## 交件（所有分類共用的格式，`agents/brook/youtube_video/visuals/delivery.py`）

- `<episode>/visuals/<row_id>.mov`：1920×1080、29.97 fps、ProRes 4444 帶 alpha，2 秒。
- `<episode>/visuals/<row_id>.json`：收據；`start_sec`＝插入點（原始錄影的秒數），外加 `insert_sec`、
  `overlap_next_sec`。

## 驗收

1. 指令最後一行是 `N/N 張完成`；有 ✗ 的列回報給修修（通常是標題空的或太長）。
2. 抽一張疊在錄影上看：框跟手冊 p24 一樣、標題在正中、炸開後露出的是錄影。
3. `ffprobe` 確認 `prores 4444`、`yuva444p`、`30000/1001`、2 秒。

## 還沒定

- 章節編號、標題大小／字牌、速度都維持現狀，修修之後要調再說。
- 稿上 `##` 自動產生章節卡列的程式（初剪／Director 那邊）還沒做；鋪時間軸的程式要照上面的時間擺。
