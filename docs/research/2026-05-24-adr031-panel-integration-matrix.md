# ADR-031 Panel Integration Matrix

**Date:** 2026-05-24
**Panel:** Claude Opus 4.7 (v1 author) · Codex GPT-5 (audit) · Gemini 2.5 Pro (audit)
**Verdict synthesis:** Both auditors say **Approve with modifications**. The architecture (Web-first; vault as SoT; 7-tab stage gate; 2 expert personas; Web-self Pomodoro) is sound. The modifications are in **factual claims, prompt design, and a few schema choices**.

---

## How to read this matrix

- **Universal (3-way agreement)** = all three viewpoints converge — high-confidence, adopt verbatim.
- **2-of-3** = two of three pushed back the same way — adopt with note.
- **Single-source unique insight** = one auditor caught something the other(s) missed — evaluate on merit.
- **Direct contradiction** = auditors disagree; needs adjudication.

Audit text:
- [`2026-05-24-codex-adr031-audit.md`](2026-05-24-codex-adr031-audit.md)
- [`2026-05-24-gemini-adr031-audit.md`](2026-05-24-gemini-adr031-audit.md)

---

## Adopted

### 1. Content-type slim (D9.c) — DO NOT remove `research`
| | |
|---|---|
| **Pattern** | Universal (Codex caught with hard evidence; Gemini did not refute) |
| **Codex** | "`蛋白質攝取量.md` has `content_type: research` at line 3. ADR D11 'No old content_type breaks' is false." + "Existing tests `tests/test_lifeos_writer.py:285-287` assert `blog`, `research`, `podcast` defaults." |
| **Adopt** | Revert D9.c: `ContentType` keeps `Literal["youtube", "blog", "research", "podcast"]`. Update D11 to document `蛋白質攝取量.md` will retain `content_type: research` post-migration. Schema doc updates accordingly. |
| **Code action** | Revert `shared/lifeos_writer.py` to 4 content-types (done in PR1). Update test_lifeos_writer.py to assert Tier C minimal template (done in PR1). |

### 2. Schema doc `Status: Active` overclaim
| | |
|---|---|
| **Pattern** | 2-of-3 (Codex explicit; Gemini implicitly via "future state confusion") |
| **Codex** | "Schema doc names nonexistent active code: `bridge_projects.py` at schema line 5 and `shared/project_indexer.py` at line 6." |
| **Adopt** | Change schema doc status to `Active (PR1 bundle, pending merge)`. Add "These modules land in PR1" annotation next to each consumer reference. |

### 3. Migration must handle BOTH marker families
| | |
|---|---|
| **Pattern** | Universal — direct evidence (live `肌酸的妙用.md` had `%%KW-START%%`/`%%KW-END%%`) |
| **Codex** | "Live `肌酸的妙用.md` uses older `%%KW-START%%` / `%%KW-END%%` markers at lines 296 and 375. Migration must handle both marker families." |
| **Adopt** | `_LEGACY_MARKER_BLOCKS` in migration script already covers both (done in PR1). |

### 4. Hook math: 75-200 字/min wrong; should be 100-250 字 / ≤300 字 cap
| | |
|---|---|
| **Pattern** | 2-of-3 same conclusion via different speaking rates (Codex: 75-200/min; Gemini: 200-300/min) |
| **Codex** | "At 75-200 字/min, 30-60 seconds is 37.5-200 字. 500 字 is 2.5-6.7 minutes. Change cap to ≤200 字, OR label 500 as absolute storage cap." |
| **Gemini** | "Taiwanese Mandarin 200-300 字/min. 30-60s = 100-300 字. ≤500 字 too loose; ≤300 字 better." |
| **Adopt (Gemini's numbers)** | Use **100-250 字 spoken range** + **≤300 字 soft cap** (Gemini's grounded rate, midpoint). Document the math + audit ranges in v2 ADR D5 + schema doc + hook tab UI counter. |
| **Code action** | Update `_tab_hook.html` placeholder + counter target to 300 char (done in PR1 v1.1; was 500). Update schema doc. |

