---
name: transcribe
description: >
  Create or review evidence-backed Traditional Chinese podcast subtitles with
  Podcast Subtitle V2. Use for transcribe/轉錄/轉字幕, podcast audio paths,
  subtitle pipeline status, cost estimates, QC review, or preparing a podcast
  episode for downstream video production. The production path is Memo-first
  and consumes an already-normalized PCM WAV.
---

# Transcribe — Podcast Subtitle V2

Use `agents/brook/podcast_subtitles/` as the only production subtitle engine.
`scripts/run_transcribe.py` and its WhisperX/Opus/Gemini pipeline are retired
V1 forensic baselines; never execute them for a new episode and never silently
fall back to them.

## Production contract

```text
Already-normalized PCM WAV
  → verified normalized-audio handoff
  → immutable Memo 1.7.5 / ggml-large-v2 recognition evidence
  → accepted Memo cue-boundary evidence
  → bounded reference retrieval for every canonical span
  → full text and audio audit work packets
  → append-only correction decisions
  → accepted canonical transcript
  → semantic units
  → fail-closed Verified Projection
```

Memo is the text and cue-boundary authority. Qwen3-ASR and Faster-Whisper are
optional corroboration only. Auphonic belongs upstream: V2 verifies the exact
normalized WAV bytes but never invokes or modifies Auphonic.

## Preflight

1. Resolve the exact episode root and normalized PCM WAV. Never guess a path.
2. Run `ffprobe` and report duration, codec, channels, sample rate, and size.
3. Verify the normalized-audio handoff, Memo raw export, Memo GUI/reviewed SRT,
   and both acceptance receipts exist and bind the same audio/export bytes.
4. Identify episode-specific books, final reports, approved outline, fact sheet,
   and isolated microphone tracks. Ask before enrolling any uncertain source;
   never scan a vault or directory as implicit evidence.
5. Before paid provider work, state the exact action and cost mode and obtain
   explicit authorization. Subscription work packets are the default.
6. If any required evidence manifest or receipt is absent, stop and report the
   missing artifact. Do not hand-author production JSON from guesses.

Required production settings are:

```text
PODCAST_SUBTITLE_V2_NORMALIZED_HANDOFF_MANIFEST
PODCAST_SUBTITLE_V2_MEMO_RECOGNITION_MANIFEST
PODCAST_SUBTITLE_V2_MEMO_RECOGNITION_SOURCE_EXPORT
PODCAST_SUBTITLE_V2_MEMO_RECOGNITION_ACCEPTANCE_RECEIPT
PODCAST_SUBTITLE_V2_MEMO_CUE_SOURCE_EXPORT
PODCAST_SUBTITLE_V2_MEMO_CUE_ACCEPTANCE_RECEIPT
PODCAST_SUBTITLE_V2_TEXT_AUDIT_MODEL
PODCAST_SUBTITLE_V2_TEXT_AUDIT_MODEL_VERSION
PODCAST_SUBTITLE_V2_SEMANTIC_MODEL
PODCAST_SUBTITLE_V2_SEMANTIC_MODEL_VERSION
PODCAST_SUBTITLE_V2_AUDIO_AUDIT_MODEL
PODCAST_SUBTITLE_V2_AUDIO_AUDIT_MODEL_VERSION
```

Unknown `PODCAST_SUBTITLE_V2_*` settings fail closed. Do not use the retired
`PODCAST_SUBTITLE_V2_ALLOW_PAID_GEMINI` switch.

## Operate V2

The default trusted factory is
`agents.brook.podcast_subtitles.production:build_production`:

```powershell
python -m agents.brook.podcast_subtitles `
  --episode-root "<episode-root>" `
  [--reference-manifest "<episode-references.v2.json>"] `
  run --episode-id "<episode-id>" --source-audio "<normalized.wav>"
```

Available verbs are `run`, `status`, `review`, `decide`, `decide-native`, and
`project`. Pass the same exact reference manifest to every verb for a
reference-backed episode. When two isolated microphones exist, pass exactly
two explicit `--mic-track LABEL=PATH` values; never infer speakers from names.

On `Interrupted`, report every packet and expected response path. Fill the
strict response JSON through the active subscription workflow without editing
packet bytes, then rerun the same verb. A missing/malformed response, provider
failure, unresolved stable-span issue, or provenance mismatch is not success.

Use `review` for stable AudioSpan issues. Never edit SRT as transcript truth.
Native corrections require the stored proposal/evidence IDs plus typed human
audio/reference receipts; no model may impersonate a human reviewer.

Run `project` only after Canonical Transcript acceptance. Delivery is complete
only when the exact SRT, projection sidecar, manifest, and QC form a verified
`VerifiedProjection`. Downstream video production must load that projection by
episode ID, generation ID, projection ID, and manifest SHA-256.

## Report results

Return clickable paths for the episode Generation, review packets, decisions,
and—only after successful projection—the SRT, sidecar, manifest, and QC. State
all unresolved issues and whether the result is production/cutover eligible.
