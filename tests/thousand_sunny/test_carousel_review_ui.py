from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = (ROOT / "thousand_sunny/templates/bridge/carousel_review.html").read_text(
    encoding="utf-8"
)
CSS = (ROOT / "thousand_sunny/static/shosho/carousel-review.css").read_text(encoding="utf-8")
BRIDGE = (ROOT / "thousand_sunny/static/shosho/carousel-preview-bridge.js").read_text(
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


def test_evidence_panel_closes_only_for_backdrop_clicks() -> None:
    assert "function closeEvidenceOnBackdrop(event)" in TEMPLATE
    assert "event.currentTarget" in TEMPLATE
    assert "event.target !== dialog" in TEMPLATE
    assert "dialog.getBoundingClientRect()" in TEMPLATE
    assert "event.clientX < bounds.left" in TEMPLATE
    assert "event.clientX > bounds.right" in TEMPLATE
    assert "event.clientY < bounds.top" in TEMPLATE
    assert "event.clientY > bounds.bottom" in TEMPLATE
    assert "dialog.addEventListener('click', closeEvidenceOnBackdrop)" in TEMPLATE


def test_feedback_and_approve_actions_are_explicit_and_mutually_exclusive() -> None:
    assert 'id="review-feedback-button"' in TEMPLATE
    assert 'id="review-approve-button"' in TEMPLATE
    assert 'data-feedback-url="/bridge/ig-cards/{{ episode_slug }}/feedback"' in TEMPLATE
    assert 'data-approve-url="/bridge/ig-cards/{{ episode_slug }}/approve"' in TEMPLATE
    assert "feedbackButton.disabled = approved || busy || editorDirty || count === 0" in TEMPLATE
    assert "approveButton.disabled = approved || busy || editorDirty || count > 0" in TEMPLATE
    assert "approveButton.addEventListener('click'" in TEMPLATE
    assert ".click()" not in TEMPLATE
    assert "requestSubmit" not in TEMPLATE


def test_approved_revision_is_rendered_and_kept_read_only() -> None:
    assert "data-approved=\"{{ 'true' if approved else 'false' }}\"" in TEMPLATE
    assert '{% if approved %}readonly disabled aria-disabled="true"{% endif %}' in TEMPLATE
    assert "此版本已核准，等待發布流程。" in TEMPLATE
    assert "{% if approved %}修改意見 · 已鎖定{% else %}" in TEMPLATE
    assert "let approved = reviewForm.dataset.approved === 'true'" in TEMPLATE
    assert "if (approved) {" in TEMPLATE
    assert "input.readOnly = true" in TEMPLATE
    assert "input.disabled = true" in TEMPLATE
    assert "feedbackButton.disabled = true" in TEMPLATE
    assert "approveButton.disabled = true" in TEMPLATE
    assert "function setApprovedState()" in TEMPLATE
    assert "setApprovedState();" in TEMPLATE
    assert "label.textContent = '修改意見 · 已鎖定'" in TEMPLATE
    assert "#review-action-help" not in CSS
    assert '.carousel-actions p[data-state="approved"]' in CSS
    assert 'aria-label="Stage 6 發布狀態"' in TEMPLATE
    assert "latest_publish_status_label" in TEMPLATE
    assert 'href="{{ publish_url }}"' in TEMPLATE
    assert '.carousel-publish-status[data-state="completed"]' in CSS


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
    assert "等待目前執行此 episode 的 Codex／Claude Code Agent 認領" in TEMPLATE
    assert "不會自動喚醒 Agent" in TEMPLATE
    assert "${job.jobId}" in TEMPLATE
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


def test_mobile_ledger_wraps_all_cells_without_horizontal_scrolling() -> None:
    assert "@media (max-width:720px)" in CSS
    assert "grid-template-columns:repeat(3,minmax(0,1fr))" in CSS
    assert ".carousel-ledger div { min-width:0; padding:8px; }" in CSS
    assert ".carousel-ledger dd { overflow-wrap:anywhere; }" in CSS
    assert "@media (max-width:480px)" in CSS
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in CSS
    assert ".carousel-ledger { margin-top:16px; overflow:auto; }" not in CSS


def test_card_editor_uses_real_dom_structured_edits_and_manifest_scoped_draft() -> None:
    assert 'data-edit-page="{{ page.page_id }}"' in TEMPLATE
    assert "編輯這張卡片" in TEMPLATE
    assert "套用修改" in TEMPLATE
    assert 'data-apply-url="/bridge/ig-cards/{{ episode_slug }}/apply-edits"' in TEMPLATE
    assert 'data-preview-base="/bridge/ig-cards/{{ episode_slug }}/preview"' in TEMPLATE
    assert 'id="carousel-editor-frame"' in TEMPLATE
    assert 'sandbox="allow-scripts"' in TEMPLATE
    assert "editorFrame.contentDocument" not in TEMPLATE
    assert "postMessage({channel: previewChannel" in TEMPLATE
    assert "querySelector('.cover-title')" in BRIDGE
    assert "querySelector('#canvas.cover .guest')" in BRIDGE
    assert "dataset.carouselResizeHandle" in BRIDGE
    assert "guest.addEventListener('pointerdown'" in BRIDGE
    assert "title_font_size_px" in TEMPLATE
    assert "const editorDraftKey = `${storagePrefix}:editor:v2`" in TEMPLATE
    assert "copy_edits: copyEdits" in TEMPLATE
    assert "layout_overrides: editorState.layout" in TEMPLATE
    assert "published" not in TEMPLATE
    assert "aspect-ratio:1" in CSS
    assert "width:1080px" in CSS


def test_cover_layout_controls_stay_hidden_on_non_cover_cards() -> None:
    assert "editorLayout.hidden = page.role !== 'cover'" in TEMPLATE
    assert ".carousel-layout[hidden] { display:none; }" in CSS
    assert "currentEditorPage = page;\n  editorStatus.textContent = '';" in TEMPLATE


def test_card_editor_uses_plain_chinese_labels_for_copy_and_layout() -> None:
    for label in (
        "標題",
        "強調文字",
        "來賓姓名",
        "來賓職稱",
        "問題",
        "承接文字",
        "內文",
        "來賓金句",
        "主持人提問",
        "節目名稱／CTA 標題",
        "來賓右側位置（px）",
        "來賓底部位置（px）",
        "來賓高度（px）",
        "標題字級（px）",
    ):
        assert label in TEMPLATE

    for technical_label in (
        "Display copy",
        "Cover layout",
        "Guest right",
        "Guest bottom",
        "Guest height",
        "Title px",
        "deterministic",
        "CARD EDITOR",
        "Carousel live preview",
    ):
        assert technical_label not in TEMPLATE


def test_editor_recovers_dirty_cards_and_validates_emphasis_inline() -> None:
    assert "data-dirty-page" in TEMPLATE
    assert 'id="carousel-editor-recovery"' in TEMPLATE
    assert "dirtyEditorPageIds()" in TEMPLATE
    # 修修 2026-09-02：頁面上那顆重複的送出鈕已拿掉，提示列只帶路不送出。
    assert "尚有 ${dirtyIds.length} 張卡片修改未送出" in TEMPLATE
    assert "carousel-editor-apply-all" not in TEMPLATE
    assert "editorStateHasInvalidEmphasis()" in TEMPLATE
    assert "強調文字必須完整出現在" in TEMPLATE
    assert "data-field-error" in TEMPLATE
    assert "aria-invalid" in TEMPLATE


def test_editor_uses_receipt_verified_sandbox_bridge_and_canonical_refit() -> None:
    assert "allow-same-origin" not in TEMPLATE
    assert "previewChannel = 'nakama-carousel-editor-v1'" in TEMPLATE
    assert "event.source !== editorFrame.contentWindow" in TEMPLATE
    assert "window.__carouselRefit()" in BRIDGE
    assert "emit('diagnostics'" in BRIDGE
    assert "fetch(" not in BRIDGE
    assert "sessionStorage" not in BRIDGE
    assert "localStorage" not in BRIDGE


def test_editor_mobile_scroll_reset_and_preserve_actions_are_explicit() -> None:
    assert 'id="carousel-editor-reset"' in TEMPLATE
    assert "重設這張" in TEMPLATE
    # 對話框只剩「取消／送出」。修修：「這麼簡單的任務，不會有那種改到一半
    # 想要儲存草稿的事情。」取消＝丟棄，且有改動時會先確認。
    assert "保留草稿並關閉" not in TEMPLATE
    assert "捨棄這張卡片尚未送出的修改？" in TEMPLATE
    # ×／Esc 保留修改（修修的用法是反覆換素材、關掉看整體再回來）；
    # 只有「取消」丟棄。兩條路徑分開，不共用同一個 handler。
    assert "discardAndCloseEditor" in TEMPLATE
    assert "preserveAndCloseEditor" not in TEMPLATE
    assert "overscroll-behavior:contain" in CSS
    assert ".carousel-editor__body { display:block; overflow-x:hidden; overflow-y:auto" in CSS


def test_text_layout_editor_uses_canonical_patch_keyboard_and_accessible_handles() -> None:
    assert 'data-text-layout-field="x_px"' in TEMPLATE
    assert 'data-text-layout-field="width_px"' in TEMPLATE
    assert 'data-text-layout-field="font_start_px"' in TEMPLATE
    assert "text_layout_overrides:" in TEMPLATE
    assert "window.applyEditorPatch(textLayouts)" in BRIDGE
    assert "document.fonts.ready.then" in BRIDGE
    assert "event.shiftKey ? 16 : 4" in BRIDGE
    assert "--editor-handle-size" in BRIDGE
    assert "editor-escape" in BRIDGE
    assert "--sho-danger" not in CSS
    assert (
        ".carousel-editor__preview,.carousel-editor__controls { overflow:visible; padding:" in CSS
    )


def test_reset_rehydrates_selected_region_controls_and_invalidates_stale_diagnostics() -> None:
    reset_handler = TEMPLATE.split(
        "document.getElementById('carousel-editor-reset').addEventListener", 1
    )[1].split("document.getElementById('carousel-editor-continue')", 1)[0]
    assert "previewDiagnostics = null" in reset_handler
    assert "editorFit.hidden = true" in reset_handler
    assert "renderTextLayoutControls(currentEditorPage, editorTextRegion.value)" in reset_handler
    assert "applyTextLayoutsToPreview(currentEditorPage)" in reset_handler
    assert reset_handler.index("renderTextLayoutControls") < reset_handler.index(
        "applyTextLayoutsToPreview"
    )


def test_region_selection_numeric_normalisation_and_diagnostics_are_one_state_machine() -> None:
    assert "function selectTextRegion(" in TEMPLATE
    assert "event.data.type === 'text-region-select'" in TEMPLATE
    assert "normaliseTextLayoutValues" in TEMPLATE
    assert "Number.isFinite" in TEMPLATE
    assert "textLayoutSafeRects[`${page.role}.${region}`]" in TEMPLATE
    assert "diagnosticsByPage" in TEMPLATE
    assert "editorDraftFingerprint" in TEMPLATE
    assert "pageHasCurrentFitDiagnostics" in TEMPLATE
    assert "event.data.type === 'preview-error'" in TEMPLATE
    assert "previewError" in TEMPLATE


def test_manual_line_validation_apply_scope_and_loading_controls_are_explicit() -> None:
    assert "validateManualLines" in TEMPLATE
    assert "manualLinesError" in TEMPLATE
    assert "editorTextLayout.setAttribute('aria-busy'" in TEMPLATE
    assert "input.disabled = loading" in TEMPLATE
    # 送出入口只有一個——這條原本斷言「必須有兩顆」，正是修修反映的重複。
    assert TEMPLATE.count("submitEditorChanges('all')") == 1
    assert "送出給 agent 修改" in TEMPLATE
    # 舊標籤「套用」讓人以為當下就生效，實際上只是建立一張待認領的修正單。
    assert "套用所有待修改" not in TEMPLATE


def test_preview_interactions_are_selected_scaled_keyboard_and_pointer_safe() -> None:
    assert "emit('text-region-select'" in BRIDGE
    assert "data-text-region-selected" in BRIDGE
    assert "--editor-handle-size" in BRIDGE
    assert "set-visual-scale" in BRIDGE
    assert "handle.addEventListener('keydown'" in BRIDGE
    assert "setPointerCapture" in BRIDGE
    assert "pointercancel" in BRIDGE
    assert "refitNow = true" in BRIDGE
    assert "node.setAttribute('aria-selected'" not in BRIDGE
    assert TEMPLATE.count("sendPreview('set-visual-scale'") >= 2
    assert "sizeEditorPreview();" in TEMPLATE


def test_editor_accessibility_loading_recovery_and_empty_numbers_fail_closed() -> None:
    assert "error.id = `carousel-editor-error-${page.page_id}-${name}`" in TEMPLATE
    assert "input.setAttribute('aria-describedby', error.id)" in TEMPLATE
    assert "input.setAttribute('aria-errormessage', error.id)" in TEMPLATE
    assert "正在載入版面控制…" in TEMPLATE
    assert "請逐張重新開啟以取得最新版面檢查" in TEMPLATE
    assert TEMPLATE.count('type="number"') == 8
    assert TEMPLATE.count('step="1" required') == 4
    assert TEMPLATE.count('step="4" required') == 3
    assert TEMPLATE.count('step="2" required') == 1
    assert "values[name] === ''" in TEMPLATE
    assert "values[input.dataset.layoutField] === ''" in TEMPLATE


def test_cutout_picker_lives_in_the_card_editor_not_the_page_header() -> None:
    """修修 2026-09-02：選圖要跟即時預覽在一起，點了馬上看到新的一層。

    第一版把清單放在頁面最上方、點一下直接開修正單——沒看到結果就先送出。
    """
    assert 'id="carousel-cutout-picker"' in TEMPLATE
    editor_start = TEMPLATE.index('<section class="carousel-editor__controls"')
    editor_end = TEMPLATE.index('<footer class="carousel-editor__footer">')
    picker_at = TEMPLATE.index('id="carousel-cutout-picker"')
    assert editor_start < picker_at < editor_end
    # 選圖是要反覆試的，排在文字欄位前面才不用每次先捲過四個 textarea。
    assert picker_at < TEMPLATE.index('<div id="carousel-editor-fields"></div>')
    # 舊的頁首選擇器與它的「套用到」下拉不可以再存在。
    assert 'id="carousel-cutouts"' not in TEMPLATE
    assert "carousel-cutout-target" not in TEMPLATE
    assert "cutout_targets" not in TEMPLATE


def test_cutout_pick_goes_through_the_normal_submit_not_its_own_job() -> None:
    """換照片跟改文字一起走「送出給 agent 修改」，不再自己建一張修正單。"""
    assert "editorState.copyEdits[page.page_id] = next" in TEMPLATE
    picker_js = TEMPLATE[TEMPLATE.index("if (cutoutPicker) {") :]
    assert "fetch(" not in picker_js[: picker_js.index("updateEditorAction();")]
    # 打字時只掃得到文字輸入框，不先接住已選的照片就會把它洗掉。
    assert "const next = {...assetEditsFor(page)};" in TEMPLATE


def test_cutout_preview_swap_carries_the_post_pick_fingerprint() -> None:
    """換圖改變重疊量測；診斷的 fingerprint 沒跟著換，送出鈕會永遠 disabled。"""
    assert "previewPatchSequence += 1;" in TEMPLATE
    assert "fingerprint: editorDraftFingerprint(page)," in TEMPLATE
    assert "type === 'apply-cutout'" in BRIDGE
    handler = BRIDGE[BRIDGE.index("type === 'apply-cutout'") :]
    adopt = handler.index("diagnosticsFingerprint = event.data.fingerprint")
    assert adopt < handler.index("applyCutout(event.data.field")


def test_cutout_reaches_the_preview_as_a_data_url() -> None:
    """預覽的 CSP 是 `img-src 'self' data:`，且 preview-assets 只服務 receipt

    驗過的樣板快照——新選的去背照不在快照裡，只能由母頁面轉成 data URL 遞進去。
    """
    assert "readAsDataURL" in TEMPLATE
    assert 'data-cutout-base="/bridge/ig-cards/{{ episode_slug }}/cutout"' in TEMPLATE
    assert "img-src 'self' data:" in (
        ROOT / "thousand_sunny/routers/carousel_review.py"
    ).read_text(encoding="utf-8")


def test_cutout_strip_cannot_blow_out_the_editor_column() -> None:
    """fieldset 預設 min-inline-size:min-content，實測會把控制欄撐到 1592px。"""
    strip = CSS[CSS.index(".carousel-cutouts {") : CSS.index(".carousel-cutout {")]
    assert "min-inline-size:0" in strip
    assert "overflow-x:auto" in CSS[CSS.index(".carousel-cutouts__list {") :][:400]
    # contain 而非 cover：去背照上方有透明留白，切到頂端整格會看起來是空的。
    thumb = CSS[CSS.index(".carousel-cutout img {") :][:200]
    assert "object-fit:contain" in thumb
