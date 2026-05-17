**1 — CODE GROUNDING**

The ADR is directionally grounded, but several “this will work by router” claims are underspecified or false against the current code.

The cited files exist. `shared/translator.py:24` is exactly a hardcoded `_DEFAULT_MODEL = "claude-sonnet-4-6"` ([file](<E:/nakama/shared/translator.py:24>)). `shared/anthropic_client.py:35-85` is the singleton auth precedence block ([file](<E:/nakama/shared/anthropic_client.py:35>)): `NAKAMA_REQUIRE_MAX_PLAN=1` ignores `ANTHROPIC_API_KEY`, requires `ANTHROPIC_AUTH_TOKEN` or `CLAUDE_CODE_OAUTH_TOKEN`, and otherwise defaults to API key before OAuth. `shared/claude_cli_client.py` is a real CLI subprocess path: it invokes `claude --print --no-session-persistence --disable-slash-commands --tools "" --output-format json --model ...`, scrubs `ANTHROPIC_API_KEY`, avoids `--bare`, and records usage from CLI JSON ([file](<E:/nakama/shared/claude_cli_client.py:127>), [file](<E:/nakama/shared/claude_cli_client.py:163>), [file](<E:/nakama/shared/claude_cli_client.py:215>)). `shared/llm_router.py:23` has `DEFAULT_MODELS`, but only model routing exists; there is no `DEFAULT_AUTH`, no `AUTH_*` parsing, and no routing decision object ([file](<E:/nakama/shared/llm_router.py:23>)).

The existing router/facade does behave as the ADR says for model/provider routing, but only for the two task names currently wired: `default` and `tool_use`. `shared.llm.ask()` always resolves `task="default"` when `model=None` ([file](<E:/nakama/shared/llm.py:54>)); only `ask_with_tools()` resolves `task="tool_use"` ([file](<E:/nakama/shared/llm.py:174>)). This breaks the ADR’s “Translator de-hardcoding” story as written. If `translator.py` changes `model=None` and simply calls `ask(..., model=model)`, `MODEL_ROBIN_TRANSLATE=claude-sonnet-4-6` will not be read. There is no `task="translate"` parameter in the facade today. The PR must add one, or translator must call the router directly before dispatch. Otherwise the advertised VPS `.env` pin is dead config.

The `/translate` grounding is also only half-right. The route exists at `thousand_sunny/routers/robin.py:535` and does not call `set_current_agent` ([file](<E:/nakama/thousand_sunny/routers/robin.py:535>)). But adding `set_current_agent("robin")` only in the route handler is not enough. The actual LLM call happens inside the sync background task `_translate_in_background()` at lines 486-510 ([file](<E:/nakama/thousand_sunny/routers/robin.py:486>)), scheduled at lines 595-599. Thread-local context must be set inside the background task body, not just before scheduling, or router/cost attribution can still fall back to `unknown`/default depending on Starlette’s background execution path.

Observability is not implemented. `record_call()` has no `auth` kwarg ([file](<E:/nakama/shared/llm_observability.py:22>)); `state.api_calls` has no `auth` column ([file](<E:/nakama/shared/state.py:53>)); migrations only add cache/latency columns, not auth ([file](<E:/nakama/shared/state.py:491>)). Any ADR wording implying this is already present is wrong.

**2 — DRIFT DETECTION**

The ADR does not contradict the original multi-model architecture in spirit. `memory/claude/project_multi_model_architecture.md` says production agents use direct SDK wrappers, bench/eval use LiteLLM, and the router resolves `MODEL_<AGENT>_<TASK>` then `MODEL_<AGENT>` then `DEFAULT_MODELS[task]` ([file](<E:/nakama/memory/claude/project_multi_model_architecture.md:11>), [file](<E:/nakama/memory/claude/project_multi_model_architecture.md:113>)). Adding auth as a parallel routing dimension is consistent with that.

