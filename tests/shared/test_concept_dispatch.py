"""Tests for shared.concept_dispatch (ADR-020 S2).

Tests use a temp vault via VAULT_PATH env var override.  LLM calls inside
``kb_writer.upsert_concept_page`` (update_merge) are monkeypatched to avoid
real API calls.  The advisory lock tests use an in-memory SQLite connection.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from shared.concept_dispatch import (
    IngestFailError,
    dispatch_concept,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def vault(tmp_path: Path, monkeypatch) -> Path:
    """Point get_vault_path() at a temporary directory."""
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    concepts_dir = tmp_path / "KB" / "Wiki" / "Concepts"
    concepts_dir.mkdir(parents=True)
    return tmp_path


@pytest.fixture()
def stub_llm(monkeypatch):
    """Stub out the LLM diff-merge call so update_merge tests stay fast."""
    monkeypatch.setattr(
        "shared.kb_writer._ask_llm",
        lambda prompt, **kw: "## Definition\n\nMerged content.\n",
    )


@pytest.fixture()
def mem_conn() -> sqlite3.Connection:
    """In-memory SQLite connection for advisory lock tests."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA journal_mode=WAL")
    yield conn
    conn.close()


def _concept_path(vault: Path, slug: str) -> Path:
    return vault / "KB" / "Wiki" / "Concepts" / f"{slug}.md"


def _parse_concept(vault: Path, slug: str) -> tuple[dict, str]:
    text = _concept_path(vault, slug).read_text(encoding="utf-8")
    assert text.startswith("---\n")
    end = text.index("\n---\n", 4)
    fm = yaml.safe_load(text[4:end])
    body = text[end + 5 :]
    return fm, body


# ---------------------------------------------------------------------------
# Basic dispatch — create
# ---------------------------------------------------------------------------


def test_create_writes_concept_page(vault):
    dispatch_concept(
        "creatine",
        "create",
        "[[Sources/Books/bse-2024/ch1]]",
        title="肌酸",
        extracted_body="## Definition\n\nCreatine is an organic compound.\n",
        domain="sport-nutrition",
    )
    assert _concept_path(vault, "creatine").exists()


def test_create_mentioned_in_set(vault):
    dispatch_concept(
        "creatine",
        "create",
        "[[Sources/Books/bse-2024/ch1]]",
        title="肌酸",
        extracted_body="## Definition\n\nCreatine is an organic compound.\n",
        domain="sport-nutrition",
    )
    fm, _ = _parse_concept(vault, "creatine")
    assert "[[Sources/Books/bse-2024/ch1]]" in fm["mentioned_in"]


# ---------------------------------------------------------------------------
# en_source_terms — create
# ---------------------------------------------------------------------------


def test_create_en_source_terms_in_frontmatter(vault):
    dispatch_concept(
        "creatine",
        "create",
        "[[Sources/Books/bse-2024/ch1]]",
        title="肌酸",
        extracted_body="## Definition\n\nCreatine is an organic compound.\n",
        domain="sport-nutrition",
        en_source_terms=["creatine", "creatine monohydrate"],
    )
    fm, _ = _parse_concept(vault, "creatine")
    assert "creatine" in fm["en_source_terms"]
    assert "creatine monohydrate" in fm["en_source_terms"]


def test_create_en_source_terms_empty_by_default(vault):
    dispatch_concept(
        "creatine",
        "create",
        "[[Sources/Books/bse-2024/ch1]]",
        title="肌酸",
        extracted_body="## Definition\n\nCreatine is an organic compound.\n",
        domain="sport-nutrition",
    )
    fm, _ = _parse_concept(vault, "creatine")
    # field may be absent or empty list — both acceptable
    terms = fm.get("en_source_terms") or []
    assert terms == []


# ---------------------------------------------------------------------------
# en_source_terms — update_merge extends with dedup
# ---------------------------------------------------------------------------


def _create_concept(vault, slug, *, terms=None):
    terms = terms or ["creatine"]
    dispatch_concept(
        slug,
        "create",
        "[[Sources/Books/bse-2024/ch1]]",
        title=slug,
        extracted_body="## Definition\n\nInitial definition content here.\n",
        domain="sport-nutrition",
        en_source_terms=terms,
    )


def test_update_merge_extends_en_source_terms(vault, stub_llm):
    _create_concept(vault, "creatine", terms=["creatine"])
    dispatch_concept(
        "creatine",
        "update_merge",
        "[[Sources/Books/bse-2024/ch2]]",
        extracted_body="## Definition\n\nExtra detail about creatine monohydrate.\n",
        en_source_terms=["creatine monohydrate"],
    )
    fm, _ = _parse_concept(vault, "creatine")
    terms = fm.get("en_source_terms") or []
    assert "creatine" in terms
    assert "creatine monohydrate" in terms


