from __future__ import annotations

from agents.brook.podcast_subtitles.display_metrics import (
    display_metrics_identity,
    grapheme_clusters,
    measure_text,
)


def test_cjk_latin_and_space_have_distinct_explicit_weights() -> None:
    measured = measure_text("安A B")

    assert measured.grapheme_count == 4
    assert measured.display_columns == 5.0
    assert measured.reading_units == 2.25


def test_combining_sequence_is_one_grapheme_and_one_column() -> None:
    measured = measure_text("e\u0301")

    assert grapheme_clusters("e\u0301") == ("e\u0301",)
    assert measured.grapheme_count == 1
    assert measured.display_columns == 1.0
    assert measured.reading_units == 0.5


def test_emoji_clusters_are_not_counted_as_raw_unicode_scalars() -> None:
    family = "👩\u200d👩\u200d👧\u200d👦"
    flag = "🇹🇼"
    keycap = "1️⃣"

    assert grapheme_clusters(family) == (family,)
    assert grapheme_clusters(flag) == (flag,)
    assert grapheme_clusters(keycap) == (keycap,)
    assert measure_text(family + flag + keycap).display_columns == 6.0
    assert measure_text(family + flag + keycap).reading_units == 3.0


def test_metric_identity_pins_unicode_runtime_and_denies_pixel_exact_claim() -> None:
    identity = display_metrics_identity()

    assert identity.algorithm == "nakama-unicode-display-metrics"
    assert identity.algorithm_version == 1
    assert identity.unicode_version
    assert identity.python_version
    assert identity.shaping_backend == "none-not-pixel-exact"
    assert len(identity.content_hash) == 64
