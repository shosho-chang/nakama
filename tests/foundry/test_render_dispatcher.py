"""render_dispatcher unit tests — workers mocked.

ADR-038 §D2: dispatch_beat now computes a content-addressed hash and returns
``(mp4_path, hash, was_cache_hit)``; tests stub ``compute_beat_hash`` to a
fixed digest to keep them isolated from layout YAML / composition HTML
content on disk.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from agents.foundry import render_dispatcher
from agents.foundry.render_workers import (
    hyperframes_worker,
)

_FAKE_HASH = "deadbeefdeadbeef"


def _beat(beat_id: int, render_target: str = "hyperframes") -> dict:
    return {
        "beat_id": beat_id,
        "layout": "full_broll",
        "broll_decision": "cutaway",
        "broll": {
            "render_target": render_target,
            "component": "bigstat",
            "params": {},
            "transitions": {},
        },
    }


@pytest.fixture(autouse=True)
def _stub_hash(monkeypatch):
    """Force compute_beat_hash → fixed digest so tests don't read repo files."""
    monkeypatch.setattr(render_dispatcher, "compute_beat_hash", lambda beat, ctx=None: _FAKE_HASH)


def test_dispatch_routes_hyperframes_to_hyperframes_worker(monkeypatch, tmp_path):
    fake = AsyncMock(return_value=tmp_path / f"b_roll_{_FAKE_HASH}.mp4")
    monkeypatch.setattr(hyperframes_worker, "render", fake)

    mp4, cached_hash, was_hit = asyncio.run(render_dispatcher.dispatch_beat(_beat(1), tmp_path))

    assert mp4 == tmp_path / f"b_roll_{_FAKE_HASH}.mp4"
    assert cached_hash == _FAKE_HASH
    assert was_hit is False
    fake.assert_awaited_once()
    # Worker invoked with cached_hash kwarg (contract enforced by worker)
    _, kwargs = fake.await_args
    assert kwargs["cached_hash"] == _FAKE_HASH


def test_dispatch_cache_hit_skips_worker(monkeypatch, tmp_path):
    """Existing content-addressed mp4 → return path without invoking worker."""
    expected = tmp_path / f"b_roll_{_FAKE_HASH}.mp4"
    expected.write_bytes(b"existing")

    fake = AsyncMock()
    monkeypatch.setattr(hyperframes_worker, "render", fake)

    mp4, cached_hash, was_hit = asyncio.run(render_dispatcher.dispatch_beat(_beat(1), tmp_path))

    assert mp4 == expected
    assert cached_hash == _FAKE_HASH
    assert was_hit is True
    fake.assert_not_awaited()


def test_dispatch_no_cache_forces_rerender(monkeypatch, tmp_path):
    """use_cache=False bypasses the cache-hit short-circuit."""
    expected = tmp_path / f"b_roll_{_FAKE_HASH}.mp4"
    expected.write_bytes(b"stale")

    fake = AsyncMock(return_value=expected)
    monkeypatch.setattr(hyperframes_worker, "render", fake)

    _mp4, _h, was_hit = asyncio.run(
        render_dispatcher.dispatch_beat(_beat(1), tmp_path, use_cache=False)
    )

    assert was_hit is False
    fake.assert_awaited_once()


def test_dispatch_raises_for_reader_playwright_phase_15(tmp_path):
    with pytest.raises(NotImplementedError, match="Phase 1.5"):
        asyncio.run(render_dispatcher.dispatch_beat(_beat(2, "reader-playwright"), tmp_path))


def test_dispatch_raises_for_web_playwright_phase_15(tmp_path):
    with pytest.raises(NotImplementedError, match="Phase 1.5"):
        asyncio.run(render_dispatcher.dispatch_beat(_beat(3, "web-playwright"), tmp_path))


def test_dispatch_raises_for_unknown_target(tmp_path):
    with pytest.raises(ValueError, match="unknown render_target"):
        asyncio.run(render_dispatcher.dispatch_beat(_beat(4, "bogus"), tmp_path))


def test_dispatch_raises_when_no_broll_spec(tmp_path):
    with pytest.raises(ValueError, match="no broll spec"):
        asyncio.run(render_dispatcher.dispatch_beat({"beat_id": 5}, tmp_path))


def test_run_queue_preserves_order_with_concurrency_1(monkeypatch, tmp_path):
    """Sequential render: concurrency=1 means strict serialization."""
    call_order: list[int] = []

    async def fake_render(beat, out_dir, cached_hash=None):
        call_order.append(beat["beat_id"])
        await asyncio.sleep(0)
        return out_dir / f"b_roll_{cached_hash}.mp4"

    monkeypatch.setattr(hyperframes_worker, "render", fake_render)

    beats = [_beat(i) for i in [10, 20, 30]]
    results = asyncio.run(render_dispatcher.run_queue(beats, tmp_path, concurrency=1))

    assert call_order == [10, 20, 30]
    assert results == [
        (tmp_path / f"b_roll_{_FAKE_HASH}.mp4", _FAKE_HASH, False) for _ in [10, 20, 30]
    ]


def test_run_queue_rejects_zero_concurrency(tmp_path):
    with pytest.raises(ValueError):
        asyncio.run(render_dispatcher.run_queue([], tmp_path, concurrency=0))


def test_run_queue_propagates_worker_error(monkeypatch, tmp_path):
    async def boom(beat, out_dir, cached_hash=None):
        raise hyperframes_worker.HyperframesRenderError(beat["beat_id"], "fake")

    monkeypatch.setattr(hyperframes_worker, "render", boom)
    with pytest.raises(hyperframes_worker.HyperframesRenderError):
        asyncio.run(render_dispatcher.run_queue([_beat(99)], tmp_path))
