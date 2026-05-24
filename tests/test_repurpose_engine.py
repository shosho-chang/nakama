"""Tests for agents.brook.repurpose_engine.

Covers:
- RepurposeEngine.run() happy path: stage1.json + all artifacts written
- Parallel fan-out: renderers run concurrently, not sequentially
- Error isolation: one renderer failing does not abort others
- I/O path conventions: run_dir matches YYYY-MM-DD-slug pattern
- Slug sanitization: special chars and CJK replaced
- Protocol conformance: Stage1Extractor + ChannelRenderer satisfy runtime_checkable
- EpisodeMetadata defaults
"""

from __future__ import annotations

import json
import time

import pytest

from agents.brook.repurpose_engine import (
    ChannelArtifact,
    ChannelRenderer,
    EpisodeMetadata,
    RepurposeEngine,
    Stage1Extractor,
    Stage1Result,
)

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeExtractor:
    def extract(self, source_input: str, metadata: EpisodeMetadata) -> Stage1Result:
        return Stage1Result(
            data={"title": "test", "episode_type": "narrative_journey", "quotes": ["Q1"]},
            source_repr=source_input[:40],
        )


class _FakeRenderer:
    """Configurable fake renderer supporting sleep + failure injection."""

    def __init__(
        self,
        filename: str,
        channel: str,
        *,
        sleep: float = 0.0,
        fail: bool = False,
    ):
        self._filename = filename
        self._channel = channel
        self._sleep = sleep
        self._fail = fail
        self.called_at: float | None = None

    def render(self, stage1: Stage1Result, metadata: EpisodeMetadata) -> list[ChannelArtifact]:
        self.called_at = time.monotonic()
        if self._sleep:
            time.sleep(self._sleep)
        if self._fail:
            raise RuntimeError(f"renderer {self._channel} exploded")
        return [
            ChannelArtifact(
                filename=self._filename,
                content=f"content-{self._channel}",
                channel=self._channel,
            )
        ]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_run_writes_stage1_and_artifacts(tmp_path, monkeypatch):
    """stage1.json and all artifact files written on happy path."""
    monkeypatch.setattr("agents.brook.repurpose_engine._DATA_ROOT", tmp_path)

    blog_renderer = _FakeRenderer("blog.md", "blog")
    engine = RepurposeEngine(
        extractor=_FakeExtractor(),
        renderers={"blog": blog_renderer},
    )
    meta = EpisodeMetadata(slug="dr-chu", date="2026-05-01")
    result = engine.run("srt content here", meta)

    run_dir = tmp_path / "2026-05-01-dr-chu"
    assert run_dir.is_dir()

    stage1_file = run_dir / "stage1.json"
    assert stage1_file.exists()
    data = json.loads(stage1_file.read_text())
    assert data["title"] == "test"
    assert data["episode_type"] == "narrative_journey"

    blog_file = run_dir / "blog.md"
    assert blog_file.exists()
    assert blog_file.read_text() == "content-blog"

    assert result.run_dir == run_dir
    assert len(result.artifacts) == 1
    assert result.artifacts[0].channel == "blog"
    assert not result.errors


def test_run_multiple_renderers_all_artifacts_written(tmp_path, monkeypatch):
    """Multiple renderers each produce their artifact."""
    monkeypatch.setattr("agents.brook.repurpose_engine._DATA_ROOT", tmp_path)

    renderers = {
        "blog": _FakeRenderer("blog.md", "blog"),
        "fb": _FakeRenderer("fb-light.md", "fb-light"),
        "ig": _FakeRenderer("ig-cards.json", "ig"),
    }
    engine = RepurposeEngine(extractor=_FakeExtractor(), renderers=renderers)
    meta = EpisodeMetadata(slug="ep1", date="2026-05-01")
    result = engine.run("src", meta)

    run_dir = tmp_path / "2026-05-01-ep1"
    assert (run_dir / "stage1.json").exists()
    assert (run_dir / "blog.md").exists()
    assert (run_dir / "fb-light.md").exists()
    assert (run_dir / "ig-cards.json").exists()
    assert len(result.artifacts) == 3
    assert not result.errors


