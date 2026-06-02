# ADR-040 — 3-way panel integration matrix (2026-06-02)

Claude v1 = `docs/decisions/ADR-040-weekly-execution-layer.md`.
Codex audit = `2026-06-02-codex-adr040-audit.md` (verdict: **approve w/ modifications**).
Gemini audit = `2026-06-02-gemini-adr040-audit.md` (verdict: **reject — different architecture**).

The verdict gap is not about the features; it is about **where machine-writable
state lives**. Codex would patch ADR-040; Gemini would replace its red-line model
with a machine-owned folder outside `Journals/`. That is the keystone escalation.

## Matrix

| # | Topic | Claude v1 | Codex | Gemini | Pattern | Resolution |
|---|-------|-----------|-------|--------|---------|------------|
| 1 | **UFO = bare `mode:deep` tag** vs verified event | tag (A2) | tag, not 75-min verified; early-finish logs full block; enforce min or rename | store `{mode,planned,actual,completed}`; UFO *derived*; bare tag = un-auditable claim | **3-way** | **ADOPT** — richer timeEntry schema; UFO derived (duration≥threshold & completed); fix early-complete |
| 2 | **A1 "structured=machine / prose=human"** | governing lens | too coarse — `top3` is human judgment | **false dichotomy** — `plan[]`/`top3` are human *intent* that is structured; reframe to intent-vs-observation | **3-way** | **ADOPT** — reframe A1: *human-generated intent (machine persists, never selects)* vs *machine-generated observation* |
| 3 | **A4 project-top3 scoring "done/total OR Σ🍅"** | unresolved "or" | breaks D8 scoring invariant — pick one | (concurs) | **2-of-3** | **ADOPT** — choose ONE rule before impl |
| 4 | **A8 Daily carve-out** | second carve-out, on roadmap | reverses D6's explicit reject; move to its OWN ADR | dual-track splits one event across 2 files = data-integrity failure; model habit as ONE unified record | **3-way (both reject as-is)** | **ADOPT** — pull Daily OUT of ADR-040 (own future ADR); redesign habit as single unified record |
| 5 | **Where machine state lives** (THE fork) | `Journals/Weekly/` carve-out (A1/A3) | alt#1: consider Bridge-owned folder `BridgeData/Weekly/` | **reject carve-out** — machine state → `System/BridgeState/`; `Journals/` 100% human, machine-read-only | **2-of-3 vs Claude** | **ESCALATE 修修** — reopens ADR-039 D5 (which explicitly weighed + rejected this). His values call |
| 6 | **A7 task-body editing in Bridge** | yes (修修 asked) | alt#3: keep authoring in Obsidian via deep-link | replace A7 — fragments the writing surface; Bridge=control, Obsidian=prose | **2-of-3 vs Claude** | **ESCALATE 修修** — he explicitly wanted it; both auditors warn it fragments Obsidian |
| 7 | **UFO behavioral design** | anti-burnout metric | — | Goodhart's Law; 75-min arbitrary; 🍅-ceiling vs UFO-target = conflicting incentives | **single (Gemini)** | **ESCALATE 修修** — his personal metric; raise the conflicting-incentive point |
| 8 | **A3 doesn't name keys / update D5 allowlist** | targets in weekly fm | name keys, update D5 allowlist | — | single (Codex) | **ADOPT** — enumerate frontmatter keys |
| 9 | **Task-accuracy excludes daily `pomodoros[]`** (contra D3 union) | — | real inconsistency | — | single (Codex) | **ADOPT** — use union for task accuracy, or scope-document |
| 10 | **Daily `pomodoros[]` not category-filtered** → non-work inflates work🍅 | — | real pre-existing bug | — | single (Codex) | **ADOPT** — filter by task category in aggregator |
| 11 | **CJK NFC/NFD cross-device + wikilink resolution** | — | — | mac NFD vs VPS NFC → tasks "disappear"; CJK wikilink parsing | single (Gemini) | **EVALUATE** — indexer/writer already `NFC`-normalize stems; verify wikilink-resolve for Slice-2 top3; confirm if 修修 uses Mac |
| 12 | **A6 "Sunday Wall" monolithic ritual** | high-leverage single flow | define transactional state pre-file | break into composable non-blocking steps | 2-of-3 (different angles) | **ADOPT** — composable steps + honest `status` (no implied-complete) |

## Adopt now (non-controversial, fold into v2)
1, 2, 3, 4 (Daily→own ADR + unified habit record), 8, 9, 10, 12. Plus 11 (verify).

## Escalate to 修修 (values calls — block v2)
- **#5 Architecture**: `Journals/Weekly/` carve-out vs machine-owned folder vs hybrid. Determines whether we sign the red line AT ALL.
- **#6 Task-body authoring**: you asked for it; panel says it fragments Obsidian.
- **#7 UFO incentives**: long blocks (UFO) eat the 🍅 ceiling — conflicting goals.

## Notes
- Gemini misnamed itself "Gemini Pro 2.0" + guessed UFO = "Ultra-Focused Object" (it is just 修修's name for the 🤩 emoji) — cosmetic; its substantive catches (NFC, single-event habit model, intent-vs-observation, machine-owned folder) are sharp.
- Codex could not run pytest (not installed in its sandbox) — its audit is code-inspection-grounded; our 78 green tests stand separately.
