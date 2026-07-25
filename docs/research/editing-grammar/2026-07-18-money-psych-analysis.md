# 成片拆解 #3／4：《致富心態》20240814（ADR-051 剪輯文法研究）

**Date:** 2026-07-18
**素材:** `E:\video example\20240814 The Psychology of Money part 1.mp4`（4K 23.976fps，27:18 = 1638.09s）＋ 991-cue SRT（底部有燒入台詞字幕）
**方法:** 同管線（scene-detect 0.25 → 382 鏡 → 分類 → overlay 第二遍 179 aroll 鏡 736 幀 →
53 事件），資料見 [`data/money-psych/`](data/money-psych/)。
**全局 caveat:** 2024-08 舊風格成片，單片觀察待交叉；本片是**概念型書籍說書**（與專注力協定
同型），是四支中與未來產線最像的樣板。

## 總體統計

| 指標 | 本片 | 對照：專注力協定 |
|---|---|---|
| 總鏡數 | 382（27.30 分） | 268（18.42 分） |
| B-roll 事件/分 | **3.41** | 3.26 |
| B-roll 時間佔比 | 58.1% | 52.9% |
| 每事件鏡數 | p50 1、max 11 | p50 1、max 10 |
| Overlay 事件（第二遍） | 53（substantive ~47；另 letterbox 5、片頭 ident 1） | 37（substantive 30） |
| 合計視覺事件密度 | ~**5.1/分** | 4.89/分 |
| B-roll 鏡時長 | p50 4.0s / p90 8.76s | p50 4.4s / p90 7.6s |
| A-roll 連續段 | p50 6.1s / p90 16.3s / max 31.9s | p50 6.0s / p90 16.4s / max 42.4s |

類型分布：**stock 125**（B-roll 主力，p50 3.84s）、kol 29、screen_recording 14、
**quote_card 11**（全系列最多）、motion_graphic 11、doc_screenshot 7、book_cover 2、
diagram 1、photo_still 1。與專注力協定的節奏輪廓幾乎重合——**概念型的文法是穩定的**。

## Hook 解剖（0–60s）

1. **0–19.5s：aroll 為底＋書封 inset 轟炸**——6 鏡 aroll，書封 inset 出現 4 次（英文版 5.5s、
   中文版 7.4／15.7／29.9／39.2s——40 秒內含後續共 **5 次**書封曝光），加一張橘底大字卡
   「金錢心理學」（14.7s，shot 4）。
2. **19.5–29.7s：首兩鏡 stock 疊白色大字**（shots 7/8：「《致富心態》…」三行字逐句滑入，
   stock 當底、文字是主角）——首個 B-roll 19.5s。
3. **29.7–66s：aroll 短打＋stock 連發**（shots 14–22：8 鏡 stock，p50 ~2.4s，辦公室/人物
   情境剪影對應「投資人百態」旁白）。

與專注力協定（jump cut＋keyword 卡、B-roll 僅書封）的差異：本片 hook 就開始用 stock；
與 CEO（傳主臉孔轟炸）差異：本片轟炸的是**書封**。共同點：前 20 秒書封多次曝光。

## 文法發現（單片觀察；證據=shot_id/秒數）

1. **金句卡配方定案（本片 11 張，強力驗證 #1 的單例）**：9/11 為「stock 底＋畫面壓暗＋
   白色大字逐行滑入」（shots 189/193/213/218/261/265/269/297/335），另兩張變體（154 淺灰底
   ＋骰子道具、158 黑白人物照底——引用 Graham 語錄配本人照片）。**11/11 與旁白首次唸到
   毫秒級同步**（154→658.908s cues 384-388、261→1118.576s cues 659-663…全表見 data）。
   p50 8.63s——比一般 B-roll 長一倍，讀字需要駐留。
2. **「第 N」章節系統完整**：滿版橘卡＋斜向色塊 wipe（與 CEO 同配方）——「我的 20 年老韭
   經驗」189.6s、「投資致富的真相」438.1s、第三 745.2s、第四 937.9s、第五 1114.2s、
   第六 1308.8s、第七 1497.8s；**第一、第二用大字 keyword 卡而非滿版卡**（442.1s 橘色大字
   ＋背景壓暗、579.6s 三行字卡）——同一系統內「滿版卡／大字卡」兩檔位混用。