def test_run_multi_artifact_renderer(tmp_path, monkeypatch):
    """A renderer returning multiple artifacts (e.g. FBRenderer 4 tonals) writes all."""
    monkeypatch.setattr("agents.brook.repurpose_engine._DATA_ROOT", tmp_path)

    class _MultiFBRenderer:
        def render(self, stage1, metadata):
            return [
                ChannelArtifact(filename=f"fb-{t}.md", content=f"fb-{t}", channel=f"fb-{t}")
                for t in ("light", "emotional", "serious", "neutral")
            ]

    engine = RepurposeEngine(
        extractor=_FakeExtractor(),
        renderers={"fb": _MultiFBRenderer()},
    )
    meta = EpisodeMetadata(slug="ep2", date="2026-05-01")
    result = engine.run("src", meta)

    run_dir = tmp_path / "2026-05-01-ep2"
    for tonal in ("light", "emotional", "serious", "neutral"):
        assert (run_dir / f"fb-{tonal}.md").exists()
    assert len(result.artifacts) == 4
    assert not result.errors


# ---------------------------------------------------------------------------
# Parallelism
# ---------------------------------------------------------------------------


def test_run_parallel_renderers_run_concurrently(tmp_path, monkeypatch):
    """All renderers start within a short window (parallel, not sequential)."""
    monkeypatch.setattr("agents.brook.repurpose_engine._DATA_ROOT", tmp_path)

    renderers = {
        "blog": _FakeRenderer("blog.md", "blog", sleep=0.05),
        "fb": _FakeRenderer("fb-light.md", "fb", sleep=0.05),
        "ig": _FakeRenderer("ig-cards.json", "ig", sleep=0.05),
    }
    engine = RepurposeEngine(extractor=_FakeExtractor(), renderers=renderers)
    meta = EpisodeMetadata(slug="ep-parallel", date="2026-05-01")

    t0 = time.monotonic()
    result = engine.run("src", meta)
    elapsed = time.monotonic() - t0

    assert not result.errors
    assert len(result.artifacts) == 3
    # Sequential would take ~0.15s; parallel upper bound < 0.13s (2× sleep + overhead).
    assert elapsed < 0.13, f"renderers appear to have run sequentially (elapsed={elapsed:.3f}s)"


# ---------------------------------------------------------------------------
# Error isolation
# ---------------------------------------------------------------------------


def test_run_one_renderer_fails_others_still_complete(tmp_path, monkeypatch):
    """Single renderer failure is isolated; other artifacts still land on disk."""
    monkeypatch.setattr("agents.brook.repurpose_engine._DATA_ROOT", tmp_path)

    renderers = {
        "blog": _FakeRenderer("blog.md", "blog"),
        "fb": _FakeRenderer("fb-light.md", "fb", fail=True),
        "ig": _FakeRenderer("ig-cards.json", "ig"),
    }
    engine = RepurposeEngine(extractor=_FakeExtractor(), renderers=renderers)
    meta = EpisodeMetadata(slug="ep-fail", date="2026-05-01")
    result = engine.run("src", meta)

    assert "fb" in result.errors
    assert isinstance(result.errors["fb"], RuntimeError)
    assert "exploded" in str(result.errors["fb"])

    run_dir = tmp_path / "2026-05-01-ep-fail"
    assert (run_dir / "blog.md").exists()
    assert (run_dir / "ig-cards.json").exists()
    assert not (run_dir / "fb-light.md").exists()

    # Successfully rendered artifacts are still in result
    channels = {a.channel for a in result.artifacts}
    assert "blog" in channels
    assert "ig" in channels


def test_run_all_renderers_fail_stage1_still_written(tmp_path, monkeypatch):
    """Even if all renderers fail, stage1.json is written (Stage 1 succeeded)."""
    monkeypatch.setattr("agents.brook.repurpose_engine._DATA_ROOT", tmp_path)

    engine = RepurposeEngine(
        extractor=_FakeExtractor(),
        renderers={"a": _FakeRenderer("a.md", "a", fail=True)},
    )
    meta = EpisodeMetadata(slug="all-fail", date="2026-05-01")
    result = engine.run("src", meta)

    assert (tmp_path / "2026-05-01-all-fail" / "stage1.json").exists()
    assert "a" in result.errors
    assert not result.artifacts


