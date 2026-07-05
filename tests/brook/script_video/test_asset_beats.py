"""Asset 類 B-roll beat（ADR-051 D5/D6/D8）＋ export_hash 路徑回歸.

Codex panel 2026-07-05 要求 asset 語意在 schema / dispatcher / emitter /
hash 同一批定義 — 本檔逐接縫驗證。
"""

from __future__ import annotations

import asyncio
import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.brook.script_video import render_dispatcher
from agents.brook.script_video.export_hash import (
    DEFAULT_COMPOSITIONS_DIR,
    DEFAULT_GUARDRAILS_PATH,
    DEFAULT_LAYOUTS_DIR,
    compute_beat_hash,
)
from agents.brook.script_video.schemas.storyboard import AssetSpec, BRollSpec, Transitions

# ---------------------------------------------------------------------------
# export_hash 預設路徑（ADR-050 搬遷回歸 — 修正前指向 agents/agents/foundry/）
# ---------------------------------------------------------------------------


def test_export_hash_default_paths_exist() -> None:
    assert DEFAULT_LAYOUTS_DIR.exists(), DEFAULT_LAYOUTS_DIR
    assert DEFAULT_COMPOSITIONS_DIR.exists(), DEFAULT_COMPOSITIONS_DIR
    assert DEFAULT_GUARDRAILS_PATH.exists(), DEFAULT_GUARDRAILS_PATH


def test_compute_beat_hash_works_with_default_context() -> None:
    beat = {
        "beat_id": 1,
        "broll_decision": "cutaway",
        "layout": "full_broll",
        "broll": {"render_target": "hyperframes", "component": "bigstat", "params": {}},
    }
    h = compute_beat_hash(beat)  # 修正前 raise FileNotFoundError
    assert len(h) == 16


# ---------------------------------------------------------------------------
# schema：asset ⟺ render_target="asset"
# ---------------------------------------------------------------------------


def _asset_spec(**over) -> AssetSpec:
    base = dict(kind="stock", path="assets/clip.mp4", source_url="https://elements.envato.com/x")
    base.update(over)
    return AssetSpec(**base)


def test_asset_target_requires_asset_spec() -> None:
    with pytest.raises(ValidationError, match="必須帶 broll.asset"):
        BRollSpec(render_target="asset", component="stock", params={}, transitions=Transitions())


def test_non_asset_target_forbids_asset_spec() -> None:
    with pytest.raises(ValidationError, match="僅限 render_target='asset'"):
        BRollSpec(
            render_target="hyperframes",
            component="bigstat",
            params={},
            transitions=Transitions(),
            asset=_asset_spec(),
        )


def test_valid_asset_beat_roundtrip() -> None:
    spec = BRollSpec(
        render_target="asset",
        component="kol",
        params={},
        transitions=Transitions(),
        asset=_asset_spec(
            kind="kol",
            source_span="00:12:03-00:12:15",
            attribution="Andrew Huberman — Huberman Lab Ep. 42",
        ),
    )
    dumped = spec.model_dump()
    assert dumped["asset"]["kind"] == "kol"
    assert BRollSpec(**dumped) == spec


# ---------------------------------------------------------------------------
# dispatcher：asset beat 驗收（存在 + digest）
# ---------------------------------------------------------------------------


def _asset_beat(path: str | None, sha256: str | None = None) -> dict:
    asset: dict = {"kind": "stock", "path": path}
    if sha256:
        asset["sha256"] = sha256
    return {
        "beat_id": 7,
        "broll_decision": "cutaway",
        "layout": "full_broll",
        "broll": {
            "render_target": "asset",
            "component": "stock",
            "params": {},
            "asset": asset,
        },
    }


def test_dispatch_asset_beat_verifies_file(tmp_path: Path) -> None:
    ep = tmp_path / "ep-1"
    (ep / "assets").mkdir(parents=True)
    (ep / "out").mkdir()
    f = ep / "assets" / "clip.mp4"
    f.write_bytes(b"fake video bytes")

    path, cached_hash, was_hit = asyncio.run(
        render_dispatcher.dispatch_beat(_asset_beat("assets/clip.mp4"), ep / "out")
    )
    assert path == f
    assert cached_hash == ""
    assert was_hit is False


def test_dispatch_asset_beat_fails_loud_when_missing(tmp_path: Path) -> None:
    ep = tmp_path / "ep-1"
    (ep / "out").mkdir(parents=True)
    with pytest.raises(ValueError, match="不存在"):
        asyncio.run(render_dispatcher.dispatch_beat(_asset_beat("assets/nope.mp4"), ep / "out"))


