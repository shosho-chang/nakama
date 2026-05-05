// Reader bootstrap — runs under CSP `script-src 'self'`, so this lives in a
// served-from-origin file rather than inline. book_id comes from the URL path
// (/books/{book_id}) so the template needs no per-page injection.

import { View } from '/vendor/foliate-js/view.js';
import { Overlayer } from '/vendor/foliate-js/overlayer.js';

const pathParts = location.pathname.split('/').filter(Boolean);
const BOOK_ID = decodeURIComponent(pathParts[pathParts.length - 1]);
const view = document.getElementById('view');

const DARK_KEY = 'bookReaderDark';
const darkToggle = document.getElementById('darkToggle');

function applyDark(on) {
  document.body.classList.toggle('dark', on);
  darkToggle.setAttribute('aria-pressed', on ? 'true' : 'false');
  darkToggle.textContent = on ? '☀️ 日間' : '🌙 夜間';
  pushReaderStyles();
}

function pushReaderStyles() {
  if (!view.renderer || typeof view.renderer.setStyles !== 'function') return;
  const dark = document.body.classList.contains('dark');
  const css = dark
    ? `html, body { background: #1a1a1a !important; color: #e0e0e0 !important; }
       a, a:visited { color: #9d97ff; }`
    : `html, body { background: #ffffff; color: #1a1a1a; }
       a, a:visited { color: #6c63ff; }`;
  try { view.renderer.setStyles(css); } catch (_) { /* renderer not ready yet */ }
}

const wideMQ = window.matchMedia('(min-width: 1500px)');
function applyColumns() {
  if (!view.renderer) return;
  view.renderer.setAttribute('flow', 'paginated');
  view.renderer.setAttribute('max-column-count', wideMQ.matches ? '2' : '1');
}
wideMQ.addEventListener('change', applyColumns);

// ── Annotation state ──────────────────────────────────────────────────────────
//
// `currentSet` is the canonical AnnotationSetV2 mirrored from the server. Each
// new write (H/A/C) appends to a clone, POSTs full-replace, and on 200 swaps
// in. On non-200 we restore the previous snapshot and toast an error.
//
// `currentChapter` follows the foliate-js `relocate` event. Used as the
// default chapter_ref when opening the C dialog.
//
// `lastSelection` snapshots the selected range + CFI + text the moment the
// user lifts the mouse, so dialogs/modals can read it after the iframe
// selection is lost (modals steal focus).

const ANN_HIGHLIGHT_COLOR = 'yellow';   // H button (highlight)
const ANN_NOTE_COLOR = 'orange';        // A button (annotation w/ note)
const ANN_COMMENT_ANCHOR_COLOR = 'blue'; // C button when cfi_anchor is set

const popup = document.getElementById('ann-popup');
const noteModal = document.getElementById('ann-note-modal');
const noteExcerpt = document.getElementById('ann-note-excerpt');
const noteText = document.getElementById('ann-note-text');
const commentModal = document.getElementById('ann-comment-modal');
const commentChapter = document.getElementById('ann-comment-chapter');
const commentAnchor = document.getElementById('ann-comment-anchor');
const commentBody = document.getElementById('ann-comment-body');
const commentsSidebar = document.getElementById('comments-sidebar');
const commentsList = document.getElementById('comments-list');
const commentsToggle = document.getElementById('commentsToggle');
const commentsClose = document.getElementById('commentsClose');
const toast = document.getElementById('ann-toast');

let currentSet = null;       // AnnotationSetV2 mirror
let bookVersionHash = document.body.dataset.bookVersionHash || '';
let currentChapter = '';     // section.id or section.href, follows relocate
let lastSelection = null;    // { cfi, text, range }

const SIDEBAR_KEY = 'bookReaderSidebarOpen';

// ── Helpers ──────────────────────────────────────────────────────────────────

function nowIso() {
  // Match shared.schemas.annotations._now_iso() format: ISO-8601 with seconds + Z.
  return new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
}

function showToast(message, ms = 3500) {
  toast.textContent = message;
  toast.classList.add('show');
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => toast.classList.remove('show'), ms);
}

