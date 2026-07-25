---
name: podcast-pipeline
description: >
  字幕產線編排：episode 資料夾一路走完 audio-prep → subtitle-gen →
  subtitle-correct，段間停下給使用者確認中間產物。Use when the user
  points at a footage episode folder (e.g. G:\footages\20260723 謝伯讓)
  and says 「跑字幕產線」「整條跑完」「podcast pipeline」「幫我把這集的
  字幕做出來」, or wants to resume a half-done episode（自動偵測進度）。
---

# podcast-pipeline — 字幕產線編排

薄編排：偵測 episode 進度 → 依序呼叫三個 skill。**不要重新發明各段邏輯**，
一律進入對應 skill（`/audio-prep`、`/subtitle-gen`、`/subtitle-correct`、
`/resolve-project`）照它的手冊做。

## 進度偵測（依檔案存在判斷）

| 檔案 | 意義 |
|---|---|
| `prep_manifest.json` + `normalized.wav` | prep 完成 → 下一步 subtitle-gen |
| `subs/gen_manifest.json` + `subs/raw.srt` | gen 完成 → 下一步 subtitle-correct |
| `subs/correct_manifest.json` + `transcript.srt` | correct 完成 → 下一步 resolve-project（需 Resolve 開著）|
| Resolve 內已有同名 project | 全部完成 → 回報 QC 摘要即可；QC 裁決後用 `--refresh-subtitles` 更新字幕軌 |

都沒有 → 從 audio-prep 開始。

## 段間 HITL（每段完成必停）

1. **prep 後**：回報裁切秒數；異常大（>60s）請使用者抽聽頭尾
2. **gen 前**：確認 `refs/` 放好了（訪綱/報告/完整稿）；確認 GPU 注意事項（見 subtitle-gen skill）
3. **gen 後**：抽 SRT 開頭幾個 cue 給使用者掃一眼再進校正
4. **correct 後**：完整呈現 `transcript.qc.md` 的「需人工確認」清單——這是最終 HITL gate

## 原則

- **成本紅線：整條產線預設零 API 錢**——校正走 subtitle-correct 的 subagent
  模式（subscription quota）；`--api` / `--arbitrate` 付費路徑只在修修明確要求時用
- 長任務（prep 上傳、subagent 校正）放背景跑，完成再回報
- 任何一段失敗 → 停在該段照該 skill 的錯誤處理節排除，不要跳段
- 使用者只丟資料夾沒說從哪開始 → 先報偵測到的進度再續跑
