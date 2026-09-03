---
name: shortform-cut
description: >
  短片線（45–120s 直式 Shorts）從當選段到可交付 preview 的**唯一**製作流程
  （ADR-067）。吃 highlight-cut Step 1–5 開採出的短片 winners（`*-S*`），跑
  緊湊化 → 導播 → 字卡 → 素材 → 音效 → BGM → 自檢。Triggers:「跑短片」
  「短片流程」「shortform」或指名 `-S` cut id 的 Step 6 之後工作。分鏡的創意
  判斷在 shortform-director / shortform-dp，本冊是流程與門檻。
---

# shortform-cut — 短片線製作流程

**版本 v1.0（2026-08-30 收斂，修修：「把目前所有有關短影片的製作流程統整、
收斂成一個確定的流程」）**

**上游**：`highlight-cut` Step 1–5 產出的 `highlights/winners.short.json`
當選段（id 形如 `punch-S02`、`story-S04`）。本冊從「winner 已物化成 Resolve
timeline」接手。長片線見 `longform-cut`——兩線 script 入口已分家（ADR-067），
不共用畫幅、節奏預算、素材庫。

## 這條線的三個不變量

1. **9:16 直式**。素材必須是直的（`height > width`），橫的裁進來只剩中間一條。
2. **字卡逐子句承接全部逐字稿**（mode B，`covers_full_transcript`）。不上字幕軌
   ——那份 tight SRT 的角色是**逐字證據**，不是顯示層。
3. **一切可重現**。企劃、判斷、收據都落檔在 episode 資料夾；重跑同一條指令
   要拿到同一個結果。scratchpad 的一次性腳本不算流程。

## 前置（缺一項就不要開始）

| 檔案 | 沒有的話 |
|---|---|
| `editorial-master/v1/EDITORIAL-MASTER.json` | 沒有正式 master，整條線不成立 |
| `editorial-master/v1/conform-map.v1.json` | 詞級刀全部停用（詞的時間戳在來源時鐘上，要投影到 Master 時鐘才敢下刀） |
| `subs/words.json` | **詞級**時間戳。走 memo dual-audit 的集數只有句級（實測中位 1.90s），抓不出口吃／贅音。缺的話 `--detect` 只產得出 pause 刀，而且**不會報錯** |
| `assets/bgm/*.wav` ＋ `.acquisition.json` | Step 7 沒有音樂可放 |

補 words.json（GPU，**agent 自己跑**，不要叫修修跑）：

```bash
python scripts/run_whisperx_words.py "<episode>/normalized.wav" --output "<episode>/subs/words.json"
```

需要 Python310（torch cu128）與 PCIe `gen.max == 4`。本機實測 2.2 分鐘 / 17749 詞。

**不能從字幕反推**：Master 的 SRT 是校正過的逐字稿，口吃與贅音在文字層早就被
拿掉了，只有 ASR 的原始詞級時間戳還留著。這一點同時也是 Step 1 贅音判準的基礎。

## 流程總覽（順序不可換）

```
1 緊湊化   run_short_tighten.py --detect → 複審 cuts.json → --apply
2 導播     run_shortform_director.py       ← 會整條重建 timeline，上層軌全洗掉
3 字卡企劃 author_shortform_titles.py      ← 讀導播重出的子句級 SRT
4 字卡     run_shortform_titles.py --validate-only → 實跑
5 素材     run_shortform_broll.py --validate-only → 實跑
6 音效     run_short_sfx.py
7 音樂     run_short_bgm.py --track <name>
8 自檢     run_short_review.py → 盲審 → 修 → loop
```

**導播重跑 = 從 Step 2 全部重來**。它整條重建 timeline，字卡／素材／音效／BGM
的軌全部消失。改了 cuts.json 就是回 Step 1。

碰 Resolve 的指令一律 `E:\nakama\.venv-v2\Scripts\python.exe`（3.12.10）——Resolve 21.0.3 的
`fusionscript.dll` 是 cp312，**3.10 與 3.14 都會在 import 當下 ACCESS_VIOLATION 崩潰**
（2026-09-03 實測；下文殘留的 `py -3.10` 一律以此為準）。本線碰 Resolve 的是
`run_short_tighten` / `run_shortform_director` / `run_short_sfx` / `run_short_bgm` / `run_short_review`。

