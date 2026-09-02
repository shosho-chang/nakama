"""Render Podcast Carousel Copy Specs through the Design System template."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from PIL import Image

from shared.schemas.podcast_carousel import (
    CarouselReviewManifestV1,
    CarouselReviewPage,
    PageFitDiagnostic,
    PodcastCarouselCopySpecV1,
    TemplateSnapshot,
    receipt_for,
)

_RENDER_TEMPLATE = "PodcastCarouselRender.html"
_BUNDLE_ASSETS = (
    "assets/logo/face-mark-black.png",
    "assets/logo/podcast-cover-white-on-orange.png",
    "assets/patterns/shards-orange-on-gray.png",
)
_DEFAULT_CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
_PLAYWRIGHT_HELPER = Path(__file__).with_name("podcast_carousel_render.cjs")


def _digest_files(files: list[tuple[str, Path]]) -> str:
    digest = hashlib.sha256()
    for relative, path in sorted(files):
        digest.update(relative.replace("\\", "/").encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _bundle_sources(template_dir: Path) -> list[tuple[str, Path]]:
    if not (template_dir / _RENDER_TEMPLATE).is_file():
        raise FileNotFoundError(f"Design System render template missing: {_RENDER_TEMPLATE}")
    sources = [
        (path.relative_to(template_dir).as_posix(), path)
        for path in template_dir.iterdir()
        if path.is_file()
    ]
    design_root = template_dir.parents[1]
    for relative in _BUNDLE_ASSETS:
        source = design_root / relative
        if not source.is_file():
            raise FileNotFoundError(f"Design System asset missing: {source}")
        sources.append((relative, source))
    return sources


def snapshot_template(template_dir: Path, package_root: Path) -> TemplateSnapshot:
    """Create one content-addressed, immutable-at-runtime template bundle."""

    sources = _bundle_sources(template_dir)
    digest = _digest_files(sources)
    destination = package_root / "templates" / digest
    if not destination.exists():
        for relative, source in sources:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    snapshot_files = [
        (path.relative_to(destination).as_posix(), path)
        for path in destination.rglob("*")
        if path.is_file()
    ]
    if _digest_files(snapshot_files) != digest:
        raise ValueError("Template Snapshot content does not match its hash")
    return TemplateSnapshot(root=str(destination.resolve()), sha256=digest)


def _safe_script_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace(
        "</script", "<\\/script"
    )


def _data_uri(path: Path) -> str:
    suffix = path.suffix.lower()
    media_type = "image/png" if suffix == ".png" else "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def _layout_override_markup(spec: PodcastCarouselCopySpecV1) -> str:
    cover = spec.layout_overrides.cover
    legacy = ""
    if cover is not None:
        legacy = (
            f":root{{--type-cover-title:{cover.title_font_size_px}px}}\n"
            ".cover .guest{"
            f"right:{cover.guest_right_px}px!important;"
            f"bottom:{cover.guest_bottom_px}px!important;"
            f"height:{cover.guest_height_px}px!important"
            "}\n"
        )
    quote = spec.layout_overrides.quote
    if quote is not None:
        # A 版的去背照直接掛在畫布上，B 版掛在 `.guest-panel` 裡；兩者都是絕對
        # 定位、同一組欄位，所以一條規則同時蓋掉。沒有 override 時完全不出手，
        # 各版型維持自己的算圖預設。
        legacy += (
            ".quote-a .guest,.quote-b .guest-panel img{"
            f"right:{quote.guest_right_px}px!important;"
            f"bottom:{quote.guest_bottom_px}px!important;"
            f"height:{quote.guest_height_px}px!important"
            "}" + chr(10)
        )
    return (
        "<style data-carousel-layout-overrides>\n"
        "[data-fit-region]{white-space:pre-wrap;height:auto}\n"
        f"{legacy}</style>"
    )


_EDITOR_PATCH_SCRIPT = r"""
  const canonicalTextSafeRects={
    "cover.headline":[48,120,1040,760],"hook.question":[48,160,1032,820],
    "hook.bridge":[48,400,1032,980],"point.headline":[48,160,1032,760],
    "point.body":[48,400,1032,980],"quote.text":[48,160,1032,980],
    "quote.host_question":[448,180,992,460],"cta.episode_topic":[48,540,1032,820]
  };
  const canonicalTextLayouts=()=>spec.layout_overrides?.text_regions||[];
  let activeEditorPatch=canonicalTextLayouts();
  const applyManualLines=(region,lines)=>{
    if(!lines)return;
    const node=canvas.querySelector(`[data-fit-region="${page.role}.${region}"]`);
    if(!node)return;
    const mark=node.querySelector(".em-box,.em-orange");
    const emphasis=mark?.textContent||null,kind=mark?.className;
    let emphasisApplied=false;
    node.replaceChildren();
    lines.forEach((line,index)=>{
      if(index)node.append(el("br"));
      const at=emphasis&&!emphasisApplied?line.indexOf(emphasis):-1;
      if(at<0){node.append(document.createTextNode(line));return}
      emphasisApplied=true;
      node.append(document.createTextNode(line.slice(0,at)),el("span",kind,emphasis),document.createTextNode(line.slice(at+emphasis.length)));
    });
  };
  window.applyEditorPatch=(patch=activeEditorPatch)=>{
    activeEditorPatch=patch;
    for(const item of patch.filter(candidate=>candidate.page_id===page.page_id)){
      const node=canvas.querySelector(`[data-fit-region="${item.role}.${item.region}"]`);
      if(!node)continue;
      const value=item.values;
      applyManualLines(item.region,value.lines);
      node.style.width=`${value.width_px}px`;
      node.style.maxWidth="none";node.style.height="auto";node.style.maxHeight="none";
      node.style.fontSize=`${value.font_start_px}px`;
      node.dataset.fitStart=String(value.font_start_px);node.dataset.fitLocked="true";
      const safe=canonicalTextSafeRects[`${item.role}.${item.region}`];
      if(safe){node.dataset.editorSafeLeft=String(safe[0]);node.dataset.editorSafeTop=String(safe[1]);node.dataset.editorSafeRight=String(safe[2]);node.dataset.editorSafeBottom=String(safe[3])}
      node.style.transform="none";
      const rect=node.getBoundingClientRect();
      node.style.transform=`translate(${value.x_px-rect.left}px,${value.y_px-rect.top}px)`;
    }
  };
