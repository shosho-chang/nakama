---
name: sound-designer
description: >
  短片/長片音效設計手冊。Triggers: /sound-designer、「配音效」、「加 SFX」、
  「這裡該有什麼聲音」。讀 tight SRT + 事件 JSON → 標語意音效點與環境音 →
  產 <id>_sound.json → run_short_sfx.py 疊軌。**意圖層在本手冊，實現層歸
  scripts/run_short_sfx.py**（照 brook-director / brook-dp 的分離）。
  音效語意以 sfx-dictionary.yaml 為準——那是修修口述的，不可從檔名猜。
---

# sound-designer — 音效設計手冊

**版本 v1.0（2026-07-27 二十一輪；修修：「sound design 比插 B-roll 更細緻，
人的聽覺比視覺靈敏得多」）**

你是這條產線的**音效設計師**：決定哪一刻該有什麼聲音、為什麼。你不是
疊軌工人（那是 `run_short_sfx.py`），也不是音效庫管理員（那是
`build_sfx_index.py`）。

## 紅線

1. **語意只認字典**：`sfx-dictionary.yaml` 的 `when` 欄是**修修親口說的
   用法**。不在字典裡、或 `verified: false` 的音效 **禁止排進 sound.json**
   ——寧缺勿猜（CLAUDE.md 最高指導原則）。
   血案：索引早期用檔名關鍵字猜語意，把 `AWW`（看到可愛/感人的憐愛聲）
   歸成「失望」、把 `TA DAHH`（亮相/show off）歸成「慶祝」——全錯。
2. **我沒有聽覺**。客觀特徵（時長/峰值/頻段/音色）可量測、可驗證「音效與
   標籤相符」；**聽感與文化記憶只有修修能判**。新音效一律走試聽包。
3. **少即是多**：聽覺比視覺敏感——**語意音效每支短片 ≤3 個**。UI 事件音效
   （卡片/punch，機械層）不算在內。寧可少一個，不要吵。
4. **不蓋話**：語意音效落在句與句之間的呼吸點，不壓在字上。長音效（>4s）
   要有留白容納，否則換短的。

## 音效的三層（分工不可混）

| 層 | 決定者 | 內容 | 檔案 |
|---|---|---|---|
| **UI 事件層** | 機械規則 | hero 卡=ding、ramp=riser、cut=impact、貼紙=pop、tier2/B-roll=swish | `run_short_sfx.py` 內建（highlight-cut SKILL Step 10） |
| **語意層** | 本手冊 | 失敗/勝利/亮相/強調/注意/震驚/憐愛 | `<id>_sound.json` |
| **環境層** | 本手冊 | 跟著 B-roll 素材走的 diegetic 音（跑車→引擎、錢包→翻找） | `<id>_sound.json` 的 `ambient` |

## Step 1 — 讀稿標語意點

輸入：`highlights/srt/<id>_tight_r*.srt`（最新版）+ `<id>_broll.json` /
`_titles.json` / `_zoom.json`（避開已有 UI 音效的時間點）。

逐句問「這裡的情緒轉折值不值一個聲音」。觸發信號 → 字典條目：

| 稿面信號 | 音效語意 | 字典候選 |
|---|---|---|
| 搞砸、預期落空、「結果失敗了」 | 失敗 | WA WA WA |
| 悲傷、遺憾的情境陳述 | 哀傷 | SAD MUSIC |
| 震驚的負面結論（研究打臉、真相揭曉） | 震驚 | DUN DUN DUNNN |
| 達成、過關、「終於」 | 勝利 | VICTORY |
| 純粹開心、可愛的成功 | 慶祝 | YAY |
| 揭曉、亮相、「就是這個」 | 展示 | TA DAHH |
| 要觀眾記住的重點、劃線句 | 強調 | MLG HORNS |
| 「但是」「你知道嗎」轉折、要人豎耳朵 | 注意 | MGS ALERT / SUDDEN SUSPENSE（同支影片擇一） |
| 可愛/感人的畫面或故事 | 憐愛 | AWW |

**節制檢查**（排完自問）：
- 語意音效總數 ≤3？超過砍最弱的
- 有沒有跟 UI 事件音效撞在 1.2s 內？（`run_short_sfx.py` 會 thinning，
  但撞掉的是你的語意音效——自己先錯開）
- 同一種語意在一支影片內重複用？重複 = 廉價感，只留最強的那次

## Step 2 — 環境音（跟素材走）

`<id>_broll.json` 逐項看：這個素材有沒有「該有的聲音」？
- 跑車 → 引擎；翻錢包 → 翻找；手機通知 → 提示音；打字 → 鍵盤
- **只加畫面上看得到來源的聲音**（diegetic）。看不到來源的環境音會讓
  觀眾以為是主場景的聲音，很怪
- 音量比語意音效再低一階（環境音是襯底不是事件）
- 素材本身有原音時**不疊**——先聽素材（修修聽），有就用原音

## Step 3 — 沒有的音效怎麼辦

1. `python scripts/build_sfx_index.py --query "<語意或關鍵字>"` 查修修的庫
   （820 檔，`★` = Usual use 常用區，優先）
2. 庫裡沒有 → Envato Sound Effects 找候選（列表頁下載鈕要用 `find` 拿 ref
   再點，座標點擊會誤觸別列）
3. **一律做試聽包**：`python scripts/build_sfx_audition.py --query "<label>"
   --out <dir>` → 候選正規化串接 + 編號 beep → 修修聽一次回編號
4. 修修口述用法 → **寫進 `sfx-dictionary.yaml`**（`verified: true`）→ 才可用

## Step 4 — 產出 `<id>_sound.json`

```json
{
  "semantic": [
    {"t": 41.9, "file": "WA WA WA", "why": "「我又賺不到錢」——預期落空",
     "gain_db": -6}
  ],
  "ambient": [
    {"t0": 0.7, "t1": 3.2, "file": "Car sounds with gas pedal",
     "slug": "sports-car-real", "gain_db": -12}
  ]
}
```

- `t` = （緊·導播）timeline 秒（與 tight SRT 同軸）
- `why` 必填——沒有理由的音效就是噪音；review 時修修看的是這欄
- `gain_db` 相對字典基準的微調（預設 0；語意音效約 −6、環境音約 −12）

疊軌：`python scripts/run_short_sfx.py <episode> --id <cid>`（會同時排
UI 事件層與本檔的語意/環境層）。

## Step 5 — 驗收

- 自檢 loop（highlight-cut SKILL Step 11）出的 preview **一定要修修聽**
  ——我只能量到「有沒有削峰」，判斷不了「吵不吵、對不對味」
- 對白本身峰值已近 0 dBFS（本集實測 −1.5 dB），SFX 疊加區量到 −0.0 是
  對白造成的，不要據此調音效大小

## 每輪教訓寫回字典

修修每次口述用法或否決一個音效，**當下就寫進 `sfx-dictionary.yaml`**
（含 `note` 記下校正原因），不要留在對話裡。字典是這個 skill 的核心資產。
