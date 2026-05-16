"""Tests for shared.claude_cli_client — subprocess CLI backend for Max Plan."""

from __future__ import annotations

import json
import subprocess

import pytest

from shared import claude_cli_client as cli

_SAMPLE_OK = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "HELLO",
    "usage": {
        "input_tokens": 12,
        "output_tokens": 3,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    },
}


def _fake_completed(
    stdout: str, returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["claude"], returncode=returncode, stdout=stdout, stderr=stderr
    )


@pytest.fixture
def fake_claude_on_path(monkeypatch):
    """Make _resolve_claude_binary return a stable path without hitting PATH."""
    monkeypatch.setenv("NAKAMA_CLAUDE_CLI", "C:/fake/claude.exe")
    yield "C:/fake/claude.exe"


def test_ask_via_cli_happy_path(fake_claude_on_path, monkeypatch):
    captured: dict = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["input"] = kwargs.get("input")
        return _fake_completed(json.dumps(_SAMPLE_OK))

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = cli.ask_via_cli("hi there", system="be concise", model="claude-sonnet-4-6")

    assert result == "HELLO"
    assert captured["args"][0] == fake_claude_on_path
    # --bare must NOT be used: it would force ANTHROPIC_API_KEY billing path.
    assert "--bare" not in captured["args"]
    assert "--print" in captured["args"]
    assert "--no-session-persistence" in captured["args"]
    assert "--disable-slash-commands" in captured["args"]
    assert "--output-format" in captured["args"]
    # Model is forwarded.
    assert captured["args"][captured["args"].index("--model") + 1] == "claude-sonnet-4-6"
    # Short system prompt goes via flag, not file.
    sys_idx = captured["args"].index("--system-prompt")
    assert captured["args"][sys_idx + 1] == "be concise"
    # Prompt is fed via stdin.
    assert captured["input"] == "hi there"


def test_ask_via_cli_scrubs_api_key_and_uses_tempdir_cwd(fake_claude_on_path, monkeypatch):
    """The subprocess must not see ANTHROPIC_API_KEY (forces OAuth path) and
    must run in system temp dir (avoids CLAUDE.md auto-discovery pollution)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-FAKE-SHOULD-NOT-LEAK")
    captured: dict = {}

    def fake_run(args, **kwargs):
        captured["env"] = kwargs.get("env")
        captured["cwd"] = kwargs.get("cwd")
        return _fake_completed(json.dumps(_SAMPLE_OK))

    monkeypatch.setattr(subprocess, "run", fake_run)
    cli.ask_via_cli("hi", model="claude-sonnet-4-6")

    assert "ANTHROPIC_API_KEY" not in (captured["env"] or {})
    import tempfile as _tf

    assert captured["cwd"] == _tf.gettempdir()


def test_ask_via_cli_long_system_prompt_uses_tempfile(fake_claude_on_path, monkeypatch, tmp_path):
    captured: dict = {}
    long_system = "x" * 5000  # > _SYSTEM_PROMPT_FILE_THRESHOLD

    def fake_run(args, **kwargs):
        captured["args"] = args
        # While the subprocess "runs", verify the temp file exists and contains
        # the system prompt.
        idx = args.index("--system-prompt-file")
        path = args[idx + 1]
        with open(path, encoding="utf-8") as f:
            captured["system_on_disk"] = f.read()
        return _fake_completed(json.dumps(_SAMPLE_OK))

    monkeypatch.setattr(subprocess, "run", fake_run)
    cli.ask_via_cli("hi", system=long_system, model="claude-sonnet-4-6")

    assert "--system-prompt-file" in captured["args"]
    assert "--system-prompt" not in captured["args"]
    assert captured["system_on_disk"] == long_system


def test_ask_via_cli_nonzero_exit_raises(fake_claude_on_path, monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: _fake_completed("", returncode=1, stderr="boom"),
    )
    # Disable retry sleeps by patching with_retry to a single pass-through call.
    from shared import claude_cli_client as mod

    monkeypatch.setattr(mod, "with_retry", lambda fn, **kw: fn())

    with pytest.raises(cli.ClaudeCliError, match="exited 1"):
        cli.ask_via_cli("hi", model="claude-sonnet-4-6")


def test_ask_via_cli_invalid_json_raises(fake_claude_on_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _fake_completed("not json"))
    from shared import claude_cli_client as mod

    monkeypatch.setattr(mod, "with_retry", lambda fn, **kw: fn())

    with pytest.raises(cli.ClaudeCliError, match="not valid JSON"):
        cli.ask_via_cli("hi", model="claude-sonnet-4-6")


def test_ask_via_cli_is_error_payload_raises(fake_claude_on_path, monkeypatch):
    err_payload = {
        "type": "result",
        "subtype": "error",
        "is_error": True,
        "api_error_status": 429,
        "result": "",
    }
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: _fake_completed(json.dumps(err_payload))
    )
    from shared import claude_cli_client as mod

    monkeypatch.setattr(mod, "with_retry", lambda fn, **kw: fn())

    with pytest.raises(cli.ClaudeCliError, match="is_error=true"):
        cli.ask_via_cli("hi", model="claude-sonnet-4-6")


def test_ask_multi_via_cli_flattens_messages(fake_claude_on_path, monkeypatch):
    captured: dict = {}

    def fake_run(args, **kwargs):
        captured["input"] = kwargs.get("input")
        return _fake_completed(json.dumps(_SAMPLE_OK))

    monkeypatch.setattr(subprocess, "run", fake_run)
    cli.ask_multi_via_cli(
        [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "follow up"},
        ],
        model="claude-sonnet-4-6",
    )

    flat = captured["input"]
    assert "[USER]\nfirst question" in flat
    assert "[ASSISTANT]\nfirst answer" in flat
    assert "[USER]\nfollow up" in flat


def test_resolve_binary_missing_raises(monkeypatch):
    monkeypatch.delenv("NAKAMA_CLAUDE_CLI", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)

    with pytest.raises(cli.ClaudeCliError, match="claude.*not found"):
        cli._resolve_claude_binary()


def test_anthropic_client_routes_to_cli_under_max_plan(monkeypatch):
    """ask_claude with NAKAMA_REQUIRE_MAX_PLAN=1 must delegate to CLI, not SDK."""
    monkeypatch.setenv("NAKAMA_REQUIRE_MAX_PLAN", "1")

    called: dict = {}

    def fake_ask_via_cli(prompt, *, system, model):
        called["prompt"] = prompt
        called["system"] = system
        called["model"] = model
        return "ROUTED-OK"

    monkeypatch.setattr("shared.claude_cli_client.ask_via_cli", fake_ask_via_cli)

    from shared.anthropic_client import ask_claude

    out = ask_claude("hello", system="sys", model="claude-sonnet-4-6")
    assert out == "ROUTED-OK"
    assert called == {"prompt": "hello", "system": "sys", "model": "claude-sonnet-4-6"}


def test_anthropic_client_tools_under_max_plan_raises(monkeypatch):
    monkeypatch.setenv("NAKAMA_REQUIRE_MAX_PLAN", "1")

    from shared.anthropic_client import call_claude_with_tools

    with pytest.raises(NotImplementedError, match="tool-use"):
        call_claude_with_tools(
            [{"role": "user", "content": "x"}],
            tools=[{"name": "t", "description": "t", "input_schema": {"type": "object"}}],
            model="claude-sonnet-4-6",
        )
