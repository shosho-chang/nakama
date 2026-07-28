# 封面品質升級計畫 — 對標 Modern Wisdom（Chris Williamson）

**日期**：2026-07-28 · **狀態**：待修修裁決 · **Anchor**：CONTENT-PIPELINE Stage 5（製作/packaging）
**觸發**：修修對 S5 首批封面的回饋（「慘不忍睹」）+ 對標檔案 153 張縮圖全量分析
**鐵律**：真人畫面不交 AI 合成 — AI 限 graphic／標題 render（`memory/claude/feedback_no_ai_synthesized_humans.md`，PR #1090）

---

## 1. 對標分析摘要（153 張全量普查）

### 類別分佈

| 類別 | 佔比 | 說明 |
|---|---|---|
| A 完整版訪談封面 | 25% | Chris＋來賓＋主題大字 |
| B1 主題短片（來賓 only） | 31% | 來賓臉＋示意圖 |
| B2 主題短片（含 Chris） | 39% | 多為兩人夾一張 prop 卡 |
| C 其他 | 6% | 純字卡／純攝影 |

### 五個可複製 recipe（頻道識別的來源）

- **R1 完整版**：Chris **38/38 張都貼左緣**、來賓貼右緣，頭高 55–65%；中欄 2–4 行全大寫粗字，白＋**恰好一個 teal 字**＋句點；白底圓角名牌；M|W logo。背景 = 去飽和 teal／淺灰／深藍 flat。
- **R2 紅框反應卡（主力，全頻道 52%）**：深紅圓角框 prop 卡佔寬 45–55%（迷因／新聞截圖／經典畫／檔案照），反應臉頭高 50–70%、**視線一律朝卡**，rim light、vignette 融進近黑底。**零文字**。
- **R3 主題卡**：左 55% 字塊＋右來賓，棚拍背景壓暗保留。
- **R4 四人橫幅**／**R5 里程碑卡**：少量。

### 工藝紀律（我們最缺的）

1. **一張圖一個 idea**：57% 完全無字（prop 卡就是敘事）；有字的 median 3 個詞。
2. **色彩角色鎖定**：每張 3–4 色；teal 只當字的 accent、紅只當框 — 全頻道不越界。
3. **臉**：頭高 45–70%、soft key＋rim light 把人從深底切出來、去飽和 matte 調色（暖膚冷影）、表情強度高（驚訝 40%／說話中 30%／大笑 25%）。
4. **裝飾近零**：153 張裡 0 箭頭、0 emoji、0 光暈。
5. **邊緣錨定**：人永遠貼frame 邊，沒有漂浮頭。

## 2. 差距診斷（我們的 3 張 vs 上表）

| 面向 | Modern Wisdom | 我們（punch-L5 三張） |
|---|---|---|
| 素材 | 棚拍級人像（rim light、眼神光、統一 grade） | 訪談影格 cutout（平光軟焦）＋手機感自拍庫 |
| 背景 | 60% 設計 flat／36% 棚拍 vignette | 純黑平面 → 浮貼感 |
| 臉占比 | 頭高 45–70% | 約 25–35%（半身入鏡浪費面積） |
| 元素數 | 臉＋1 載體 | 臉×2＋麥×2＋麥臂＋大字 = 6 件打架 |
| 文字 | ≤5 詞、單 accent、名牌層級 | 置中白字像字幕、無層級 |
| 概念圖層 | prop 卡 52% 出現率（視覺隱喻） | **整層不存在** |
| 簽名感 | 紅框卡＋teal 字＋左緣 Chris | 無 |

**根因排序**：① 素材品質（天花板）② 封面設計系統不存在 ③ 視覺隱喻（概念圖）這一層整個缺 ④ composition 引擎只是「把素材擺上去」。

## 3. 三配方繁中品牌翻譯（設計凍結草案）

沿用 `--sho-*` 品牌語言：accent = PANTONE 165 橘 `#e98965`、深暖灰底、LINE Seed TW。

- **N1 完整版**（≙R1）：修修**永遠左緣**、來賓右緣（頭高 ≥55%）；中欄繁中大字 2–6 字（LINE Seed EB，白＋**一詞橘**＋句點）；白底圓角名牌「謝伯讓｜腦科學家」；bolt 小 logo 角落（99/1 規則）。背景：深暖灰 flat 或壓暗棚拍。
- **N2 反應卡**（≙R2，建議主力）：**橘色圓角框 prop 卡**（框內偶帶 zigzag 缺口作品牌記號）佔寬 45–55%；概念圖進卡；反應臉頭高 60–70%、視線朝卡、vignette 融底。**預設零文字**。
- **N3 主題卡**（≙R3）：左字塊（2–3 行、級距對比：關鍵詞特大）＋右來賓，壓暗棚拍背景。

