"""Unit + integration tests for agents/franky/state/context_snapshot.py.

Per ADR-023 §3 Phase 1 (S2a) acceptance criteria: each block builder gets a
fixture set (with git fake / gh mock / MEMORY missing fallback), plus an
integration test that runs ``regenerate`` against a tmpdir and asserts all
four sections are present and the total token count stays under 9k.
"""

from __future__ import annotations

import json
from pathlib import Path

from agents.franky.state import context_snapshot as cs

# ---------------------------------------------------------------------------
# token utility
# ---------------------------------------------------------------------------


def test_estimate_tokens_4_chars_per_token():
    assert cs.estimate_tokens("") == 0
    assert cs.estimate_tokens("abcd") == 1
    assert cs.estimate_tokens("abcde") == 2
    assert cs.estimate_tokens("a" * 4000) == 1000


def test_truncate_to_token_budget_under_budget():
    text = "short text"
    assert cs.truncate_to_token_budget(text, 1000) == text


def test_truncate_to_token_budget_over_budget_appends_marker():
    text = "x" * 5000
    out = cs.truncate_to_token_budget(text, 100)  # 100 tokens = 400 chars
    assert len(out) <= 400
    assert "truncated for token budget" in out


# ---------------------------------------------------------------------------
# Block 1 — active priorities
# ---------------------------------------------------------------------------


def test_active_priorities_reads_first_n_bullets(tmp_path: Path):
    mem = tmp_path / "MEMORY.md"
    mem.write_text(
        "# Memory Index\n"
        "\n"
        "- [a.md](a.md) — first\n"
        "- [b.md](b.md) — second\n"
        "- [c.md](c.md) — third\n"
        "- [d.md](d.md) — fourth\n"
        "- [e.md](e.md) — fifth\n"
        "- [f.md](f.md) — sixth (should be cut)\n",
        encoding="utf-8",
    )
    out = cs.build_active_priorities(memory_path=mem, n=5)
    assert "first" in out
    assert "fifth" in out
    assert "sixth" not in out


def test_active_priorities_memory_missing_fallback(tmp_path: Path):
    out = cs.build_active_priorities(memory_path=tmp_path / "nope.md")
    assert "MEMORY.md not found" in out


def test_active_priorities_no_bullets_fallback(tmp_path: Path):
    mem = tmp_path / "MEMORY.md"
    mem.write_text("# Just a title\n\nNo bullets here.\n", encoding="utf-8")
    out = cs.build_active_priorities(memory_path=mem)
    assert "No memory entries" in out


# ---------------------------------------------------------------------------
# Block 2 — recent ADR assumptions
# ---------------------------------------------------------------------------


def _write_adr(adr_dir: Path, name: str, decision_body: str) -> Path:
    p = adr_dir / name
    p.write_text(
        "# Some ADR\n\n## Context\n\nctx.\n\n## Decision\n\n"
        + decision_body
        + "\n\n## Consequences\n\nfoo.\n",
        encoding="utf-8",
    )
    return p


def test_recent_adr_assumptions_extracts_decision_summary(tmp_path: Path, monkeypatch):
    adr_dir = tmp_path / "decisions"
    adr_dir.mkdir()
    _write_adr(adr_dir, "ADR-100-foo.md", "We decided X for reasons Y.")
    _write_adr(adr_dir, "ADR-101-bar.md", "Pick option B because cheaper.")

    fake_log = "ADR-100-foo.md\nADR-101-bar.md\n"

    def fake_run_git(args, cwd=None):
        if args[0] == "log":
            return fake_log
        return ""

    monkeypatch.setattr(cs, "_run_git", fake_run_git)
    monkeypatch.setattr(cs, "REPO_ROOT", tmp_path)

    # Compute relative path inside fake REPO_ROOT
    monkeypatch.setattr(cs, "ADR_DIR", adr_dir)

    # list_recent_adrs builds REPO_ROOT / line, so place ADRs at expected location
    target = tmp_path / "ADR-100-foo.md"
    target.write_text((adr_dir / "ADR-100-foo.md").read_text(encoding="utf-8"), encoding="utf-8")
    target2 = tmp_path / "ADR-101-bar.md"
    target2.write_text((adr_dir / "ADR-101-bar.md").read_text(encoding="utf-8"), encoding="utf-8")

    # Override fake log to point to repo-root paths it expects
    def fake_run_git2(args, cwd=None):
        if args[0] == "log":
            return "ADR-100-foo.md\nADR-101-bar.md\n"
        return ""

    monkeypatch.setattr(cs, "_run_git", fake_run_git2)

    out = cs.build_recent_adr_assumptions(adr_dir=adr_dir)
    assert "ADR-100-foo" in out
    assert "We decided X" in out
    assert "ADR-101-bar" in out