### 5. Pomodoro write rate: "on completion / manual +1" not "each timer tick"
| | |
|---|---|
| **Pattern** | Single-source (Codex) but verified true |
| **Codex** | "Schema line says `pomodoro.*` is recomputed on 'each timer tick,' which would be high-frequency and should not hit vault frontmatter. Fix to 'on completion/manual +1/save.'" |
| **Adopt** | Schema doc §4 "writes on each timer tick" → "writes on completion / +1🍅 / save". Behavior already correct in code (`_write_pomodoro_entry` called only from `/timer/complete` + `/manual-pomodoro` routes, never per-tick). |

### 6. Filter on `type: project`, not name heuristic, for skipping meta files
| | |
|---|---|
| **Pattern** | Universal (Codex explicit; Gemini's "implicit contract" pushback aligns) |
| **Codex** | "Skipping `Brook 風格訓練.md` is correct, but not because the filename starts with 'Brook.' It is correct because frontmatter says `type: agent-workspace`. The indexer and migration should filter on `type: project`, not name heuristics." |
| **Adopt** | Already done in PR1 (`ProjectIndexer._entry_from_path` filters `fm.get("type") != "project"`). Migration script does same check. The `--skip-meta` flag is removed from the migration script (the type check already covers it; no name heuristic). |
| **Code action** | Remove `--skip-meta` flag from migration script + ADR D9.e text. |

### 7. Persona prompts need scoring rubric + few-shot example
| | |
|---|---|
| **Pattern** | Single-source (Gemini) but high-merit |
| **Gemini** | "The decision to use pure description (zero-shot) for the personas is a major missed opportunity. Prompts should include (a) scoring rubric (1-5 definitions) and (b) one-shot or few-shot example of a good review." |
| **Adopt** | Defer to PR2 (where personas land); v2 ADR adds explicit requirement under D8 that PR2 prompts ship with rubric + 1+ few-shot examples. PR2 acceptance gate. |

### 8. Persona prompt — add Traditional Chinese leakage guard
| | |
|---|---|
| **Pattern** | Single-source (Gemini) — known Sonnet 4.6 failure mode |
| **Gemini** | "Add direct instruction: 「請務必使用台灣慣用的正體中文回覆，避免使用簡體字或中國大陸用語。」" |
| **Adopt** | v2 ADR D8 persona prompts gain this instruction. PR2 prompts ship with it. |

### 9. Hook timer needs explicit `## Example Review` for persona prompts (PR2)
| | |
|---|---|
| **Adopt** | Per #7; PR2 scope. |

### 10. Soft gate too weak — needs persisted "Publish anyway" decision
| | |
|---|---|
| **Pattern** | Single-source (Codex) but high-stakes (past discipline drift is exactly the named failure mode) |
| **Codex** | "A dismissible toast just recreates the old failure mode with nicer UI. Persist the 'Publish anyway' decision." |
| **Adopt with modification** | v2 ADR D4: pre-publish banner shows incomplete tabs. Publish button changes label to "Publish anyway (X tabs incomplete)" when stage-gate ○ exists. Click writes a `publish_decision` log entry to `state.db api_calls.scope_json` with `{decision: published_with_incomplete, incomplete_tabs: [...]}`. Future PR may surface a "weekly publish-with-incomplete count" in Bridge ops. |
| **Code action** | Deferred to PR2 (single-call refactor moment — minor follow-up). PR1 keeps current toast; v2 ADR documents PR2 must add. |

### 11. Hook math + cap label fix in schema doc + ADR
| | |
|---|---|
| **Adopt** | Per #4 above. |

---

## Adopted with significant modification

### 12. Reviews schema: dict-per-persona → list-of-versioned-objects (Gemini)
| | |
|---|---|
| **Pattern** | Single-source (Gemini); 修修 prompt-iteration use case validates the value |
| **Gemini** | "Overwriting reviews destroys data for prompt engineering. Use list with prompt_version. UI displays latest." |
| **Adopt (PR2 only)** | v2 ADR D5 schema: `reviews.{persona}: list[{run_at, prompt_version, score, summary, suggestions}]`. UI shows latest. Indexer in PR1 implements **dual-shape tolerance** (read dict OR list-of-dicts) so PR2 can flip without breaking. |
| **Code action** | PR1 indexer tolerates both dict + list shapes (small adjust); PR2 ships list-only schema in code paths. |

### 13. `append_timeentry` mtime/hash conflict detection (Codex)
| | |
|---|---|
| **Pattern** | Single-source (Codex); Syncthing two-writer scenario |
| **Codex** | "Last-write-wins too casual for Syncthing plus two writers. Read + preserve unknown keys + abort on mtime/hash mismatch. Also preserve optional `description`." |
| **Adopt with modification** | PR1 already preserves unknown frontmatter keys (`_read_split` + deep merge). PR1 already updates `dateModified` (in `_now_iso_z`). **mtime guard deferred to PR2** — single-user vault has near-zero concurrent-edit probability per [`user_vault_edit_pattern_no_concurrent`](../../memory/claude/user_vault_edit_pattern_no_concurrent.md). v2 ADR documents this trade-off and the PR2 follow-up. |

### 14. TaskNotes plugin contract — formalize via schema file (Gemini)
| | |
|---|---|
| **Pattern** | Single-source (Gemini); aligns with "explicit contracts" principle |
| **Adopt deferred** | v2 ADR §Out-of-scope: TaskNotes frontmatter schema validation is a known follow-up. PR1 ships with informal contract (matches observed plugin shape). Frontmatter validation lands when first plugin schema break causes regression. |

---

## Rejected with rationale

### 15. Codex "alt 3: Hybrid Obsidian — leave simple markdown buttons that link to Bridge URL"
| | |
|---|---|
| **Pattern** | Single-source (Codex); not adopted |
| **Reject** | The reason 修修 wants Web-first is that Obsidian is the failure mode (crash) — adding "any" interactive affordance to the md body keeps the Syncthing reparse footprint and tempts adding more. Tier C is a clean separation: Obsidian = read+prose-edit; Web = interactive. The migration script DOES preserve all human-only sections as plain markdown, so the Obsidian read surface remains functional. No "simple markdown buttons" added. |

### 16. Codex "alt 2: persist timer session state across reload"
| | |
|---|---|
| **Reject for PR1** | Documented as known v1 limitation per ADR Negative §Web Pomodoro non-persistent. Triggers v2 polish only if 修修 reports friction. SessionStorage covers within-tab; across-reload would require server-side timer state (`state.db`) which adds non-trivial schema. |

### 17. Gemini "alt: Single LLM call with both personas, structured output"
| | |
|---|---|
| **Reject for v1** | Two separate calls = per-persona re-run granularity (per ADR D8 rationale). Gemini's case for single-call is valid but trade-off doesn't favor the single-call path at owner's iteration cadence. PR2 may revisit after live usage. |

### 18. Gemini "Bridge availability SPOF — bundle Electron app for offline"
| | |
|---|---|
| **Reject** | Out of scope. Mitigation already exists: vault md is the SoT; Obsidian works offline for read + prose edit. Only Web-driven mutations (timer auto-tick, LLM review dispatch, frontmatter quick-edit forms) are blocked when Bridge is down. Manual frontmatter edit in Obsidian remains a valid escape hatch. v2 ADR documents this trade-off explicitly. |

### 19. Codex "do not add ungrounded Fact-checker persona"
| | |
|---|---|
| **Already aligned** | v1 ADR explicitly defers fact-checker per D8 §"Why no third persona". Codex's pushback verifies the rationale; no change. v2 ADR §D8 strengthens the language: "Fact-checker will not ship without source-grounding harness." |

### 20. Gemini "Add 👍/👎 buttons to LLM review UI for prompt-quality feedback"
| | |
|---|---|
| **Defer to PR3+** | Valuable signal but adds UI complexity. v2 ADR notes PR3 backlog item. |

---

## Verification of adoptions (PR1 status)

| Adoption | Status in PR1 |
|---|---|
| #1 content-type 4 retained | ✓ code reverted, tests updated |
| #2 schema doc status | ✓ updated to "Active (PR1 bundle, pending merge)" in v2 doc |
| #3 dual marker handling | ✓ migration script handles both |
| #4 hook ≤300 字 + 100-250 字 spoken | ✓ tab template + counter target updated |
| #5 pomodoro write rate language | ✓ schema doc reworded |
| #6 type filter, no name heuristic | ✓ `--skip-meta` flag removed |
| #7+#8 persona prompts (rubric + few-shot + zh-Hant guard) | → PR2 scope |
| #10 publish-anyway persistence | → PR2 scope |
| #12 reviews list-of-objects | → PR2 schema; PR1 indexer dual-shape tolerant |
| #13 mtime guard | → PR2 scope |
| #14 TaskNotes schema | → known follow-up issue |
