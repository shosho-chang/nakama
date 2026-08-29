# 鐘穎 Ep02〈波旬〉Title Motion 逐幀重審

Stage 5（製作）參考片校準。來源：`E:\data\鐘穎_Ep02_波旬Final.mp4`。

## 先更正上一輪結論

上一輪用「有幾張卡、每張是哪種進場」當分析單位，這個單位錯了。參考片的核心不是
很多獨立 title clip，而是 **title sequence**：一個 sequence 內的字會繼續長出、加行、
換詞、改尺寸、換 layout，最後才交棒給下一個畫面。

依清楚的進出場／composition change 分組，全片約有 **25 組 title sequence**；若每次
文字內容、行數或尺寸層級改變都算一個 state，約有 **34 個 semantic states**。數字會因
「混合字幕是否算 title」差 1–2 個，但無論採哪種口徑，都不是上一輪寫的「約 22 張
彼此獨立的卡」。

更重要的差別是：

- 簡單 title 多數只完整停留約 0.3–0.9 秒。
- 2–4 秒的長 sequence 並非靜止，而是每 0.1–0.6 秒增加字、換行或改尺寸。
- 鄭國威 baseline 的卡大多進場後靜止約 2–3 秒，因此即使換了四種 entry，仍有模板感。

## 檢視方法與精度

- 片長 67.566667 秒，1080×1920，30fps。
- 全片以 1fps 看構圖總覽、5fps（每 0.2 秒）建立完整 frame strip。
- 六個變化密集段再以 10fps（每 0.1 秒）檢查：0.8–5.0、27.2–32.2、
  36.8–40.0、42.8–45.8、47.8–52.8、58.6–62.6 秒。
- 下表時間精度為 ±0.1 秒；不是根據記憶或只看 1fps contact sheet。

## Title event timeline

| 時間 | 畫面文字／狀態 | 逐幀變化 | 剪輯功能 |
|---|---|---|---|
| 1.2–1.9 | 佛經故事 | 橘塊與字一起快速展開，短 hold 後硬收 | 第一個具體名詞撞點 |
| 2.5–3.0 | 佛陀 | 插在白底逐字字幕旁，不另開完整大卡 | 把普通字幕中的關鍵詞升級 |
| 3.0–4.5 | 完美示範 → 坐著 | 先標準尺寸；下一個詞突然升成 hero，接全畫面水平 motion blur | 同一句內的尺寸 promotion，不是換一張同規格卡 |
| 5.2–5.7 | 惡魔 | 單詞短卡，約半秒 | 角色命名，快進快出 |
| 6.0–6.5 | 波旬＋插畫 | title 與右側角色貼紙組成一個 unit | title 會和素材互相定位 |
| 7.6–8.2 | 佛陀＋插畫 | 換另一角色插畫，位置與前卡不同 | 角色對照 |
| 8.9–9.3 | 擋在門外 | 寬橘塊短促出現 | 動作 payoff |
| 10.3–13.1 | 佛陀 →「就跟阿難說／讓波旬進來」 | 橘色關鍵詞先出；之後變成橘色 eyebrow＋白底正文的 hybrid caption | title 與逐字字幕並非兩套互斥系統 |
| 15.6–17.7 | 我看到你了 →「第一句話叫／I see you.」 | hero 先撞入；縮回後改成橘 eyebrow＋白底英文 | 大主張降階成說明，不需整組退場重來 |
| 23.9–24.5 | 魔 | 單字小卡貼在普通字幕旁 | 極短語意標記 |
| 27.3–28.1 | 橘色 patterned rails | 上下橘色圖樣框從 motion blur 建立，talking head 被放進新的 stage | layout mode change，不只是 overlay |
| 28.2–29.6 | 獲得什麼 | 左側先飛入橘色 smear，卡帶輕微旋轉後落定 | 問題／命題 |
| 29.7–30.4 | 放輕鬆 | 巨大模糊文字 overshoot 後縮回標準尺寸 | 內容 replacement＋scale hit |
| 30.5–32.0 | 坐著 | 同一 anchor 換成更大的 hero 尺寸，rails 繼續存在 | 尺寸再 promotion，完成三拍句列 |
| 32.1–33.9 | B-roll＋武裝自己 | title 在 B-roll 上，不重開 talking-head card | 語意具象化 |
| 34.0–34.8 | 黑暗地方 | 同一 B-roll 內換詞 | B-roll 內的連續 sentence state |
| 34.9–36.2 | 不用 → 你坐著 | 切回主持人；先超大單詞，再換較小完整短句 | 反差與幽默節奏 |
| 37.2–39.5 | 焦慮 → 加「不安」→ 加「憤怒」→ 將頂行換成「悲傷」 | 每一行前都有左→右橘色 smear；逐行堆疊，最後只替換其中一行 | **stack/add/replace**，現有 engine 完全沒有 |
| 39.6–43.1 | B-roll＋橘 eyebrow／白底正文 | 橘色小標先說「你就說／問他有」，白底字幕承載完整句子 | hybrid caption＋visual reset |
| 43.2–45.8 | 謝謝他／今天來保護我 | 第一行逐字長出；保留第一行，第二行再以約每字 0.1 秒長出，橘底寬度跟字一起擴張 | progressive multi-line build |
| 47.9–50.4 | 他是為了／某種保護目的的／出現的 | 依序增加到三行；每行內也是逐字 build | 三階段論證句列，不是 3 張卡 |
| 51.2–52.0 | 防衛機轉 | 先以接近全寬的模糊字 overshoot，再縮回標準尺寸 | 名詞首次出現的 hero hit |
| 52.2–54.6 | 和他坐著 → 如佛陀／對待波旬那樣 | 同 anchor 換詞並增加第二行 | 機制→比喻連續推進 |
| 55.1–56.0 | 他會離開 | 切到主持人鏡位仍保留 title 語法 | 結果／承諾 |
| 58.0–62.5 | patterned rails；這些情緒 → 無論正面 → 還是負面 → 都還要／來得大 | 先 blur/scale hit，之後在同一 stage 逐句 replacement；最後變成雙行 | closing thesis sequence，約 4.5 秒內有四次內容狀態 |
| 62.5–67.6 | 品牌片尾 | talking head 疊化進全版品牌卡 | 獨立 outro，不與 title engine 混用 |