def test_recent_adr_assumptions_empty_when_no_log(monkeypatch, tmp_path: Path):
    adr_dir = tmp_path / "decisions"
    adr_dir.mkdir()
    monkeypatch.setattr(cs, "_run_git", lambda args, cwd=None: "")
    out = cs.build_recent_adr_assumptions(adr_dir=adr_dir)
    assert "No ADR commits" in out


def test_recent_adr_assumptions_dir_missing(monkeypatch, tmp_path: Path):
    out = cs.build_recent_adr_assumptions(adr_dir=tmp_path / "nope")
    assert "No ADR commits" in out or "No Decision" in out


def test_extract_decision_section_handles_no_decision_header():
    assert cs._extract_decision_section("# Title\n\n## Other\n\nbody.\n") == ""


def test_summarise_truncates():
    long = "a" * 2000
    out = cs._summarise(long, max_chars=100)
    assert len(out) <= 100
    assert out.endswith("…")


# ---------------------------------------------------------------------------
# Block 3 — top-N open issues
# ---------------------------------------------------------------------------


def test_top_open_issues_renders_lines(monkeypatch):
    fake_issues = [
        {
            "number": 473,
            "title": "Franky S2a snapshot",
            "labels": [{"name": "ready-for-agent"}, {"name": "franky"}],
            "createdAt": "2026-05-07T10:00:00Z",
        },
        {
            "number": 480,
            "title": "Other thing",
            "labels": [],
            "createdAt": "2026-05-06T10:00:00Z",
        },
    ]
    monkeypatch.setattr(cs, "fetch_open_issues", lambda limit=15: fake_issues)
    out = cs.build_top_open_issues(limit=15)
    assert "#473 Franky S2a snapshot" in out
    assert "ready-for-agent,franky" in out
    assert "(2026-05-07)" in out
    assert "#480 Other thing" in out


def test_top_open_issues_gh_unavailable(monkeypatch):
    monkeypatch.setattr(cs, "fetch_open_issues", lambda limit=15: [])
    out = cs.build_top_open_issues()
    assert "No open issues" in out


def test_fetch_open_issues_gh_missing(monkeypatch):
    def boom(*a, **kw):
        raise FileNotFoundError("gh not on PATH")

    monkeypatch.setattr(cs.subprocess, "run", boom)
    assert cs.fetch_open_issues() == []


def test_fetch_open_issues_nonzero_returncode(monkeypatch):
    class R:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(cs.subprocess, "run", lambda *a, **kw: R())
    assert cs.fetch_open_issues() == []


def test_fetch_open_issues_parses_json(monkeypatch):
    class R:
        returncode = 0
        stdout = json.dumps(
            [{"number": 1, "title": "t", "labels": [], "createdAt": "2026-05-07T00:00:00Z"}]
        )
        stderr = ""

    monkeypatch.setattr(cs.subprocess, "run", lambda *a, **kw: R())
    out = cs.fetch_open_issues(limit=5)
    assert out[0]["number"] == 1


# ---------------------------------------------------------------------------
# Block 4 — recent MEMORY changes (git diff)
# ---------------------------------------------------------------------------


def test_memory_changes_missing_file(tmp_path, monkeypatch):
    out = cs.build_recent_memory_changes(memory_path=tmp_path / "nope.md")
    assert "not found" in out


def test_memory_changes_no_log_in_window(tmp_path, monkeypatch):
    mem = tmp_path / "MEMORY.md"
    mem.write_text("- old line\n", encoding="utf-8")
    monkeypatch.setattr(cs, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cs, "_run_git", lambda args, cwd=None: "")
    out = cs.build_recent_memory_changes(memory_path=mem)
    assert "No MEMORY.md changes" in out


