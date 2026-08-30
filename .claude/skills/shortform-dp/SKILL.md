---
name: shortform-dp
description: >
  短片（60–120s 直式 Shorts）專屬攝影指導手冊（ADR-067）。Triggers: /shortform-dp、
  「短片找素材」、「跑短片 DP」。讀 shortform-director 的意圖層 → 出**直式**搜尋詞 →
  取得素材、寫 acquisition receipt、驗收 → 回填 `<id>_broll.json`。
  **不服務長片**——長片走 brook-dp（它的搜尋一律鎖 Horizontal）。
  取得與驗收契約歸 shared/shortform_broll.py，本 skill 只呼叫、不重新發明。
---

# shortform-dp — 短片攝影指導手冊

**版本：v1.0（2026-08-30，ADR-067 長短分家）**

修修 2026-08-30 裁決：

> 「再次強調，短影片的素材都要直的⋯⋯所以短影音的 Director 跟 DP 應該是要分開才對，
> 因為他們做出來的素材跟剪輯的緊湊程度完全都是不一樣的。」

你是短片這條線的 **DP**：拿到 `shortform-director` 的意圖，決定**用哪一支直式素材**、
把它弄到手、寫收據、驗收、回填。你**不改意圖**——畫不出來就退回 Director。

## 與 brook-dp 的分界

| | 本手冊（短片） | brook-dp（長片） |
|---|---|---|
| 候選頁方向 | **一律 Vertical** | 一律 Horizontal |
| 淘汰條件 | 預覽或 metadata 顯示**高不大於寬**就直接淘汰 | 寬不大於高就淘汰 |
| 每支使用長度 | 1.5–4s | 依 beat |
| 全片支數 | 2–3 支 | 數十支 |
| 授權證據 | `assets/broll/<slug>.acquisition.json`（ADR-067） | ADR-065 收據鏈 `trusted-acquisitions/` |
| 落地檔 | `highlights/tighten/<id>_broll.json` | `storyboard.yaml` / DP-FULFILLMENT.json |

**跨格式共通、刻意共用**（改那邊兩線一起變，這是故意的）：

- brook-dp〈修修本人情境的固定 stand-in〉——同一位模特兒是跨集品牌一致性，與畫幅無關
- brook-dp〈選片鐵則〉3 的語意判準（畫面＝語意、情緒極性）與 5 的負面意象禁用清單

## 紅線

1. **直式是硬條件**。搜尋 URL 就要帶方向參數，不要搜完再挑：
   - Envato：`app.envato.com/search?itemType=stock-video&term=<英文>&filter.orientation=Vertical`
   - Pexels：搜尋頁的 Orientation 篩選選 Portrait
   下載前用 metadata 或縮圖比例再確認一次；`ffprobe` 是最終判準，gate 也會擋。
2. **沒有收據不上片**。素材落地時同時寫 `assets/broll/<slug>.acquisition.json`
   （contract `podcast-highlight-asset-acquisition-receipt-v1`），`original_media.sha256`
   必須是**實際檔案 bytes** 的雜湊。**絕不憑檔名替既有檔案補寫收據**——那是偽造來源。
   硬碟上有一支看起來對的素材但沒有收據 → 重新走一次取得流程，不是補一張紙。
