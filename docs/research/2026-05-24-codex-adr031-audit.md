**1 — Code Grounding**

Most referenced repo paths exist, but ADR-031 overstates several implementation facts.

`shared/lifeos_writer.py:21` is correctly cited: `ContentType = Literal["youtube", "blog", "research", "podcast"]`. `DEFAULT_TASKS` is at `shared/lifeos_writer.py:28`, and the code still supports all four content types. Existing tests also encode that contract: `tests/test_lifeos_writer.py:285-287` assert `blog`, `research`, and `podcast` defaults, and `tests/test_gateway_handlers.py:180/227` exercise Nami creating a `research` project. Dropping `research` is therefore not a documentation-only cleanup; it is a breaking code change.

`shared/lifeos_templates/project_youtube.md.tpl` matches the crash-prone claim. The base block starts at `project_youtube.md.tpl:9`, dataviewjs blocks appear at `:48`, `:86`, and `:168`, and Zoro markers are emitted at `:277-278`. The ADR’s “strip dataviewjs/base/markers” direction is technically grounded.

`shared/digest_indexer.py` exists and is a valid D2 precedent. `thousand_sunny/routers/bridge_digests.py:37` imports it, and `/bridge/digests/ask` is implemented at `bridge_digests.py:145-153`.

`thousand_sunny/routers/projects.py` exists, but ADR-031’s namespace description is slightly stale. The current file defines API routes under `/api/projects` at `projects.py:45`; legacy `/projects/{slug}` redirects at `projects.py:263`; the actual Brook page is `/brook/projects/{slug}` at `projects.py:269`.

The claimed new Tier C implementation is not present. `thousand_sunny/routers/bridge_projects.py`, `shared/project_indexer.py`, `shared/project_writer.py`, `shared/project_reviews.py`, and `scripts/migrate_projects_to_tier_c.py` do not exist. That is fine for a Proposed ADR, but the companion schema should not present these as active consumers.

The live vault check is the major correction. `E:\Shosho LifeOS\Projects` has 3 files, but not “all youtube”:

- `肌酸的妙用.md`: `content_type: youtube` at line 3.
- `蛋白質攝取量.md`: `content_type: research` at line 3, plus `target_date:` at line 10.
- `Brook 風格訓練.md`: not a project at all; `type: agent-workspace` at line 2, `agent: brook` at line 3, `total_posts: 192` at line 7.

So ADR-031 D11’s “vault scan confirmed zero `blog`/`research` projects” is false.

**2 — Drift Detection**

ADR-031 mostly extends ADR-030 coherently: vault markdown remains canonical, Bridge becomes the interactive surface, and D2/D3 are reused. I agree with the high-level Tier C direction.

The drift is in specifics:

- ADR-031 D9.c says slim to `Literal["youtube", "podcast"]`, but live vault has `蛋白質攝取量.md` as `research`. This contradicts D11’s “No old content_type breaks.”
- PR1 acceptance says `/bridge/projects` should list 2 projects post-migration, but D9.c says the migration script should warn and skip `research`. With current vault facts, PR1 would list only `肌酸的妙用.md` unless `research` is retained or explicitly converted.
- The schema doc says `Status: Active` at `docs/schemas/project-frontmatter-nested.md:3`, while ADR-031 is `Status: Proposed` at `docs/decisions/ADR-031-project-workspace-migration.md:4`. It also names nonexistent active code: `bridge_projects.py` at schema line 5 and `shared/project_indexer.py` at line 6.
- ADR-031 says Project pages no longer emit `%%agent-zoro-keywords-*%%`; current template still emits them at `project_youtube.md.tpl:277-278`, and the live `肌酸的妙用.md` uses older `%%KW-START%%` / `%%KW-END%%` markers at lines 296 and 375. Migration must handle both marker families.
- ADR-029’s 5-slot nav claim is internally consistent with current `_chassis_nav.html`: comment at `thousand_sunny/templates/bridge/_chassis_nav.html:25` lists `bridge / drafts / digests / seo`, with Fleet and Ops as dropdowns. There is no PROJECTS slot yet, so ADR-031 D2 is prospective, not implemented.

**3 — Numerical / Factual Claims**

The “5-50 KB markdown blob” claim is directionally true for `肌酸的妙用.md`, but the actual observed case is at the low end. The file is 18,512 bytes total; body after frontmatter is about 17,954 characters. The persisted Zoro `%%KW-START%%` block spans lines 296-375 and measures about 7,370 characters by PowerShell measurement. So “5-50 KB” is plausible as a range, but should be rewritten as “observed current blob ~7 KB; expected range 5-50 KB.”

