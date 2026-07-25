// Pre-ingest confirm modal. Runs under CSP `script-src 'self'` (served-from-
// origin file, no inline). Any form marked `data-ingest-confirm` gets its submit
// intercepted: we fetch GET /robin/estimate for the time estimate, show a modal,
// and only POST the real ingest when the user clicks 確定. Shared by the three
// ingest surfaces (book / article / video readers) — each tags its form with:
//   data-ingest-confirm
//   data-est-type="book|video|article"   → estimate source_type
//   data-est-id-field="book_id|video_id|filename"  → which input holds the id
//   data-est-name="<human title>"        → shown in the modal (optional)
(function () {
  'use strict';

  let dialog = null;
  let els = null;

  function build() {
    if (dialog) return;
    dialog = document.createElement('dialog');
    dialog.className = 'ingest-confirm';
    dialog.setAttribute('aria-labelledby', 'ic-head');
    dialog.innerHTML =
      '<div class="ic-head" id="ic-head">確認 Ingest</div>' +
      '<div class="ic-body">' +
      '<p class="ic-src" data-src></p>' +
      '<div class="ic-est" data-est>' +
      '<span class="ic-spinner" data-spinner aria-hidden="true"></span>' +
      '<span data-est-text>估算所需時間中…</span>' +
      '</div>' +
      '<p class="ic-detail" data-detail></p>' +
      '<p class="ic-note">會跑完整摘要 → 分析概念 → 寫入知識庫 → 開卡建議。過程中可在進度頁按「取消」。</p>' +
      '</div>' +
      '<div class="ic-actions">' +
      '<button type="button" class="ic-cancel" data-cancel>取消</button>' +
      '<button type="button" class="ic-confirm" data-confirm>確定執行</button>' +
      '</div>';
    document.body.appendChild(dialog);
    els = {
      src: dialog.querySelector('[data-src]'),
      est: dialog.querySelector('[data-est]'),
      spinner: dialog.querySelector('[data-spinner]'),
      estText: dialog.querySelector('[data-est-text]'),
      detail: dialog.querySelector('[data-detail]'),
      cancel: dialog.querySelector('[data-cancel]'),
      confirm: dialog.querySelector('[data-confirm]'),
    };
    els.cancel.addEventListener('click', function () { dialog.close('cancel'); });
  }

  async function open(form) {
    build();
    const type = form.dataset.estType || 'article';
    const idField = form.dataset.estIdField || 'filename';
    const idEl = form.querySelector('[name="' + idField + '"]');
    const idVal = idEl ? idEl.value : '';
    const name = form.dataset.estName || idVal;

    // Reset to the loading state for each open.
    els.src.textContent = name ? ('來源：' + name) : '';
    els.detail.textContent = '';
    els.est.classList.remove('is-error');
    els.spinner.style.display = '';
    els.estText.textContent = '估算所需時間中…';

    // Confirm submits the real form. form.submit() does NOT fire the submit
    // event, so our capture-phase interceptor won't re-open the modal.
    function onConfirm() { cleanup(); dialog.close(); form.submit(); }
    function cleanup() { els.confirm.removeEventListener('click', onConfirm); }
    els.confirm.addEventListener('click', onConfirm);
    dialog.addEventListener('close', cleanup, { once: true });

    dialog.showModal();
    els.confirm.focus();

    try {
      const url = '/robin/estimate?source_type=' + encodeURIComponent(type)
        + '&source_id=' + encodeURIComponent(idVal);
      const r = await fetch(url, { headers: { Accept: 'application/json' } });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      els.spinner.style.display = 'none';
      els.estText.textContent = '預計 ' + (data.time_label || '時間未知');
      els.detail.textContent = data.detail || '';
    } catch (err) {
      // Estimate failed — the user can still run the ingest, just without a number.
      els.spinner.style.display = 'none';
      els.est.classList.add('is-error');
      els.estText.textContent = '估算失敗，仍可直接執行';
    }
  }

  document.addEventListener('submit', function (e) {
    const form = e.target;
    if (!form || typeof form.matches !== 'function') return;
    if (!form.matches('[data-ingest-confirm]')) return;
    e.preventDefault();
    open(form);
  }, true);
})();
