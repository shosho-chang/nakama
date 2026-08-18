from __future__ import annotations

import sys
from argparse import Namespace

import pytest

from agents.brook.podcast_carousel_copy import build_transcript_index
from agents.brook.podcast_carousel_panel import (
    PanelFinding,
    PanelResult,
    PanelReview,
    PanelSynthesis,
)
from scripts import run_podcast_carousel
from shared.schemas.podcast_carousel import (
    CoverPage,
    CTAPage,
    EpisodeMetadata,
    HookPage,
    PodcastCarouselCopySpecV1,
    PointPage,
    QuotePage,
)


def _inputs(tmp_path):
    episode_dir = tmp_path / "episode"
    episode_dir.mkdir()
    prose = episode_dir / "transcript_prose.md"
    prose.write_text(
        "**張修修**：為什麼大家只看到成功？\n\n"
        "**鄭國威**：演算法會把失敗作品沉下去，所以大家只看到成功。\n",
        encoding="utf-8",
    )
    srt = episode_dir / "transcript.srt"
    srt.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\n為什麼大家只看到成功\n\n"
        "2\n00:00:03,000 --> 00:00:08,000\n"
        "演算法會把失敗作品沉下去所以大家只看到成功\n",
        encoding="utf-8",
    )
    cutouts = episode_dir / "packaging" / "cutouts"
    cutouts.mkdir(parents=True)
    (cutouts / "guest.png").write_bytes(b"png")
    (cutouts / "host.png").write_bytes(b"png")
    template_dir = tmp_path / "template"
    template_dir.mkdir()

    index = build_transcript_index(prose, srt)
    host_evidence = index.evidence(["B0001"])
    guest_evidence = index.evidence(["B0002"])
    spec = PodcastCarouselCopySpecV1(
        episode_id="ep120",
        revision="r001",
        episode=EpisodeMetadata(
            number=120,
            topic="演算法藏起來的失敗",
            guest_name="鄭國威",
            guest_title="泛科學共同創辦人",
        ),
        pages=[
            CoverPage(
                page_id="cover",
                headline="演算法藏起來的失敗",
                emphasis="藏起來的失敗",
                guest_name="鄭國威",
                guest_title="泛科學共同創辦人",
                cutout="guest.png",
                evidence=guest_evidence,
            ),
            HookPage(
                page_id="hook",
                question="為什麼大家只看到成功？",
                emphasis="只看到成功",
                bridge="先看演算法藏起了什麼。",
                evidence=host_evidence + guest_evidence,
            ),
            PointPage(
                page_id="point-algorithm",
                headline="演算法會沉掉失敗作品",
                emphasis="沉掉失敗作品",
                body="因此觀眾更常只看到成功。",
                evidence=guest_evidence,
            ),
            QuotePage(
                page_id="quote",
                variant="B",
                text="所以大家只看到成功。",
                emphasis="只看到成功",
                guest_name="鄭國威",
                guest_cutout="guest.png",
                host_question="為什麼大家只看到成功？",
                host_question_evidence=host_evidence,
                host_cutout="host.png",
                evidence=guest_evidence,
            ),
            CTAPage(
                page_id="cta",
                episode_topic="演算法藏起來的失敗",
                emphasis="失敗",
                engagement_question="你如何看待失敗？",
                evidence=guest_evidence,
            ),
        ],
        publish_compatibility="api_compatible",
    )
    return episode_dir, template_dir, spec


def _pass_reviews():
    return {
        lens: PanelReview(lens=lens, verdict="pass", findings=[])
        for lens in ("ig_audience", "episode_editorial", "brand_evidence")
    }


def _write_artifacts(tmp_path, spec, panel):
    spec_path = tmp_path / "copy_spec.v1.json"
    panel_path = tmp_path / "panel_result.v1.json"
    spec_path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    panel_path.write_text(panel.model_dump_json(indent=2), encoding="utf-8")
    return spec_path, panel_path


def _args(episode_dir, template_dir, spec_path, panel_path):
    return Namespace(
        episode_dir=episode_dir,
        template_dir=template_dir,
        copy_spec=spec_path,
        panel_result=panel_path,
        force=False,
    )


def test_runner_only_validates_reviewed_artifacts_and_renders(tmp_path, monkeypatch):
    episode_dir, template_dir, spec = _inputs(tmp_path)
    panel = PanelResult(
        episode_id="ep120",
        revision="r001",
        status="converged",
        reviews=_pass_reviews(),
        verified_findings=[],
        verification_rejections=[],
        synthesis=PanelSynthesis(
            accepted_finding_ids=[],
            revision_instructions=[],
            blockers=[],
        ),
    )
    spec_path, panel_path = _write_artifacts(tmp_path, spec, panel)
    rendered = []
    monkeypatch.setitem(sys.modules, "shared.llm", None)
    monkeypatch.setattr(
        run_podcast_carousel,
        "render_carousel",
        lambda **kwargs: rendered.append(kwargs),
    )

    summary = run_podcast_carousel.run(
        _args(episode_dir, template_dir, spec_path, panel_path)
    )

    editorial = episode_dir / "ig-carousel" / "editorial" / "r001"
    assert (editorial / "copy_spec.v1.json").is_file()
    assert (editorial / "panel_result.v1.json").is_file()
    assert len(rendered) == 1
    assert summary["panel_status"] == "converged"


@pytest.mark.parametrize(
    ("status", "blockers", "accepted", "instructions", "message"),
    [
        ("blocked", ["missing evidence"], [], [], "blockers"),
        (
            "needs_revision",
            [],
            ["ig-01"],
            ["tighten the hook"],
            "has not converged",
        ),
    ],
)
def test_runner_fails_closed_until_panel_converges(
    tmp_path,
    monkeypatch,
    status,
    blockers,
    accepted,
    instructions,
    message,
):
    episode_dir, template_dir, spec = _inputs(tmp_path)
    finding = PanelFinding(
        finding_id="ig-01",
        severity="medium",
        page_id="hook",
        claim="Hook payoff is vague.",
        page_copy_quote="先看演算法藏起了什麼。",
        evidence_ids=["B0002"],
        suggested_change="Make the payoff concrete.",
    )
    reviews = _pass_reviews()
    verified = []
    rejected = []
    if accepted:
        reviews["ig_audience"] = PanelReview(
            lens="ig_audience", verdict="revise", findings=[finding]
        )
        verified = [finding]
    panel = PanelResult(
        episode_id="ep120",
        revision="r001",
        status=status,
        reviews=reviews,
        verified_findings=verified,
        verification_rejections=[],
        synthesis=PanelSynthesis(
            accepted_finding_ids=accepted,
            rejected=rejected,
            revision_instructions=instructions,
            blockers=blockers,
        ),
    )
    spec_path, panel_path = _write_artifacts(tmp_path, spec, panel)
    monkeypatch.setattr(
        run_podcast_carousel,
        "render_carousel",
        lambda **_kwargs: pytest.fail("blocked panel must not render"),
    )

    with pytest.raises(RuntimeError, match=message):
        run_podcast_carousel.run(
            _args(episode_dir, template_dir, spec_path, panel_path)
        )
