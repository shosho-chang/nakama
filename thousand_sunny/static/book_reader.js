// Reader bootstrap — runs under CSP `script-src 'self'`, so this lives in a
// served-from-origin file rather than inline. book_id comes from the URL path
// (/robin/books/{book_id}) so the template needs no per-page injection.

import { View } from '/vendor/foliate-js/view.js';
import { Overlayer } from '/vendor/foliate-js/overlayer.js';

const pathParts = location.pathname.split('/').filter(Boolean);
const BOOK_ID = decodeURIComponent(pathParts[pathParts.length - 1]);
const view = document.getElementById('view');

// Dark mode is owned by the shared theme toggle (static/shosho/theme.js),
// which sets data-theme on <html>. The foliate iframe has its own document
// and cannot inherit page CSS, so mirror the resolved light/dark into it.
function isDarkTheme() {
  const t = document.documentElement.getAttribute('data-theme');
  if (t === 'dark') return true;
  if (t === 'light') return false;
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
}

// ── Typography prefs (修修 2026-08-29) ───────────────────────────────────────
// 字級 ± 與字體選擇。兩者都經由 renderer.setStyles 注入 foliate 的章節 iframe —
// 那是本頁唯一能影響書內排版的管道（章節內容活在 blob: iframe 裡，外層 CSS 進不去）。
//
// 字體來源受 CSP 限制（middleware/csp.py: default-src 'self' ⇒ 不允許遠端字型）：
//   · LINE Seed TW  — repo 自帶 webfont（tokens.css @font-face），到處都能用。
//   · Taipei Sans TC — 未內建；走「本機已安裝字型」解析（修修桌機已裝
//     TaipeiSansTCBeta）。沒裝的裝置由 document.fonts.check 偵測後標為不可用，
//     不會給出一個按了沒反應的選項。
const FONT_SIZE_KEY = 'bookReaderFontSize';
const FONT_FAMILY_KEY = 'bookReaderFontFamily';
const FONT_STEPS = [80, 90, 100, 110, 125, 140, 160, 180, 200];
const FONTS = [
  { id: 'publisher', label: '書本原樣', stack: '' },
  {
    id: 'line-seed',
    label: 'LINE Seed',
    stack: "'LINE Seed TW', system-ui, sans-serif",
    probe: 'LINE Seed TW',
    // A webfont only exists in the document that declared it. The URL must carry
    // location.origin: chapters live in blob: documents, where a root-relative
    // '/static/...' does not resolve back to the site (measured: font never loaded). tokens.css lives in
    // the OUTER page, so inside foliate's chapter iframes 'LINE Seed TW' silently
    // fell back to serif (measured: identical advance width to serif). Ship the
    // @font-face along with the rule — same-origin, allowed by the reader CSP.
    faces: [
      { weight: '400 500', file: 'LINESeedTW_Rg' },
      { weight: '600 900', file: 'LINESeedTW_Bd' },
    ],
  },
  {
    id: 'taipei-sans',
    label: 'Taipei Sans TC',
    // 系統安裝名為 "Taipei Sans TC Beta"；後者是保險寫法，兩種命名都吃得到
    stack: "'Taipei Sans TC Beta', 'Taipei Sans TC', system-ui, sans-serif",
    probe: 'Taipei Sans TC Beta',
  },
];

function readFontSize() {
  const n = parseInt(localStorage.getItem(FONT_SIZE_KEY) || '100', 10);
  return FONT_STEPS.indexOf(n) === -1 ? 100 : n;
}
function readFontFamily() {
  const id = localStorage.getItem(FONT_FAMILY_KEY) || 'publisher';
  return FONTS.some((f) => f.id === id) ? id : 'publisher';
}
function fontIsAvailable(font) {
  if (!font.probe) return true;                       // publisher default
  if (!document.fonts || typeof document.fonts.check !== 'function') return true;
  try { return document.fonts.check(`16px "${font.probe}"`); } catch (_) { return true; }
}

