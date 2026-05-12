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
const commentAnchorRow = document.getElementById('ann-comment-anchor-row');
const commentBody = document.getElementById('ann-comment-body');
const commentsSidebar = document.getElementById('comments-sidebar');
const commentsList = document.getElementById('comments-list');
const commentsToggle = document.getElementById('commentsToggle');
const commentsClose = document.getElementById('commentsClose');
const addCommentBtn = document.getElementById('addCommentBtn');
const toast = document.getElementById('ann-toast');

// Annotation detail bubble (δ.1) — appears when user clicks an annotated
// span. Reuses the foliate-js ``show-annotation`` event so we don't have
// to re-implement hit testing.
const annBubble = document.getElementById('ann-bubble');
const annBubbleClose = document.getElementById('annBubbleClose');
const annBubbleKind = annBubble ? annBubble.querySelector('[data-kind]') : null;
const annBubbleExcerpt = annBubble ? annBubble.querySelector('[data-excerpt]') : null;
const annBubbleNote = annBubble ? annBubble.querySelector('[data-note]') : null;
const annBubbleMeta = annBubble ? annBubble.querySelector('[data-meta]') : null;

// Reading-progress footer (δ.2).
const rpfChapter = document.getElementById('rpfChapter');
const rpfPosition = document.getElementById('rpfPosition');
const rpfPercent = document.getElementById('rpfPercent');

// section.href → real chapter label, walked from view.book.toc once the
// EPUB opens. Falls back to section.id / href when TOC is incomplete (δ.3).
const _chapterLabelByHref = new Map();

// ζ.5 — TOC ancestry. ``_chapterEntries`` is the flat list of "chapter-
// level" TOC entries (heuristic: depth 1 if Parts exist at depth 0, else
// depth 0). ``_chapterIndexByHref`` maps every section.href in a chapter's
// subtree → that chapter's index in the flat list. Used by the progress
// footer to show "第 X / Y 章" and the right chapter title even when
// foliate-js's deepest tocItem is a sub-heading like "支柱一：身分".
let _chapterEntries = [];
const _chapterIndexByHref = new Map();

let currentSet = null;       // AnnotationSetV2 mirror
let bookVersionHash = document.body.dataset.bookVersionHash || '';
let currentChapter = '';     // section.id or section.href, follows relocate
let lastSelection = null;    // { cfi, text, range }

const SIDEBAR_KEY = 'bookReaderSidebarOpen';
const TOC_SIDEBAR_KEY = 'bookReaderTocSidebarOpen';

// TOC sidebar — populated from ``_chapterEntries`` once view.book.toc is
// walked. Highlights the chapter that contains the current reading position.
const tocSidebar = document.getElementById('toc-sidebar');
const tocToggle = document.getElementById('tocToggle');
const tocClose = document.getElementById('tocClose');
const tocList = document.getElementById('toc-list');
let _currentChapterIdx = -1;

function renderTocSidebar() {
  if (!tocList) return;
  tocList.innerHTML = '';
  if (!_chapterEntries || _chapterEntries.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = '這本書沒有可顯示的目錄。';
    tocList.appendChild(empty);
    return;
  }
  _chapterEntries.forEach((entry, idx) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'toc-item';
    btn.dataset.idx = String(idx);
    const num = document.createElement('span');
    num.className = 'toc-num';
    num.textContent = String(idx + 1).padStart(2, '0');
    const label = document.createElement('span');
    label.className = 'toc-label';
    label.textContent = (entry.label || entry.href || `Chapter ${idx + 1}`).trim();
    btn.appendChild(num);
    btn.appendChild(label);
    btn.addEventListener('click', async () => {
      const href = entry.href;
      if (!href) return;
      setTocSidebarOpen(false);
      try {
        await view.goTo(href);
      } catch (err) {
        console.warn('toc: goTo failed', href, err);
      }
    });
    tocList.appendChild(btn);
  });
  updateTocCurrent(_currentChapterIdx);
}

