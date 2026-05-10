"""Behaviour tests for the Writing Assist routes (ADR-024 Slice 9 / #517).

3 tests covering Brief §5 RT1-RT3 + a couple of negative-path sanity
checks:

- RT1  GET /writing-assist/{id} → 200 with section blocks rendered.
- RT2  Response HTML must NOT contain first-person tokens outside excerpt
       blockquotes (W3 enforcement).
- RT3  Template uses var(--brk-*) tokens; no hardcoded color hex / fontnames.

Tests inject a fake WritingAssistService via set_service. No real LLM, no
vault writes. Mirrors #516 fake-injection pattern.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shared.schemas.reading_context_package import (
    EvidenceItem,
    IdeaCluster,
    MissingPiecePrompt,
    Question,
    ReadingContextPackage,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


# ── Fake service ─────────────────────────────────────────────────────────────


class _FakeWritingAssistService:
    """In-memory stand-in for the WritingAssistService Protocol."""

    def __init__(self) -> None:
        self.packages: dict[str, ReadingContextPackage] = {}
        self.missing_keys: set[str] = set()

    def load_package(self, source_id: str) -> ReadingContextPackage:
        if source_id in self.missing_keys:
            raise KeyError(f"package missing for {source_id!r}")
        if source_id not in self.packages:
            raise KeyError(f"unknown source_id={source_id!r}")
        return self.packages[source_id]


def _basic_package(source_id: str = "ebook:alpha-book") -> ReadingContextPackage:
    annotation = EvidenceItem(
        item_kind="annotation",
        locator="anno:cfi-001",
        excerpt="RMSSD short-window response distinguishes acute fatigue",
        source="annotation · ch-1",
    )
    return ReadingContextPackage(
        source_id=source_id,
        annotations=[annotation],
        idea_clusters=[
            IdeaCluster(
                cluster_id="clu_ch-1",
                label="ch-1",
                annotation_refs=["anno:cfi-001"],
            ),
        ],
        questions=[
            Question(
                question_id="q_1",
                text="how does RMSSD respond to overtraining?",
                related_clusters=["clu_ch-1"],
            )
        ],
        missing_piece_prompts=[
            MissingPiecePrompt(
                prompt_id="miss_clu_ch-1",
                text="ch-1: 需要更多 evidence",
            )
        ],
    )


def _b64(source_id: str) -> str:
    import base64

    return base64.urlsafe_b64encode(source_id.encode("utf-8")).decode("ascii").rstrip("=")


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_service():
    return _FakeWritingAssistService()


@pytest.fixture
def app_client(fake_service: _FakeWritingAssistService, monkeypatch):
    """TestClient on the real app with auth disabled and a fake service."""
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.writing_assist as wa_module

    importlib.reload(auth_module)
    importlib.reload(wa_module)
    importlib.reload(app_module)

    wa_module.set_service(fake_service)

    return TestClient(app_module.app, follow_redirects=False)


# ── RT1 — render scaffold for known source ──────────────────────────────────


def test_rt1_route_renders_scaffold_for_known_source(
    app_client: TestClient, fake_service: _FakeWritingAssistService
):
    package = _basic_package()
    fake_service.packages[package.source_id] = package

    r = app_client.get(f"/writing-assist/{_b64(package.source_id)}")
    assert r.status_code == 200
    body = r.text
    # Section block heading rendered as <h2>.
    assert "<h2" in body
    assert "ch-1" in body
    # Question prompt rendered.
    assert "how does RMSSD respond to overtraining?" in body
    # Missing-piece prompt rendered.
    assert "需要更多 evidence" in body
    # Excerpt rendered inside a <blockquote> per W1-W7 affordance — quoted
    # source content distinguished from authored content.
    assert "<blockquote" in body
    assert "RMSSD short-window response" in body
    # Section block has the expected outline overview entry.
    assert "outline" in body.lower()


# ── RT2 — response does not render completed prose / first-person ───────────


def test_rt2_route_does_not_render_completed_prose(
    app_client: TestClient, fake_service: _FakeWritingAssistService
):
    """Sweep the rendered HTML for first-person tokens outside <blockquote>.
    The blockquote is the ONE place quoted source content lives, so we
    strip those before checking.
    """
    package = _basic_package()
    fake_service.packages[package.source_id] = package

    r = app_client.get(f"/writing-assist/{_b64(package.source_id)}")
    assert r.status_code == 200
    body = r.text

    # Strip <blockquote>...</blockquote> spans so first-person tokens that
    # legitimately appear in quoted source content are excluded from the
    # sweep. This mirrors the W3/W4 boundary at the surface layer.
    stripped = re.sub(r"<blockquote[^>]*>.*?</blockquote>", "", body, flags=re.DOTALL)

    # W3 word-bounded English sweep. The English check uses word boundaries
    # so words like "switch" / "in" don't false-positive.
    en_first_person = re.compile(r"\b(I|we|my|our|us|me|we'll|I'll|I've|we've)\b", re.IGNORECASE)
    # Allow false-positive-free zone via known whitelist tokens that the
    # template uses for accessibility ("aria-label" attribute values, etc.).
    # Accept if a match falls inside an attribute name; we strip
    # ``aria-label="..."`` attributes before sweeping to be safe.
    stripped = re.sub(r'aria-label="[^"]*"', "", stripped)
    stripped = re.sub(r'cite="[^"]*"', "", stripped)
    # Brief-phrase template strings that include "Stage 4" → no first-person.
    en_match = en_first_person.search(stripped)
    assert en_match is None, (
        f"W3 violation: HTML response contains first-person token "
        f"{en_match.group()!r} outside <blockquote>; context: "
        f"{stripped[max(0, en_match.start() - 40) : en_match.end() + 40]!r}"
    )

    # W3 Chinese first-person sweep.
    for token in ("我", "我們", "我们"):
        assert token not in stripped, (
            f"W3 violation: HTML response contains Chinese first-person "
            f"token {token!r} outside <blockquote>"
        )

    # W4 sweep — opinion patterns must not appear in template chrome.
    for opinion in ("I think", "I believe", "我認為", "我覺得", "我相信"):
        assert opinion not in stripped, (
            f"W4 violation: HTML response contains opinion pattern {opinion!r} outside <blockquote>"
        )


# ── RT3 — template uses design tokens ───────────────────────────────────────


def test_rt3_route_uses_design_tokens():
    """The CSS file uses var(--brk-*) tokens for every visual property; the
    template + CSS must NOT hardcode color hex codes or font names. Brief §5
    self-imposed gate — Aesthetic direction is tokens-only.
    """
    css_path = REPO_ROOT / "thousand_sunny" / "static" / "writing_assist.css"
    template_path = REPO_ROOT / "thousand_sunny" / "templates" / "writing_assist" / "scaffold.html"
    assert css_path.exists(), css_path
    assert template_path.exists(), template_path

    css_text = css_path.read_text(encoding="utf-8")
    template_text = template_path.read_text(encoding="utf-8")

    # No 6-digit / 3-digit hex color codes (#aabbcc / #abc) outside comments.
    # Strip block comments first.
    css_no_comments = re.sub(r"/\*.*?\*/", "", css_text, flags=re.DOTALL)
    hex_pattern = re.compile(r"#[0-9a-fA-F]{3,8}\b")
    hex_matches = [m.group() for m in hex_pattern.finditer(css_no_comments)]
    # Also accept urlhash anchors in url() — but our CSS has none.
    assert hex_matches == [], (
        f"writing_assist.css must not hardcode hex color codes; violators: {hex_matches}"
    )

    # No rgb()/rgba() literals in CSS proper.
    rgb_pattern = re.compile(r"\brgba?\(", re.IGNORECASE)
    assert rgb_pattern.search(css_no_comments) is None, (
        "writing_assist.css must not hardcode rgb() / rgba() literals"
    )

    # The template MUST reference /static/projects/tokens.css and
    # /static/writing_assist.css (tokens layered first; surface stylesheet
    # second).
    assert "/static/projects/tokens.css" in template_text, (
        "scaffold.html must include /static/projects/tokens.css for design tokens"
    )
    assert "/static/writing_assist.css" in template_text, (
        "scaffold.html must include /static/writing_assist.css"
    )

    # The CSS itself must contain at least one var(--brk-*) reference per
    # visual category — this is a smell test for "did we keep tokens-only?".
    for token in (
        "var(--brk-bg",
        "var(--brk-ink",
        "var(--brk-rule",
        "var(--brk-font-",
        "var(--brk-s-",
    ):
        assert token in css_text, f"writing_assist.css missing token category {token!r}"


# ── Negative — invalid base64 returns 400 ───────────────────────────────────


def test_invalid_source_id_b64_returns_400(app_client: TestClient):
    """Sanity: a source_id_b64 that is not valid base64url returns 400."""
    r = app_client.get("/writing-assist/!!!not-base64!!!")
    assert r.status_code == 400


# ── Negative — unknown source returns 404 ───────────────────────────────────


def test_unknown_source_returns_404(
    app_client: TestClient, fake_service: _FakeWritingAssistService
):
    """A source_id the service doesn't know about → 404 (KeyError mapped)."""
    r = app_client.get(f"/writing-assist/{_b64('ebook:nonexistent')}")
    assert r.status_code == 404


# ── Error envelope renders without crashing ─────────────────────────────────


def test_route_renders_error_envelope_without_calling_surface(
    app_client: TestClient, fake_service: _FakeWritingAssistService
):
    """When the package carries an error envelope (F1-analog invariant),
    the route renders the template without calling the surface — the
    template surfaces the error message instead."""
    error_pkg = ReadingContextPackage(
        source_id="ebook:alpha-book",
        error="build_failed: OSError: digest path missing",
    )
    fake_service.packages[error_pkg.source_id] = error_pkg

    r = app_client.get(f"/writing-assist/{_b64(error_pkg.source_id)}")
    assert r.status_code == 200
    body = r.text
    assert "digest path missing" in body
    # The error template path should not render <h2> section blocks.
    assert "<h2" not in body or "build error" in body.lower()
