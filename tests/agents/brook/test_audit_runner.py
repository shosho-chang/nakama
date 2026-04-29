"""Tests for `agents/brook/audit_runner.py` — issue #232 acceptance.

Unit-test boundary: mock `subprocess.run` so we never invoke the real audit
script. The runner is responsible for:

- Calling subprocess with the right CLI args.
- Parsing the script's `{"output_path": ...}` JSON line.
- Reading the markdown file → frontmatter + suggestion sections.
- Persisting an `AuditSuggestionV1` list (pass/skip excluded).
- Mapping subprocess / parse / persist failures to `AuditRunResult.error_*`.

Per `feedback_test_realism.md` we use real markdown samples shaped exactly
like `audit.py::render_markdown` produces.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agents.brook import audit_runner
from shared import audit_results_store

# ---------------------------------------------------------------------------
# Sample markdown — mirrors `.claude/skills/seo-audit-post/scripts/audit.py`
# `render_markdown` output exactly so the parser is exercised against the
# real shape (anti-realism guard per `feedback_test_realism.md`).
# ---------------------------------------------------------------------------


_SAMPLE_MARKDOWN = """\
---
type: seo-audit-report
schema_version: 1
audit_target: https://shosho.tw/example
target_site: wp_shosho
focus_keyword: 深層睡眠
fetched_at: 2026-04-29T03:00:00+00:00
phase: '1.5 (deterministic + llm)'
generated_by: seo-audit-post (Slice D.2)
pagespeed_strategy: mobile
llm_level: sonnet
gsc_section: included
kb_section: skipped (--no-kb)
summary:
  total: 30
  pass: 20
  warn: 5
  fail: 4
  skip: 1
  overall_grade: B+
---

# SEO Audit — Example Article

## 1. Summary

| 類別 | Pass | Warn | Fail | Skip |
|---|---|---|---|---|
| Metadata | 5 | 0 | 0 | 0 |

**Overall grade: B+**

## 2. Critical Fixes（必修）

### [M1] title 長度 50-60

- **Actual**: title is 42 characters
- **Expected**: 50-60 characters
- **Fix**: extend with focus keyword variant

### [SC2] Article schema missing

- **Actual**: no Article JSON-LD
- **Expected**: WebPage + Article schema present
- **Fix**: add SEOPress Schema → Article block

## 3. Warnings（建議修）

### [H1] H1 lacks focus keyword

- **Actual**: H1 = 'Example Article'
- **Expected**: H1 contains focus_keyword '深層睡眠'

### [I3] image missing alt

- **Actual**: 2 images without alt
- **Expected**: every <img> has descriptive alt
- **Fix**: add alt to all body images

## 4. Info（觀察）

- [Info1] something for context

## 5. PageSpeed Insights Summary