3. **自身對帳單當證據＝本片獨有的高信任文法**：永豐金對帳單 16.4s（shot 55，黃 highlight
   「客戶應收總額」）、Excel 手續費/交易稅 8.6s（shot 56）、複利計算機操作 23.7s（shot 63）、
   2013 騎乘紀錄+Google 地圖（shots 73/76）、大立光成交紀錄含 85.43% 報酬率（shot 78）——
   screen_recording 14 鏡大半是**修修自己的真實數據**，不是通用素材。
4. **人名→橘框人物照 inset 系統化**（photo_inset 17 事件）：Morgan Housel×4、Ronald Read、
   Richard Fuscone、Shiller、Graham、Angus Campbell、Derek Sivers、Buffett——**對比人物用
   雙卡左右並列**（Read vs Fuscone 536.1s、Shiller＋Graham 688.6s）。
5. **去背拼貼動畫講人物故事**：Derek Sivers 段 27.2s＋22.6s（shots 284/294，去背大頭＋
   背景場景切換）、比爾蓋茲 12.9s（shot 360）——人物軼事不用 kol 片段時的替代品。
   另有**自製虛構書封 gag**「閉上嘴巴，等就對了！Shut Up and Wait!」（shot 369，11.4s）。
6. **文獻配方與 CEO 一致**：SPIVA 報告（608–625s，13.6s 逐步 highlight）、CNBC 文章 4 連鏡
   （1039–1080s，黃 highlight 隨旁白逐句移動）——doc_screenshot p50 7.26s。
7. **letterbox 縮框＝插敘/轉折語氣標記**（5 次）：228.8s 搭「白忙一場」字卡、280.5s 轉折句、
   1434.3s 題外話插敘——與 CEO 的 cinematic 強調用法一致，語義更清楚：**跳出主線時縮框**。
8. **stock 連發 3–4 鏡/句是常態**（≥3 連發 26 處），最長 11 鏡混排（1217–1274s Sivers 故事，
   stock 9＋motion_graphic 2）。
9. **kol 僅 29 鏡**：Tim Ferriss podcast（書作者訪談）、MagnatesMedia（Amazon 段）等——
   概念型的 kol 是佐證用，不是主體（與 CEO 的 166 鏡對照）。
10. **片頭 ident 93.4s 才出現**（overlay 第二遍抓到，白底手繪塗鴉）；片尾 CTA 同 CEO：
    「張修修的自由之路」1595.8s＋「shosho.tw/free」1603.8s＋走鐘獎投票頁 screen_recording
    （shots 376/377，紅框 highlight 獎項）。
11. **經典散戶心理曲線圖**（shot 209，3.17s，紅色股價線＋27 段心境獨白＋「投資經典」框）——
    引用網路梗圖當 diagram，附「投資經典」包裝而非學術署名。

## 資料品質備註

- shot 302（1294.9s）修修環球騎行舊 vlog 自拍被歸 other——自家 archive 素材在 vocabulary
  中沒有位置（CEO 片同樣狀況歸 other/kol 不一致），綜合報告需正名一類「self_archive」。
- KOL 來源計數為正則近似（BRIGHT×10 疑為同一頻道連續片段）。

## 對兩個 skill 的含意（provisional，待四支交叉）

- **Director**：概念型節奏錨定 3.3–3.5 事件/分＋overlay ~1.7/分（兩片一致）；金句卡規則可
  升級為「查證原文→首次唸到即上卡→時長 8–10s」；章節卡兩檔位（滿版卡 vs 大字卡）；
  「提到人名」觸發 photo_inset（對比人物並列）；「自身經驗證據」slot（對帳單/紀錄截圖，
  外供素材請求）。
- **DP**：金句卡配方=stock 底＋壓暗＋白色大字逐行滑入（9/11 主配方）；人物照 inset=橘框
  瀏覽器窗＋左右位置交替；拼貼動畫（去背人物＋場景切換）為人物故事的 kol 替代品；
  文獻=截圖＋黃 highlight 隨旁白移動；letterbox=插敘標記。
- **Guardrails**：quote_card 時長（8–10s）遠超一般 cutaway，validate 的時長 heuristic 需
  按 component 分檔。
