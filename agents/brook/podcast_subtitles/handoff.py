"""Read-only Stage 4 → Stage 5 Verified Projection composition seam."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .composition import FactoryContextV1, build_factory_context
from .module import PodcastSubtitleV2, VerifiedProjection

ProjectionVerifierFactory = Callable[[FactoryContextV1], PodcastSubtitleV2]


def open_verified_projection(
    *,
    episode_root: str | Path,
    projection_id: str,
    expected_episode_id: str,
    expected_generation_id: str,
    expected_manifest_sha256: str,
    reference_manifest: str | Path | None = None,
    factory: ProjectionVerifierFactory | None = None,
) -> VerifiedProjection:
    """Build a fresh verifier and return exact bytes from one verified projection.

    The default composition root constructs provider Adapters but this operation
    invokes only their local replay/verification seams.  It never normalizes,
    recognizes, corrects, audio-audits, or projects new content.

    ``factory`` is an explicit trusted test/composition seam.  It is a callable,
    never an import string supplied by episode data.
    """

    context = build_factory_context(
        episode_root=Path(episode_root).resolve(),
        episode_id=expected_episode_id,
        reference_manifest=reference_manifest,
    )
    if factory is None:
        from .production import build_production

        factory = build_production
    module = factory(context)
    if not isinstance(module, PodcastSubtitleV2):
        raise TypeError("Verified Projection factory must return PodcastSubtitleV2")
    if context.reference_bundle is not None:
        context.reference_bundle.assert_module_binding(module)
    return module.load_verified_projection(
        projection_id,
        expected_episode_id=expected_episode_id,
        expected_generation_id=expected_generation_id,
        expected_manifest_sha256=expected_manifest_sha256,
    )


__all__ = ["ProjectionVerifierFactory", "open_verified_projection"]
