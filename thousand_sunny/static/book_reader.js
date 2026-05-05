// Reader bootstrap — runs under CSP `script-src 'self'`, so this lives in a
// served-from-origin file rather than inline. book_id comes from the URL path
// (/books/{book_id}) so the template needs no per-page injection.

import { View } from '/vendor/foliate-js/view.js';

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

(async () => {
  try {
    const res = await fetch(`/api/books/${encodeURIComponent(BOOK_ID)}/file?lang=bilingual`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const blob = await res.blob();
    const file = new File([blob], `${BOOK_ID}.epub`, { type: 'application/epub+zip' });
    await view.open(file);
    applyColumns();
    pushReaderStyles();
  } catch (err) {
    const shell = document.querySelector('.reader-shell');
    shell.innerHTML = `<div class="error-banner">無法載入書籍：${String(err.message || err)}</div>`;
  }
})();

const initialDark = localStorage.getItem(DARK_KEY) === '1';
applyDark(initialDark);

darkToggle.addEventListener('click', () => {
  const next = !document.body.classList.contains('dark');
  localStorage.setItem(DARK_KEY, next ? '1' : '0');
  applyDark(next);
});