function updateTocCurrent(idx) {
  _currentChapterIdx = idx;
  if (!tocList) return;
  const items = tocList.querySelectorAll('.toc-item');
  items.forEach((el) => {
    const i = Number(el.dataset.idx);
    if (i === idx) {
      el.classList.add('is-current');
      el.setAttribute('aria-current', 'true');
    } else {
      el.classList.remove('is-current');
      el.removeAttribute('aria-current');
    }
  });
  // Scroll the current chapter into view if the sidebar is open.
  if (tocSidebar && !tocSidebar.hidden) {
    const cur = tocList.querySelector('.toc-item.is-current');
    if (cur && typeof cur.scrollIntoView === 'function') {
      cur.scrollIntoView({ block: 'nearest' });
    }
  }
}

function setTocSidebarOpen(open) {
  if (!tocSidebar || !tocToggle) return;
  tocSidebar.hidden = !open;
  tocToggle.setAttribute('aria-pressed', open ? 'true' : 'false');
  localStorage.setItem(TOC_SIDEBAR_KEY, open ? '1' : '0');
  if (open) updateTocCurrent(_currentChapterIdx);
}

if (tocToggle) {
  tocToggle.addEventListener('click', () => {
    setTocSidebarOpen(tocSidebar.hidden);
  });
}
if (tocClose) {
  tocClose.addEventListener('click', () => setTocSidebarOpen(false));
}

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

// ADR-021 §1: ``comment`` (v2) was renamed to ``reflection`` (v3). Treat them as
// equivalent here so a v3 GET round-trip renders the sidebar + chapter overlays
// without any wire-format change on the popup write path.
function isReflection(item) {
  return item.type === 'reflection' || item.type === 'comment';
}

function renderHighlight(item) {
  // Best-effort: invalid CFI throws; bump the broken counter and move on.
  let color = ANN_HIGHLIGHT_COLOR;
  if (item.type === 'annotation') color = ANN_NOTE_COLOR;
  else if (isReflection(item)) color = ANN_COMMENT_ANCHOR_COLOR;
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
    } else if (isReflection(item)) {
      // Only render an overlay if the reflection carries a cfi_anchor.
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
  // v3 ``reflection`` + legacy v2 ``comment`` both feed the chapter-reflection sidebar.
  const comments = currentSet.items.filter(isReflection);
  commentsList.innerHTML = '';
  if (comments.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = '尚無反思。點上方 + 新增章節反思，或選取文字後按「C 反思」錨定到具體段落。';
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
    // δ.3 / ε.3 — robust chapter label lookup tries multiple href key shapes
    // (full path / basename / decoded) before falling back to the raw ref.
    chap.textContent = _labelForChapterRef(c.chapter_ref);
    card.appendChild(chap);

    const preview = document.createElement('div');
    preview.className = 'preview';
    const body = c.body || '';
    // ε.4 — sidebar always shows the same fixed-length preview; the full
    // body lives behind the click → modal. The previous toggle-in-place
    // pattern made the card grow unboundedly inside the 360-px sidebar.
    preview.textContent = body.length > 80 ? `${body.slice(0, 80)}…` : body;
    card.appendChild(preview);

    const open = () => openReflectionModal(c);
    card.addEventListener('click', open);
    card.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        open();
      }
    });
    commentsList.appendChild(card);
  }
}

// ε.4 — reflection full-text viewer. Sidebar cards now open this modal on
// click; the previous inline expand is replaced because a multi-paragraph
// reflection is unreadable in the 360-px sidebar width.
const reflectionModal = document.getElementById('reflection-modal');
const reflectionModalChapter = document.getElementById('reflectionModalChapter');
const reflectionModalTime = document.getElementById('reflectionModalTime');
const reflectionModalBody = document.getElementById('reflectionModalBody');

function openReflectionModal(item) {
  if (!reflectionModal) return;
  reflectionModalChapter.textContent = _labelForChapterRef(item.chapter_ref);
  reflectionModalTime.textContent = item.created_at || '';
  reflectionModalBody.textContent = item.body || '';
  reflectionModal.showModal();
}