function emptyAnnotationSet() {
  return {
    schema_version: 2,
    slug: BOOK_ID,
    book_id: BOOK_ID,
    book_version_hash: bookVersionHash,
    base: 'books',
    items: [],
    updated_at: nowIso(),
    last_synced_at: null,
  };
}

async function fetchBookMetadata() {
  try {
    const r = await fetch(`/api/books/${encodeURIComponent(BOOK_ID)}`);
    if (!r.ok) return;
    const meta = await r.json();
    if (meta.book_version_hash) bookVersionHash = meta.book_version_hash;
    applyIngestState({
      has_original: meta.has_original === true,
      ingest_status: typeof meta.ingest_status === 'string' ? meta.ingest_status : 'never',
    });
  } catch (err) {
    console.warn('book metadata fetch failed', err);
  }
}

async function fetchAnnotations() {
  try {
    const r = await fetch(`/api/books/${encodeURIComponent(BOOK_ID)}/annotations`);
    if (!r.ok) {
      currentSet = emptyAnnotationSet();
      return;
    }
    currentSet = await r.json();
    if (!currentSet.items) currentSet.items = [];
  } catch (err) {
    console.warn('annotations fetch failed', err);
    currentSet = emptyAnnotationSet();
  }
}

async function persistSet(nextSet) {
  // Full-replace POST. Caller passes the new set; on success it becomes the
  // canonical mirror. On failure, the prior snapshot is restored and the
  // caller's UI side-effect is best-effort rolled back via the return value.
  const prior = currentSet;
  currentSet = nextSet;
  try {
    const r = await fetch(
      `/api/books/${encodeURIComponent(BOOK_ID)}/annotations`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(nextSet),
      },
    );
    if (!r.ok) {
      const detail = await r.text().catch(() => '');
      console.error('annotation save failed', r.status, detail);
      currentSet = prior;
      showToast(`儲存失敗 (HTTP ${r.status})`);
      return false;
    }
    return true;
  } catch (err) {
    console.error('annotation save error', err);
    currentSet = prior;
    showToast(`儲存失敗：${String(err.message || err)}`);
    return false;
  }
}

function renderHighlight(item) {
  // Best-effort: invalid CFI throws; bump the broken counter and move on.
  let color = ANN_HIGHLIGHT_COLOR;
  if (item.type === 'annotation') color = ANN_NOTE_COLOR;
  else if (item.type === 'comment') color = ANN_COMMENT_ANCHOR_COLOR;
  try {
    view.addAnnotation({ value: item.cfi || item.cfi_anchor, color });
    return true;
  } catch (err) {
    console.debug('addAnnotation failed for cfi', item.cfi || item.cfi_anchor, err);
    return false;
  }
}

function renderAllExisting() {
  let broken = 0;
  if (!currentSet || !Array.isArray(currentSet.items)) return broken;
  for (const item of currentSet.items) {
    if (item.type === 'highlight' || item.type === 'annotation') {
      if (!renderHighlight(item)) broken += 1;
    } else if (item.type === 'comment') {
      // Only render an overlay if the comment carries a cfi_anchor.
      if (item.cfi_anchor) {
        if (!renderHighlight(item)) broken += 1;
      }
    }
  }
  rebuildCommentsSidebar();
  return broken;
}

function rebuildCommentsSidebar() {
  if (!currentSet) return;
  const comments = currentSet.items.filter(it => it.type === 'comment');
  commentsList.innerHTML = '';
  if (comments.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = '尚無反思。選取文字後按「C 反思」即可新增。';
    commentsList.appendChild(empty);
    return;
  }
  for (const c of comments) {
    const card = document.createElement('div');
    card.className = 'comment-card';
    card.tabIndex = 0;
    card.setAttribute('role', 'button');

    const chap = document.createElement('div');
    chap.className = 'chap';
    chap.textContent = c.chapter_ref || '(無章節)';
    card.appendChild(chap);

    const preview = document.createElement('div');
    preview.className = 'preview';
    const body = c.body || '';
    const collapsed = body.length > 80 ? `${body.slice(0, 80)}…` : body;
    preview.textContent = collapsed;
    card.appendChild(preview);

    let expanded = false;
    const toggle = () => {
      expanded = !expanded;
      preview.textContent = expanded ? body : collapsed;
      card.classList.toggle('expanded', expanded);
    };
    card.addEventListener('click', toggle);
    card.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        toggle();
      }
    });
    commentsList.appendChild(card);
  }
}

