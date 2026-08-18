# 情境 icon 動畫

把簡單透明 icon 當成畫面中的角色，依講者描述的動詞改變位置。目標是讓「誰對誰做了
什麼」可視化；不是替靜態貼紙增加無意義的上下漂浮。

## 使用條件

只在 transcript 同時存在**具體角色／物件**和**可視動詞或關係**時使用：

- 進入／出現 → 從畫面邊緣進場
- 靠近／對話 → 兩 icon 靠近並停在對話距離
- 擋住／阻止 → 一個 icon 移到另一個 icon 與目標之間
- 離開／放下 → icon 退到畫面外或縮小消失
- 聚合／散開 → 多個 icon 向中心集合或向外分離
- 追逐／逃離 → 同方向不同速度移動
- 角色轉換／情緒替換 → 在同一 anchor swap icon

只有名詞、沒有動作時用 `sticker`；需要真實場景時用 B-roll；需要解釋抽象因果時用
`concept`。錯的動作比沒有動畫更干擾，無法從原話判定路徑時 fail closed。

## 素材取得

1. Director 先寫每個 icon 的角色、當下動詞、視覺特徵、negative terms 與備選搜尋詞。
2. 用登入中的 Envato Browser Computer Use 搜尋 icons／graphics，優先 SVG 或透明 PNG；
   AI/EPS 可接受，但要保留原始 zip／向量作為 receipt。
3. 避免：漸層 3D corporate icon、內嵌文字、品牌 logo、浮水印、過細線條、不同畫風混搭。
4. 下載成功以實體檔案與 download event 為準；依 brook-director 狀態機重試一次、換一個
   候選後仍失敗就標 `failed`，不插人工下載步驟。
5. 原始檔放 `assets/source/`；工作圖轉為最長邊 2048px、透明背景 PNG，放
   `assets/icons/`。manifest 記錄 source URL、license、原始與 working SHA-256。
6. 跨集掃 manifest 與 SHA-256；除非修修批准，不重複使用觀眾已看過的主角 icon。

動畫使用靜態 icon；不要下載 Envato motion template。動作由本地 deterministic
composition 產生，才能和 word timestamp 對齊並維持可修改性。

## 計畫格式

在 `<id>_broll.json` 使用 `kind: icon_motion`。LLM 決定語意 primitive 和 anchor；
renderer 將 primitive 展開成固定 tween，不讓 LLM 自由填每一幀座標。

```json
{
  "t0": 12.4,
  "t1": 17.2,
  "kind": "icon_motion",
  "slug": "buddha-lets-mara-enter",
  "note": "佛陀讓波旬進來後，兩者相對而坐",
  "icons": [
    {"id": "buddha", "file": "buddha.png", "anchor": "left"},
    {"id": "mara", "file": "mara.png", "anchor": "right"}
  ],
  "steps": [
    {"at": 0.0, "op": "enter", "id": "mara", "from": "right_edge", "to": "right"},
    {"at": 0.8, "op": "move_to", "id": "mara", "to": "center_right"},
    {"at": 1.8, "op": "move_to", "id": "buddha", "to": "center_left"},
    {"at": 2.8, "op": "face", "ids": ["buddha", "mara"]},
    {"at": 4.4, "op": "exit", "id": "mara", "to": "right_edge"}
  ]
}
```

允許的 operations 保持小而固定：

- `enter` / `exit`
- `move_to`
- `approach` / `separate`
- `block`
- `gather` / `scatter`
- `chase`
- `swap`
- `face`
- `emphasis`（一次 scale pop 或 shake）

## 動作參數

- 一個 sequence 1–3 個 icon，2–6 秒。
- 主要語意位移 0.3–1.2 秒；總路徑通常不超過畫面寬度 45%。
- 進場可 `back.out`，敘事位移用 `power2.inOut`，離場用 `power2.in`。
- 只在主要動作完成後允許 ±2–4% idle float；idle 不能取代情境動作。
- icon 可因行進方向水平翻轉，但不得任意旋轉人物；物件旋轉上限通常 ±8°。
- 每個 semantic hit 最多一個 SFX；不要替每段 tween 都配聲音。

## 安全區與軌道

- 疊 Resolve video track 4；不得覆蓋 track 3 title。
- 臉部保護區預設 x=24–76%、y=8–55%；逐字字幕保護區 y=76–96%。
- icon 的可視 bbox 必須全程留在 3–97% 畫布內；出入場過程例外，但落點必須安全。
- 左右角色至少保留畫面寬度 8% 的對話距離；`block` 必須看得出三者前後關係。

## QC

1. 檔案具真 alpha；沒有白底、棋盤格、浮水印或殘留文字。
2. 畫風一致；同一 sequence 不混用扁平插畫與寫實 3D icon。
3. 輸出 10fps motion strip，逐格檢查 enter、semantic hit、exit。
4. 對照 tight SRT／word timestamp：每次主要位移要落在對應動詞前後 ±0.2 秒。
5. 檢查全程不遮臉、字幕、hero title；衝突時先改 path，再縮 icon，最後才刪除。
6. Review packet 把每次 `icon_motion` 的 semantic hit 列為強視覺事件；單純 idle 不另計事件。
7. Renderer 尚未支援 `icon_motion` 時標 `pending_renderer` 並停下，不得靜默退化成
   `sticker_pair` 的左右漂浮。