// δ.3 / ε.3 — walk view.book.toc (recursive children) and build a href →
// label map. EPUB TOC hrefs and spine section hrefs frequently disagree on
// directory prefix (TOC uses paths relative to the OPF, spine sections may
// be absolute or include the OEBPS/ prefix), so we index three keys per
// TOC entry: the bare path, the basename, and the percent-decoded path.
// Lookup tries full path → basename → decoded path before giving up.
function _hrefKeys(rawHref) {
  if (!rawHref) return [];
  const bare = rawHref.split('#')[0];
  const keys = new Set([bare]);
  // Strip leading directory segments → basename. Tolerates both / and \.
  const base = bare.split(/[\\/]/).pop();
  if (base) keys.add(base);
  // Some EPUBs URL-encode TOC hrefs but spine sections come back decoded.
  try {
    const decoded = decodeURIComponent(bare);
    keys.add(decoded);
    const decodedBase = decoded.split(/[\\/]/).pop();
    if (decodedBase) keys.add(decodedBase);
  } catch (_) { /* invalid % escape — ignore */ }
  return [...keys];
}

function _buildChapterLabelMap() {
  _chapterLabelByHref.clear();
  _chapterEntries = [];
  _chapterIndexByHref.clear();
  if (!view.book || !view.book.toc) return;

  // First pass: build the flat "chapter level" list.
  // Heuristic: if any depth-0 entry has children, treat depth-0 as Parts
  // and depth-1 as Chapters. Otherwise depth-0 IS the chapter level.
  const top = view.book.toc;
  const hasParts = top.some(e => e.subitems && e.subitems.length);
  const chapterDepth = hasParts ? 1 : 0;

  const collectChapterEntries = (entries, depth) => {
    for (const entry of entries || []) {
      if (depth === chapterDepth) _chapterEntries.push(entry);
      if (entry.subitems && entry.subitems.length) {
        collectChapterEntries(entry.subitems, depth + 1);
      }
    }
  };
  collectChapterEntries(top, 0);

  // Second pass: full label map (deepest-leaf-first-wins for backward
  // compat with the inline reflection-card title), AND href → chapter
  // index map (every href in a chapter's subtree maps to that chapter).
  const walkLabel = (entries) => {
    for (const entry of entries || []) {
      if (entry.href && entry.label) {
        const label = entry.label.trim();
        for (const key of _hrefKeys(entry.href)) {
          if (!_chapterLabelByHref.has(key)) _chapterLabelByHref.set(key, label);
        }
      }
      if (entry.subitems && entry.subitems.length) walkLabel(entry.subitems);
    }
  };
  walkLabel(top);

  _chapterEntries.forEach((chapter, chapterIdx) => {
    const collectHrefs = (entry) => {
      if (entry.href) {
        for (const key of _hrefKeys(entry.href)) {
          if (!_chapterIndexByHref.has(key)) _chapterIndexByHref.set(key, chapterIdx);
        }
      }
      if (entry.subitems) entry.subitems.forEach(collectHrefs);
    };
    collectHrefs(chapter);
  });

  // Diagnostic — surfaces in browser console when TOC matching fails so
  // the failure mode is visible without re-running the build.
  console.debug(
    'chapter label map built',
    {
      labels: _chapterLabelByHref.size,
      chapters: _chapterEntries.length,
      hasParts,
      chapterDepth,
      chapter_titles: _chapterEntries.map(c => c.label),
    },
  );
}

// ζ.5 — given the current relocate detail, find which chapter the user is
// in. Walks up tocItem ancestry by matching href to ``_chapterIndexByHref``.
function _resolveCurrentChapter(detail) {
  // Try the deepest tocItem first; fall back to the section href via the
  // index map.
  const candidates = [];
  if (detail.tocItem && detail.tocItem.href) candidates.push(detail.tocItem.href);
  if (view.book && view.book.sections && detail.section
      && typeof detail.section.current === 'number') {
    const sec = view.book.sections[detail.section.current];
    if (sec && sec.href) candidates.push(sec.href);
    if (sec && sec.id) candidates.push(sec.id);
  }
  for (const href of candidates) {
    for (const key of _hrefKeys(href)) {
      if (_chapterIndexByHref.has(key)) {
        const idx = _chapterIndexByHref.get(key);
        return { idx, entry: _chapterEntries[idx] };
      }
    }
  }
  return null;
}

function _chapterLabelFor(section, idx) {
  for (const key of _hrefKeys(section.href || section.id || '')) {
    if (_chapterLabelByHref.has(key)) return _chapterLabelByHref.get(key);
  }
  return section.label || section.id || section.href || `Section ${idx + 1}`;
}