def test_run_empty_renderers(tmp_path, monkeypatch):
    """Engine with no renderers only writes stage1.json."""
    monkeypatch.setattr("agents.brook.repurpose_engine._DATA_ROOT", tmp_path)

    engine = RepurposeEngine(extractor=_FakeExtractor(), renderers={})
    meta = EpisodeMetadata(slug="no-renderers", date="2026-05-01")
    result = engine.run("src", meta)

    assert (tmp_path / "2026-05-01-no-renderers" / "stage1.json").exists()
    assert not result.artifacts
    assert not result.errors


# ---------------------------------------------------------------------------
# I/O path conventions
# ---------------------------------------------------------------------------


def test_run_dir_uses_correct_date_and_slug(tmp_path, monkeypatch):
    """run_dir matches YYYY-MM-DD-slug convention."""
    monkeypatch.setattr("agents.brook.repurpose_engine._DATA_ROOT", tmp_path)

    engine = RepurposeEngine(extractor=_FakeExtractor(), renderers={})
    meta = EpisodeMetadata(slug="my-episode", date="2026-05-01")
    result = engine.run("src", meta)

    assert result.run_dir.name == "2026-05-01-my-episode"
    assert result.run_dir.parent == tmp_path


def test_run_dir_slug_sanitized_ascii(tmp_path, monkeypatch):
    """Slugs with special characters are sanitized for filesystem safety."""
    monkeypatch.setattr("agents.brook.repurpose_engine._DATA_ROOT", tmp_path)

    engine = RepurposeEngine(extractor=_FakeExtractor(), renderers={})
    meta = EpisodeMetadata(slug="Dr. Chu / longevity!", date="2026-05-01")
    result = engine.run("src", meta)

    name = result.run_dir.name
    assert "/" not in name
    assert "." not in name
    assert "!" not in name
    assert name.startswith("2026-05-01")


def test_run_dir_cjk_slug_sanitized(tmp_path, monkeypatch):
    """CJK characters in slug are replaced (filesystem-safe)."""
    monkeypatch.setattr("agents.brook.repurpose_engine._DATA_ROOT", tmp_path)

    engine = RepurposeEngine(extractor=_FakeExtractor(), renderers={})
    meta = EpisodeMetadata(slug="朱為民醫師訪談", date="2026-05-01")
    result = engine.run("src", meta)

    name = result.run_dir.name
    assert name.startswith("2026-05-01")
    assert "朱" not in name


def test_run_dir_auto_date(tmp_path, monkeypatch):
    """Empty date in metadata is auto-resolved to today (Asia/Taipei) for the run_dir name.

    The engine must NOT mutate the caller's EpisodeMetadata — date is resolved
    locally; see test_metadata_date_not_mutated_on_run for the no-mutation contract.
    """
    monkeypatch.setattr("agents.brook.repurpose_engine._DATA_ROOT", tmp_path)

    engine = RepurposeEngine(extractor=_FakeExtractor(), renderers={})
    meta = EpisodeMetadata(slug="auto-date")
    result = engine.run("src", meta)

    # run_dir name has YYYY-MM-DD prefix (auto-resolved Asia/Taipei date)
    import re

    assert re.match(r"\d{4}-\d{2}-\d{2}-", result.run_dir.name)


def test_metadata_date_not_mutated_on_run(tmp_path, monkeypatch):
    """engine.run() must NOT mutate the caller's EpisodeMetadata.date.

    Regression: previous version assigned `metadata.date = ...` in place when
    empty, surprising callers reusing one EpisodeMetadata for batch runs.
    """
    monkeypatch.setattr("agents.brook.repurpose_engine._DATA_ROOT", tmp_path)

    engine = RepurposeEngine(extractor=_FakeExtractor(), renderers={})
    meta = EpisodeMetadata(slug="immutable", date="")
    engine.run("src", meta)

    assert meta.date == "", "engine mutated metadata.date — caller contract broken"


