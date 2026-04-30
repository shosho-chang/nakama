---
name: Whisper hallucination 防禦三件套 + initial_prompt 不用 label
description: faster-whisper / WhisperX 在低 SNR / silence 段會 echo initial_prompt 的 substring；用三 ASR option + 純詞表 prompt 防
type: feedback
created: 2026-04-30
---

**規則：所有 Whisper 系列（faster-whisper / WhisperX / openai-whisper）transcribe 都要在 `asr_options` / `decode_options` 設這三件，並且 `initial_prompt` **不用 label 結構**。**

```python
asr_options = {
    "condition_on_previous_text": False,
    "compression_ratio_threshold": 2.4,
    "no_speech_threshold": 0.6,
    "initial_prompt": "詞A、詞B、詞C",   # 純頓號分隔，不用「主持人：X」label
}
```

**Why:** 2026-04-30 PR #271 swap WhisperX 後跑 76min 訪談，10 個 cue 在低 SNR / silence 段輸出「主持人 張修修」吃掉 ~110 秒真實 audio（cue 70/450/451/452/628/803/804/805/869/1028）。根因：`_build_initial_prompt` 用 `f"主持人：{host_name}"` label 結構，Whisper 把 prompt 的 substring 整段 echo 回來——經典 Whisper hallucination。

**How to apply:**

### 三件 ASR option

| Option | 預設值 | 為何要設 |
|---|---|---|
| `condition_on_previous_text=False` | True | 上一 segment 的錯誤 / hallucination 不會 propagate 到下一 segment |
| `compression_ratio_threshold=2.4` | 2.4（faster-whisper default 已是；明寫提醒） | text 重複度過高（壓縮比 > threshold）= hallucination 信號，丟棄 segment |
| `no_speech_threshold=0.6` | 0.6（同上） | silence 段更積極跳過，避免 model 在無語音段亂編 |

### `initial_prompt` 結構

| ❌ 不要 | ✅ 改成 | 為何 |
|---|---|---|
| `"節目：X。主持人：Y。來賓：Z。"` | `"X、Y、Z"` 純頓號分隔詞表 | label 結構在低 SNR 段被當成輸出 echo（observed 10 處 cue / ~110s），純詞表 Whisper 仍可 bias vocabulary 但不會 echo 整段 |
| `f"專名：{names}"` | inline 進詞表 | 同上 |

### 適用範圍

- nakama: `shared/transcriber.py` `_get_asr_model` / `_build_initial_prompt`（PR #274 已落地）
- 任何用 faster-whisper / WhisperX / openai-whisper 的下游專案
- 不適用於 paraformer-zh 等非 Whisper 系（FunASR 走 hotwords API 不會 echo）

### 對應 test

`test_get_asr_model_passes_anti_hallucination_options` + `test_build_initial_prompt_no_label_hallucination_pattern`（regression gate）。