function _labelForChapterRef(rawRef) {
  for (const key of _hrefKeys(rawRef || '')) {
    if (_chapterLabelByHref.has(key)) return _chapterLabelByHref.get(key);
  }
  return rawRef || '(無章節)';
}

function populateChapterSelect() {
  // ζ.2 — list only TOC chapter-level entries (skip spine sections that
  // don't correspond to a TOC chapter, like sub-heading anchors or back
  // matter without a TOC entry). Falls back to all sections when TOC
  // resolution failed (rare but defensive).
  commentChapter.innerHTML = '';
  if (_chapterEntries.length > 0) {
    _chapterEntries.forEach((chapter) => {
      const opt = document.createElement('option');
      opt.value = (chapter.href || '').split('#')[0];
      opt.textContent = (chapter.label || chapter.href || 'Chapter').trim();
      commentChapter.appendChild(opt);
    });
  } else if (view.book && view.book.sections) {
    view.book.sections.forEach((section, idx) => {
      const opt = document.createElement('option');
      const ref = section.id || section.href || `section-${idx}`;
      opt.value = ref;
      opt.textContent = _chapterLabelFor(section, idx);
      commentChapter.appendChild(opt);
    });
  }

  // Pre-select the chapter the user is currently reading. ``currentChapter``
  // tracks the section href, but the dropdown values are TOC chapter hrefs
  // — match them via the chapter index map.
  if (currentChapter) {
    let matchedHref = null;
    for (const key of _hrefKeys(currentChapter)) {
      if (_chapterIndexByHref.has(key)) {
        const idx = _chapterIndexByHref.get(key);
        const ch = _chapterEntries[idx];
        if (ch && ch.href) { matchedHref = ch.href.split('#')[0]; break; }
      }
    }
    if (matchedHref) commentChapter.value = matchedHref;
    else commentChapter.value = currentChapter;
  }
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
    text: lastSelection.text,
    created_at: ts,
    modified_at: ts,
  };
  hidePopup();
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
  // δ.4 — only show the "綁到剛選取的段落" toggle when there is a live
  // selection; otherwise the row is irrelevant noise.
  const hasSelection = !!(lastSelection && lastSelection.cfi);
  commentAnchor.disabled = !hasSelection;
  if (commentAnchorRow) commentAnchorRow.hidden = !hasSelection;
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
if (reflectionModal) {
  const closeBtn = reflectionModal.querySelector('[data-cancel]');
  if (closeBtn) closeBtn.addEventListener('click', () => reflectionModal.close('cancel'));
}

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
addCommentBtn.addEventListener('click', openCommentModal);

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

// The ingest button is a state-machine: its current text + dataset.mode tell the
// click handler which API to call. queued is the only cancellable state — once
// the LLM ingest starts (status='ingesting') the API refuses (409) since the
// background job can't be aborted mid-run.
function applyIngestState({ has_original, ingest_status }) {
  if (!ingestBtn || !ingestWrap) return;
  ingestBtn.classList.remove('is-queued');
  ingestBtn.dataset.mode = 'ingest';
  if (!has_original) {
    ingestBtn.disabled = true;
    ingestBtn.textContent = '📥 Ingest 整本書';
    ingestWrap.setAttribute('data-disabled-reason', '上傳 EN 原檔以啟用 ingest');
    return;
  }
  ingestWrap.removeAttribute('data-disabled-reason');
  if (ingest_status === 'queued') {
    ingestBtn.disabled = false;
    ingestBtn.classList.add('is-queued');
    ingestBtn.dataset.mode = 'cancel';
    ingestBtn.textContent = '📥 取消 Queued';
    return;
  }
  if (ingest_status === 'ingesting') {
    ingestBtn.disabled = true;
    ingestBtn.classList.add('is-queued');
    ingestBtn.textContent = '📥 Ingesting';
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
    ingestBtn.textContent = '📥 重試 ingest';
    return;
  }
  ingestBtn.disabled = false;
  ingestBtn.textContent = '📥 Ingest 整本書';
}

async function requestIngest() {
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
    applyIngestState({ has_original: true, ingest_status: 'queued' });
  } catch (err) {
    console.error('ingest request error', err);
    showToast(`Ingest 送出失敗：${String(err.message || err)}`);
    ingestBtn.disabled = false;
    ingestBtn.textContent = prevText;
  }
}

