---
name: youtube-doc-highlight
description: >
  YouTube 照稿影片第 7 類畫面「論文／文件 highlighter」的 DP 元件：把畫面規劃表（visual_plan.json）上核准的
  文件列，渲成滿版透明 overlay 素材——品牌冷灰底、白紙文件疊在左上右下的品牌手冊 p24 碎片上、
  黃色螢光筆隨旁白一步一步劃、鏡頭推近到劃的地方、左下橘色「資料來源」——交件到 `<episode>/visuals/`。
  DP 要先寫文件說明檔（文件、頁碼、每一步劃哪裡、從哪句字幕開始），劃的位置用 PDF 文字層自動找。
  Triggers: /youtube-doc-highlight、「做文件 highlight」「論文截圖劃重點」「把這份報告劃給觀眾看」。只做這一類。
  不用在：金句卡（第 8 類）、重點條列（第 18 類）、螢幕錄影（第 12 類）。
---

# youtube-doc-highlight — 論文／文件 highlighter

**版本：v1.0（2026-11-16）**

YouTube 照稿影片的 DP 由一類一類的小 skill 組成（`youtube-concept-title`、`youtube-book-cover`、
`youtube-person`、`youtube-reframe`、`youtube-chapter-card`、`youtube-section-title`、`youtube-quote-card`、
`youtube-side-bullets`）。文件列由 Director 提、修修在 Bridge（`/bridge/video-plan/<episode>`）核准；
本 skill 把**已經核准**的列做成素材：DP 寫說明檔 → 出檢查圖自己看過 → 渲。

## 設計（修修逐輪定版，不要自己改）

| 元素 | 定版 | 修修原話／來源 |
|---|---|---|
| 配方 | 文件滿版，黃色螢光筆 `#ffe14a` 隨旁白一步一步劃（字左→右刷、整欄上→下刷），上一步淡掉；鏡頭推近到這一步劃的地方（最多 1:1），劃整欄時退回看整張 | 修修過去成片「文獻＝截圖＋黃 highlight 隨旁白逐步移動」（`docs/research/editing-grammar/2026-07-18-shoshotw-editing-grammar.md`）；三款（滿版黃色螢光筆／人讓到左邊＋右側文件視窗／錄影壓暗＋聚光框）選滿版黃色螢光筆 |
| 版面與進出場 | 品牌冷灰 `#dddddb` 沿碎片斜邊方向從右下刷進來 → 白紙浮上來 → 左上、右下兩角探進品牌手冊 p24 碎片（小標題那組，原樣不重畫）→ 左下橘色「資料來源」字牌；退場紙往下掉、碎片縮回、灰底斜著刷掉 | 「出場的動畫以及在畫面上的顯示樣式，想要再有一些設計感」→ 三種（角落碎片／疊紙／掃描揭露）選角落碎片 |
| 疊放 | **文件疊在碎片上面**，碎片只從紙的四周露出來 | 「讓文件疊在品牌的 pattern 上面，這樣不怕被遮到了」 |

被否決過：人讓到左邊＋右側文件視窗（寬表格整張時數字太小）、錄影壓暗＋聚光框；疊紙、掃描揭露；碎片蓋在紙上。

元件原始檔：`video/compositions/doc_highlight/compositions/doc_highlight.html`。改設計＝改這一份，並附渲染截圖給修修看過。

## DP 怎麼做

### 1. 拿到文件（要先問修修）

- 規劃表那一列（Director 寫的）：
  - 「給 DP 的指示」＝文件網址或檔名、第幾頁，並**照講的順序列出要劃的字或數字**。
    沒列要劃哪些 → 把這列標「重排」請 Director 補，**DP 不自己決定劃哪些**。
  - 「出處」＝畫面左下要印的名稱，給觀眾看的（例：`行政院主計總處〈111年國富統計及110年家庭財富分配統計結果〉表6，110年底（2021年）`）。
    不能是網址——網址放在「給 DP 的指示」。
- **下載：整集開工前一次問。** 把這集所有要下載的檔案（各分類一起：每份的檔名、來源、大小）列成一張清單給修修核准，
  不要一份一份打斷他；清單外臨時要下載的再另外問。優先用原始出處（政府、期刊、機構官網），
  不要用別人重排過的版本。
