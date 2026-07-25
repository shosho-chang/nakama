# 參考片拆解：Ali Abdaal × Jeff Su 四支（剪輯文法研究・對照組）

**Date:** 2026-07-18
**素材:** `E:\video example\Ali and Jeff\`——Jeff Su《AI Agents, Clearly Explained》（10:09）、
《Learn 80% of Claude Cowork in Under 20 Minutes》（18:54）、Ali Abdaal《The Only Investing
Video You'll Ever Need》（29:31）、《6 Side Hustles Students Can Start in 2026》（25:30）。
四支皆 4K、無燒入台詞字幕。
**方法:** 同管線。門檻 0.25；ali-hustles 因粉彩 fade 轉場大量漏切，降 0.12 重切重分類
（264 鏡）。overlay 第二遍：ali-invest 全掃（188 aroll 鏡、991 幀→93 事件）；Jeff 兩支
aroll 僅佔片長 9–13%，改人工抽查 9 幀（省額度，修修 2026-07-18 核准）。
資料見 `data/ref-*/`。

## 八支總表（修修 4 支 vs 參考 4 支）

| 影片 | 長度 | cutaway 事件/分 | overlay 事件/分 | B-roll 時間佔比 | aroll 連續段 p50 | B-roll 主力 |
|---|---|---|---|---|---|---|
| 修修・專注力協定 | 18:25 | 3.26 | ~1.6 | 52.9% | 6.0s | stock |
| 修修・執行長日記 | 24:10 | 3.52 | ~1.5 | 64.1% | 4.8s | kol（傳主素材） |
| 修修・致富心態 | 27:18 | 3.41 | ~1.7 | 58.1% | 6.1s | stock |
| 修修・生命設計師 | 26:11 | 2.41 | ~2.6 | 43.5% | 9.7s | stock＋kol |
| Jeff・AI Agents | 10:09 | 2.27 | ~0（抽查） | **86.8%** | **2.5s** | kol＋圖卡＋錄屏 |
| Jeff・Claude Cowork | 18:54 | 1.27 | ~0（抽查） | **91.3%** | 3.0s | screen_recording（107 鏡） |
| Ali・Investing | 29:31 | 2.54 | **~2.8**（keyword 60 張） | 46.7% | 7.0s | **motion_graphic**（45 鏡、p50 9.6s） |
| Ali・Side Hustles | 25:30 | 2.59 | 未全掃（fade 內嵌多） | 49.3% | 7.1s | stock＋doc_screenshot（30 鏡） |

三個結構典範：
- **修修＝talking-head 為底、素材為插入**（cutaway＋overlay 合計恆定 ~5/分）。
- **Jeff＝素材為底、talking head 為標點**——本人只出現 2–3 秒粘合兩段畫面；文字全部住在
  滿版自製圖卡裡，**不疊在人身上**（抽查 9 幀 0 張字卡疊臉；一例為「人嵌進圖卡畫布」
  shot051 @220.8s「AI WORKFLOW = HUMAN DECISION MAKER」）。
- **Ali＝canvas＋PIP 混合**——長資訊圖卡段落中，本人以圓角直式 PIP 持續在場；
  合計視覺密度同樣 ~5.3/分（2.54＋~2.8），與修修的節拍器殊途同歸。

## Ali 的文法（兩支交叉）

1. **Canvas＋PIP 是系統性版面**：投資片 motion_graphic 45 鏡、p50 9.6s、max 29.5s——米白底
   手繪風動畫（S&P 500 曲線逐步繪入、$1,000→-34%→$660 實算、waffle grid 500 家公司、
   比較卡並列），**主持人 PIP 全程掛在左側**（shots 67/197/204/292…）。長圖表段人臉不消失，
   注意力有錨。Side Hustles 用同構的「粉彩卡片 layout」：大卡放素材＋右側 host PIP。
2. **圖卡講的是論證不是名詞**：動畫全是 worked example（實際數字、實際年份、實際公司
   logo），不是「上升箭頭」式的名詞插圖——anti-literal 的系統級實踐。
3. **keyword 字卡密度全場最高**（投資片 60 張／29.5 分 ≈ 每 30s 一張；疊 aroll 時每 16s
   一張）：白色圓角膠囊 pill、公司 logo app-icon 卡（shot 84 黑白段連發 5 張 logo 卡）。
4. **章節系統＝品牌 grid 總覽卡＋回卡**：hook 內 35.9–57.2s 用 5 鏡把 Part 1–4 卡片輪巡
   預告（roadmap），每章開頭回到同一 grid 聚焦該卡（242.6s／1007.2s／1478.7s）——
   比修修的「切換時才出卡」多了「全片地圖」功能。
5. **金句卡＝kinetic text 逐字打出**（投資片 7 張、米白底大字、可帶署名如 Einstein
   @1167.6s）——不用 stock 底，製作成本低於修修的「stock 底＋壓暗」配方。
6. **插敘標記＝整段黑白去色**（investing shots 84/108/122，去色期間疊 logo 卡）——與
   修修的 letterbox 同功能不同手段。
7. **Sponsor 段有專屬 layout**：host 縮小＋黑色 disclaimer 卡＋「trading212.com/join/ALI」
   膠囊（908–994s）；滿版 sponsor 橫幅疊 aroll（TRADING 212 @900s 前後）。
8. **自身檔案素材當證據**：Side Hustles 大量 Ali 醫院時期 scrubs 實拍、早期拍片房間、
   家教場景（shots 6/7/22/23/24/28…）——與修修的對帳單同一邏輯：**用自己的過去背書**。
9. **手寫俯拍＝法則儀式**：Sharpie 在素描本手寫「THE FIRST LAW OF MONEY…」邊寫邊唸
   （shots 43/44/46…連續段）——低成本高儀式感的金句替代形態。
10. **Hook 對比**：投資片 hook 密度炸裂（60s 內 keyword×4、UI 卡×2、動畫曲線×3、
    「UPDATED」橫幅、章節 grid 巡禮）；Side Hustles hook 反而極簡（26.9s 長 aroll 內嵌
    金句卡＋自家 YouTube 錄屏自嘲）——同一創作者兩種 hook 溫度，題材決定。

## Jeff 的文法（兩支交叉）

1. **教學型 screencast 文法**：Cowork 片 screen_recording 107/165 鏡（91.3% B-roll 時間）；
   錄屏帶「雙三角快轉 icon＋RGB glitch」速度標記（shots 118/129/130）、局部 zoom、
   黃底 highlight 網頁標題（AI Agents shots 31–34 RAG 文章三連發）。
2. **黑底品牌 slide 系統**：點陣暗底＋橘黃 accent 的自製資訊圖卡（「COWORK: CORE
   CAPABILITIES」編號卡 grid、三欄比較卡「Too technical / Just right / Too basic」含迷因）
   ——章節卡與資訊卡同一視覺語言，辨識度極高。
3. **權威借用 hook**：AI Agents 片 0–30s 全是 keynote/訪談蒙太奇（Pichai I/O、DealBook、
   CNBC、Zuckerberg、Jensen Huang——kol 25 鏡 p50 僅 0.54s 快剪），主持人 30s 後才露臉
   ——cold-open on evidence。
4. **aroll 是標點不是底**：連續段 p50 2.5–3.0s、max 15.6s——每講一個 claim 立刻切證據
   畫面；本人臉孔只負責節奏呼吸與信任感。
5. **host PIP 圓角直式卡**疊在錄屏/圖卡角落（出現則 2–4s），或整個人嵌進圖卡畫布
   （「AI WORKFLOW」幀）——與 Ali 的 canvas+PIP 同族。
6. **單支影片主張單一**：10 分鐘片 86 鏡講一個概念階梯（LLM→RAG→Workflow→Agent），
   每階一張定義 slide＋一段錄屏 demo——資訊架構本身就是剪輯結構。

## 修修 vs Ali/Jeff：可學習點（依影響排序）

1. **Canvas＋PIP 版面**（Ali/Jeff 皆用，修修全無）：修修滿版 cutaway 時人臉完全消失，
   長圖表段（如專注力的 etymology 23.2s、diagram 18.8s）尤其可惜——引入「圖卡為底＋
   host PIP」layout 可讓長視覺段落保留人的連結。**這是 schema 需要的新 layout 值。**
2. **Worked-example 動畫 > 名詞式 stock**（Ali）：修修的 stock 主力策略在「畫面感語句」
   上有效，但論證段落（數字、比較、流程）用 stock 是弱替代——Ali 全部用實算動畫。
   對應到產線：hyperframes composition 的高價值標的是 chart/comparison/list 類。
3. **章節 grid 總覽卡**（Ali）：hook 尾預告全片地圖＋每章回卡——修修的橘卡系統可升級
   「總覽版」。
4. **金句卡 kinetic text 形態**（Ali）：不需 stock 底的低成本變體，可作為修修金句卡的
   第二檔位（省 stock 搜尋/下載）。
5. **錄屏速度標記**（Jeff）：快轉 icon＋glitch——修修的 screen_recording（對帳單、網站）
   目前是等速平放。
6. **Sponsor/CTA 專屬 layout**（Ali）：修修片尾 CTA 已固定式，但業配段落無專屬視覺語言。
7. **黑白去色作插敘**（Ali）：與修修 letterbox 並存為兩檔位插敘標記，可按語氣強度選用。

**修修已做對、不必改**：合計視覺事件 ~5/分的節拍器（Ali 同值）；金句首唸即上卡；
書封輪播；自證素材（對帳單/雜誌封面 vs Ali 的醫院實拍——同構）；來源標示紀律
（「影片來源：X」比 Ali/Jeff 都嚴謹——Jeff 的 keynote 片段多數無來源標）。

## 資料品質備註

- ali-hustles 即使 0.12 門檻仍有 fade 內嵌（shot 4 = 26.9s 長鏡內含金句卡；quote_card
  統計 1 張為低估，note 交叉顯示 ≥4 張）——Ali 的 dissolve 轉場密度是本管線 scene-detect
  的已知極限，統計以事件層與 note 修正為準。
- Jeff 兩支 overlay 層為抽查結論（n=9 幀）非全掃——「Jeff 不疊字卡在人身上」的置信度
  中等，但與其圖卡系統的結構邏輯一致。
- jeff-cowork chunk 118–130 分類時安全分類器暫時離線，已人工複核 3 幀相符（qc 記錄）。