// The typography half of the injected stylesheet. Font size scales the ROOT and
// forces em-relative sizing on text elements, so a publisher's px-pinned CSS
// still responds to the control (that CSS lands in the same cascade, hence
// !important). font-family is only forced when 修修 picked a font — 書本原樣
// leaves the publisher's own faces (including any embedded fonts) alone.
function typographyCSS() {
  const pct = readFontSize();
  const font = FONTS.find((f) => f.id === readFontFamily());
  const stack = font && font.stack && fontIsAvailable(font) ? font.stack : '';
  const parts = [];
  if (pct !== 100) {
    parts.push(`html { font-size: ${pct}% !important; }`);
    parts.push(
      `p, li, dt, dd, blockquote, figcaption, td, th, span, div { font-size: 1em !important; }`
    );
  }
  if (stack && font.faces) {
    font.faces.forEach(function (f) {
      parts.push(
        `@font-face { font-family: 'LINE Seed TW';
         src: url('${location.origin}/static/shosho/fonts/${f.file}.woff2') format('woff2'),
              url('${location.origin}/static/shosho/fonts/${f.file}.woff') format('woff');
         font-weight: ${f.weight}; font-style: normal; font-display: swap; }`
      );
    });
  }
  if (stack) {
    parts.push(
      `html, body, p, li, dt, dd, blockquote, figcaption, caption, th, td,
       h1, h2, h3, h4, h5, h6, span, div, cite, em, strong, b, i, a
       { font-family: ${stack} !important; }`
    );
  }
  return parts.join(' ');
}

function pushReaderStyles() {
  if (!view.renderer || typeof view.renderer.setStyles !== 'function') return;
  const dark = isDarkTheme();
  // Dark mode: publisher EPUB stylesheets hard-code near-black text colors
  // (color:#000000) on headings, list items, footnotes, boxed text, etc. —
  // designed for a white page. Once the book's own CSS loads (style-src now
  // allows the blob: stylesheet, see middleware/csp.py) those elements go
  // dark-on-dark and vanish. Force every text-bearing element to the light
  // reading color so dark mode stays readable. This is a deliberate monochrome
  // trade-off: accent colors (e.g. pink section titles) are dropped in dark
  // mode but still render in light mode. Font sizes are left untouched, so the
  // publisher's heading scale is preserved.
  const css = dark
    ? `html, body { background: #1a1a1a !important; color: #e0e0e0 !important; }
       h1, h2, h3, h4, h5, h6,
       p, li, dt, dd, blockquote, figcaption, caption, th, td,
       span, div, cite, em, strong, b, i, u, small, sub, sup {
         color: #e0e0e0 !important;
       }
       a, a:visited { color: #9d97ff !important; }`
    : `html, body { background: #ffffff; color: #1a1a1a; }
       a, a:visited { color: #6c63ff; }`;
  try { view.renderer.setStyles(css + ' ' + typographyCSS()); } catch (_) { /* renderer not ready yet */ }
}

const wideMQ = window.matchMedia('(min-width: 1500px)');
const mobileMQ = window.matchMedia('(max-width: 768px)');
function applyColumns() {
  if (!view.renderer) return;
  // 修修 2026-08-29: mobile now PAGINATES too (was continuous scroll) — 手機也要
  // 能翻頁。One column on a phone; desktop keeps 1, or 2 on very wide screens.
  // The swipe / edge-tap handlers below drive the turns; in paginated flow they
  // move page-by-page and cross section boundaries at the ends.
  view.renderer.setAttribute('flow', 'paginated');
  view.renderer.setAttribute('max-column-count', !mobileMQ.matches && wideMQ.matches ? '2' : '1');
}
wideMQ.addEventListener('change', applyColumns);
mobileMQ.addEventListener('change', applyColumns);

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
// A button (annotation w/ note) — brand orange (tokens.css --sho-accent,
// PANTONE 165). Literal because overlays draw inside the sandboxed EPUB
// iframe where --sho-* custom properties don't cascade.
const ANN_NOTE_COLOR = 'oklch(0.71 0.135 41)';

const popup = document.getElementById('ann-popup');
const noteModal = document.getElementById('ann-note-modal');
const noteExcerpt = document.getElementById('ann-note-excerpt');
const noteText = document.getElementById('ann-note-text');
const commentModal = document.getElementById('ann-comment-modal');
const commentChapter = document.getElementById('ann-comment-chapter');
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
const annBubbleEdit = document.getElementById('annBubbleEdit');
const noteModalTitle = document.getElementById('ann-note-title');
const NOTE_MODAL_DEFAULT_TITLE = noteModalTitle ? noteModalTitle.textContent : '';