def test_memory_changes_parses_added_bullets(tmp_path, monkeypatch):
    mem = tmp_path / "MEMORY.md"
    mem.write_text("- existing\n", encoding="utf-8")
    monkeypatch.setattr(cs, "REPO_ROOT", tmp_path)

    fake_diff = (
        "diff --git a/MEMORY.md b/MEMORY.md\n"
        "--- a/MEMORY.md\n"
        "+++ b/MEMORY.md\n"
        "@@\n"
        " - existing\n"
        "+- [new1.md](new1.md) — new entry one\n"
        "+- [new2.md](new2.md) — new entry two\n"
        "+# header line, not a bullet\n"
    )

    def fake_run_git(args, cwd=None):
        if args[0] == "log":
            return "abc1234\n"
        if args[0] == "diff":
            return fake_diff
        return ""

    monkeypatch.setattr(cs, "_run_git", fake_run_git)
    out = cs.build_recent_memory_changes(memory_path=mem)
    assert "new entry one" in out
    assert "new entry two" in out
    assert "header line" not in out


def test_memory_changes_git_failure_fallback(tmp_path, monkeypatch):
    mem = tmp_path / "MEMORY.md"
    mem.write_text("- x\n", encoding="utf-8")
    monkeypatch.setattr(cs, "REPO_ROOT", tmp_path)

    def fake_run_git(args, cwd=None):
        raise RuntimeError("git boom")

    monkeypatch.setattr(cs, "_run_git", fake_run_git)
    out = cs.build_recent_memory_changes(memory_path=mem)
    assert "git log failed" in out


# ---------------------------------------------------------------------------
# Compose / regenerate integration
# ---------------------------------------------------------------------------


def _stub_all_builders(monkeypatch):
    monkeypatch.setattr(cs, "build_active_priorities", lambda **kw: "- p1\n- p2\n")
    monkeypatch.setattr(
        cs, "build_recent_adr_assumptions", lambda **kw: "### ADR-022\n\nDecision summary.\n"
    )
    monkeypatch.setattr(cs, "build_top_open_issues", lambda **kw: "- #1 t [l] (2026-05-07)\n")
    monkeypatch.setattr(cs, "build_recent_memory_changes", lambda **kw: "- new mem\n")
    monkeypatch.setattr(cs, "get_repo_sha", lambda: "abc1234")


def test_build_snapshot_has_all_four_blocks(monkeypatch):
    _stub_all_builders(monkeypatch)
    text = cs.build_snapshot()
    assert "## 1. Active priorities" in text
    assert "## 2. Recent ADR assumptions" in text
    assert "## 3. Top open issues" in text
    assert "## 4. Recent MEMORY changes" in text
    assert "generated_at:" in text
    assert "nakama_repo_sha: abc1234" in text


def test_regenerate_writes_file_under_token_budget(tmp_path, monkeypatch):
    """Integration: real regenerate into tmpdir, all 4 blocks present + total < 9k tokens."""
    _stub_all_builders(monkeypatch)
    out_path = tmp_path / "snap.md"
    text = cs.regenerate(dry_run=False, output_path=out_path)

    assert out_path.exists()
    written = out_path.read_text(encoding="utf-8")
    assert written == text

    for header in (
        "## 1. Active priorities",
        "## 2. Recent ADR assumptions",
        "## 3. Top open issues",
        "## 4. Recent MEMORY changes",
    ):
        assert header in written

    total_tokens = cs.estimate_tokens(written)
    assert total_tokens < cs.TOTAL_BUDGET_TOKENS, (
        f"snapshot {total_tokens} tokens exceeds 9k budget"
    )


def test_regenerate_dry_run_does_not_write(tmp_path, monkeypatch):
    _stub_all_builders(monkeypatch)
    out_path = tmp_path / "snap.md"
    text = cs.regenerate(dry_run=True, output_path=out_path)
    assert not out_path.exists()
    assert "## 1. Active priorities" in text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_regenerate_dry_run_prints_to_stdout(monkeypatch, capsys):
    _stub_all_builders(monkeypatch)
    rc = cs.main(["regenerate", "--dry-run"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "## 1. Active priorities" in captured.out


def test_cli_regenerate_writes_and_logs(monkeypatch, capsys, tmp_path):
    _stub_all_builders(monkeypatch)
    target = tmp_path / "snap.md"
    monkeypatch.setattr(cs, "SNAPSHOT_PATH", target)
    rc = cs.main(["regenerate"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "snapshot regenerated" in captured.out
    assert target.exists()
