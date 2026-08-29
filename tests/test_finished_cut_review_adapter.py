from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agents.brook.script_video.finished_cut_production import (
    ArtifactView,
    ComponentView,
    CutView,
    EventView,
    FinishedCutInspection,
)
from thousand_sunny.adapters import finished_cut_review as adapter_module
from thousand_sunny.adapters.finished_cut_review import (
    FinishedCutReviewAdapter,
    ReviewState,
)


class _FakeInspector:
    def __init__(self, result: FinishedCutInspection) -> None:
        self.result = result
        self.calls: list[str] = []

    def inspect_current(self, episode_id: str) -> FinishedCutInspection:
        self.calls.append(episode_id)
        return self.result


def _artifact(path: str, seed: str, *, duration_sec: float | None = None) -> ArtifactView:
    return ArtifactView(
        reference=path,
        bytes=len(seed),
        sha256=(seed.encode("utf-8").hex() + "0" * 64)[:64],
        duration_sec=duration_sec,
        probe=(("codec", "h264"), ("stream_count", 2)),
    )


def _release(
    cut_id: str,
    *,
    duration_sec: float,
    format: str = "long",
    events: tuple[EventView, ...] = (),
    components: tuple[ComponentView, ...] = (),
) -> CutView:
    return CutView(
        release_id=f"release-{cut_id}",
        cut_id=cut_id,
        format=format,
        preview=_artifact(
            f"highlights/preview/{cut_id}.mp4",
            f"preview-{cut_id}",
            duration_sec=duration_sec,
        ),
        subtitle=_artifact(f"highlights/srt/{cut_id}.srt", f"subtitle-{cut_id}"),
        events=events,
        components=components,
    )


def _inspection(
    *cuts: CutView,
    episode_id: str = "episode-001",
) -> FinishedCutInspection:
    return FinishedCutInspection(episode_id=episode_id, state="ready", cuts=cuts)


def test_missing_current_is_explicit_and_never_reads_a_historical_sentinel(
    tmp_path: Path,
) -> None:
    sentinel = tmp_path / "finished_review_manifest_99999999.json"
    sentinel.write_text('{"schema":"historical"}', encoding="utf-8")
    inspector = _FakeInspector(
        FinishedCutInspection(
            episode_id="episode-001",
            state="missing",
            error_code="current_release_missing",
        )
    )
    adapter = FinishedCutReviewAdapter(inspector)

    view = adapter.load("episode-001")

    assert view.state is ReviewState.MISSING
    assert view.episode_id == "episode-001"
    assert view.cuts == ()
    assert view.review_capability.enabled is False
    assert inspector.calls == ["episode-001"]
    assert sentinel.read_text(encoding="utf-8") == '{"schema":"historical"}'


@pytest.mark.parametrize(
    "_scenario",
    [
        "corrupt current index",
        "wrong episode identity",
        "release digest mismatch",
    ],
)
def test_corrupt_wrong_episode_and_release_digest_are_invalid(_scenario: str) -> None:
    inspector = _FakeInspector(
        FinishedCutInspection(
            episode_id="episode-001",
            state="invalid",
            error_code="current_release_invalid",
        )
    )
    adapter = FinishedCutReviewAdapter(inspector)

    view = adapter.load("episode-001")

    assert view.state is ReviewState.INVALID
    assert view.cuts == ()
    assert view.review_capability.enabled is False
    assert view.review_capability.reason == "current_invalid"
    assert view.error == "current_release_invalid"


def test_v3_three_cut_release_index_projects_exact_artifacts() -> None:
    releases = tuple(
        _release(cut_id, duration_sec=duration)
        for cut_id, duration in (
            ("value-L01", 592.0),
            ("value-L02", 511.25),
            ("punch-L04", 489.5),
        )
    )
    adapter = FinishedCutReviewAdapter(_FakeInspector(_inspection(*releases)))

    view = adapter.load("episode-001")

    assert view.state is ReviewState.READY
    assert view.review_capability.enabled is True
    assert tuple(cut.cut_id for cut in view.cuts) == ("value-L01", "value-L02", "punch-L04")
    assert view.cuts[1].preview.reference == "highlights/preview/value-L02.mp4"
    assert view.cuts[1].preview.duration_sec == 511.25
    assert view.cuts[1].preview.probe == (("codec", "h264"), ("stream_count", 2))
    assert view.cuts[2].subtitle.reference == "highlights/srt/punch-L04.srt"


