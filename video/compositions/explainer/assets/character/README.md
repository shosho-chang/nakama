# 修修動畫人偶：角色設計

| 檔案 | 內容 |
|---|---|
| `turnaround_v1.png` | 定稿五面轉面圖（正面、斜前、側面、斜背、背面），1536×1024 |
| `expressions_v1.png` | 定稿表情圖，正面頭肩 3×3（ChatGPT 生成，以 `turnaround_v1.png` 為參考），1536×1024 |
| `turnaround_v1_source_chatgpt.png` | 修圖前的 ChatGPT 原圖（ChatGPT 對話「Generate Character Turnaround Sheet」） |
| `tools/turnaround_fix.py` | 從原圖修到定稿的程式：`python turnaround_fix.py <原圖> <輸出> 5`（最後一個參數是眉毛角度） |
| `tools/shirt_pattern.py` | 把品牌碎片印到 T 恤下擺：`python shirt_pattern.py <修好的圖> <shards-band-gray.png> <輸出資料夾> 0.24`，取 `opt_C_hem.png` |

## 程式人偶（`assets/kit/figure.js`）

依這兩張定稿做成可動的人偶，檢查頁 `compositions/figure_sheet.html`（五角度、九表情、全部姿勢）。

- 頭：正面預設＝logo 原檔；其他表情、q3／side／q3back／back＝`assets/heads/`（`tools/make_head_sprites.py` 從定稿圖切，`tools/fix_think_head.py` 把思考格的大手拿掉、接回下巴）。
- 身體：程式畫，尺寸量自 `turnaround_v1.png` 正面 ×0.827（全身約 600px，跟舊版一樣，既有合成頁的透視縮放不用改）。
- 四肢：手臂跟腿一樣是單一條黑線（粗細同外框），任何姿勢都不變成雙線；袖子是圓肩、跟著上臂轉；手是 H3 手套、鞋是圓頭鞋。
- 跑步（jogA／jogB）：屈膝那腳的對側手往前擺（手肘露在袖子外、前臂橫到胸前、畫在身體前面），另一手往後幾乎伸直。
- 側面走路（sideWalkA／sideWalkB，配 `view: "side"`）：兩腿一前一後跨步、後腳膝蓋往後彎；手跟對側腳同向擺。
- 特寫（手、腳、道具）：**不另外畫一份手腳**，是把同一隻人偶放大、對準那個部位——`make` 時給大 `scale` 加 `lineWidth`（畫面上的線寬 px，特寫用約 20），
  再用 `f.aim("fL", x, y)` 把關節對到格子裡的位置；`f.joint("hRtip")` 拿手套前端位置放道具；`f.tween(tl, { lh: 25, lk: -45 }, at, dur)` 做姿勢表以外的小動作（抖腳）。
  不給 `lineWidth` 直接放大，線會跟著變五倍粗、整隻變一團黑（2026-11-20 實測）。
- 下擺碎片：`shards_orange.png`（設計系統 `shards-band-gray.png` 只取橘色，原圖不重畫）。
- 用法：`f.view(tl, "side", at, { dir: 1 })`、`f.face(tl, "laugh", at)`、`f.pose(tl, "think", at, dur)`、`f.set({ view, dir, face, pose })`。

## 設計決定（2026-11-18～19，修修逐項確認）

- 身體：A3 方向（曲線版 T 恤輪廓），四肢單線，手是 H3 手套（短圓拇指、手腕不留缺口）。
- 側面頭：沿用 ChatGPT 原本的側臉，只修三處——頭頂弧線到額頭接成一條平滑曲線、眉毛改成從鼻樑伸出、往後上揚約 5°、眼睛獨立成半月。
- 斜前：鞋朝右（跟臉同方向）；領口黑塊拿掉，兩條領口線接進下巴。
- T 恤圖樣：C「下擺碎片」——品牌 Motif A/B 碎片（設計系統「張修修的不正常宇宙」`assets/patterns/shards-band-gray.png` 原圖裁切，不重畫），只印在 T 恤下半部，五面都看得到。原本胸口那塊單一橘色矩形拿掉。

- 表情圖（2026-11-19 定案）：由左到右、由上到下為 1 預設（圓點眼＋眨眼＋開口笑）、2 大笑、3 驚訝、4 思考（H3 手套托下巴）、5 困惑、6 疲累、7 生氣、8 緊張（兩眼睜開、八字眉、波浪嘴、汗滴）、9 堅定。第 8 格右眉沒有接鼻線，是表情需要的例外。

- 人偶定案（2026-11-19，修修確認）：外型（轉面圖、表情、下擺碎片）＋程式人偶的規則（全身約 600、四肢單線、H3 手套、圓頭鞋、正面預設 logo 原檔、`view`／`face`／`pose`／`jump` 呼叫方式）。之後修漫畫場景與動作時不動這些規則。
- 品牌例外（2026-11-19，修修確認）：側面、背面與表情的臉是依定稿圖重畫的，是「logo 不重畫」規則的明文例外，只用在這個人偶；正面預設一律用 logo 原檔。

## 已知限制（修漫畫時遇到再處理）

- 側面跑步沿用正面姿勢的角度（側面走路已補：sideWalkA／B）。
- 鞋子不會以腳尖為支點轉動，抖腳是整隻腳抬起再放下。
- 碎片圖樣在每一面是各自貼的，轉身動畫時不會像真布料一樣連續繞過去。