## 真正使用的 motion primitives

### 1. Keyword promotion

普通白底字幕裡的一個詞可以升級成橘底關鍵詞；不需要把整句另做成大卡。這讓 title
出現頻率高，但不會每次都遮住整個畫面。

### 2. Semantic replacement

同一個空間 anchor 直接從「獲得什麼」換成「放輕鬆」再換成「坐著」。觀眾感受到的是
一句話持續推進，不是三張彼此無關的模板卡。

### 3. Stack / add / replace

「焦慮／不安／憤怒」不是同時顯示：每個詞依語音重音逐行加入，之後只把頂行替換成
「悲傷」。這是資料結構層級的差異，不是多做一個 CSS entry 就能補上。

### 4. Progressive glyph build

「今天來保護我」「某種保護目的的」約每 0.1 秒增加一字，而且橘色背景寬度隨內容
同步變長。現有 `word` 動畫先展開整條背景，再讓 glyph 在固定背景內上浮，視覺效果不同。

### 5. Scale promotion and blur overshoot

部分字會先以超過最終尺寸很多的模糊版本撞進來，再縮回；另一些詞會在 sequence 中
從一般尺寸升成 hero。尺寸變化發生在播放過程中，不只是 render 前設定不同 scale。

### 6. Whip-bar precursor

「獲得什麼」和情緒堆疊的每行前面，先有一個左→右的橘色 motion-blur streak。它既是
進場 anticipation，也是語音重音的視覺拍點。

### 7. Persistent stage / patterned rails

27.3–32.0 與 58.0–62.5 秒不是透明 title 疊在原畫面，而是進入一個有上下橘色圖樣的
temporary layout mode。多個文字 state 共用同一 stage，讓它們讀成一個章節。

### 8. Hybrid caption

橘色 eyebrow 負責語氣或說話動作，白底正文負責完整內容。這種中間層比純逐字字幕
更強、又比 hero title 安靜，是目前三層模型缺掉的第四層。

### 9. Exit diversity

參考片很多 title 不是統一縮小淡出，而是直接 replacement、跟著鏡位 hard cut、被 whip
transition 帶走、或留在 stage 中等下一行。現有 engine 對每張卡固定做 0.22 秒縮小淡出，
會產生規律且可預測的模板感。

### 10. Handcrafted variation

橘塊不是永遠水平置中：會有約數度旋轉、左右錯位、不同寬度和不同基線。變化受語意
控制——hero 放大、列表垂直堆疊、說明句用 hybrid，而不是隨機 jitter。

## 和目前 punch-card engine 的差距

目前 `punch_card.html` 的能力是：最多兩行、內容在 render 前固定、整張卡選一種 entry、
進場後 hold、最後統一 shrink/fade。即使已有 `swipe/slam/wipe/word` 與 line scale，仍缺：

1. 一個 clip 內的多 state timeline。
2. 第三行與任意行的 add/remove/replace。
3. 播放中的 scale promotion。
4. 背景寬度隨 glyph 增長。
5. whip-bar anticipation。
6. persistent patterned stage。
7. orange eyebrow＋white body hybrid caption。
8. title 與 sticker／B-roll 的共同 layout。
9. replacement、hard cut、transition handoff 等 exit strategy。

因此問題不是「再多做幾種 entry animation」；真正要改的是 title 的資料模型。

## 建議的正確改版順序

### P0：Sequence schema

將 `titles.json` 從一筆一個 static card，改成一筆一個 sequence：

```yaml
t0: 43.2
layout: free
steps:
  - {at: 0.0, op: type_on, id: line1, text: 謝謝他, cadence: 0.10}
  - {at: 0.8, op: add_line, id: line2, text: 今天來保護我, cadence: 0.10}
  - {at: 2.5, op: exit, style: hard_cut}
```

必要 operations：`enter`、`type_on`、`add_line`、`replace_text`、`remove_line`、
`promote_scale`、`whip_hit`、`exit`。

### P1：兩種 composition

- `kinetic_sequence`：透明 overlay，支援 1–3 行與多 state。
- `framed_thesis`：含 patterned rails 的 temporary layout stage。

不要把兩者再塞進一個有大量條件分支的 `punch_card.html`。

### P2：第四層 hybrid caption

建立 orange eyebrow＋white body composition，補上普通字幕與大 title 之間的強度層級。

### P3：語音同步與 planner

`type_on` 和 `add_line` 要對齊 word timestamp／語音重音；planner 先決定 sequence 的
語意操作，再由 renderer 決定 tween。不能讓 LLM 直接憑感覺填每個 frame 的動畫數值。

## 對鄭國威 baseline 的結論

三支 baseline 的字幕、鏡位與個別 card 風格可保留，但 title 層需要重做；目前不是再微調
`line1_scale` 就能接近參考片。正確作法是先完成 P0＋P1，用其中一支做 A/B，再把另外兩支
重建。否則繼續在 static card engine 上加動畫，只會讓模板變多，不會變成 reference 的
動態句列語法。
