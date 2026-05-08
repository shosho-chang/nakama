"""Golden chapter end-to-end fixture test (issue #501, P0.7).

Validates that the frozen BSE ch3 staging output approved by 修修 on 2026-05-08
passes all 7 acceptance conditions defined in compute_acceptance_7, and that
running the acceptance gate twice produces identical deterministic results.

Fixture directory: tests/fixtures/golden/bse-ch3-expected/
  bse-ch3.md          — source page (verbatim body + appendix)
  concepts/           — dispatched L2/L3 concept pages
  _alias_map.md       — L1 alias entries recorded by ch3 ingest
  dispatch_log.json   — Phase 2 dispatch log used to drive C1/C4/C6 checks

HITL approval: 修修 reviewed the staging output after commit a70ca16 and
posted explicit "Approved" on issue #501 before this fixture was frozen.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from scripts.run_s8_preflight import compute_acceptance_7  # noqa: E402

_FIXTURE_DIR = _REPO_ROOT / "tests" / "fixtures" / "golden" / "bse-ch3-expected"
_BSE_BOOK_ID = "biochemistry-for-sport-and-exercise-maclaren"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_staging(tmp_path: Path) -> tuple[Path, Path, list[dict]]:
    """Copy fixture files into a tmp staging tree; return (source_page, concepts_dir, log)."""
    # Mirror the path structure that run_s8_preflight.py writes to staging.
    source_dir = tmp_path / "KB" / "Wiki.staging" / "Sources" / "Books" / _BSE_BOOK_ID
    source_dir.mkdir(parents=True)
    source_page = source_dir / "ch3.md"
    source_page.write_text(
        (_FIXTURE_DIR / "bse-ch3.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    concepts_dir = tmp_path / "KB" / "Wiki.staging" / "Concepts"
    concepts_dir.mkdir(parents=True)
    for concept_file in (_FIXTURE_DIR / "concepts").iterdir():
        (concepts_dir / concept_file.name).write_text(
            concept_file.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    dispatch_log = json.loads((_FIXTURE_DIR / "dispatch_log.json").read_text(encoding="utf-8"))
    return source_page, concepts_dir, dispatch_log


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_fixture_dir_exists() -> None:
    """Sanity gate: fixture directory and required files are present."""
    assert _FIXTURE_DIR.is_dir(), f"Golden fixture dir missing: {_FIXTURE_DIR}"
    assert (_FIXTURE_DIR / "bse-ch3.md").is_file()
    assert (_FIXTURE_DIR / "concepts").is_dir()
    assert (_FIXTURE_DIR / "dispatch_log.json").is_file()
    assert (_FIXTURE_DIR / "_alias_map.md").is_file()
    # Must have at least one concept page
    assert any((_FIXTURE_DIR / "concepts").glob("*.md"))


def test_bse_ch3_round_trip(tmp_path: Path) -> None:
    """Golden chapter round-trip: frozen fixture passes all 7 acceptance conditions.

    Invariant: calling compute_acceptance_7 on the same frozen fixture twice
    produces bit-identical AcceptanceResult7 (pure deterministic function).
    """
    source_page, concepts_dir, dispatch_log = _setup_staging(tmp_path)

    # Live KB/Wiki/Concepts must be empty so C4 (no live writes) passes.
    live_dir = tmp_path / "KB" / "Wiki" / "Concepts"
    live_dir.mkdir(parents=True)

    # --- First pass ---
    acc1 = compute_acceptance_7(
        source_page_path=source_page,
        dispatch_log=dispatch_log,
        staging_concepts_dir=concepts_dir,
        live_concepts_dir=live_dir,
    )

    # All 7 conditions must pass.
    assert acc1.c1_dispatch_ok, f"C1 failed: {acc1.c1_dispatch_errors}"
    assert acc1.c2_wikilinks_resolve_ok, f"C2 failed: {acc1.c2_unresolved}"
    assert acc1.c3_fm_body_count_ok, f"C3 failed: FM={acc1.c3_fm_count} body={acc1.c3_body_count}"
    assert acc1.c4_no_live_writes_ok, f"C4 failed: {acc1.c4_live_slugs}"
    assert acc1.c5_no_placeholders_ok, f"C5 failed: {acc1.c5_placeholder_hits}"
    assert acc1.c6_no_collisions_ok, f"C6 failed: {acc1.c6_collision_pairs}"
    assert acc1.c7_golden_ok, f"C7 failed: {acc1.reasons}"
    assert not acc1.c7_skipped, "C7 must be active (not skipped) for BSE ch3"
    assert acc1.acceptance_pass, f"Gate failed: {acc1.reasons}"

    # --- Second pass — determinism invariant ---
    acc2 = compute_acceptance_7(
        source_page_path=source_page,
        dispatch_log=dispatch_log,
        staging_concepts_dir=concepts_dir,
        live_concepts_dir=live_dir,
    )

    assert acc2.acceptance_pass == acc1.acceptance_pass
    assert acc2.c1_dispatch_ok == acc1.c1_dispatch_ok
    assert acc2.c2_unresolved == acc1.c2_unresolved
    assert acc2.c3_fm_count == acc1.c3_fm_count
    assert acc2.c3_body_count == acc1.c3_body_count
    assert acc2.c4_live_slugs == acc1.c4_live_slugs
    assert acc2.c5_placeholder_hits == acc1.c5_placeholder_hits
    assert acc2.c6_collision_pairs == acc1.c6_collision_pairs
    assert acc2.c7_golden_ok == acc1.c7_golden_ok
    assert acc2.c7_skipped == acc1.c7_skipped
    assert acc2.reasons == acc1.reasons


def test_c7_detects_wikilink_divergence(tmp_path: Path) -> None:
    """C7 catches a regression where the wikilink list diverges from golden.

    Simulates a future ingest that produces an extra or missing concept slug.
    """
    source_page, concepts_dir, dispatch_log = _setup_staging(tmp_path)
    live_dir = tmp_path / "KB" / "Wiki" / "Concepts"
    live_dir.mkdir(parents=True)

    # Tamper: add a spurious wikilink to the source page's appendix.
    original = source_page.read_text(encoding="utf-8")
    tampered = original.replace(
        "## Wikilinks Introduced\n\n- [[atp]]",
        "## Wikilinks Introduced\n\n- [[atp]]\n- [[spurious-concept]]",
    )
    # Also add a FM entry to keep C3 consistent (FM count == body count).
    tampered = tampered.replace(
        "wikilinks_introduced:\n- atp\n",
        "wikilinks_introduced:\n- atp\n- spurious-concept\n",
    )
    source_page.write_text(tampered, encoding="utf-8")

    # Add the spurious concept page so C2 resolves (we're testing C7 in isolation).
    (concepts_dir / "spurious-concept.md").write_text(
        "# Spurious\n\nThis should not appear in the golden fixture.\n",
        encoding="utf-8",
    )

    acc = compute_acceptance_7(
        source_page_path=source_page,
        dispatch_log=dispatch_log,
        staging_concepts_dir=concepts_dir,
        live_concepts_dir=live_dir,
    )

    assert not acc.c7_golden_ok, "C7 should fail when wikilinks diverge from golden"
    assert not acc.c7_skipped
    assert not acc.acceptance_pass
    assert any("C7" in r for r in acc.reasons)
