from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from agents.brook.podcast_carousel_render import (
    _content_sha,
    _write_render_input,
    render_carousel,
    snapshot_template,
)
from shared.schemas.podcast_carousel import (
    CarouselLayoutOverridesV1,
    CoverLayoutOverride,
    PageTextLayoutOverrideV1,
    PodcastCarouselCopySpecV1,
    TextLayoutOverrideV1,
    receipt_for,
)

CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
DESIGN_TEMPLATE = Path(
    r"E:\Company\02_品牌資源_BrandAssets\Shosho Abnormal Universe Design System"
    r"\templates\ig-carousel-episode"
)
SHA = "a" * 64


def _template(tmp_path: Path) -> Path:
    root = tmp_path / "design"
    template = root / "templates" / "ig-carousel-episode"
    template.mkdir(parents=True)
    for relative in (
        "assets/logo/face-mark-black.png",
        "assets/logo/podcast-cover-white-on-orange.png",
        "assets/patterns/shards-orange-on-gray.png",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (8, 8), "#F04F23").save(target)
    (template / "PodcastCarouselRender.html").write_text(
        """<!doctype html>
<html><head><!--__BASE_HREF__--><style>
html,body{margin:0;width:1080px;height:1080px;overflow:hidden}
#canvas{width:1080px;height:1080px;background:#f1eee8;color:#111;font:72px sans-serif}
</style></head><body><div id="canvas"></div><script>
const spec=/*__CAROUSEL_SPEC__*/null;
const assets=/*__CAROUSEL_ASSETS__*/null;
const index=Number(new URLSearchParams(location.search).get('page')||0);
document.querySelector('#canvas').textContent=spec.pages[index].role+' '+Object.keys(assets).length;
const fitTargets=[{
  node:document.querySelector('#canvas'),region:'headline',start:72,
  absoluteMin:40,minReadable:48,lineHeight:1
}];
const fit=()=>{
  const diagnostics={status:'fit',regions:{},notes:[]};
  for(const item of fitTargets){
    let size=item.start;
    item.node.style.fontSize=`${size}px`;
    diagnostics.regions[item.region]=size;
  }
  document.body.dataset.fitDiagnostics=JSON.stringify(diagnostics);
  document.body.dataset.ready="1";
};
fit();
</script></body></html>""",
        encoding="utf-8",
    )
    return template


def _spec() -> PodcastCarouselCopySpecV1:
    evidence = {
        "evidence_id": "ev-1",
        "source_path": "transcript_prose.md",
        "source_sha256": SHA,
        "speaker": "guest",
        "text": "source excerpt",
        "t0": 1,
        "t1": 2,
    }
    pages = [
        {
            "role": "cover",
            "page_id": "cover",
            "headline": "A useful failure",
            "emphasis": "failure",
            "guest_name": "Guest",
            "guest_title": "Researcher",
            "cutout": "guest.png",
            "evidence": [evidence],
        },
        {
            "role": "hook",
            "page_id": "hook",
            "question": "Why do we only see success?",
            "emphasis": "only see success",
            "bridge": "Look behind the selection process.",
            "evidence": [evidence],
        },
        {
            "role": "point",
            "page_id": "point-one",
            "headline": "Algorithms hide weak attempts",
            "emphasis": "hide weak attempts",
            "body": "The audience mostly sees work that survives.",
            "evidence": [evidence],
        },
        {
            "role": "quote",
            "page_id": "quote",
            "variant": "B",
            "text": "People see the part that worked.",
            "emphasis": "part that worked",
            "guest_name": "Guest",
            "guest_cutout": "guest.png",
            "host_question": "How do you stay consistent?",
            "host_question_evidence": [{**evidence, "evidence_id": "ev-host", "speaker": "host"}],
            "host_cutout": "host.png",
            "evidence": [evidence],
        },
        {
            "role": "cta",
            "page_id": "cta",
            "episode_topic": "The failures we do not see",
            "emphasis": "failures we do not see",
            "evidence": [evidence],
        },
    ]
    return PodcastCarouselCopySpecV1.model_validate(
        {
            "episode_id": "ep120",
            "revision": "r001",
            "episode": {
                "number": 120,
                "topic": "Creative consistency",
                "guest_name": "Guest",
                "guest_title": "Researcher",
            },
            "pages": pages,
            "publish_compatibility": "api_compatible",
        }
    )


def test_template_snapshot_is_content_addressed(tmp_path: Path):
    template = _template(tmp_path)
    first = snapshot_template(template, tmp_path / "package")
    second = snapshot_template(template, tmp_path / "package")
    assert first == second
    assert Path(first.root, "PodcastCarouselRender.html").is_file()


