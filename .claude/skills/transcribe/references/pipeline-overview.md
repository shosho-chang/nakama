# Pipeline Overview

The Nakama transcribe pipeline has four optional stages. All can be toggled
independently. Read this before Step 4 to explain trade-offs to the user.

## Stage 1: Auphonic Normalization (cloud)

- **What**: Upload audio to Auphonic, apply loudness normalization + dynamic
  noise reduction, download processed file.
- **When to enable**: Almost always. Substantially improves ASR accuracy on
  noisy / uneven-volume recordings (podcasts, interviews).
- **When to skip (`--no-auphonic`)**:
  - Slow upload link and short deadline (upload dominates wall-clock time).
  - Audio is already studio-clean (re-normalization yields nothing).
  - Auphonic quota exhausted on all configured accounts.
- **Cost**: Free tier 2 hr/month per account; pipeline supports multiple
  accounts round-robin.
- **Time**: ~`file_size_mb / (user_upload_mbps * 8 / 60)` min upload + ~1 min
  processing. Upload bandwidth is the bottleneck.

## Stage 2: FunASR Paraformer-zh (local GPU)

- **What**: Local GPU ASR using FunASR Paraformer-zh model. Outputs SRT with
  VAD-based segmentation, word-level timestamps, and hotword priming.
- **When to enable**: Always (this is the only ASR stage; disabling it means
  no transcription at all, not a supported mode).
- **Cost**: $0 (local compute).
- **Time**: ~10 seconds per 20 minutes of audio on RTX 5070 Ti.
- **Language**: Traditional Chinese output (OpenCC 簡→繁 post-step).
- **Why not Whisper**: Whisper large-v3 CER on AIShell1 is 4.72% vs FunASR
  Paraformer-zh at 0.54% — nearly an order of magnitude worse on Mandarin.

## Stage 3: Claude Opus Correction

- **What**: Opus reads the SRT with Pinyin annotations and a three-round
  correction prompt (mechanical → semantic → final check). Outputs JSON
  diff with per-line corrections and uncertainty flags.
- **When to enable**: When you want homophone correction (e.g. `蘇味行銷`
  → `素味平生`), domain-term fixes (driven by `--project-file` hotwords),
  and QC uncertainty flagging.
- **When to skip (`--no-llm-correction`)**:
  - Pure ASR needed (fast output, willing to live with homophone errors).
  - API quota exhausted.
- **Cost**: ~$0.33 / hour of audio (Opus pricing).
- **Note**: Disabling Opus also makes Gemini arbitration moot (arbitration
  only runs on Opus-flagged uncertain segments). Warn the user if they
  disable Opus alone.

## Stage 4: Gemini 2.5 Pro Multimodal Arbitration

- **What**: For each uncertain segment flagged by Opus, slice a ±1s audio
  clip around it (from the Auphonic-normalized audio), send the clip +
  Opus suggestion + SRT context to Gemini 2.5 Pro, let Gemini decide
  among `keep_original` / `accept_suggestion` / `custom` / `refused`.
- **When to enable**: For episodes where transcription accuracy matters
  (published content, research material).
- **When to skip (`--no-arbitration`)**:
  - Cost-sensitive batch runs.
  - No Gemini API key configured.
  - Preview run (skip arbitration for speed, re-run later with only
    arbitration enabled if such mode exists — currently it doesn't, so
    a full re-run would be needed).
- **Cost**: ~$0.3-0.5 / hour of audio with `thinking_budget=512` (default).
  Without budget cap the thinking output dominates and cost scales 5-10x.
- **Time**: ~70 seconds per 10-13 clips (3 worker threads).
- **Refusal behavior**: If Gemini says "無關 / 無法判斷 / 無法辨識 / 無法識別 /
  沒有相關 / 沒有對應 / 不相關", verdict is downgraded to `refused` and
  the ASR original text is kept. These appear in the QC report for human
  review.

## Output Files

- `<output_dir>/<audio_stem>.srt` — final SRT with corrections applied
- `<output_dir>/<audio_stem>.qc.md` — QC report (see `qc-report-format.md`)
- Intermediate files are cleaned up automatically

## Why no second ASR engine?

An earlier design explored dual-ASR (FunASR + Whisper) with alignment
+ diff. Dropped because Whisper's Mandarin quality is too far below
FunASR to add signal (the diff is noise-dominated). Multimodal arbitration
against the raw audio is a cleaner way to resolve uncertainty.