3. **下載目錄只是暫存區**：瀏覽器預設下載到 `E:\` 根目錄。驗過 bytes 與 SHA-256 之後
   移進 `<episode>/assets/broll/`，那裡是唯一 authoritative copy。
4. **寧缺勿猜**：找不到語意精準的直式素材 → 退回 Director 改成留 talking head。
   短片只有 2–3 個 cutaway，錯一個就是三分之一。
5. **不改意圖**：`source_cues`、落點、要表達什麼是 Director 的；你只決定用哪一支。
6. **AI 生成素材一律停用**（修修十八輪裁決，跨格式共通）。

## Step 1 — 每個意圖出 3–5 組不同切面的英文詞

切面照 brook-dp Step 2（字面／視覺隱喻／場景／證據感／情緒），但短片多兩條約束：

- **景別要窄**：直式畫面只有 1080 寬，全景在手機上什麼都看不到。優先
  `close up`、`medium shot`、單一主體。多主體的熱鬧畫面在 9:16 裡會變成一團
- **主體要在中線**：直式素材若主體偏一側，裁切／構圖沒有救援空間

每則搜尋請求欄位：

- `query` — 該切面英文詞（含景別，如 `cockatiel playing toy close up`）
- `orientation` — **恆為 vertical**
- `duration_hint_sec` — 使用區間 1.5–4s（來源可以更長，用 `src_in` 取段）
- `mood` — 光線/調性；同一支短片調性一致
- `negative` — 綠幕（標題含 Green）、corporate 擺拍假笑、文字疊圖、視訊通話感、
  AI 生成感、小孩用平板/3C（修修負面清單）

搜尋詞與候選寫進 `<id>_broll.json` 的 `_search` 區塊，否決理由一併記——
下一輪才知道哪些切面試過了。

## Step 2 — 候選複核（自己做，不要卡在修修那裡）

**素材由 agent 自己開瀏覽器下載**（Claude in Chrome，修修的 Envato 已登入）。
修修 2026-08-31：「你就去開 Envato，把素材補齊啊。我已經說過很多次了，我不會
自己去下載 Envato 的素材。」訂閱是吃到飽，抓錯一支的成本只有硬碟空間；把候選
丟回去等他點頭，換來的是整支短片卡住不動。**驗收點在 preview，不在候選頁。**

每個落點自己過一遍這四項再決定，不合格就換一支：

1. 尺寸與時長——`height > width`，能覆蓋一個完整語意單位
2. 一句「不看字幕，這個畫面讀出來是什麼意思」——必須讀得出 Director 標的那句話
3. 情緒極性與這支短片一致
4. 負面清單（綠幕、浮水印、AI 生成、擺拍感、文化錯位）

「有人、有動物、有小孩」不算理由。判不下去的**寧缺勿猜**——留 talking head 並把
意圖寫進 `_wanted`，不要為了填格子塞一支語意不準的。

**下載格式**：item 頁的下載鈕有下拉選單，選 **1080P（1080×1920）**——時間軸就是
1080×1920，抓 4K ProRes 只是白佔幾百 MB。沒有下拉就是只有一種格式，照抓。

## Step 3 — 取得與寫收據

下載後逐項驗，不可抽查：

```bash
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,duration \
  -of csv=p=0 "<檔案>"
```

1. **直式**：height > width，不是就丟掉重找
2. **SHA-256**：算實際檔案雜湊
3. 移進 `<episode>/assets/broll/<slug>.<ext>`
4. 寫 `<episode>/assets/broll/<slug>.acquisition.json`：

```json
{
  "contract": "podcast-highlight-asset-acquisition-receipt-v1",
  "acquired_at": "<ISO8601 UTC>",
  "asset_id": "<slug>",
  "episode_id": "<episode 資料夾名>",
  "cut_id": "<winner id>",
  "provider": "pexels|envato",
  "provider_item_id": "<平台 id>",
  "source_url": "<素材頁 URL>",
  "license": "<授權字串>",
  "source_class": "licensed_stock",
  "original_media": {
    "path": "assets/broll/<slug>.<ext>",
    "bytes": <實際 bytes>,
    "sha256": "<實際檔案雜湊>"
  }
}
```

5. **跨集去重**：掃其他集 `assets/broll/*.acquisition.json` 的 `sha256` 與
   `source_url`，命中就換候選

## Step 4 — 回填與驗收

回填 `<id>_broll.json` 的 `items`：`kind`／`slug`／`t0`／`t1`／`src_in`／
`source_cues`，並保留 Director 的 `_shot`／`_mood`。

```bash
py -3.10 scripts/run_shortform_broll.py <episode> --id <id> --validate-only
py -3.10 scripts/run_shortform_broll.py <episode> --id <id> --stills <dir>
```

gate 會驗：收據存在、SHA-256 相符、直式、`source_cues` 落點、不壓 punch、
不壓開場分割、彼此不重疊、`src_in` 沒有超出素材長度。

樣張逐張看：主體有沒有被裁掉、有沒有浮水印/綠幕、有沒有蓋到字卡。
不合格 → 換備選或退回 Director 降級，不硬上。

## 每支寫回

搜尋詞、候選、否決理由、降級寫進 `<id>_broll.json` 的 `_search`；
手冊層級的教訓（新的負面清單、新的搜尋切面）寫回本檔並記日期。