def test_no_leftover_dir_when_extractor_fails(tmp_path, monkeypatch):
    """Stage 1 extractor failure must NOT leave an empty run dir on disk."""
    monkeypatch.setattr("agents.brook.repurpose_engine._DATA_ROOT", tmp_path)

    class _ExploderExtractor:
        def extract(self, source_input, metadata):
            raise RuntimeError("Stage 1 boom")

    engine = RepurposeEngine(extractor=_ExploderExtractor(), renderers={})
    meta = EpisodeMetadata(slug="extract-fail", date="2026-05-01")

    with pytest.raises(RuntimeError, match="Stage 1 boom"):
        engine.run("src", meta)

    expected_dir = tmp_path / "2026-05-01-extract-fail"
    assert not expected_dir.exists(), "extractor failure left leftover dir on disk"


def test_write_failure_isolated_from_other_channels(tmp_path, monkeypatch):
    """OSError on one channel's write must NOT abort other channel writes.

    Regression: previously path.write_text was outside the try/except, so a
    disk-write failure on channel A would escape `run()` entirely and the
    "per-renderer error isolation" claim would silently drop B/C artifacts.
    """
    monkeypatch.setattr("agents.brook.repurpose_engine._DATA_ROOT", tmp_path)

    class _BadFilenameRenderer:
        """Filename pointing into nonexistent subdir → FileNotFoundError on write."""

        def render(self, stage1, metadata):
            return [ChannelArtifact(filename="missing_subdir/x.md", content="x", channel="bad")]

    blog_renderer = _FakeRenderer("blog.md", "blog")
    engine = RepurposeEngine(
        extractor=_FakeExtractor(),
        renderers={"blog": blog_renderer, "bad": _BadFilenameRenderer()},
    )
    meta = EpisodeMetadata(slug="write-fail", date="2026-05-01")
    result = engine.run("src", meta)

    # Good channel still wrote its artifact
    assert (result.run_dir / "blog.md").exists()
    # Bad channel recorded as error, no crash
    assert "bad" in result.errors
    assert isinstance(result.errors["bad"], OSError | ValueError)


def test_slug_truncation_no_trailing_dash(tmp_path, monkeypatch):
    """Slug truncated mid-segment must rstrip trailing dash (regression).

    Previous order: ``.strip("-")[:60]`` left a trailing dash if the cut
    landed on a separator. Fix: ``[:60].rstrip("-")``.
    """
    monkeypatch.setattr("agents.brook.repurpose_engine._DATA_ROOT", tmp_path)

    # Slug crafted so [:60] lands on a dash: 59 'a' + '-' + filler
    slug = "a" * 59 + "-" + "b" * 10
    engine = RepurposeEngine(extractor=_FakeExtractor(), renderers={})
    meta = EpisodeMetadata(slug=slug, date="2026-05-01")
    result = engine.run("src", meta)

    name = result.run_dir.name
    assert not name.endswith("-"), f"trailing dash leaked into run_dir name: {name!r}"


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_stage1_extractor_protocol_satisfied():
    """_FakeExtractor satisfies the Stage1Extractor Protocol at runtime."""
    assert isinstance(_FakeExtractor(), Stage1Extractor)


def test_channel_renderer_protocol_satisfied():
    """_FakeRenderer satisfies the ChannelRenderer Protocol at runtime."""
    assert isinstance(_FakeRenderer("x.md", "x"), ChannelRenderer)


def test_plain_object_does_not_satisfy_stage1_protocol():
    """An object missing the required method fails the Protocol check."""

    class _Bad:
        pass

    assert not isinstance(_Bad(), Stage1Extractor)


def test_plain_object_does_not_satisfy_renderer_protocol():
    class _Bad:
        pass

    assert not isinstance(_Bad(), ChannelRenderer)


# ---------------------------------------------------------------------------
# EpisodeMetadata defaults
# ---------------------------------------------------------------------------


def test_episode_metadata_defaults():
    meta = EpisodeMetadata(slug="test")
    assert meta.host == "張修修"
    assert meta.date == ""
    assert meta.extra == {}


def test_episode_metadata_extra_is_isolated():
    """extra dict is not shared across instances."""
    m1 = EpisodeMetadata(slug="a")
    m2 = EpisodeMetadata(slug="b")
    m1.extra["x"] = 1
    assert "x" not in m2.extra