def test_update_merge_en_source_terms_dedup(vault, stub_llm):
    _create_concept(vault, "creatine", terms=["creatine"])
    dispatch_concept(
        "creatine",
        "update_merge",
        "[[Sources/Books/bse-2024/ch2]]",
        extracted_body="## Definition\n\nMore content.\n",
        en_source_terms=["creatine"],  # same term already present
    )
    fm, _ = _parse_concept(vault, "creatine")
    terms = fm.get("en_source_terms") or []
    assert terms.count("creatine") == 1  # no duplicate


def test_noop_extends_en_source_terms(vault):
    _create_concept(vault, "creatine", terms=["creatine"])
    dispatch_concept(
        "creatine",
        "noop",
        "[[Sources/Books/bse-2024/ch3]]",
        en_source_terms=["phosphocreatine"],
    )
    fm, _ = _parse_concept(vault, "creatine")
    terms = fm.get("en_source_terms") or []
    assert "creatine" in terms
    assert "phosphocreatine" in terms


# ---------------------------------------------------------------------------
# maturity_level — written to frontmatter on create
# ---------------------------------------------------------------------------


def test_create_maturity_level_l2_in_frontmatter(vault):
    dispatch_concept(
        "cori-cycle",
        "create",
        "[[Sources/Books/bse-2024/ch3]]",
        title="Cori-cycle",
        extracted_body="## Definition\n\nThe Cori cycle is a metabolic cycle.\n",
        domain="sport-nutrition",
        maturity_level="L2",
        high_value_signals=["section_heading", "bolded_define"],
    )
    fm, _ = _parse_concept(vault, "cori-cycle")
    assert fm.get("maturity_level") == "L2"


def test_create_high_value_signals_in_frontmatter(vault):
    dispatch_concept(
        "cori-cycle",
        "create",
        "[[Sources/Books/bse-2024/ch3]]",
        title="Cori-cycle",
        extracted_body="## Definition\n\nThe Cori cycle is a metabolic cycle.\n",
        domain="sport-nutrition",
        maturity_level="L2",
        high_value_signals=["section_heading", "bolded_define"],
    )
    fm, _ = _parse_concept(vault, "cori-cycle")
    sigs = fm.get("high_value_signals") or []
    assert "section_heading" in sigs
    assert "bolded_define" in sigs


# ---------------------------------------------------------------------------
# Hard invariant: placeholder stub detection
# ---------------------------------------------------------------------------


def test_placeholder_stub_raises_ingest_fail(vault, stub_llm):
    with pytest.raises(IngestFailError, match="placeholder stub"):
        dispatch_concept(
            "creatine",
            "create",
            "[[Sources/Books/bse-2024/ch1]]",
            title="肌酸",
            extracted_body=(
                "## Definition\n\nWill be enriched as Robin processes future ingests.\n"
            ),
            domain="sport-nutrition",
        )


def test_phase_b_stub_text_raises_ingest_fail(vault, stub_llm):
    with pytest.raises(IngestFailError, match="placeholder stub"):
        dispatch_concept(
            "creatine",
            "create",
            "[[Sources/Books/bse-2024/ch1]]",
            title="肌酸",
            extracted_body=("## Definition\n\nStub — auto-created by Phase B reconciliation.\n"),
            domain="sport-nutrition",
        )


# ---------------------------------------------------------------------------
# Hard invariant: L3 active body word count
# ---------------------------------------------------------------------------

_L3_BODY_SHORT = "## Definition\n\nShort body with only ten words in here."

_L3_BODY_OK = "## Definition\n\n" + (
    "Creatine is an organic compound found in muscle tissue. " * 20
)


def test_l3_active_short_body_raises_ingest_fail(vault):
    with pytest.raises(IngestFailError, match="body word count"):
        dispatch_concept(
            "creatine",
            "create",
            "[[Sources/Books/bse-2024/ch1]]",
            title="肌酸",
            extracted_body=_L3_BODY_SHORT,
            domain="sport-nutrition",
            maturity_level="L3",
        )


def test_l3_active_sufficient_body_passes(vault):
    # Must not raise
    dispatch_concept(
        "creatine",
        "create",
        "[[Sources/Books/bse-2024/ch1]]",
        title="肌酸",
        extracted_body=_L3_BODY_OK,
        domain="sport-nutrition",
        maturity_level="L3",
    )
    assert _concept_path(vault, "creatine").exists()


def test_l2_stub_short_body_allowed(vault):
    # L2 stub is allowed to have < 200 words — it's a productive workflow state
    dispatch_concept(
        "cori-cycle",
        "create",
        "[[Sources/Books/bse-2024/ch2]]",
        title="Cori-cycle",
        extracted_body=_L3_BODY_SHORT,
        domain="sport-nutrition",
        maturity_level="L2",
    )
    assert _concept_path(vault, "cori-cycle").exists()


