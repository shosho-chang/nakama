"""Tests for FrankyAgent vault-audit wiring (ADR-028 §11 β, PR-C1 B3)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agents.franky.agent import FrankyAgent
from scripts.vault_layout_audit import AuditFinding, AuditReport


def _make_agent(vault_path):
    """Construct a FrankyAgent with vault path stubbed."""
    with (
        patch("agents.franky.agent.get_vault_path", return_value=vault_path),
        patch("agents.franky.agent.get_agent_config", return_value={}),
    ):
        return FrankyAgent()


@pytest.fixture
def agent(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    return _make_agent(vault)


def test_run_vault_audit_appends_markdown(agent, monkeypatch):
    """run_audit's report markdown is what _run_vault_audit returns."""
    canned = AuditReport(findings=[AuditFinding("folder_diff", "warn", "Foo", "undeclared")])
    monkeypatch.setattr("scripts.vault_layout_audit.run_audit", lambda *_a, **_kw: canned)

    md = agent._run_vault_audit()

    assert md is not None
    assert "Vault Audit" in md
    assert "Foo" in md


def test_run_vault_audit_logs_warning_on_errors(agent, monkeypatch, caplog):
    canned = AuditReport(findings=[AuditFinding("folder_diff", "error", "Files", "regression")])
    monkeypatch.setattr("scripts.vault_layout_audit.run_audit", lambda *_a, **_kw: canned)
    # kb_log resolves the real vault path via shared.config — in CI that path
    # does not exist, the call raises, and the broad except in
    # _run_vault_audit would mask the real assertion target. Mock it out.
    monkeypatch.setattr("agents.franky.agent.kb_log", lambda *_a, **_kw: None)

    with caplog.at_level("WARNING"):
        md = agent._run_vault_audit()

    assert md is not None
    assert any("error-severity" in record.message for record in caplog.records)


def test_run_vault_audit_returns_none_on_missing_vault(tmp_path):
    """If vault root is gone, audit no-ops."""
    missing = tmp_path / "nonexistent"
    agent = _make_agent(missing)
    md = agent._run_vault_audit()
    assert md is None


def test_run_vault_audit_swallows_exceptions(agent, monkeypatch):
    """Audit failure must never crash the weekly digest."""
    monkeypatch.setattr(
        "scripts.vault_layout_audit.run_audit",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("audit blew up")),
    )
    md = agent._run_vault_audit()
    assert md is None


def test_run_appends_audit_to_report_body(tmp_path, monkeypatch):
    """End-to-end: FrankyAgent.run() injects audit markdown into report.body_markdown."""
    vault = tmp_path / "vault"
    vault.mkdir()

    fake_health = MagicMock(status="ok", notes=[])
    fake_report = MagicMock(
        period="2026-W21",
        period_start="2026-05-18",
        open_tasks=0,
        closed_tasks=0,
        blocked_count=0,
        health=fake_health,
        body_markdown="## Original body\n\ncontent here.\n",
    )

    with (
        patch("agents.franky.agent.get_vault_path", return_value=vault),
        patch("agents.franky.agent.get_agent_config", return_value={}),
        patch("agents.franky.agent.read_repo_page", return_value="backlog"),
        patch("agents.franky.agent.list_repo_files", return_value=[]),
        patch("agents.franky.agent.kb_log"),
        patch("agents.franky.agent.emit"),
    ):
        agent = FrankyAgent()
        agent.reporter = MagicMock()
        agent.reporter.generate.return_value = fake_report
        agent.reporter.write.return_value = "data/agent_reports/franky/weekly/2026-W21.md"
        agent.get_memory_context = MagicMock(return_value=None)
        # Force a deterministic audit return so we don't depend on real vault state.
        agent._run_vault_audit = MagicMock(return_value="## Vault Audit\n\n_No drift detected._\n")

        agent.run()

    write_args = agent.reporter.write.call_args
    written_report = write_args[0][0]
    assert "Vault Audit" in written_report.body_markdown
    assert "## Original body" in written_report.body_markdown  # original preserved


def test_run_skips_audit_append_when_none(tmp_path):
    """If audit returns None, report body is unchanged."""
    vault = tmp_path / "vault"
    vault.mkdir()

    fake_health = MagicMock(status="ok", notes=[])
    original_body = "## Original body\n\nunchanged.\n"
    fake_report = MagicMock(
        period="2026-W21",
        period_start="2026-05-18",
        open_tasks=0,
        closed_tasks=0,
        blocked_count=0,
        health=fake_health,
        body_markdown=original_body,
    )

    with (
        patch("agents.franky.agent.get_vault_path", return_value=vault),
        patch("agents.franky.agent.get_agent_config", return_value={}),
        patch("agents.franky.agent.read_repo_page", return_value="backlog"),
        patch("agents.franky.agent.list_repo_files", return_value=[]),
        patch("agents.franky.agent.kb_log"),
        patch("agents.franky.agent.emit"),
    ):
        agent = FrankyAgent()
        agent.reporter = MagicMock()
        agent.reporter.generate.return_value = fake_report
        agent.reporter.write.return_value = "x.md"
        agent.get_memory_context = MagicMock(return_value=None)
        agent._run_vault_audit = MagicMock(return_value=None)

        agent.run()

    written_report = agent.reporter.write.call_args[0][0]
    assert written_report.body_markdown == original_body
