# ADR-048 Panel Audit — Codex / GPT-5（2026-06-24）

> Multi-agent panel step 2，逐字保留。審 ADR-048 v1 + ADR-047。判決：approve with modifications。

**Section 1 — CODE GROUNDING**

The core daily-review loss claim is mostly correct. `collect_yesterday_items()` only accepts annotation items whose local `created_at` date equals the supplied `yesterday`; everything else is skipped at `agents/robin/daily_review.py:312` and `:330`. `run_daily_review()` computes `yesterday = today - timedelta(days=1)` at `:1195`, then builds candidates only from that set. There is no union with old open candidates.

The “single-slot snapshot” claim is correct. The persisted bundle path is hard-coded to `KB/.centaur/daily_review_latest.json` at `agents/robin/daily_review.py:164`, and `save_review_bundle()` overwrites that file at `:171`. The cron path calls `run_daily_review()` and then `save_review_bundle()` in `agents/robin/__main__.py:39-45`; cron runs it daily via `cron.conf:17`.

`create_permanent` really accepts `candidate_id` and does not mark it done. `CreatePermanentIn.candidate_id` exists at `thousand_sunny/routers/kb_review.py:190-199`; `create_permanent()` writes the Permanent file and calls `_run_phase5()` at `:283-319`, but never calls `_update_review_state()`. Only skip/later endpoints record candidate actions: `_update_review_state()` is at `:142`, `review_skip()` at `:561`, and `review_later()` at `:573`.

Important nuance: there is already a lightweight skip/defer state file, not a real inbox. `daily_review_state.json` stores only `skipped` and `deferred` IDs at `agents/robin/daily_review.py:124-156`; it does not persist candidate bodies, first-seen dates, source refs, or open status. A deferred candidate can expire into a sweep item by ID, but the original candidate content is not carried forward. ADR-048 should say “existing skip/defer suppressor is not a durable candidate inbox,” not imply there is zero state.

`memory_extractor` is agent-parameterized. `extract_from_messages(agent, user_id, messages)` is generic at `shared/memory_extractor.py:161-164`, reads existing memories for that agent at `:177`, and writes via `agent_memory.add(agent=agent, ...)` at `:209`. Nami’s handler is not generic: it hard-codes `format_as_context("nami", user_id)` at `gateway/handlers/nami.py:898` and `extract_in_background(agent="nami", ...)` at `:969`. So a Robin Slack handler can reuse the extractor, but “directly套用” still requires a handler/scaffold, identity, and lifecycle wiring.

Reflection `--all` is real. `_all_agent_user_pairs()` selects distinct active `(agent, user_id)` pairs at `shared/memory_reflection.py:336-341`; CLI `--all` exists at `:350`; cron runs `python3 -m shared.memory_reflection --all --apply` at `cron.conf:62`.

The episodic-layer claim is overstated in this checkout. I found only generic support for `memories.type` including `episodic` in `shared/state.py:940-957` and an old markdown-memory comment saying `memory/episodic/` is “Phase 3” in `shared/memory.py:13`. I did not find a landed cross-agent task-event schema or automatic task logging. D-F must not depend on “ADR-047 Phase 2a just landed” unless ADR-048 cites PR #932 and its concrete files.

**Section 2 — DRIFT / CONSISTENCY WITH ADR-047 & CENTAUR KB**

ADR-048 does not violate ADR-047 D-D if it stays on SQLite/vault JSON. A candidate inbox table or JSON store is still DIY-on-SQLite/transparent. The danger is not framework drift; the danger is scope drift. ADR-047 explicitly says Phase 3 evaluation comes before “more aggressive changes or frameworks” (`docs/decisions/ADR-047-agent-memory-v2-self-improving.md:75`). ADR-048 D-E jumps from “three Slack agents wired manually” to “memory as platform default” before Robin memory quality is measured. That contradicts ADR-047’s discipline, not its storage choice.

ADR-047’s deployed model is per-agent-siloed today. The naming section says `user_memories` means Nami/Sanji/Zoro memories of the user at `ADR-047:14`, and Phase 2 cross-agent sharing remains future work at `:74` and `:85`. ADR-048 can extend Robin as agent 4, but it must not imply “team shared understanding” is automatic. Current schema still keys memory by `agent, user_id, subject` (`shared/agent_memory.py:53-64`) and active retrieval filters one agent at a time (`:172-189`, `:390`).

The larger drift risk is with ADR-043. ADR-043 says `KB/Permanent/` is the human-written authoritative layer at `docs/decisions/ADR-043-centaur-zettelkasten-permanent-layer.md:7`, `:9`, and `:32`. It also says AI writes/scaffolds must not own Permanent body/status/relations (`:34`) and that consumers should prefer Permanent while downgrading candidates (`:38`). Robin learning from annotations is acceptable only if the output is a ranking preference, not canonical knowledge. Do not let “Robin learned 修修 believes X” leak into KB truth. ADR-048 D-C must state: raw source quotes are not user beliefs; annotations and accept/skip are evidence for “interest/card-worthiness,” not authority.

**Section 3 — NUMERICAL / FACTUAL CLAIMS**

