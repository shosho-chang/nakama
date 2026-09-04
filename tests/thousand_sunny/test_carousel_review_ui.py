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
    # 2026-09-02 拿掉：版面檢查不再 gate 送出，就沒有「請逐張重新開啟以取得
    # 最新版面檢查」這個要求了。
    assert "請逐張重新開啟以取得最新版面檢查" not in TEMPLATE
    # 封面 4 + 金句 3 + 文字區塊 4。金句那三個是 2026-09-02 補的——在那之前
    # 金句的去背照沒有任何幾何欄位，位置寫死在算圖 CSS 裡，所以拖不動。
    assert TEMPLATE.count('type="number"') == 11
    assert TEMPLATE.count('step="1" required') == 7
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
    assert "img-src 'self' data:" in (ROOT / "thousand_sunny/routers/carousel_review.py").read_text(
        encoding="utf-8"
    )


def test_cutout_strip_cannot_blow_out_the_editor_column() -> None:
    """fieldset 預設 min-inline-size:min-content，實測會把控制欄撐到 1592px。"""
    strip = CSS[CSS.index(".carousel-cutouts {") : CSS.index(".carousel-cutout {")]
    assert "min-inline-size:0" in strip
    assert "overflow-x:auto" in CSS[CSS.index(".carousel-cutouts__list {") :][:400]
    # contain 而非 cover：去背照上方有透明留白，切到頂端整格會看起來是空的。
    thumb = CSS[CSS.index(".carousel-cutout img {") :][:200]
    assert "object-fit:contain" in thumb


def test_guest_geometry_is_not_hardcoded_to_the_cover() -> None:
    """金句的去背照原本沒有拖曳綁定——選擇器寫死 `#canvas.cover .guest`。

    修修 2026-09-02：「金句那邊也按照你現在建議的修法去修。」
    """
    assert "configureCoverInteraction" not in BRIDGE
    assert "function configureGuestInteraction()" in BRIDGE
    assert "const guestLayoutSpecs = {" in BRIDGE
    specs = BRIDGE[BRIDGE.index("const guestLayoutSpecs = {") :][:900]
    assert "cover:" in specs and "quote:" in specs
    assert "#canvas.quote-a .guest" in specs
    # 標題字級只有封面有；金句套用幾何時不可以去碰任何字級。
    assert "Object.hasOwn(layout, 'title_font_size_px')" in BRIDGE


def test_guest_drag_binds_on_measure_not_on_a_parent_message() -> None:
    """金句沒有 schema default，母頁面要等基準值才知道送什麼——押在 apply-layout

    上綁定會變成先有雞先有蛋，金句永遠綁不上。
    """
    discover = BRIDGE[BRIDGE.index("function discoverGuestLayout()") :]
    body = discover[: discover.index("\n  function ", 10)]
    assert "emit('guest-layout-baseline'" in body
    assert "configureGuestInteraction();" in body


def test_resize_handle_is_clamped_into_the_canvas() -> None:
    """去背照靠右定位且常比畫布寬，錨點算出來可能落在畫布外：

    那時控制點會被 overflow:hidden 裁掉、再也點不到，縮放就整個失效。
    """
    fn = BRIDGE[BRIDGE.index("function positionResizeHandle(guest)") :][:1600]
    assert "clamp" in fn
    assert "bounds.width - size" in fn and "bounds.height - size" in fn


def test_name_card_does_not_eat_the_cutout_pointer_events() -> None:
    """`.guest-label` 是 z-index:5，蓋在去背照上面，左下角整塊拖不動。"""
    assert ".cover .guest-label{pointer-events:none}" in BRIDGE
    # 兩個控制點原本長得一樣（去背照 z30、文字 z40），要分得開。
    assert "border-radius:50%" in BRIDGE
    assert "outline:4px dashed var(--orange)" in BRIDGE


