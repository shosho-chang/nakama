# ingest skill — evals

Two evals, two distinct jobs:

## 1. `evals.json` — triggering eval (routing only)
13 `{prompt, should_trigger}` cases: does the skill description fire on the right prompts (「ingest 這篇」「/ingest <slug>」) and stay quiet on near-misses (kb-search / 每日回顧 link-writing / Brook rewrite / route B/E book+video)? Tests the **description boundary**, not output quality.

## 2. `quality.json` — quality eval (output grading) ← measures "is the ingest actually good"
Grades the QUALITY of ingest output against a typed rubric, as a **with_skill vs without_skill A/B**. The pipeline's quality is LLM-driven (summary generation, concept/entity extraction, the HITL judgement), so quality is graded by an **LLM judge on real output** — it cannot be asserted deterministically.

```
evals/
├── evals.json                     # triggering eval (routing)
├── quality.json                   # quality eval cases → assertions + fixtures
├── fixtures/
│   └── intermittent-fasting-longevity.md   # golden article (highlights + annotation)
├── assertions/
│   ├── eval-1-article.json         # route-C rubric A1–A12
│   └── eval-2-refusals.json        # R1–R3: refuse content-filter + Permanent link, no over-refusal
├── iteration-1/                    # latest baseline run (grading + benchmark)
│   ├── benchmark.md
│   └── eval-1-article/grading.json
└── README.md
```

### The rubric (eval-1, A1–A12)
Structure (A1 7 sections, A2 per-claim confidence+evidence tags), language (A3 繁中 + preserved terms), provenance (A4 `agent_robin`/`original_author`), workflow (A5 HITL gate, A9 full-text D-A), filtering (A6 ≤5 concepts / ≤3 entities), formatting (A7 plain-text, no dangling `[[links]]` — ADR-043 / PR #955), red-lines (A8 no Permanent writes + anti-laundering), content (A10 number fidelity, A11 annotation emphasis, A12 candidate quality).

### How to run the A/B
For each case, run the **same prompt twice**:
1. **with_skill** — the ingest skill loaded (drives `IngestPipeline` per `SKILL.md`).
2. **without_skill** — no skill; a baseline agent given the raw fixture + 「幫我 ingest 進 KB」.

Then an **LLM judge** grades each output against the case's assertions — *no partial credit*, *burden of proof on the assertion*, *evidence required per verdict* — producing a per-config `pass_rate`. The skill should beat the baseline; the gap is the skill's measured value. (This mirrors the Anthropic skill-creator executor→grader→benchmark flow. That tooling is not wired into this repo's CI, so the A/B is run manually / on demand; the prompts used for iteration-1 are recoverable from this repo's git history of the run.)

### Baseline (iteration-1, 2026-06-28)
**with_skill 1.00 (12/12) vs without_skill 0.33 (4/12)** — delta **+8** assertions. Discriminators A1–A8 (the KB discipline). The un-skilled baseline read the source fine (A9–A12) but skipped the gate, claimed the human's authorship, wrote in English, and created dangling `[[wikilinks]]` — every failure mode the skill prevents. Details in `iteration-1/benchmark.md`.

## CI guard (deterministic)
`tests/agents/robin/test_ingest_eval_spec.py` validates this spec stays well-formed (quality.json parses; fixtures + assertions files exist; assertion ids unique + typed; the route-C rubric encodes the repo-specific invariants — provenance, HITL, no-dangling-links, red-lines). The LLM-judge quality grading is **manual/opt-in** (nondeterministic + costs tokens), so it is not a CI gate. The pipeline's deterministic structural/red-line invariants are separately gated by `test_ingest_route_c.py`.
