from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run_short_titles as titles  # noqa: E402


def _title(index: int, *, tier: int = 1) -> dict:
    t0 = float(index * 3)
    t1 = t0 + 2.56
    params = {
        "text": f"重點{index}",
        "tier": tier,
        "style": "paper",
        "show_sec": 2.56,
        "pos_y": 0.6,
    }
    projection = {
        "materialization_id": f"title-{index:03d}-s01",
        "media": {"path": f"highlights/visual-pipeline/title-{index:03d}.mov"},
        "source_range": {"start_sec": 0.0, "end_sec": 2.56},
        "render_spec": {"render_params": params},
    }
    return {
        "text": params["text"],
        "t0": t0,
        "t1": t1,
        "tier": tier,
        "visual_materialization": projection,
    }


def _install_apply_inputs(monkeypatch: pytest.MonkeyPatch, recipe: list[dict], *, fmt: str) -> None:
    identity = {"content_hash": "a" * 64}
    master = SimpleNamespace(identity=lambda: identity)
    candidate = {"format": fmt, "title": "Audited cut"}
    winner = {"rank": 1}
    lineage = {"content_hash": "b" * 64}
    monkeypatch.setattr(titles, "_open_editorial_master", lambda _episode: master)
    monkeypatch.setattr(titles, "_load_winner", lambda *_args: (candidate, winner))
    monkeypatch.setattr(titles, "_load_titles", lambda *_args: (Path("titles.json"), recipe))
    monkeypatch.setattr(
        titles,
        "verify_visual_recipe_lineage",
        lambda *_args, **_kwargs: (lineage, []),
    )


def _resolve_with_duration(
    episode: Path,
    *,
    duration_sec: float,
    timeline_name: str = "長1 - Audited cut（緊·導播）",
    timeline_uid: str = "timeline-uid",
):
    track_count = 1

    def add_track(_track_type: str) -> bool:
        nonlocal track_count
        track_count += 1
        return True

    director = SimpleNamespace(
        GetName=lambda: timeline_name,
        GetUniqueId=lambda: timeline_uid,
        GetEndFrame=lambda: int(duration_sec * 30),
        GetStartFrame=lambda: 0,
        GetTrackCount=lambda _track_type: track_count,
        GetItemListInTrack=lambda *_args: [],
        AddTrack=add_track,
        DeleteClips=lambda _clips: True,
    )
    cards_bin = SimpleNamespace(GetName=lambda: "Cards", GetClipList=lambda: [])
    root = SimpleNamespace(GetSubFolderList=lambda: [cards_bin])
    media_item = SimpleNamespace(SetClipProperty=lambda *_args: True)
    media_pool = SimpleNamespace(
        GetRootFolder=lambda: root,
        SetCurrentFolder=lambda _folder: True,
        ImportMedia=lambda _paths: [media_item],
        AppendToTimeline=lambda _items: True,
        DeleteTimelines=lambda _timelines: True,
        DeleteClips=lambda _clips: True,
    )
    project = SimpleNamespace(
        GetName=lambda: episode.name,
        GetSetting=lambda key: "30" if key == "timelineFrameRate" else "",
        GetMediaPool=lambda: media_pool,
        GetTimelineCount=lambda: 1,
        GetTimelineByIndex=lambda index: director if index == 1 else None,
        SetCurrentTimeline=lambda _timeline: True,
        GetCurrentTimeline=lambda: director,
    )
    manager = SimpleNamespace(
        GetCurrentProject=lambda: project,
        LoadProject=lambda _name: project,
        SaveProject=lambda: True,
    )
    return SimpleNamespace(GetProjectManager=lambda: manager)


def test_apply_accepts_thirteen_audited_long_hero_titles_within_density(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipe = [_title(index) for index in range(13)]
    _install_apply_inputs(monkeypatch, recipe, fmt="long")
    resolve = _resolve_with_duration(tmp_path, duration_sec=180.0)
    monkeypatch.setattr("build_resolve_project.connect_resolve", lambda: resolve)

    result = titles.apply(tmp_path, "value-L02")

    assert result["status"] == "titled"
    assert len(result["cards"]) == 13


@pytest.mark.parametrize("hero_count", [0, 4])
def test_apply_keeps_short_hero_count_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hero_count: int
) -> None:
    recipe = [_title(index) for index in range(hero_count)]
    if hero_count == 0:
        recipe = [_title(0, tier=2)]
    _install_apply_inputs(monkeypatch, recipe, fmt="short")
    monkeypatch.setattr(
        "build_resolve_project.connect_resolve",
        lambda: (_ for _ in ()).throw(AssertionError("Resolve must not be opened")),
    )

    with pytest.raises(SystemExit, match=rf"hero.*{hero_count} 張"):
        titles.apply(tmp_path, "punch-S1")


def test_apply_rejects_overdense_long_titles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipe = [_title(index) for index in range(13)]
    _install_apply_inputs(monkeypatch, recipe, fmt="long")
    resolve = _resolve_with_duration(tmp_path, duration_sec=45.0)
    monkeypatch.setattr("build_resolve_project.connect_resolve", lambda: resolve)

    with pytest.raises(SystemExit, match=r"密度上限 10 張"):
        titles.apply(tmp_path, "value-L02")


def test_orchestrator_apply_trusts_approved_text_density_and_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipe = [_title(index) for index in range(13)]
    recipe[0]["text"] = "ENTRY-LEVEL JOB"
    recipe[0]["visual_materialization"]["render_spec"]["render_params"]["text"] = "ENTRY-LEVEL JOB"
    recipe[-1]["t1"] = recipe[-1]["t0"] + 7.88
    recipe[-1]["visual_materialization"]["source_range"]["end_sec"] = 7.88
    recipe[-1]["visual_materialization"]["render_spec"]["render_params"]["show_sec"] = 7.88
    _install_apply_inputs(monkeypatch, recipe, fmt="long")
    timeline_name = "長2 - Audited cut（緊·導播）"
    timeline_uid = "long-orchestrator-work-uid"
    resolve = _resolve_with_duration(
        tmp_path,
        duration_sec=45.0,
        timeline_name=timeline_name,
        timeline_uid=timeline_uid,
    )
    monkeypatch.setattr("build_resolve_project.connect_resolve", lambda: resolve)
    broll_recipe = tmp_path / "broll.json"
    broll_recipe.write_text(
        '{"items":[{"kind":"concept","slug":"overlap","t0":0,"t1":2.56,"y_pct":63,"size_pct":40}]}',
        encoding="utf-8",
    )

    result = titles.apply(
        tmp_path,
        "value-L02",
        orchestrator_timeline_name=timeline_name,
        orchestrator_timeline_uid=timeline_uid,
        broll_recipe_path=broll_recipe,
    )

    assert result["status"] == "titled"
    assert len(result["cards"]) == 13