def test_quote_layout_controls_exist_and_are_separate_from_cover() -> None:
    assert 'id="carousel-quote-layout"' in TEMPLATE
    assert 'data-quote-layout-field="guest_right_px"' in TEMPLATE
    assert 'data-quote-layout-field="guest_height_px"' in TEMPLATE
    assert "editorQuoteLayout.hidden = page.role !== 'quote'" in TEMPLATE
    assert "quote_layout_overrides:" in TEMPLATE
    # 金句幾何要進 fingerprint，否則調完診斷對不上、送出鈕解不開。
    assert "quote: page.role === 'quote' ? activeQuoteLayout() : null," in TEMPLATE


def test_quote_baseline_arrival_refreshes_diagnostics() -> None:
    """基準值進了 fingerprint，之前那輪診斷就對不上。

    不重新要一次，金句的「送出」會卡在 disabled，直到使用者剛好改了別的東西。
    """
    handler = TEMPLATE[TEMPLATE.index("type === 'guest-layout-baseline'") :]
    block = handler[: handler.index("} else if")]
    assert "quoteLayoutBaseline = event.data.values;" in block
    assert "applyTextLayoutsToPreview(currentEditorPage);" in block


def test_every_guest_role_can_seed_a_complete_layout_before_binding() -> None:
    """封面拖不動的真因（2026-09-02）。

    綁定改成「一量到就自己綁」之後，seeding 條件是 bounds 的每個 key 都量得到。
    封面的 bounds 多一個 `title_font_size_px`，那個不在去背照身上、量不到，
    於是 `layout` 停在 null；拖曳時 `{...null}` 展開成 `{}`，算出 NaN，
    而 `style.right = 'NaNpx'` 會被瀏覽器**靜默忽略**——症狀正好是
    「綁得到、游標會變、就是不動」。金句三個值都量得到所以正常，只有封面中槍。
    """
    assert "extraMeasure:" in BRIDGE
    assert "--type-cover-title" in BRIDGE
    seed = BRIDGE[BRIDGE.index("const seed = {") :][:400]
    assert "spec.extraMeasure ? spec.extraMeasure() : {}" in seed
    # seeding 必須看合併後的 seed，不是只有量到的三個幾何值
    assert "Object.keys(spec.bounds).every((name) => name in seed)" in BRIDGE
    assert "Object.keys(spec.bounds).every((name) => name in values)" not in BRIDGE


def test_incomplete_layout_fails_loudly_instead_of_writing_nan() -> None:
    """NaN 寫進 style 會被丟掉、什麼都不發生——那是最難查的失敗樣態。"""
    apply_fn = BRIDGE[BRIDGE.index("function applyLayout(values,") :][:1200]
    assert "const bounded = boundedLayout(values, spec.bounds);" in apply_fn
    assert "!Number.isFinite(value)" in apply_fn
    assert "emit('preview-error'" in apply_fn
    # 驗證在賦值給 layout 之前
    assert apply_fn.index("!Number.isFinite(value)") < apply_fn.index("layout = bounded;")


def test_guest_role_comes_from_the_canvas_not_the_spec_query() -> None:
    """`currentPage()` 需要 `__CAROUSEL_SPEC__` 與 `?page=` 都對，是改寫時引進的

    相依。任一不如預期，`applyLayout` 與 `configureGuestInteraction` 都會安靜
    return——症狀就是「拖了沒反應、也沒有錯誤」。畫布 class 是算圖時就寫死的。
    """
    assert "function guestRole()" in BRIDGE
    assert "canvas.matches(klass)" in BRIDGE
    spec_fn = BRIDGE[BRIDGE.index("function guestSpec()") :][:300]
    assert "guestRole()" in spec_fn
    assert "currentPage()" not in spec_fn


def test_failure_to_bind_the_cutout_is_never_silent() -> None:
    """綁不上原本完全靜默，只有「拖了沒反應」，沒有任何線索可查。"""
    cfg = BRIDGE[BRIDGE.index("function configureGuestInteraction()") :]
    body = cfg[: cfg.index("\n  function ", 10)]
    assert body.count("emit('guest-editor-state'") >= 3
    assert "bound: false" in body and "bound: true" in body
    # 母頁面要把它顯示出來
    assert "guest-editor-state" in TEMPLATE
    assert "去背照可拖曳縮放" in TEMPLATE
    assert "去背照無法拖曳：" in TEMPLATE