// Edit-in-place state (η.1) — `bubbleItem` is the item the detail bubble is
// currently showing; `editingItem` is non-null while the note modal is open in
// edit mode (opened from the bubble's edit button instead of a fresh selection).
let bubbleItem = null;
let editingItem = null;

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

// ── Typography controls (修修 2026-08-29) ────────────────────────────────────
const fontSmallerBtn = document.getElementById('fontSmaller');
const fontLargerBtn = document.getElementById('fontLarger');
const fontSizeLabel = document.getElementById('fontSizeLabel');
const fontFamilySel = document.getElementById('fontFamily');

function renderTypeControls() {
  const pct = readFontSize();
  const idx = FONT_STEPS.indexOf(pct);
  if (fontSizeLabel) fontSizeLabel.textContent = pct + '%';
  if (fontSmallerBtn) fontSmallerBtn.disabled = idx <= 0;
  if (fontLargerBtn) fontLargerBtn.disabled = idx >= FONT_STEPS.length - 1;
}

function stepFontSize(delta) {
  const idx = FONT_STEPS.indexOf(readFontSize());
  const next = FONT_STEPS[Math.min(FONT_STEPS.length - 1, Math.max(0, idx + delta))];
  localStorage.setItem(FONT_SIZE_KEY, String(next));
  renderTypeControls();
  pushReaderStyles();
}

function buildFontPicker() {
  if (!fontFamilySel) return;
  const cur = readFontFamily();
  fontFamilySel.textContent = '';
  FONTS.forEach((f) => {
    const opt = document.createElement('option');
    opt.value = f.id;
    // 沒安裝的字型不假裝可選 — 標示出來並停用，避免「選了沒反應」
    const ok = fontIsAvailable(f);
    opt.textContent = ok ? f.label : f.label + '（未安裝）';
    opt.disabled = !ok;
    if (f.id === cur) opt.selected = true;
    fontFamilySel.appendChild(opt);
  });
}

if (fontSmallerBtn) fontSmallerBtn.addEventListener('click', () => stepFontSize(-1));
if (fontLargerBtn) fontLargerBtn.addEventListener('click', () => stepFontSize(1));
if (fontFamilySel) {
  fontFamilySel.addEventListener('change', () => {
    localStorage.setItem(FONT_FAMILY_KEY, fontFamilySel.value);
    pushReaderStyles();
  });
}
renderTypeControls();
// Font availability is only knowable once the font set has settled; ready is
// already resolved on a warm load, so this covers both paths.
if (document.fonts && document.fonts.ready && typeof document.fonts.ready.then === 'function') {
  document.fonts.ready.then(buildFontPicker).catch(buildFontPicker);
} else {
  buildFontPicker();
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
  // ADR-021 §1 v3 shape. Action handlers (actionHighlight / submitNote / etc.) build
  // items with the v3 field set — notably `text` on highlights — which V2 schemas
  // reject as extra_forbidden. For books that already had annotations on disk this
  // didn't surface because the server returns v3 on GET; but for a freshly imported
  // book the 404 path runs here, and the first save would 422. Stay v3 throughout.
  return {
    schema_version: 3,
    slug: BOOK_ID,
    book_id: BOOK_ID,
    book_version_hash: bookVersionHash,
    items: [],
    updated_at: nowIso(),
    last_synced_at: null,
  };
}

async function fetchBookMetadata() {
  try {
    const r = await fetch(`/robin/api/books/${encodeURIComponent(BOOK_ID)}`);
    if (!r.ok) return;
    const meta = await r.json();
    if (meta.book_version_hash) bookVersionHash = meta.book_version_hash;
    applyIngestState({
      ingest_status: typeof meta.ingest_status === 'string' ? meta.ingest_status : 'never',
    });
  } catch (err) {
    console.warn('book metadata fetch failed', err);
  }
}