def test_cover_layout_override_is_injected_and_changes_deterministic_content_hash(tmp_path: Path):
    template = _template(tmp_path)
    package = tmp_path / "package"
    snapshot = snapshot_template(template, package)
    cutouts = tmp_path / "cutouts"
    cutouts.mkdir()
    for name in ("guest.png", "host.png"):
        Image.new("RGBA", (48, 48), "#00000000").save(cutouts / name)
    original = _spec()
    edited = original.model_copy(
        update={
            "layout_overrides": CarouselLayoutOverridesV1(
                cover=CoverLayoutOverride(
                    guest_right_px=-180,
                    guest_bottom_px=-90,
                    guest_height_px=980,
                    title_font_size_px=112,
                )
            )
        }
    )
    destination = tmp_path / "render_input.html"
    _write_render_input(
        snapshot=snapshot,
        spec=edited,
        cutouts_dir=cutouts,
        destination=destination,
    )
    source = destination.read_text(encoding="utf-8")
    assert "data-carousel-layout-overrides" in source
    assert "right:-180px!important" in source
    assert "--type-cover-title:112px" in source
    assert "window.__carouselRefit=()=>" in source
    assert "item.node.dataset.fitStart" in source
    assert "return diagnostics" in source
    assert _content_sha(original, 0, snapshot.sha256, cutouts) != _content_sha(
        edited, 0, snapshot.sha256, cutouts
    )


@pytest.mark.skipif(not DESIGN_TEMPLATE.is_dir(), reason="canonical design template required")
def test_text_layout_uses_canonical_editor_patch_and_locks_user_font_size(tmp_path: Path):
    snapshot = snapshot_template(DESIGN_TEMPLATE, tmp_path / "package")
    cutouts = tmp_path / "cutouts"
    cutouts.mkdir()
    for name in ("guest.png", "host.png"):
        Image.new("RGBA", (48, 48), "#00000000").save(cutouts / name)
    original = _spec()
    edited = original.model_copy(
        update={
            "layout_overrides": CarouselLayoutOverridesV1(
                text_regions=[
                    PageTextLayoutOverrideV1(
                        page_id="hook",
                        role="hook",
                        region="question",
                        values=TextLayoutOverrideV1(
                            x_px=64,
                            y_px=240,
                            width_px=880,
                            font_start_px=104,
                            lines=["Why do we ", "only see success?"],
                        ),
                    ),
                ]
            ),
        }
    )
    destination = tmp_path / "render_input.html"
    _write_render_input(
        snapshot=snapshot,
        spec=edited,
        cutouts_dir=cutouts,
        destination=destination,
    )
    source = destination.read_text(encoding="utf-8")
    assert "window.applyEditorPatch=" in source
    assert 'item.node.dataset.fitLocked!=="true"' in source
    assert "applyManualLines" in source
    assert "font_start_px" in source
    assert "editor text regions overlap" in source
    assert "editorProtectedCollisions" in source
    assert "editor text/protected collision" in source
    assert "editor text containment failure" in source
    assert '".podcast-cover"' in source
    assert '".search"' in source
    assert '".platforms"' in source
    assert '".bubble"' in source
    assert '".quote-a .guest"' in source
    assert "editorRect.right>editorSafeRight+overflowTolerance" in source
    assert "editorRect.bottom>editorSafeBottom+overflowTolerance" in source
    assert _content_sha(original, 1, snapshot.sha256, cutouts) != _content_sha(
        edited, 1, snapshot.sha256, cutouts
    )


def test_design_system_cover_and_cta_use_reviewed_visual_contract():
    source = (DESIGN_TEMPLATE / "PodcastCarouselRender.html").read_text(encoding="utf-8")

    assert ".cover .em-orange{display:inline-block;color:var(--white)" in source
    assert "cover-headline-zone" in source
    assert ".cta-title .em-orange{color:var(--white)" in source
    assert "richWithPreferredBreak(title,page.episode_topic,page.emphasis" in source
    assert 'createElementNS("http://www.w3.org/2000/svg","svg")' in source
    assert 'aria-label",name' in source
    assert '[["AP","Apple Podcasts"],["S","Spotify"],["YT","YouTube"]]' not in source
    assert "cover headline/cutout overlap" in source
    assert "--cover-cutout-overlap-limit:240px" in source
    assert "right:-260px;bottom:-130px;height:900px;width:auto;z-index:4" in source
    assert "--type-cover-title:106px" in source
    assert "richCover(title,page.headline,page.emphasis)" in source
    assert 'text.slice(0,emphasisAt)),el("br")' in source
    assert "--type-hook-title:104px" in source
    assert "--hook-optical-lift:-22px" in source
    assert "--type-point-title:72px" in source
    assert "--type-point-body:44px" in source
    assert "--point-optical-lift:-20px" in source
    assert "--cta-logo-top:185px" in source
    assert ".point .ghost-number{position:absolute;right:-40px;top:-70px" in source
    assert "transform:translateY(var(--point-optical-lift))" in source
    assert "#canvas.hook{background:var(--orange)}" in source
    assert ".hook .em-box{color:var(--ink);background:var(--white);border:0" in source
    assert "border:0;display:inline-block" in source
    assert "display:inline-block;white-space:nowrap" in source
    assert "padding:12px 18px;line-height:1" in source
    assert "transform:rotate(var(--emphasis-tilt,-1.5deg))" in source
    assert 'canvas.style.setProperty("--emphasis-tilt",`${emphasisTilt}deg`)' in source
    assert "--cover-emphasis-gap:18px" in source
    assert "--cover-emphasis-pad-top:10px" in source
    assert "--cover-emphasis-pad-bottom:22px" in source
    assert "--cover-emphasis-pad-x:24px" in source
    assert 'target(title,"cover.headline",typeSize("--type-cover-title"),104,92,1.05)' in source