def test_binding_does_not_hang_on_a_single_trigger() -> None:
    """fonts.ready 被延後或不觸發時，綁定不該就此消失。"""
    tail = BRIDGE[BRIDGE.index("document.fonts.ready.then") :]
    assert tail.count("discoverGuestLayout()") >= 2


def test_resize_handle_anchors_to_the_visible_subject_not_the_bounding_box() -> None:
    """封面「縮放不了」的真因（2026-09-02，修修在真實瀏覽器回報）。

    去背照的外框遠大於看得見的人——封面這張寬 1119（比 1080 畫布還寬），
    左上角落在標題文字上。控制點錨在外框角落，就變成一個橘色圓點壓在
    「AI 要讓人」上面，橘底配橘點，完全看不出是那張照片的控制項。
    金句用的是 `_trim` 版，外框貼著人，所以一直沒問題。
    """
    assert "function opaqueInset(" in BRIDGE
    assert "getImageData" in BRIDGE
    # 羽化邊緣不能算進去，否則邊界會比實際大一圈
    assert "> 128" in BRIDGE
    pos = BRIDGE[BRIDGE.index("function positionResizeHandle(guest)") :][:1200]
    assert "opaqueInset(guest)" in pos
    # 高度往上長，抓上緣才對得上手感；水平取人物中央以避開標題
    assert "(inset.left + inset.right) / 2" in pos
    assert "rect.height * inset.top" in pos


def test_resize_handle_is_legible_on_an_orange_card() -> None:
    """原本是橘底橘點，在橘色封面上等於隱形。"""
    style = BRIDGE[BRIDGE.index(".carousel-preview-resize{") :][:500]
    assert "background:#fff" in style
    assert "border-radius:50%" in style
    assert "box-shadow" in style


def test_tainted_canvas_falls_back_instead_of_losing_the_handle() -> None:
    """非 data: 來源會污染畫布讓 getImageData 丟例外——控制點不可以因此消失。"""
    fn = BRIDGE[BRIDGE.index("function opaqueInset(img)") :][:1600]
    assert "catch (error)" in fn
    assert fn.count("{left: 0, right: 1, top: 0}") >= 1


def test_live_geometry_beats_the_injected_important_override() -> None:
    """封面「數字有變、畫面不動」的真因（2026-09-02，修修從畫面看出來的）。

    算圖端注入的 layout override 是 `.cover .guest{height:…!important}`，
    而沒有標記的 inline style **打不贏樣式表的 !important**。實測同一元素：
    普通 inline height:1300px → 畫面仍是 956；帶 !important → 畫面才變 1300。

    所以這個 bug 只發生在**已經存過 override 的卡**上。r002 的封面有
    `layout_overrides.cover`、金句沒有——這就是兩張卡表現不同的全部原因，
    也是為什麼盯著 `style.right` 有沒有被設定會一路測「通過」：屬性確實有設，
    只是不生效。驗收要看算出來的幾何。
    """
    fn = BRIDGE[BRIDGE.index("function applyLayout(values,") :][:2000]
    for name in ("right", "bottom", "height"):
        assert f"guest.style.setProperty('{name}'" in fn
    assert fn.count("'important'") >= 3
    assert "guest.style.right =" not in BRIDGE
    assert "guest.style.height =" not in BRIDGE


def test_renderer_still_pins_saved_geometry_with_important() -> None:
    """算圖端維持 !important——它要蓋掉樣板自己的 `.cover .guest` 預設。

    編輯器改用 important inline 取勝，兩邊不必再彼此遷就。
    """
    render = (ROOT / "agents/brook/podcast_carousel_render.py").read_text(encoding="utf-8")
    assert "px!important" in render