“~600 tasks vault-wide” is false in the accessible vault. `E:\Shosho LifeOS\TaskNotes\Tasks` contains 11 markdown files recursively, not ~600.

The Pomodoro write-rate claim is half-right, half-hand-wavy. ADR-030 D4 says high-frequency mutation goes to `state.db` at `docs/decisions/ADR-030-vault-as-substrate-read-strategy.md:125`, but it does not define a numeric threshold. One append per 25 minutes per active task is probably acceptable for vault files. But schema line `docs/schemas/project-frontmatter-nested.md:165` says `pomodoro.*` is recomputed on “each timer tick,” which would be high-frequency and should not hit vault frontmatter. Fix the schema to “on completion/manual +1/save,” never per-second ticks.

The hook arithmetic is wrong. Q8 claims `≤500 字` supports 30-60 seconds at 75-200 字/min. At 75-200 字/min, 30-60 seconds is 37.5-200 字. 500 字 is 2.5-6.7 minutes. Keep the owner’s 30-60s habit, but change the soft cap to `≤200 字`, or label 500 as an absolute storage cap, not a duration-derived cap.

**4 — Assumption Push-Back**

The biggest weak assumption is that content types can be slimmed because no `research` exists. Evidence says the opposite. Do not ship D9.c as written. Either keep `research` as a legacy/read-only content type, or explicitly migrate `蛋白質攝取量.md` with owner approval.

The “soft gate + remind” model is philosophically consistent with ADR-031, but a toast is too weak if the known failure mode is discipline drift. Do not block publishing, but require an explicit “Publish anyway” acknowledgment when prerequisites are incomplete and persist that decision. A dismissible toast just recreates the old failure mode with nicer UI.

“No version history of reviews in frontmatter” is correct for frontmatter, but the proposed fallback is insufficient. `state.db api_calls` records model/token/scope metadata; it does not store the review output. If LLM reviews overwrite `reviews.{persona}`, old critique content is gone. Add `project_review_runs` in `state.db` or a JSONL audit file keyed by project/persona/run_at.

Web-self Pomodoro is the right direction; driving the Obsidian plugin from Bridge is unrealistic. But last-write-wins is too casual for Syncthing plus two writers. `append_timeentry` must read current file, preserve unknown keys, append only the new entry, update `dateModified`, and abort on mtime/hash mismatch. Also preserve optional `description`; a real TaskNotes sample includes `description: Work session`.

Two personas are reasonable. I agree with rejecting style mimic and with separating Storyteller from Writing Coach. But for health content, “Fact-checker deferred” leaves a real risk. Do not add an ungrounded fact-checker persona. Add a source-grounded evidence check later that refuses to run without citations/sources.

Skipping `Brook 風格訓練.md` is correct, but not because the filename starts with “Brook.” It is correct because frontmatter says `type: agent-workspace`. The indexer and migration should filter on `type: project`, not name heuristics.

**5 — Alternatives Not Considered**

First, keep `research` as legacy-supported. The better PR1 move is `ContentType = Literal["youtube", "research", "podcast"]` or retain all four until a separate cleanup ADR. Tradeoff: slightly more code, but no silent breakage of live vault state, Nami tests, and bootstrap CLI examples.

Second, persist timer session state across reload. Use browser `localStorage` for active timer start time plus a server-side mtime guard when completing. Tradeoff: more edge-case handling, but reloads and laptop sleep are normal, not exotic.

Third, use a hybrid Obsidian pattern: strip dataviewjs, but leave simple markdown links/buttons to open the Bridge project URL. This preserves Obsidian as a launch/read surface without reintroducing query crashes. Tradeoff: one more tiny Obsidian affordance, but much better discoverability.

**6 — Final Verdict**

Approve with modifications. The architecture is right; the migration facts are not.

Required changes before v2:

1. Fix D9.c/D11: do not claim zero `research`. Either retain `research` or document explicit conversion of `蛋白質攝取量.md`.
2. Downgrade schema status from Active to Proposed/PR1 Draft and remove active references to nonexistent modules.
3. Update migration to handle both `%%KW-START%%` and `%%agent-zoro-keywords-start%%`.
4. Fix Pomodoro writes: no per-tick vault writes, add mtime/hash conflict detection, preserve TaskNotes fields.
5. Correct hook math: 30-60s means roughly 40-200 字, not 500.
