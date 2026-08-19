from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = (ROOT / "thousand_sunny/templates/bridge/carousel_publish.html").read_text(
    encoding="utf-8"
)
REVIEW_TEMPLATE = (ROOT / "thousand_sunny/templates/bridge/carousel_review.html").read_text(
    encoding="utf-8"
)
CSS = (ROOT / "thousand_sunny/static/shosho/carousel-publish.css").read_text(encoding="utf-8")


def test_publish_page_is_explicit_stage6_not_approve_side_effect() -> None:
    assert "stage 6" in TEMPLATE.lower()
    assert "不會立即發布" in TEMPLATE
    assert 'action="/bridge/ig-cards/{{ episode_slug }}/publish/jobs"' in TEMPLATE
    assert "payload.publish_url" in REVIEW_TEMPLATE
    assert "window.location.assign(sameOriginUrl(payload.publish_url))" in REVIEW_TEMPLATE


def test_publish_page_shows_square_assets_caption_and_capabilities() -> None:
    assert "aspect-ratio:1/1" in CSS
    assert 'name="caption"' in TEMPLATE
    assert 'name="platforms"' in TEMPLATE
    assert "capability.strategy" in TEMPLATE
    assert "capability.required_executor_capabilities" in TEMPLATE
    assert "1080 × 1080" in TEMPLATE
    assert "var(--sho-font-zh)" in CSS
    assert "#ff" not in CSS.lower()


def test_publish_job_polls_and_recovers_after_refresh() -> None:
    assert "sessionStorage.setItem" in TEMPLATE
    assert "publish-latest-job" in TEMPLATE
    assert "storageGet(jobKey)" in TEMPLATE
    assert "pollJob(resumable)" in TEMPLATE
    for state in ("loading", "queued", "running", "completed", "failed", "error"):
        assert f"'{state}'" in TEMPLATE
    assert "payload.results" in TEMPLATE
    assert "result.permalink" in TEMPLATE
    assert 'aria-live="polite"' in TEMPLATE
    assert "@media (prefers-reduced-motion:reduce)" in CSS