---

## Step 1 — 緊湊化

```bash
py -3.10 scripts/run_short_tighten.py <episode> --id <cid> --detect
```

複審 `highlights/tighten/<cid>_cuts.json` 的 `keep=null`，然後：

```bash
py -3.10 scripts/run_short_tighten.py <episode> --id <cid> --apply
```

短片開頭的「那、那」口吃**絕不能出現**，中間停頓／贅詞也要剪，jump cut 越緊越好。

`--detect` 產六類候選。`noise` 由規則自動判（`_judge_noise`），其餘留 `keep=null`
給人審：

| kind | 判準 |
|---|---|
| `pause` | 靜音 ≥ `MIN_PAUSE`，自動 |
| `filler` | 「那／啊／喔」拖 ≥0.4s。連接詞用法照剪，但要確認剪掉後字幕仍通順 |
| `stutter` | 真口吃（那那／他他）剪第一個。APP 拼字／數字／疊詞誤報標 `keep=false` |
| `backchannel` | 整句附和（對／嗯／沒錯）。**緊貼前後語流無縫隙 = 重疊型附和，剪了會斬到來賓語音**，`keep=false`；有獨立空檔才整刀剪 |
| `redundant` | **ASR 有、校正稿沒有的字**——校正稿就是判決書 |
| `noise` | **ASR 沒有字、校正稿也沒有字的一段聲音**（咳嗽、「呃」「這個」） |

### 贅字與贅音的判準（2026-08-30 定案）

判「這是不是贅字」**不需要字表也不需要猜**：subtitle-correct 那一關已經有人／
模型逐句看過，把口吃、遲疑、贅詞從文字裡拿掉了，音檔還留著。那份判決直接拿來用。

- `redundant` = ASR↔校正稿逐字比對的 `delete`。只收 `delete`——`replace` 是 ASR
  聽錯／正規化（「十萬人」→「10萬人」、「小死皮」→「小比例」），音檔那裡有正常
  語音，剪了會斷字。cue 邊界溢出（詞尾壓到下一句第一個字）另外排除。
- `noise` = 詞與詞之間**有聲音**的段落。三個否決：已被別的刀蓋住／該 cue 的
  校正稿有 ASR 缺的字（那個字可能就落在這段裡）／峰值 < −10dB（換氣）。

> ⚠️ **複審時一定要看 `enclosed_by`。** WhisperX 常把一個字對成 1–2 秒（本集實測
> 最長 2.30s）。候選落在那種詞的中間時，「結束於候選之前／開始於候選之後」的
> 前後文會**把那個字整個藏起來**，看起來像 ASR 漏字、候選是語音。2026-08-30
> 我就是這樣把修修親耳聽到的「呃，這個」判成語音，同一個誤判在兩支短片發生 6 次。
> 現在候選自帶 `enclosed_by` 與 `context`，別再只看前後文。

### 手動刀

假起手（「裡面就是裡面裡面他他」）雙字詞重複偵測不到，人工掃頭 10 秒的 cue 文字，
用 `{"kind":"manual","t0","t1","strip_text"}` 下刀（`strip_text` = 同步從字幕刪除的
字串；**寫成 `null` 就是只剪聲音、不動文字**）。

片語重複（「而且是高階的、高階的白領工作」）剪其中一份即可——**機械偵測會把
另一份也標出來，兩份都剪整句就沒了**。手動刀的 `note` 要寫清楚剪的是哪一份。

`--detect` 重跑會保留 manual 刀與**機械類的人審結論**（同一個 `(kind, t0, t1)`
指的是同一段音檔，判斷仍成立）；`noise` 例外，規則是權威，舊結論不沿用。

## Step 2 — 導播

```bash
py -3.10 scripts/run_shortform_director.py <episode> --id <cid> [--stills <dir>] [--no-opener]
```

產出 `短N - <標題>（緊·導播）`：機位切換、反應鏡頭、punch zoom、開場上下分割，
並**重出子句級 tight SRT**（`fine = not covers_full_transcript`）與
`<cid>_zoom.resolved.json`、materialization receipt。