function populateChapterSelect() {
  if (!view.book || !view.book.sections) return;
  commentChapter.innerHTML = '';
  view.book.sections.forEach((section, idx) => {
    const opt = document.createElement('option');
    const ref = section.id || section.href || `section-${idx}`;
    opt.value = ref;
    opt.textContent = section.label || section.id || section.href || `Section ${idx + 1}`;
    commentChapter.appendChild(opt);
  });
  if (currentChapter) commentChapter.value = currentChapter;
}

// ── Selection capture ────────────────────────────────────────────────────────
//
// foliate-js fires `load` on `view` with `detail: { doc, index }` each time a
// section's iframe loads. We attach a `pointerup` listener to the doc to
// catch fresh text selections, then position the popup near the selection
// using the iframe's getBoundingClientRect plus the range's client rect.

function hidePopup() {
  popup.hidden = true;
}

function showPopup(rect) {
  // rect is in viewport coords. Place popup just above the selection's top
  // edge; if there's no room, drop to below.
  popup.hidden = false;
  const popW = popup.offsetWidth || 200;
  const popH = popup.offsetHeight || 40;
  let left = rect.left + rect.width / 2 - popW / 2;
  let top = rect.top - popH - 8;
  if (top < 8) top = rect.bottom + 8;
  left = Math.max(8, Math.min(left, window.innerWidth - popW - 8));
  popup.style.left = `${left}px`;
  popup.style.top = `${top}px`;
}

function getRendererSectionIndex(doc) {
  // The renderer keeps a list of mounted contents with { index, doc }. We
  // reverse-lookup the index for `getCFI(index, range)`.
  if (!view.renderer || typeof view.renderer.getContents !== 'function') return -1;
  const list = view.renderer.getContents();
  const found = list.find(c => c.doc === doc);
  return found ? found.index : -1;
}

function attachSelectionListener(doc) {
  doc.addEventListener('pointerup', () => {
    // Defer slightly so the selection settles after the pointerup default.
    setTimeout(() => onIframePointerUp(doc), 0);
  });
}

function onIframePointerUp(doc) {
  const sel = doc.getSelection();
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) {
    hidePopup();
    lastSelection = null;
    return;
  }
  const range = sel.getRangeAt(0);
  const text = sel.toString().trim();
  if (!text) {
    hidePopup();
    lastSelection = null;
    return;
  }
  const index = getRendererSectionIndex(doc);
  let cfi = '';
  try {
    cfi = view.getCFI(index, range);
  } catch (err) {
    console.warn('getCFI failed', err);
  }
  lastSelection = { cfi, text, range };

  // Convert range rect (iframe-local) to viewport coords by adding the
  // iframe's getBoundingClientRect. paginator.js mounts iframes inside its
  // shadow DOM, so we walk up doc.defaultView.frameElement if present.
  let rect = range.getBoundingClientRect();
  const frame = doc.defaultView && doc.defaultView.frameElement;
  if (frame) {
    const fRect = frame.getBoundingClientRect();
    rect = {
      left: rect.left + fRect.left,
      top: rect.top + fRect.top,
      right: rect.right + fRect.left,
      bottom: rect.bottom + fRect.top,
      width: rect.width,
      height: rect.height,
    };
  }
  showPopup(rect);
}

// Hide popup when clicking elsewhere on the host page.
document.addEventListener('pointerdown', e => {
  if (popup.hidden) return;
  if (popup.contains(e.target)) return;
  hidePopup();
});

// ── Action handlers (H / A / C) ─────────────────────────────────────────────

function appendItemAndPersist(item) {
  if (!currentSet) currentSet = emptyAnnotationSet();
  const next = {
    ...currentSet,
    items: [...currentSet.items, item],
    updated_at: nowIso(),
  };
  return persistSet(next);
}

