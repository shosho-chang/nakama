# Avatar 動畫（第 15 類）生成影片模型調查

- 日期：2026-11-17
- 背景：修修選 C 混合——2D 解釋動畫用程式（HyperFrames），3D 情境＋表情的 Avatar 用生成影片模型。造型＝線條修修（品牌 logo 臉＋線條身體，`video/compositions/explainer/assets/kit/figure.js`）。
- 目的：找出能「拿線條修修當參考圖、在不同 3D 場景保持同一個人」的模型，挑出第一個測試對象。
- **用途定位**（修修 2026-11-17）：「我沒有要教學，也沒有要求精確，我只是要用有品牌 identity 的動畫來輔助我影片的說明」。
  所以重點是**角色認得出、風格一致**；動作只要看得懂在做什麼，不追求正確姿勢，不需要真人動作來源（不用 Motion Control）。
- **為什麼還是要生成影片模型**（修修同日）：「我需要人物是在 3D 的場景下做動作，當他移動或是做動作的時候，整個場景也會改變，
  會有運鏡、有不同的視角跟角度，就跟一般的動畫一樣」。角色本身程式畫得出來，但真的 3D 運鏡與視角變化需要生成模型
  （程式做得靠 Three.js 一個個建 3D 場景，工程量不划算）。靜態 3D 背景＋程式角色的做法被否決。
- 方法：網路搜尋，以官方文件為準；只有第三方來源的標「未查證」。**沒有實測任何一家。**

## 已下架（不用考慮）

