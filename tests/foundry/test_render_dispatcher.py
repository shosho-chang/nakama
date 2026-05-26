"""render_dispatcher unit tests — workers mocked."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from agents.foundry import render_dispatcher
from agents.foundry.render_workers import (
    hyperframes_worker,
)


def _beat(beat_id: int, render_target: str = "hyperframes") -> dict:
    return {
        "beat_id": beat_id,
        "broll": {
            "render_target": render_target,
            "component": "bigstat",
            "params": {},
            "transitions": {},
        },
    }


def test_dispatch_routes_hyperframes_to_hyperframes_worker(monkeypatch, tmp_path):
    fake = AsyncMock(return_value=tmp_path / "b_roll_1.mp4")
    monkeypatch.setattr(hyperframes_worker, "render", fake)

    result = asyncio.run(render_dispatcher.dispatch_beat(_beat(1), tmp_path))

    assert result == tmp_path / "b_roll_1.mp4"
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

    async def fake_render(beat, out_dir):
        call_order.append(beat["beat_id"])
        await asyncio.sleep(0)
        return out_dir / f"b_roll_{beat['beat_id']}.mp4"

    monkeypatch.setattr(hyperframes_worker, "render", fake_render)

    beats = [_beat(i) for i in [10, 20, 30]]
    results = asyncio.run(render_dispatcher.run_queue(beats, tmp_path, concurrency=1))

    assert call_order == [10, 20, 30]
    assert results == [tmp_path / f"b_roll_{i}.mp4" for i in [10, 20, 30]]


def test_run_queue_rejects_zero_concurrency(tmp_path):
    with pytest.raises(ValueError):
        asyncio.run(render_dispatcher.run_queue([], tmp_path, concurrency=0))


def test_run_queue_propagates_worker_error(monkeypatch, tmp_path):
    async def boom(beat, out_dir):
        raise hyperframes_worker.HyperframesRenderError(beat["beat_id"], "fake")

    monkeypatch.setattr(hyperframes_worker, "render", boom)
    with pytest.raises(hyperframes_worker.HyperframesRenderError):
        asyncio.run(render_dispatcher.run_queue([_beat(99)], tmp_path))