def test_inspector_result_for_a_different_episode_is_invalid() -> None:
    release = _release("value-L01", duration_sec=592.0)
    adapter = FinishedCutReviewAdapter(
        _FakeInspector(_inspection(release, episode_id="episode-other"))
    )

    view = adapter.load("episode-001")

    assert view.state is ReviewState.INVALID
    assert view.cuts == ()
    assert view.review_capability.enabled is False
    assert view.error == "current_release_invalid"


def test_short_release_is_projected_without_a_virtual_manifest(tmp_path: Path) -> None:
    legacy_packet = tmp_path / "highlights" / "review" / "KS1" / "events.json"
    legacy_packet.parent.mkdir(parents=True)
    legacy_packet.write_text('{"events":["must not be read"]}', encoding="utf-8")
    inspector = _FakeInspector(_inspection(_release("KS1", duration_sec=58.0, format="short")))
    adapter = FinishedCutReviewAdapter(inspector)

    view = adapter.load("episode-001")

    assert view.state is ReviewState.READY
    assert view.cuts[0].format == "short"
    assert view.cuts[0].preview.reference == "highlights/preview/KS1.mp4"
    assert inspector.calls == ["episode-001"]
    assert not (legacy_packet.parents[1] / "virtual_short_finished_review_manifest.json").exists()


def test_ready_projection_is_read_only_and_has_no_current_writer(tmp_path: Path) -> None:
    current = tmp_path / "finished_review_manifest_current.json"
    current.write_bytes(b"sentinel current bytes")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}
    adapter = FinishedCutReviewAdapter(
        _FakeInspector(_inspection(_release("value-L01", duration_sec=592.0)))
    )

    view = adapter.load("episode-001")

    after = {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}
    assert view.state is ReviewState.READY
    assert after == before
    assert not hasattr(adapter, "publish_current")
    assert not hasattr(adapter, "replace_current")