def test_layout_diagnostics_advise_but_never_block_submission() -> None:
    """修修 2026-09-02：「跟文字一樣，我送出去的就是 override 全部的規則。

    機器看不到我看到的東西。」重疊量、字級、碰撞都是機器對著 DOM 量出來的
    啟發式規則，看不到他在畫面上看到的整體感覺——那是編輯決定，不是機器的。
    在此之前這是硬性 gate：他把去背照放大到重疊 267px（上限 240）之後，
    送出鈕變成 disabled，按下去毫無反應也沒有交代。
    """
    gate = TEMPLATE[TEMPLATE.index("const blocked = approved || busy") :][:400]
    assert "allDiagnosticsFit" not in gate
    submit = TEMPLATE[TEMPLATE.index("async function submitEditorChanges(scope)") :][:700]
    assert "allDiagnosticsFit" not in submit
    # 改成提示，而且跟「真的擋住」在視覺上分得開
    assert "function layoutAdvisory(" in TEMPLATE
    assert "確定要這樣就直接送出" in TEMPLATE
    assert "editorBlocked.dataset.kind" in TEMPLATE
    assert '.carousel-editor-blocked[data-kind="blocked"]' in CSS


def test_render_fatal_input_still_blocks() -> None:
    """會讓算圖直接失敗的才擋——空欄位、強調文字不在主要文字裡、數值不是數字。"""
    fn = TEMPLATE[TEMPLATE.index("function applyBlockedReason(") :][:900]
    assert "算圖會失敗" in fn
    assert "editorHasEmptyValue()" in fn
    assert "editorValidationError" in fn


def test_active_job_conflict_names_the_blocking_job() -> None:
    """原本只回一句英文 `correction job is still active`，畫面上看不到那張單。"""
    router = (ROOT / "thousand_sunny/routers/carousel_review.py").read_text(encoding="utf-8")
    assert "def _active_job_conflict(" in router
    assert "已經有一張待處理的修改工作" in router
    assert router.count("_active_job_conflict(package_root, manifest, manifest_sha256)") >= 2


def test_submit_failure_is_shown_where_the_user_is_looking() -> None:
    """修修 2026-09-02 連續兩次回報「按下送出沒有任何反應」。

    第二次其實送出去了、伺服器回了 409（被他自己前一張還沒被 agent 認領的
    修正單擋住），但失敗訊息只寫在頁尾一個小 span 裡——按了之後畫面上看不出
    任何變化，就是「沒反應」。
    """
    assert "submitFailure = `送出失敗：" in TEMPLATE
    # 失敗要壓過版面提示，那是使用者剛按下去的結果
    assert "const boardMessage = submitFailure || reason;" in TEMPLATE
    assert "submitFailure ? 'error'" in TEMPLATE
    assert '.carousel-editor-blocked[data-kind="error"]' in CSS
    assert "var(--sho-error)" in CSS


def test_submit_lives_on_the_board_and_the_card_editor_only_confirms() -> None:
    """修修 2026-09-02：「每一張點進去，修改之後按確定，然後出來之後沒問題的話，

    我按下一個按鈕，就會有 Agent 把整個接走，一張一張 render。」

    送出鍵原本長在單張卡的編輯器裡、寫著「送出給 agent 修改」——但它其實一次
    送出**所有**累積的修改。所以改完第一張很自然就按了，單子成立，第二張再按
    就被自己那張擋住（同一版本一次只能有一張進行中的修正單）。
    """
    assert 'id="carousel-editor-done"' in TEMPLATE
    assert "完成這張" in TEMPLATE
    assert 'id="carousel-editor-apply"' not in TEMPLATE
    # 註解裡還留著這個舊名字當紀錄，所以比對的是按鈕標籤本身。
    assert ">送出給 agent 修改<" not in TEMPLATE
    # 送出只在主畫面那條，且會數出幾張卡
    assert 'id="carousel-editor-submit"' in TEMPLATE
    assert "`送出 ${dirtyIds.length} 張卡片給 agent`" in TEMPLATE
    submit_at = TEMPLATE.index('id="carousel-editor-submit"')
    dialog_at = TEMPLATE.index('<dialog id="carousel-editor"')
    assert submit_at < dialog_at, "送出鍵必須在主畫面，不能又跑回編輯器裡"
    # 「完成這張」不連網，只保留草稿並關閉
    done = TEMPLATE[TEMPLATE.index("editorDone.addEventListener") :][:300]
    assert "persistEditorDraft();" in done and "editorDialog.close();" in done
    assert "fetch(" not in done


