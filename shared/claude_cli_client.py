"""Claude Code CLI subprocess backend — routes Claude calls through the local
``claude`` binary instead of the bare Anthropic SDK.

Why this exists: Anthropic rate-limits ``sk-ant-oat01-*`` OAuth tokens used
with the bare Python SDK very aggressively (immediate 429 even at 1 RPS).
The Claude Code CLI binary, in contrast, gets the actual subscription
quota — it carries auth identity / observability headers that the bare SDK
cannot. So when ``NAKAMA_REQUIRE_MAX_PLAN=1`` is set, we want Claude API
calls to go through ``claude -p`` subprocess (which uses Max Plan quota)
rather than ``anthropic.Anthropic.messages.create`` (which 429s on OAuth).

Public surface mirrors :mod:`shared.anthropic_client` so the hook in
``anthropic_client.ask_claude`` / ``ask_claude_multi`` can call this
transparently:

- :func:`ask_via_cli` — single-turn text → text
- :func:`ask_multi_via_cli` — multi-turn messages → text

Tool-use is **not** supported via CLI (tool execution loop is inside the
CLI itself, not exposed as raw JSON). Callers that need tools must use the
SDK path with an API key.

CLI invocation contract:

    claude --print --no-session-persistence --disable-slash-commands
           --tools "" --output-format json --model <model>
           [--system-prompt <s> | --system-prompt-file <path>]
        <stdin: user prompt>

Notable absence: ``--bare`` is **not** used. ``--bare`` forces auth to
``ANTHROPIC_API_KEY`` only and refuses OAuth / keychain (per
``claude --help``), which would defeat the Max Plan purpose. To keep the
call clean without ``--bare`` we instead:

1. Set ``cwd`` to the system temp dir so CLAUDE.md auto-discovery finds
   nothing to inject.
2. Scrub ``ANTHROPIC_API_KEY`` from the subprocess env so the CLI is
   forced to read OAuth via keychain / credentials.json.
3. Pass ``--disable-slash-commands`` + ``--tools ""`` to suppress skills
   and tool use — we want pure inference, not the developer surface.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time

from shared.llm_observability import record_call
from shared.log import get_logger
from shared.retry import with_retry

logger = get_logger("nakama.claude_cli_client")

__all__ = ["ask_via_cli", "ask_multi_via_cli", "ClaudeCliError"]

# Args longer than this go via temp file to avoid Windows CreateProcess argv
# limits (~32K) and to keep system prompts off ps/Task Manager.
_SYSTEM_PROMPT_FILE_THRESHOLD = 2000


class ClaudeCliError(RuntimeError):
    """Raised when ``claude -p`` returns non-zero or unparseable output."""


def _resolve_claude_binary() -> str:
    """Find the ``claude`` CLI on PATH.

    Lets ``NAKAMA_CLAUDE_CLI`` override for tests / non-standard installs.
    """
    override = os.environ.get("NAKAMA_CLAUDE_CLI")
    if override:
        return override
    found = shutil.which("claude")
    if not found:
        raise ClaudeCliError(
            "NAKAMA_REQUIRE_MAX_PLAN=1 requires the 'claude' CLI on PATH but "
            "it was not found. Install Claude Code or set NAKAMA_CLAUDE_CLI "
            "to the binary path."
        )
    return found


def _flatten_messages(messages: list[dict]) -> str:
    """Collapse multi-turn ``messages`` into a single prompt for ``claude -p``.

    The CLI's non-interactive mode (``--print``) takes a single prompt; it
    does not accept a pre-existing assistant turn history via flags. For
    the textbook ingest pipeline the multi-turn calls are short (typically
    one user message), so a lossy flatten is acceptable.

    Format:
        [USER]
        <content>

        [ASSISTANT]
        <content>

        [USER]
        <content>

    Empty / system roles in the list are skipped (system is hoisted to
    ``--system-prompt`` separately by the caller).
    """
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "user").upper()
        if role == "SYSTEM":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            # Claude tool-use shape: list of content blocks. Concatenate text
            # blocks only; tool_use blocks are unsupported and dropped.
            text_parts = [
                b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
            ]
            content = "\n".join(text_parts)
        parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts)


def _invoke(
    prompt: str,
    *,
    system: str,
    model: str,
) -> dict:
    """Run ``claude -p`` once, return parsed JSON result dict.

    Caller layers ``with_retry`` on top of this for transient failures.
    """
    binary = _resolve_claude_binary()
    args = [
        binary,
        "--print",
        "--no-session-persistence",
        "--disable-slash-commands",
        "--tools",
        "",
        "--output-format",
        "json",
        "--model",
        model,
    ]

    tmp_system_path: str | None = None
    if system:
        if len(system) > _SYSTEM_PROMPT_FILE_THRESHOLD:
            fd, tmp_system_path = tempfile.mkstemp(
                prefix="nakama-syscli-", suffix=".txt", text=True
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(system)
            args.extend(["--system-prompt-file", tmp_system_path])
        else:
            args.extend(["--system-prompt", system])

    # Scrub ANTHROPIC_API_KEY so the CLI cannot fall back to API-key billing
    # (without --bare, the CLI's auth precedence prefers OAuth from keychain
    # when API_KEY is absent — which is exactly the Max Plan path we want).
    sub_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

    # cwd = system temp avoids CLAUDE.md auto-discovery polluting the prompt
    # with project instructions intended for interactive sessions.
    sub_cwd = tempfile.gettempdir()

    try:
        proc = subprocess.run(
            args,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=600,
            check=False,
            env=sub_env,
            cwd=sub_cwd,
        )
    finally:
        if tmp_system_path:
            try:
                os.unlink(tmp_system_path)
            except OSError:
                pass

    if proc.returncode != 0:
        raise ClaudeCliError(
            f"claude -p exited {proc.returncode}.\n"
            f"stderr: {proc.stderr[:2000]}\n"
            f"stdout: {proc.stdout[:500]}"
        )

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ClaudeCliError(
            f"claude -p stdout was not valid JSON: {e}\n"
            f"first 500 chars: {proc.stdout[:500]}"
        ) from e

    if payload.get("is_error"):
        raise ClaudeCliError(
            f"claude -p reported is_error=true: "
            f"subtype={payload.get('subtype')} "
            f"api_error_status={payload.get('api_error_status')}"
        )

    return payload


def _record_cli_usage(payload: dict, model: str, latency_ms: int) -> None:
    """Extract usage from CLI JSON payload and forward to observability.

    The CLI's ``total_cost_usd`` is the API-equivalent price; under Max
    Plan it is informational, not billed. We still record it because the
    cost tracker uses the same row schema across providers — it's the
    accounting truth of *what this call would cost on API* even when the
    actual billing is monthly subscription.
    """
    try:
        usage = payload.get("usage") or {}
        record_call(
            model=model,
            input_tokens=usage.get("input_tokens", 0) or 0,
            output_tokens=usage.get("output_tokens", 0) or 0,
            cache_read_tokens=usage.get("cache_read_input_tokens", 0) or 0,
            cache_write_tokens=usage.get("cache_creation_input_tokens", 0) or 0,
            latency_ms=latency_ms,
        )
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("CLI cost tracking failed (ignored): %s", e)


def ask_via_cli(
    prompt: str,
    *,
    system: str = "",
    model: str,
) -> str:
    """Single-turn Claude call via ``claude -p`` subprocess.

    Drop-in for :func:`shared.anthropic_client.ask_claude` when
    ``NAKAMA_REQUIRE_MAX_PLAN=1``. ``max_tokens`` / ``temperature`` from
    the SDK signature are intentionally dropped — the CLI uses model
    defaults and does not expose these as flags (verified via
    ``claude --help`` 2026-05-15).
    """

    def _call() -> dict:
        return _invoke(prompt, system=system, model=model)

    start = time.perf_counter()
    payload = with_retry(_call, max_attempts=3, backoff_base=2.0)
    latency_ms = int((time.perf_counter() - start) * 1000)

    _record_cli_usage(payload, model, latency_ms)

    result = payload.get("result")
    if not isinstance(result, str):
        raise ClaudeCliError(
            f"claude -p JSON missing 'result' string field: keys={list(payload.keys())}"
        )
    return result


def ask_multi_via_cli(
    messages: list[dict],
    *,
    system: str = "",
    model: str,
) -> str:
    """Multi-turn Claude call via CLI — flattens messages into a single prompt.

    Drop-in for :func:`shared.anthropic_client.ask_claude_multi`. The CLI
    has no flag for pre-existing turn history under ``--print``, so we
    serialize ``messages`` with ``[USER]`` / ``[ASSISTANT]`` headers.
    Acceptable for textbook ingest (mostly 1-turn user→assistant); not
    suitable for long agent loops with many turns.
    """
    prompt = _flatten_messages(messages)
    return ask_via_cli(prompt, system=system, model=model)