- 誰講話切誰的機位（`1_CAMERA 1.mp4`＝修修、`2_CAMERA 2.mp4`＝來賓）；<1s 的附和不切鏡
- **punch zoom 寫在逐字稿座標上，不寫秒數**：`cue`（哪一句）／`phrase`（該 cue 的
  **句首**，用來確認指對句子）／`until_cue`（放掉的那一句，落在句尾）／`steps`。
  手寫秒數會落在句子中間——修修 2026-08-30 抓到 `t1=12.53` 停在下一句開頭 0.67s 處、
  `t0=32.78` 打在前一句第 14 個字。ramp 自動提前 **0.5s** 起跳（`PUNCH_LEAD_SEC`）：
  「要在他講那一句話前 0.5 秒就 zoom in，觀眾才會覺得等一下那句話很重要」
- `"style":"ramp"`（預設）＝0.25s smootherstep +25%，不過衝回彈；`"cut"`＝1 frame 硬切
- 同一句內放掉再進是硬錯誤（「為什麼要拉遠又拉近」），script 會擋

**換集校準**：機位固定但**換集必校**——抓各機位一幀量臉部中心 x，寫
`highlights/tighten/director.json` 覆蓋 `face_x`；先跑一支 `--stills` 看樣張。

## Step 3 — 字卡企劃

```bash
py -3.10 scripts/author_shortform_titles.py <episode> --id <cid>
```

讀 `<cid>_titles.plan.json`（論證骨架：哪幾句歸同一個 beat、哪一句升級成
emphasis——**創意判斷，agent 依 shortform-director 手冊寫**）展開成
`<cid>_titles.json`。機械自檢：逐 cue 恰好覆蓋一次、斷行排得下、強調預算
≤¼、tier1 段數 1–3、單段 ≤11.8s。

排不下**不是換樣式，是上游沒拆句**——回 Step 2 讓 `_split_long_cues` 拆。
2026-08-30 修修看到的三行橘底卡就是舊版排不下時偷偷降級成 `role="hybrid"` 生的，
那個 fallback 已經拿掉。

斷行走 `shared/zh_linebreak.py`（與字幕同一套乾淨切點判準 ＋ 語法偏好），
不要自己 jieba 取最均衡——最均衡的切點常常正是最爛的那個。

## Step 4 — 字卡

```bash
py -3.10 scripts/run_shortform_titles.py <episode> --id <cid> --validate-only
```

過了才實跑（拿掉 `--validate-only`）。kinetic sequence 疊上 video track 3。

## Step 5 — 素材（B-roll ＋ 開場 LOGO）

```bash
py -3.10 scripts/run_shortform_broll.py <episode> --id <cid> --validate-only
```

過了才實跑（拿掉 `--validate-only`，可加 `--stills <dir>`）。

意圖層 `<cid>_broll.json` 由 **shortform-director** 決定落點、**shortform-dp**
找片回填 slug。gate（`shared/shortform_broll.py`）驗四件事：

1. **授權**：`assets/broll/<slug>.acquisition.json` ＋ 檔案 SHA-256 對得上
2. **直式**：`height > width`
3. **落點對齊那句話**：`source_cues` 宣告對哪幾句，t0/t1 必須包在那幾句的時間裡（容差 0.35s）
4. **不衝突**：不蓋 punch 區間、不壓開場上下分割

全片 **2–3 支**，每支 1.5–4s。密度用**具象覆蓋率**算，不用事件／分——mode B 之後
字卡逐句出現，事件密度永遠自動達標、失去意義。具象句（有可拍的名詞／動作）配畫面，
抽象句一律留 talking head。

**時間軸一動，落點要重排**：Step 1 多剪 2 秒，`source_cues` 的絕對時間就跟著移，
gate 會擋。回頭改 `<cid>_broll.json` 的 t0/t1，不要調 gate。

開場 LOGO 是 structural item（`{"kind":"badge","slug":"brand-logo-opener"}`），
**不需要授權收據**（自家品牌資產）。先產 badge：

```bash
py -3.10 scripts/build_brand_logo_badge.py <episode> --source "<...>_alpha_prores4444.mov" --width 440 --seam-offset 30
```

用 deliverables 的 `*_alpha_prores4444.mov`（透明主檔），不要 `*_preview_*.mp4`
（MP4 不支援透明）。底邊貼在接縫上方，不要跨接縫——下半格主持人的臉幾乎從接縫
就開始（耳機頂端約 y=980）。

