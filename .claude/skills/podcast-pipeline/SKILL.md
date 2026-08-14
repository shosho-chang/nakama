---
name: podcast-pipeline
description: >
  訪談集全產線編排：episode 資料夾一路走完 audio-prep → subtitle-gen →
  subtitle-correct → resolve-project → highlight-cut → packaging
  （標題×封面 → gate），段間停下給使用者確認中間產物。Use when the user
  points at a footage episode folder (e.g. G:\footages\20260723 謝伯讓)
  and says 「跑字幕產線」「整條跑完」「podcast pipeline」「幫我把這集的
  字幕做出來」「一路跑到 gate」, or wants to resume a half-done episode
  （自動偵測進度）。
---

# podcast-pipeline — 訪談集全產線編排

薄編排：偵測 episode 進度 → 依序呼叫各段 skill。**不要重新發明各段邏輯**，
一律進入對應 skill（`/audio-prep`、`/subtitle-gen`、`/subtitle-correct`、
`/resolve-project`、`/highlight-cut`、`title-brainstorm --batch`、
`/thumbnail-brainstorm`）照它的手冊做。

## 進度偵測（依檔案存在判斷）

| 檔案 | 意義 |
|---|---|
| `prep_manifest.json` + `normalized.wav` | prep 完成 → 下一步 subtitle-gen |
| `subs/gen_manifest.json` + `subs/raw.srt` | gen 完成 → 下一步 subtitle-correct |
| `subs/correct_manifest.json` + `transcript.srt` | correct 完成 → 說話者切分 → resolve-project（需 Resolve 開著）|
| `transcript_prose.md` | 人讀逐字稿已產（字幕定稿的副產物；缺就補跑，見下節）|
| Resolve 內已有同名 project | **resolve 段完成**（非全部完成）→ 回報 QC 摘要；QC 裁決後 `--refresh-subtitles`；下一步 highlight-cut |
| `highlights/candidates.json` | 選段開採完成（mining）→ 續 highlight-cut 評審段 |
| `highlights/選段候選表.md`（無 winners.json） | 盲審排完、**卡在選段 gate** → 把表貼給修修等他挑（見下方 HITL 第 5 條）|
| `highlights/winners.json` + `highlights/選段企劃-*.md` | highlight-cut 完成 → 下一步 packaging |
| `packaging/manifest.json` | packaging 進行中/完成 — 用 `python scripts/packaging_manifest.py status` 判斷該續哪支（見下節），全完成 → 去 gate review |

都沒有 → 從 audio-prep 開始。

**說話者切分**（correct 之後、上 Resolve 之前）：episode 有分軌 mic
（Audio/ 內兩軌以上人聲）就跑 `python scripts/run_speaker_split.py <episode>`——
混人 cue 在說話者變更處切開（校正文字不動、冪等可重跑）。

**補洞**（切分之後）：`python scripts/run_gap_fill.py <episode>`——偵測
無字幕區段（>3s）重聽補回（典型成因：幻覺 cue 刪除後底下的真實對話沒補；
重聽結果有快取，重跑免 GPU）。

**QC 裁決後的完整重跑鏈**（冪等）：改 corrections.json → `--apply` →
speaker split → gap fill → `--refresh-subtitles`。⚠️ `--apply` 會重生
transcript.qc.md（見 subtitle-correct skill 的批註保護警告）。

**人讀逐字稿**（字幕定稿後，與剪輯線並行、互不阻擋）——兩段：

1. `python scripts/run_transcript_prose.py <episode> --guest <來賓姓名>
   [--outtakes-from 1:01:36]`——去時間戳、一問一答分段的完整訪談稿，落
   episode 內 `transcript_prose.md` 與 vault `KB/Raw/Podcasts/{slug}.md`。
   - **`--guest` 一定要給**（機器不猜姓名）；輸出的 `first_line` 是給使用者
     一眼驗軌序用的，兩位講者對調就補 `--swap` 重跑
   - `--outtakes-from` 給**訪談結束**的時間碼（在逐字稿裡找致謝收尾那句），
     之後的收工閒聊另存 `transcript_outtakes.md`——那段兩人搶話、講者不可靠
   - `transcript_prose_suspect.json` 是判定可疑的 cue 清單（兩軌 mic 能量差
     <7dB），交付前掃一眼

2. **語意標點 pass**——把「停頓標點」修成「語意標點」（實例：「反芻了過去好的
   事情，我們要延續不好的事情」意思是反的，應為「反芻了過去，好的事情我們要
   延續，不好的事情…」）：

   ```
   python scripts/run_transcript_punctuate.py emit <episode>
   〔派 subagent 逐塊改標點 → punct_work/NNN.done.md〕
   python scripts/run_transcript_punctuate.py apply <episode> --guest <來賓> --slug <slug>
   ```

   派 subagent 時**鐵則要寫進 prompt**：一個字都不准改（不加不刪、不換同義詞、
   不刪贅詞、不修錯字）、話不可以換人講、只能搬標點與切同一人的長段落。
   `apply` 會逐塊機械驗證（去標點後逐字比對 + 逐 turn 比對），**任何一塊沒過
   就整批不寫檔**並列出原因，不會靜默跳過。

