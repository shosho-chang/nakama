"""Validate reviewed Podcast Carousel artifacts and render them deterministically."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.brook.podcast_carousel_copy import TranscriptIndex, build_transcript_index
from agents.brook.podcast_carousel_panel import PanelResult, assert_panel_renderable
from agents.brook.podcast_carousel_render import render_carousel
from shared.episode_transcript import resolve_transcript_srt
from shared.schemas.podcast_carousel import PodcastCarouselCopySpecV1, TranscriptEvidence

DEFAULT_TEMPLATE = Path(
    r"E:\Company\02_品牌資源_BrandAssets\Shosho Abnormal Universe Design System"
    r"\templates\ig-carousel-episode"
)

ModelT = TypeVar("ModelT", bound=BaseModel)


def _load_model(path: Path, model: type[ModelT]) -> ModelT:
    if not path.is_file():
        raise FileNotFoundError(f"required Podcast Carousel artifact missing: {path}")
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(value, "model_dump_json"):
        payload = value.model_dump_json(indent=2)
    else:
        payload = json.dumps(value, ensure_ascii=False, indent=2)
    path.write_text(payload + "\n", encoding="utf-8", newline="\n")


def _all_evidence(spec: PodcastCarouselCopySpecV1) -> list[TranscriptEvidence]:
    values: list[TranscriptEvidence] = []
    for page in spec.pages:
        values.extend(page.evidence)
        values.extend(getattr(page, "host_question_evidence", []))
    return values


def _validate_spec_evidence(
    spec: PodcastCarouselCopySpecV1,
    transcript: TranscriptIndex,
) -> None:
    """Confirm every materialised excerpt still exactly matches local transcript evidence."""

    for evidence in _all_evidence(spec):
        try:
            expected = transcript.evidence([evidence.evidence_id])[0]
        except ValueError as exc:
            raise ValueError(
                f"Copy Spec references unknown transcript evidence: {evidence.evidence_id}"
            ) from exc
        if evidence != expected:
            raise ValueError(
                f"Copy Spec evidence does not match transcript: {evidence.evidence_id}"
            )


def run(args: argparse.Namespace) -> dict:
    episode_dir = args.episode_dir.resolve()
    prose_path = episode_dir / "transcript_prose.md"
    # 逐字稿來源由 shared.episode_transcript 決定：有 Editorial Master 就用它。
    # 寫死 episode/transcript.srt 會讓 ADR-064 之後的集數引用到**已經剪掉**的內容
    # ——輪播的每一句都要能對回實際播出的節目。
    srt_path = resolve_transcript_srt(episode_dir).srt_path
    cutouts_dir = episode_dir / "packaging" / "cutouts"
    for path in (episode_dir, prose_path, srt_path, cutouts_dir, args.template_dir):
        if not path.exists():
            raise FileNotFoundError(f"required Podcast Carousel input missing: {path}")

    spec = _load_model(args.copy_spec.resolve(), PodcastCarouselCopySpecV1)
    panel = _load_model(args.panel_result.resolve(), PanelResult)
    transcript = build_transcript_index(prose_path, srt_path)
    _validate_spec_evidence(spec, transcript)
    assert_panel_renderable(panel, spec=spec)

    cutouts = sorted(cutouts_dir.glob("*.png"))
    if not cutouts:
        raise FileNotFoundError(f"no PNG cutouts found: {cutouts_dir}")

    package_root = episode_dir / "ig-carousel"
    if (package_root / "current.json").exists() and not args.force:
        raise FileExistsError("ig-carousel/current.json already exists; pass --force to rerun")

    editorial_dir = package_root / "editorial" / spec.revision
    _write_json(editorial_dir / "copy_spec.v1.json", spec)
    _write_json(editorial_dir / "panel_result.v1.json", panel)

    render_carousel(
        spec=spec,
        package_root=package_root,
        template_dir=args.template_dir,
        cutouts_dir=cutouts_dir,
    )
    summary = {
        "episode_id": spec.episode_id,
        "revision": spec.revision,
        "page_count": len(spec.pages),
        "publish_compatibility": spec.publish_compatibility,
        "panel_status": panel.status,
        "final_panel_findings": len(panel.verified_findings),
        "review_route": f"/bridge/ig-cards/{episode_dir.name}",
        "manifest": str(
            (package_root / "revisions" / spec.revision / "review_manifest.v1.json").resolve()
        ),
    }
    _write_json(package_root / "run_summary.json", summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episode_dir", type=Path)
    parser.add_argument("--copy-spec", type=Path, required=True)
    parser.add_argument("--panel-result", type=Path, required=True)
    parser.add_argument("--template-dir", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(run(parse_args()), ensure_ascii=False, indent=2))