def test_job_poller_gives_up_when_the_job_is_gone() -> None:
    """原本一律 5 秒重試，對著 404 會永遠打下去——實測留下約 700 筆 console

    錯誤，而且 UI 卡在 busy 解不開。
    """
    poll = TEMPLATE[TEMPLATE.index("async function pollJob(job)") :]
    body = poll[: poll.index("\n}\n")]
    assert "failure.status = response.status;" in body
    assert "error.status === 404" in body
    assert "MAX_POLL_ERRORS" in body
    assert body.count("busy = false;") >= 2


def test_drafts_orphaned_by_a_new_revision_are_offered_back() -> None:
    """修修 2026-09-02：「我第 2 張卡片之前改的已經被移除掉了嗎？」

    草稿的 key 含 manifest sha（`nakama.carousel-review:<sha>:editor:v2`），
    版本一換就整個讀不到。他那筆文字修改先被 409 擋下（草稿保留），接著 r003
    出來，草稿還在 sessionStorage 裡卻再也不會被看到——畫面看起來像「改的東西
    被移掉了」，而且完全沒有交代。
    """
    assert "function orphanEditorDrafts()" in TEMPLATE
    assert "key.endsWith(':editor:v2')" in TEMPLATE
    assert "key === editorDraftKey" in TEMPLATE
    assert 'id="carousel-orphan-notice"' in TEMPLATE
    assert "上一版還留著" in TEMPLATE
    # 版面數值是對著舊版量的，不可以默默套到新版上
    assert "不會帶過來，需要重調" in TEMPLATE
    apply_handler = TEMPLATE[TEMPLATE.index("carousel-orphan-apply').addEventListener") :][:600]
    assert "draft.copyEdits[pageId]" in apply_handler
    assert "draft.layout" not in apply_handler
    assert "storageRemove(key);" in apply_handler


def test_board_thumbnails_show_pending_edits() -> None:
    """修修 2026-09-02：「我需要等我退出來之後，在整個畫面預覽那邊，顯示的都是

    已經修改過的內容。」主畫面的縮圖是**已出圖**的 PNG，草稿沒出圖，所以改完
    退出來看到的還是舊的，只多一個「有未套用修改」標記。
    """
    assert "function refreshCardPreviews(" in TEMPLATE
    assert "carousel-card-preview" in TEMPLATE
    assert ".carousel-card-preview" in CSS
    # 疊在 PNG 上但不可互動——點擊要落到底下的「查看逐字稿證據」按鈕
    strip = CSS[CSS.index(".carousel-card-preview") :][:400]
    assert "pointer-events:none" in strip
    # 縮放後的 iframe 靠它定位；只能有一條 `.carousel-image-button` 規則。
    assert CSS.count(".carousel-image-button {") == 1
    assert "position:relative" in CSS[CSS.index(".carousel-image-button {") :][:220]
    # 沒有草稿的卡片要維持 PNG——那才是真正出圖過的東西
    fn = TEMPLATE[TEMPLATE.index("function refreshCardPreviews(") :][:1200]
    assert "frame.remove();" in fn
    assert "cardPreviewFrames.delete(pageId);" in fn