async function cancelIngest() {
  if (!ingestBtn) return;
  const prevText = ingestBtn.textContent;
  ingestBtn.disabled = true;
  ingestBtn.textContent = '📥 取消中⋯';
  try {
    const r = await fetch(
      `/api/books/${encodeURIComponent(BOOK_ID)}/ingest-request`,
      { method: 'DELETE' },
    );
    if (!r.ok) {
      const detail = await r.text().catch(() => '');
      console.error('ingest cancel failed', r.status, detail);
      showToast(`取消失敗 (HTTP ${r.status})`);
      ingestBtn.disabled = false;
      ingestBtn.textContent = prevText;
      return;
    }
    applyIngestState({ has_original: true, ingest_status: 'never' });
  } catch (err) {
    console.error('ingest cancel error', err);
    showToast(`取消失敗：${String(err.message || err)}`);
    ingestBtn.disabled = false;
    ingestBtn.textContent = prevText;
  }
}

if (ingestBtn) {
  ingestBtn.addEventListener('click', () => {
    if (ingestBtn.dataset.mode === 'cancel') {
      cancelIngest();
    } else {
      requestIngest();
    }
  });
}

const deleteBookBtn = document.getElementById('deleteBookBtn');
if (deleteBookBtn) {
  deleteBookBtn.addEventListener('click', async () => {
    if (!confirm('刪除整本書？此動作會同時清掉註解、進度與 ingest 紀錄，且無法復原。')) return;
    const prevText = deleteBookBtn.textContent;
    deleteBookBtn.disabled = true;
    deleteBookBtn.textContent = '刪除中⋯';
    try {
      const r = await fetch(`/api/books/${encodeURIComponent(BOOK_ID)}`, { method: 'DELETE' });
      if (!r.ok) throw new Error(await r.text());
      window.location.href = '/books';
    } catch (err) {
      alert('刪除失敗：' + err.message);
      deleteBookBtn.disabled = false;
      deleteBookBtn.textContent = prevText;
    }
  });
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
    // ζ.4 — clicks inside the iframe don't bubble to the host document, so
    // the click-outside-dismiss handler above never sees them. Mirror it
    // here. The deferred check leaves the bubble alone if a fresh
    // show-annotation just (re-)opened it for a different annotation.
    doc.addEventListener('click', _maybeDismissBubble);
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
  updateProgressFooter(detail);
  scheduleProgressWrite(payload);
});

// δ.2 / ε.2 — fixed-bottom progress footer showing real chapter title +
// position + percent. ``detail.index`` is consumed by foliate-js's internal
// ``#onRelocate`` and stripped before the public event fires; the position
// counter must read ``detail.section.{current,total}`` (a 0-based section
// index + total) instead.
function updateProgressFooter(detail) {
  if (!rpfChapter || !rpfPosition || !rpfPercent) return;

  // ζ.5 — chapter title + X / Y 章 are derived from the TOC chapter level,
  // not from foliate's deepest tocItem (which would surface "支柱一：身分"
  // instead of the parent chapter "投資組合人生的四大支柱") and not from
  // detail.section (spine sections include sub-files that aren't chapters).
  const chapter = _resolveCurrentChapter(detail);
  const totalChapters = _chapterEntries.length;

  updateTocCurrent(chapter ? chapter.idx : -1);

  if (chapter && chapter.entry && chapter.entry.label) {
    rpfChapter.textContent = chapter.entry.label.trim();
    rpfPosition.textContent = totalChapters > 0
      ? `第 ${chapter.idx + 1} / ${totalChapters} 章`
      : '第 — / — 章';
  } else {
    // Fallback for positions outside any chapter (e.g. front matter):
    // show whatever label foliate has, drop the X/Y count.
    if (detail.tocItem && detail.tocItem.label) {
      rpfChapter.textContent = detail.tocItem.label.trim();
    } else {
      rpfChapter.textContent = '—';
    }
    rpfPosition.textContent = totalChapters > 0
      ? `共 ${totalChapters} 章`
      : '';
  }

  const pct = typeof detail.fraction === 'number' ? detail.fraction : 0;
  rpfPercent.textContent = `${Math.round(pct * 100)}%`;
}

