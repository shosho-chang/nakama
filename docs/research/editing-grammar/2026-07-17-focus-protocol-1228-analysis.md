# 成片拆解 #1／4：《專注力協定》1228（ADR-051 剪輯文法研究）

**Date:** 2026-07-17（v1.1 修訂 2026-07-18：三稽核 18 findings 全數採納＋overlay 第二遍偵測）
**素材:** `E:\video example\專注力協定 1228.mp4`（4K 23.976fps，18:25）＋ 662-cue SRT（秒數完全對齊）
**全局 caveat:** 本片為 **2024-12 發布的舊風格成片**，n=1；文中「文法」皆為**單片觀察，
待 #2–#4 交叉驗證**後才升級為規則。修修 2026-07-17 的新指示（hook 逐名詞給 stock 等）
優先於舊風格。

**方法:**
1. ffmpeg scene-detect（480p proxy、門檻 0.25、0.3s debounce）→ 268 鏡；6 個章節無聲空隙全部命中 cut 點
2. 每鏡 2–3 幀（960px）→ 20 vision agents 逐鏡分類；稽核分層重驗 27 鏡 26/27 相符
3. **Overlay 第二遍**（稽核揪出 scene-detect 盲區後補做）：148 個 aroll 鏡以 1s 間隔密集抽幀
   （467 幀）→ 10 agents 偵測疊加層 → **37 個 overlay 事件**（scene-detect 完全看不見）
4. SRT cue 對映 → ground truth；與 Director v2 storyboard 以「雙向覆蓋率 ≥0.5 ＋ near-miss ±5s」
   三欄制 diff（v1.0 的 IoU 0.3 判準經稽核證實對長短懸殊區間失效，已棄用）

**資料:** [`data/focus-protocol/`](data/focus-protocol/)（shots / classifications / overlay_events /
ground_truth / diff / qc，全部可溯源到秒）

## 總體統計（v1.1 修正版）

| 指標 | 成片實測 | 我的 v2 storyboard | 現行 guardrail |
|---|---|---|---|
| B-roll 鏡數 | 117（268 鏡中） | — | — |
| **B-roll 事件**（連續鏡合併，與 cutaway 同單位） | **60 = 3.26/分** | 36 = 1.95/分 | max 2.5/分（偏低 ~30%） |
| 每事件鏡數 | p50 1、**max 10**（快切連發） | 全部 1 | 無此概念 |
| Overlay 事件（疊在 aroll 上，scene-detect 抓不到） | **30**（keyword 字卡 14、章節/步驟橫幅 5、照片 inset 8、清單卡 2、書封 1） | 0 | 無此概念 |
| **合計視覺事件** | **90 = 4.89/分** | 36 = 1.95/分 | — |
| B-roll 佔畫面時間 | 52.9% | ~12% | — |
| B-roll 鏡時長 | p50 4.4s / p90 7.6s（stock p50 3.9s） | ~3s | — |
| A-roll 連續段 | p50 6.0s / p90 16.4s / **max 42.4s**（片尾 CTA 段；次長 37.7s 為 hook 段） | — | 連續 8 beat 警告 |

類型分布（shot 級）：stock 67、book_cover 13、quote_card 12、motion_graphic 12、diagram 8、
title_card 3（另有 ≥2 張滿版步驟橫幅內嵌於 aroll 鏡，見發現 9）、screen_recording 2。
**「疊在 aroll 上」的 overlay 佔 B-roll 鏡 ~14%**（16/117；v1.0 誤把 12 張滿版金句卡算進
overlay 得 24%，稽核已糾正——金句卡是「滿版 stock 底＋大字」複合形態，另計）。

## 文法發現（單片觀察；每條標注證據等級）

1. **視覺事件密度 4.89/分（B-roll 事件 3.26/分＋overlay 0.63/分＋快切展開）**。guardrail 的
   2.5/分與「B-roll 事件」同單位比較偏低約 30%——不是 v1.0 說的 2.5 倍（那是鏡數 vs 決策數
   的單位錯置，稽核糾正）。真正的新概念是**一個 cutaway 事件可展開成最多 10 鏡快切**。
2. **Hook（0–55s）＝jump cut＋keyword 字卡，B-roll 僅書封**：37 秒 7 個 A-roll 快切
   （2.8→4.8→1.4→5.8 短拳連打 → 15.3s 長 hold → 2.6→5.0 收短；hold 段內疊「預先計畫/實際
   行動」「極致專注力」兩張關鍵字卡——v1.0 說 hook 無 overlay 是 scene-detect 盲區）。
   修修新指示「逐名詞給 stock」為未來方向，並存記錄。
3. **書封卡=overlay 滑入疊 aroll**（13/13，主持人留半邊；shot 47/61 同時手持實體書）。
4. **金句卡=滿版 stock 底＋白霧＋中文大字**（12 張、p50 5.4s；shot 35/152），且**金句第一次
   唸到就上卡**（462.879s 與 SRT cue 逐毫秒對齊；本片單例，待交叉驗證）。
5. **概念圖是核心資產**：8 張 diagram（Hook 模型引作者原圖附署名 shot 64、心無旁鶩十字模型
   18.8s shot 95、三領域同心圓）。v2 當 composition 缺口降級 none 的位置成片全有圖。
6. **etymology 動態字卡 23.2s 全片最長 motion graphic**（shot 80/83/85）——對應 v2 storyboard
   **beats 42–45**（v1.0 誤植 beat 23）全排 none 的區間。
7. **stock 連發**：同語意段落 3–5 支不同 footage 一句一換（shot 17–26 五連發；單一事件
   最高 10 鏡）。
8. **外部素材一律標示來源**：Nintendo 遊戲畫面（播放器框＋署名）、Headspace 動畫、
   作者演講「Talks at Google」用**照片 inset**（不是影片片段——KOL 需求比想像輕）、
   **自家舊影片縮圖 inset 做互相導流**（慢速工作力 shot 214、上線時間管理術 shot 219——
   順帶解謎：《上線時間管理術》=Laura Mae Martin《Uptime》，畫面縮圖可證）。
9. **章節/步驟標記 = 橫幅為主、滿版卡為輔**：滿版 title_card 僅 3 張，但 aroll 內嵌
   橫幅≥5（「精通內在誘因」shot 100、「步驟一」「步驟二」shot 125、「你人生的引力有哪些？」
   shot 185）——**步驟也有卡**（v2 否決步驟卡的判斷與成片相反），只是形態是「短橫幅疊 aroll」
   不是滿版章節卡。
10. **關鍵字字卡是高頻小型武器**（14 張：心無旁鶩超能力/專業人士/時間管理等於痛苦管理/
    水流上的樹葉/自我疼惜/hell yeah or no/心無旁鶩時刻/為什麼/心無旁鶩模型…）——講到抽象
    概念名詞時上 2–4s 橘底/白底字卡，這是 v2 完全沒有的 vocabulary。
11. **「水流上的樹葉」用扁平風動畫插畫**（附出處）而非實拍——冥想意象傾向插畫化。

## Diff 成績單（三欄制修正版）

| | v1.0（IoU 0.3） | **v1.1（覆蓋率 0.5＋near ±5s）** |
|---|---|---|
| 位置命中 | 23 | **24**（其中 type 也對 15） |
| near-miss（差 ≤5s） | — | **38** |
| 真 miss | 94 | **55** |
| recall | 0.20 | 位置 0.21／**含 near 0.53** |
| false positive | 15 | **真 FP 6**＋near-FP 8（且 FP beat 5/63 位置其實有 overlay 字卡，屬 ground truth 第一遍漏切） |

錯誤模式（修正後仍然成立）：

- **量級錯**：55 個真 miss 多為 stock——「畫面感語句幾乎句句給畫面」vs 我的節制預算。
- **形態錯**：位置命中 24 中 type 全對僅 15；書封（滿版 vs overlay）、金句（純卡 vs stock底）、
  步驟卡（否決 vs 橫幅）皆形態錯。
- **層級錯**：diagram/etymology/keyword 字卡三類 vocabulary 在 v2 完全缺席。
- 38 個 near-miss 說明**位置嗅覺不差、切點與時長對不準**——這是可以用規則修的。

## 對兩個 skill 的含意（provisional，待四支交叉）

- Director：分鏡出「視覺事件」不只 cutaway——vocabulary 需含 keyword 字卡、橫幅、inset、
  快切連發（1 事件 N 鏡）；密度目標暫定 3–3.5 事件/分＋overlay 另計；hook 節奏規則；
  金句首次出現即上卡。visual_intent 欄位需能表達 overlay/滿版形態。
- DP：金句卡配方（stock 底＋白霧＋大字）；書封 overlay＋實體書聯動；引用包裝（播放器框/
  瀏覽器框/照片 inset＋署名）；自家舊片縮圖導流；stock 一次出 3–5 支同語意不同景。
- guardrails：max_cutaways_per_minute 以「事件」定義重校（2.5 → ~3.5）；新增 overlay 層
  規則；「連續同 component」對快切連發（同 kind 不同 footage）需放行——已在 PR #1005 做對。

## QC / 稽核紀錄

三個獨立稽核 agent（資料正確性/方法論/結論推論）共 18 findings（5 major），v1.1 全數採納：
單位錯置、overlay regex artifact（24%→14%）、aroll run max 42.4、IoU→覆蓋率判準、beat/shot
引用錯誤、「первый」typo、單片推廣標注。詳見 [`data/focus-protocol/qc.yaml`](data/focus-protocol/qc.yaml)。
