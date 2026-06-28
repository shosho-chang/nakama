"""Deterministic CI guard for the ingest skill's QUALITY eval spec.

The quality eval itself is LLM-judged (the ingest pipeline's quality is LLM-driven —
summary / concept extraction / HITL judgement — so it can't be asserted
deterministically; see ``.claude/skills/ingest/evals/README.md`` for the with/without
A/B run). What CI *can* guard deterministically is that the eval spec stays well-formed:
quality.json parses, every referenced fixture + assertions file exists, and each
assertion carries a unique id + text + type. This stops the rubric from silently
rotting (a dangling assertions_file, a duplicate id, a fixture renamed away).

Separate from ``evals/evals.json`` (the should_trigger routing eval) and from
``test_ingest_route_c.py`` (the pipeline's deterministic artifact/red-line invariants).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_EVALS_DIR = Path(__file__).resolve().parents[3] / ".claude" / "skills" / "ingest" / "evals"
_QUALITY_JSON = _EVALS_DIR / "quality.json"
_ALLOWED_TYPES = {
    "structural",
    "language",
    "provenance",
    "workflow",
    "filtering",
    "formatting",
    "red-line",
    "content",
}


def _load_quality() -> dict:
    return json.loads(_QUALITY_JSON.read_text(encoding="utf-8"))


def test_quality_json_parses_and_has_cases():
    data = _load_quality()
    assert data["skill_name"] == "ingest"
    assert data["evals"], "quality.json must declare at least one eval case"


def test_every_case_fixture_and_assertions_file_exist():
    data = _load_quality()
    for case in data["evals"]:
        for rel in case.get("files", []):
            assert (_EVALS_DIR / rel).is_file(), f"missing fixture {rel} (eval {case['id']})"
        af = _EVALS_DIR / case["assertions_file"]
        assert af.is_file(), (
            f"missing assertions file {case['assertions_file']} (eval {case['id']})"
        )


def test_assertions_well_formed():
    data = _load_quality()
    for case in data["evals"]:
        spec = json.loads((_EVALS_DIR / case["assertions_file"]).read_text(encoding="utf-8"))
        assertions = spec["assertions"]
        assert assertions, f"eval {case['id']} has no assertions"
        ids = [a["id"] for a in assertions]
        assert len(ids) == len(set(ids)), f"duplicate assertion id in eval {case['id']}"
        for a in assertions:
            assert a["id"] and a["text"], f"assertion missing id/text in eval {case['id']}"
            assert a["type"] in _ALLOWED_TYPES, f"unknown assertion type {a['type']!r}"


def test_article_rubric_covers_repo_specific_invariants():
    """The route-C rubric must encode the repo's own policies, not a generic one —
    notably: provenance separation, the HITL gate, the no-dangling-[[links]] rule
    (PR #955 / ADR-043), full-text D-A, and red-lines."""
    spec = json.loads(
        (_EVALS_DIR / "assertions" / "eval-1-article.json").read_text(encoding="utf-8")
    )
    blob = json.dumps(spec, ensure_ascii=False)
    assert "agent_robin" in blob  # provenance separation (A4)
    assert "HITL" in blob or "accept / defer / exclude" in blob  # gate (A5)
    assert "[[" in blob and "retrieval-first" in blob  # no-dangling-links rule (A7)
    assert "red-line" in {a["type"] for a in spec["assertions"]}  # red-lines graded


@pytest.mark.parametrize("name", ["eval-1-article.json", "eval-2-refusals.json"])
def test_assertion_files_reference_their_fixture(name):
    spec = json.loads((_EVALS_DIR / "assertions" / name).read_text(encoding="utf-8"))
    assert (_EVALS_DIR / spec["fixture"]).is_file()
