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

## 完成後回報

1. 讀 `transcript.qc.md`：回報統計（修正行數、QC 行數／低對齊 cue 數）
2. **把「需人工確認」清單完整呈現給使用者**（這是 HITL gate，不可略過；
   subagent 模式沒有 Gemini 聽音檔，uncertain 全數靠修修裁決）
3. 修修裁決後可把採納的項目補進 corrections.json 重跑 `--apply`（冪等）