## Step 6 — 音效

```bash
py -3.10 scripts/run_short_sfx.py <episode> --id <cid>
```

讀 titles／zoom.resolved／broll → audio **track 2**（track 1 對白絕不碰）。落點決定性、
零人工：tier1 卡＝ding、ramp punch＝riser（t0−0.35）、cut punch＝impact、tier2 卡＝swish。

- **B-roll 切出不配音效**——畫面切換本身就是訊號
- **語意音效層停用**（情緒音效容易用錯場合）；環境音（跟素材走的 diegetic 音）仍可用
- 間距 <1.2s 只留優先級高的
- 響度烘焙在素材端（`assets/sfx/*.wav`），不靠 Resolve clip gain——重跑才可重現

## Step 7 — 音樂

```bash
py -3.10 scripts/run_short_bgm.py <episode> --id <cid> --track <assets/bgm 的檔名，不含副檔名>
```

audio **track 4**（1 對白／2 SFX／3 環境／4 BGM）。烘焙到 **−43 LUFS**（比對白低
28 dB）——「感覺得到、聽不出來」，這個差距不需要 ducking。頭尾 fade、裁到片長、
短於片長自動循環，全部烘在檔案端。

選曲跟著**這支的內容**走，不是看 id 前綴——`punch-S07`（天堂裡的人想來人間
受苦）是沉思不是明快，配的是 night-sky 不是 all-good-folks。新曲用
`scripts/stage_bgm_track.py` 進 `assets/bgm/`，會一併寫 acquisition receipt——
**`source_url` 不知道就寫 `null`，不要從檔名編一個出來**。

## Step 8 — 自檢 loop

```bash
py -3.10 scripts/run_short_review.py <episode> --id <cid>
```

出 `highlights/review/<cid>/`：540×960 preview、縮圖牆、逐事件抽幀、events.json。

1. 自己先看 contact sheet 與 events.json（節拍器缺口、遮臉、裁切）
2. dispatch 盲審 subagent：packet 路徑 ＋ 八項 checklist（鋪滿／貼合／時長／遮臉／
   裁切感／節奏／字幕／其他異常），輸出 findings JSON
3. high/medium 必修 → 重跑受影響 step → 重出 packet 再審。**收斂條件：無 high/medium**
4. 修完的教訓寫回本冊或 code，不要留在對話裡

**修完任何 JSON／素材都要再跑一次**——首航就抓到 titles 清場誤殺整條貼紙層。

---

## 軌道契約

| 軌 | 內容 |
|---|---|
| video 1 | 主鏡（機位） |
| video 2 | 開場第二機 ＋ B-roll |
| video 3 | 字卡（kinetic sequence） |
| video 5 | 開場品牌 LOGO badge |
| audio 1 | Master 對白 |
| audio 2 | SFX |
| audio 3 | 環境音 |
| audio 4 | BGM |

## 已知地雷

- **Resolve 回 `[None]` 不一定是「未就緒」**：軌不存在時也回 `[None]`，重試幾次都一樣。
  `data/` 在 .gitignore，worktree 裡沒有字幕樣式模板 → `CreateEmptyTimeline` 只給
  1 條視訊軌 → 開場上半格要的 track 2 不存在。所有上軌前都要
  `while GetTrackCount < N: AddTrack`。
- **不給 `trackIndex` 就跟著 auto track selector 走**，那個狀態看不見也不可控。
- **in-point 退格／out-point 少一格**（H.264 長 GOP 解碼邊界）：長度錯一格＝後面
  所有畫面相對聲音位移一格且會累積。`_append_cam` 用退掉重接收斂到長度正確。
- **`--apply` 的 SRT 是 `fine=True` 的呼吸單元**，不是顯示層；子句級 SRT 由 Step 2
  導播重出。看到 8 字固定寬的 cue 就是還沒跑導播。
- **企劃腳本不要留在 scratchpad**：2026-08-30 實測把 punch-S02 的 20 句企劃
  用舊的一次性腳本蓋成 31 句。規格進 episode、工具進 repo。

## 換段／改稿

改 `winners.short.json` → 重跑物化。改 cuts.json → 回 Step 1 `--apply`，然後
**Step 2 之後全部重跑**（導播重建 timeline 會洗掉上層軌）。
