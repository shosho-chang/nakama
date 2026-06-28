# ingest skill — quality eval baseline (iteration-1)

- **Run:** 2026-06-28, manual A/B via LLM judge (mirrors the kb-ingest skill-creator executor→grader flow).
- **Fixture:** `fixtures/intermittent-fasting-longevity.md` (route C — article, with `==highlights==` + a `> [!annotation]`).
- **Rubric:** `assertions/eval-1-article.json` (A1–A12).

| config | pass_rate | passed |
|---|---|---|
| **with_skill** | **1.00** | 12 / 12 |
| without_skill (baseline) | **0.33** | 4 / 12 |
| **delta** | **+0.67** | **+8** |

## Discriminating assertions (skill passed, baseline failed)

A1 7-section Source Summary · A2 per-claim confidence+evidence tags · A3 繁中 body · A4 `agent_robin`/`original_author` provenance · A5 HITL gate before any write · A6 ≤5 concept / ≤3 entity caps · A7 plain-text related items (no dangling `[[links]]`) · A8 no `KB/Permanent/` writes + anti-laundering.

## Converged (both passed)

A9 full-text D-A · A10 content fidelity (numbers) · A11 annotation emphasis · A12 candidate quality.

## Reading

The skill's measured value is **KB discipline** — structure, provenance, the human-in-the-loop gate, and the no-dangling-links + Permanent red-lines — **not** raw reading comprehension (the baseline model reads the source fine). Concretely, the un-skilled baseline wrote permanent/concept notes immediately with no gate, claimed the human's authorship, used English, and littered the vault with dangling `[[wikilinks]]` — every failure mode this skill exists to prevent. The eval would catch a regression in any of A1–A8.

Transcripts are not committed (large + nondeterministic); the run is reproducible from the fixture + the prompts in `README.md`.