**斷句全片掃描**（選配，使用者反映斷句差時）：派 subagent 分 chunk 掃
transcript.srt 的壞邊界（數量詞/「的」結構/專名被拆）→ 產出
`highlights/line_moves_*.json` → `python scripts/run_line_polish.py <episode>`
機械套用（防護：22 字上限/括號完整/不跨說話者搬字）→ 主 timeline
`--refresh-subtitles` + 精華 timeline `run_highlight_cut.py --refresh-subs`
（後者只換字幕不動剪輯）。

## Packaging 末段（winners.json 就緒後；ADR-054 D14/D16）

執行權在本段（D16① — highlight-cut Step 4 已降為規則指標，不重跑）。逐支處理，
帳本走 `scripts/packaging_manifest.py`（唯一 manifest 寫入口，skill 不手改 JSON）：

1. **開跑前**：`python scripts/packaging_manifest.py status "<ep>/packaging"` —
   有 `next` 就從那支那個 stage 續（**已完成 stage 的產物不重生**）
2. **每支長片**（winners.json 的 long 當選，依 rank 序）：
   - `titles`：進 `title-brainstorm` skill 跑 `--batch <packaging_dir>`（完整 7 步，
     深度不可簡化 — D13）→ 成功後 `mark --cut <id> --stage titles`
   - **標題 gate（修修 2026-08-14 裁決，必停）**：titles emit 完 → 把 Top 5 貼給修修，
     等他挑／改字才進封面段。理由同選段 gate——**HITL 放在成本最低的分叉點**；
     標題沒定就做封面，等於用已經做完的封面逼他接受標題（鄭國威集實例）。
     他也可以直接在 gate 頁的標題輸入框改字（`/bridge/packaging/<slug>/title`）。
   - `thumbnails`：進 `/thumbnail-brainstorm`（joint pairing → cutout → render 3 PNG
     ＋**變體板**（3 表情對 × 2 大字，Step 4.6）→ attach_packages 回填+雙落點）
     → `mark --cut <id> --stage thumbnails`
   - `emitted`：確認該 cut 在 **vault 端** `Attachments/packaging/<slug>/packages.json`
     內 packages 滿 3 且 PNG 檔在 → `mark --cut <id> --stage emitted`
3. **短片**（winners.json 的 short 當選）：title-brainstorm `--batch` 內建 LLM 直出
   （不跑 panel — D4），emit 後同樣三段 mark
4. **任何 stage 失敗 → 停該支該段**，照該 skill 錯誤處理節排除後重跑（manifest 會
   自動跳過已完成的）；**禁止跳段續跑**（mark 會擋跳序）
5. **交付訊息前**：實際 `ls` vault `Attachments/packaging/<slug>/` 確認 packages.json
   + PNG 都在（Syncthing 有延遲，working set 在不代表 vault 在）→ 停下告知
   「去 gate review：https://nakama.shosho.tw/bridge/packaging/<slug>（本機 dev =
   http://127.0.0.1:8765/bridge/packaging/<slug>）」

推導鏈逐支落地（`title_trace.json` 每支寫完即存）— 第 5 支掛掉時前 4 支不蒸發。

## 段間 HITL（每段完成必停）

1. **prep 後**：回報裁切秒數；異常大（>60s）請使用者抽聽頭尾
2. **gen 前**：確認 `refs/` 放好了（訪綱/報告/完整稿）；確認 GPU 注意事項（見 subtitle-gen skill）
3. **gen 後**：抽 SRT 開頭幾個 cue 給使用者掃一眼再進校正
4. **correct 後**：完整呈現 `transcript.qc.md` 的「需人工確認」清單——這是字幕的最終 HITL gate
5. **選段盲審後（必停，修修 2026-08-11 裁決）**：跑
   `python scripts/run_cut_shortlist.py <episode> --format long` 出候選表貼給修修，
   **等他指定 id** 才 `--pick` 寫 winners.json 進製作。理由：panel 讀逐字稿評的是素材強度，
   不是成片吸引力也不是他的品味；做完才發現主題不吸引人，製作＋packaging 的成本已經付掉
   （安吉集實例）。詳見 highlight-cut skill Step 2.4
6. **標題 emit 後（必停）**：Top 5 貼給修修挑／改字，定案才進封面段（見上節）
7. **packaging 後**：告知 gate URL 即停 — approve、挑封面變體、打封面大字都在
   Web UI 做，不在對話裡代決。修修若在 gate 打了 `bigtext_request`，下次跑
   thumbnail-brainstorm 要讀它重出變體

## 原則

- **成本紅線：整條產線預設零 API 錢**——校正走 subtitle-correct 的 subagent
  模式（subscription quota）；`--api` / `--arbitrate` 付費路徑只在修修明確要求時用
- 長任務（prep 上傳、subagent 校正）放背景跑，完成再回報
- 任何一段失敗 → 停在該段照該 skill 的錯誤處理節排除，不要跳段
- 使用者只丟資料夾沒說從哪開始 → 先報偵測到的進度再續跑
