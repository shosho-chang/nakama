# 成片拆解 #1／4：《專注力協定》1228（ADR-051 剪輯文法研究）

**Date:** 2026-07-17
**素材:** `E:\video example\專注力協定 1228.mp4`（4K 23.976fps，18:25）＋ 662-cue SRT（秒數完全對齊）
**方法:** ffmpeg scene-detect（480p proxy、門檻 0.25、0.3s debounce）→ 268 鏡 → 每鏡 2–3 幀
（960px）→ 20 個 vision agent 逐鏡分類（268/268、抽查 3/3 正確）→ 與 SRT cue 對映 →
與 Director v2 storyboard diff。門檻 sanity check：6 個章節無聲空隙全部命中 cut 點。
**資料:** [`data/focus-protocol/`](data/focus-protocol/)（shots / classifications / ground_truth / diff，全部可溯源到秒）

## 總體統計

| 指標 | 成片實測 | 我的 v2 storyboard | 現行 guardrail |
|---|---|---|---|
| B-roll 鏡數 | **117**（268 鏡中） | 36 cutaways | — |
| B-roll 密度 | **6.35 / 分** | 1.95 / 分 | max 2.5 / 分 ❌ |
| B-roll 佔時 | **52.9%** | ~12% | — |
| B-roll 時長 | p50 4.4s / p90 7.6s | ~3s 上下 | — |
| stock 時長 | p50 3.9s | — | — |
| A-roll 連續段 | p50 6.0s / p90 16.4s / max 37.7s | 常見 20s+ | 連續 8 beat 警告 |

類型分布（成片）：stock 67、book_cover 13、quote_card 12、motion_graphic 12、diagram 8、
title_card 3、screen_recording 2。**overlay/疊加式佔 B-roll 24%**（書封滑入疊 aroll、
條列動畫佔左半、瀏覽器視窗 inset）；另有 **69 個 aroll 鏡帶效果**（letterbox 強調、
zoom punch、橘底白字 keyword overlay）——「A-roll」本身也是被設計過的。

## 文法發現（十條，皆有 shot id 佐證）

1. **密度是我以為的 2.5 倍以上**：6.35/分。guardrail 的 2.5/分是「滿版 cutaway」時代的
   誤估（2026-07-05 頻道分析用粗取樣，漏算 overlay 與快切）。
2. **Hook（0–55s）靠 jump cut 不靠 B-roll**：37 秒內 7 個 A-roll jump cut（2.8→4.8→1.4→
   5.8→15.3→2.6→5.0s，節奏由快到慢），唯一 B-roll 是書名點題時的書封卡（shot 8）。
   注意：修修 2026-07-17 指示 hook 要「逐名詞給 stock」——這是**新方向**，成片舊風格
   靠表演與剪接節奏；兩者並存記錄，執行以修修新指示為準。
3. **書封卡 = overlay 不是滿版**：13 張書封全部「滑入疊在 aroll 上」（主持人留在半邊，
   常同時手持實體書，shot 33/47/61）。v2 排的滿版 book_cover composition 形態錯誤。
4. **金句卡 = stock 底 + 白霧 + 中文大字**，不是純色卡（shot 35/149/…共 12 張，p50 5.4s）：
   壓暗或刷白的實拍畫面上疊置中大字。而且**金句在第一次唸到時就上卡**（成片 462.9s vs
   我排在「再強調一次」469.3s——晚了一拍）。出現頻率也遠超我以為：12 vs 我的 2。
5. **概念圖 diagram 是核心資產不是缺口 nice-to-have**：8 張（Hook 模型、traction/distraction
   雙向箭頭、心無旁鶩十字模型 18.8s、三領域同心圓）。作者原圖（NirAndFar.com）直接引用
   並保留署名。我在 v2 全部降級 none 的位置，成片全部有圖。
6. **etymology 動態字卡存在且是大場面**（shot 80/83/85，黑底星塵、彩色註解、23.2s）——
   我 v2 標記「無 composition 可用」的 beat 23 位置，成片是全片最長的 motion graphic。
7. **stock 是工作馬（67 鏡、p50 3.9s）且密集連發**：分心場景一句一鏡連換（打字→滑鼠→
   平板→揉眼→發呆，shot 17-26 區間），同語意段落用 3–5 支不同 footage 快切，不是一支蓋全段。
8. **外部素材皆有來源標示**：Nintendo 遊戲畫面（橘框播放器＋「Nintendo of America」）、
   Headspace 品牌動畫、作者演講照（瀏覽器視窗框 inset）——「引用感」是刻意的視覺語言，
   用「播放器框/瀏覽器框」包裝。
9. **章節卡只有 3 張**（不是我以為的每策略一張=4），p50 3.7s；部分章節切換直接用
   motion graphic 或 zoom 處理。
10. **「水流上的樹葉」用的是扁平風動畫插畫**（附出處「Flow Ne…」）而非實拍溪流——
    冥想類意象傾向插畫化，避免太寫實。

## Diff 成績單（v2 vs 成片）

recall **0.20**（117 個 B-roll 只對到 23）、miss 94、false positive 15。錯誤模式：

- **量級錯**：56 個 stock miss——成片對「畫面感語句」幾乎句句給畫面，我的「節制預算」
  完全錯頻。
- **形態錯**：book_cover/quote_card 命中的位置對但形態錯（滿版 vs overlay；純卡 vs stock底）。
- **禁區錯**：我因「無 composition」降級的 diagram/etymology 位置，成片全部都有——
  「寧缺勿猜」用錯了層級：該缺的是 render 資產，不該缺的是分鏡意圖。
- false positive 多數是「位置對、時間錯半拍」（金句提早上卡、章節卡不一定有）。

## 對兩個 skill 的直接含意（待四支交叉驗證後定稿）

- Director：分鏡密度目標 ~6/分（B-roll 佔時 ~50%）；hook 節奏規則；「一句一畫面」的
  stock 連發文法；金句первый次出現即上卡；visual_intent 需要能表達 overlay/滿版形態。
- DP：金句卡=stock底+大字 的複合配方；書封卡=overlay+實體書聯動；外部引用=播放器/
  瀏覽器框包裝＋署名；stock 搜尋要一次出 3–5 支同語意不同景。
- guardrails：max 2.5/分、連續同 component 規則、8-none 警告全部要按實測重校。