async function actionHighlight() {
  if (!lastSelection || !lastSelection.cfi) {
    showToast('找不到選取位置');
    return;
  }
  const ts = nowIso();
  const item = {
    type: 'highlight',
    cfi: lastSelection.cfi,
    text_excerpt: lastSelection.text,
    book_version_hash: bookVersionHash,
    created_at: ts,
    modified_at: ts,
  };
  hidePopup();
  // Render first; if persist fails, leave the overlay (user may retry).
  try { view.addAnnotation({ value: item.cfi, color: ANN_HIGHLIGHT_COLOR }); } catch (_) { /* ignore */ }
  await appendItemAndPersist(item);
}

function openNoteModal() {
  if (!lastSelection || !lastSelection.cfi) {
    showToast('找不到選取位置');
    return;
  }
  noteExcerpt.textContent = lastSelection.text;
  noteText.value = '';
  hidePopup();
  noteModal.showModal();
  setTimeout(() => noteText.focus(), 0);
}

async function submitNote() {
  const note = noteText.value.trim();
  if (!note) {
    showToast('請輸入註解內容');
    return false;
  }
  if (!lastSelection || !lastSelection.cfi) {
    showToast('找不到選取位置');
    return false;
  }
  const ts = nowIso();
  const item = {
    type: 'annotation',
    cfi: lastSelection.cfi,
    text_excerpt: lastSelection.text,
    note,
    book_version_hash: bookVersionHash,
    created_at: ts,
    modified_at: ts,
  };
  try { view.addAnnotation({ value: item.cfi, color: ANN_NOTE_COLOR }); } catch (_) { /* ignore */ }
  await appendItemAndPersist(item);
  return true;
}

function openCommentModal() {
  populateChapterSelect();
  commentBody.value = '';
  commentAnchor.checked = false;
  commentAnchor.disabled = !(lastSelection && lastSelection.cfi);
  hidePopup();
  commentModal.showModal();
  setTimeout(() => commentBody.focus(), 0);
}

async function submitComment() {
  const body = commentBody.value.trim();
  if (!body) {
    showToast('請輸入反思內容');
    return false;
  }
  const chapterRef = commentChapter.value || currentChapter || '';
  const anchor = commentAnchor.checked && lastSelection && lastSelection.cfi
    ? lastSelection.cfi
    : null;
  const ts = nowIso();
  const item = {
    type: 'comment',
    chapter_ref: chapterRef,
    cfi_anchor: anchor,
    body,
    book_version_hash: bookVersionHash,
    created_at: ts,
    modified_at: ts,
  };
  if (anchor) {
    try { view.addAnnotation({ value: anchor, color: ANN_COMMENT_ANCHOR_COLOR }); } catch (_) { /* ignore */ }
  }
  const ok = await appendItemAndPersist(item);
  if (ok) rebuildCommentsSidebar();
  return ok;
}

// Popup button delegation
popup.addEventListener('click', e => {
  const btn = e.target.closest('button[data-action]');
  if (!btn) return;
  const action = btn.dataset.action;
  if (action === 'highlight') actionHighlight();
  else if (action === 'annotation') openNoteModal();
  else if (action === 'comment') openCommentModal();
});

// Modal cancel buttons
noteModal.querySelector('[data-cancel]').addEventListener('click', () => noteModal.close('cancel'));
commentModal.querySelector('[data-cancel]').addEventListener('click', () => commentModal.close('cancel'));

// Modal submit handlers — intercept the dialog's submit so we can validate
// and POST before the dialog closes.
noteModal.querySelector('form').addEventListener('submit', async e => {
  e.preventDefault();
  const ok = await submitNote();
  if (ok) noteModal.close('save');
});
commentModal.querySelector('form').addEventListener('submit', async e => {
  e.preventDefault();
  const ok = await submitComment();
  if (ok) commentModal.close('save');
});

// Sidebar toggles
function setSidebarOpen(open) {
  commentsSidebar.hidden = !open;
  commentsToggle.setAttribute('aria-pressed', open ? 'true' : 'false');
  localStorage.setItem(SIDEBAR_KEY, open ? '1' : '0');
}
commentsToggle.addEventListener('click', () => {
  setSidebarOpen(commentsSidebar.hidden);
});
commentsClose.addEventListener('click', () => setSidebarOpen(false));

// ── Ingest button (Slice 4D) ─────────────────────────────────────────────────
//
// Reader-side trigger for the whole-book ingest pipeline (Slices 4A–4C). The
// button is gated on `has_original` — uploads without an EN original cannot be
// ingested, so we surface an inline tooltip instead of a silent disabled state.
// On 200 we lock the button into the "Queued" state; the badge on the library
// page is the source of truth for downstream status (ingesting / ingested /
// partial / failed). Single user, manual refresh — no polling here.