"""

_EDITOR_PROTECTED_SCRIPT = r"""
    const editorRegionNode=(region)=>
      fitTargets.find(item=>item.region===region)?.node;
    const editorProtectedCollisions=[];
    const editorContainments=[];
    if(page.role==="cover"){
      editorProtectedCollisions.push(
        ["cover.headline",".header",0],
        ["cover.headline",".guest-label",12]
      );
    }
    if(page.role==="hook"){
      editorProtectedCollisions.push(
        ["hook.question",".header",0],
        ["hook.bridge",".arrow",12]
      );
    }
    if(page.role==="point"){
      editorProtectedCollisions.push(
        ["point.headline",".header",0],
        ["point.body",".arrow",12]
      );
    }
    if(page.role==="quote"&&page.variant==="A"){
      editorProtectedCollisions.push(
        ["quote.text",".header",0],
        ["quote.text",".quote-a .guest",12]
      );
      editorContainments.push(["quote.text",".bubble",0]);
    }
    if(page.role==="quote"&&page.variant==="B"){
      editorProtectedCollisions.push(["quote.host_question",".header",0]);
    }
    if(page.role==="cta"){
      editorProtectedCollisions.push(
        ["cta.episode_topic",".header",0],
        ["cta.episode_topic",".podcast-cover",12],
        ["cta.episode_topic",".search",12],
        ["cta.episode_topic",".platforms",12]
      );
      editorContainments.push(["cta.episode_topic",".cta-copy",0]);
    }
    for(const [region,selector,gap] of editorProtectedCollisions){
      const textNode=editorRegionNode(region);
      const protectedNode=canvas.querySelector(selector);
      if(textNode&&protectedNode&&overlap(
        textNode.getBoundingClientRect(),
        protectedNode.getBoundingClientRect(),
        gap
      )){
        diagnostics.status="needs_review";
        diagnostics.notes.push(
          `editor text/protected collision: ${region} / ${selector}`
        );
      }
    }
    for(const [region,selector,inset] of editorContainments){
      const textNode=editorRegionNode(region);
      const protectedNode=canvas.querySelector(selector);
      if(!textNode||!protectedNode)continue;
      const inner=textNode.getBoundingClientRect();
      const outer=protectedNode.getBoundingClientRect();
      const contained=
        inner.left>=outer.left+inset&&inner.top>=outer.top+inset&&
        inner.right<=outer.right-inset&&inner.bottom<=outer.bottom-inset;
      if(!contained){
        diagnostics.status="needs_review";
        diagnostics.notes.push(
          `editor text containment failure: ${region} / ${selector}`
        );
      }
    }
