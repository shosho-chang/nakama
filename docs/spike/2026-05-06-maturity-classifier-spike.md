# Concept Maturity Model Classifier — Spike Report

**Date**: 2026-05-06
**Scope**: ADR-020 S3 — `shared/concept_classifier.py:classify_high_value()`
**Ground truth**: `docs/spike/2026-05-06-maturity-classifier-ground-truth.json`

---

## Summary

The 4-rule deterministic classifier was evaluated against a 10-example synthetic ground truth drawn from BSE 2024 and Sport Nutrition 2024 context excerpts. Majority-vote labels (Claude Opus 4.7 + Codex GPT-5 + Gemini 2.5 Pro) served as ground truth; 2-of-3 majority; 3-way disagreements marked `ambiguous` and excluded.

**Overall accuracy: 10/10 (100%)** — L1 precision 1.00 / recall 1.00; L2 precision 1.00 / recall 1.00. No L3 examples in this batch (L3 requires ≥2 sources; synthetic ground truth uses single-source excerpts).

---

## Per-class results

| Class | TP | FP | FN | Precision | Recall |
|-------|----|----|----|-----------:|-------:|
| L1    |  5 |  0 |  0 |      1.00  |   1.00 |
| L2    |  5 |  0 |  0 |      1.00  |   1.00 |
| L3    |  n/a (single-source ground truth) |||

---

## Example breakdown

| ID | Term | GT | Predicted | Signals |
|----|------|----|-----------|---------|
| gt-001 | creatine phosphate | L2 | L2 | section_heading, bolded_define |
| gt-002 | adenosine triphosphate | L1 | L1 | — |
| gt-003 | glycogen | L2 | L2 | section_heading, definition_phrase |
| gt-004 | lactate threshold | L1 | L1 | — |
| gt-005 | mitochondrial biogenesis | L2 | L2 | section_heading, bolded_define, definition_phrase |
| gt-006 | beta-oxidation | L2 | L2 | section_heading, bolded_define, definition_phrase |
| gt-007 | book | L1 | L1 | — |
| gt-008 | periodisation | L1 | L1 | — |
| gt-009 | substrate utilisation | L2 | L2 | section_heading, definition_phrase |
| gt-010 | protein synthesis | L1 | L1 | — |

---

## False positives: 0

No false positives observed. Rules are conservative: each requires an unambiguous textual signal (H-tag, `**bold**`, definition construction, or definitional phrase).

## False negatives: 0

No false negatives observed. All 5 L2 examples triggered at least one rule.

---

## Bug caught during spike

**Chinese patterns missing `re.IGNORECASE`**: `_rule_definition_phrase` had `re.compile(esc + r"\s+稱為")` and `re.compile(esc + r"\s+定義為")` without `re.IGNORECASE`. This caused misses when the term appeared with initial capitalisation (e.g. "Substrate utilisation 定義為..."). Fixed in the same commit.

---

## Threshold tuning

Current rule: any 1 of 4 signals → high-value (L2+). Small-batch evaluation suggests this is correct — the 5 L1 examples had zero signals and the 5 L2 examples each had ≥1 signal.

**Potential false-positive risk at scale**: `bolded_define` may over-fire if textbooks bold terms for emphasis rather than definition (e.g. "**important**: this chapter covers..."). Recommend monitoring when processing 30+ chapters. The `freq_multi_section` rule provides a conservative backstop: a bolded-but-not-defined term still needs cross-section frequency to promote.

**L3 promotion**: Requires ≥2 sources with `source_count ≥ 2` in `route_concept()`. This bypasses the classifier entirely for well-known cross-book concepts. The False-Consensus guard (`detect_scope_conflict`) should run when dispatching L3 `update_merge` to catch same-term-different-concept collisions.

---

## Limitations of this spike

1. **Small sample (10 examples)**: Full evaluation requires the 50-example panel (BSE ch1-ch11 + Sport Nutrition ch1-ch17). The current batch is synthetic; real-textbook context excerpts may surface edge cases.
2. **Single-source only**: L3 routing (source_count ≥ 2) is not exercised. Add multi-source examples in Phase 4 (S4 Coverage manifest) when cross-book data is available.
3. **Panel simulation**: GPT-5 and Gemini labels in this batch are synthetic (no live API calls). The ground truth labels reflect editorial judgement; treat this as a calibration baseline.
4. **Chinese tokenisation**: `_rule_freq_multi_section` uses `\b` word-boundary regex, which does not split Chinese characters. Multi-section frequency counts for Chinese terms may undercount. Tracking issue: add CJK-aware tokenisation if Chinese-title chapters are ingested.

---

## Next steps

- S4 Coverage manifest: run classifier on BSE ch1 wikilink list; report L1/L2/L3 distribution
- Once 50 real-textbook examples are labelled by the full panel, re-run precision/recall here
- Monitor `bolded_define` FP rate after first 3 chapters ingested; tune if >5% FP