const ingestBtn = document.getElementById('ingestBtn');
const ingestWrap = document.getElementById('ingestWrap');

function applyIngestState({ has_original, ingest_status }) {
  if (!ingestBtn || !ingestWrap) return;
  ingestBtn.classList.remove('is-queued');
  if (!has_original) {
    ingestBtn.disabled = true;
    ingestBtn.textContent = '📥 Ingest 整本書';
    ingestWrap.setAttribute('data-disabled-reason', '上傳 EN 原檔以啟用 ingest');
    return;
  }
  ingestWrap.removeAttribute('data-disabled-reason');
  if (ingest_status === 'queued' || ingest_status === 'ingesting') {
    ingestBtn.disabled = true;
    ingestBtn.classList.add('is-queued');
    ingestBtn.textContent = ingest_status === 'ingesting' ? '📥 Ingesting' : '📥 Queued';
    return;
  }
  if (ingest_status === 'ingested') {
    ingestBtn.disabled = true;
    ingestBtn.classList.add('is-queued');
    ingestBtn.textContent = '📥 Ingested';
    return;
  }
  if (ingest_status === 'partial' || ingest_status === 'failed') {
    ingestBtn.disabled = false;
    ingestBtn.textContent = ingest_status === 'partial' ? '📥 重試 ingest' : '📥 重試 ingest';
    return;
  }
  ingestBtn.disabled = false;
  ingestBtn.textContent = '📥 Ingest 整本書';
}

const ingestConfirmModal = document.getElementById('ingest-confirm-modal');
if (ingestConfirmModal) {
  ingestConfirmModal.querySelector('[data-cancel]')
    .addEventListener('click', () => ingestConfirmModal.close('cancel'));
}

async function postIngestRequest() {
  if (!ingestBtn) return;
  const prevText = ingestBtn.textContent;
  ingestBtn.disabled = true;
  ingestBtn.textContent = '📥 送出中⋯';
  try {
    const r = await fetch(
      `/api/books/${encodeURIComponent(BOOK_ID)}/ingest-request`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' } },
    );
    if (!r.ok) {
      const detail = await r.text().catch(() => '');
      console.error('ingest request failed', r.status, detail);
      showToast(`Ingest 送出失敗 (HTTP ${r.status})`);
      ingestBtn.disabled = false;
      ingestBtn.textContent = prevText;
      return;
    }
    ingestBtn.classList.add('is-queued');
    ingestBtn.textContent = '📥 Queued';
  } catch (err) {
    console.error('ingest request error', err);
    showToast(`Ingest 送出失敗：${String(err.message || err)}`);
    ingestBtn.disabled = false;
    ingestBtn.textContent = prevText;
  }
}

function requestIngest() {
  if (!ingestConfirmModal) {
    // Fallback if the template hasn't been updated — still gate behind a
    // native confirm() so a stale page can't fire a 30-minute job by mistake.
    if (!confirm('送出 Ingest 請求？實際執行需在桌機 Claude Code 跑 /textbook-ingest --from-queue。')) return;
    postIngestRequest();
    return;
  }
  ingestConfirmModal.returnValue = '';
  ingestConfirmModal.showModal();
}

if (ingestConfirmModal) {
  ingestConfirmModal.addEventListener('close', () => {
    if (ingestConfirmModal.returnValue === 'confirm') postIngestRequest();
  });
}

if (ingestBtn) {
  ingestBtn.addEventListener('click', requestIngest);
}

// ── Progress state (Slice 3C) ────────────────────────────────────────────────
//
// Mirrors GET/PUT /api/books/{id}/progress with three reliability layers:
//
// 1. 5-second debounce on `relocate` for normal page-flips — coalesces rapid
//    bursts (the user paging through 10 spreads in 5s = 1 PUT, not 10).
// 2. Synchronous flush on visibilitychange(hidden) and beforeunload, the
//    latter via sendBeacon so the request survives the tab dying.
// 3. localStorage["book-progress-{id}"] mirrors every successful PUT. If the
//    PUT fails we keep the cached payload so the next relocate-burst retries.
//    Multi-tab discipline: on each PUT we re-read localStorage and only keep
//    our snapshot if our updated_at is >= the cached updated_at; otherwise
//    another tab is ahead and we skip writing this round.