| 模型 | 狀態 | 來源 |
|---|---|---|
| OpenAI Sora 2 / Sora 2 Pro | App 與網頁 2026-04-26 結束；API 2026-09-24 下架，官方無替代 | [OpenAI Help](https://help.openai.com/en/articles/20001152-what-to-know-about-the-sora-discontinuation)、[Deprecations](https://developers.openai.com/api/docs/deprecations) |
| Google Veo 3.1（Gemini API preview） | 2026-10-22 下架，官方建議改用 Gemini Omni Flash | [AI Studio Veo docs](https://aistudio.google.com/docs/veo) |

`.env` 裡唯一的生成類金鑰是 OpenAI，所以它對影片沒有用了。

## 候選

| 模型 | 角色一致的做法 | 跟我們需求相關的 | 查證程度 |
|---|---|---|---|
| **Kling 3.0** | 「元素」：2–4 張多角度參考圖（或 3–8 秒影片）建一個角色，生成時勾「綁定主體」；一次最多 3 個元素 | **動作控制（Motion Control）**：用一段真人動作影片驅動角色——修修自己做一次深蹲，角色照做。但官方說綁定時只吃臉部資訊 | 官方文件：[元素庫](https://kling.ai/quickstart/klingai-element-library-3-user-guide)、[3.0 指南](https://kling.ai/quickstart/klingai-video-3-model-user-guide)、[Element API](https://kling.ai/document-api/apiReference/model/element)、[Motion Control](https://kling.ai/quickstart/motion-control-user-guide) |
| **Runway（Gen-4 References）** | 1–3 張參考圖，可存名字用 @ 叫出來；先生成靜態圖，再丟影片模型 | **Act-Two**：用一段表演影片驅動角色的表情和手勢——修修對鏡頭演表情，角色照演 | 官方 help center：[References](https://help.runwayml.com/hc/en-us/articles/40042718905875-Creating-with-Gen-4-Image-References)、[Act-Two](https://help.runwayml.com/hc/en-us/articles/42311337895827-Performance-Capture-with-Act-Two)（References 文件約 13 個月前，已有 Gen-4.5） |
| **Gemini Omni Flash**（Google，Veo 後繼） | 參考圖（數量 5–10 張，說法不一）＋參考影片 | 單段 3–10 秒（未查證） | 只有第三方頁面，如 [WaveSpeed](https://wavespeed.ai/models/google/gemini-omni-1.1-flash/reference-to-video)；**未查證** |
| **Seedance 2.0**（字節，BytePlus ModelArk） | 多張角色參考圖 | 第三方說不收寫實人臉參考圖（我們是插畫，可能不受影響）；BytePlus 不對美國開放 | [BytePlus](https://www.byteplus.com/en/blog/dreamina-seedance2-0)；細節**未查證** |

## 對我們特別要注意的

1. **logo 臉只有一個角度。** Kling 要 2–4 張不同角度的參考圖，業界也普遍說多角度比單張穩。logo 臉只有一張（略側的正面），側面、背面不存在，模型只能自己補——這正是最可能走樣的地方。
2. **沒有任何來源證實「平面線條角色放進 3D 場景時，線條粗細能維持」。** 一致性的宣稱大多是寫實人物或 3D 卡通角色。這要實測。
3. **表情：** 生成模型可以做，但 logo 臉的手繪線條會被重畫。Runway Act-Two 用修修本人的表演驅動，可能最好控制。
4. **動作：** Kling Motion Control 能用真人動作影片驅動角色，但我們不追求動作正確（見上方用途定位），不用。
5. 文章裡「Kling、Seedance 一致性最好」「Seedance 95%」這類數字都來自業者部落格，不是獨立評測。

## 建議的第一個測試

- 對象：**Kling 3.0**——角色一致（元素綁定）的官方文件最完整。
- 參考圖：從 `figure.js` 渲染線條修修（身體可轉，頭只有一個角度）。
- 三個鏡頭：健身房深蹲、廚房煮東西、書桌看書，全部只用文字提示詞＋角色元素。
- 驗收：logo 臉認得出、線條粗細和白 T＋橘牌三鏡頭一致、表情線條是手繪感。
- 需要修修：開 Kling 帳號（帳號、付款由修修處理）。價格這次沒查。

## 測試包（修修 2026-11-17：「可以，用 Kling」）

### 參考圖（建 Kling「元素」用，4 張，1024×1280 白底）

由 `video/compositions/explainer/compositions/figure_ref.html` 產生（`?pose=stand&scale=2.0`、`?pose=armsOut&scale=1.75`、
`?pose=wave&scale=2.0`、`?face=1`），headless Chrome 截圖。
1. 站姿正面　2. 雙手平舉　3. 揮手　4. logo 臉特寫（讓模型看清楚臉的線條）

元素名稱：`shosho_line`。

### 三個鏡頭（每個 5 秒、16:9）

每段提示詞都帶同一段「風格鎖定」：

> The character @shosho_line is a flat 2D hand-drawn line illustration: thick black outlines, white face,
> spiky black hair strokes, white T-shirt with a small orange rectangle on the chest, thin black stick arms
> and legs. Keep him exactly as drawn — flat 2D lines, no shading, no 3D rendering of the character —
> like a paper cut-out standing inside a real 3D environment.

| # | 場景 | 提示詞（接在風格鎖定後面） | 方式 | 表情 |
|---|---|---|---|---|
| 1 | 健身房深蹲 | In a bright modern gym with soft daylight, he does one slow bodyweight squat, facing the camera. Static camera, medium-wide shot. | 文字＋元素 | 吃力 |
| 2 | 廚房 | In a warm home kitchen, he stirs a pot on the stove and smiles happily at the camera. Slow push-in. | 文字＋元素 | 開心 |
| 3 | 書桌 | At a wooden desk by a window in the evening, he reads a book, then frowns slightly in concentration. Static camera, medium shot. | 文字＋元素 | 專注 |

### 第一輪結果（2026-11-17，Kling 4.0 Flash、720p、3 秒 × 3，共 54 點）

實際做法：角色元素 `shosho_line`＝正面站姿＋logo 臉特寫兩張（建立免費）；免費帳號一次只能跑一個任務。
價格：Kling 4.0 5 秒 50 點；4.0 Flash 5 秒 30 點、3 秒 18 點。逐格看 0.2／1.6／2.9 秒：

| 鏡頭 | logo 臉 | 白 T＋橘牌 | 平面線條 | 表情 | 3D 場景／運鏡 |
|---|---|---|---|---|---|
| 健身房 | 部分：鋸齒頭髮在，臉變成一般線條臉（兩點眼），中段一格接近 logo 的眨眼笑 | ✓ | ✓ | 有（皺眉→咬牙笑） | 寫實健身房，鏡頭有繞；深蹲不明顯 |
| 廚房 | **最像**：鋸齒頭髮、眨眼、歪嘴笑都在，但左右眼對調 | ✓ | ✓ | 開心 ✓ | 卡通風 3D 廚房，推近 ✓ |
| 書桌 | 部分：一般線條臉＋皺眉 | ✗ 變成黑上衣（橘牌還在） | ✓ | 專注皺眉 ✓ | 寫實書房＋檯燈，橫移 ✓ |

結論：平面線條在 3D 場景裡守得住、表情是手繪線條感、運鏡有；**沒守住的是 logo 臉的辨識度（三段只有一段像）和衣服顏色（一段變黑）**。
參考圖只有一個角度、只有兩張，是可能原因之一（未驗證）。

### 成本換算（修修嫌貴，2026-11-17 查）

- Kling 網頁的訂閱彈窗寫 Standard「$6.99 / 1st mo」（實際看到）；第三方說 Standard 每月 660 點、續訂約 $8.80（未在 Kling 官方頁查證）。
  → 約 **每 100 點 1 美元**。第一輪 54 點 ≈ 0.6 美元；下一輪 70–90 點 ≈ 0.8–1 美元。
- 每秒成本：Kling 4.0 Flash 6 點／秒 ≈ 0.06 美元；Kling 4.0 10 點／秒 ≈ 0.1 美元。
- 其他家（第三方彙整，未查證、各家數字互相矛盾）：Hailuo 02 Standard ≈ 0.045 美元／秒、PixVerse v5.6（收 3 張參考圖）≈ 0.035–0.075、
  Seedance 1.5 Pro ≈ 0.025、Vidu Q3 ≈ 0.07、Wan 2.6 ≈ 0.07。角色一致性都沒有獨立評測。
- 本機跑開源 Wan 2.2：每段不花錢，但要裝 ComfyUI 等環境；參考圖一致性（Phantom／VACE）沒找到實測。

### 本機試跑：Wan2GP + Phantom 1.3B（2026-11-17）

- 安裝：`E:\Wan2GP`（commit 38758d3），Python 3.12 venv、torch 2.10.0+cu130、triton-windows 3.7.1、SageAttention 2.2.0；
  RTX 5070 Ti（sm_120）可用，**沒裝系統 CUDA 工具包也跑得起來**。
- 設定：同兩張參考圖、同健身房提示詞，832×480、49 格（約 3 秒）、30 步、seed 42。
- 時間：首次下載模型＋編譯約 15 分鐘；生成約 6 分鐘、解碼約 30 秒。
- 結果：**第一格的 logo 臉幾乎是原檔**（耳朵、鼻子、眨眼都在，比 Kling 準）；但後面臉變形、頭和身體黏在一起、
  橘牌跑到脖子；背景是模糊的白色空間不是健身房；沒有深蹲、鏡頭幾乎不動。1.3B 抄參考圖很準，但不聽提示詞。

### Dreamina（Seedance）試跑（2026-11-17）

- 免費帳號：登入有 110 點（隔一陣子變 135）。**2.0 Fast、2.5 系列免費帳號按生成會跳付費方案**，點數不能用；
  只有 2.0 Mini 有「1 次免費」。付費：Basic 首月 NT$9.31（98% off）、之後 NT$490／月、每月 725 點（約 20 段 2.0 Fast）。
- 價格（頁面實際顯示，720p 4 秒）：2.0 Fast 24 點、2.5 Flash 56 點、2.0 Mini 免費這次 0 點（原 36）。
- 上傳：Dreamina 用 showOpenFilePicker，改寫它把分段送進頁面的兩張圖交出去；提示詞用 @ 指名兩張參考圖。
- 健身房（2.0 Mini、4 秒、720p）：**動作最好**——真的蹲下去、鏡頭有繞、全身入鏡、白 T＋橘牌、平面線條身體都守住；
  **臉不對**：被上了膚色（棕褐色）、頭髮變灰、一般的苦臉，沒有 logo 的鋸齒頭髮和眨眼。
- 免費點數（135）**不能用在 Seedance**：付費彈窗明寫「Subscriber-exclusive models: Seedance 2.5 and 2.0 series」；
  用完 Mini 免費那次後按 Regenerate（24 點）也跳付費彈窗、點數沒扣。免費版下載會有 Dreamina 浮水印。

## 改走多格漫畫（2026-11-18）

修修：「我想一開始還是採取簡單一點的方式，就讓 2D 的人偶在 2D 的場景移動就好了。場景也可以由你去呼叫 ChatGPT 的 ImageGen 產生出來。就以多格漫畫形式來做。」生成影片模型暫停，這份調查留作之後回頭時的底。

- 分工：ChatGPT（修修的 Plus 帳號，網頁版）只畫**沒有人物**的場景；人物一律用 `figure.js` 的線條修修疊上去，所以 logo 臉不會被重畫。
- 場景提示詞（三張同一個對話裡連續生，第二、三張說「exactly the same style and rules」維持一致）：
  > Flat 2D comic background illustration, thick clean black outlines, flat fills in white and light warm gray only, at most one small orange (#f57331) accent object, no shading, no gradients, no texture, no text, no people, no characters, no hands. Eye-level straight-on view, the floor occupies the bottom third of the image and the middle of the floor is left empty so a character can be placed standing there. Landscape 3:2. Scene: …
- 產出 1536×1024 PNG，每張約 30–40 秒；三張（書桌、健身房、廚房）都一次到位，畫風和線條修修一致。
- 取圖：頁面上的 `blob:` 圖用 JS 觸發下載，瀏覽器窗格會把檔案留在 `C:\Users\Shosho\Downloads\<uuid>.tmp`（內容就是 PNG），再複製到 `assets/scenes/comic/<場景>.png`。
- demo：`comic.html`，15 秒四格。鏡頭＝整頁當畫布逐格推近（`camTo`）、最後拉遠；格內背景慢慢放大，放大中心設在人物腳下，腳不會在地板上滑。人物站位用 `imgPt(格子, 圖上 x, 圖上 y)` 從場景圖座標換算。特寫格用集中線＋汗滴表現費力（logo 表情本身不動）。
- 第一支照稿 demo（2026-11-18，修修：「參考我過去的文章先產生一個腳本，把它變成四格漫畫的分鏡，然後把人物放進去動」）：`comic_neat.html`，稿子取自 2023-02-10〈不用運動輕鬆減肥……〉（`E:\nakama\data\brook\shosho-posts-raw\activate-neat-to-burn-more-fat.md`），台詞與數字全部出自原文。四格＝跑步只燒 625 大卡／愛動的人一天 800～2000 大卡／這叫 NEAT／站著工作。新生兩張場景：河濱跑道 `run.png`、站立辦公桌 `standing.png`。
- 漫畫元件：對白泡泡（修修自己講）、旁白方框、橘色大數字（白描邊）、汗滴、集中線；figure.js 新增 `jogA`／`jogB` 兩拍跑步姿勢。
- 格子比圖寬很多時（2.5:1 對 3:2）用 `fit:"h"`：整張圖照高度放，左右用同色牆面＋地板延伸（要先量圖左右緣的地板線高度與地板色）；否則 cover 會把桌面或地板切掉，人物比例對不上。
- 存圖的限制：chatgpt.com 頁面連不到本機（`fetch 127.0.0.1` 失敗）、也不准寫剪貼簿，只能走下載；窗格每次下載都會跳「儲存」視窗。檔案在視窗跳出時就已經以 `.tmp` 寫進 `C:\Users\Shosho\Downloads\`，我當下複製走；修修按取消後 `.tmp` 會自動消失。

### v2：3D 透視場景＋走 API（2026-11-18）

修修：「這場景都是 2D 的，但我需要人物在 3D 的場景走動，所以是靜態的圖片，但是是 3D 的場景……走向是縱向的 Z 軸，人物大小就會改變」；並要求「改走 API，然後你看看要花多少 credit」。

- 生圖改走 OpenAI Images API（`E:\nakama\.env` 的 `OPENAI_API_KEY`），直接寫檔、不跳視窗。腳本：scratchpad `comic/gen_scene.py`；每次呼叫寫一行到 `assets/scenes/comic3d/_cost_log.jsonl`。
- 價格（developers.openai.com/api/docs/pricing，2026-11-18 查，每 1M tokens）：chatgpt-image-latest／gpt-image-1.5 文字輸入 $5、圖片輸出 $32、文字輸出 $10；gpt-image-2／2.5／2.6 圖片輸出 $30；gpt-image-1-mini 圖片輸出 $8。頁面沒有「每張」價，按回應的 usage 算。
- 實測：chatgpt-image-latest、medium、1536×1024，每張約 250 輸入 token、1,568 圖片 token＋約 400 文字 token → **約 US$0.055／張**，12–15 秒。這次 4 張（跑道試一次＋跑道、書房、開放辦公室）合計約 US$0.22。
- 提示詞要求一點透視、鏡頭在眼睛高度、地平線約 40%、地板從畫面下緣一路收到消失點、地板中間淨空——三張都一次到位。
- 消失點自動量：scratchpad `comic/vp.py`（OpenCV Hough 抓斜線＋RANSAC 交點），三張量到的消失點目測都對。
- 透視縮放：鏡頭在眼睛高度時，站在地板上的人頭頂都在地平線附近 → 腳踩在第 iy 列時身高 ≈ 1.08 ×（iy − 地平線 y）。人物沿路徑移動時在「世界座標」線性插值（深度 1/(iy−地平線)），所以往鏡頭跑會越近越快、越來越大。實作在 `comic3d_neat.html` 的 `walker()`。
- 版面改成上排兩格 3:2、下排寬特寫＋一格 3:2，避免 cover 裁切把透視地板切掉；字級、框線按每格推進倍率換算，推進後螢幕上字一樣大。
- 限制：線條修修只有正面，往畫面深處走會像倒退走，所以這版只讓他往鏡頭走或橫移；logo 臉不會轉向。

### 驗收（三個鏡頭都看）

- logo 臉認得出來（鋸齒頭髮、一眼眨、歪嘴笑的配置還在）
- 線條粗細、白 T＋胸口橘色長方形三個鏡頭一致
- 角色保持平面線條，沒有被畫成 3D 卡通
- 表情是手繪線條感，不是另一張臉
- 場景是 3D、有光影
