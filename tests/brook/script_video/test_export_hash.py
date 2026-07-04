"""export_hash tests (ADR-038 §D2).

Acceptance per #778:
(a) same beat → same hash (stability)
(b) changing broll.params → new hash
(c) editing referenced layout YAML content → new hash
(d) bumping EXPORT_VERSION → new hash
(e) already-rendered mp4 → dispatcher returns cache hit, worker not invoked
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agents.brook.script_video import export_hash as export_hash_mod
from agents.brook.script_video import render_dispatcher
from agents.brook.script_video.export_hash import HashContext, compute_beat_hash
from agents.brook.script_video.render_workers import hyperframes_worker


def _beat(**overrides) -> dict:
    base = {
        "beat_id": 7,
        "broll_decision": "cutaway",
        "layout": "full_broll",
        "broll": {
            "render_target": "hyperframes",
            "component": "bigstat",
            "params": {"target": 11000, "label": "受試者"},
            "transitions": {},
        },
    }
    base.update(overrides)
    return base


def _fixture_ctx(tmp_path: Path) -> tuple[HashContext, Path, Path, Path]:
    layouts = tmp_path / "layouts"
    comps = tmp_path / "compositions"
    guard = tmp_path / "guardrails.yaml"
    layouts.mkdir(parents=True)
    (comps / "bigstat").mkdir(parents=True)
    layout_path = layouts / "full_broll.yaml"
    layout_path.write_text("name: full_broll\nslots: []\n", encoding="utf-8")
    comp_path = comps / "bigstat" / "index.html"
    comp_path.write_text("<html><body>v1</body></html>", encoding="utf-8")
    guard.write_text("hard_limits: {}\n", encoding="utf-8")
    ctx = HashContext(layouts_dir=layouts, compositions_dir=comps, guardrails_path=guard)
    return ctx, layout_path, comp_path, guard


# ---------------------------------------------------------------------------
# (a) stability
# ---------------------------------------------------------------------------
def test_same_beat_same_hash(tmp_path):
    ctx, *_ = _fixture_ctx(tmp_path)
    h1 = compute_beat_hash(_beat(), ctx)
    h2 = compute_beat_hash(_beat(), ctx)
    assert h1 == h2
    assert len(h1) == 16


def test_hash_order_independent(tmp_path):
    """dict key ordering must not affect the hash."""
    ctx, *_ = _fixture_ctx(tmp_path)
    a = _beat()
    b = {
        "broll": a["broll"],
        "layout": a["layout"],
        "broll_decision": a["broll_decision"],
        "beat_id": a["beat_id"],
    }
    assert compute_beat_hash(a, ctx) == compute_beat_hash(b, ctx)


# ---------------------------------------------------------------------------
# (b) params change → new hash
# ---------------------------------------------------------------------------
def test_changing_broll_params_changes_hash(tmp_path):
    ctx, *_ = _fixture_ctx(tmp_path)
    h_a = compute_beat_hash(_beat(), ctx)
    edited = _beat()
    edited["broll"]["params"] = {"target": 99999, "label": "受試者"}
    h_b = compute_beat_hash(edited, ctx)
    assert h_a != h_b


def test_changing_component_changes_hash(tmp_path):
    ctx, comps_path, _comp, _guard = (None,) * 4  # placeholder for readability
    ctx, _layout_path, comp_path, _guard = _fixture_ctx(tmp_path)
    # Add a 2nd composition so the swap is valid
    (comp_path.parent.parent / "bigstat2").mkdir()
    (comp_path.parent.parent / "bigstat2" / "index.html").write_text(
        "<html>other</html>", encoding="utf-8"
    )
    h_a = compute_beat_hash(_beat(), ctx)
    edited = _beat()
    edited["broll"]["component"] = "bigstat2"
    h_b = compute_beat_hash(edited, ctx)
    assert h_a != h_b


# ---------------------------------------------------------------------------
# (c) layout YAML content change → new hash
# ---------------------------------------------------------------------------
def test_editing_layout_yaml_content_changes_hash(tmp_path):
    """The critical panel-review correctness check (P3): silently editing
    layouts/full_broll.yaml must invalidate the cache.
    """
    ctx, layout_path, _comp, _guard = _fixture_ctx(tmp_path)
    h_before = compute_beat_hash(_beat(), ctx)
    layout_path.write_text("name: full_broll\nslots: []\n# edited font size\n", encoding="utf-8")
    h_after = compute_beat_hash(_beat(), ctx)
    assert h_before != h_after


def test_editing_composition_html_changes_hash(tmp_path):
    """Same correctness check for composition HTML content."""
    ctx, _layout, comp_path, _guard = _fixture_ctx(tmp_path)
    h_before = compute_beat_hash(_beat(), ctx)
    comp_path.write_text("<html><body>v2 — edited</body></html>", encoding="utf-8")
    h_after = compute_beat_hash(_beat(), ctx)
    assert h_before != h_after


def test_editing_guardrails_changes_hash(tmp_path):
    ctx, _layout, _comp, guard = _fixture_ctx(tmp_path)
    h_before = compute_beat_hash(_beat(), ctx)
    guard.write_text("hard_limits:\n  max_cutaways_per_minute: 5\n", encoding="utf-8")
    h_after = compute_beat_hash(_beat(), ctx)
    assert h_before != h_after


# ---------------------------------------------------------------------------
# (d) EXPORT_VERSION bump → new hash
# ---------------------------------------------------------------------------
def test_bumping_export_version_changes_hash(tmp_path, monkeypatch):
    ctx, *_ = _fixture_ctx(tmp_path)
    h_v1 = compute_beat_hash(_beat(), ctx)
    monkeypatch.setattr(export_hash_mod, "EXPORT_VERSION", 2)
    h_v2 = compute_beat_hash(_beat(), ctx)
    assert h_v1 != h_v2


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------
def test_missing_layout_raises(tmp_path):
    ctx, layout_path, *_ = _fixture_ctx(tmp_path)
    layout_path.unlink()
    with pytest.raises(FileNotFoundError, match="layout file not found"):
        compute_beat_hash(_beat(), ctx)


def test_missing_composition_raises(tmp_path):
    ctx, _layout, comp_path, _guard = _fixture_ctx(tmp_path)
    comp_path.unlink()
    with pytest.raises(FileNotFoundError, match="composition html not found"):
        compute_beat_hash(_beat(), ctx)


def test_missing_guardrails_tolerated(tmp_path):
    """Guardrails absent is non-fatal (early-fixture rationale in module doc)."""
    ctx, _layout, _comp, guard = _fixture_ctx(tmp_path)
    guard.unlink()
    # Should not raise
    h = compute_beat_hash(_beat(), ctx)
    assert len(h) == 16


def test_missing_layout_field_raises(tmp_path):
    ctx, *_ = _fixture_ctx(tmp_path)
    beat = _beat()
    del beat["layout"]
    with pytest.raises(ValueError, match="missing 'layout'"):
        compute_beat_hash(beat, ctx)


# ---------------------------------------------------------------------------
# (e) dispatcher integration: existing mp4 → cache hit, no worker call
# ---------------------------------------------------------------------------
def test_dispatcher_cache_hit_when_mp4_exists(tmp_path, monkeypatch):
    """End-to-end: dispatch_beat returns existing mp4 without invoking worker."""
    ctx, *_ = _fixture_ctx(tmp_path / "ctx")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    # Pre-compute hash + write a fake "rendered" mp4 at that path
    beat = _beat()
    cached = compute_beat_hash(beat, ctx)
    (out_dir / f"b_roll_{cached}.mp4").write_bytes(b"already rendered")

    async def explode(*_a, **_kw):
        raise AssertionError("worker should not be called on cache hit")

    monkeypatch.setattr(hyperframes_worker, "render", explode)

    mp4, h, was_hit = asyncio.run(render_dispatcher.dispatch_beat(beat, out_dir, ctx=ctx))

    assert was_hit is True
    assert h == cached
    assert mp4 == out_dir / f"b_roll_{cached}.mp4"


def test_dispatcher_cache_miss_when_layout_edited(tmp_path, monkeypatch):
    """Edit layout YAML between renders → hash changes → no cache hit."""
    ctx, layout_path, *_ = _fixture_ctx(tmp_path / "ctx")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    beat = _beat()

    first_hash = compute_beat_hash(beat, ctx)
    (out_dir / f"b_roll_{first_hash}.mp4").write_bytes(b"v1")

    # Operator edits layout
    layout_path.write_text("name: full_broll\nedited: true\n", encoding="utf-8")
    second_hash = compute_beat_hash(beat, ctx)
    assert first_hash != second_hash

    called = {"n": 0}

    async def fake_render(beat, out_dir, cached_hash=None):
        called["n"] += 1
        path = out_dir / f"b_roll_{cached_hash}.mp4"
        path.write_bytes(b"v2")
        return path

    monkeypatch.setattr(hyperframes_worker, "render", fake_render)

    _mp4, h, was_hit = asyncio.run(render_dispatcher.dispatch_beat(beat, out_dir, ctx=ctx))

    assert was_hit is False
    assert h == second_hash
    assert called["n"] == 1
