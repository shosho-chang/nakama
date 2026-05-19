# ADR-028 Panel Integration Matrix

**Date:** 2026-05-19
**Panel step:** 4 of 5 (multi-agent-panel skill)
**Inputs:**
- Claude v1 draft: `docs/decisions/ADR-028-vault-layout-consolidation.md` + `docs/VAULT-LAYOUT.md`
- Codex audit: `docs/research/2026-05-19-codex-adr028-audit.md`
- Gemini audit: `docs/research/2026-05-19-gemini-adr028-audit.md`

Adjudication of each distinct push-back point. Output drives ADR-028 v2 draft (step 5).

---

## Matrix

| # | Topic | Claude v1 stance | Codex stance | Gemini stance | Pattern | Resolution | Trace |
|---|---|---|---|---|---|---|---|
| **F1** | Concept page count | "644+ Concepts" | "2,762 actual count, fix" | (didn't address) | single-source Codex (factual) | **adopt** — fix to 2,762 in ADR + VAULT-LAYOUT | Codex §3 |
| **F2** | Files/ split count | "38 paper figs + 31 journal" | "35 + 34, zero overlap" | (didn't address) | single-source Codex (factual) | **adopt** — fix to 35/34 | Codex §3 |
| **F3** | Referring source counts | "9 papers / 3 inbox" | "6 papers / 3 inbox / 7 journals" | (didn't address) | single-source Codex (factual) | **adopt** — fix counts | Codex §3 |
| **F4** | Inbox top-level count | "3 .md files" | "17 files (7 .md + 8 .epub + 2 .mhtml)" | (didn't address) | single-source Codex (factual) | **adopt** — fix to 17 | Codex §3 |
| **F5** | File path: `gateway/handlers/nami.py:1002` | "Nami/Notes write" | "wrong; that's TaskNotes/Tasks. Real contract at :458-525, :1773, `vault_rules.py:14-20`" | (didn't address) | single-source Codex (factual) | **adopt** — correct citations | Codex §1 |
| **F6** | File path: `shared/kb_writer.py:475` | "Entity v1 schema codified here" | "wrong; that's `_append_to_section`" | (didn't address) | single-source Codex (factual) | **adopt** — fix Drift D2 line ref | Codex §1 |
| **F7** | File path: `pubmed_digest.py:477,522` | "writes KB/Raw/Papers" | "wrong; writes `KB/Wiki/Sources/pubmed-{pmid}` + `Digests/PubMed/`" | (didn't address) | single-source Codex (factual) | **adopt** — fix producer matrix | Codex §1 |
| **F8** | ADR-017 v3 citation | "v1/v2/v3 discriminated union" | "ADR-017 documents v1/v2 only; v3 is later" | (didn't address) | single-source Codex (factual) | **adopt** — narrow citation | Codex §2 |
| **F9** | seo-audit/ tree placement | tree puts under `franky/`, text corrects to `brook/` | "fix the tree, not just the note" | (didn't address) | single-source Codex | **adopt** — fix the ASCII tree | Codex §2 |
| **F10** | Missing mapping table | ADR §141 promises `§migration-files-category-a` table | "section doesn't exist" | (didn't address) | single-source Codex | **adopt** — add table to VAULT-LAYOUT.md | Codex §3 |
| **F11** | Audit script returns `[]` | "skeleton stub OK for now" | "by construction reports 'no drift', misleading" | (implied agreement) | 2-of-3 | **adopt** — add explicit "STUB; returns false-negative; full impl Phase 3 PR-10" header to audit script + ADR `[待修]` entry | Codex §1 |
| **S1** | VAULT-LAYOUT.md status mislabel | `Status: Active (post-ADR-028)` | "false — Phase 3 not done. Either current-state truth OR label target-state" | "section heading contract is also brittle (G1)" | 2-of-3 (different angles) | **adopt** — change to `Status: Target (post-ADR-028 Phase 3); current state has drift entries below`. Add explicit drift table for unmigrated state | Codex §1, §5 |
| **S2** | PR-Promotion-Attachment-Fix sequencing | PR-8 (late) | "must be PR-1; cleaning Inbox today silently breaks image refs" | (concurs in §0 preamble) | 2-of-3 (universal really) | **adopt** — re-sequence Phase 3 PR list, make this PR-1 | Codex §4, Gemini preamble |
| **S3** | Promotion fix scope | only `shared/promotion_commit.py` | "also `agents/robin/agent.py:105-129` (legacy path with `shutil.copy2` + `unlink`)" | (didn't address) | single-source Codex | **adopt** — expand fix scope, both paths | Codex §1, §4 |
| **S4** | Journals red-line exception breadth | "applies retroactively to any future similar mechanical rewrite" | "too broad, creates precedent. Narrow to one-time ADR-028 §8 Files/ Cat B" | (didn't address) | single-source Codex | **adopt** — narrow exception text | Codex §4 |
| **S5** | Files/ delete verification | "delete when empty" | "needs manifest with pre/post hash + dry-run + recycle-bin semantics" | (didn't address) | single-source Codex | **adopt** — add verification + rollback plan to PR-Files-Cleanup | Codex §4 |
| **S6** | News Coo FSA re-pick transition | "one-time manual" | "incomplete — open popups hold old handle. Add transition plan + Robin dual-read old+new for one release" | (didn't address) | single-source Codex | **adopt** — add transition plan to PR-Inbox-Restructure | Codex §4 |
| **M1** | Marker convention syntax | positional `%%agent-{agent}-{section}-start/-end%%` | "use YAML-in-comment, carry schema_version/updated_at metadata" | (didn't address; but G1 raises adjacent concern) | single-source Codex (alternative) | **mod** — keep positional syntax (simpler grep, less parser surface) but add `schema_version` in a leading frontmatter block at marker site. Document deprecation path | Codex §5 |
| **M2** | Human-only section contract | literal heading text (`## 專案描述`, etc.) | (didn't address) | "brittle — emoji prefix or synonym change silently breaks audit. Use `<!-- vault:human-only-section -->` marker" | single-source Gemini | **adopt** — high-value catch. Refactor §10 to use HTML-comment marker as authority, heading text is human-facing only | Gemini §1 |
| **M3** | Unicode normalization risk | (not addressed) | (not addressed) | "macOS NFD vs Windows/Linux NFC for CJK filenames; Syncthing usually OK but byte-compare scripts break" | single-source Gemini | **mod** — add to known drift §7 as "D-unicode-norm" `[已接受]` risk with mitigation guidance (`unicodedata.normalize('NFC', path)` in audit script + any byte-compare code) | Gemini §1 |
| **A1** | KB/ rename to Resources/ | "Strength 1: keep KB/" | "right call, do not rename — 2,762 pages + 73 code refs" | "cognitive cost long-term; acknowledge as strategic trade-off, not pragmatic win" | 2-of-3 (keep, but Gemini wants honesty) | **mod** — keep `KB/` as decided; add explicit "Considered Options" rejection note acknowledging the long-term cognitive trade-off Gemini raised. Reserve rename for future ADR if friction materializes | Codex §4, Gemini §2 |
| **A2** | Defer Areas/ vs create now | "no Areas/ folder (Strength 1)" | "defer until OKR grill" | "create now even if sparse; deferring compounds debt" | direct contradiction Codex vs Gemini | **escalate** — owner judgment call. **Default recommendation: hold on Areas/ until OKR grill** (consistent with §1 "OKR/Tasks is another grill session") but ADR explicitly notes Gemini push-back as open issue for OKR session | Codex §4, Gemini §3 |
| **A3** | 10-PR Phase 3 plan | 10 atomic PRs | "10 is true count" (passive accept) | "operationally fragile, migration fatigue risk. Consolidate to 3-4 phases" | single-source Gemini (architectural) | **mod** — keep 10-PR ATOMIC LANDING BLOCKS for clean review, but group them into 3 named **phases** with go/no-go gates: Phase A (Code Prep: PRs 1[promotion-fix], 7[markers], 9[code-paths]) → Phase B (Bulk Migration: PRs 2-6 as single "big bang" branch) → Phase C (Activation: PRs 10[vault CLAUDE.md], 11[audit script full]). Each phase has rollback procedure | Gemini §5 |
| **A4** | Multimodal blind spot | not addressed | not addressed | "video/audio/Premiere/DaVinci files — vault is silent. Add Projects/{slug}/assets.md pointer convention" | single-source Gemini | **adopt** — high-value catch. Add VAULT-LAYOUT.md §primary-media-assets with pointer convention (`Projects/{slug}/assets.md` → external NAS / cloud paths); explicitly NOT in vault scope but documented as system boundary | Gemini §3 |
| **A5** | Knowledge decay / orphan detection | not addressed | not addressed | "Knowledge Gardener — orphan concept detection, stale page surfacing" | single-source Gemini | **mod** — too big for ADR-028 scope. Add to ADR-028 "Open follow-ups" + create separate research stub. Audit script (Phase 3 PR-11) gets a `--graph-health` flag stub | Gemini §2 |
| **A6** | `_alias_map.md` lifecycle | "preserved + documented" | "verified producers exist (`shared/concept_classifier.py`, `scripts/run_s8_preflight.py`)" | "lifecycle underspecified — writers, conflict resolution, removal" | 2-of-3 agree more docs needed | **adopt** — expand VAULT-LAYOUT.md §_alias_map subsection with producers + conflict resolution + removal protocol. Trace Codex's verified file paths | Codex §2, Gemini §4 |
| **A7** | Attachment storage scheme | `KB/Attachments/{source-slug}/` flat | (didn't address) | "consider content-addressable `Attachments/by-hash/{sha256}.png` to prevent duplication when sources share figures" | single-source Gemini (alternative) | **reject for now** — adds significant complexity. Real-world dup rate likely <1% in this corpus (each paper has its own figures). Reserve as future ADR if dup surfaces. Document rejection reasoning in ADR §Considered Options | Gemini §4 |
| **A8** | AgentOutputs durability split | "all in vault under AgentOutputs/" | "Franky weekly + audits + dev-backlog fail your own heuristic (Nakama-砍掉重寫-no-meaning) → move to repo `docs/reports/` or `data/ops/`" | (didn't address) | single-source Codex | **adopt** — partial. Split as: `AgentOutputs/{nami,brook}/` in vault (life/work-relevant); `data/agent_reports/{franky}/` in repo or `data/` (system-relevant). Re-route Franky reporter + dev-backlog to repo path. **This is a meaningful divergence from Q6 grill decision (alpha consolidation in vault) — needs owner sign-off** | Codex §5 |
| **A9** | Inbox β capture friction | "Inbox/{web,books,papers,snapshots}/ pre-sorted" | (didn't push back) | "increases capture friction (which bucket at moment of capture?). Use flat Inbox/ + frontmatter routing" | single-source Gemini | **escalate** — owner already chose β over α in Q7 grill (recorded as "Inbox 結構 = β"). Gemini's push-back has real merit but conflicts with grill outcome. **Default: keep β** (owner ergonomics call) but add ADR note that Gemini's flat-Inbox alternative was raised post-grill and can be revisited if capture friction is observed in practice | Gemini §5 |
| **A10** | Heuristic subjectivity | "if Nakama rewritten, still meaningful = vault" | (didn't address) | "language-dependent judgment call; document as recurring discussion not algorithm" | single-source Gemini | **adopt** — small editorial change. Add note in VAULT-LAYOUT.md §5 that heuristic requires human judgment; provide 2-3 worked examples (current 3 dev-artifact relocations) as precedent | Gemini §1 |

---

## Resolution tally

- **adopt** (verbatim incorporate): F1-F11, S1-S6, M2, A4, A6, A10 = **20 items**
- **mod** (partial / different framing): M1, M3, A1, A3, A5 = **5 items**
- **reject** (push-back declined, document reason): A7 = **1 item**
- **escalate** (owner judgment required, default direction noted): A2, A8, A9 = **3 items**

Total distinct push-back points: **29**.

## Items requiring owner adjudication (escalations)

1. **A2 — Areas/ folder timing**: Codex says defer to OKR grill; Gemini says create now. Default direction = defer. Owner can override.
2. **A8 — Franky outputs in vault vs repo**: Codex says Franky weekly + dev-backlog fail the heuristic and belong in repo. Default direction = move to repo `data/agent_reports/franky/`. Owner already chose α (full vault consolidation) in Q6 grill — Codex's audit reveals a self-inconsistency. **Highest-impact escalation.**
3. **A9 — Inbox β vs α (flat)**: Owner chose β in Q7. Gemini raises capture-friction concern. Default direction = keep β. Owner can revisit if friction observed.

## Items adopted but require careful integration

- **S2** (PR-Promotion-Attachment-Fix to PR-1) cascades through Phase 3 numbering — every subsequent PR shifts.
- **A3** (10 PRs → 3 phases) changes the ADR's §Consequences structure significantly.
- **M2** (HTML-comment marker for human-only sections) needs an update to `scripts/vault_layout_audit.py` skeleton signatures.
- **A8** (if owner adopts default) needs `agents/franky/reporter.py:277` and `agents/franky/agent.py:33` to point at repo, not vault — meaningful change to Phase 3 PR-Codebase-Path-Update.

---

## Sanity check

- **Universal-agreement items rejected?** None. F1-F11 (all factual) adopted.
- **Single-source items adopted blindly?** No — each Gemini-only / Codex-only item has an evaluation. A7 rejected with stated reason.
- **Contradictions auto-resolved?** A2 (Codex vs Gemini on Areas/) is escalated — not auto-resolved.
- **Trace gaps?** Each row cites audit section.

---

## Next step

Build ADR-028 v2 + VAULT-LAYOUT.md v2 incorporating all `adopt` + `mod` resolutions. Present to owner with:

1. Summary of v1 → v2 changes (this matrix)
2. The 3 escalation items requiring owner adjudication
3. Confidence levels: factual fixes (F1-F11) = high (verified by file system); architectural calls (A1-A10) = medium-high; escalations = open

After owner sign-off, commit v2 + push to PR #611 for re-review.
