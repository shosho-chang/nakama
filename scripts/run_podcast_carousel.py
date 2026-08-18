"""Run the evidence-backed Podcast Carousel editorial and render pipeline."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.brook.podcast_carousel_copy import build_transcript_index, generate_copy_spec
from agents.brook.podcast_carousel_panel import run_panel
from agents.brook.podcast_carousel_render import render_carousel
from shared.schemas.podcast_carousel import EpisodeMetadata, PodcastCarouselCopySpecV1

DEFAULT_TEMPLATE = Path(
    r"E:\Company\02_品牌資源_BrandAssets\Shosho Abnormal Universe Design System"
    r"\templates\ig-carousel-episode"
)


def _load_environment(repo_root: Path) -> None:
    configured = os.environ.get("NAKAMA_ENV_FILE", "").strip()
    candidates = [Path(configured)] if configured else []
    candidates.extend([repo_root / ".env", repo_root.parents[1] / ".env"])
    for candidate in candidates:
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(value, "model_dump_json"):
        payload = value.model_dump_json(indent=2)
    else:
        payload = json.dumps(value, ensure_ascii=False, indent=2)
    path.write_text(payload + "\n", encoding="utf-8", newline="\n")


def _revision(number: int) -> str:
    return f"r{number:03d}"


def _panel_revision_payload(panel) -> list[dict]:
    accepted = set(panel.synthesis.accepted_finding_ids)
    findings = [
        finding.model_dump(mode="json")
        for finding in panel.verified_findings
        if finding.finding_id in accepted
    ]
    findings.append(
        {
            "main_editor_revision_instructions": panel.synthesis.revision_instructions,
        }
    )
    return findings


def run(args: argparse.Namespace) -> dict:
    episode_dir = args.episode_dir.resolve()
    prose_path = episode_dir / "transcript_prose.md"
    srt_path = episode_dir / "transcript.srt"
    cutouts_dir = episode_dir / "packaging" / "cutouts"
    for path in (episode_dir, prose_path, srt_path, cutouts_dir, args.template_dir):
        if not path.exists():
            raise FileNotFoundError(f"required Podcast Carousel input missing: {path}")
    package_root = episode_dir / "ig-carousel"
    if (package_root / "current.json").exists() and not args.force:
        raise FileExistsError("ig-carousel/current.json already exists; pass --force to rerun")

    transcript = build_transcript_index(prose_path, srt_path)
    cutouts = sorted(path.name for path in cutouts_dir.glob("*.png"))
    if not cutouts:
        raise FileNotFoundError(f"no PNG cutouts found: {cutouts_dir}")
    social_brief = episode_dir / "social_brief.md"
    editorial_direction = (
        social_brief.read_text(encoding="utf-8") if social_brief.is_file() else None
    )
    episode = EpisodeMetadata(
        number=args.episode_number,
        topic=args.initial_topic,
        guest_name=args.guest_name,
        guest_title=args.guest_title,
    )
    episode_id = args.episode_id or f"ep{args.episode_number}"
    prior: PodcastCarouselCopySpecV1 | None = None
    revision_findings: list[dict] | None = None
    accepted_rounds = 0
    final_panel = None

    for revision_number in range(1, args.max_rounds + 1):
        revision = _revision(revision_number)
        spec = generate_copy_spec(
            transcript=transcript,
            episode_id=episode_id,
            episode=episode,
            host=args.host_name,
            cutouts=cutouts,
            revision=revision,
            editorial_direction=editorial_direction,
            editorial_direction_path=str(social_brief.resolve())
            if social_brief.is_file()
            else None,
            prior_spec=prior,
            revision_findings=revision_findings,
        )
        editorial_dir = package_root / "editorial" / revision
        _write_json(editorial_dir / "copy_spec.v1.json", spec)
        panel = run_panel(spec=spec, transcript=transcript)
        _write_json(editorial_dir / "panel_result.v1.json", panel)
        final_panel = panel
        if panel.synthesis.blockers:
            raise RuntimeError(f"editorial panel blockers: {panel.synthesis.blockers}")
        if not panel.synthesis.accepted_finding_ids:
            break
        accepted_rounds += 1
        if revision_number == args.max_rounds:
            raise RuntimeError("editorial panel did not converge; no carousel was rendered")
        prior = spec
        revision_findings = _panel_revision_payload(panel)
    else:  # pragma: no cover - loop always breaks or raises
        raise RuntimeError("Podcast Carousel editorial loop ended unexpectedly")

    render_carousel(
        spec=spec,
        package_root=package_root,
        template_dir=args.template_dir,
        cutouts_dir=cutouts_dir,
    )
    summary = {
        "episode_id": episode_id,
        "revision": spec.revision,
        "page_count": len(spec.pages),
        "publish_compatibility": spec.publish_compatibility,
        "accepted_revision_rounds": accepted_rounds,
        "final_panel_findings": len(final_panel.verified_findings) if final_panel else 0,
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
    parser.add_argument("--episode-number", type=int, required=True)
    parser.add_argument("--episode-id")
    parser.add_argument("--guest-name", required=True)
    parser.add_argument("--guest-title", required=True)
    parser.add_argument("--host-name", default="張修修")
    parser.add_argument("--initial-topic", default="Podcast 訪談重點")
    parser.add_argument("--template-dir", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--max-rounds", type=int, default=3, choices=range(1, 5))
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    repository = Path(__file__).resolve().parents[1]
    _load_environment(repository)
    print(json.dumps(run(parse_args()), ensure_ascii=False, indent=2))
