---
name: 設計 rationale 寫之前要實際 trace pipeline
description: 在 commit message / PR description / 註解寫「保留 X 是為了 Y」時，必須實際 trace pipeline 順序確認 X 真的有貢獻給 Y，不能靠直覺
type: feedback
---

寫設計 rationale（commit message、PR 描述、code 註解）時，「保留 X 是為了 Y」這類因果陳述要實際讀 code 確認 X 真的能影響 Y。不要靠對 pipeline 的「印象」就下筆。

**Why:** PR #23 我 commit message 寫「保留 FunASR `punc_model=ct-punc-c` 讓 LLM 校正階段仍有斷句參考」。Code review 抓到這個矛盾後我才驗證 — Pass 1 `_process_srt_line` 在 `_correct_with_llm` 之**前**跑（`transcribe()` L820 vs L827），LLM 永遠看不到標點。`punc_model` 確實有用，但實際作用是給 `_funasr_to_srt` 句尾標點做**字級時間戳對齊**（`_split_sentences` L631「先用句尾標點拆分」），完全不關 LLM 的事。我寫的 rationale 是直覺猜測，不是實際 trace 結果。

review 修正過程：先以為要拿掉 `punc_model`，差點動破壞時間軸對齊的 code → 強迫自己讀 `_funasr_to_srt` 才搞清楚真正用途。

**How to apply:**
1. 任何「保留/移除 X 是為了 Y」的 rationale 寫進 commit/PR 之前，至少 trace 一次：X 的輸出流到哪裡？Y 是 X 的下游嗎？中間有沒有 transformation 把 X 抹掉？
2. Pipeline 順序問題特別容易出錯 — 函式 A 呼叫 B 不代表 B 在 A 之前執行，要看 caller 順序
3. Code review 提出 rationale 矛盾時，**反問自己是否真懂這段 code**，先 trace 再決定 reviewer 對錯。可能 reviewer 點出的不是 code bug，而是我的理解 bug
4. 若 trace 後發現 rationale 寫錯但 code 是對的（如 PR #23），squash merge 時把 commit message 改正，別讓錯的 rationale 進 git history
