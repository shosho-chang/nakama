---
name: feedback-oauth-env-pinning-long-batch
description: Long-running Max Plan batches (>1h) must NOT export CLAUDE_CODE_OAUTH_TOKEN in shell env; subprocess locks the stale token and mid-batch expiry → silent 401 cascade
metadata:
  type: feedback
created: 2026-05-16
---

# OAuth env-var pinning trap on long batches

**Rule**: When launching long-running Max Plan workloads (`NAKAMA_REQUIRE_MAX_PLAN=1` going through `claude -p` subprocess), do NOT export `CLAUDE_CODE_OAUTH_TOKEN` in the parent shell env. Let the CLI subprocess read `~/.claude/.credentials.json` directly per invocation so the access token auto-refreshes via the file's `refreshToken` field.

Defensive setup that works on long batches:

```bash
unset CLAUDE_CODE_OAUTH_TOKEN          # critical — don't pin
export ANTHROPIC_AUTH_TOKEN="$OAUTH"   # SDK fallback, ignored by CLI
export NAKAMA_REQUIRE_MAX_PLAN=1
unset ANTHROPIC_API_KEY                # claude_cli_client scrubs it anyway
# Then: cd into worktree, python -m scripts.run_s8_batch ...
```

**Why**: `shared/claude_cli_client.py:166` does `sub_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}` — it only scrubs `ANTHROPIC_API_KEY`. Every other env var (including `CLAUDE_CODE_OAUTH_TOKEN`) flows through to the `claude -p` subprocess. If the parent shell pinned a token that's now expired, the subprocess uses that stale token and gets `401 Invalid authentication credentials`. With the env unset, the CLI falls back to reading `credentials.json` keychain, which auto-refreshes via the `refreshToken` field.

Real incident — SN textbook ingest 2026-05-16: launched batch at 16:04 with the wrapper-style env (CLAUDE_CODE_OAUTH_TOKEN pinned), token expired at 16:27 mid-ch3 dispatch, ch4-17 all fail-fast with 401 in <1s each. Lost ~14 chapters of work and required re-launch from ch3. Credentials.json had already refreshed (Claude Code foreground session triggered it) but the subprocess was locked on the old env value.

**How to apply**:
- For one-off batches < 30 min: wrapper-style (pinning) is fine — token won't expire.
- For batches ≥ 1h: ALWAYS use the unset-token pattern above. Even a freshly-refreshed token has only 8h max life; long batches outlast that.
- Don't trust "token expires in X hours" — refresh windows can collapse if Claude Code foreground/background sessions touch credentials.json concurrently.

**Known broken**: `scripts/Invoke-IngestTextbook.ps1` line 93-94 still pins `CLAUDE_CODE_OAUTH_TOKEN` and `ANTHROPIC_AUTH_TOKEN`. Wrapper works for the documented `bse` / `sn` smoke runs (single-chapter, fast) but breaks for the full `-Book all` 28-chapter run on token-expiry boundaries. Patch left for follow-up — see PR #576 commit message + this memory.

**Related**:
- [[feedback-claude-cli-subprocess-env-leak]] — sibling concern about other env vars
- [[reference-claude-cli-keychain-auth]] — how `credentials.json` auto-refresh works
- [[project-textbook-ingest-v3-pipeline]] — caller context
