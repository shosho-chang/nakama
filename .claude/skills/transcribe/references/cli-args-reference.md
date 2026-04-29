# CLI Args Reference — scripts/run_transcribe.py

Authoritative contract as of Nakama PR #26. If the script evolves, update
this file to match. The skill should NEVER hardcode flags in SKILL.md
outside what's documented here.

## Usage

```
python scripts/run_transcribe.py <audio_path> [options]
```

## Positional

| Arg | Type | Required | Description |
|-----|------|----------|-------------|
| `audio_path` | Path | yes | Audio file (.wav / .mp3 / .m4a / .flac) |

## Options

| Flag | Type | Default | Effect |
|------|------|---------|--------|
| `--output-dir PATH` | Path | `<audio_dir>/out` | Directory for SRT + QC output |
| `--project-file PATH` | Path | None | LifeOS Podcast Project `.md`, used to extract hotwords + correction context |
| `--no-auphonic` | flag | off (Auphonic ON) | Skip Auphonic normalization (saves upload time) |
| `--no-arbitration` | flag | off (arbitration ON) | Skip Gemini 2.5 Pro multimodal arbitration (saves cost) |
| `--no-llm-correction` | flag | off (Opus ON) | Skip Opus correction entirely (pure ASR output) |

## Command Construction (skill-side)

Build the list in this order to match user expectation in logs:

```bash
python scripts/run_transcribe.py \
    "<audio_path>" \
    --output-dir "<output_dir>" \
    --project-file "<project_path>"   # omit if None
    --no-auphonic                       # only if disabled
    --no-llm-correction                 # only if disabled
    --no-arbitration                    # only if disabled
```

Quote all paths — they often contain spaces on Windows (e.g.
`E:/Shosho LifeOS/Projects/`).

## Exit Codes

The script currently uses default Python behavior: 0 on success, non-zero
on unhandled exceptions. Do not rely on specific non-zero codes; parse
stderr for error type instead.

## Stdout Format

The script prints these markers:

```
音檔: {audio_path}
輸出: {output_dir}
Project: {project_file}               # only if set
Pipeline: {stages joined by +}
------------------------------------------------------------
{pipeline log lines — Auphonic, FunASR, LLM, arbitration}
------------------------------------------------------------
完成！耗時 {X.X} 分鐘
SRT: {srt_path}
QC:  {qc_path}                        # only if exists
```

Parse the final four lines in Step 7 to extract paths. If the final line
doesn't appear, the run failed — check stderr.

## Cost & Token Usage

Cost log is emitted by `shared/anthropic_client` and `shared/gemini_client`
during the run. Lines look like:

```
[cost] opus input={N} output={M} cost=${X.XX}
[cost] gemini-2.5-pro input={N} thoughts={K} output={M} cost=${X.XX}
```

Aggregate these for the Step 7 summary if present.

## Environment Variables (pipeline)

The script reads from `.env` via `python-dotenv`:

- `ANTHROPIC_API_KEY` — required for Opus correction
- `GEMINI_API_KEY` — required for multimodal arbitration
- `AUPHONIC_ACCOUNT_*` — optional, multi-account round-robin
- `DISABLE_ROBIN=1` — unrelated to transcribe, ignore

If a required key is missing, the pipeline fails at the respective stage.
Suggest the user disable that stage (`--no-llm-correction` /
`--no-arbitration`) or populate the key in `.env`.

## Python Environment

Run from the Nakama repo root (`F:/nakama/` on Windows). The script uses
`sys.path.insert(0, ...)` to find the `shared/` package, so it works
anywhere — but `.env` is loaded from CWD, so running from repo root is
simplest.

On Windows: `python scripts/run_transcribe.py ...`
On macOS/Linux: `python3 scripts/run_transcribe.py ...` if the `python`
alias isn't Python 3.
