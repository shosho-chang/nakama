from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from agents.brook.podcast_carousel_render import render_carousel, snapshot_template
from shared.schemas.podcast_carousel import PodcastCarouselCopySpecV1, receipt_for

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
html,body{margin:0;width:1080px;height:1350px;overflow:hidden}
#canvas{width:1080px;height:1350px;background:#f1eee8;color:#111;font:72px sans-serif}
</style></head><body><div id="canvas"></div><script>
const spec=/*__CAROUSEL_SPEC__*/null;
const assets=/*__CAROUSEL_ASSETS__*/null;
const index=Number(new URLSearchParams(location.search).get('page')||0);
document.querySelector('#canvas').textContent=spec.pages[index].role+' '+Object.keys(assets).length;
document.body.dataset.fitDiagnostics=JSON.stringify({status:'fit',regions:{headline:72},notes:[]});
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
            "emphasis": "mostly sees",
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
            "engagement_question": "Which failure changed your process?",
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
            assert image.size == (1080, 1350)
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