# ---------------------------------------------------------------------------
# #682 — .approved.<channel> sentinels block overwrite
# ---------------------------------------------------------------------------


def _seed_run_dir_with_approved(tmp_path, date: str, slug: str, channel: str):
    """Helper: create run_dir with an existing artifact + .approved.<channel> sentinel."""
    run_dir = tmp_path / f"{date}-{slug}"
    run_dir.mkdir(parents=True)
    # seed approved content so we can assert "unchanged"
    if channel == "blog":
        (run_dir / "blog.md").write_text("APPROVED-BLOG", encoding="utf-8")
    elif channel == "ig":
        (run_dir / "ig-cards.json").write_text("APPROVED-IG", encoding="utf-8")
    else:
        tonal = channel.removeprefix("fb.")
        (run_dir / f"fb-{tonal}.md").write_text(f"APPROVED-FB-{tonal}", encoding="utf-8")
    (run_dir / f".approved.{channel}").touch()
    return run_dir


def test_run_skips_approved_blog_and_records_skip_exception(tmp_path, monkeypatch, caplog):
    """Engine must not overwrite blog.md when .approved.blog is present (#682)."""
    import logging

    from agents.brook.repurpose_sentinels import ApprovalSkipException

    monkeypatch.setattr("agents.brook.repurpose_engine._DATA_ROOT", tmp_path)

    run_dir = _seed_run_dir_with_approved(tmp_path, "2026-05-01", "approved-run", "blog")

    engine = RepurposeEngine(
        extractor=_FakeExtractor(),
        renderers={"blog": _FakeRenderer("blog.md", "blog")},
    )
    meta = EpisodeMetadata(slug="approved-run", date="2026-05-01")
    with caplog.at_level(logging.INFO, logger="nakama.brook.repurpose_engine"):
        result = engine.run("src", meta)

    # Approved blog content untouched
    assert (run_dir / "blog.md").read_text() == "APPROVED-BLOG"
    # Skip recorded under the channel id (not the renderer key)
    assert "blog" in result.errors
    assert isinstance(result.errors["blog"], ApprovalSkipException)
    assert result.errors["blog"].channel == "blog"
    # Successful-write list does NOT include the skipped artifact
    assert all(a.filename != "blog.md" for a in result.artifacts)
    # INFO log mentions the skip
    assert any("already approved" in r.getMessage() for r in caplog.records)


def test_run_skips_approved_fb_tonal_only(tmp_path, monkeypatch):
    """Only the specific approved fb tonal is preserved; other tonals overwrite."""
    from agents.brook.repurpose_engine import ChannelArtifact
    from agents.brook.repurpose_sentinels import ApprovalSkipException

    monkeypatch.setattr("agents.brook.repurpose_engine._DATA_ROOT", tmp_path)

    run_dir = tmp_path / "2026-05-01-fb-mix"
    run_dir.mkdir(parents=True)
    (run_dir / "fb-light.md").write_text("APPROVED-LIGHT", encoding="utf-8")
    (run_dir / ".approved.fb.light").touch()

    class _MultiFB:
        def render(self, stage1, metadata):
            return [
                ChannelArtifact(filename=f"fb-{t}.md", content=f"NEW-{t}", channel=f"fb-{t}")
                for t in ("light", "emotional", "serious", "neutral")
            ]

    engine = RepurposeEngine(extractor=_FakeExtractor(), renderers={"fb": _MultiFB()})
    meta = EpisodeMetadata(slug="fb-mix", date="2026-05-01")
    result = engine.run("src", meta)

    # Approved tonal preserved
    assert (run_dir / "fb-light.md").read_text() == "APPROVED-LIGHT"
    # Other tonals overwritten with new content
    assert (run_dir / "fb-emotional.md").read_text() == "NEW-emotional"
    assert (run_dir / "fb-serious.md").read_text() == "NEW-serious"
    assert (run_dir / "fb-neutral.md").read_text() == "NEW-neutral"
    # Skip recorded under fb.light only
    assert "fb.light" in result.errors
    assert isinstance(result.errors["fb.light"], ApprovalSkipException)
    assert "fb.emotional" not in result.errors