"""


def _write_render_input(
    *,
    snapshot: TemplateSnapshot,
    spec: PodcastCarouselCopySpecV1,
    cutouts_dir: Path,
    destination: Path,
) -> None:
    template_root = Path(snapshot.root)
    source = (template_root / _RENDER_TEMPLATE).read_text(encoding="utf-8")
    if source.count("/*__CAROUSEL_SPEC__*/null") != 1:
        raise ValueError("render template must contain one spec injection marker")
    if source.count("/*__CAROUSEL_ASSETS__*/null") != 1:
        raise ValueError("render template must contain one asset injection marker")
    if source.count("<!--__BASE_HREF__-->") != 1:
        raise ValueError("render template must contain one base-href marker")
    cutout_names = {
        value
        for page in spec.pages
        for value in (
            getattr(page, "cutout", None),
            getattr(page, "guest_cutout", None),
            getattr(page, "host_cutout", None),
        )
        if value
    }
    assets: dict[str, str] = {
        relative: _data_uri(template_root / relative) for relative in _BUNDLE_ASSETS
    }
    for name in cutout_names:
        path = cutouts_dir / name
        if not path.is_file():
            raise FileNotFoundError(f"Carousel cutout missing: {path}")
        assets[name] = _data_uri(path)
    rendered = source.replace(
        "<!--__BASE_HREF__-->",
        f'<base href="{template_root.resolve().as_uri()}/">',
    )
    rendered = rendered.replace(
        "/*__CAROUSEL_SPEC__*/null",
        _safe_script_json(spec.model_dump(mode="json")),
    ).replace("/*__CAROUSEL_ASSETS__*/null", _safe_script_json(assets))
    canonical_editor = "const fitTargets=[]" in rendered
    refit_markers = {
        "const fit=()=>{": (
            f"{_EDITOR_PATCH_SCRIPT}\n  const fit=()=>{{" if canonical_editor else "const fit=()=>{"
        ),
        "let size=item.start;": (
            "let size=item.node.dataset.fitStart?Number(item.node.dataset.fitStart):item.start;"
        ),
        'document.body.dataset.ready="1";': ('document.body.dataset.ready="1";return diagnostics;'),
    }
    for marker, replacement in refit_markers.items():
        if rendered.count(marker) != 1:
            raise ValueError(f"render template must contain one canonical refit marker: {marker}")
        rendered = rendered.replace(marker, replacement, 1)
    rendered = rendered.replace(
        "const fit=()=>{",
        "const fit=window.__carouselRefit=()=>{window.applyEditorPatch?.();",
        1,
    )
    if canonical_editor:
        rendered = rendered.replace(
            "while(size>item.absoluteMin&&",
            'while(item.node.dataset.fitLocked!=="true"&&size>item.absoluteMin&&',
            1,
        )
        rendered = rendered.replace(
            "diagnostics.regions[item.region]=size;if(size<item.minReadable)",
            "diagnostics.regions[item.region]=size;"
            "const editorRect=item.node.getBoundingClientRect(),"
            "editorSafeLeft=Number(item.node.dataset.editorSafeLeft),"
            "editorSafeTop=Number(item.node.dataset.editorSafeTop),"
            "editorSafeRight=Number(item.node.dataset.editorSafeRight),"
            "editorSafeBottom=Number(item.node.dataset.editorSafeBottom);"
            'const editorLocked=item.node.dataset.fitLocked==="true";'
            "if(editorLocked){"
            "diagnostics.regions[`${item.region}.editor_scroll_overflow_x`]="
            "item.node.scrollWidth-item.node.clientWidth;"
            "diagnostics.regions[`${item.region}.editor_scroll_overflow_y`]="
            "item.node.scrollHeight-item.node.clientHeight;"
            "diagnostics.regions[`${item.region}.editor_left_overflow`]="
            "editorSafeLeft-editorRect.left;"
            "diagnostics.regions[`${item.region}.editor_top_overflow`]="
            "editorSafeTop-editorRect.top;"
            "diagnostics.regions[`${item.region}.editor_right_overflow`]="
            "editorRect.right-editorSafeRight;"
            "diagnostics.regions[`${item.region}.editor_bottom_overflow`]="
            "editorRect.bottom-editorSafeBottom;}"
            "const editorOverflow=editorLocked&&("
            "item.node.scrollWidth>item.node.clientWidth+overflowTolerance||"
            "item.node.scrollHeight>item.node.clientHeight+overflowTolerance||"
            "editorRect.left<editorSafeLeft-overflowTolerance||"
            "editorRect.top<editorSafeTop-overflowTolerance||"
            "editorRect.right>editorSafeRight+overflowTolerance||"
            "editorRect.bottom>editorSafeBottom+overflowTolerance);"
            'if(editorOverflow){diagnostics.status="needs_review";'
            "diagnostics.notes.push(`${item.region} exceeds its editor safe region`)}"
            "if(size<item.minReadable)",
            1,
        )
        rendered = rendered.replace(
            "const richCover=(node,text,emphasis)=>{",
            'const richCover=(node,text,emphasis)=>{if(text.includes("\\n"))'
            'return rich(node,text,emphasis,"orange");',
            1,
        )
        rendered = rendered.replace(
            'const breakAt=text.indexOf("，");',
            'const breakAt=text.includes("\\n")?-1:text.indexOf("，");',
            1,
        )
        overlap_marker = (
            "const overlap=(a,b,gap=0)=>a.right+gap>b.left&&a.left<b.right+gap&&"
            "a.bottom+gap>b.top&&a.top<b.bottom+gap;"
        )
        if rendered.count(overlap_marker) != 1:
            raise ValueError("render template must contain one canonical overlap marker")
        rendered = rendered.replace(
            overlap_marker,
            overlap_marker
            + _EDITOR_PROTECTED_SCRIPT
            + "for(let first=0;first<fitTargets.length;first++){"
            + "for(let second=first+1;second<fitTargets.length;second++){"
            + "const a=fitTargets[first],b=fitTargets[second];"
            + "if(overlap(a.node.getBoundingClientRect(),b.node.getBoundingClientRect(),12)){"
            + 'diagnostics.status="needs_review";'
            + "diagnostics.notes.push(`editor text regions overlap: ${a.region} / ${b.region}`)"
            + "}}}",
            1,
        )
    if "</head>" not in rendered:
        raise ValueError("render template must contain a closing head tag")
    rendered = rendered.replace("</head>", f"{_layout_override_markup(spec)}</head>", 1)
    destination.write_text(rendered, encoding="utf-8", newline="\n")


def _playwright_environment() -> tuple[Path, dict[str, str]]:
    node_value = os.environ.get("NAKAMA_NODE") or shutil.which("node")
    if not node_value:
        raise FileNotFoundError("Node.js is required for Podcast Carousel rendering")
    node = Path(node_value)
    env = os.environ.copy()
    configured_modules = os.environ.get("NAKAMA_NODE_PATH") or env.get("NODE_PATH")
    codex_modules = (
        Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"
    )
    if configured_modules:
        env["NODE_PATH"] = configured_modules
    elif codex_modules.is_dir():
        env["NODE_PATH"] = str(codex_modules)
        bundled_node = codex_modules.parent / "bin" / "node.exe"
        if bundled_node.is_file():
            node = bundled_node
    return node, env


def _render_page(*, chrome: Path, url: str, screenshot: Path) -> PageFitDiagnostic:
    if not _PLAYWRIGHT_HELPER.is_file():
        raise FileNotFoundError(f"Playwright render helper missing: {_PLAYWRIGHT_HELPER}")
    node, env = _playwright_environment()
    completed = subprocess.run(
        [str(node), str(_PLAYWRIGHT_HELPER), str(chrome), url, str(screenshot)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Playwright render failed: {completed.stderr[-1200:]}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ValueError("Playwright render helper emitted invalid diagnostics") from error
    return PageFitDiagnostic.model_validate(payload)


def _content_sha(
    spec: PodcastCarouselCopySpecV1,
    page_index: int,
    template_sha: str,
    cutouts_dir: Path,
) -> str:
    page = spec.pages[page_index]
    point_number = sum(value.role == "point" for value in spec.pages[: page_index + 1])
    payload = {
        "template_sha256": template_sha,
        "episode": spec.episode.model_dump(mode="json"),
        "page": page.model_dump(mode="json"),
        "page_number": page_index + 1,
        "page_total": len(spec.pages),
        "point_number": point_number,
        "layout_override": (
            spec.layout_overrides.cover.model_dump(mode="json")
            if page.role == "cover" and spec.layout_overrides.cover is not None
            else None
        ),
        "quote_layout_override": (
            spec.layout_overrides.quote.model_dump(mode="json")
            if page.role == "quote" and spec.layout_overrides.quote is not None
            else None
        ),
        "text_layout_overrides": [
            item.model_dump(mode="json")
            for item in spec.layout_overrides.text_regions
            if item.page_id == page.page_id
        ],
        "cutout_sha256": {
            name: receipt_for(cutouts_dir / name).sha256
            for name in (
                getattr(page, "cutout", None),
                getattr(page, "guest_cutout", None),
                getattr(page, "host_cutout", None),
            )
            if name
        },
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_render_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"pages": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"pages": {}}
    if isinstance(payload, dict) and isinstance(payload.get("pages"), dict):
        return payload
    return {"pages": {}}


def render_carousel(
    *,
    spec: PodcastCarouselCopySpecV1,
    package_root: Path,
    template_dir: Path,
    cutouts_dir: Path,
    chrome: Path = _DEFAULT_CHROME,
) -> CarouselReviewManifestV1:
    """Render or safely reuse every page in one carousel revision."""

    if not chrome.is_file():
        raise FileNotFoundError(f"Chrome executable not found: {chrome}")
    snapshot = snapshot_template(template_dir, package_root)
    revision_dir = package_root / "revisions" / spec.revision
    pages_dir = revision_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    copy_path = revision_dir / "copy_spec.v1.json"
    copy_path.write_text(spec.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    render_input = revision_dir / "render_input.html"
    _write_render_input(
        snapshot=snapshot,
        spec=spec,
        cutouts_dir=cutouts_dir,
        destination=render_input,
    )

    state_path = revision_dir / "render_state.json"
    previous = _load_render_state(state_path).get("pages", {})
    next_state: dict[str, Any] = {"pages": {}}
    review_pages: list[CarouselReviewPage] = []
    for index, page in enumerate(spec.pages):
        content_sha = _content_sha(spec, index, snapshot.sha256, cutouts_dir)
        image_path = pages_dir / f"{index + 1:02d}.png"
        prior = previous.get(page.page_id, {})
        reusable = (
            prior.get("content_sha256") == content_sha
            and image_path.is_file()
            and prior.get("image_sha256") == receipt_for(image_path).sha256
            and isinstance(prior.get("fit"), dict)
        )
        if reusable:
            fit = PageFitDiagnostic.model_validate(prior["fit"])
        else:
            url = f"{render_input.resolve().as_uri()}?{urlencode({'page': index})}"
            fit = _render_page(
                chrome=chrome,
                url=url,
                screenshot=image_path.resolve(),
            )
            with Image.open(image_path) as image:
                if image.size != (1080, 1080):
                    raise ValueError(f"rendered page has wrong dimensions: {image.size}")
        image_receipt = receipt_for(image_path)
        next_state["pages"][page.page_id] = {
            "content_sha256": content_sha,
            "image_sha256": image_receipt.sha256,
            "fit": fit.model_dump(mode="json"),
        }
        review_pages.append(
            CarouselReviewPage(
                page_id=page.page_id,
                page_number=index + 1,
                role=page.role,
                content_sha256=content_sha,
                image=image_receipt,
                fit=fit,
                copy_page=page,
            )
        )
    state_path.write_text(
        json.dumps(next_state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    manifest = CarouselReviewManifestV1(
        episode_id=spec.episode_id,
        revision=spec.revision,
        copy_spec=receipt_for(copy_path),
        render_input=receipt_for(render_input),
        template=snapshot,
        publish_compatibility=spec.publish_compatibility,
        pages=review_pages,
    )
    manifest_path = revision_dir / "review_manifest.v1.json"
    manifest_path.write_text(
        manifest.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (package_root / "current.json").write_text(
        json.dumps(
            {
                "episode_id": spec.episode_id,
                "revision": spec.revision,
                "manifest": str(manifest_path.resolve()),
                "manifest_sha256": receipt_for(manifest_path).sha256,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest
