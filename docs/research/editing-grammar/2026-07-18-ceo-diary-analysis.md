# 成片拆解 #2／4：《執行長日記》20240701（ADR-051 剪輯文法研究）

**Date:** 2026-07-18
**素材:** `E:\video example\20240701 The Diary of a CEO.mp4`（4K 23.976fps，24:10 = 1450.03s）＋ 955-cue SRT（底部有燒入台詞字幕）
**方法:** 與拆解 #1 相同管線（scene-detect 0.25 → 382 鏡 → 86-agent 逐鏡分類 → overlay 第二遍
130 aroll 鏡 549 幀 → 47 事件），資料見 [`data/ceo-diary/`](data/ceo-diary/)。
**全局 caveat:** 2024-07 舊風格成片，單片觀察待四支交叉；本片是**人物傳記型**說書
（傳主 Steven Bartlett），素材文法與概念型（專注力協定）差異極大。

## 總體統計

| 指標 | 本片 | 對照：專注力協定 |
|---|---|---|
| 總鏡數 | 382（24.17 分） | 268（18.42 分） |
| B-roll 事件/分 | **3.52** | 3.26 |
| B-roll 時間佔比 | **64.1%**（全系列最高） | 52.9% |
| 每事件鏡數 | p50 **2**、max **14** | p50 1、max 10 |
| Overlay 事件（第二遍） | 47（substantive ~37；另 letterbox 6、轉場 4） | 37（substantive 30） |
| 合計視覺事件密度 | ~**5.0/分** | 4.89/分 |
| B-roll 鏡時長 | p50 2.65s / p90 7.34s | p50 4.4s / p90 7.6s |
| A-roll 連續段 | p50 4.8s / p90 12.8s / max 42.5s | p50 6.0s / p90 16.4s / max 42.4s |

類型分布：**kol 166**（B-roll 鏡的 66%）、stock 34、motion_graphic 23、photo_still 10、
screen_recording 7、doc_screenshot 4、diagram 3、title_card 2（另有 ≥6 張橘底章節卡內嵌於
aroll 鏡，見發現 4）。

## Hook 解剖（0–60s）

三段式，與專注力協定（jump cut＋keyword 卡）完全不同：

1. **0–13.2s：aroll 快切＋inset 三連發**——6 鏡 aroll（1.5→3.1→1.9→1.8→0.8→4.2s），
   1.7s 就滑入 Bartlett 肖像 inset（shot 2）、6.7s 英文書封（shot 4）、9.2s 中文書封（shot 6）。
   書封 8 秒內出現兩次。
2. **13.2–30.2s：kol 九連發蒙太奇**（shots 7–15，p50 ~2.1s）——DOAC 名場面快剪（圓桌對談、
   名人來賓 Cole Sprouse／Alex Hormozi 樣貌），每鏡左上「影片來源：The Diary of a CEO」。
3. **30.2–59.5s：傳主生平轉場**——1.3s aroll 標點（shot 16）後進 Behind the Diary 紀錄片＋
   底片框老照片（shots 18/19，KODAK 邊框滑入）＋Social Chain logo 動畫（shot 22）。

首個 B-roll 13.2s（介於書籍型 15s 內的頻道慣例）；hook 的 B-roll 全部是**傳主素材**，
零 stock——傳主的臉就是 hook。（單片觀察）

## 文法發現（單片觀察；證據=shot_id/秒數）

1. **人物傳記型 = kol 引用為 B-roll 主體**（166 鏡）。來源生態系：Behind the Diary 紀錄片 ~52
   鏡、DOAC podcast ~23、TED/TED-Ed ~14、VICE ~7、Ali Abdaal ~7、Huberman/Big Think 各 5、
   BBC/CNBC/TODAY 各 4、Manchester's Finest ~11 等 **30+ 來源**，每鏡左上「影片來源：X」。
2. **kol 蒙太奇連發是節奏主武器**：≥3 連發共 30+ 處；最長 14 鏡（shots 57–70，179–270s
   傳主創業故事，kol 10＋photo 1＋motion_graphic 2 混排）；每事件 p50 2 鏡（專注力 p50 1）——
   引用素材天然帶「多角度快切」的既有剪輯節奏。
