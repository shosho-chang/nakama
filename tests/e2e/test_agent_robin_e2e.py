"""Robin agent E2E happy path (Phase 6 Slice 4).

End-to-end of ``RobinAgent.run()`` — the orchestration layer that scans the
Inbox, copies new files to ``KB/Raw/``, runs ``IngestPipeline.ingest`` (mocked
out here), marks files as processed, and removes the inbox copy.

What is mocked:
- ``shared.config.get_vault_path`` — points at a tmp_path so writes don't
  touch the real vault.
- ``IngestPipeline.ingest`` — replaced with a stub. The real pipeline
  (``agents/robin/ingest.py``) calls multiple LLM endpoints + writes
  half a dozen vault pages; covering its full contract is Slice 4b
  (separate PR — see Slice 4 PR description).

What is NOT mocked:
- ``RobinAgent._scan_inbox`` filename → extension dispatch
- ``shared.state.is_file_processed`` / ``mark_file_processed`` (against
  ``isolated_db`` autouse tmp DB)
- File copy to ``KB/Raw/<dir>/`` (real shutil)
- ``Path.unlink`` removing the inbox file post-ingest

Marker: none. Runs on every CI invocation.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from shared.state import _get_conn


@pytest.fixture
def robin_e2e_env(tmp_path, monkeypatch):
    """Mount tmp_path as the vault and prep a fresh Inbox/kb folder."""
    vault = tmp_path / "vault"
    inbox = vault / "Inbox" / "kb"
    inbox.mkdir(parents=True)

    # Make a fake .md file in Inbox; supported extensions in
    # ``EXTENSION_TO_RAW_DIR`` cover .md / .pdf / .html.
    src_file = inbox / "fake-source.md"
    src_file.write_text(
        "---\ntitle: Fake source\nauthor: Property Test\n---\n\nBody content.",
        encoding="utf-8",
    )

    monkeypatch.setattr("shared.config.get_vault_path", lambda: vault)
    # RobinAgent reads vault on init via ``self.vault = get_vault_path()`` —
    # so we patch BEFORE constructing the agent.
    monkeypatch.setattr("agents.robin.agent.get_vault_path", lambda: vault)
    return {"vault": vault, "inbox": inbox, "src_file": src_file}


def test_robin_run_happy_path_orchestration(robin_e2e_env, monkeypatch):
    """Inbox file → copy to Raw → pipeline.ingest called → mark processed → unlink."""
    fake_ingest = MagicMock(return_value=None)
    monkeypatch.setattr("agents.robin.ingest.IngestPipeline.ingest", fake_ingest, raising=True)

    from agents.robin.agent import RobinAgent

    agent = RobinAgent(interactive=False)
    summary = agent.run()

    assert "1/1" in summary

    # Pipeline was invoked exactly once with kwargs aligned to its real signature
    fake_ingest.assert_called_once()
    _, kw = fake_ingest.call_args
    assert kw["raw_path"].name == "fake-source.md"
    assert kw["source_type"] == "article"
    assert kw["interactive"] is False

    # Source copied to KB/Raw/Articles/ (per EXTENSION_TO_RAW_DIR['.md'])
    raw_copy = robin_e2e_env["vault"] / "KB" / "Raw" / "Articles" / "fake-source.md"
    assert raw_copy.exists()
    assert raw_copy.read_text(encoding="utf-8").startswith("---")

    # Inbox file removed; SQLite processed-set updated for the inbox path.
    # (RobinAgent calls ``mark_file_processed(file_path, ...)`` BEFORE
    # ``file_path.unlink()`` — the persisted path is the inbox copy, not the
    # raw copy. We assert via direct DB read because ``is_file_processed``
    # re-hashes the path which fails after unlink.)
    assert not robin_e2e_env["src_file"].exists()
    row = (
        _get_conn()
        .execute(
            "SELECT agent, status FROM files_processed WHERE file_path = ?",
            (str(robin_e2e_env["src_file"]),),
        )
        .fetchone()
    )
    assert row is not None
    assert row["agent"] == "robin"
    assert row["status"] == "done"


def test_robin_run_inbox_empty_returns_no_files(robin_e2e_env, monkeypatch):
    """Inbox/kb exists but contains no files → returns the no-files summary."""
    # Remove the seeded file
    robin_e2e_env["src_file"].unlink()

    # Pipeline must NOT be called when there's nothing to process
    fake_ingest = MagicMock()
    monkeypatch.setattr("agents.robin.ingest.IngestPipeline.ingest", fake_ingest, raising=True)

    from agents.robin.agent import RobinAgent

    agent = RobinAgent(interactive=False)
    summary = agent.run()

    assert summary == "無新檔案"
    fake_ingest.assert_not_called()


def test_robin_run_inbox_missing_returns_skip(tmp_path, monkeypatch):
    """No Inbox/kb dir → returns the skip summary; no pipeline call."""
    vault = tmp_path / "vault"
    vault.mkdir()
    # Note: we do NOT create Inbox/kb

    monkeypatch.setattr("shared.config.get_vault_path", lambda: vault)
    monkeypatch.setattr("agents.robin.agent.get_vault_path", lambda: vault)

    fake_ingest = MagicMock()
    monkeypatch.setattr("agents.robin.ingest.IngestPipeline.ingest", fake_ingest, raising=True)

    from agents.robin.agent import RobinAgent

    agent = RobinAgent(interactive=False)
    summary = agent.run()

    assert "Inbox 不存在" in summary
    fake_ingest.assert_not_called()


# ── Extension dispatch table — locks EXTENSION_TO_RAW_DIR / SOURCE_TYPE drift ─


@pytest.mark.parametrize(
    "filename,expected_raw_dir,expected_source_type",
    [
        pytest.param("doc.md", "Articles", "article", id="md→article"),
        pytest.param("paper.pdf", "Papers", "paper", id="pdf→paper"),
        pytest.param("page.html", "Articles", "article", id="html→article"),
        pytest.param("notes.txt", "Articles", "article", id="txt→article"),
        pytest.param("book.epub", "Books", "book", id="epub→book"),
    ],
)
def test_robin_extension_dispatch_table(
    robin_e2e_env, monkeypatch, filename, expected_raw_dir, expected_source_type
):
    """Each supported extension maps to the correct Raw subdir + source_type."""
    # Replace the seeded .md with a file of the parametrized extension
    robin_e2e_env["src_file"].unlink()
    src_file = robin_e2e_env["inbox"] / filename
    src_file.write_bytes(b"sample content")

    fake_ingest = MagicMock(return_value=None)
    monkeypatch.setattr("agents.robin.ingest.IngestPipeline.ingest", fake_ingest, raising=True)

    from agents.robin.agent import RobinAgent

    RobinAgent(interactive=False).run()

    fake_ingest.assert_called_once()
    _, kw = fake_ingest.call_args
    assert kw["raw_path"].parent.name == expected_raw_dir
    assert kw["source_type"] == expected_source_type


def test_robin_run_pipeline_error_does_not_abort_loop(robin_e2e_env, monkeypatch):
    """If pipeline.ingest raises, the file is NOT marked processed and the loop continues.

    Robin's ``_process_file`` exception handler (agent.py:74-79) logs and
    continues; ``processed`` counter only increments on success. Locks the
    ``X/Y`` semantics in the summary string.

    ``kb_log`` is also patched out: the error path appends to KB/log.md via
    ``shared.obsidian_writer.get_vault_path`` (separate import-binding from
    ``agents.robin.agent.get_vault_path``); rather than chase every binding,
    a no-op stub keeps the test focused on the loop-continuation invariant.
    """
    # Add a second valid file so we can verify "loop continues"
    second_file = robin_e2e_env["inbox"] / "second.md"
    second_file.write_text("body", encoding="utf-8")

    call_count = {"n": 0}

    def flaky_ingest(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("pipeline blew up")
        return None

    monkeypatch.setattr("agents.robin.ingest.IngestPipeline.ingest", flaky_ingest, raising=True)
    monkeypatch.setattr("agents.robin.agent.kb_log", lambda *args, **kwargs: None)

    from agents.robin.agent import RobinAgent

    summary = RobinAgent(interactive=False).run()

    # Both files attempted; only one succeeded
    assert call_count["n"] == 2
    # processed=1 / total=2 — loop continued past the first failure
    assert "1/2" in summary