# ---------------------------------------------------------------------------
# BSE ch1 → ch2 sequential: ch1 creates, ch2 update_merges (not duplicate)
# ---------------------------------------------------------------------------


def test_sequential_ch1_create_ch2_update_merge(vault, stub_llm):
    """ch1 creates concept; ch2 triggers update_merge, not a duplicate create."""
    slug = "gut-microbiota"

    # ch1 dispatch — concept does not exist → create
    dispatch_concept(
        slug,
        "create",
        "[[Sources/Books/bse-2024/ch1]]",
        title="腸道菌群",
        extracted_body=(
            "## Definition\n\nThe gut microbiota comprises trillions of microorganisms.\n"
        ),
        domain="sport-nutrition",
        en_source_terms=["gut microbiota", "intestinal flora"],
    )
    assert _concept_path(vault, slug).exists()
    fm1, _ = _parse_concept(vault, slug)
    assert fm1["mentioned_in"] == ["[[Sources/Books/bse-2024/ch1]]"]

    # ch2 dispatch — concept exists → update_merge (NOT a second create)
    dispatch_concept(
        slug,
        "update_merge",
        "[[Sources/Books/bse-2024/ch2]]",
        extracted_body="## Core Mechanism\n\nGut microbiome affects nutrient absorption.\n",
        en_source_terms=["gut microbiome"],
    )
    fm2, _ = _parse_concept(vault, slug)

    # Both source links should appear in mentioned_in
    assert "[[Sources/Books/bse-2024/ch1]]" in fm2["mentioned_in"]
    assert "[[Sources/Books/bse-2024/ch2]]" in fm2["mentioned_in"]

    # en_source_terms extended from ch1 + ch2
    terms2 = fm2.get("en_source_terms") or []
    assert "gut microbiota" in terms2
    assert "intestinal flora" in terms2
    assert "gut microbiome" in terms2


def test_sequential_double_create_falls_back_to_update_merge(vault, stub_llm):
    """Calling action=create twice does NOT produce two separate pages."""
    slug = "atp"

    dispatch_concept(
        slug,
        "create",
        "[[Sources/Books/bse-2024/ch1]]",
        title="ATP",
        extracted_body="## Definition\n\nATP is the energy currency of cells.\n",
        domain="sport-nutrition",
    )
    # Second call with action=create should fall back to update_merge
    dispatch_concept(
        slug,
        "create",
        "[[Sources/Books/bse-2024/ch2]]",
        title="ATP",
        extracted_body="## Definition\n\nATP provides energy for muscle contraction.\n",
        domain="sport-nutrition",
    )

    fm, _ = _parse_concept(vault, slug)
    # Both chapters in mentioned_in
    assert "[[Sources/Books/bse-2024/ch1]]" in fm["mentioned_in"]
    assert "[[Sources/Books/bse-2024/ch2]]" in fm["mentioned_in"]


# ---------------------------------------------------------------------------
# Per-concept advisory lock
# ---------------------------------------------------------------------------


def test_advisory_lock_acquired_and_released(vault, mem_conn):
    """dispatch_concept with lock_conn acquires and releases the lock."""
    dispatch_concept(
        "creatine",
        "create",
        "[[Sources/Books/bse-2024/ch1]]",
        title="肌酸",
        extracted_body="## Definition\n\nCreatine is an organic compound.\n",
        domain="sport-nutrition",
        lock_conn=mem_conn,
    )
    # After dispatch, lock row must be gone
    row = mem_conn.execute(
        "SELECT key FROM advisory_locks WHERE key = 'concept_creatine'"
    ).fetchone()
    assert row is None


def test_advisory_lock_key_is_concept_prefixed(vault, mem_conn, monkeypatch):
    """Lock key uses 'concept_{slug}' namespace."""
    acquired_keys: list[str] = []
    original_lock = __import__("shared.locks", fromlist=["advisory_lock"]).advisory_lock

    from contextlib import contextmanager

    @contextmanager
    def _spy_lock(conn, key, **kw):
        acquired_keys.append(key)
        with original_lock(conn, key, **kw):
            yield

    monkeypatch.setattr("shared.concept_dispatch.advisory_lock", _spy_lock)

    dispatch_concept(
        "creatine",
        "create",
        "[[Sources/Books/bse-2024/ch1]]",
        title="肌酸",
        extracted_body="## Definition\n\nCreatine is an organic compound.\n",
        domain="sport-nutrition",
        lock_conn=mem_conn,
    )
    assert "concept_creatine" in acquired_keys
