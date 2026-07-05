"""3-path render dispatcher (ADR-032 §1, ADR-038 §D2 cache layer).

Phase 1 only `hyperframes` is implemented. The other two workers raise
NotImplementedError until web_highlight_record.py promotion + Robin URL
scheme land in Phase 1.5.

ADR-038 §D2 added content-addressed output filenames: each beat's hash is
computed from `EXPORT_VERSION` + minimal beat fields + layout YAML digest +
composition HTML digest + guardrails digest. The dispatcher computes the
hash, performs an early cache-skip when `out/b_roll_<hash>.mp4` already
exists, and passes the hash down to the worker so the filename matches.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

from agents.brook.script_video.export_hash import HashContext, compute_beat_hash
from agents.brook.script_video.render_workers import (
    hyperframes_worker,
    reader_playwright_worker,
    web_playwright_worker,
)

logger = logging.getLogger(__name__)


def _verify_asset_beat(beat: dict, broll: dict, out_dir: Path) -> Path:
    """驗收 asset 類 beat 的素材檔（存在 + 可選 sha256 digest 比對）.

    ``asset.path`` 為 episode 目錄相對路徑（out_dir 的上層即 episode 目錄）。
    檔案缺席 = 素材尚未下載交接／外供 — fail loud 讓 operator 先跑
    assets 流程再 render。
    """
    beat_id = beat.get("beat_id")
    asset = broll.get("asset") or {}
    rel = asset.get("path")
    if not rel:
        raise ValueError(
            f"beat {beat_id}: asset beat 缺 broll.asset.path — 素材尚未取得"
            f"（先完成 asset 下載交接／外供，再把落地路徑寫回 storyboard）"
        )
    path = Path(rel)
    if not path.is_absolute():
        path = out_dir.parent / rel
    if not path.exists():
        raise ValueError(f"beat {beat_id}: asset 檔案不存在 {path} — 素材尚未落地或路徑錯誤")
    expected = asset.get("sha256")
    if expected:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(
                f"beat {beat_id}: asset 檔案 digest 不符（{path}）— 檔案在驗收後被替換過，"
                f"重新審核或更新 storyboard 的 asset.sha256"
            )
    logger.info("beat %s asset verified at %s", beat_id, path)
    return path


def beat_output_path(beat: dict, out_dir: Path, ctx: HashContext | None = None) -> tuple[Path, str]:
    """Resolve (output mp4 path, hash) for a beat without rendering.

    Useful for cache-check tooling and for fcpxml_emitter lookups when the
    storyboard's ``status.cached_hash`` was not persisted (e.g. legacy
    fixtures or external callers). Returns the same hash that
    ``dispatch_beat`` would use.
    """
    cached_hash = compute_beat_hash(beat, ctx)
    return out_dir / f"b_roll_{cached_hash}.mp4", cached_hash


async def dispatch_beat(
    beat: dict,
    out_dir: Path,
    *,
    use_cache: bool = True,
    ctx: HashContext | None = None,
) -> tuple[Path, str, bool]:
    """Route a single beat to the worker named by ``beat.broll.render_target``.

    Returns ``(rendered_mp4_path, cached_hash, was_cache_hit)``.

    When ``use_cache=True`` (default) and the content-addressed mp4 already
    exists on disk, returns the existing path without invoking the worker.
    Pass ``use_cache=False`` to force re-render (mirrors
    ``pipeline.py render --no-cache``).

    Raises ``NotImplementedError`` for Phase 1.5 targets and ``ValueError``
    for unknown targets or missing broll specs.
    """
    broll = beat.get("broll")
    if broll is None:
        raise ValueError(f"beat {beat.get('beat_id')} has no broll spec")

    # asset 類 beat（ADR-051 D5/D6/D8）不 render：素材由下載交接／修修外供
    # 落地到 episode 目錄後，這裡只做存在＋digest 驗收。cached_hash 回空字串
    # （emit 對 asset beat 走 broll.asset.path，不走 b_roll_<hash>.mp4）。
    if broll.get("render_target") == "asset":
        return _verify_asset_beat(beat, broll, out_dir), "", False

    cached_hash = compute_beat_hash(beat, ctx)
    out_path = out_dir / f"b_roll_{cached_hash}.mp4"

    if use_cache and out_path.exists():
        logger.info(
            "beat %s cache hit at %s (hash=%s) — skipping worker",
            beat.get("beat_id"),
            out_path,
            cached_hash,
        )
        return out_path, cached_hash, True

    out_dir.mkdir(parents=True, exist_ok=True)

    target = broll["render_target"]
    if target == "hyperframes":
        rendered = await hyperframes_worker.render(beat, out_dir, cached_hash=cached_hash)
    elif target == "reader-playwright":
        rendered = await reader_playwright_worker.render(beat, out_dir)
    elif target == "web-playwright":
        rendered = await web_playwright_worker.render(beat, out_dir)
    else:
        raise ValueError(f"unknown render_target: {target!r}")

    return rendered, cached_hash, False


async def run_queue(
    beats: list[dict],
    out_dir: Path,
    concurrency: int = 1,
    *,
    use_cache: bool = True,
    ctx: HashContext | None = None,
) -> list[tuple[Path, str, bool]]:
    """Render a list of beats with a Semaphore-bounded concurrency.

    Phase 1 default concurrency=1 (ADR-032 §8 conservative). Returns
    ``[(mp4_path, hash, was_cache_hit), ...]`` in input order so the caller
    (``pipeline._cmd_render``) can update ``storyboard[beat].status``.
    """
    if concurrency < 1:
        raise ValueError("concurrency must be ≥ 1")
    sem = asyncio.Semaphore(concurrency)

    async def _one(b: dict) -> tuple[Path, str, bool]:
        async with sem:
            return await dispatch_beat(b, out_dir, use_cache=use_cache, ctx=ctx)

    return await asyncio.gather(*[_one(b) for b in beats])
