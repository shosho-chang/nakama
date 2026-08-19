from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = (ROOT / "thousand_sunny/templates/bridge/carousel_review.html").read_text(
    encoding="utf-8"
)
CSS = (ROOT / "thousand_sunny/static/shosho/carousel-review.css").read_text(
    encoding="utf-8"
)


def test_each_card_has_feedback_only_and_no_decision_radios() -> None:
    assert 'type="radio"' not in TEMPLATE
    assert 'name="status_' not in TEMPLATE
    assert 'name="feedback_{{ page.page_id }}"' in TEMPLATE
    assert "data-feedback-input" in TEMPLATE


def test_review_grid_uses_square_carousel_previews() -> None:
    assert "aspect-ratio:1/1" in CSS
    assert "aspect-ratio:4/5" not in CSS


def test_feedback_and_approve_actions_are_explicit_and_mutually_exclusive() -> None:
    assert 'id="review-feedback-button"' in TEMPLATE
    assert 'id="review-approve-button"' in TEMPLATE
    assert 'data-feedback-url="/bridge/ig-cards/{{ episode_slug }}/feedback"' in TEMPLATE
    assert 'data-approve-url="/bridge/ig-cards/{{ episode_slug }}/approve"' in TEMPLATE
    assert "feedbackButton.disabled = approved || busy || count === 0" in TEMPLATE
    assert "approveButton.disabled = approved || busy || count > 0" in TEMPLATE
    assert "approveButton.addEventListener('click'" in TEMPLATE
    assert ".click()" not in TEMPLATE
    assert "requestSubmit" not in TEMPLATE


def test_approved_revision_is_rendered_and_kept_read_only() -> None:
    assert 'data-approved="{{ \'true\' if approved else \'false\' }}"' in TEMPLATE
    assert "{% if approved %}readonly disabled aria-disabled=\"true\"{% endif %}" in TEMPLATE
    assert "此 revision 已核准，等待發布流程。" in TEMPLATE
    assert "{% if approved %}Feedback · 已鎖定{% else %}" in TEMPLATE
    assert "let approved = reviewForm.dataset.approved === 'true'" in TEMPLATE
    assert "if (approved) {" in TEMPLATE
    assert "input.readOnly = true" in TEMPLATE
    assert "input.disabled = true" in TEMPLATE
    assert "feedbackButton.disabled = true" in TEMPLATE
    assert "approveButton.disabled = true" in TEMPLATE
    assert "function setApprovedState()" in TEMPLATE
    assert "setApprovedState();" in TEMPLATE
    assert "label.textContent = 'Feedback · 已鎖定'" in TEMPLATE
    assert '#review-action-help' not in CSS
    assert '.carousel-actions p[data-state="approved"]' in CSS


def test_manifest_scoped_draft_and_job_survive_refresh() -> None:
    assert "reviewForm.dataset.manifestSha" in TEMPLATE
    assert "const draftKey = `${storagePrefix}:draft:v2`" in TEMPLATE
    assert "const jobKey = `${storagePrefix}:job:v1`" in TEMPLATE
    assert "sessionStorage.setItem" in TEMPLATE
    assert "sessionStorage.getItem" in TEMPLATE
    assert "restoreDraft()" in TEMPLATE
    assert "resumableJobRaw" in TEMPLATE


def test_real_job_states_poll_until_completed_and_restore_failed_feedback() -> None:
    for state in ("loading", "queued", "running", "completed", "failed", "error"):
        assert f"'{state}'" in TEMPLATE
    assert "payload.job_id" in TEMPLATE
    assert "payload.status_url" in TEMPLATE
    assert "statusUrlForPayload(payload)" in TEMPLATE
    assert "payload.steps || payload.progress" in TEMPLATE
    assert "item.progress_percent" in TEMPLATE
    assert "payload.result_revision" in TEMPLATE
    assert "正在等待 Agent 開始" in TEMPLATE
    assert "restoreFailedFeedback(job)" in TEMPLATE
    assert "window.location.reload()" in TEMPLATE


def test_accessible_status_and_five_column_visual_contract() -> None:
    assert 'aria-live="polite"' in TEMPLATE
    assert 'aria-busy="false"' in TEMPLATE
    assert 'tabindex="-1"' in TEMPLATE
    assert "grid-template-columns:repeat(5,minmax(0,1fr))" in CSS
    assert "var(--sho-font-zh)" in CSS
    assert "@media (prefers-reduced-motion:reduce)" in CSS
    assert "#ff" not in CSS.lower()
