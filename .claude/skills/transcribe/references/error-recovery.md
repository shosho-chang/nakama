# Error Recovery Guide

When `scripts/run_transcribe.py` fails, map the error to a recommended
re-run command. Do NOT auto-retry — let the user decide.

## Diagnosis Steps

1. Read the last ~40 lines of stderr.
2. Match against the patterns below.
3. Present: (a) one-line cause, (b) suggested re-run command, (c) link
   to deeper investigation if needed.

## Error → Recovery Table

### Auphonic errors

| Pattern in stderr | Cause | Suggested re-run |
|-------------------|-------|-----------------|
| `AuphonicQuotaError` / `402` / `quota exceeded` | All configured accounts hit free-tier limit | Add `--no-auphonic` or wait for monthly reset |
| `AuphonicTimeout` / `requests.exceptions.ReadTimeout` on upload | Slow / unstable link | Retry with same command; if persistent, `--no-auphonic` |
| `FileNotFoundError: .../download_url` | Auphonic API format changed or hit failure mid-processing | Check PR #22 bug #7; retry once; if persistent, file an issue |
| `NameError: ACCOUNT_X` in `.env` | Inline `#` comment in `.env.example` being parsed | Check PR #22 bug #6; clean `.env` |

### FunASR errors

| Pattern | Cause | Suggested re-run |
|---------|-------|-----------------|
| `torch.cuda.OutOfMemoryError` | GPU VRAM insufficient | Restart Python (free VRAM), close other GPU apps, retry |
| `CUDA error: device-side assert` | Corrupted audio or unsupported format | Convert to 16kHz mono WAV with `ffmpeg -ar 16000 -ac 1` and retry |
| `ModuleNotFoundError: funasr` | Python environment | `pip install -r requirements.txt` |
| `No audio detected by VAD` | Silent file or format mismatch | Check with `ffplay`; convert format if needed |

### Claude / Opus errors

| Pattern | Cause | Suggested re-run |
|---------|-------|-----------------|
| `AnthropicError: 401 Unauthorized` | Missing / wrong `ANTHROPIC_API_KEY` | Fix `.env` or `--no-llm-correction` |
| `RateLimitError 429` | Hit rate limit | Wait 60s and retry, or run `--no-llm-correction` for now |
| `BadRequestError: model not found` | Outdated Opus model ID | Check `claude-opus-4-7`; if deprecated again, consult `reference_api_contract_pitfalls.md` |
| `json.JSONDecodeError` in correction | Opus returned malformed JSON (rare) | Retry; pipeline has regex fallback |

### Gemini errors

| Pattern | Cause | Suggested re-run |
|---------|-------|-----------------|
| `GoogleAPIError: 401` / `PERMISSION_DENIED` | Missing / wrong `GEMINI_API_KEY` | Fix `.env` or `--no-arbitration` |
| `ResourceExhausted 429` | Rate limit | Wait and retry, or `--no-arbitration` |
| Empty response / `finish_reason=MAX_TOKENS` | `thinking_budget` exceeded — see PR #24 | Should not happen with default 512; if it does, file issue |
| All verdicts = `refused` | Gemini saw audio-text mismatch for most clips | Likely ASR fundamentally wrong (wrong file? wrong language?). Double-check input. |

### ffmpeg / audio errors

| Pattern | Cause | Suggested re-run |
|---------|-------|-----------------|
| `ffmpeg: command not found` | ffmpeg not in PATH | Install ffmpeg; on Windows `winget install ffmpeg` |
| `Invalid data found when processing input` | Corrupted / unsupported audio | Re-encode: `ffmpeg -i input.x -c:a pcm_s16le output.wav` |
| `audio_clip.py` tempfile cleanup warning | Benign, ignore | — |

### Generic / unknown

If nothing matches:
1. Show the last 40 lines of stderr to the user.
2. Offer three options:
   - "Retry as-is" (same command)
   - "Skip the failing stage" (infer which `--no-*` flag to add)
   - "Investigate" — help the user read the traceback

Do NOT suggest editing source code as a first-line recovery. If the error
recurs predictably, it's a bug worth filing; the skill isn't the place
to patch it.

## Partial Re-run Strategy

The pipeline does not currently support resuming from a mid-stage checkpoint.
If Auphonic succeeded but Opus failed:
- The normalized audio is NOT cached (cleaned up on exit).
- Re-running re-uploads to Auphonic (same cost in time).

If this becomes painful, file an enhancement for caching the Auphonic
output. Don't work around it in the skill.

## When to Escalate

If the same error recurs across multiple files / different audio:
1. The pipeline has a real bug.
2. Suggest the user file an issue at the Nakama repo (if open-sourced)
   or send you the traceback in a fresh conversation for investigation.
3. Do NOT keep retrying the same failing command.