const PROGRESS_KEY = `book-progress-${BOOK_ID}`;
const PROGRESS_DEBOUNCE_MS = 5000;
const READING_GAP_CAP_S = 60;

const progressBarEl = document.getElementById('reader-progress');
const progressBarFill = progressBarEl ? progressBarEl.querySelector('.bar') : null;

let currentProgress = null;       // last BookProgress sent / cached
let pendingProgress = null;       // next BookProgress to send (latest wins)
let progressDebounceTimer = null;
let lastRelocateAt = 0;           // wall-clock ms of last relocate, for reading-time delta
let totalReadingSeconds = 0;

function readProgressCache() {
  try {
    const raw = localStorage.getItem(PROGRESS_KEY);
    if (!raw) return null;
    const obj = JSON.parse(raw);
    if (!obj || obj.book_id !== BOOK_ID) return null;
    return obj;
  } catch (_) {
    return null;
  }
}

function writeProgressCache(payload) {
  try {
    localStorage.setItem(PROGRESS_KEY, JSON.stringify(payload));
  } catch (_) { /* quota / private mode — non-fatal */ }
}

function updateProgressBar(percent) {
  if (!progressBarEl || !progressBarFill) return;
  const pct = Math.max(0, Math.min(1, Number.isFinite(percent) ? percent : 0));
  progressBarFill.style.width = `${(pct * 100).toFixed(2)}%`;
  progressBarEl.dataset.state = pct > 0 ? 'reading' : 'empty';
  progressBarEl.setAttribute('aria-valuenow', String(Math.round(pct * 100)));
}

function buildProgressFromRelocate(detail) {
  const tocHref = detail.tocItem && detail.tocItem.href ? detail.tocItem.href : null;
  let chapterRef = tocHref;
  if (!chapterRef && typeof detail.index === 'number' && view.book && view.book.sections) {
    const sec = view.book.sections[detail.index];
    if (sec) chapterRef = sec.id || sec.href || `section-${detail.index}`;
  }
  const fraction = typeof detail.fraction === 'number' ? detail.fraction : 0;
  const cfi = typeof detail.cfi === 'string' ? detail.cfi : null;
  const spreadIdx = typeof detail.index === 'number' ? detail.index : 0;

  const now = Date.now();
  if (lastRelocateAt > 0) {
    const deltaS = Math.min(READING_GAP_CAP_S, Math.max(0, (now - lastRelocateAt) / 1000));
    totalReadingSeconds += Math.round(deltaS);
  }
  lastRelocateAt = now;

  return {
    book_id: BOOK_ID,
    last_cfi: cfi,
    last_chapter_ref: chapterRef,
    last_spread_idx: spreadIdx,
    percent: Math.max(0, Math.min(1, fraction)),
    total_reading_seconds: totalReadingSeconds,
    updated_at: nowIso(),
  };
}

async function putProgress(payload) {
  // Returns true on 2xx, false otherwise. Caller decides retry policy.
  try {
    const r = await fetch(
      `/api/books/${encodeURIComponent(BOOK_ID)}/progress`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
    );
    if (!r.ok) {
      console.warn('progress PUT failed', r.status);
      return false;
    }
    return true;
  } catch (err) {
    console.warn('progress PUT error', err);
    return false;
  }
}

async function flushProgress() {
  if (!pendingProgress) return;
  const payload = pendingProgress;
  pendingProgress = null;
  if (progressDebounceTimer) {
    clearTimeout(progressDebounceTimer);
    progressDebounceTimer = null;
  }

  // Multi-tab: if another tab wrote a newer snapshot while we waited, defer to it.
  const cached = readProgressCache();
  if (cached && cached.updated_at && cached.updated_at > payload.updated_at) {
    currentProgress = cached;
    return;
  }

  const ok = await putProgress(payload);
  if (ok) {
    currentProgress = payload;
    writeProgressCache(payload);
  } else {
    // Keep payload as pending so the next relocate-burst retries it (but with
    // a fresher updated_at). Also leave the prior cache untouched.
    pendingProgress = payload;
  }
}

