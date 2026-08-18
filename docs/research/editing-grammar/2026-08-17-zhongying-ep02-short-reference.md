# 鐘穎 Ep02〈波旬〉短片剪輯文法拆解

> 這是第一輪宏觀筆記；它把動態句列誤扁平化為獨立卡片。0.1 秒級重審與
> 修正後的 title motion event table 見
> [`2026-08-18-zhongying-ep02-title-motion-audit.md`](2026-08-18-zhongying-ep02-title-motion-audit.md)。

Stage 5（製作）參考片校準。來源：`E:\data\鐘穎_Ep02_波旬Final.mp4`。

## 實測基線

- 片長：67.566667 秒
- 畫面：1080×1920、30fps
- 音訊：約 -15 LUFS；片尾可降至約 -35.8 dB，沒有用連續高存在感 BGM 撐滿全片
- 一般字幕：小型黑字／白底，和強調字卡分層，不把逐字字幕本身做成大型動畫
- 大型視覺段落：約 32.0–35.4 秒、39.3–43.1 秒兩段 B-roll；62.5–67.6 秒品牌片尾

## 畫面語法

### 1. 三層文字層級

1. Tier 3：逐字字幕，只負責可讀性。
2. Tier 2：橘底白字 punch card，負責 hook、機制、證據與轉折。
3. Tier 1：明顯更大的橘底主張，負責單獨成立的 insight/payoff。

兩行字卡不是同字級排版；會把關鍵行放大、輔助行縮小，形成先後視線。尺寸變化不是
裝飾，而是在同一張卡內告訴觀眾哪個詞先讀。

### 2. 進出場不是單一模板

- `swipe`：橫向滑入，適合快速陳述。
- `wipe`：遮罩展開，適合語意轉折。
- `word`：逐詞／逐字 reveal，適合推理鏈與層層揭露。
- `slam`：縮放、輕旋轉、back-out 落定，保留給 hero/payoff。

典型進場約 0.2–0.6 秒，含 motion blur；兩行常 stagger，不同時整齊出現。退場短而快，
不讓動畫拖慢下一句。連續三張不能同一種進場，相鄰卡至少改動畫或行級大小。

### 3. 節奏來源是組合，不是狂塞字卡

- talking head 換鏡與 punch zoom：維持微節奏。
- 動態字卡：標記論證節點。
- 貼紙／插畫：在 6 秒與 13–21 秒附近提供較輕的敘事變化。
- whip/motion blur：約 4.47 秒製造一次明顯撞點。
- B-roll：每約 25–30 秒有一個較大的 visual reset，但只在語意能精準對應時使用。
- 品牌片尾：最後約 5 秒獨立收束。

因此「每 8 秒必塞一張卡」不是正確模仿；接近參考片的關鍵是小變化與大 reset 交替。

## 映射到鄭國威三支 baseline

- KS1：壽司師傅手部工序對齊「老師傅／品質要求」；6 張動態卡、鏡位切換與 punch ramp。
- KS2：彩色沉積岩空拍對齊「地殼變化／海洋累積／氣候」；10 張動態卡，保留慢科學主題的
  較長呼吸，不照搬 60 秒片的速度。
- KS3：父女 stock 在兩個 Envato 候選都無法產生 download event／實體檔後 fail closed；
  不拿泛用家庭畫面硬湊，改用「爸爸做的事／對社會好」tier 1 hero、鏡位與尺寸變化完成重點。

## 已落進系統的約束

- `punch_card.html` 支援 `swipe/slam/wipe/word`、整卡與兩行獨立 scale。
- `run_short_titles.py` 驗證動畫白名單、尺寸安全範圍、連續三張同動畫、四張以上沒有尺寸變化。
- Resolve 落軌後驗證 Media Pool item 與實體檔案路徑，防止看似成功但實際 Media Offline。
- `highlight-cut` skill 記錄三層字卡、動畫分工、25–30 秒 visual reset 與 B-roll fail-closed 原則。
- Envato 原始檔、working copy、SHA-256、失敗候選與 fallback 寫入 episode asset manifest。

## 下一輪應由人工 feedback 調的參數

baseline 已把可量化的文法落地；修修 review 時最有價值的 feedback 是：

1. 哪張卡的語意不值得被放大。
2. 哪個 `slam` 太吵或哪個 quiet period 太長。
3. 哪個 B-roll 雖然字面正確，情緒或文化語境仍不對。
4. 哪一行應該成為視覺主詞（`line1_scale` / `line2_scale`）。

這四種 feedback 可直接改 plan／skill，不需要重新發明整條 pipeline。
