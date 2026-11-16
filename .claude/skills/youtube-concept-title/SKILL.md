---
name: youtube-concept-title
description: >
  YouTube 照稿影片第 2 類畫面「重點概念 title」的 DP 元件：把畫面規劃表（visual_plan.json）
  上核准的重點概念列，渲成透明 overlay 素材（白色字牌黑字＋品牌碎片 pattern），交件到
  `<episode>/visuals/`。Triggers: /youtube-concept-title、「渲重點概念 title」「做重點概念字卡」
  「這集的重點概念卡做出來」。只做這一類；其他類畫面各有自己的 skill。
  不用在：長精華（舊 punch_card 系統，之後才會改套本元件）、短片字卡（shortform-cut）。
---

# youtube-concept-title — 重點概念 title

**版本：v1.0（2026-11-15）**

YouTube 照稿影片的 DP 由一類一類的小 skill 組成。修修 2026-11-15：

> 「DP 這 16 種功能，我每一個都會跟你合作討論一個 skill 出來，這不用擔心，之後會一個一個做出來、
> 一個一個去調整到最好。」

這是第一個。它只負責把**已經核准**的重點概念列做成素材；**哪裡要放**由 Director 提、修修在
Bridge（`/bridge/video-plan/<episode>`）決定，本 skill 不改時機、不改字。

## 設計（修修逐輪定版，不要自己改）

設計系統是 **「張修修的不正常宇宙」**（claude.ai Design System artifact
`https://claude.ai/artifact/KEfVLi769roGThzUkQXj5S`，權威來源是裡面的品牌識別手冊 PDF）。
**不是**長精華用的「半透明紙卡＋手繪橘線」，也不是 Bridge 網頁的 `docs/design-system.md`。

| 元素 | 定版 | 修修原話／來源 |
|---|---|---|
| 字牌 | 白色、黑字（Noto Sans TC Black＝思源黑體），字保持水平，上下兩邊平行、左右微斜 | 「把字的底圖再改回白色」 |
| 背後 | 一條品牌碎片 pattern（橘底灰碎片，設計系統原圖，不重畫） | 「我目前傾向背景有我們品牌 pattern 的那一個」 |
| 形狀語言 | 上下兩邊平行，左右兩邊各有一個斜度；**不是**把長方形轉一個角度 | 「依照我現在 T 恤上面那個形狀……上下邊是平行的，而左右邊各有一個斜度」 |
| 四種碎片條 | 往右斜／往左斜／上窄下寬／一邊斜一邊近直，每張卡隨機挑一種 | 「讓這四個都採用，然後隨機選擇要用哪一種」 |
| 動作 | 碎片條刷開 → 字牌拍上落定 → 快速退場（設計系統的剪紙節奏） | 設計系統 motion token |

被否決過、不要再提的方向：長精華紙卡＋手繪底線、字卡整張旋轉、灰字牌、字牌與碎片同灰
（「感覺沒有很好看」）、照 T 恤一模一樣的梯形。

元件原始檔：`video/compositions/concept_title/compositions/concept_title.html`。
改設計＝改這一份，並在 PR 附渲染截圖給修修看過。

## 卡上放什麼

- **只放概念名稱本身，一行**（例：萬分之一法則、1% 法則、複利篩選）。不放整句、不放結論。
  長精華的 Hero 大字卡就是因為要 AI 挑「夠強的一句」而四次失敗、2026-09-15 退役。
- 上限約 12 個全形字（半形字算 0.55）；太長程式會擋，回 Bridge 縮短。
- 字取自規劃表的「畫面上的字」（`onscreen_text`）。空的、換行的、太長的都不渲。

## 時間

從這一列蓋住的第一句字幕開始；停留＝那幾句字幕的總長，夾在 **1.5–4 秒**。
（舊片 keyword 字卡多半 2–4 秒。）要更長或更短，是改規劃表蓋住的字幕範圍，不是改元件。

## 怎麼跑

```bash
python -m agents.brook.youtube_video.visuals.concept_title "G:/Footages/<episode>"
```

- 只渲 `category == concept_title` 且 `status == approved` 的列。
- `--row <id>` 只渲一列；`--force` 收據對得上也重渲。
- 一張約 25–30 秒（hyperframes 0.7.72）。收據對得上目前的字與元件就沿用，不重渲。

## 交件（所有分類共用的格式，`agents/brook/youtube_video/visuals/delivery.py`）

- `<episode>/visuals/<row_id>.mov`：1920×1080、29.97 fps、ProRes 4444 帶 alpha，
  長度＝停留秒數，第 0 格就是進場第一格。
- `<episode>/visuals/<row_id>.json`：收據——分類、元件、變數（字、形狀、秒數）、元件原始檔雜湊、
  `inputs_sha`、`start_sec`（放在原始錄影的哪一秒）、`duration_sec`。
- 鋪時間軸是之後的程式的事；本 skill 不碰 Resolve。

## 驗收（每次渲完都做）

1. 指令最後一行是 `N/N 張完成`；有 ✗ 的列回報給修修（通常是字太長或空的）。
2. 至少抽一張疊在錄影上看一格（`ffmpeg -i 錄影 -i visuals/<id>.mov -filter_complex overlay`），
   確認字型是黑體 Black、碎片條沒有被切掉、字沒有出框。
3. `ffprobe` 確認 `prores 4444`、`yuva444p`、`30000/1001`。
