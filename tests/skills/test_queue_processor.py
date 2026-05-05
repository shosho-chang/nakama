"""Slice 4C — ``.claude/skills/textbook-ingest/scripts/queue_processor.py`` CLI.

The skill calls this from the host shell when invoked with ``--from-queue``:

- ``python queue_processor.py next`` — print the next queued book_id to stdout.
  Exit 0 if a book was found; exit 1 + empty stdout if the queue is empty.
- ``python queue_processor.py mark <book_id> <status> [--chapters N] [--error MSG]``
  — call ``shared.book_queue.mark_status`` with the args. Exit 0 on success;
  exit 2 on bad inputs (unknown status / missing book).

Tests run the script as a subprocess (real CLI behavior), with a fresh
``state.db`` in tmp via ``DB_PATH`` env override.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".claude" / "skills" / "textbook-ingest" / "scripts" / "queue_processor.py"

if not SCRIPT.exists():
    pytest.skip(
        "queue_processor.py is the production module Slice 4C must create",
        allow_module_level=True,
    )


def _env(tmp_path: Path) -> dict:
    e = os.environ.copy()
    e["DB_PATH"] = str(tmp_path / "state.db")
    e["NAKAMA_BOOKS_DIR"] = str(tmp_path / "books")
    e["PYTHONPATH"] = str(REPO_ROOT)
    return e


def _run(*args, env, cwd=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        env=env,
        cwd=cwd or REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _seed(tmp_path: Path, book_id: str = "alpha") -> None:
    """Insert a book row + queue row directly via the shared modules."""
    env = _env(tmp_path)
    code = f"""
import os
os.environ['DB_PATH'] = {str(tmp_path / "state.db")!r}
os.environ['NAKAMA_BOOKS_DIR'] = {str(tmp_path / "books")!r}
import sys
sys.path.insert(0, {str(REPO_ROOT)!r})
from shared.book_storage import insert_book
from shared.book_queue import enqueue
from shared.schemas.books import Book
b = Book(book_id={book_id!r}, title='T', author=None, lang_pair='en-zh',
         genre=None, isbn=None, published_year=None, has_original=True,
         book_version_hash='a' * 64, created_at='2026-05-05T00:00:00+00:00')
insert_book(b)
enqueue({book_id!r})
"""
    r = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=30
    )
    assert r.returncode == 0, f"seed failed: {r.stderr}"


# ---------------------------------------------------------------------------
# next subcommand
# ---------------------------------------------------------------------------


def test_next_prints_queued_book_id(tmp_path):
    _seed(tmp_path, "alpha")
    r = _run("next", env=_env(tmp_path))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "alpha"


def test_next_returns_exit_1_when_empty(tmp_path):
    """No queued book → exit 1, empty stdout. Caller (the skill) treats this
    as 'nothing to ingest, bail cleanly'."""
    env = _env(tmp_path)
    # initialize DB schema by inserting NOTHING — call _get_conn lazy
    init_code = (
        f"import os; os.environ['DB_PATH']={str(tmp_path / 'state.db')!r}; "
        f"import sys; sys.path.insert(0, {str(REPO_ROOT)!r}); "
        "from shared.state import _get_conn; _get_conn()"
    )
    subprocess.run([sys.executable, "-c", init_code], env=env, check=True)
    r = _run("next", env=env)
    assert r.returncode == 1
    assert r.stdout.strip() == ""


# ---------------------------------------------------------------------------
# mark subcommand
# ---------------------------------------------------------------------------


def test_mark_writes_ingesting(tmp_path):
    _seed(tmp_path, "alpha")
    r = _run("mark", "alpha", "ingesting", env=_env(tmp_path))
    assert r.returncode == 0, r.stderr
    # Verify the row updated
    check = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import os; os.environ['DB_PATH']={str(tmp_path / 'state.db')!r}; "
            f"import sys; sys.path.insert(0, {str(REPO_ROOT)!r}); "
            "from shared.state import _get_conn; "
            "row = _get_conn().execute("
            "'SELECT status, started_at FROM book_ingest_queue WHERE book_id=?',"
            " ('alpha',)).fetchone(); "
            "print(row['status']); print(row['started_at'])",
        ],
        env=_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert "ingesting" in check.stdout
    # started_at non-null (something other than the literal "None")
    lines = check.stdout.strip().split("\n")
    assert lines[-1] not in ("None", "")


def test_mark_writes_ingested_with_chapters(tmp_path):
    _seed(tmp_path, "alpha")
    _run("mark", "alpha", "ingesting", env=_env(tmp_path))
    r = _run("mark", "alpha", "ingested", "--chapters", "11", env=_env(tmp_path))
    assert r.returncode == 0, r.stderr


def test_mark_writes_failed_with_error(tmp_path):
    _seed(tmp_path, "alpha")
    _run("mark", "alpha", "ingesting", env=_env(tmp_path))
    r = _run("mark", "alpha", "failed", "--error", "parse_book threw", env=_env(tmp_path))
    assert r.returncode == 0, r.stderr


def test_mark_invalid_status_exit_2(tmp_path):
    _seed(tmp_path, "alpha")
    r = _run("mark", "alpha", "totally-bogus", env=_env(tmp_path))
    assert r.returncode == 2


def test_mark_unknown_book_exit_2(tmp_path):
    """No queue row exists for this book — must exit 2 cleanly, not crash."""
    env = _env(tmp_path)
    # Init DB without seeding
    init_code = (
        f"import os; os.environ['DB_PATH']={str(tmp_path / 'state.db')!r}; "
        f"import sys; sys.path.insert(0, {str(REPO_ROOT)!r}); "
        "from shared.state import _get_conn; _get_conn()"
    )
    subprocess.run([sys.executable, "-c", init_code], env=env, check=True)
    r = _run("mark", "ghost-book", "ingesting", env=env)
    assert r.returncode == 2


# ---------------------------------------------------------------------------
# Top-level CLI hygiene
# ---------------------------------------------------------------------------


def test_cli_help_lists_subcommands(tmp_path):
    r = _run("--help", env=_env(tmp_path))
    # `argparse --help` exits 0
    assert r.returncode == 0
    assert "next" in r.stdout
    assert "mark" in r.stdout


def test_cli_unknown_subcommand_exit_2(tmp_path):
    r = _run("party", env=_env(tmp_path))
    # argparse uses exit code 2 for usage errors
    assert r.returncode == 2
