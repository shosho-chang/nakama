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
    assert 'disabled aria-disabled="true"' in TEMPLATE
    assert "idempotency key" not in TEMPLATE
    assert "TARGETS &amp; CAPABILITIES" not in TEMPLATE
    assert ":checked:not(:disabled)" in TEMPLATE
    assert "capability.eligible and capability.platform in selected_platforms" in TEMPLATE
    assert "!item.disabled && selected.has(item.value)" in TEMPLATE
    assert "browser_session" not in TEMPLATE


def test_publish_page_has_narrow_mobile_overflow_guards() -> None:
    assert "@media (max-width:480px)" in CSS
    assert "grid-template-columns:repeat(3,minmax(0,1fr))" in CSS
    assert "overflow-wrap:anywhere" in CSS
    assert ".publish-platform__name .sho-tag" in CSS


def test_publish_job_polls_and_recovers_after_refresh() -> None:
    assert "sessionStorage.setItem" in TEMPLATE
    assert "publish-latest-job" in TEMPLATE
    assert "storageGet(jobKey)" in TEMPLATE
    assert "pollJob(resumable)" in TEMPLATE
    for state in (
        "loading",
        "queued",
        "running",
        "completed",
        "failed",
        "superseded",
        "error",
    ):
        assert f"'{state}'" in TEMPLATE
    assert "payload.target_states" in TEMPLATE
    assert "防重複" in TEMPLATE
    assert "payload.results" in TEMPLATE
    assert "result.permalink" in TEMPLATE
    assert "partialFailureSummary(payload)" in TEMPLATE
    assert 'aria-live="polite"' in TEMPLATE
    assert "@media (prefers-reduced-motion:reduce)" in CSS


def test_successful_republish_requires_an_explicit_confirmation_control() -> None:
    assert 'name="confirm_republish" value="true"' in TEMPLATE
    assert "publish-republish-required" in TEMPLATE
    assert "republishRequiredForSelection" in TEMPLATE
    assert (
        "selectedPlatforms().some((platform) => republishRequiredPlatforms.has(platform))"
        in TEMPLATE
    )
    assert "republishPanel.hidden = !required" in TEMPLATE
    assert "if (!required) republishConfirmation.checked = false" in TEMPLATE
    assert "await refreshPublishPreflight()" in TEMPLATE
    assert "payload.republish_required_platforms" in TEMPLATE
    assert ".publish-republish-confirmation[hidden] { display:none; }" in CSS
    assert "body: new FormData(publishForm)" in TEMPLATE
    assert "response.status === 409" in TEMPLATE
    assert "publish-context-retry" in TEMPLATE
    assert "重新檢查後再送出" in TEMPLATE


def test_queued_state_exposes_agent_neutral_local_handoff() -> None:
    assert "publish-handoff-id" in TEMPLATE
    assert "publish-claim-codex" in TEMPLATE
    assert "publish-claim-claude" in TEMPLATE
    assert "不會呼叫 Anthropic API" in TEMPLATE
    assert "renderHandoff(job, payload)" in TEMPLATE
    assert "工作會停在等待中，不會自行前進" in TEMPLATE


def test_caption_shows_selected_platform_compatibility_before_handoff() -> None:
    assert 'id="caption-compatibility"' in TEMPLATE
    assert "2,200" in TEMPLATE
    assert "63,206" in TEMPLATE
    assert "會共用這份文案" in TEMPLATE
    assert "const instagramOverLimit = platforms.includes('instagram') && count > 2200" in TEMPLATE
    assert "|| instagramOverLimit" in TEMPLATE
    assert '.caption-compatibility p[data-state="warning"]' in CSS


def test_partial_failure_summary_is_localized_and_retries_only_unfinished() -> None:
    assert "部分平台發布未完成" in TEMPLATE
    assert "已成功發布並保留 checkpoint" in TEMPLATE
    assert "只會重試" in TEMPLATE
    assert "one or more publish targets failed" not in TEMPLATE