3. **⚠️ 單源取用長度與現行 guardrail 衝突**：Behind the Diary 累計 ~52 鏡 × p50 2.5s ≈ 130s+，
   DOAC 累計 ~60s+——遠超 guardrails 的 KOL 單源 ≤20s 紅線（D6）。**這不是自動放寬的理由**
   （D6 是版權合規設計），列為需修修裁決的政策問題：傳主型選題是否例外、或需改變素材策略。
4. **章節卡（法則一～八）滿版橘卡為主、dissolve 進出**：獨立 title_card 鏡僅 2（shot 137 法則四
   501.3s、shot 233 法則六 924.1s），但 overlay 第二遍抓回內嵌卡：法則一（shot 65, 204.7s）、
   法則二（344.6s）、法則三（430.0s）、法則七（1105.5s）、法則八（1259.5s）——orange 滿版卡
   ＋白色 icon＋米白/橘黃斜向色塊 wipe 轉場是固定配方；法則五未在抽幀中觀察到卡（可能落在
   幀間隙，不確定）。章節卡與旁白唸出「法則N」同步（shot 137 cues 337–342）。
5. **橘色瀏覽器視窗框 inset 是本片 signature**（photo_inset 17 事件）：人物肖像（Bartlett×6、
   巴菲特 1264s、Michael Scott 劇照 1164.9s）、logo 卡（BBC＋BuzzFeed 雙卡 449.6s）、
   **自家影片縮圖導流 ≥4 次**（高效閱讀法 350.6s、三連縮圖 410.4s、破解意志力 738.3s、
   新晨間習慣 871.1s）——比專注力協定（2 次）更密。
6. **letterbox 縮框 6 次**：cinematic 強調手法，最完整例 670.9s（Queen〈Under Pressure〉哏，
   台詞字幕移入下方黑邊）——專注力協定未見此 vocabulary。
7. **TED-Ed 動畫長蒙太奇撐科學段**：壓力生理學段 677–735s 以 motion_graphic 7 連發＋4 鏡
   （shots 177–195）全用 TED-Ed 素材（附來源），單段近 60s——概念解釋外包給現成動畫，
   不是自製 composition。
8. **文獻視覺化＝論文截圖＋黃色 highlight 隨旁白逐步移動**：shots 203/205/207（767–803s，
   三鏡共 ~29s，美國成人壓力調查＋All-Cause Mortality 表格）＋shot 222 長條圖 16.2s 逐步
   高亮——doc_screenshot p50 9.6s，遠長於其他 B-roll，讀表需要駐留時間。
9. **自我素材引用**：shots 131–134（488–498s）修修自己的環球單車 vlog＋照片（紅箭頭指
   吉他袋），來源標「張修修 Shosho Chang」——拿自己的故事對照傳主論點。
10. **片頭 channel ident**：shot 27（70.3s）塗鴉風「張修修的不正常人生」白底動畫卡 2s——
    hook 收尾後才進 ident（不是影片第一幀）。
11. **片尾 CTA**：keyword 卡「張修修的自由之路」（1411.3s）＋「shosho.tw/free」（1418.3s）＋
    頻道頁 screen_recording 快切 4 鏡（1351–1354s，單鏡 0.3–1.8s）。

## 資料品質備註

- shot 65（204.7s，10.8s）與 shot 137 含未切開的剪點（dissolve 吞切點，a/b/c 幀內容不同）——
  章節卡計數已依 note＋overlay 事件修正，是本管線已知系統性弱點（與 design 片同）。
- KOL 來源計數是從 note 正則抽取的近似值（多字來源名被截斷），量級可信、個位數不保證。

## 對兩個 skill 的含意（provisional，待四支交叉）

- **Director**：需要「傳主型」分型——B-roll 預算主力配 kol／archive，而非 stock；分鏡時
  應前置「來源素材研究」步驟（傳主的 podcast／紀錄片／演講清單）。hook 配方=aroll 快切
  ＋inset 連發（書封 8s 內兩次）→ kol 蒙太奇。章節卡 vocabulary 需含「滿版橘卡＋wipe 轉場」。
- **DP**：kol 包裝配方（黑格紋框＋左上來源標）；老照片=底片框＋滑入；橘視窗框 inset 系統
  （人物/logo/自家縮圖三用途）；letterbox 強調；論文截圖=黃 highlight 逐步移動。
- **Guardrails**：KOL 單源 ≤20s 與傳主型選題根本衝突（發現 3）——需修修裁決，不可自行放寬。