@pytest.mark.skipif(not CHROME.is_file(), reason="system Chrome required")
def test_render_outputs_exact_images_and_reuses_unchanged_pages(tmp_path: Path):
    template = _template(tmp_path)
    cutouts = tmp_path / "cutouts"
    cutouts.mkdir()
    for name in ("guest.png", "host.png"):
        Image.new("RGBA", (48, 48), "#00000000").save(cutouts / name)
    package = tmp_path / "ig-carousel"

    first = render_carousel(
        spec=_spec(),
        package_root=package,
        template_dir=template,
        cutouts_dir=cutouts,
        chrome=CHROME,
    )
    mtimes = {page.page_id: Path(page.image.path).stat().st_mtime_ns for page in first.pages}
    second = render_carousel(
        spec=_spec(),
        package_root=package,
        template_dir=template,
        cutouts_dir=cutouts,
        chrome=CHROME,
    )

    assert len(first.pages) == 5
    assert first == second
    assert all(page.fit.status == "fit" for page in first.pages)
    for page in second.pages:
        image_path = Path(page.image.path)
        assert receipt_for(image_path) == page.image
        assert image_path.stat().st_mtime_ns == mtimes[page.page_id]
        with Image.open(image_path) as image:
            assert image.size == (1080, 1080)
    assert receipt_for(package / "revisions" / "r001" / "render_input.html") == second.render_input
    current = json.loads((package / "current.json").read_text(encoding="utf-8"))
    assert current["revision"] == "r001"


@pytest.mark.skipif(
    not CHROME.is_file() or not DESIGN_TEMPLATE.is_dir(),
    reason="system Chrome and local Design System required",
)
def test_real_design_system_template_renders_every_page_role(tmp_path: Path):
    cutouts = tmp_path / "cutouts"
    cutouts.mkdir()
    Image.new("RGBA", (600, 900), "#00000000").save(cutouts / "guest.png")
    Image.new("RGBA", (600, 900), "#00000000").save(cutouts / "host.png")
    package = tmp_path / "design-system-smoke"

    manifest = render_carousel(
        spec=_spec(),
        package_root=package,
        template_dir=DESIGN_TEMPLATE,
        cutouts_dir=cutouts,
        chrome=CHROME,
    )

    assert [page.role for page in manifest.pages] == ["cover", "hook", "point", "quote", "cta"]
    assert all(Path(page.image.path).is_file() for page in manifest.pages)
    assert all(page.fit.status == "fit" for page in manifest.pages)
    assert manifest.pages[0].fit.regions["cover.cutout_height"] >= 880
    assert manifest.pages[0].fit.regions["cover.headline_cutout_overlap_y"] <= 240
    assert (
        manifest.pages[0].fit.regions["cover.headline"]
        > manifest.pages[2].fit.regions["point.headline"]
    )
    assert manifest.pages[0].fit.regions["cover.emphasis_gap"] >= 12
    assert manifest.pages[0].fit.regions["cover.emphasis_padding_x"] >= 20
    assert manifest.pages[0].fit.regions["cover.emphasis_padding_top"] >= 10
    assert manifest.pages[0].fit.regions["cover.emphasis_padding_top"] == 10
    assert manifest.pages[0].fit.regions["cover.emphasis_padding_bottom"] == 22
    assert (
        manifest.pages[0].fit.regions["cover.emphasis_padding_left"]
        == manifest.pages[0].fit.regions["cover.emphasis_padding_right"]
    )
    assert manifest.pages[1].fit.regions["hook.background_is_orange"] == 1
    assert manifest.pages[1].fit.regions["hook.emphasis_fill_is_white"] == 1
    assert manifest.pages[1].fit.regions["hook.emphasis_lines"] == 1
    assert manifest.pages[1].fit.regions["hook.content_top"] <= 464
    assert manifest.pages[2].fit.regions["emphasis.rotation_deg"] == -1.5
    assert manifest.pages[2].fit.regions["point.body"] >= 40
    assert manifest.pages[2].fit.regions["point.emphasis_lines"] == 1
    assert manifest.pages[2].fit.regions["point.emphasis_padding_top"] == 12
    assert manifest.pages[2].fit.regions["point.emphasis_padding_bottom"] == 12
    assert manifest.pages[2].fit.regions["point.emphasis_padding_left"] == 18
    assert manifest.pages[2].fit.regions["point.emphasis_padding_right"] == 18
    assert manifest.pages[2].fit.regions["point.content_top"] <= 380
    assert manifest.pages[3].fit.regions["quote.answer_cutout_overlap"] == 0
    assert manifest.pages[3].fit.regions["quote.question_divider_gap"] >= 24
    assert manifest.pages[4].fit.regions["cta.logo_gap_delta"] <= 16
    render_input = (package / "revisions" / "r001" / "render_input.html").read_text(
        encoding="utf-8"
    )
    assert "留言告訴我" not in render_input
    assert ".engagement" not in render_input
    with Image.open(manifest.pages[-1].image.path).convert("RGB") as cta:
        assert cta.getpixel((12, 1068)) == (40, 37, 37)