- 文件放在 `<episode>/visual_sources/`。支援：
  - **PDF**（有文字層最好：劃的位置自動找）；
  - **圖片**（png／jpg／webp：沒有文字層，劃的位置要用 `rect` 手動標）；
  - **網頁**：先用瀏覽器「列印 → 另存 PDF」再當 PDF 用。

### 2. 寫說明檔 `<episode>/visual_sources/<row_id>.json`

```json
{
  "document": "111年新聞稿.pdf",
  "page": 15,
  "crop": null,
  "steps": [
    {"cue": 131, "marks": [{"column": {"text": "5,133", "row": "淨值(A+B-C)"}, "top": {"text": "淨值五等分位組"}}]},
    {"cue": 133, "marks": [{"text": "金融性資產(B)"}, {"text": "222", "row": "金融性資產(B)"}]}
  ]
}
```

- `steps`：照講的順序。每一步在 `cue`（這一列蓋住的那幾句字幕之一）開頭劃；字幕要一步比一步後面。
  第一步最早在 0.8 秒（紙浮上來之後）；最後一步至少要在畫面上停 1 秒。
- `marks`：這一步劃的地方（可以好幾個；跟上一步一樣的會留著不重劃）。
  - `{"text": "金融性資產(B)"}`：劃這段字（第一處；`"nth": 2` 選第二處，由上而下、由左而右）。
  - `{"text": "222", "row": "金融性資產(B)"}`：同一列（跟那段字同一行、在它右邊）的「222」——表格裡同一個數字常出現好幾次，**數字一律帶 `row`**。
  - `{"column": {...同上...}, "top": {"text": "淨值五等分位組"}}`：劃整欄：從 `top` 那段字下緣到那一格。欄寬用同一行相鄰兩格的距離算。
  - `{"rect": [左, 上, 右, 下]}`：手動標（PDF 用點、左上原點；圖片用 px）。找不到字、掃描檔、圖片都用這個。
- `crop`：要裁的範圍 `[左, 上, 右, 下]`；`null`＝自動裁到所有劃的地方再留一點邊。

### 3. 出檢查圖、自己看過

```bash
python -m agents.brook.youtube_video.visuals.doc_highlight "G:/Footages/<episode>" --row <id> --check
```

產生 `visual_sources/<row_id>.check.png`：紅框＝劃字、藍框＝劃整欄，框角的數字＝第幾步。
**逐框對過**（框對位置、沒框到隔壁格、整欄有包到欄標題）才進下一步；不對就改說明檔再出一次。

### 4. 渲

```bash
python -m agents.brook.youtube_video.visuals.doc_highlight "G:/Footages/<episode>"
```

- 只渲 `category == doc_highlight` 且 `status == approved` 的列；`--row`、`--force` 同其他元件。
- 一張約 1.5 分鐘（元件固定渲 60 秒再截）。文件、說明檔、出處、秒數與元件沒變就沿用。

## 時間

整張停留＝這一列蓋住的字幕總長（4–60 秒）。修修過去成片文件畫面 p50 7–10 秒；像主計總處表 6 這種一路講數字的，
一整段 40 秒也合理。要更長或更短，是改規劃表蓋住的字幕範圍。

## 交件（所有分類共用的格式，`agents/brook/youtube_video/visuals/delivery.py`）

- `<episode>/visuals/<row_id>.mov`：1920×1080、29.97 fps、ProRes 4444 帶 alpha，長度＝停留秒數；第一格、最後一格全透明。
- `<episode>/visuals/<row_id>.json`：收據（文件圖太大不存，只存長度；內容已算進 `inputs_sha`）。

## 驗收

1. 檢查圖每一框都對。
2. 指令最後一行是 `N/N 張完成`；有 ✗ 的列回報（通常是找不到字、字幕不在範圍裡、出處空的或是網址）。
3. 疊回錄影看：每一步劃的時間對得上講到的數字、推近後字清楚、退場後回到錄影。
4. `ffprobe` 確認 `prores 4444`、`yuva444p`、`30000/1001`、長度對。

## 還沒定

- 網頁直接截圖＋用 DOM 找字（目前先列印成 PDF）。
- 同一張文件要不要跨頁（目前一列一頁；跨頁就拆成兩列）。