def test_card_thumbnails_hide_editor_affordances() -> None:
    """縮圖上不該出現拖曳圓點與文字選取外框——那是編輯器的控制項，不是卡片內容。

    這段樣式原本寫在 `configureGuestInteraction()` 裡，而那個函式只有封面／金句
    會走到，於是 hook 那類卡片的縮圖仍然帶著控制點（2026-09-02 實測到）。
    """
    assert "type === 'set-presentation'" in BRIDGE
    assert "style.dataset.presentationMode" in BRIDGE
    inject = BRIDGE[BRIDGE.index("style.dataset.presentationMode") :][:600]
    assert ".carousel-preview-resize" in inject
    assert ".carousel-preview-text-resize{display:none!important}" in inject
    # 必須在 IIFE 頂層一律注入，不能藏在只有部分卡片會走到的函式裡
    assert BRIDGE.index("style.dataset.presentationMode") < BRIDGE.index(
        "function configureGuestInteraction()"
    )


def test_submitting_clears_the_pending_state() -> None:
    """修修 2026-09-03：送出 8 張之後，主畫面仍寫著「尚有 8 張卡片修改未送出」，

    送出鍵也還在（再按只會拿到 409）。工作其實已經建立成功了。
    原因是成功路徑只清了 storage，`editorState` 還留在記憶體裡。
    """
    # `storageRemove(editorDraftKey)` 在 pollJob 也有一處，錨在送出成功那段的註解上。
    success = TEMPLATE[TEMPLATE.index("只清 storage 不夠") :][:900]
    assert (
        "editorState = {copyEdits: {}, layout: null, quoteLayout: null, textLayouts: {}};"
        in success
    )
    assert "submitFailure = '';" in success
    # 草稿隨 job 一起存進 jobKey，工作失敗時由 restoreFailedFeedback 還原
    assert "storageSet(jobKey" in success
    assert "restoreFailedFeedback" in TEMPLATE


def test_busy_label_lands_on_the_button_that_was_pressed() -> None:
    """「送出中…」原本跑到「修改意見」那顆上——使用者按的是主畫面的送出鍵。

    `busy` 在**工作進行中**會一直是 true，所以它不能用來決定哪顆顯示 loading。
    """
    assert "let busySource = null;" in TEMPLATE
    assert "busySource === 'feedback'" in TEMPLATE
    assert "busySource === 'approve'" in TEMPLATE
    assert "busySource === 'editor'" in TEMPLATE
    assert "busy && !editorSubmitBusy" not in TEMPLATE
    # 每一處放開 busy 都要一併放開 busySource，否則標籤會卡住
    import re as _re

    lines = TEMPLATE.split("\n")
    for index, line in enumerate(lines):
        if _re.fullmatch(r"\s*busy = false;", line):
            assert "busySource = null;" in lines[index + 1], f"line {index + 1} 沒有清掉 busySource"


def test_draft_keys_are_namespaced_by_episode() -> None:
    """page_id（cover／hook／quote）每一集都一樣。

    只用 manifest sha 當前綴時，A 集沒送出的草稿會在 B 集被遺留掃描撿起來並
    套用——把 A 的文案寫進 B（2026-09-03 review 抓到）。
    """
    assert 'data-episode="{{ episode_slug }}"' in TEMPLATE
    assert "const episodeKey = reviewForm.dataset.episode;" in TEMPLATE
    assert "`nakama.carousel-review:${episodeKey}:${reviewForm.dataset.manifestSha}`" in TEMPLATE
    # 遺留掃描也要限定同一集
    assert "const orphanPrefix = `nakama.carousel-review:${episodeKey}:`;" in TEMPLATE
    assert "key.startsWith(orphanPrefix)" in TEMPLATE
    assert "key.startsWith('nakama.carousel-review:')" not in TEMPLATE


def test_cancel_discards_quote_geometry_too() -> None:
    """漏掉的話，按「捨棄」之後 dirty 標記還在，下一次送出會把剛剛丟掉的位置送出去。"""
    fn = TEMPLATE[TEMPLATE.index("function discardAndCloseEditor()") :]
    body = fn[: fn.index("\n}")]
    assert "editorState.layout = null" in body
    assert "editorState.quoteLayout = null" in body
    assert "delete editorState.copyEdits[currentEditorPage.page_id];" in body
