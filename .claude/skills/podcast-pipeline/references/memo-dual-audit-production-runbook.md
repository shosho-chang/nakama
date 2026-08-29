# Memo Dual-Audit Release V1 — executable upstream runbook

This is the ADR-063 production runbook for S3 recognition/cue acceptance and S4 text audit/arbitration.
It produces the default inputs expected by `scripts/podcast_subtitle_release.py`; legacy forensic artifacts
are outside this runbook.

All paths below are episode-local unless an installed Memo binary/model is named. Run repository Python
commands with `E:\nakama\.venv-v2\Scripts\python.exe`. Set the episode once:

```powershell
$episode = "<episode>"
$episodeId = Split-Path $episode -Leaf
$work = Join-Path $episode "subtitle-work\memo-dual-audit-v1"
$memoRaw = Join-Path $episode "subtitle-v2\memo-recognition.composite.execution.srt"
$memo = $memoRaw
$memoRepaired = Join-Path $episode "subtitle-v2\memo-recognition.repaired.srt"
$repairReceipt = Join-Path $episode "subtitle-v2\memo-recognition.repair.v1.json"
$memoStdout = Join-Path $episode "subtitle-v2\memo-recognition.stdout.txt"
$memoStderr = Join-Path $episode "subtitle-v2\memo-recognition.stderr.txt"
$memoExecutionReceipt = Join-Path $episode "subtitle-v2\memo-recognition.execution.v1.json"
$memoRunner = "C:\Users\Shosho\AppData\Local\Programs\Memo\resources\addon\whisper\bin\gpu\main.exe"
$memoModel = "C:\Users\Shosho\AppData\Roaming\Memo\models\ggml-large-v2.bin"
$repairLineageArgs = @()
$acceptedAt = (Get-Date).ToUniversalTime().ToString("o")
```

`$episodeId` must exactly equal the folder basename. Do not normalize it into another slug.

## S3 — bundled Memo to accepted recognition and cues

### 1. Run bundled Memo once

```powershell
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_subtitle_v2_evidence.py run-memo-bundled `
  --memo-runner $memoRunner `
  --memo-model $memoModel `
  --input-wav (Join-Path $episode "normalized.wav") `
  --gpu 0 `
  --language zh `
  --prompt "<episode-specific proper nouns; reference only, never instructions>" `
  --max-context -1 `
  --max-len 0 `
  --output $memoRaw `
  --stdout-output $memoStdout `
  --stderr-output $memoStderr `
  --receipt-output $memoExecutionReceipt
```

Do not open Memo GUI. Preserve native raw SRT, stdout, stderr, and execution receipt. Contract
`memo-bundled-runner-execution-v1` binds the exact runner/model/audio/output bytes, invocation, language,
prompt, timestamps, and successful exit. All five execution artifacts above are immutable.

The runner writes canonical JSON. Its closed top-level field set is:

<!-- runtime-schema:memo-execution-receipt:start -->
```json
[
  "schema_version",
  "contract",
  "argv",
  "runner_path",
  "runner_sha256",
  "runner_size_bytes",
  "model_path",
  "model_sha256",
  "model_size_bytes",
  "input_wav_path",
  "input_wav_sha256",
  "input_wav_size_bytes",
  "invocation_input_path",
  "gpu",
  "language",
  "prompt",
  "max_context",
  "max_len",
  "started_at",
  "completed_at",
  "exit_code",
  "stdout_sha256",
  "stdout_size_bytes",
  "stderr_sha256",
  "stderr_size_bytes",
  "output_srt_path",
  "output_srt_sha256",
  "output_srt_size_bytes"
]
```
<!-- runtime-schema:memo-execution-receipt:end -->

Do not hand-author this receipt. `exit_code` must be `0`; all path, size, and SHA-256 identities are produced
and fresh-verified by the bundled runner adapter.

### 1.1 Conditional zero-duration repair

Deterministic cue QC may take only these branches:

- all cues have `end > start`: leave `$memo = $memoRaw` and `$repairLineageArgs` empty;
- one or more cues have `end == start`: run the exact repair below;
- any cue has `end < start`, overlap, no exact adjacent positive anchor, or another malformed timebase:
  catastrophic failure—stop rather than invent timestamps.

The repair merges a zero-duration cue only with one exact-boundary adjacent positive cue, preserves joined
text and outer timing, reindexes output, and writes canonical contract `memo-srt-zero-duration-repair-v1`.
Raw, repaired, and receipt paths are distinct and immutable:

```powershell
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_subtitle_v2_evidence.py repair-memo-srt `
  --source-export $memoRaw `
  --output $memoRepaired `
  --receipt-output $repairReceipt