The real drift is more subtle: the ADR says “subscription-first is philosophy,” but the repo has conflicting memories. `feedback_llm_model_choice.md` says default API calls should use the strongest current Claude model, Opus 4.7 ([file](<E:/nakama/memory/claude/feedback_llm_model_choice.md:10>)). `feedback_cost_management.md` later says daily work should use Sonnet 4.6 and Opus only for P9/P10/complex debug because Opus 1M was costing `$200-400/day` ([file](<E:/nakama/memory/claude/feedback_cost_management.md:7>), [file](<E:/nakama/memory/claude/feedback_cost_management.md:9>)). Claude’s ADR leans on the first memory but ignores the second. Do not present “always strongest / subscription-first” as a single uncontested doctrine. The actual principle is workload-sensitive quality-first with explicit cost scars.

The ADR’s claim that `ask_claude` will “no longer read process-wide `NAKAMA_REQUIRE_MAX_PLAN`” is achievable, but not as casually as written. Today `ask_claude()` and `ask_claude_multi()` branch to CLI when the flag is set ([file](<E:/nakama/shared/anthropic_client.py:143>), [file](<E:/nakama/shared/anthropic_client.py:272>)); `call_claude_with_tools()` raises under the same flag because CLI cannot expose raw tool-use JSON ([file](<E:/nakama/shared/anthropic_client.py:208>)). `scripts/run_s8_batch.py` sets the flag internally because textbook ingest “must never charge the API budget” ([file](<E:/nakama/scripts/run_s8_batch.py:1004>)). If the flag is moved outward, the outer layer must still cover direct facade calls from `run_s8_preflight._ask_llm()` ([file](<E:/nakama/scripts/run_s8_preflight.py:208>)) and must preserve the tool-use raise. Mapping `NAKAMA_REQUIRE_MAX_PLAN=1` to a per-call `subscription_required` decision is cleaner than keeping it as an awkward side channel.

**3 — NUMERICAL / FACTUAL CLAIMS**

“Bare SDK + OAuth = 429 at 1 RPS” is not verified as an official Anthropic-documented constraint. Official docs document normal API 429 behavior, RPM/ITPM/OTPM limits, acceleration limits, and rate-limit headers, but I found no official line saying `sk-ant-oat01-*` OAuth tokens through the bare SDK are hard-limited at 1 RPS. Anthropic’s Help Center does document that Claude Code can connect to Pro/Max subscription usage, that API keys in the environment take priority over subscription auth, and that Claude paid plans do not include normal Console/API usage. That supports “CLI subscription path exists” and “scrub API key,” but not the exact 1 RPS assertion. Treat the 1 RPS statement as Nakama operational evidence or folklore unless you attach a support ticket/log. citeturn1view0turn1view1turn3view0turn3view1

The local repo does contain evidence that the team observed an OAuth/sandcastle failure: the handoff says Stage 4.0 found “anti-automation 429” and fell back to API ([file](<E:/nakama/memory/claude/project_session_2026_05_07_pm_stage4_batch_handoff.md:115>)). The plan anticipated a dry-run gate requiring no `429` / `auth` / `rate limit` before using Path C ([file](<E:/nakama/docs/plans/2026-05-07-textbook-ingest-v3-path-b-rewrite.md:239>), [file](<E:/nakama/docs/plans/2026-05-07-textbook-ingest-v3-path-b-rewrite.md:250>)). That is good internal evidence, but it is not the same as a vendor constraint.

The ADR’s “`DEFAULT_MODELS["default"]` Sonnet 4.5 drift” is numerically wrong. Code says `DEFAULT_MODELS["default"] = "claude-sonnet-4-20250514"` ([file](<E:/nakama/shared/llm_router.py:23>)); tests assert that exact model as the Claude default ([file](<E:/nakama/tests/test_llm_router.py:77>)). That is Sonnet 4 dated 2025-05-14, not Sonnet 4.5. The `tool_use` default is `claude-haiku-4-5`.

**4 — ASSUMPTION PUSH-BACK**