@pytest.mark.skipif(
    not CHROME.is_file() or not DESIGN_TEMPLATE.is_dir(),
    reason="system Chrome and local Design System required",
)
def test_cover_editor_canonical_baseline_remains_fit(tmp_path: Path):
    cutouts = tmp_path / "cutouts"
    cutouts.mkdir()
    Image.new("RGBA", (600, 900), "#00000000").save(cutouts / "guest.png")
    Image.new("RGBA", (600, 900), "#00000000").save(cutouts / "host.png")
    payload = _spec().model_dump(mode="json")
    payload["pages"][0]["headline"] = "內容爆量，泛科學怎麼活？"
    payload["pages"][0]["emphasis"] = "怎麼活"
    payload["layout_overrides"]["text_regions"] = [
        {
            "page_id": "cover",
            "role": "cover",
            "region": "headline",
            "values": {
                "x_px": 64,
                "y_px": 168,
                "width_px": 976,
                "font_start_px": 106,
                "lines": None,
            },
        }
    ]
    spec = PodcastCarouselCopySpecV1.model_validate(payload)

    manifest = render_carousel(
        spec=spec,
        package_root=tmp_path / "canonical-cover-baseline",
        template_dir=DESIGN_TEMPLATE,
        cutouts_dir=cutouts,
        chrome=CHROME,
    )

    cover = manifest.pages[0].fit
    assert cover.status == "fit", cover.notes
    assert not any("cover.headline exceeds its editor safe region" in note for note in cover.notes)
    for dimension in (
        "scroll_overflow_x",
        "scroll_overflow_y",
        "left_overflow",
        "top_overflow",
        "right_overflow",
        "bottom_overflow",
    ):
        assert cover.regions[f"cover.headline.editor_{dimension}"] <= 24
    assert cover.regions["cover.headline_cutout_overlap_y"] <= 240


@pytest.mark.skipif(
    not CHROME.is_file() or not DESIGN_TEMPLATE.is_dir(),
    reason="system Chrome and local Design System required",
)
def test_quote_a_text_cannot_collide_with_protected_guest_or_escape_bubble(tmp_path: Path):
    cutouts = tmp_path / "cutouts"
    cutouts.mkdir()
    Image.new("RGBA", (600, 900), "#00000000").save(cutouts / "guest.png")
    Image.new("RGBA", (600, 900), "#00000000").save(cutouts / "host.png")
    payload = _spec().model_dump(mode="json")
    payload["episode"]["number"] = 121
    quote_page = next(page for page in payload["pages"] if page["page_id"] == "quote")
    quote_page["variant"] = "A"
    quote_page["host_question"] = None
    quote_page["host_question_evidence"] = []
    quote_page["host_cutout"] = None
    payload["layout_overrides"]["text_regions"] = [
        {
            "page_id": "quote",
            "role": "quote",
            "region": "text",
            "values": {
                "x_px": 600,
                "y_px": 640,
                "width_px": 400,
                "font_start_px": 68,
                "lines": None,
            },
        }
    ]
    manifest = render_carousel(
        spec=PodcastCarouselCopySpecV1.model_validate(payload),
        package_root=tmp_path / "protected-quote",
        template_dir=DESIGN_TEMPLATE,
        cutouts_dir=cutouts,
        chrome=CHROME,
    )

    quote = next(page.fit for page in manifest.pages if page.page_id == "quote")
    assert quote.status == "needs_review"
    assert any(
        "editor text/protected collision" in note or "editor text containment failure" in note
        for note in quote.notes
    ), quote.notes