function flushProgressSync() {
  // Used by visibilitychange(hidden) and beforeunload. Prefers sendBeacon so
  // the request survives the tab dying; falls back to fetch+keepalive.
  if (!pendingProgress) return;
  const payload = pendingProgress;
  pendingProgress = null;
  if (progressDebounceTimer) {
    clearTimeout(progressDebounceTimer);
    progressDebounceTimer = null;
  }

  const cached = readProgressCache();
  if (cached && cached.updated_at && cached.updated_at > payload.updated_at) {
    currentProgress = cached;
    return;
  }

  const url = `/api/books/${encodeURIComponent(BOOK_ID)}/progress`;
  const body = JSON.stringify(payload);
  let queued = false;
  try {
    if (navigator.sendBeacon) {
      const blob = new Blob([body], { type: 'application/json' });
      queued = navigator.sendBeacon(url, blob);
    }
  } catch (_) { /* fall through to fetch */ }

  if (!queued) {
    try {
      fetch(url, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body,
        keepalive: true,
      }).catch(() => { /* best-effort during unload */ });
    } catch (_) { /* ignore */ }
  }

  // Optimistically mirror to localStorage even if we didn't await — sendBeacon
  // gives us no completion signal, so cache is our local source of truth.
  currentProgress = payload;
  writeProgressCache(payload);
}

function scheduleProgressWrite(payload) {
  pendingProgress = payload;
  if (progressDebounceTimer) clearTimeout(progressDebounceTimer);
  progressDebounceTimer = setTimeout(() => {
    progressDebounceTimer = null;
    flushProgress();
  }, PROGRESS_DEBOUNCE_MS);
}

async function fetchProgress() {
  // Returns BookProgress on success, or the localStorage cache on failure, or null.
  try {
    const r = await fetch(`/api/books/${encodeURIComponent(BOOK_ID)}/progress`);
    if (!r.ok) {
      console.warn('progress GET failed', r.status);
      return readProgressCache();
    }
    return await r.json();
  } catch (err) {
    console.warn('progress GET error', err);
    return readProgressCache();
  }
}

async function restoreProgress(progress) {
  // Try last_cfi first; on throw OR on goTo rejection, fall back to chapter
  // ref; on full failure, stay at page 0 and warn.
  if (!progress) return;
  totalReadingSeconds = progress.total_reading_seconds || 0;
  updateProgressBar(progress.percent || 0);

  if (progress.last_cfi) {
    try {
      await view.goTo(progress.last_cfi);
      return;
    } catch (err) {
      console.warn('restore: last_cfi failed, falling back to chapter', err);
    }
  }
  if (progress.last_chapter_ref) {
    try {
      await view.goTo(progress.last_chapter_ref);
      return;
    } catch (err) {
      console.warn('restore: last_chapter_ref failed, staying at page 0', err);
    }
  }
}

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') flushProgressSync();
});
window.addEventListener('beforeunload', () => {
  flushProgressSync();
});
window.addEventListener('pagehide', () => {
  // pagehide fires on bfcache-eligible navigations where beforeunload doesn't.
  flushProgressSync();
});