def test_run_force_path_via_clear_sentinels_overwrites(tmp_path, monkeypatch):
    """After clear_approval_sentinels (CLI --force surface), engine overwrites."""
    from agents.brook.repurpose_sentinels import clear_approval_sentinels

    monkeypatch.setattr("agents.brook.repurpose_engine._DATA_ROOT", tmp_path)

    run_dir = _seed_run_dir_with_approved(tmp_path, "2026-05-01", "force-run", "blog")

    # Simulate `--force` precondition: clear sentinels before engine runs
    cleared = clear_approval_sentinels(run_dir)
    assert cleared == ["blog"]
    assert not (run_dir / ".approved.blog").exists()

    engine = RepurposeEngine(
        extractor=_FakeExtractor(),
        renderers={"blog": _FakeRenderer("blog.md", "blog")},
    )
    meta = EpisodeMetadata(slug="force-run", date="2026-05-01")
    result = engine.run("src", meta)

    # Overwritten with the fake renderer's new content
    assert (run_dir / "blog.md").read_text() == "content-blog"
    assert "blog" not in result.errors
    assert len(result.artifacts) == 1


def test_run_preserves_stage1_and_unrelated_files_on_approved_rerun(tmp_path, monkeypatch):
    """Integration-ish: a full re-run on an approved run_dir does not lose the
    Annotations / approved artifacts, only refreshes stage1.json + un-approved
    channels.
    """
    from agents.brook.repurpose_engine import ChannelArtifact

    monkeypatch.setattr("agents.brook.repurpose_engine._DATA_ROOT", tmp_path)

    run_dir = tmp_path / "2026-05-01-integration"
    run_dir.mkdir(parents=True)
    (run_dir / "blog.md").write_text("APPROVED-BLOG", encoding="utf-8")
    (run_dir / "ig-cards.json").write_text("OLD-IG", encoding="utf-8")
    (run_dir / ".approved.blog").touch()
    # Simulate Annotations / sidecar file unrelated to engine channels
    (run_dir / "annotations.json").write_text('{"note": "keep me"}', encoding="utf-8")

    class _BlogRenderer:
        def render(self, stage1, metadata):
            return [ChannelArtifact(filename="blog.md", content="NEW-BLOG", channel="blog")]

    class _IGRenderer:
        def render(self, stage1, metadata):
            return [ChannelArtifact(filename="ig-cards.json", content="NEW-IG", channel="ig")]

    engine = RepurposeEngine(
        extractor=_FakeExtractor(),
        renderers={"blog": _BlogRenderer(), "ig": _IGRenderer()},
    )
    meta = EpisodeMetadata(slug="integration", date="2026-05-01")
    result = engine.run("src", meta)

    # Approved blog preserved; un-approved ig overwritten; unrelated file untouched
    assert (run_dir / "blog.md").read_text() == "APPROVED-BLOG"
    assert (run_dir / "ig-cards.json").read_text() == "NEW-IG"
    assert (run_dir / "annotations.json").read_text() == '{"note": "keep me"}'
    # stage1.json refreshed (regenerated by extractor)
    assert (run_dir / "stage1.json").exists()
    # blog skip + ig success
    assert "blog" in result.errors
    assert any(a.channel == "ig" for a in result.artifacts)


# ---------------------------------------------------------------------------
# Filename ↔ channel mapping
# ---------------------------------------------------------------------------


def test_channel_for_filename_known_mappings():
    from agents.brook.repurpose_sentinels import channel_for_filename

    assert channel_for_filename("blog.md") == "blog"
    assert channel_for_filename("ig-cards.json") == "ig"
    assert channel_for_filename("fb-light.md") == "fb.light"
    assert channel_for_filename("fb-emotional.md") == "fb.emotional"
    assert channel_for_filename("fb-serious.md") == "fb.serious"
    assert channel_for_filename("fb-neutral.md") == "fb.neutral"


def test_channel_for_filename_returns_none_for_unknown():
    from agents.brook.repurpose_sentinels import channel_for_filename

    # Defensive: stage1.json, sentinel files, sidecars all return None
    assert channel_for_filename("stage1.json") is None
    assert channel_for_filename(".approved.blog") is None
    assert channel_for_filename("annotations.json") is None
    assert channel_for_filename("fb-unknown.md") is None