The `max_tokens 2048 → 8192` claim is not verified in this checkout. Current `agents/robin/daily_review.py` still has P-1 `max_tokens=2048` at `:409`, P-2 edge `max_tokens=1536` at `:606`, and no `8192` hit in the searched Robin/shared/test files. Current git log ends at ADR-047 PR #929, not #934. If PR #934 fixed this elsewhere, ADR-048 must cite the PR/commit and mark Phase 0 “done outside this branch.” As written, D-0 is stale against local code.

The 1-day window claim is verified: `created != yesterday` is the hard filter (`agents/robin/daily_review.py:330`), and `yesterday` is computed once per run (`:1195`).

`candidate_id = slug + anchors hash` is verified: `_candidate_id(slug, anchors)` sorts anchors, SHA1-hashes `slug|anchors`, takes 8 hex chars, and returns `slug-hash` at `agents/robin/daily_review.py:436-440`. But “stable” is conditional. It is stable only if source slug and anchors do not change. ADR-043 already flags CJK anchor rot risk (`ADR-043:14`, `:42`); anchor repair will change IDs.

The `29 → 22 active` claim is documented in accepted ADR-047 at `ADR-047:90`; I did not independently verify it against `data/state.db`.

**Section 4 — ASSUMPTION PUSH-BACK**

D-A is basically right: inbox and `user_memories` are different layers. But ADR-048 turns that into a too-clean dichotomy. Accept/skip is primarily product telemetry and queue state. It only becomes user memory after aggregation, explanation, and confidence calibration. Store accept/skip first as `candidate_events`; derive memories later. Do not write “修修 cares about mitochondria” because one card was accepted.

D-C is the weakest decision. “Annotations + accept/skip” is a noisy taste signal, not a durable preference stream. A highlight can mean “important,” “wrong,” “weird,” “quote this later,” or “I need to think.” A skip can mean low quality, duplicate, bad timing, too obvious, already known, or UI fatigue. Treating these as stable user facts will overfit Robin to yesterday’s interface behavior. If D-C survives, restrict extraction to user-authored annotation notes and explicit action reasons; never extract from source quotes. Use low-confidence `context/preference`, with provenance fields the current `user_memories` schema lacks.

D-D is scope creep. A Robin Slack bot may be useful, but it is not “the step that makes Robin memory natural.” The real win is the inbox because it stops data loss and creates clean telemetry. Slack adds gateway surface, permissions, UX, vault-write semantics, and another conversational identity. Do not let a bot delay durable candidate handling.

D-E should be “platform scaffold by default,” not “memory writes by default.” Batch agents and conversational agents need different memory policies. Automatic extraction without an agent-specific policy will store junk. Make every agent declare `memory_mode = none/read/context/extract/events`, then scaffold accordingly.

D-F has the right shape, one shared detector, but the prerequisite is missing. A shared detector over unstructured `memories(type='episodic')` will become another noisy memory pile. Define a strict task-event schema first: `agent`, `user_intent`, `input_artifacts`, `workflow_steps`, `outputs`, `duration`, `repeat_key`, `success/failure`. Then run one cross-agent detector over that.

**Section 5 — ALTERNATIVES NOT CONSIDERED**

1. **No Robin `user_memories` yet.** Use the KB itself as the model of the user: Permanent cards, MOCs, accepted cards, and source_refs. Tradeoff: less “personalized agent” language, much stronger Centaur consistency. This should be the default until inbox telemetry proves memory adds ranking lift.

2. **Inbox-only Phase 1.** Build durable `candidate_inbox` and `candidate_events`; add `done_on_open`, skip, defer, resurrect, aging, and dedupe. Tradeoff: no shiny memory story, but it directly fixes the disappearing-ideas bug and creates the clean dataset D-C needs.

3. **Telemetry-derived taste profile, no Slack bot.** Derive topic/card-worthiness features from accept/skip/defer/open-card events after N actions. Tradeoff: slower learning, but lower noise and no gateway scope creep.

4. **Revisit frameworks only after D-F has real load.** ADR-047 D-D is still correct today. Do not adopt Mem0/Letta/Zep now. Reopen that only if task-event clustering and cross-agent memory queries become the bottleneck.

**Section 6 — FINAL VERDICT**

**Approve with modifications.** Do not approve as-is.

Top required changes:

1. **D-B / Phase 1 first:** make the candidate inbox the only Phase-1 deliverable. Persist candidate bodies, source refs, status, first_seen/last_seen, action history, and `done_on_open`. This fixes the proven bug.

2. **D-C:** narrow Robin memory extraction. Extract only from user-authored notes and aggregated action telemetry, not raw annotations. Add provenance/confidence and state that memories affect ranking only, never KB authority.

3. **D-D / Phase 3:** remove “bot is not another project.” It is another project. Split Robin Slack bot into its own optional ADR/phase after inbox metrics.

4. **D-E:** change “platform default” to policy-based scaffold. Default event logging is fine; default semantic memory extraction is not.

5. **D-F:** replace the “episodic layer already landed” claim with concrete file/schema references, or downgrade it to prerequisite work. One shared detector is correct only after all agents emit structured task events.

ADR-048 has a good center: stop losing candidates. Keep that center. Cut the speculative memory/bot/platform claims down until they are backed by telemetry.
