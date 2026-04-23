---
name: Agent entry points that may run without DB access must call load_config() explicitly
description: Don't rely on get_db_path() as the implicit .env loader — alert/Slack paths can complete without touching the DB
type: feedback
tags: [env, config, franky, load_dotenv, agent-bootstrap]
---
All agent `__main__.py` modules must call `shared.config.load_config()` (or `load_dotenv()`) at the top of `main()` before dispatching subcommands — do not rely on `get_db_path()` as an implicit loader.

**Why:** Surfaced on Franky Phase 1 VPS smoke test (2026-04-23). Franky's slice subcommands (`alert --test`, `health`, `backup-verify`, `digest`) construct `FrankySlackBot.from_env()` / `R2Client.from_env()` **before** any DB operation. Other agents (Robin, Nami) hit `get_db_path()` early in their pipelines — which internally calls `load_dotenv()` — so env loads "for free." Franky's alert/Slack paths can complete without the DB, so env reads returned empty and both Slack DMs and R2 reads silently fell back to no-op stubs. `.env` on VPS was correctly set; the bug was that nothing in the subcommand's code path triggered the load. Fix: PR #87, add `load_config()` at top of `main()` — idempotent, cheap, canonical.

**How to apply:** When writing a new `agents/<x>/__main__.py` or any standalone script that reads env via `os.getenv`/`from_env` factories, add `from shared.config import load_config; load_config()` at the top of `main()`. Do not assume get_db_path / agent runner / test fixtures will load `.env` for you. Especially critical for: Slack bots (silently degrade to stub), R2/S3 clients (silently "not configured"), any LLM factory (silently picks default model). Check during code review: grep the new entry point for `load_config` / `load_dotenv` — if absent and the script reads env without upstream agent framework, flag it.
