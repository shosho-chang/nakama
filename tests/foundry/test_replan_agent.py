"""Tests for ``agents.foundry.replan_agent`` (ADR-038 §D3 + §D6).

We never call the real Anthropic API — the ``ask`` callable is dependency-
injected so each test stages a deterministic sequence of fake Messages.

Acceptance covered:
- 5-iter cap enforced even if the LLM keeps emitting tool calls
- token budget cap enforced before next iteration
- tool calls parsed into ``BeatEdit`` instances correctly
- ``[beat:N]`` syntax appears in the rendered storyboard prompt
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agents.foundry.beat_editor import ReplaceQuote, SetLayout
from agents.foundry.replan_agent import (
    MAX_TOOL_ITERATIONS,
    render_storyboard,
    run,
)

# ── fake Anthropic Message shape ─────────────────────────────────────────────


@dataclass
class _FakeBlock:
    type: str
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class _FakeUsage:
    input_tokens: int = 100
    output_tokens: int = 50


@dataclass
class _FakeMessage:
    content: list[_FakeBlock]
    stop_reason: str = "tool_use"
    usage: _FakeUsage = field(default_factory=_FakeUsage)


def _tool_block(name: str, args: dict[str, Any], block_id: str = "tu_1") -> _FakeBlock:
    return _FakeBlock(type="tool_use", id=block_id, name=name, input=args)


# ── tiny storyboard fixture ──────────────────────────────────────────────────


def _sb():
    return [
        {
            "beat_id": 7,
            "start_quote": "老的開頭",
            "end_quote": "老的結尾",
            "broll_decision": "cutaway",
            "layout": "full_broll",
            "broll": {
                "render_target": "hyperframes",
                "component": "bigstat",
                "params": {"value": "42%"},
                "transitions": {"in_transition": None, "out_transition": None},
            },
            "status": {
                "text_approved": True,
                "render_status": "done",
                "visual_approved": True,
                "cached_hash": "h1",
            },
            "user_notes": [],
            "timing": {"start": 1.0, "duration": 2.0},
            "srt_line_ids": [1],
        }
    ]


# ── render_storyboard uses [beat:N] (D6) ─────────────────────────────────────


def test_render_storyboard_uses_bracket_beat_n_syntax():
    out = render_storyboard(_sb())
    assert "[beat:7]" in out
    # Make sure bare [7] is NOT how we present things.
    assert "[7]\n" not in out


# ── tool calls parsed into typed BeatEdits ───────────────────────────────────


def test_tool_calls_parsed_into_typed_beat_edits():
    calls: list[Any] = []

    def fake_ask(messages, tools, *, system, tool_choice):
        calls.append((messages, tool_choice))
        if len(calls) == 1:
            # First iteration: emit two tool calls.
            return _FakeMessage(
                content=[
                    _tool_block(
                        "replace_quote",
                        {"beat_id": 7, "old_quote": "老的開頭", "new_quote": "新的開頭"},
                        block_id="tu_1",
                    ),
                    _tool_block(
                        "set_layout",
                        {"beat_id": "[beat:7]", "layout": "side_overlay_l"},
                        block_id="tu_2",
                    ),
                ],
                stop_reason="tool_use",
            )
        # Second iteration: model decides it's done — emit a no-op set_layout
        # to satisfy the "at least one tool call" rule, then end_turn.
        return _FakeMessage(
            content=[],  # no tool calls => loop exits with no_tool_calls
            stop_reason="end_turn",
        )

    result = run(_sb(), beat_id=7, note="punchier", ask=fake_ask)

    assert len(result.edits) == 2
    assert isinstance(result.edits[0], ReplaceQuote)
    assert result.edits[0].new_quote == "新的開頭"
    assert isinstance(result.edits[1], SetLayout)
    assert result.edits[1].layout == "side_overlay_l"
    assert result.terminated_reason == "no_tool_calls"

    # tool_choice must force a tool call.
    assert calls[0][1] == {"type": "any"}


# ── 5-iter cap enforced ──────────────────────────────────────────────────────


def test_max_iterations_cap_enforced():
    counter = {"n": 0}

    def runaway_ask(messages, tools, *, system, tool_choice):
        counter["n"] += 1
        # Always emit one tool call so the loop never wants to stop on its own.
        return _FakeMessage(
            content=[
                _tool_block(
                    "set_layout",
                    {"beat_id": 7, "layout": "full_broll"},
                    block_id=f"tu_{counter['n']}",
                ),
            ],
            stop_reason="tool_use",
            usage=_FakeUsage(input_tokens=10, output_tokens=5),
        )

    result = run(_sb(), beat_id=7, note="...", ask=runaway_ask)
    assert counter["n"] == MAX_TOOL_ITERATIONS
    assert result.iterations == MAX_TOOL_ITERATIONS
    assert result.terminated_reason == "max_iterations"


# ── token budget cap enforced ────────────────────────────────────────────────


def test_token_budget_cap_enforced_before_next_call():
    counter = {"n": 0}

    def big_ask(messages, tools, *, system, tool_choice):
        counter["n"] += 1
        # First call burns way over the budget; the loop must exit before a
        # second call is issued.
        return _FakeMessage(
            content=[
                _tool_block(
                    "set_layout",
                    {"beat_id": 7, "layout": "full_broll"},
                    block_id="tu_big",
                )
            ],
            stop_reason="tool_use",
            usage=_FakeUsage(input_tokens=10_000, output_tokens=10_000),
        )

    result = run(
        _sb(),
        beat_id=7,
        note="...",
        ask=big_ask,
        token_budget=500,  # well below 20k single-call usage
    )
    assert counter["n"] == 1
    assert result.terminated_reason == "token_budget_exceeded"
    # The single iteration's edits are still recorded.
    assert len(result.edits) == 1


# ── invalid tool args don't crash the loop ───────────────────────────────────


def test_cli_replan_beat_runs_end_to_end(tmp_path, monkeypatch):
    """`python -m agents.foundry --episode <id> replan-beat 7 --note "..."` smoke.

    Mocks the LLM so the CLI exercises planner → engine → edit_log without
    network. Verifies storyboard mutated and edit_log gains edit_ops.
    """
    import yaml

    from agents.foundry import edit_log, pipeline, replan_agent
    from agents.foundry.beat_editor import ReplaceQuote

    # Seed an episode dir.
    ep_root = tmp_path / "data" / "script_video" / "ep-cli-1"
    ep_root.mkdir(parents=True)
    storyboard = _sb()
    (ep_root / "storyboard.yaml").write_text(
        yaml.dump(storyboard, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(pipeline, "_DATA_ROOT", tmp_path / "data" / "script_video")
    monkeypatch.setattr(edit_log, "_LOG_DIR", tmp_path / "edit_log")

    def fake_run(sb, beat_id, note, **kwargs):
        return replan_agent.ReplanResult(
            edits=[ReplaceQuote(beat_id=beat_id, old_quote="老的開頭", new_quote="新的開頭")],
            iterations=1,
            tokens_used=200,
            terminated_reason="end_turn",
        )

    monkeypatch.setattr(replan_agent, "run", fake_run)

    rc = pipeline.main(["--episode", "ep-cli-1", "replan-beat", "7", "--note", "make it punchier"])
    assert rc == 0
    saved = yaml.safe_load((ep_root / "storyboard.yaml").read_text(encoding="utf-8"))
    assert saved[0]["start_quote"] == "新的開頭"
    entries = edit_log.read_entries("ep-cli-1")
    assert len(entries) == 1
    assert entries[0]["edit_ops"][0]["op"] == "replace_quote"
    assert entries[0]["user_note"] == "make it punchier"


def test_invalid_tool_args_are_reported_as_tool_error_not_crash():
    counter = {"n": 0}

    def ask(messages, tools, *, system, tool_choice):
        counter["n"] += 1
        if counter["n"] == 1:
            return _FakeMessage(
                content=[
                    _tool_block(
                        "replace_quote",
                        # Missing required fields → ValidationError; should be
                        # surfaced as tool_result with is_error rather than
                        # raising out of run().
                        {"beat_id": 7},
                        block_id="tu_bad",
                    )
                ],
                stop_reason="tool_use",
            )
        return _FakeMessage(content=[], stop_reason="end_turn")

    result = run(_sb(), beat_id=7, note="x", ask=ask)
    assert result.edits == []
    assert result.terminated_reason == "no_tool_calls"
