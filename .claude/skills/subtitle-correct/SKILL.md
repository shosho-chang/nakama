---
name: subtitle-correct
description: >
  字幕校正：episode 的 subs/raw.srt（或任何外部 AI 工具產的 SRT）+ refs/
  參考資料 → transcript.srt + transcript.qc.md 校正報告。兩種模式自動選擇：
  refs 有完整逐字稿（script.* / 逐字稿*）→ difflib 對稿（零 LLM）；只有訪綱/
  報告 → Opus 分段校正 + Gemini 聽音檔仲裁。Use when the user says
  「校正字幕」「subtitle-correct」「校正這份 SRT」「用訪綱/逐字稿修字幕」,
  or after subtitle-gen completes in the podcast pipeline.
---

# subtitle-correct — 字幕校正

`scripts/run_subtitle_correct.py`（repo：`E:\nakama`）的互動包裝。
核心在 `shared/subtitle_correct.py`，與 `/transcribe` 共用校正 prompt 與仲裁機器。

## 模式（自動選擇，`--mode` 可覆寫）

| | scripted（有完整稿） | llm（訪談型） |
|---|---|---|
| 觸發 | `refs/` 有 `script.*` / `逐字稿*` / `腳本*` | 其他情況 |
| 機制 | difflib 字元流對齊：文字以稿為準、時間軸/cue 切分保留 | Opus 分段校正（每 chunk 150 cues）+ Gemini 聽音檔仲裁 |
| 成本 | 零 | 60 分鐘訪談約數美元（Opus）+ 仲裁少量 |
| 適用 | 照稿錄的 YouTube 影片 | Podcast 訪談（參考有限） |

外部工具（如 MemoAI）產的 SRT 也能校：`--srt <path>` 直接指定。

## 參考資料來源（自動）

`discover_ref_files` 自動合併兩處的 `.md`/`.txt`：
1. `<episode>/refs/`
2. **訪談準備資料夾**（預設 `E:\Projects\張修修的AI創作者新世紀\訪談準備\`，
   `.env` `INTERVIEW_PREP_DIR` 可覆寫）— 依來賓名配對子資料夾
   （episode `20260723 謝伯讓` ↔ prep `2026-07-22-謝伯讓`）

## 執行前跟使用者確認

1. 回報自動找到的參考檔清單給使用者掃一眼——**參考資料品質直接決定校正品質**
2. llm 模式：要不要仲裁（預設開，需 `normalized.wav` 在場；`--no-arbitration` 關）
3. 來賓名／節目名（`--host-name` / `--show-name`，llm 模式的重要 hotword）

## 執行

```
python E:\nakama\scripts\run_subtitle_correct.py "<episode 資料夾>" ^
  --host-name "張修修" --show-name "<節目名>"
```

llm 模式長訪談 10–30 分鐘（Opus chunks + 仲裁逐段聽音檔）——背景跑。

## 完成後回報

1. 讀 `transcript.qc.md`：回報統計（修正行數、QC 行數／低對齊 cue 數、整體對齊率）
2. **把「需人工確認」清單完整呈現給使用者**（這是 HITL gate，不可略過）
3. scripted 模式整體對齊率 < 50% 時警告：稿與錄音差異大，建議改走 llm 模式或人工比對