- **Performance**: 88 / 100 (mobile)
"""


def _fake_subprocess_run(
    output_dir: Path,
    *,
    markdown: str = _SAMPLE_MARKDOWN,
    returncode: int = 0,
    stderr: str = "",
    raise_exc: BaseException | None = None,
):
    """Build a `subprocess.run` replacement that writes `markdown` to a file
    inside `output_dir` and returns a `CompletedProcess` whose stdout last
    line is the JSON line the real script emits.
    """
    output_path = output_dir / "audit.md"

    def _impl(cmd, **kwargs):
        if raise_exc is not None:
            raise raise_exc
        # Honor the --output-dir arg by writing the markdown there.
        if returncode == 0:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(markdown, encoding="utf-8")
            stdout = (
                "INFO some log line\n"
                + json.dumps({"output_path": str(output_path)}, ensure_ascii=False)
                + "\n"
            )
        else:
            stdout = "fake stdout\n"
        return subprocess.CompletedProcess(
            args=cmd, returncode=returncode, stdout=stdout, stderr=stderr
        )

    return _impl


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestRunHappy:
    def test_run_persists_suggestions_and_returns_audit_id(self, tmp_path, monkeypatch):
        # The runner uses tempfile.TemporaryDirectory; intercept subprocess.run
        # (used inside `_run_subprocess`) and write our markdown to the output_dir
        # the runner passes.

        captured: dict = {}

        def _spy(cmd, **kwargs):
            # Find --output-dir <path> in cmd
            output_dir = None
            for i, c in enumerate(cmd):
                if c == "--output-dir":
                    output_dir = Path(cmd[i + 1])
            assert output_dir is not None, "expected --output-dir in cmd"
            captured["cmd"] = list(cmd)
            captured["output_dir"] = output_dir
            return _fake_subprocess_run(output_dir)(cmd, **kwargs)

        monkeypatch.setattr("agents.brook.audit_runner.subprocess.run", _spy)

        result = audit_runner.run(
            "https://shosho.tw/example",
            target_site="wp_shosho",
            wp_post_id=42,
            focus_keyword="深層睡眠",
        )

        assert result.status == "ok"
        assert isinstance(result.audit_id, int)
        assert result.error_stage is None

        # Persisted row matches frontmatter counts + grade.
        row = audit_results_store.get_by_id(result.audit_id)
        assert row is not None
        assert row["overall_grade"] == "B+"
        assert row["pass_count"] == 20
        assert row["warn_count"] == 5
        assert row["fail_count"] == 4
        assert row["skip_count"] == 1
        assert row["target_site"] == "wp_shosho"
        assert row["wp_post_id"] == 42
        assert row["focus_keyword"] == "深層睡眠"
        assert row["url"] == "https://shosho.tw/example"
        assert row["raw_markdown"] == _SAMPLE_MARKDOWN

        # 4 suggestions persisted: 2 from §2 (fail) + 2 from §3 (warn).
        # (`pass`/`skip` rules are NOT in the markdown sections we parse.)
        suggestions = row["suggestions"]
        assert len(suggestions) == 4
        rule_ids = {s.rule_id for s in suggestions}
        assert rule_ids == {"M1", "SC2", "H1", "I3"}

        m1 = next(s for s in suggestions if s.rule_id == "M1")
        assert m1.severity == "fail"
        assert m1.title == "title 長度 50-60"
        assert m1.current_value == "title is 42 characters"
        assert m1.suggested_value == "50-60 characters"
        assert m1.rationale == "extend with focus keyword variant"
        assert m1.status == "pending"

        h1 = next(s for s in suggestions if s.rule_id == "H1")
        assert h1.severity == "warn"
        assert h1.rationale == ""  # no Fix line in the sample warn block

        # Subprocess was invoked with the right shape.
        cmd = captured["cmd"]
        assert "--url" in cmd
        assert cmd[cmd.index("--url") + 1] == "https://shosho.tw/example"
        assert "--no-kb" in cmd
        assert "--focus-keyword" in cmd
        assert cmd[cmd.index("--focus-keyword") + 1] == "深層睡眠"

    def test_run_target_site_resolved_from_url_when_omitted(self, monkeypatch):
        def _spy(cmd, **kwargs):
            output_dir = Path(cmd[cmd.index("--output-dir") + 1])
            return _fake_subprocess_run(output_dir)(cmd, **kwargs)

        monkeypatch.setattr("agents.brook.audit_runner.subprocess.run", _spy)

        result = audit_runner.run("https://shosho.tw/example")
        assert result.status == "ok"
        row = audit_results_store.get_by_id(result.audit_id)
        # `shosho.tw` host should map to `wp_shosho` via site_mapping.
        assert row is not None
        assert row["target_site"] == "wp_shosho"

    def test_external_url_resolves_to_none_target_site(self, monkeypatch):
        def _spy(cmd, **kwargs):
            output_dir = Path(cmd[cmd.index("--output-dir") + 1])
            return _fake_subprocess_run(output_dir)(cmd, **kwargs)

        monkeypatch.setattr("agents.brook.audit_runner.subprocess.run", _spy)

        result = audit_runner.run("https://unknown.test/x")
        assert result.status == "ok"
        row = audit_results_store.get_by_id(result.audit_id)
        assert row is not None
        assert row["target_site"] is None


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


class TestRunErrors:
    def test_subprocess_nonzero_exit_maps_to_error_subprocess(self, monkeypatch):
        def _fake(cmd, **kwargs):
            output_dir = Path(cmd[cmd.index("--output-dir") + 1])
            return _fake_subprocess_run(output_dir, returncode=1, stderr="boom")(cmd, **kwargs)

        monkeypatch.setattr("agents.brook.audit_runner.subprocess.run", _fake)

        result = audit_runner.run("https://shosho.tw/x")
        assert result.status == "error"
        assert result.audit_id is None
        assert result.error_stage == "subprocess"
        assert "boom" in (result.error_message or "")

    def test_subprocess_timeout_maps_to_error_subprocess(self, monkeypatch):
        timeout_exc = subprocess.TimeoutExpired(cmd=["audit"], timeout=300)

        def _fake(cmd, **kwargs):
            raise timeout_exc

        monkeypatch.setattr("agents.brook.audit_runner.subprocess.run", _fake)

        result = audit_runner.run("https://shosho.tw/x")
        assert result.status == "error"
        assert result.error_stage == "subprocess"
        assert "exceeded 300s" in (result.error_message or "")

    def test_missing_frontmatter_maps_to_error_parse(self, monkeypatch):
        def _fake(cmd, **kwargs):
            output_dir = Path(cmd[cmd.index("--output-dir") + 1])
            return _fake_subprocess_run(output_dir, markdown="# no frontmatter here\n")(
                cmd, **kwargs
            )

        monkeypatch.setattr("agents.brook.audit_runner.subprocess.run", _fake)

        result = audit_runner.run("https://shosho.tw/x")
        assert result.status == "error"
        assert result.error_stage == "parse"
        assert "frontmatter" in (result.error_message or "")

    def test_unknown_grade_maps_to_error_parse(self, monkeypatch):
        bad_md = (
            "---\n"
            "summary:\n"
            "  pass: 0\n  warn: 0\n  fail: 0\n  skip: 0\n"
            "  overall_grade: ZZZ\n"
            "---\n\n"
            "## 2. Critical Fixes\n\n"
        )

        def _fake(cmd, **kwargs):
            output_dir = Path(cmd[cmd.index("--output-dir") + 1])
            return _fake_subprocess_run(output_dir, markdown=bad_md)(cmd, **kwargs)

        monkeypatch.setattr("agents.brook.audit_runner.subprocess.run", _fake)

        result = audit_runner.run("https://shosho.tw/x")
        assert result.status == "error"
        assert result.error_stage == "parse"
        assert "ZZZ" in (result.error_message or "")

    def test_malformed_json_line_maps_to_error_subprocess(self, monkeypatch):
        def _fake(cmd, **kwargs):
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="not json\n", stderr=""
            )

        monkeypatch.setattr("agents.brook.audit_runner.subprocess.run", _fake)

        result = audit_runner.run("https://shosho.tw/x")
        assert result.status == "error"
        assert result.error_stage == "subprocess"

    def test_persist_failure_maps_to_error_persist(self, monkeypatch):
        # Force `audit_results_store.insert_run` to raise.
        def _fake(cmd, **kwargs):
            output_dir = Path(cmd[cmd.index("--output-dir") + 1])
            return _fake_subprocess_run(output_dir)(cmd, **kwargs)

        monkeypatch.setattr("agents.brook.audit_runner.subprocess.run", _fake)
        boom = MagicMock(side_effect=RuntimeError("DB down"))
        monkeypatch.setattr("agents.brook.audit_runner.audit_results_store.insert_run", boom)

        result = audit_runner.run("https://shosho.tw/x")
        assert result.status == "error"
        assert result.error_stage == "persist"
        assert "RuntimeError" in (result.error_message or "")
        assert "DB down" in (result.error_message or "")


# ---------------------------------------------------------------------------
# Suggestions parsing edge cases
# ---------------------------------------------------------------------------


class TestSuggestionsParsing:
    def test_empty_critical_section_yields_no_fail_suggestions(self, monkeypatch):
        empty_md = (
            "---\n"
            "summary:\n  pass: 1\n  warn: 0\n  fail: 0\n  skip: 0\n  overall_grade: A\n"
            "---\n\n"
            "## 2. Critical Fixes（必修）\n\n（無）\n\n"
            "## 3. Warnings（建議修）\n\n（無）\n"
        )

        def _fake(cmd, **kwargs):
            output_dir = Path(cmd[cmd.index("--output-dir") + 1])
            return _fake_subprocess_run(output_dir, markdown=empty_md)(cmd, **kwargs)

        monkeypatch.setattr("agents.brook.audit_runner.subprocess.run", _fake)

        result = audit_runner.run("https://shosho.tw/x")
        assert result.status == "ok"
        row = audit_results_store.get_by_id(result.audit_id)
        assert row is not None
        assert row["suggestions"] == []

    def test_pass_and_skip_rules_not_persisted(self, monkeypatch):
        """The summary section + §4 Info / §5 PageSpeed are NOT parsed into
        suggestions; only §2 fail + §3 warn blocks land in `suggestions_json`.
        """

        def _fake(cmd, **kwargs):
            output_dir = Path(cmd[cmd.index("--output-dir") + 1])
            return _fake_subprocess_run(output_dir)(cmd, **kwargs)

        monkeypatch.setattr("agents.brook.audit_runner.subprocess.run", _fake)

        result = audit_runner.run("https://shosho.tw/x")
        assert result.status == "ok"
        row = audit_results_store.get_by_id(result.audit_id)
        assert row is not None
        # Sample has pass=20, skip=1 in summary; none of those show up.
        for s in row["suggestions"]:
            assert s.severity in ("fail", "warn")


# ---------------------------------------------------------------------------
# Form validation guard
# ---------------------------------------------------------------------------


class TestSubprocessShape:
    def test_focus_keyword_omitted_when_empty(self, monkeypatch):
        captured: dict = {}

        def _spy(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            output_dir = Path(cmd[cmd.index("--output-dir") + 1])
            return _fake_subprocess_run(output_dir)(cmd, **kwargs)

        monkeypatch.setattr("agents.brook.audit_runner.subprocess.run", _spy)
        audit_runner.run("https://shosho.tw/x", focus_keyword="")
        assert "--focus-keyword" not in captured["cmd"]


@pytest.fixture(autouse=True)
def _disable_real_subprocess(monkeypatch):
    """Belt-and-suspenders — never call the real subprocess in this test
    file.  Each test that needs a fake patches its own `subprocess.run`;
    this fixture catches any test that forgets and would otherwise hit
    the network / read disk for the real audit script.
    """

    def _refuse(cmd, **kwargs):  # noqa: ARG001
        raise AssertionError(f"subprocess.run was called without a per-test patch: cmd={cmd!r}")

    monkeypatch.setattr("agents.brook.audit_runner.subprocess.run", _refuse)