繁中排字特規：中文無 condensed caps 的密度感 → 用 **EB 字重＋字距收緊＋級距對比**（主詞 1.6–2×）補；直排留作 N1 選項。teal→橘、句點保留。

**版權護欄**：R2 的迷因／電影劇照文化在歐美靠 reaction 慣例撐；我們的 prop 卡素材優先序 = 公版藝術品（如浮世繪）→ 自產／AI graphic → 授權 stock → 研究圖表重繪。**不用**未授權劇照／他人照片。

## 4. 分階段執行

### P0 — 素材層（修修 2026-07-28 二裁：兩人 cutout 都從訪談 raw file 抽）
1. **主供給線 = 訪談影片抽格**（不是棚拍）：來賓與修修的 cutout 都由 S3 funnel 從
   各自機位原檔抽 — 清晰度排序（Laplacian variance）本來就在挑 motion blur 最少的
   格，vision subagent 挑表情。修修側新增：修修機位（CAM1）走同一條抽格線
   （反應臉可取自來賓說話窗，speaker-dominance 驗證對 host 放行）。
2. **抽格偏好升級**：臉占比大、正面 ±45°、無麥克風遮擋（麥臂偵測進淘汰規則）；
   從機位原檔全解析度抽。
3. 棚拍降為 optional（未來想要更強的 rim light 質感再排）；預建 shosho 庫保留給
   非訪談的 `youtube_host` 配方。

### P1 — 設計系統＋composition v2（工程，零 AI、零新費用）
1. `docs/thumbnail-design-system.md`：凍結 §3 三配方 spec（尺寸、色彩角色鎖定、type、名牌、prop 卡框）。
2. hyperframes compositions 重寫：`thumbnail_reaction`（N2）／`thumbnail_full`（N1）／`thumbnail_topic`（N3）— vignette 混合層、名牌元件、highlight 分色字、prop 卡框元件。淘汰現行置中字幕式模板。
3. **去背升級**：u2net → **BiRefNet**（`rembg` 同套件即有 `birefnet-general`）— 解決毛邊＋桌面殘留。
4. **調色統一 pass**：cutout 套 LUT（壓飽和、暖膚冷影）貼合背景 — 傳統曲線調色，非 AI relight（原則紅線）。
5. thumbnail-brainstorm SKILL v2：diversity 軸改「配方 × 概念圖 × 大字」；每 package 標配方。

### P2 — 概念圖層（修修 2026-07-28 二裁：不用 AI 生圖，graphics 走 Envato）
1. skill 新 Step「視覺隱喻 brainstorm」：從標題 archetype＋段落內容發想 prop 卡
   內容，供給線（依序）：(a) **Envato stock**（MCP `search_items` 已接，brook-dp
   同一條）(b) 公版藝術品／自有素材 (c) 研究圖表重繪（hyperframes 參數 render）。
2. **AI 生圖暫不用**（修修：「傾向不要用 Nano Banana 來產生圖」）— nano banana /
   gpt-image 選項作廢；金額歸零（Envato 走既有 Elements 訂閱）。
3. **繁中大字永遠 deterministic render**（hyperframes 真字型 — 就是「用 code 寫的
   Canva 模板」：字型排版凍結在 composition，agent 只填參數，成果 100% 可控）。
4. 明確禁區（memory 已凍結）：AI 人臉／img2img 重繪真人／換臉／AI relight；升頻
   灰區預設不用。

### P3 — 品質迴圈
1. 終檢 rubric（vision subagent 對照 Chris 參考集打分）：臉占比 ≥50%？元素 ≤3？色彩 ≤4 家族且角色不越界？100px 可讀？視線向量朝卡？— 不過門檻**重做，不交付**。
2. gate 顯示配方標籤；reject_note 持續累積 taste。
3. 參考集固化：`cw_thumbs/` 153 張＋普查報告存 `Attachments/cutouts/reference/modern-wisdom/`（僅內部 rubric 用）。

### 里程碑

- **M1**（P1 完成，不等棚拍）：用現有素材重出謝伯讓集 3 張 — 與現版直接對比，驗證設計系統的增量。
- **M2**（P0 拍完）：完整品質版。
- **M3**：S10 新集全流程含 P2/P3。

## 5. 成本

- 全案 **$0 新增金額**（2026-07-28 二裁後）：抽格/render 零費用；graphics 走既有
  Envato Elements 訂閱；AI 生圖作廢。

## 6. 裁決紀錄

1. ~~整案方向~~ → 修修 2026-07-28 裁：照改版方向做（cutout 全從 raw file 抽、
   deterministic 排版、graphics 走 Envato）。
2. ~~P2 image API~~ → 作廢（不用 AI 生圖）。
3. ~~P0 棚拍排程~~ → 降 optional；主供給線 = 訪談抽格。
