# Podcast Carousel reviewed-template fixture

`PodcastCarouselRender.html` is a checked-in golden fixture for source-level tests of the
reviewed visual contract. It makes those assertions deterministic on CI hosts that do not
mount the external Shosho Design System.

This fixture is not a production template, runtime fallback, or second authoring source.
Production rendering must still receive the canonical Design System template directory and
create the immutable episode-local Template Snapshot required by ADR-064. Update this golden
only after the canonical template has passed visual review, then keep the source-contract
assertions in `tests/agents/brook/test_podcast_carousel_render.py` aligned with that review.
