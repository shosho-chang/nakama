---
name: subtitle-correct
description: >
  字幕校正：episode 的 subs/raw.srt（或任何外部 AI 工具產的 SRT）+ refs/
  參考資料 → transcript.srt + transcript.qc.md 校正報告。兩種模式自動選擇：
  refs 有完整逐字稿（script.* / 逐字稿*）→ difflib 對稿（零 LLM）；只有訪綱/
  報告 → 派 Opus subagent 分段校正（subscription quota，零 API 錢）。
  Use when the user says 「校正字幕」「subtitle-correct」「校正這份 SRT」
  「用訪綱/逐字稿修字幕」, or after subtitle-gen completes in the podcast
  pipeline.
---

# subtitle-correct — 字幕校正

`scripts/run_subtitle_correct.py`（repo：`E:\nakama`）的互動包裝。
核心在 `shared/subtitle_correct.py`。**成本原則：一切預設跑在 subscription
quota 內（subagent），付費 API 是明確 opt-in——不要主動建議 API 路徑。**

## 模式（自動選擇）

| | scripted（有完整稿） | subagent（訪談型，預設） |
|---|---|---|
| 觸發 | `refs/` 有 `script.*` / `逐字稿*` / `腳本*` | 其他情況 |
| 機制 | difflib 字元流對齊：文字以稿為準、時間軸/cue 切分保留 | 切 chunk → **派 Opus subagent 並行校正** → 機械套用 |
| 成本 | 零 | subscription quota（零 API 錢） |
| 適用 | 照稿錄的 YouTube 影片 | Podcast 訪談（參考有限） |

外部工具（如 MemoAI）產的 SRT 也能校：`--srt <path>` 直接指定。

## 參考資料來源（自動）

`discover_ref_files` 自動合併：`<episode>/refs/` + 訪談準備資料夾
（`INTERVIEW_PREP_DIR`，依來賓名配對，episode `20260723 謝伯讓` ↔
prep `2026-07-22-謝伯讓`）。執行前把找到的清單回報給使用者掃一眼——
**參考資料品質直接決定校正品質**。

## scripted 模式（有完整稿）

```
python E:\nakama\scripts\run_subtitle_correct.py "<episode>"
```
直接跑完。整體對齊率 < 50% 時警告使用者：稿與錄音差異大。

## subagent 模式（訪談型）— 三步驟

**① 切工作區**
```
python E:\nakama\scripts\run_subtitle_correct.py "<episode>" --emit-chunks --host-name "張修修" --show-name "<節目名>"
```
產出 `subs/correct_work/`：`instructions.md`（校正規則 + 參考資料清單 +
輸出契約）、`chunk_NN.txt`（`[seq] 文字 (拼音)`）、`meta.json`。

**② 派 subagent 並行校正**——每個 chunk 一個 subagent（**model: opus**；
一次全部並行發出）。每個 subagent 的 prompt：

> Read `<workdir>/instructions.md`，依其指示 Read 所有參考資料檔，然後
> Read `<workdir>/chunk_NN.txt` 逐行校正。你的最終回覆**只能是** JSON：
> `{"corrections": {"<seq>": "校正後文字"}, "uncertain": [{"line": <seq>,
> "original": "...", "suggestion": "...", "reason": "...", "risk": "high|medium|low"}]}`
> corrections 只含有修改的行；seq 必須落在本 chunk 範圍（見 meta.json）。

收齊後把所有 chunk 的 corrections / uncertain **合併成一個 JSON** 寫到
`<workdir>/corrections.json`（key 衝突不應發生——每個 chunk 的 seq 範圍不重疊，
若真衝突以較後 chunk 為準並記下來回報）。

**③ 機械套用**
```
python E:\nakama\scripts\run_subtitle_correct.py "<episode>" --apply "<workdir>/corrections.json"
```
script 會做：越界過濾、過度刪減防護（縮短逾半 → 進 QC）、Pass 2 標點/簡體
過濾、產 `transcript.srt` + `transcript.qc.md` + manifest。

## 付費 API 路徑（明確 opt-in，不要主動用）

`--api`（Anthropic Opus 直呼，花 API 錢）、`--arbitrate`（Gemini 聽音檔仲裁，
再花 API 錢）。只在修修**明確要求**（例如無人值守批次、或指名要 Gemini 仲裁）
時使用，用前提醒一句成本。

## 字幕 house style（修修 2026-07-25 裁決）

- **書名／作品名必須《》標出**、**專有名詞／術語必須「」標出**——校正時主動補上
- 其他標點（逗號句號等）省略，停頓用半形空格
- 這些規則已寫進 instructions.md 的校正 prompt 與 Pass 2 過濾（《》「」不會被清掉），
  subagent 只要照 instructions 做即可

## QC 自主裁決（修修 2026-07-25 裁示）

**不要把整份 QC 清單丟給修修拍板**——「要我拍板的地方實在太多，我沒那麼多時間」。
QC 的 uncertain 項目由你自己裁決完，流程：

1. **「聽音檔」= 重開 WhisperX** — 用 `scripts/run_subtitle_relisten.py`：

   ```
   py -3.10 scripts/run_subtitle_relisten.py "<episode>"                  # 預設掃 qc.md 的 HIGH
   py -3.10 scripts/run_subtitle_relisten.py "<episode>" --risk medium    # 再掃 MEDIUM
   ```

   對該 cue 裁出 ±1 cue 的音檔片段（從 normalized.wav），**明確不給
   initial_prompt** 重辨識（帶原 prompt 重跑只會重現同一個錯），比對
   「原文 / 建議 / 重聽」三方，落 `subs/relisten.json`。⚠️ 行號吃 raw.srt
   序號——transcript.srt 經 speaker split / gap fill 後序號已位移。
   重聽項目多時把 relisten.json 交給 subagent 依裁決規則批次判讀成 delta。
   重疊說話（重聽時句子消失）→ 改裁分軌 mic 軌（stem 比 mix 早約 0.167s，
   `speaker_assign._measure_offset` 可量）
2. **重聽支持建議 → 直接改**；**重聽兩次仍是原文 → 保留原文**（講者口誤照實
   保留，忠實優先）；語意判斷確鑿（如「智慧家」非詞 → 哲學家）→ 直接改
3. **人名地名 → 派 agent 查證**（refs / web），不要標「請確認」
4. 全部裁決寫進 `subs/qc_decisions.md`（決定 + 證據 + 可否決的 judgment call），
   更新 corrections.json 重跑 `--apply`（冪等）
5. **只把真正音義衝突、多次重聽仍無法判定的極少數（目標 <5 項）升級給修修**

⚠️ `--apply` 會**重生 transcript.qc.md**——修修若在裡面用 `[]` 批註中，先把批註
原文抄進 qc_decisions.md 再跑，否則會被蓋掉（2026-07-25 血淚）。

## 完成後回報

1. 讀 `transcript.qc.md` 統計 + 你的 `subs/qc_decisions.md` 裁決摘要
2. 只呈現升級清單（真無法判定項）與可否決的 judgment call
3. 修修否決任何一項 → 改 corrections.json → `--apply` → speaker split → refresh（冪等）