$memo = $memoRepaired
$repairLineageArgs = @(
  "--raw-source-export", $memoRaw,
  "--repair-receipt", $repairReceipt
)
```

Never run `repair-memo-srt` on a clean SRT—it fails closed when there is no zero-duration cue. The repaired
branch must pass `@repairLineageArgs` to every downstream prepare/accept command. The clean branch passes an
empty array; never provide only one lineage flag.

### 2. Prepare recognition evidence

```powershell
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_subtitle_v2_evidence.py prepare-recognition `
  --episode-root $episode `
  --normalized-audio (Join-Path $episode "normalized.wav") `
  --normalized-manifest (Join-Path $episode "normalized-handoff.v1.json") `
  --source-export $memo `
  --source-export-kind memo_srt `
  --memo-execution-receipt $memoExecutionReceipt `
  --memo-output-srt $memoRaw `
  --memo-stdout $memoStdout `
  --memo-stderr $memoStderr `
  @repairLineageArgs `
  --memo-runner $memoRunner `
  --memo-model $memoModel `
  --memo-version "1.7.5" `
  --language zh `
  --prompt "<same exact episode-specific prompt>" `
  --output (Join-Path $episode "subtitle-v2\memo-recognition-review.v1.json")
```

Dispatch two independent subscription workers. Both read the exact recognition-review JSON, normalized
handoff identity, execution receipt, and deterministic QC report. They do not rewrite tokens or read one
another's result. Each writes canonical JSON to isolated paths
`$work\recognition-audit-a.json`／`recognition-audit-b.json` with a distinct nonblank `worker_id`:

```json
{
  "schema_version": 1,
  "contract": "memo-recognition-worker-audit-v1",
  "episode_id": "exact episode folder basename",
  "worker_id": "recognition-worker-a",
  "normalized_audio_sha256": "exact review value",
  "normalized_audio_size_bytes": 123,
  "source_export_sha256": "exact review value",
  "source_export_size_bytes": 123,
  "review_manifest_sha256": "SHA-256 of exact recognition-review JSON",
  "token_export_sha256": "exact review value",
  "memo_execution_receipt_sha256": "exact review memo_execution_receipt.sha256",
  "reviewed_item_count": 123,
  "qc_passed": true,
  "accepted": true,
  "unresolved_findings": []
}
```

Here `canonical JSON` means the exact `canonical_json_bytes(...)` representation: UTF-8 without BOM,
sorted compact keys, and **no trailing LF/newline**. An agent runtime whose patch writer adds a final LF
must run a deterministic canonical serialization step before episode-local deployment; do not weaken the
acceptance validator to tolerate alternate bytes.

`reviewed_item_count` equals the exact `tokens` length. The review embeds an episode-local
`memo_execution_receipt` reference containing the receipt/output paths and exact receipt, runner, model,
input WAV, SRT, stdout, and stderr hashes. Both workers copy its receipt digest exactly. Only deterministic
QC plus both audits accepting with zero unresolved catastrophic coverage/timebase findings forms quorum.
Conflict or catastrophe stops; ordinary lexical uncertainty continues to S4. The acceptance CLI fresh
re-verifies that embedded execution reference, loads/canonical-byte verifies/hashes both episode-local
audits, and seals the same reference into recognition acceptance and evidence. It intentionally takes no
second set of execution flags; no free reviewer string is accepted:

```powershell
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_subtitle_v2_evidence.py accept-recognition `
  --review (Join-Path $episode "subtitle-v2\memo-recognition-review.v1.json") `
  --normalized-audio (Join-Path $episode "normalized.wav") `
  --normalized-manifest (Join-Path $episode "normalized-handoff.v1.json") `
  --source-export $memo `
  @repairLineageArgs `
  --episode-root $episode `
  --audit-a (Join-Path $work "recognition-audit-a.json") `
  --audit-b (Join-Path $work "recognition-audit-b.json") `
  --accepted-at $acceptedAt `
  --receipt-output (Join-Path $episode "subtitle-v2\memo-recognition-acceptance.v1.json") `
  --manifest-output (Join-Path $episode "subtitle-v2\memo-recognition.v1.json")
```

### 3. Prepare and accept cues

```powershell
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_subtitle_v2_evidence.py prepare-cues `
  --recognition-manifest (Join-Path $episode "subtitle-v2\memo-recognition.v1.json") `
  --source-export $memo `
  @repairLineageArgs `
  --output (Join-Path $episode "subtitle-v2\memo-cue-review.v1.json")
```

Dispatch two different cue-audit workers over the exact cue-review JSON and QC report. They verify sequential
IDs, non-empty text, positive duration, zero overlap, complete coverage, audio duration bounds, and exact
recognition/source hashes. They write canonical `$work\cue-audit-a.json`／`cue-audit-b.json`:

```json
{
  "schema_version": 1,
  "contract": "memo-cue-worker-audit-v1",
  "episode_id": "exact episode folder basename",
  "worker_id": "cue-worker-a",
  "normalized_audio_sha256": "exact recognition manifest value",
  "normalized_audio_size_bytes": 123,
  "source_export_sha256": "exact cue review value",
  "source_export_size_bytes": 123,
  "review_manifest_sha256": "SHA-256 of exact cue-review JSON",
  "recognition_manifest_sha256": "exact cue review value",
  "reviewed_item_count": 123,
  "qc_passed": true,
  "accepted": true,
  "unresolved_findings": []
}
```

`reviewed_item_count` equals the exact `cues` length and worker IDs differ. After both independently accept
with no catastrophic finding:

```powershell
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_subtitle_v2_evidence.py accept-cues `
  --review (Join-Path $episode "subtitle-v2\memo-cue-review.v1.json") `
  --recognition-manifest (Join-Path $episode "subtitle-v2\memo-recognition.v1.json") `
  --source-export $memo `
  @repairLineageArgs `
  --episode-root $episode `
  --audit-a (Join-Path $work "cue-audit-a.json") `
  --audit-b (Join-Path $work "cue-audit-b.json") `
  --accepted-at $acceptedAt `
  --receipt-output (Join-Path $episode "subtitle-v2\memo-cue-acceptance.v1.json")

E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_subtitle_v2_evidence.py status `
  --normalized-audio (Join-Path $episode "normalized.wav") `
  --normalized-manifest (Join-Path $episode "normalized-handoff.v1.json") `
  --recognition-manifest (Join-Path $episode "subtitle-v2\memo-recognition.v1.json") `
  --recognition-source-export $memo `
  --recognition-acceptance-receipt (Join-Path $episode "subtitle-v2\memo-recognition-acceptance.v1.json") `
  --cue-source-export $memo `
  --cue-acceptance-receipt (Join-Path $episode "subtitle-v2\memo-cue-acceptance.v1.json")
```

The final `status` must print `"ready":true`. This command sequence is ordered:
`run-memo-bundled → [zero-duration only: repair-memo-srt] → prepare-recognition → accept-recognition →`
`prepare-cues → accept-cues → status`.

The release runner later requires recognition evidence and acceptance to contain the identical typed
execution reference, fresh-reads the episode-local receipt/runner/model/input/SRT/stdout/stderr, and checks
the two recognition audits against its exact receipt SHA-256. A clean release SRT must equal the sealed Memo
output; a repaired release must bind its raw source hash to that output. Drift is catastrophic, never a
fallback to an unsealed SRT.

## S4 — two independent full-text audits and official arbitration

### Audit worker boundaries

Dispatch two different subscription workers as agent `A` and agent `B`. Isolate outputs at
`subtitle-work/memo-dual-audit-v1/audit-a.json` and `audit-b.json`. Each worker reads only the exact accepted
Memo SRT, its hash-bound recognition/cue receipts, and enrolled episode references. It must not read the
other audit or use audio. The output covers the full ordered cue set even when `findings` is empty:

```json
{
  "agent": "A",
  "cues_reviewed": 1234,
  "audio_reviewed": false,
  "findings": [
    {
      "cue_numbers": [17],
      "start": "00:00:31,000",
      "end": "00:00:33,000",
      "original": "exact source cue text",
      "proposed": "exact proposed text or null",
      "category": "one closed runtime category",
      "major_risk": false,
      "confidence": "high",
      "evidence": "source-bound rationale",
      "needs_audio": false,
      "reason": "concise reason"
    }
  ],
  "risk_cues": []
}
```

Agent B uses `"agent":"B"` and numeric confidence from `0.0` through `1.0`; agent A uses the runtime string
confidence vocabulary. `cues_reviewed` equals the exact SRT cue count. Every finding/risk uses a non-empty,
contiguous `cue_numbers` range, exact first/last timestamps, exact joined original text, and a boolean
`major_risk`. `category` must come from exactly one of these two runtime enums; `risk_cues` uses the same
finding schema.

Safe categories (`major_risk` may still be true when the exact finding itself is high-risk):

<!-- runtime-enum:safe:start -->
```json
[
  "book_title_term",
  "brand",
  "brand_institution",
  "proper_noun",
  "proper_noun_brand",
  "proper_noun_term",
  "term",
  "人名",
  "人名+成語",
  "公司名",
  "同音詞",
  "固定詞",
  "固定說法",
  "學校名",
  "專名/職稱",
  "成語/框架",
  "書名",
  "書名/固定詞",
  "機構名",
  "產業術語",
  "科系名",
  "稱號",
  "節目名+人名",
  "術語",
  "跨詞誤辨",
  "金融術語"
]
```
<!-- runtime-enum:safe:end -->