async function fetchAnnotations() {
  try {
    const r = await fetch(`/robin/api/books/${encodeURIComponent(BOOK_ID)}/annotations`);
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

// ADR-044 §B8 — surface Syncthing conflict copies of this book's annotation
// file (VPS Robin write vs mobile Obsidian edit) so the user merges them
// manually, instead of the divergence being silently dropped.
function renderConflictBanner() {
  const banner = document.getElementById('conflictBanner');
  if (!banner) return;
  const conflicts = currentSet && Array.isArray(currentSet.conflicts)
    ? currentSet.conflicts
    : [];
  if (conflicts.length === 0) {
    banner.hidden = true;
    return;
  }
  const textEl = document.getElementById('conflictBannerText');
  if (textEl) {
    const devices = [...new Set(conflicts.map((c) => c.device))].join('、');
    textEl.textContent =
      `偵測到 ${conflicts.length} 個同步衝突檔（${devices}）—— 此來源的標註在不同裝置上分歧，`
      + '請到 KB/Annotations 手動合併。新標註仍會存進主檔。';
  }
  banner.hidden = false;
}

async function persistSet(nextSet) {
  // Full-replace POST. Caller passes the new set; on success it becomes the
  // canonical mirror. On failure, the prior snapshot is restored and the
  // caller's UI side-effect is best-effort rolled back via the return value.
  const prior = currentSet;
  currentSet = nextSet;
  try {
    const r = await fetch(
      `/robin/api/books/${encodeURIComponent(BOOK_ID)}/annotations`,
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
    }
    // θ.1（修修 2026-06-10）：反思是章節層級，不再畫段落 overlay —
    // legacy item 的 cfi_anchor 資料保留，只是不渲染。反思入口在側欄。
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
    empty.textContent = '尚無反思。點上方「+ 新增」選擇章節，寫下這一章的長段思考。';
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
const reflectionModalEdit = document.getElementById('reflectionModalEdit');

// θ.1 — the viewer is now also the reflection edit entry point（反思沒有
// 段落 overlay 了，bubble 編輯路徑到不了它）。
let reflectionModalItem = null;

function openReflectionModal(item) {
  if (!reflectionModal) return;
  reflectionModalItem = item;
  reflectionModalChapter.textContent = _labelForChapterRef(item.chapter_ref);
  reflectionModalTime.textContent = item.created_at || '';
  reflectionModalBody.textContent = item.body || '';
  reflectionModal.showModal();
}

if (reflectionModalEdit) {
  reflectionModalEdit.addEventListener('click', () => {
    if (!reflectionModalItem) return;
    const item = reflectionModalItem;
    reflectionModal.close('edit');
    openEditModal(item);
  });
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
  // Is a pointer currently down in this doc? A mouse drag fires selectionchange
  // continuously while the user is still choosing the range — we let pointerup
  // handle that case. Touch long-press ends in pointercancel (the OS selection UI
  // takes the gesture over), which is exactly why pointerup alone missed it.
  let pointerDown = false;
  const pointerEnded = () => { pointerDown = false; };
  doc.addEventListener('pointerdown', () => { pointerDown = true; }, { passive: true });
  doc.addEventListener('pointercancel', pointerEnded, { passive: true });
  doc.addEventListener('touchend', pointerEnded, { passive: true });
  doc.addEventListener('pointerup', () => {
    pointerDown = false;
    // Defer slightly so the selection settles after the pointerup default.
    setTimeout(() => onIframePointerUp(doc), 0);
  });
  // 修修 2026-08-29 (手機): a long-press selection never delivers a pointerup to
  // the document — the gesture is consumed by the OS selection UI — so on a phone
  // the 螢光/註解 popup simply never appeared (實測: selection 有了、popup 仍 hidden).
  // selectionchange fires for touch, mouse AND handle-dragging, so it covers every
  // way a selection can settle. Debounced: dragging a selection handle fires it
  // continuously, and we only want the popup once the selection stops moving.
  let selTimer = null;
  doc.addEventListener('selectionchange', () => {
    if (selTimer) clearTimeout(selTimer);
    selTimer = setTimeout(() => {
      selTimer = null;
      if (pointerDown) return;   // still dragging — pointerup will finish the job
      onIframePointerUp(doc);
    }, 250);
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
  if (editingItem) return submitNoteEdit(note);
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
  // Honest return: on a failed save the modal stays open so the typed note is
  // not silently discarded (2026-06-10 data-loss lesson).
  return appendItemAndPersist(item);
}

// η.1 — edit-in-place save. Replaces `editingItem` inside currentSet instead
// of appending. A highlight gaining a note becomes an annotation (v3 highlights
// have no note field), so its overlay is repainted in the annotation color.
async function submitNoteEdit(note) {
  const target = editingItem;
  const ts = nowIso();
  let updated;
  if (target.type === 'highlight') {
    updated = {
      type: 'annotation',
      schema_version: 3,
      cfi: target.cfi,
      text_excerpt: target.text_excerpt,
      book_version_hash: target.book_version_hash,
      note,
      speaker: target.speaker || '',
      created_at: target.created_at,
      modified_at: ts,
    };
  } else if (isReflection(target)) {
    updated = { ...target, body: note, modified_at: ts };
  } else {
    updated = { ...target, note, modified_at: ts };
  }
  const next = {
    ...currentSet,
    items: currentSet.items.map(it => (it === target ? updated : it)),
    updated_at: ts,
  };
  const ok = await persistSet(next);
  if (!ok) return false;
  if (target.type === 'highlight') {
    try {
      view.deleteAnnotation({ value: updated.cfi });
      view.addAnnotation({ value: updated.cfi, color: ANN_NOTE_COLOR });
    } catch (_) { /* ignore */ }
  }
  if (isReflection(target)) rebuildCommentsSidebar();
  return true;
}

// η.1 — open the note modal in edit mode from the bubble's edit button.
// Reuses the same modal for all three item kinds; the reflection variant
// hides the excerpt strip (reflections carry no text_excerpt in v3).
function openEditModal(item) {
  editingItem = item;
  if (noteModalTitle) {
    noteModalTitle.textContent = item.type === 'highlight' ? '新增註解 · 為這段螢光加上想法'
      : isReflection(item) ? '編輯反思'
        : '編輯註解';
  }
  if (isReflection(item)) {
    noteExcerpt.hidden = true;
    noteExcerpt.textContent = '';
  } else {
    noteExcerpt.hidden = false;
    noteExcerpt.textContent = item.text_excerpt || '';
  }
  noteText.value = item.type === 'highlight' ? '' : (item.note || item.body || '');
  hideAnnotationBubble();
  noteModal.showModal();
  setTimeout(() => noteText.focus(), 0);
}

function openCommentModal() {
  populateChapterSelect();
  commentBody.value = '';
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
  const ts = nowIso();
  const item = {
    // v3 wire name. ``comment`` was the v2 name — since #870 every set the
    // server hands out is v3, whose item union only accepts ``reflection``,
    // so posting ``comment`` 422s. isReflection() keeps reading both.
    // θ.1: reflections are chapter-level only — no paragraph anchor.
    type: 'reflection',
    chapter_ref: chapterRef,
    cfi_anchor: null,
    body,
    book_version_hash: bookVersionHash,
    created_at: ts,
    modified_at: ts,
  };
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
});

// η.1 — bubble edit button → note modal in edit mode.
if (annBubbleEdit) {
  annBubbleEdit.addEventListener('click', () => {
    if (bubbleItem) openEditModal(bubbleItem);
  });
}

// η.1 — whatever way the note modal closes (save / cancel / Escape), drop the
// edit state and restore the create-mode chrome so the next H/A flow is clean.
noteModal.addEventListener('close', () => {
  editingItem = null;
  if (noteModalTitle) noteModalTitle.textContent = NOTE_MODAL_DEFAULT_TITLE;
  noteExcerpt.hidden = false;
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

// θ.2（修修 2026-06-10）— 點側欄以外的地方就收起側欄（反思 + 目錄）。
// toggle 按鈕本身除外：開啟的那一下 click 會冒泡到 document，
// 不排除的話側欄一開就被同一個 click 關掉。
function dismissSidebarsOnOutsideClick(target) {
  // Clicks inside an open <dialog>（編輯/反思/新增 modal）不算「點到畫面
  // 其他地方」— 否則在 modal 裡按儲存會順手把背後的側欄關掉。
  if (target && target.closest && target.closest('dialog')) return;
  if (commentsSidebar && !commentsSidebar.hidden
      && !commentsSidebar.contains(target)
      && !(commentsToggle && commentsToggle.contains(target))) {
    setSidebarOpen(false);
  }
  if (tocSidebar && !tocSidebar.hidden
      && !tocSidebar.contains(target)
      && !(tocToggle && tocToggle.contains(target))) {
    setTocSidebarOpen(false);
  }
}
document.addEventListener('click', e => dismissSidebarsOnOutsideClick(e.target));

// ── Ingest button label ──────────────────────────────────────────────────────
//
// The button is a <form method="post" action="/start-book"> submit (book_reader.html):
// clicking POSTs synchronously → /processing runs the SAME SSE autonomous flow as
// articles/videos (摘要→概念→寫入→開卡建議), 3–5 min + progress bar. No ingest queue,
// no async fetch here — the form navigates. We only relabel: an already-ingested book
// (ingest_status='ingested', i.e. its KB/Wiki/Sources page exists) offers "重新 ingest".

const ingestBtn = document.getElementById('ingestBtn');

function applyIngestState({ ingest_status }) {
  if (!ingestBtn) return;
  const label = ingestBtn.querySelector('.btn-label');
  if (label) {
    label.textContent = ingest_status === 'ingested' ? '重新 ingest' : 'Ingest 整本書';
  }
}

const deleteBookBtn = document.getElementById('deleteBookBtn');
if (deleteBookBtn) {
  deleteBookBtn.addEventListener('click', async () => {
    if (!confirm('刪除整本書？此動作會同時清掉註解、進度與 ingest 紀錄，且無法復原。')) return;
    const prevText = deleteBookBtn.textContent;
    deleteBookBtn.disabled = true;
    deleteBookBtn.textContent = '刪除中⋯';
    try {
      const r = await fetch(`/robin/api/books/${encodeURIComponent(BOOK_ID)}`, { method: 'DELETE' });
      if (!r.ok) throw new Error(await r.text());
      window.location.href = '/robin/books';
    } catch (err) {
      alert('刪除失敗：' + err.message);
      deleteBookBtn.disabled = false;
      deleteBookBtn.textContent = prevText;
    }
  });
}

// ── Progress state (Slice 3C) ────────────────────────────────────────────────
//
// Mirrors GET/PUT /robin/api/books/{id}/progress with three reliability layers:
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
      `/robin/api/books/${encodeURIComponent(BOOK_ID)}/progress`,
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

  const url = `/robin/api/books/${encodeURIComponent(BOOK_ID)}/progress`;
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
    const r = await fetch(`/robin/api/books/${encodeURIComponent(BOOK_ID)}/progress`);
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

// ── Reader keyboard shortcuts ────────────────────────────────────────────────
//
// <foliate-view> doesn't bind nav keys; the upstream demo
// (vendor/foliate-js/reader.js:138,191-192) wires its own keydown handler on
// both the host document and each iframe doc as it loads. We extend that with
// the full Reader keymap (page nav + annotation actions + UI toggles + help).
//
// Guards:
//   - any <dialog open> swallows everything (Esc closes via dialog default)
//   - input / textarea / select / contentEditable focused → don't intercept typing
//   - Shift+Arrow stays out of our way so EPUB selection extension still works
//
// Combos use Ctrl on Windows/Linux, Cmd on macOS (metaKey). All other actions
// are bare keys (no modifier). The keymap dialog (? key) lists everything for
// the user.
const kbdHelpDialog = document.getElementById('kbdHelpDialog');
const kbdHelpBtn = document.getElementById('kbdHelpBtn');

function _shouldSkipKey(e) {
  // any modal open
  const openDialogs = document.querySelectorAll('dialog[open]');
  if (openDialogs.length > 0) return true;
  const ae = document.activeElement;
  if (ae && (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA' || ae.tagName === 'SELECT' || ae.isContentEditable)) return true;
  return false;
}

function _cycleTheme() {
  // Delegate to shosho/theme.js by simulating a click on its toggle. That keeps
  // the canonical state (localStorage + iframe re-theming) flowing through the
  // one source of truth instead of re-implementing the cycle here.
  const toggle = document.querySelector('.sho-theme-toggle');
  if (toggle) toggle.click();
}

function _openKbdHelp() {
  if (kbdHelpDialog && typeof kbdHelpDialog.showModal === 'function' && !kbdHelpDialog.open) {
    kbdHelpDialog.showModal();
  }
}

function handleReaderKey(e) {
  if (_shouldSkipKey(e)) return;
  const k = e.key;
  const mod = e.ctrlKey || e.metaKey;
  const alt = e.altKey;
  const shift = e.shiftKey;

  // ── Selection actions (Ctrl/Cmd + B / I / M) ─────────────────────────────
  if (mod && !alt) {
    const lower = k.toLowerCase();
    if (lower === 'b') {
      e.preventDefault();
      actionHighlight();
      return;
    }
    if (lower === 'i') {
      e.preventDefault();
      openNoteModal();
      return;
    }
    if (lower === 'm') {
      e.preventDefault();
      openCommentModal();
      return;
    }
    // Other Ctrl/Cmd combos: let the browser handle them (copy / find / etc.)
    return;
  }

  // ── Help dialog ──────────────────────────────────────────────────────────
  // `?` is Shift+/ on US layout; key === '?' covers most layouts where the
  // browser surfaces the produced character. Keep it tolerant of variants.
  if (k === '?' || (shift && k === '/')) {
    e.preventDefault();
    _openKbdHelp();
    return;
  }

  // Below here we only handle bare keys (no modifiers). Shift alone is
  // tolerated for Shift+Space → prev page (Space alone = next page).
  if (alt) return;
  if (mod) return;  // already handled above; defensive

  // ── Page navigation ──────────────────────────────────────────────────────
  if (k === 'ArrowLeft' || k === 'PageUp') {
    e.preventDefault();
    view.goLeft();
    return;
  }
  if (k === 'ArrowRight' || k === 'PageDown') {
    e.preventDefault();
    view.goRight();
    return;
  }
  if (k === ' ') {
    // Space = next page; Shift+Space = prev page (mirrors browser convention)
    e.preventDefault();
    if (shift) view.goLeft();
    else view.goRight();
    return;
  }
  // J/K vim-style — only without Shift (Shift+J/K is reserved for future)
  if (!shift && (k === 'j' || k === 'J')) {
    e.preventDefault();
    view.goRight();
    return;
  }
  if (!shift && (k === 'k' || k === 'K')) {
    e.preventDefault();
    view.goLeft();
    return;
  }

  // Remaining shortcuts: bare keys, no shift.
  if (shift) return;

  // ── UI toggles ───────────────────────────────────────────────────────────
  if (k === 't' || k === 'T') {
    e.preventDefault();
    if (tocToggle) tocToggle.click();
    return;
  }
  if (k === 'r' || k === 'R') {
    e.preventDefault();
    if (commentsToggle) commentsToggle.click();
    return;
  }
  if (k === 'd' || k === 'D') {
    e.preventDefault();
    _cycleTheme();
    return;
  }
}

document.addEventListener('keydown', handleReaderKey);

// Legacy alias retained for the view.load handler below that mirrors keydown
// into each iframe doc. Same function, just the historical name.
const handleNavKey = handleReaderKey;

// ── Mobile page-turn (touch) ─────────────────────────────────────────────────
//
// Mobile uses foliate's `scrolled` flow (see applyColumns): native vertical
// scroll reads *within* a section, but scrolled mode never advances across a
// section boundary on its own, and next()/prev() are only bound to the keyboard
// — which a phone doesn't have. Net result: you can scroll a chapter but can't
// reach the next one without opening the TOC. So on mobile we add touch
// page-turn on each section doc (where foliate also binds its own touch
// listeners): a horizontal swipe, or a tap in the left/right screen-edge zone,
// calls goLeft()/goRight() — which in scrolled flow pages within the section and
// crosses into the adjacent section at the boundary. Vertical reading scroll is
// left alone: we only react to horizontal-dominant swipes and short edge taps,
// and never preventDefault, so native scrolling keeps working.
const _TAP_MAX_MOVE = 10;     // px — beyond this a touch is a drag/scroll, not a tap
const _TAP_MAX_MS = 300;      // ms — longer is a press (selection), not a tap
const _SWIPE_MIN_X = 45;      // px — minimum horizontal travel to count as a swipe
const _SWIPE_H_RATIO = 1.5;   // horizontal must dominate vertical by this factor
const _EDGE_ZONE = 0.18;      // left/right 18% of width are tap-to-turn zones

function attachMobilePageTurn(doc) {
  let sx = 0, sy = 0, st = 0, tracking = false;
  doc.addEventListener('touchstart', e => {
    if (e.touches.length !== 1) { tracking = false; return; }
    const t = e.changedTouches[0];
    sx = t.clientX; sy = t.clientY; st = e.timeStamp; tracking = true;
  }, { passive: true });
  doc.addEventListener('touchend', e => {
    const ok = tracking && mobileMQ.matches;
    tracking = false;
    if (!ok || e.changedTouches.length !== 1) return;
    // Don't hijack the gesture that just finished a text selection.
    const sel = doc.getSelection && doc.getSelection();
    if (sel && !sel.isCollapsed) return;
    const t = e.changedTouches[0];
    const dx = t.clientX - sx, dy = t.clientY - sy, dt = e.timeStamp - st;
    // Horizontal swipe → page turn (swipe left brings in the next page).
    if (Math.abs(dx) >= _SWIPE_MIN_X && Math.abs(dx) >= Math.abs(dy) * _SWIPE_H_RATIO) {
      if (dx < 0) view.goRight(); else view.goLeft();
      return;
    }
    // Edge tap → page turn. Skip links so footnote/anchor taps still work.
    if (Math.abs(dx) < _TAP_MAX_MOVE && Math.abs(dy) < _TAP_MAX_MOVE && dt < _TAP_MAX_MS) {
      if (e.target && e.target.closest && e.target.closest('a')) return;
      const w = doc.documentElement.clientWidth || window.innerWidth;
      if (t.clientX > w * (1 - _EDGE_ZONE)) view.goRight();
      else if (t.clientX < w * _EDGE_ZONE) view.goLeft();
    }
  }, { passive: true });
}

if (kbdHelpBtn) {
  kbdHelpBtn.addEventListener('click', _openKbdHelp);
}
if (kbdHelpDialog) {
  const closeBtn = kbdHelpDialog.querySelector('[data-cancel]');
  if (closeBtn) closeBtn.addEventListener('click', () => kbdHelpDialog.close('cancel'));
}

// ── view event wiring ────────────────────────────────────────────────────────

view.addEventListener('load', e => {
  const doc = e.detail && e.detail.doc;
  if (doc) {
    attachSelectionListener(doc);
    // Mobile-only touch page-turn (swipe / edge-tap → next / prev section).
    attachMobilePageTurn(doc);
    // Mirror the host-level keydown into each iframe doc so keys still work
    // when focus is inside the EPUB content (foliate-js demo does the same in
    // vendor/foliate-js/reader.js:195).
    doc.addEventListener('keydown', handleNavKey);
    // ζ.4 — clicks inside the iframe don't bubble to the host document, so
    // the click-outside-dismiss handler above never sees them. Mirror it
    // here. The deferred check leaves the bubble alone if a fresh
    // show-annotation just (re-)opened it for a different annotation.
    doc.addEventListener('click', _maybeDismissBubble);
    // θ.2 — same mirroring for the sidebars: a click on the book content is
    // by definition outside both sidebars and toggles.
    doc.addEventListener('click', () => dismissSidebarsOnOutsideClick(doc.body));
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

// δ.1 / ζ.1 / ζ.3 / η.1 — annotation detail bubble. Behaviours:
// - Highlights pop a minimal bubble too (η.1 revises ζ.1): no content beyond
//   the overlay itself, but the edit button lets the user add a note in place,
//   upgrading the highlight to an annotation.
// - Annotations show only the note (skip the redundant excerpt — the user
//   already sees the highlighted text on the page).
// - Reflections with a cfi_anchor show the body text.
// - Click anywhere outside the bubble dismisses it (handled separately).
let _bubbleShownAt = 0;

function showAnnotationBubble(value) {
  if (!annBubble || !currentSet || !Array.isArray(currentSet.items)) return;
  const item = currentSet.items.find(it => (it.cfi || it.cfi_anchor) === value);
  if (!item) return;
  bubbleItem = item;

  const kind = item.type === 'comment' ? 'reflection' : item.type;
  annBubbleKind.textContent = ({
    highlight: '螢光',
    annotation: '註解',
    reflection: '反思',
  })[kind] || kind;
  annBubbleKind.dataset.kind = kind;

  if (annBubbleEdit) {
    // 修修 2026-06-10：編輯就寫「編輯」兩個字。螢光還沒有註解，
    // 動作是「新增」不是「編輯」，所以保留全名。
    annBubbleEdit.textContent = item.type === 'highlight' ? '新增註解' : '編輯';
  }

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
    const res = await fetch(`/robin/api/books/${encodeURIComponent(BOOK_ID)}/file?lang=bilingual`);
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
    renderConflictBanner();

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

// Re-theme the foliate iframe when the shared toggle flips, or when the OS
// theme changes while the toggle is in 'auto' mode. Initial theming happens
// in the book-load path above (pushReaderStyles after the renderer mounts).
new MutationObserver(pushReaderStyles).observe(document.documentElement, {
  attributes: true,
  attributeFilter: ['data-theme'],
});
if (window.matchMedia) {
  window.matchMedia('(prefers-color-scheme: dark)')
    .addEventListener('change', pushReaderStyles);
}