// δ.1 / ζ.1 / ζ.3 — annotation detail bubble. Behaviours:
// - Highlights (no note) DO NOT pop the bubble — there's nothing extra to
//   show beyond the colored overlay itself.
// - Annotations show only the note (skip the redundant excerpt — the user
//   already sees the highlighted text on the page).
// - Reflections with a cfi_anchor show the body text.
// - Click anywhere outside the bubble dismisses it (handled separately).
let _bubbleShownAt = 0;

function showAnnotationBubble(value) {
  if (!annBubble || !currentSet || !Array.isArray(currentSet.items)) return;
  const item = currentSet.items.find(it => (it.cfi || it.cfi_anchor) === value);
  if (!item) return;
  // ζ.1 — highlights have nothing to add beyond the overlay color.
  if (item.type === 'highlight') return;

  const kind = item.type === 'comment' ? 'reflection' : item.type;
  annBubbleKind.textContent = ({
    annotation: '註解',
    reflection: '反思',
  })[kind] || kind;
  annBubbleKind.dataset.kind = kind;

  // ζ.3 — annotations: show ONLY the note (skip the excerpt; user already
  // sees the highlighted text on the page). Reflections: show the body
  // (no excerpt either — reflections don't carry one in v3 schema).
  annBubbleExcerpt.hidden = true;
  annBubbleExcerpt.textContent = '';
  const noteText = item.note || item.body || '';
  if (noteText) {
    annBubbleNote.hidden = false;
    annBubbleNote.textContent = noteText;
  } else {
    annBubbleNote.hidden = true;
    annBubbleNote.textContent = '';
  }
  const created = item.created_at || '';
  annBubbleMeta.textContent = created ? `建立於 ${created}` : '';
  annBubble.hidden = false;
  _bubbleShownAt = Date.now();
}

function hideAnnotationBubble() {
  if (annBubble) annBubble.hidden = true;
}

view.addEventListener('show-annotation', e => {
  const value = e.detail && e.detail.value;
  if (value) showAnnotationBubble(value);
});

// ε.1 — foliate-js attaches overlays to the **currently-loaded section
// iframe only**. When the user paginates into a section that hasn't been
// rendered before, a fresh overlayer is created and the previously-applied
// annotations are NOT re-attached automatically. The library emits
// ``create-overlay`` on each new section iframe; re-running the full render
// is cheap (addAnnotation is a no-op for non-current sections so cross-talk
// is not a concern) and keeps highlights visible across navigation +
// after closing the detail bubble (which itself triggers a re-paint that
// can drop overlays in some EPUBs).
view.addEventListener('create-overlay', () => {
  if (currentSet && Array.isArray(currentSet.items)) renderAllExisting();
});

if (annBubbleClose) annBubbleClose.addEventListener('click', hideAnnotationBubble);
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && annBubble && !annBubble.hidden) {
    e.preventDefault();
    hideAnnotationBubble();
  }
});
// ζ.4 — click anywhere outside the bubble dismisses it. Two paths:
// (a) clicks on the host document (outside the iframe entirely)
// (b) clicks INSIDE an iframe (handled in the ``load`` handler, see below)
// Both defer with setTimeout(0) so a click that just fired
// ``show-annotation`` (which re-shows the bubble for a different item)
// doesn't get immediately undone.
function _maybeDismissBubble() {
  setTimeout(() => {
    if (!annBubble || annBubble.hidden) return;
    // If the bubble was just (re-)shown within this same click cycle,
    // leave it alone. The 30 ms window absorbs ordering jitter between
    // the foliate hit-test handler and our document listener.
    if (Date.now() - _bubbleShownAt < 30) return;
    hideAnnotationBubble();
  }, 0);
}
document.addEventListener('click', e => {
  if (!annBubble || annBubble.hidden) return;
  if (annBubble.contains(e.target)) return;
  _maybeDismissBubble();
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

    // δ.3 — build href→label map from view.book.toc; populated once after
    // view.open() finishes so view.book is ready.
    _buildChapterLabelMap();
    renderTocSidebar();

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
    const tocOpen = localStorage.getItem(TOC_SIDEBAR_KEY) === '1';
    setTocSidebarOpen(tocOpen);
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