def test_dispatch_asset_beat_fails_loud_without_path(tmp_path: Path) -> None:
    ep = tmp_path / "ep-1"
    (ep / "out").mkdir(parents=True)
    with pytest.raises(ValueError, match="素材尚未取得"):
        asyncio.run(render_dispatcher.dispatch_beat(_asset_beat(None), ep / "out"))


def test_dispatch_asset_beat_digest_mismatch(tmp_path: Path) -> None:
    ep = tmp_path / "ep-1"
    (ep / "assets").mkdir(parents=True)
    (ep / "out").mkdir()
    f = ep / "assets" / "clip.mp4"
    f.write_bytes(b"replaced content")
    wrong = hashlib.sha256(b"original content").hexdigest()
    with pytest.raises(ValueError, match="digest 不符"):
        asyncio.run(
            render_dispatcher.dispatch_beat(_asset_beat("assets/clip.mp4", wrong), ep / "out")
        )


def test_dispatch_asset_beat_digest_match(tmp_path: Path) -> None:
    ep = tmp_path / "ep-1"
    (ep / "assets").mkdir(parents=True)
    (ep / "out").mkdir()
    f = ep / "assets" / "clip.mp4"
    content = b"stable content"
    f.write_bytes(content)
    good = hashlib.sha256(content).hexdigest()
    path, _, _ = asyncio.run(
        render_dispatcher.dispatch_beat(_asset_beat("assets/clip.mp4", good), ep / "out")
    )
    assert path == f


# ---------------------------------------------------------------------------
# emitter：asset beat 直接引用素材檔（不走 b_roll_<hash>.mp4）
# ---------------------------------------------------------------------------


def test_emit_resolves_asset_beat_from_asset_path(tmp_path: Path, monkeypatch) -> None:
    from agents.brook.script_video import fcpxml_emitter

    ep = tmp_path / "ep-emit"
    (ep / "assets").mkdir(parents=True)
    (ep / "out").mkdir()
    (ep / "raw_recording.mp4").write_bytes(b"aroll")
    clip = ep / "assets" / "huberman_talk.mp4"
    clip.write_bytes(b"broll")

    durations = {"raw_recording.mp4": 60.0, "huberman_talk.mp4": 8.0}
    monkeypatch.setattr(fcpxml_emitter, "_mp4_duration", lambda p: durations[Path(p).name])

    beat = _asset_beat("assets/huberman_talk.mp4")
    beat["timing"] = {"start": 12.0, "duration": 8.0}
    beat["status"] = {"render_status": "done"}

    out = fcpxml_emitter.emit([beat], ep)

    root = ET.parse(out).getroot()
    hrefs = [mr.get("src", "") for mr in root.iter() if mr.tag == "media-rep"]
    assert any("huberman_talk" in h for h in hrefs), hrefs
    clips = [c for c in root.iter() if c.tag == "asset-clip" and c.get("lane") == "1"]
    assert len(clips) == 1
    assert clips[0].get("name") == "huberman_talk"


def test_emit_asset_beat_fails_loud_when_file_missing(tmp_path: Path, monkeypatch) -> None:
    from agents.brook.script_video import fcpxml_emitter

    ep = tmp_path / "ep-emit2"
    (ep / "out").mkdir(parents=True)
    (ep / "raw_recording.mp4").write_bytes(b"aroll")
    monkeypatch.setattr(fcpxml_emitter, "_mp4_duration", lambda p: 60.0)

    beat = _asset_beat("assets/missing.mp4")
    beat["timing"] = {"start": 1.0, "duration": 2.0}
    beat["status"] = {"render_status": "done"}

    with pytest.raises(ValueError, match="素材尚未落地"):
        fcpxml_emitter.emit([beat], ep)


# ---------------------------------------------------------------------------
# Bridge promote-to-example 路徑（ADR-050 遷移漏網回歸）
# ---------------------------------------------------------------------------


def test_promote_examples_dir_points_at_brook_not_foundry() -> None:
    from thousand_sunny.routers import brook_video

    assert "foundry" not in str(brook_video._EXAMPLES_DIR)
    assert brook_video._EXAMPLES_DIR.parent == (
        Path(brook_video.__file__).resolve().parents[2] / "agents" / "brook" / "script_video"
    )