def test_adapter_has_no_legacy_visual_pipeline_or_glob_dependency() -> None:
    source = Path(adapter_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    imported_modules.update(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    called_names.update(
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    )

    assert "highlight_visual_pipeline" not in source
    assert "highlight_visual_pipeline" not in " ".join(imported_modules)
    assert "finished_cut_production._" not in source
    assert {
        "FinishedCutRelease",
        "ReleaseArtifact",
        "ReleaseLifecycleError",
    }.isdisjoint(imported_modules)
    assert {"visual_pipeline_status", "verify_visual_pipeline", "glob"}.isdisjoint(called_names)


def test_event_anchors_and_typed_component_kinds_and_lanes_project_without_inference() -> None:
    events = (
        EventView(
            event_id="event-chapter",
            master_cue_ids=("cue-101", "cue-102"),
            text="下一個完整論點",
            text_hash="a" * 64,
            t0=0.0,
            t1=2.5,
            section_id="section-02",
            intent="chapter_boundary",
            display="下一個黃金年代，屬於誰？",
            semantic_kind="chapter",
            implementation_kind="fullscreen_transition",
            lane="fullscreen_transition",
            asset_ref=None,
            visual_status="approved",
            intentional_aroll=False,
        ),
        EventView(
            event_id="event-support",
            master_cue_ids=("cue-211",),
            text="也要照顧 Agency 自主權",
            text_hash="b" * 64,
            t0=13.25,
            t1=16.0,
            section_id="section-02",
            intent="supporting_emphasis",
            display="也要照顧 Agency 自主權",
            semantic_kind="supporting_title",
            implementation_kind="supporting_title",
            lane="supporting_title",
            asset_ref="asset:neutral-photo:sha256:" + "c" * 64,
            visual_status="approved",
            intentional_aroll=False,
        ),
    )
    components = (
        ComponentView(
            component_id="component-chapter",
            event_id="event-chapter",
            semantic_kind="chapter",
            implementation_kind="fullscreen_transition",
            lane="fullscreen_transition",
            display="下一個黃金年代，屬於誰？",
            t0=0.0,
            t1=2.5,
            asset_ref=None,
        ),
        ComponentView(
            component_id="component-hero",
            event_id="event-support",
            semantic_kind="hero_title",
            implementation_kind="hero_title",
            lane="hero_title",
            display="K 型發展",
            t0=8.0,
            t1=11.0,
            asset_ref=None,
        ),
        ComponentView(
            component_id="component-support",
            event_id="event-support",
            semantic_kind="supporting_title",
            implementation_kind="supporting_title",
            lane="supporting_title",
            display="也要照顧 Agency 自主權",
            t0=13.25,
            t1=16.0,
            asset_ref="asset:neutral-photo:sha256:" + "c" * 64,
        ),
        ComponentView(
            component_id="component-b-roll",
            event_id="event-support",
            semantic_kind="b_roll",
            implementation_kind="person_inset",
            lane="b_roll",
            display="簡立峰博士",
            t0=18.0,
            t1=22.0,
            asset_ref="asset:neutral-photo:sha256:" + "c" * 64,
        ),
        ComponentView(
            component_id="component-identity",
            event_id="event-support",
            semantic_kind="identity_card",
            implementation_kind="identity_card",
            lane="identity_card",
            display="講者身分",
            t0=24.0,
            t1=27.0,
            asset_ref=None,
        ),
        ComponentView(
            component_id="component-effect",
            event_id="event-support",
            semantic_kind="visual_effect",
            implementation_kind="visual_effect",
            lane="visual_effect",
            display="焦點強調",
            t0=29.0,
            t1=31.0,
            asset_ref=None,
        ),
    )
    adapter = FinishedCutReviewAdapter(
        _FakeInspector(
            _inspection(
                _release(
                    "punch-L04",
                    duration_sec=489.5,
                    events=events,
                    components=components,
                ),
            )
        )
    )

    cut = adapter.load("episode-001").cuts[0]

    assert tuple(
        (
            event.event_id,
            event.master_cue_ids,
            event.text,
            event.text_hash,
            event.t0,
            event.t1,
            event.section_id,
            event.intent,
            event.display,
            event.semantic_kind,
            event.implementation_kind,
            event.lane,
            event.asset_ref,
            event.visual_status,
            event.intentional_aroll,
        )
        for event in cut.events
    ) == tuple(
        (
            event.event_id,
            event.master_cue_ids,
            event.text,
            event.text_hash,
            event.t0,
            event.t1,
            event.section_id,
            event.intent,
            event.display,
            event.semantic_kind,
            event.implementation_kind,
            event.lane,
            event.asset_ref,
            event.visual_status,
            event.intentional_aroll,
        )
        for event in events
    )
    assert tuple(
        (
            component.component_id,
            component.event_id,
            component.semantic_kind,
            component.implementation_kind,
            component.lane,
            component.display,
            component.t0,
            component.t1,
            component.asset_ref,
        )
        for component in cut.components
    ) == tuple(
        (
            component.component_id,
            component.event_id,
            component.semantic_kind,
            component.implementation_kind,
            component.lane,
            component.display,
            component.t0,
            component.t1,
            component.asset_ref,
        )
        for component in components
    )
    assert tuple(
        (component.semantic_kind, component.implementation_kind, component.lane)
        for component in cut.components
    ) == (
        ("chapter", "fullscreen_transition", "fullscreen_transition"),
        ("hero_title", "hero_title", "hero_title"),
        ("supporting_title", "supporting_title", "supporting_title"),
        ("b_roll", "person_inset", "b_roll"),
        ("identity_card", "identity_card", "identity_card"),
        ("visual_effect", "visual_effect", "visual_effect"),
    )
