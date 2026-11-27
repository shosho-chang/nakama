---
name: youtube-reframe
description: >
  YouTube 照稿影片的「畫面搬移」：在 Resolve 的 A-roll 片段上用 Fusion Transform 逐格打關鍵影格，
  原地推近（1.3 倍），或放大並把臉讓到左／右 1/3（1.4 倍），角落小卡則輕移（1.3 倍、臉到 40%／60%），
  讓疊加畫面（書封、舊片導流、梗圖等）有位置；0.5 秒推進、
  同速退回，眼睛高度全程不動。Triggers: /youtube-reframe、「畫面搬移」「把臉移到左邊」「推近」
  「讓位給書封」。也有不開 Resolve 的預覽。
  不用在：跳接剪點的推近（第 17 類，硬切、不緩動，另外處理）、短片／長精華的 punch
  （run_shortform_director／run_short_director 自己的機制）。
---

# youtube-reframe — 畫面搬移

**版本：v1.0（2026-11-15）**

修修：「把我的臉移到左邊 1/3 的位置，然後放大，這個動作是一起同時進行的」；
「在螢幕上畫東西，以及變更影片 frame 的大小跟位置，這兩件事是分開的」——所以畫面搬移是獨立的 skill，
書封這類疊加畫面只負責畫東西。

## 搬移種類（修修逐輪定版，不要自己改）

| 種類 | 放大 | 臉最後在哪 | 時間 | 修修原話 |
|---|---|---|---|---|
| `push` 原地推近 | 1.3 倍 | 原地，眼睛高度不變 | 0.5 秒 ease-in-out | 「0.5 秒，我覺得 1.3 倍 OK」（試過 0.4／0.5／0.6／1／2 秒） |
| `left` 放大左移 | 1.4 倍 | 畫面寬度 1/3 | 同上 | 「1.4 倍」（1.3 倍最多只能到 38%） |
| `right` 放大右移 | 1.4 倍 | 畫面寬度 2/3 | 同上 | 「第三種就是往右」，照左移對稱 |
| `nudge_left`／`nudge_right` 輕移 | 1.3 倍 | 畫面寬度 40%／60% | 同上 | 2026-11-27 角落小卡：「把人稍微移到側邊、挪出空間…調整的幅度或許就不用這麼大」→「直接做」；先試 1.2 倍（臉偏右時只到 43%）→「試試看 1.3 倍」 |

- **退回**：「這三種是以同樣的速度回到原來的畫面」——同樣 0.5 秒、同一條曲線。
- **退回時機**：搭配畫面結束前 0.25 秒開始（書封開始退場的那一刻），結束後 0.25 秒回到原畫面。
  搭配畫面很短時，推到位就馬上退。
- **眼睛高度不變**：以兩眼中點為準（mediapipe 虹膜中點，整段取中位數；頭自然晃動不追）。
- **不露黑邊**：水平平移跟放大走同一條曲線，任何一格都不會露邊；臉偏太多到不了目標時，停在不露邊的極限。
- 參考過 MKBHD 的推近，但查不到他的實際設定；以上數字都是修修看原型選的。

## 疊加畫面跟搬移怎麼配

| 疊加畫面 | 畫面在哪 | 搬移 |
|---|---|---|
| 書封（`youtube-book-cover`） | 固定右側 | `left`，跟書封同一秒開始 |
| 重點條列（`youtube-side-bullets`） | 側欄固定右側 | `left`，跟側欄同一秒開始 |
| 舊片導流、梗圖（`youtube-inset`） | 上方角落，規格檔 `side` 決定（預設右） | 卡在右 → `nudge_left`；卡在左 → `nudge_right`（收據帶 `side`） |

新增一類固定在某一側的疊加畫面，就在 `reframe.py` 的 `OVERLAY_MOVES` 加一筆；側邊由 DP 決定的放 `SIDE_MOVES`。
`moves_for_receipts(<episode>)` 會依 `visuals/*.json` 收據排出搬移（原始錄影的秒數；
鋪時間軸時換成 timeline 秒數再套）。

**`push` 什麼時候用還沒定**：規劃表目前沒有對應的分類（第 17 類「推近」是跳接剪點用的硬切，
不是這個）。要讓 Director 能選，得先跟修修定一個分類。

## 怎麼做的（Resolve）

- 每個片段加 Fusion comp：MediaIn → `Reframe`（Transform）→ MediaOut。
- **Pivot 固定在畫面中心，只打 Size 與 Center 兩條逐格關鍵影格**——同一片段上好幾次搬移互不干擾；
  搬移跨剪點時各片段各自寫，曲線連續。
- **一律用 Python 的 Fusion API**（`comp.AddTool`、`ConnectInput`、`AddModifier`、`SetInput(值, 格)`）。
  不用 `comp.Execute(lua)`：它是非同步的，寫完當下讀不回來確認。
- 寫完**逐格讀回**比對 Size、Center，對不上就停（`ReframeError`），不讓錯的推近進成片。
- 同一片段上舊的 `Reframe` 會先刪掉重做——`apply` 一次要給這條 timeline 的**全部**搬移。
- 片段上已經有別的 Fusion 節點（例如 punch 的 `PunchZoom`）會停下來，請修修手動處理。
- 只動 Resolve **目前開著的專案**，不切換專案。

2026-11-15 在測試專案實測：三種搬移寫進三個片段約 3.6 秒，Resolve 輸出跟預覽逐格一致
（平均差 2–4／255，是壓縮差與錨點取樣範圍不同），四邊沒有黑邊。

## 怎麼跑

```bash
# 寫進 Resolve 目前開著的 timeline（秒數＝timeline 上的秒數，從 timeline 起點算）
python -m agents.brook.youtube_video.visuals.reframe apply --move left@12.0-15.5 --move push@40-43
# 指定 timeline
python -m agents.brook.youtube_video.visuals.reframe apply --timeline "<名稱>" --move right@8-11
# 不開 Resolve，輸出預覽（秒數從 --start 起算）
python -m agents.brook.youtube_video.visuals.reframe preview "<錄影>" --start 118 --dur 7 \
    --move left@1.5-5.0 -o preview.mp4
```

`--move 種類@開始秒-結束秒`：開始＝開始推的那一秒；結束＝搭配畫面結束的那一秒。

需要：本機 DaVinci Resolve Studio（Preferences → General → 外部 scripting 設 Local）、
mediapipe 與臉部模型 `E:\data\models\face_landmarker.task`（`FACE_LANDMARKER_MODEL` 可改）；
預覽另外要 OpenCV。這些都只在桌機上用，CI 測試不需要。

## 驗收

1. 指令最後一行是「寫好 N 次搬移……逐格讀回都對」；有 ✗ 就回報給修修。
2. 在 Resolve 播一次：推到位時臉在 1/3（或 2/3）線上、眼睛高度沒變、四邊沒有黑邊。
3. 錄影是 1080p 時放大會變軟；4K 錄影放到 1.4 倍不掉畫質（2026-11 的錄影是 1080p 29.97）。

## 還沒定

- `push` 由誰、在什麼情況下觸發（規劃表分類）。
- 鋪時間軸的程式（初剪 skill）怎麼把收據的原始錄影秒數換成 timeline 秒數、再呼叫 `apply_moves`。