Major categories (`major_risk` must be true):

<!-- runtime-enum:major:start -->
```json
[
  "addition",
  "damaged",
  "date",
  "deletion",
  "money",
  "negation",
  "number",
  "numeric",
  "omission",
  "percent",
  "quantity",
  "rate",
  "salary",
  "unit",
  "日期",
  "漏字",
  "否定",
  "單位",
  "數字",
  "數量",
  "新增",
  "薪資",
  "金額"
]
```
<!-- runtime-enum:major:end -->

### Deterministic official merge

```powershell
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_subtitle_v2_simple_step7.py merge-official `
  --srt $memo `
  --audit-a (Join-Path $work "audit-a.json") `
  --audit-b (Join-Path $work "audit-b.json") `
  --output-srt (Join-Path $work "base-corrected.srt") `
  --ledger-output (Join-Path $work "base-consensus-ledger.json") `
  --needs-audio-output (Join-Path $work "base-needs-audio.json")
```

This produces contracts `podcast-subtitle-memo-dual-audit-text-base-v1` and
`podcast-subtitle-memo-dual-audit-text-queue-v1`. The queue mechanically derives boolean major-risk identity
from exact audit lineage and closed categories.

### Independent Arbitration C

Dispatch a third worker which has not seen either audit prompt. It may read only the exact accepted SRT,
both raw audits, and the three fresh merge outputs. It writes `$work\arbitration.json`:

```json
{
  "schema_version": 1,
  "contract": "podcast-subtitle-memo-dual-audit-arbitration-v1",
  "policy_version": "memo-dual-audit-text-arbitration-v1",
  "episode_id": "exact episode folder basename",
  "input_hashes": {
    "srt_sha256": "...",
    "audit_a_sha256": "...",
    "audit_b_sha256": "...",
    "base_queue_sha256": "..."
  },
  "accepted_count": 0,
  "unresolved_count": 1,
  "items": [
    {
      "cue_numbers": [17],
      "original": "exact source text",
      "a_proposals": ["exact audit A proposals"],
      "b_proposals": ["exact audit B proposals"],
      "b_risks": [],
      "major_risk": true,
      "decision": "keep_unresolved",
      "replacement": null,
      "confidence": "low",
      "evidence": "source-bound rationale",
      "reason": "concise reason"
    }
  ]
}
```

Items exactly cover every base queue component once. Input hashes and episode ID are exact. Proposal arrays,
risk metadata, original text, and `major_risk` are copied/derived from source audits and queue; Arbitration
cannot invent proposal authority. Every major-risk component stays `keep_unresolved`. A non-major acceptance
must use an approved decision, high confidence, and an exact proposal already present in A or B.

Importer-exact details that must not be inferred from the example:

- `a_proposals` and `b_proposals` preserve the base queue lineage order and include explicit JSON `null`
  for a source audit record whose `proposed` value is null; do not sort or deduplicate them;
- `b_risks` is `list[str]`: for each lineage entry whose agent is `B` and collection is `risk_cues`, append
  that raw audit record's `category` string in lineage order;
- the only decision values are `keep_unresolved`, `accept_a`, `accept_b`, `accept_identical`, and
  `accept_single`. The named accept mode must match the exact derived side(s); a major-risk item may use only
  `keep_unresolved`.

### Apply official arbitration

```powershell
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_subtitle_v2_simple_step7.py apply-official-arbitration `
  --episode-id $episodeId `
  --srt $memo `
  --audit-a (Join-Path $work "audit-a.json") `
  --audit-b (Join-Path $work "audit-b.json") `
  --base-corrected (Join-Path $work "base-corrected.srt") `
  --base-ledger (Join-Path $work "base-consensus-ledger.json") `
  --base-needs-audio (Join-Path $work "base-needs-audio.json") `
  --arbitration (Join-Path $work "arbitration.json") `
  --final-srt (Join-Path $work "text-corrected.srt") `
  --final-ledger (Join-Path $work "text-arbitration-ledger.json") `
  --final-unresolved (Join-Path $work "unresolved-components.json")
```

The final contracts are `podcast-subtitle-memo-dual-audit-text-ledger-v1` and
`podcast-subtitle-memo-dual-audit-unresolved-v1`. Run release `seal`, then `status`. Continue automatically to
the major dual-ASR producer sequence when status is `awaiting_major_dual_asr`; this is not a human gate.