// ── Page navigation (keyboard) ───────────────────────────────────────────────
//
// <foliate-view> doesn't bind nav keys; the upstream demo
// (vendor/foliate-js/reader.js:138,191-192) wires its own keydown handler on
// both the host document and each iframe doc as it loads. Without this the
// first page renders but ←/→ does nothing on desktop. Skip when a modal is
// open or when typing in a form field, and stay out of the way of selection
// extension (Shift+Arrow) and browser shortcuts (Ctrl/Cmd/Alt combos).
function handleNavKey(e) {
  if (document.querySelector('dialog[open]')) return;
  const ae = document.activeElement;
  if (ae && (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA' || ae.tagName === 'SELECT' || ae.isContentEditable)) return;
  if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
  const k = e.key;
  if (k === 'ArrowLeft' || k === 'PageUp') {
    e.preventDefault();
    view.goLeft();
  } else if (k === 'ArrowRight' || k === 'PageDown' || k === ' ') {
    e.preventDefault();
    view.goRight();
  }
}
document.addEventListener('keydown', handleNavKey);

// ── view event wiring ────────────────────────────────────────────────────────

view.addEventListener('load', e => {
  const doc = e.detail && e.detail.doc;
  if (doc) {
    attachSelectionListener(doc);
    // Mirror the host-level keydown into each iframe doc so keys still work
    // when focus is inside the EPUB content (foliate-js demo does the same in
    // vendor/foliate-js/reader.js:195).
    doc.addEventListener('keydown', handleNavKey);
  }
});

view.addEventListener('relocate', e => {
  const detail = e.detail || {};
  // Prefer section.id, fall back to TOC item href, then to section index.
  if (detail.tocItem && detail.tocItem.href) {
    currentChapter = detail.tocItem.href;
  } else if (typeof detail.index === 'number' && view.book && view.book.sections) {
    const sec = view.book.sections[detail.index];
    if (sec) currentChapter = sec.id || sec.href || `section-${detail.index}`;
  }

  const payload = buildProgressFromRelocate(detail);
  updateProgressBar(payload.percent);
  scheduleProgressWrite(payload);
});

// foliate-js requires a `draw-annotation` listener for our addAnnotation calls
// to produce visible overlays — without it, the renderer fires the event but
// no overlay is drawn. Map our color tokens onto the Overlayer.highlight draw
// function via the `draw(func, opts)` helper foliate-js hands us.
view.addEventListener('draw-annotation', e => {
  const { draw, annotation } = e.detail;
  draw(Overlayer.highlight, { color: annotation.color || ANN_HIGHLIGHT_COLOR });
});

// ── Boot ─────────────────────────────────────────────────────────────────────

(async () => {
  try {
    const res = await fetch(`/api/books/${encodeURIComponent(BOOK_ID)}/file?lang=bilingual`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const blob = await res.blob();
    const file = new File([blob], `${BOOK_ID}.epub`, { type: 'application/epub+zip' });
    await view.open(file);
    applyColumns();
    pushReaderStyles();
    // foliate-js view.open() doesn't auto-render the first page — the demo
    // (vendor/foliate-js/reader.js) calls renderer.next() right after open
    // to kick off pagination. Without this the paginator sits idle and the
    // reader shell stays blank.
    if (view.renderer && typeof view.renderer.next === 'function') {
      try { await view.renderer.next(); } catch (_) { /* first-page nav noop */ }
    }

    // Restore reading position before annotation work so we don't paint
    // overlays for a page the reader is about to leave. fetchProgress falls
    // back to localStorage on network failure.
    const progress = await fetchProgress();
    if (progress) {
      currentProgress = progress;
      await restoreProgress(progress);
      // Reset the wall-clock anchor — time spent away from the reader between
      // sessions shouldn't count toward total_reading_seconds.
      lastRelocateAt = 0;
    }

    // Load metadata + annotations after the book opens so sections are
    // available for chapter <select> population.
    await fetchBookMetadata();
    await fetchAnnotations();

    if (currentSet && currentSet.book_version_hash &&
        bookVersionHash && currentSet.book_version_hash !== bookVersionHash) {
      const broken = renderAllExisting();
      console.warn(
        `book version mismatch: stored=${currentSet.book_version_hash.slice(0, 8)} ` +
        `current=${bookVersionHash.slice(0, 8)} — ${broken} annotations may have stale CFI anchors`,
      );
    } else {
      const broken = renderAllExisting();
      if (broken > 0) {
        console.warn(`${broken} annotations failed to render (stale or invalid CFI)`);
      }
    }

    // Restore sidebar visibility from previous session.
    const sidebarOpen = localStorage.getItem(SIDEBAR_KEY) === '1';
    setSidebarOpen(sidebarOpen);
  } catch (err) {
    const shell = document.querySelector('.reader-shell');
    if (shell) {
      shell.innerHTML = `<div class="error-banner">無法載入書籍：${String(err.message || err)}</div>`;
    }
  }
})();

const initialDark = localStorage.getItem(DARK_KEY) === '1';
applyDark(initialDark);

darkToggle.addEventListener('click', () => {
  const next = !document.body.classList.contains('dark');
  localStorage.setItem(DARK_KEY, next ? '1' : '0');
  applyDark(next);
});
