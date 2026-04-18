---
name: transcribe
description: >
  Transcribe Chinese podcast / long-form audio into SRT subtitles via an
  interactive pipeline (Auphonic normalization + FunASR Paraformer-zh ASR +
  Claude Opus correction + Gemini 2.5 Pro multimodal arbitration). Produces
  clean SRT plus a QC report with risk-tagged segments. Use this skill
  whenever the user says things like "轉錄", "transcribe", "轉字幕", gives
  you an audio file path (.wav/.mp3/.m4a), asks for podcast subtitles, or
  mentions running the transcribe / run_transcribe pipeline. Also trigger
  when the user asks to estimate transcription cost or review a .qc.md
  report from the pipeline.
---

# Transcribe — Podcast Transcription Pipeline

You are the interactive wrapper for the Nakama transcriber pipeline
(`scripts/run_transcribe.py`). Your job is to guide the user from an audio
file to a clean SRT + QC report, making judgment calls on cost, pipeline
composition, and error recovery so the user does not have to remember CLI
flags or read raw tracebacks.

You do NOT re-implement the pipeline. You shell out to
`scripts/run_transcribe.py` (the CLI contract from PR #26) and surface its
results back to the user.

## When to Use This Skill

Trigger on intent like:
- "幫我轉錄 F:/Audio/episode.wav"
- "transcribe this podcast"
- "轉字幕 / 做字幕"
- "跑 run_transcribe"
- Any explicit audio path (.wav / .mp3 / .m4a / .flac)
- Questions about transcription cost estimate for a given audio file
- Review of an existing `.qc.md` report

Do NOT trigger for:
- Real-time recording ("start recording my meeting") — this skill is for
  post-processing existing audio files
- Video editing / subtitle styling — SRT output only
- Translation of finished subtitles — separate concern

## Workflow Overview

The pipeline has 7 steps and 2 mandatory confirmation points (audio sanity
check + final cost go/no-go). Middle steps can be skipped in **fast mode**
when the user says things like "用 default"、"full pipeline"、"快速跑"、
"all defaults"。

```
Step 1. Parse intent + resolve audio_path
Step 2. Audio sanity check (ffprobe)                    [CONFIRM #1]
Step 3. LifeOS Project auto-match
Step 4. Pipeline composition (3 toggles)
Step 5. Cost + time estimate                             [CONFIRM #2]
Step 6. Invoke run_transcribe.py (stream output)
Step 7. QC report summary
```

---

## Step 1: Parse Intent

From the user's message, extract:
- `audio_path` — absolute path to the audio file
- `output_dir` (optional, defaults to `<audio_dir>/out`)
- Fast-mode signals ("用 default" / "full pipeline" / "快速跑")

If `audio_path` is not clear or the path does not exist, ask the user
explicitly. Do NOT guess.

## Step 2: Audio Sanity Check (CONFIRM #1, never skip)

Run `ffprobe` to inspect the file:

```bash
ffprobe -v quiet -print_format json -show_format -show_streams "<audio_path>"
```

Extract: duration (seconds), format, codec, channels, sample rate, file size.

Present a compact summary to the user:

```
偵測音檔 ✓
  路徑: <audio_path>
  時長: MM:SS (duration_min min)
  格式: <format>, <codec>, <channels>ch @ <sample_rate>Hz
  大小: <size_mb> MB
```

If duration > 2 hours or < 10 seconds, warn the user (likely wrong file or
oversized job). If format is not wav/mp3/m4a/flac, suggest converting with
ffmpeg first.

Ask: "路徑對嗎？" (even in fast mode — this is a mandatory sanity gate).

## Step 3: LifeOS Project Auto-Match

Why this step: passing a Project file to `--project-file` significantly
improves LLM correction accuracy (guest names, domain-specific terms
become hotwords).

Read `references/lifeos-project-format.md` for the fuzzy match algorithm.

In short:
1. Determine the LifeOS Projects directory from env `LIFEOS_PROJECTS_DIR`,
   falling back to `F:/Shosho LifeOS/Projects/` on Windows.
2. `Glob: {projects_dir}/*.md`
3. Extract keywords from the audio filename (e.g. `Angie-E42.wav` →
   `[angie, e42]`).
4. Score each Project file by: filename overlap + frontmatter `guest` /
   `aliases` match. Rank descending.
5. Present top candidates:

```
匹配到的 LifeOS Project：
  [x] Angie-E42.md        (score 0.95, most recent)
  [ ] Angie.md            (score 0.70, generic)
  [ ] （不帶 Project）
哪個用作 hotword 來源？
```

Behavior:
- Single high-confidence match (score > 0.9) → use it directly in fast mode;
  show for confirmation in normal mode.
- Multiple candidates → always show selection UI (user said this is a
  hard requirement).
- No match → ask "要手指定 --project-file 嗎？或不帶？"

## Step 4: Pipeline Composition

Read `references/pipeline-overview.md` for what each stage does.

Present three toggles with defaults all ON:

```
Pipeline 組合（全 ON 為預設）：
  [x] Auphonic normalization  （品質 ↑，但需上傳音檔）
  [x] Opus 校正              （同音字修正）
  [x] Gemini 2.5 Pro 仲裁    （多模態驗證）
要改嗎？（不改直接 go）
```

In fast mode: skip this step, use all-defaults.

Translations to CLI args:
| Toggle OFF | CLI flag |
|-----------|---------|
| Auphonic  | `--no-auphonic` |
| Opus 校正 | `--no-llm-correction` |
| Gemini 仲裁 | `--no-arbitration` |

Note: turning OFF Opus correction also bypasses Gemini arbitration (Gemini
arbitrates uncertain segments flagged by Opus — if there's no Opus pass,
there's nothing to arbitrate). Warn the user if they disable only Opus.

## Step 5: Cost + Time Estimate (CONFIRM #2, never skip)

Read `references/cost-estimation.md` for the full formula.

Quick summary for estimation (per hour of audio):
- Auphonic: upload-bandwidth-limited (default assumption: 10 Mbps up →
  roughly 1 min upload per 60 MB) + ~1 min server processing
- FunASR: ~30 seconds (local GPU)
- Opus correction: ~$0.33 / hr
- Gemini arbitration: ~$0.3-0.5 / hr (with thinking_budget=512)

Present to the user:

```
預估：
  時間：~XX min (Auphonic 上傳 YY min + pipeline ZZ min)
  成本：~$0.XX (Opus $0.XX + Gemini $0.XX)
  輸出：<output_dir>/*.srt + .qc.md
確認開跑？
```

Wait for explicit confirmation. Accept: "確認" / "go" / "跑吧" / "yes" /
"ok". Reject (or ask to re-select): "不" / "cancel" / "改一下".

## Step 6: Invoke run_transcribe.py

Build the command based on confirmed toggles:

```bash
python scripts/run_transcribe.py "<audio_path>" \
    --output-dir "<output_dir>" \
    [--project-file "<project_path>"] \
    [--no-auphonic] \
    [--no-llm-correction] \
    [--no-arbitration]
```

Run with `Bash`, stream stdout/stderr, allow long timeout (up to
600000ms = 10 min per Bash tool limit — if the audio is longer than ~40
min with Auphonic ON, run in background and monitor).

While running, do NOT narrate each line — just let stdout pass through.
Only interrupt if an exception fires.

On failure, read `references/error-recovery.md` for common errors and
their suggested re-run commands. Present the error context + recommended
next step. Do NOT auto-retry (user decides).

## Step 7: QC Report Summary

After successful run, locate the outputs:
- SRT: `<output_dir>/<audio_stem>.srt`
- QC:  `<output_dir>/<audio_stem>.qc.md`

Read the QC file. Follow `references/qc-report-format.md` to parse it.

Present a structured summary:

```
完成！耗時 XX.X 分鐘

輸出：
  SRT: <srt_path>
  QC:  <qc_path>

成本實測：
  Opus: $0.XX
  Gemini: $0.XX
  合計: $0.XX

QC 摘要：
  - Refused (Gemini 拒答): N 片段
  - High risk (建議複查): M 處 → lines: [X, Y, Z]
  - Medium risk: K 處
  - Corrections applied: J 處

建議人工複查：
  Line 42: "成功試過游牧" ← Gemini refused, 保留 ASR 原文
  Line 78: "Omega-3" ← high risk, Opus 提高到 95% 信心
  ...
```

If QC file is missing (pipeline crashed mid-way), say so explicitly and
suggest re-running the relevant stage.

---

## Fast Mode Behavior

Triggered by phrases: "用 default" / "full pipeline" / "快速跑" /
"all defaults" / "go with defaults"

- Step 2 (audio sanity) → still shown, still requires confirmation
- Step 3 (Project match) → single high-conf candidate auto-used; multi-
  candidate still requires selection (hard requirement)
- Step 4 (pipeline toggles) → skipped, all ON
- Step 5 (cost) → still shown, still requires confirmation

The two "never skip" gates are: audio path correctness + final cost.

## Open-Source Friendliness

This skill is part of the Nakama repo and intended to be extractable.
Design constraints:

- No hardcoded personal paths. LifeOS directory comes from
  `LIFEOS_PROJECTS_DIR` env var with a documented fallback.
- No hardcoded cost assumptions. The cost formulas are in
  `references/cost-estimation.md` and can be updated without editing
  the SKILL.md.
- No assumptions that Auphonic / Gemini API keys exist. If an API call
  fails due to missing key, suggest the user disable that stage.

## References

All reference files in `references/` should be read when you need the
detailed template for each step:

| File | When to read |
|------|--------------|
| `pipeline-overview.md` | Before Step 4 (explain what each stage does) |
| `cost-estimation.md` | Step 5 (full formula + bandwidth assumption) |
| `qc-report-format.md` | Step 7 (how to parse `.qc.md`) |
| `lifeos-project-format.md` | Step 3 (fuzzy match algorithm + frontmatter) |
| `cli-args-reference.md` | Step 6 (authoritative CLI contract) |
| `error-recovery.md` | Step 6 on failure (error → suggested re-run) |