Do not let “subscription-first” imply “subscription is safe to burn by default.” Max Plan quota is not an infinite cheap pool; Anthropic documents shared Claude/Claude Code usage limits and gives “wait until reset” as a normal outcome for Max users. The ADR assumes the subscription pool is preferable for all unclassified traffic, but it also says low-value tasks should not share quota with long prompts in Phase 2. That is a contradiction in operational default: if Phase 1 sends all unspecified Anthropic calls to CLI, low-value tasks immediately share quota until Phase 2 exists.

Auto-downgrade is too quiet. `subscription` plus no OAuth falling back to API with only a warn log hides exactly the deployment mistakes this repo already has scars around. `feedback_vps_env_drift_check.md` exists because silent env fallback is dangerous; `.env.example` currently only documents model envs, not auth envs ([file](<E:/nakama/.env.example:12>)). The default should not silently spend money when the operator thought they were using Max. Use three policies: `api`, `subscription_preferred`, and `subscription_required`. The old `NAKAMA_REQUIRE_MAX_PLAN=1` maps to `subscription_required`. Robin/Brook long-running jobs can also be required without poisoning the whole process.

The binary `subscription/api` design is too rigid because it mixes billing intent with fallback behavior. “subscription” in the ADR actually means “try subscription, then maybe API.” That is not a canonical auth policy; it is a fallback chain hidden behind one word. Define the router return as a decision object: `model`, `provider`, `auth_policy`, `auth_strictness`, `task`, and eventually `fallback_chain`. Then Phase 2 does not need to supersede the interface.

Env-only policy will not scale cleanly. Today I counted 54 `shared.llm` import/reference lines across 35 production files and 56 ask-family call lines across 37 production files. Adding `MODEL_*` plus `AUTH_*` for every agent/task will turn `.env` into an unreviewable policy database. Keep env overrides, but put canonical routing in a versioned YAML/TOML policy from day 1.

**5 — ALTERNATIVES NOT CONSIDERED**

Claude did not seriously evaluate call-site intent. Translator is the best counterexample: the call site knows this is `translate`, high-volume, plain text, no tools. Agent context alone is an unreliable proxy, especially because `/translate` runs through a web background task. The better shape is `ask(..., task="translate")` or a translator wrapper that resolves `task="translate"` internally. Policy remains centralized, but the call site supplies the semantic task.

Claude also under-evaluated “CLI default for Anthropic.” I reject it as a universal default because current CLI support drops SDK knobs (`max_tokens` / `temperature`) per `claude_cli_client.py:246-250`, flattens multi-turn messages lossy at lines 270-284, and cannot support tool-use JSON. But it is a valid default for plain text Anthropic calls when policy is `subscription_preferred|required`.

Finally, this is partly a budget/dispatch problem, not just an auth problem. If Max quota is valuable, dispatch should rate-limit subscription usage by task class before the provider call: e.g. Robin translate gets a quota bucket, Brook compose gets another, Franky health checks default to API or cheap non-Anthropic models. The `auth` column alone will observe burn after the fact; it will not prevent quota starvation.

**6 — FINAL VERDICT**

Approve with modifications. Do not approve as-is.

Top required changes:

1. Replace binary `subscription/api` with `api`, `subscription_preferred`, `subscription_required`. Map `NAKAMA_REQUIRE_MAX_PLAN=1` to required, not a separate semantic layer.

2. Add task-aware facade routing before translator de-hardcoding. `MODEL_ROBIN_TRANSLATE` is dead unless `shared.llm.ask()` accepts `task="translate"` or translator resolves that task explicitly.

3. Set agent/task context inside `_translate_in_background()`, not only in the `/translate` handler.

4. Make fallback observable and non-silent: record `auth_requested`, `auth_actual`, and fallback reason; raise for `subscription_required`.

5. Reword the 1 RPS OAuth claim as observed Nakama behavior unless backed by a vendor source, and correct the Sonnet 4.5 drift claim to `claude-sonnet-4-20250514`.
