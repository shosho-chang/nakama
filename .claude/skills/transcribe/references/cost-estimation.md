# Cost & Time Estimation

Use these formulas in Step 5 to present a pre-run estimate. Numbers are
2026-04 snapshots; refresh when pricing or benchmarks change.

## Inputs

From Step 2 (ffprobe):
- `duration_min` — audio duration in minutes
- `file_size_mb` — file size in MB

From environment:
- `user_upload_mbps` — default 10 Mbps (common home upload speed).
  Override via env `TRANSCRIBE_UPLOAD_MBPS` if the user has documented
  their link. Do NOT ask every run — it's stable.

From Step 4 (toggles):
- `auphonic_on` / `opus_on` / `arbitration_on` — booleans

## Time Formula

```python
# Auphonic
if auphonic_on:
    upload_min = file_size_mb / (user_upload_mbps * 0.125 * 60)  # MB / (Mbps * 0.125 MBps * 60 s/min)
    auphonic_time_min = upload_min + 1.0     # +1 min server processing
else:
    auphonic_time_min = 0

# FunASR (GPU, very fast)
funasr_time_min = max(0.2, duration_min * 0.008)   # ~0.5s per 1min audio, floor 12s

# Opus correction (mostly API latency, scales sublinearly)
if opus_on:
    opus_time_min = max(1.0, duration_min * 0.08)  # ~75s per 20min audio
else:
    opus_time_min = 0

# Gemini arbitration (3 workers, ~70s per 10-13 clips)
if arbitration_on and opus_on:
    # estimate ~8-15 clips per 20min, scale linearly
    clips_estimate = duration_min * 0.6
    gemini_time_min = clips_estimate * 0.09  # ~70s / 13 clips ≈ 5.4s/clip
else:
    gemini_time_min = 0

total_time_min = auphonic_time_min + funasr_time_min + opus_time_min + gemini_time_min
```

Sanity check for 20 min Angie podcast, full pipeline:
- Auphonic: 344MB / (10 * 0.125 * 60) = ~4.6 min upload + 1 = ~5.6 min (measured: 19 min due to actual ~2 Mbps upload)
- FunASR: 0.16 min (measured: 11 s)
- Opus: 1.6 min (measured: 75 s)
- Gemini: 1.2 min (measured: 70 s for 13 clips)
- Total: ~8.6 min estimate, measured ~23 min (Auphonic upload dominates)

**If the user's measured Auphonic time exceeds the estimate by >2x**, suggest
updating `TRANSCRIBE_UPLOAD_MBPS` with actual upload speed.

## Cost Formula (USD)

```python
# Auphonic
auphonic_cost = 0.0    # free tier, assuming quota available

# FunASR
funasr_cost = 0.0      # local

# Opus correction (2026-04 pricing: $15/M input, $75/M output)
# Empirically ~$0.33/hr; rough proportional model:
if opus_on:
    opus_cost = duration_min / 60 * 0.33
else:
    opus_cost = 0.0

# Gemini 2.5 Pro arbitration with thinking_budget=512
# Input: text+audio ~0.002/clip; Output: thinking+JSON ~$0.02/clip (budget-limited)
# Empirically ~$0.3-0.5/hr audio
if arbitration_on and opus_on:
    gemini_cost = duration_min / 60 * 0.4
else:
    gemini_cost = 0.0

total_cost = opus_cost + gemini_cost  # Auphonic/FunASR are free
```

## Presentation Template

```
預估：
  時間：~{total_time_min:.0f} min
    Auphonic 上傳 {upload_min:.0f} min (assumed {user_upload_mbps} Mbps)
    FunASR {funasr_time_min*60:.0f} s
    Opus 校正 {opus_time_min:.0f} min
    Gemini 仲裁 {gemini_time_min:.0f} min
  成本：~${total_cost:.2f}
    Opus: ${opus_cost:.2f}
    Gemini: ${gemini_cost:.2f}
  輸出：{output_dir}/{audio_stem}.srt + .qc.md
確認開跑？
```

## Refresh Policy

Update this file when:
- Claude or Gemini pricing changes significantly
- Observed times diverge >2x from estimate for typical workloads
- New pipeline stages added

The skill should NOT read pricing from runtime APIs — that's over-engineering
for a rough estimate. Keep it static and refresh manually.
