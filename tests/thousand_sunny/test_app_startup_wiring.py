"""Tests for ``thousand_sunny.app`` lifespan-based promotion wiring
(ADR-024 Slice 10 / N518a + N518b).

Brief §5 wiring tests:

- WT1  Lifespan wires ``promotion_review`` service.
- WT2  Lifespan wires ``writing_assist`` service.
- WT3  ``GET /promotion-review/`` returns 200 (not 503) after wiring.
- WT4  ``GET /writing-assist/{id_b64}`` for missing package → 404 (not 503).
- WT5  ``DISABLE_ROBIN=1`` skips wiring.
- WT6  Missing ``NAKAMA_VAULT_ROOT`` → startup raises.
- WT7  ``NAKAMA_PROMOTION_MODE=llm`` → startup raises ``RuntimeError``
        mentioning N519.
- WT8  Adapter modules expose no top-level instances.
- WT9  Adapter modules don't import ``fastapi`` / ``thousand_sunny.*``.
- WT10 No module under ``shared.*`` (within N518's surface) imports
        ``anthropic``.

After N518b the dry-run extractor / matcher stubs were replaced with
deterministic bodies (see ``tests/shared/test_dry_run_extractor.py`` and
``test_dry_run_matcher.py``). The test
``test_post_start_surfaces_dry_run_response`` below confirms that
``POST /promotion-review/.../start`` no longer 500s with
``NotImplementedError`` — it now returns the route's normal 4xx for an
unresolvable source_id, proving the wiring + dry-run path works
end-to-end.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# ── Helpers ──────────────────────────────────────────────────────────────────


def _b64(source_id: str) -> str:
    import base64

    return base64.urlsafe_b64encode(source_id.encode("utf-8")).decode("ascii").rstrip("=")


def _make_minimal_vault(root: Path) -> Path:
    """Create a minimal vault tree the app expects to scan at startup.

    Returns the vault root (for use as ``NAKAMA_VAULT_ROOT``).
    """
    (root / "Inbox" / "kb").mkdir(parents=True)
    (root / "data" / "books").mkdir(parents=True)
    (root / "KB" / "Wiki" / "Concepts").mkdir(parents=True)
    return root


def _disable_auth(monkeypatch) -> None:
    """Drop auth guards so route handlers run without login redirects."""
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)


def _reload_app_modules() -> object:
    """Reload the Thousand Sunny app + relevant routers so test env vars
    take effect on the lifespan startup. Returns the reloaded app module."""
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.promotion_review as pr_module
    import thousand_sunny.routers.writing_assist as wa_module

    importlib.reload(auth_module)
    importlib.reload(pr_module)
    importlib.reload(wa_module)

    import thousand_sunny.app as app_module

    importlib.reload(app_module)
    return app_module


@pytest.fixture
def vault_with_robin(tmp_path: Path, monkeypatch) -> Path:
    """Configure env so the app boots with Robin enabled + dry_run mode."""
    vault = _make_minimal_vault(tmp_path / "vault")
    # Reroute book storage to the same dir the lister will enumerate.
    monkeypatch.setenv("NAKAMA_BOOKS_DIR", str(vault / "data" / "books"))
    monkeypatch.setenv("NAKAMA_VAULT_ROOT", str(vault))
    # Ensure NO DISABLE_ROBIN — we want the lifespan to fire.
    monkeypatch.delenv("DISABLE_ROBIN", raising=False)
    monkeypatch.setenv("NAKAMA_PROMOTION_MODE", "dry_run")
    monkeypatch.setenv("NAKAMA_PROMOTION_MANIFEST_ROOT", str(vault / ".promotion-manifests"))
    monkeypatch.setenv(
        "NAKAMA_READING_CONTEXT_PACKAGE_ROOT",
        str(vault / ".reading-context-packages"),
    )
    _disable_auth(monkeypatch)
    return vault


# ── WT1 — promotion review service wired ────────────────────────────────────


def test_wt1_app_lifespan_wires_promotion_review_service(vault_with_robin: Path):
    app_module = _reload_app_modules()
    import thousand_sunny.routers.promotion_review as pr_module

    # The lifespan only fires when the app starts; TestClient triggers it
    # via the ASGI lifespan protocol.
    with TestClient(app_module.app) as client:
        # Touching any route forces lifespan startup.
        _ = client.get("/healthz")
        assert pr_module._service is not None


# ── WT2 — writing assist service wired ──────────────────────────────────────


def test_wt2_app_lifespan_wires_writing_assist_service(vault_with_robin: Path):
    app_module = _reload_app_modules()
    import thousand_sunny.routers.writing_assist as wa_module

    with TestClient(app_module.app) as client:
        _ = client.get("/healthz")
        assert wa_module._service is not None


# ── WT3 — GET /promotion-review/ returns 200 ────────────────────────────────


def test_wt3_app_get_promotion_review_returns_200_dry_run(
    vault_with_robin: Path,
):
    app_module = _reload_app_modules()
    with TestClient(app_module.app, follow_redirects=False) as client:
        r = client.get("/promotion-review/")
    # Empty list view is acceptable; what matters is NOT 503.
    assert r.status_code == 200, r.text
    assert "503" not in r.text


# ── WT4 — GET /writing-assist/{id_b64} missing package → 404 (not 503) ──────


def test_wt4_app_get_writing_assist_missing_package_returns_404_not_503(
    vault_with_robin: Path,
):
    app_module = _reload_app_modules()
    with TestClient(app_module.app, follow_redirects=False) as client:
        # No package persisted at this id; default disk-backed service
        # raises ``KeyError`` which the route maps to 404 via
        # ``_HTTP_BOUNDARY_FAILURES``.
        r = client.get(f"/writing-assist/{_b64('ebook:does-not-exist')}")
    assert r.status_code == 404, r.text


# ── WT5 — DISABLE_ROBIN=1 skips wiring ──────────────────────────────────────


def test_wt5_app_disable_robin_skips_wiring(tmp_path: Path, monkeypatch):
    vault = _make_minimal_vault(tmp_path / "vault")
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    # NAKAMA_VAULT_ROOT not required when DISABLE_ROBIN is set; we set
    # it anyway so any leak doesn't blow up.
    monkeypatch.setenv("NAKAMA_VAULT_ROOT", str(vault))
    _disable_auth(monkeypatch)

    app_module = _reload_app_modules()
    import thousand_sunny.routers.promotion_review as pr_module
    import thousand_sunny.routers.writing_assist as wa_module

    # Reset any service set by a previous test — module reload restores
    # _service = None but be explicit.
    pr_module._service = None
    wa_module._service = None

    with TestClient(app_module.app, follow_redirects=False) as client:
        # / → redirect path defined by the DISABLE_ROBIN branch.
        r = client.get("/")
        assert r.status_code in {302, 307, 308}, r.text

        # Services NOT wired.
        assert pr_module._service is None
        assert wa_module._service is None


# ── WT6 — bad config raises ─────────────────────────────────────────────────


def test_wt6_app_bad_config_raises_when_vault_root_missing(tmp_path: Path, monkeypatch):
    """Missing ``NAKAMA_VAULT_ROOT`` (with Robin enabled) must crash the
    lifespan — silent fallback is forbidden by W4."""
    monkeypatch.delenv("DISABLE_ROBIN", raising=False)
    monkeypatch.delenv("NAKAMA_VAULT_ROOT", raising=False)
    monkeypatch.setenv("NAKAMA_PROMOTION_MODE", "dry_run")
    _disable_auth(monkeypatch)

    app_module = _reload_app_modules()

    with pytest.raises(RuntimeError, match="NAKAMA_VAULT_ROOT"):
        with TestClient(app_module.app):
            pass


# ── WT7 — NAKAMA_PROMOTION_MODE=llm raises in N518 ─────────────────────────


def test_wt7_app_llm_mode_raises_in_n518a(tmp_path: Path, monkeypatch):
    """``NAKAMA_PROMOTION_MODE=llm`` is not yet wired — must raise with a
    clear message pointing at N519."""
    vault = _make_minimal_vault(tmp_path / "vault")
    monkeypatch.setenv("NAKAMA_BOOKS_DIR", str(vault / "data" / "books"))
    monkeypatch.setenv("NAKAMA_VAULT_ROOT", str(vault))
    monkeypatch.delenv("DISABLE_ROBIN", raising=False)
    monkeypatch.setenv("NAKAMA_PROMOTION_MODE", "llm")
    _disable_auth(monkeypatch)

    app_module = _reload_app_modules()

    with pytest.raises(RuntimeError, match="N519"):
        with TestClient(app_module.app):
            pass


def test_app_unknown_promotion_mode_raises(tmp_path: Path, monkeypatch):
    """Unknown mode also raises, with a clear message naming the field."""
    vault = _make_minimal_vault(tmp_path / "vault")
    monkeypatch.setenv("NAKAMA_BOOKS_DIR", str(vault / "data" / "books"))
    monkeypatch.setenv("NAKAMA_VAULT_ROOT", str(vault))
    monkeypatch.delenv("DISABLE_ROBIN", raising=False)
    monkeypatch.setenv("NAKAMA_PROMOTION_MODE", "rumpelstiltskin")
    _disable_auth(monkeypatch)

    app_module = _reload_app_modules()

    with pytest.raises(RuntimeError, match="NAKAMA_PROMOTION_MODE"):
        with TestClient(app_module.app):
            pass


# ── WT8 — adapters expose no module-level instances ─────────────────────────


@pytest.mark.parametrize(
    "module_name",
    [
        "shared.blob_loader",
        "shared.source_resolver",
        "shared.reading_source_lister",
        "shared.kb_concept_index_default",
        "shared.dry_run_extractor",
        "shared.dry_run_matcher",
    ],
)
def test_wt8_no_module_singleton_in_adapters(module_name: str):
    """Subprocess: import each adapter module, assert no top-level
    instances of the adapter class (W6 / boundary 4)."""
    code = (
        f"import sys, importlib;"
        f"mod = importlib.import_module({module_name!r});"
        f"top = [name for name, val in vars(mod).items() "
        f"       if not name.startswith('_') "
        f"       and isinstance(val, object) and type(val).__module__ == mod.__name__];"
        f"assert not top, f'top-level instances: {{top}}';"
        f"print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)


# ── WT9 — adapters don't import fastapi / thousand_sunny ────────────────────


@pytest.mark.parametrize(
    "module_name",
    [
        "shared.blob_loader",
        "shared.source_resolver",
        "shared.reading_source_lister",
        "shared.kb_concept_index_default",
        "shared.dry_run_extractor",
        "shared.dry_run_matcher",
    ],
)
def test_wt9_no_fastapi_or_thousand_sunny_import_in_adapters(module_name: str):
    """Subprocess: import the module fresh, ensure neither ``fastapi`` nor
    any ``thousand_sunny.*`` module is in ``sys.modules`` afterwards."""
    code = (
        f"import sys;"
        # Pre-load the stdlib so its imports don't pollute the check.
        f"_preload = (sys, );"
        f"import importlib;"
        f"importlib.import_module({module_name!r});"
        f"forbidden_prefixes = ('fastapi', 'thousand_sunny', 'agents');"
        f"loaded = [m for m in sys.modules "
        f"          if any(m == p or m.startswith(p + '.') for p in forbidden_prefixes)];"
        f"assert not loaded, f'forbidden modules loaded: {{loaded}}';"
        f"print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)


# ── WT10 — no anthropic import in N518a surface ─────────────────────────────


@pytest.mark.parametrize(
    "module_name",
    [
        "shared.blob_loader",
        "shared.source_resolver",
        "shared.reading_source_lister",
        "shared.kb_concept_index_default",
        "shared.dry_run_extractor",
        "shared.dry_run_matcher",
    ],
)
def test_wt10_no_anthropic_import_in_n518a_modules(module_name: str):
    """Subprocess: each N518a module must NOT import ``anthropic``."""
    code = (
        "import sys, importlib;"
        f"importlib.import_module({module_name!r});"
        "assert 'anthropic' not in sys.modules, "
        "'anthropic was loaded transitively'; "
        "print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)


# ── N518b — dry-run mode is wired end-to-end ─────────────────────────────────


def test_post_start_surfaces_dry_run_response(vault_with_robin: Path):
    """``POST /promotion-review/source/{id_b64}/start`` no longer raises
    ``NotImplementedError`` after N518b — the dry-run extractor + matcher
    are now real deterministic bodies.

    Because no real book / inbox doc exists in the fixture vault, the
    registry returns ``None`` and the service raises ``ValueError`` from
    its source-not-found path → 400. What this test asserts is the
    *absence* of a 500 with N518b stub messaging, AND the absence of 503
    (which would mean the service wasn't wired).
    """
    app_module = _reload_app_modules()
    with TestClient(app_module.app, follow_redirects=False) as client:
        r = client.post(f"/promotion-review/source/{_b64('ebook:nope')}/start")

    # Must NOT be 503 (service wired).
    assert r.status_code != 503, r.text
    # Must NOT carry the deferred-slice stub message (those got replaced
    # in N518b — if a regression brings the stub back, this catches it).
    assert "deferred to N518b" not in r.text
    # The expected outcome for an unresolvable id is 400 from the
    # source_not_resolved path. 404 is also acceptable depending on how
    # future routing surfaces it; either way the route now reaches the
    # service successfully.
    assert r.status_code in {400, 404}, r.text


def test_dry_run_extractor_returns_real_claims_in_n518b():
    """Sanity check: the extractor is a real body now, not a stub.
    Replaces the N518a ``test_dry_run_extractor_stub_raises_with_n518b_message``
    test which asserted the stub raised."""
    from shared.dry_run_extractor import DryRunClaimExtractor

    ex = DryRunClaimExtractor()
    result = ex.extract(chapter_text="some prose", chapter_title="Chapter 1", primary_lang="en")
    assert 1 <= len(result.claims) <= 3
    assert all(c.startswith("[DRY-RUN] ") for c in result.claims)


def test_dry_run_matcher_returns_no_match_in_n518b():
    """Sanity check: the matcher is a real body now, not a stub.
    Replaces the N518a ``test_dry_run_matcher_stub_raises_with_n518b_message``
    test which asserted the stub raised."""
    from shared.dry_run_matcher import DryRunConceptMatcher
    from shared.schemas.concept_promotion import ConceptCandidate

    m = DryRunConceptMatcher()
    candidate = ConceptCandidate(
        candidate_id="cand_001",
        label="x",
        evidence_language="en",
        chapter_refs=["ch-1"],
        raw_quotes=["q"],
    )
    outcome = m.match(candidate, kb_index=None, primary_lang="en")
    assert outcome.canonical_match.match_basis == "none"
    assert outcome.canonical_match.confidence <= 0.1
