"""Playwright smoke tests for the Promotion Review surface (N518b PT1-PT4).

Brief §5 PT1-PT4:

- PT1 Open ``/promotion-review/`` → list view renders, no console errors.
- PT2 Click into a fixture source → claim review interface renders.
- PT3 Start review → dry-run claims surface with ``[DRY-RUN]`` badge / prefix.
- PT4 Navigate to ``/writing-assist/{id_b64}`` → renders without 503.

Status: **DEFERRED to follow-up**. The Nakama test suite does not currently
have a Playwright harness wired (no ``playwright`` in ``requirements.txt``,
no ``conftest.py`` in this directory bringing up uvicorn against a fixture
vault). Building that harness from scratch is its own slice — bigger than
N518b's scope.

What this file provides instead:

1. A ``pytest.skip(...)`` at module load so the file doesn't pollute CI
   with import errors but is discoverable on next harness work.
2. Documented PT1-PT4 stubs so the next implementer (likely N518c or a
   dedicated playwright-harness slice) can fill in the bodies without
   re-deriving what each test asserts.
3. RT1-RT3 + the dry-run unit tests + ``test_app_startup_wiring.py`` cover
   the same behaviours at the route + service layer with FastAPI's
   ``TestClient`` — Playwright would add browser-console / DOM-render
   verification on top.

Issue #540 PR body surfaces this as an open follow-up.
"""

from __future__ import annotations

import pytest

pytest.skip(
    "Playwright harness not wired in this repo (no playwright dep, no "
    "uvicorn fixture). N518b RT1-RT3 + N518a WT1-WT10 cover the same "
    "behaviours at the route + service layer with TestClient. Browser-"
    "console + DOM-render assertions deferred to a dedicated playwright-"
    "harness slice.",
    allow_module_level=True,
)


# ── PT1 — list page renders, no console errors ─────────────────────────────


def test_pt1_list_page_renders_with_no_console_errors():
    """PT1: open ``/promotion-review/`` → list view renders;
    ``page.console`` collected zero error-level messages.

    Implementation outline (when harness exists):

    1. ``await page.goto(f"{base_url}/promotion-review/")``
    2. ``await page.wait_for_selector('main')``
    3. Assert no console messages with type='error'.
    """
    raise NotImplementedError("see module docstring — Playwright harness deferred")


# ── PT2 — click into candidate, claim review interface renders ──────────────


def test_pt2_click_candidate_renders_claim_review():
    """PT2: click a candidate row in the list → land on
    ``/promotion-review/source/{id_b64}`` → review surface renders.

    Implementation outline:

    1. Pre-seed a fixture book (mirror RT1's ``_store_test_book``).
    2. ``await page.goto(...)`` → ``await page.click('a[data-source-id]')``
    3. Assert URL pattern ``/promotion-review/source/{id_b64}``.
    4. Assert review surface chrome present (review.html template anchor).
    """
    raise NotImplementedError("see module docstring — Playwright harness deferred")


# ── PT3 — start review surfaces [DRY-RUN] claims ───────────────────────────


def test_pt3_start_review_surfaces_dry_run_claims():
    """PT3: ``POST .../start`` → review surface shows items with
    ``[DRY-RUN]`` markers visible in the rendered HTML.

    Implementation outline:

    1. Pre-seed fixture book.
    2. Navigate to per-source review surface.
    3. Click "Start review" button.
    4. Wait for review items to render.
    5. Assert at least one item card contains ``[DRY-RUN]`` text.

    The route-level test ``test_rt2_start_creates_manifest_with_dry_run_claims``
    already asserts the marker is in the persisted manifest; PT3 extends to
    the rendered template so the UI surfacing is verified end-to-end.
    """
    raise NotImplementedError("see module docstring — Playwright harness deferred")


# ── PT4 — writing assist surface renders without 503 ───────────────────────


def test_pt4_writing_assist_surface_renders_without_503():
    """PT4: navigate to ``/writing-assist/{id_b64}`` → renders without 503.

    Implementation outline:

    1. Pre-seed a ReadingContextPackage fixture (or accept the controlled
       404 for a missing-package id — both prove the wiring).
    2. ``await page.goto(...)``
    3. Assert ``response.status`` ∈ ``{200, 404}`` (NOT 503).
    4. Assert no console errors.

    The route-level test
    ``test_wt4_app_get_writing_assist_missing_package_returns_404_not_503``
    already asserts the 404 response code at the HTTP layer; PT4 extends
    to the rendered surface for full UX coverage.
    """
    raise NotImplementedError("see module docstring — Playwright harness deferred")
