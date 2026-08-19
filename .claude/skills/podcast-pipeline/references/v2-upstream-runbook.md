# Podcast Subtitle V2 upstream operator reference

## Canonical source plan

Paths are resolved relative to this plan. Include only episode-specific sources approved for review.

```json
{"episode_id":"episode-id","schema_version":1,"sources":[{"author":"Producer","document_date":"2026-08-13","kind":"interview_outline","path":"訪綱.md","publisher":null,"source_id":"episode-outline-v1","title":"Episode Interview Outline","version":"v1"}]}
```

Supported `kind`: `book`, `research_report`, `interview_outline`, `knowledge_base`.
Omitted authority attestation always produces `contextual_reference` with no scopes.

## Explicit authority attestation

Do not create this file on the user's behalf. Populate it only after the user explicitly confirms
that the exact source bytes and role are authoritative for the closed scope. `source_sha256` and
`source_size_bytes` must describe the reviewed source. The attestation file's own SHA-256 becomes
the descriptor's `record_sha256`.

```json
{"accepted_at":"2026-08-19T03:00:00+08:00","attestor":{"display_name":"Guest Author","kind":"person","schema_version":1,"stable_id":"person:guest-author"},"confirmed":true,"contract":"podcast-reference-authority-attestation-v1","provenance":"author_record","reviewer":"shosho","role":"published_author_book","schema_version":1,"source_id":"guest-book-v1","source_sha256":"<64 lowercase hex>","source_size_bytes":123}
```

Closed role matrix:

| Role | Source | Result | Fixed scopes |
|---|---|---|---|
| `published_author_book` | `book` | authoritative | title, author, terminology, verbatim source text |
| `owner_final_report` | `research_report` | authoritative | title, terminology, verbatim source text |
| `owner_approved_outline_glossary` | `interview_outline` | curated | owner-approved glossary spelling only |

An outline cannot become authoritative. Role/source/provenance mismatch fails closed.

## Evidence status command

```powershell
python scripts/podcast_subtitle_v2_evidence.py status `
  --normalized-audio "<episode>/normalized.wav" `
  --normalized-manifest "<episode>/normalized-handoff.v1.json" `
  --recognition-manifest "<episode>/subtitle-v2/memo-recognition.v1.json" `
  --recognition-source-export "<episode>/subtitle-v2/memo-recognition.srt" `
  --recognition-acceptance-receipt "<episode>/subtitle-v2/memo-recognition-acceptance.v1.json" `
  --cue-source-export "<episode>/subtitle-v2/memo-recognition.srt" `
  --cue-acceptance-receipt "<episode>/subtitle-v2/memo-cue-acceptance.v1.json"
```

`ready=true` means the six evidence paths are mutually bound. It does not mean a human accepted
unresolved text, that V2 full audit finished, or that projection/publish is approved.

## Verified Projection → highlight handoff

The first formal Resolve/project open uses explicit projection, generation, episode, projection
manifest hash, and Reference Manifest lineage. It persists the verified result atomically at
`<episode>/.stage5/verified-subtitle-handoff.v2.json`.

```powershell
python scripts/run_highlight_cut.py "<episode>" --mining-input
```

Miners read only the returned `srt_path`. `--validate`, `run_cut_shortlist.py --pick`,
`--materialize`, and `--refresh-subs` carry or verify the same lineage. Never copy V2 SRT to root
`transcript.srt` as truth; root V1 is available only for explicit legacy forensic work.
