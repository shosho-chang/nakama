/* Shosho unified theme toggle — Design System v0.1.
   Single source of truth for light / dark / auto across every Nakama surface.

   Must load as a render-blocking <script src> in <head>: the stored theme is
   applied to <html> before <body> paints, so there is no light→dark flash.
   Do NOT add defer/async — that reintroduces the flash. The toggle control
   itself is injected on DOMContentLoaded (it needs <body>).

   State: localStorage['sho-theme'] ∈ {'light','dark','auto'}, default 'auto'.
   'auto' removes data-theme entirely so tokens.css falls back to the
   prefers-color-scheme media query; 'light'/'dark' set it as an explicit
   override. data-theme lives on <html> because this script runs before
   <body> exists. */
(function () {
  var KEY = 'sho-theme';
  var ORDER = ['auto', 'light', 'dark'];
  var LABELS = { auto: 'Auto', light: 'Light', dark: 'Dark' };

  function read() {
    var v = null;
    try {
      v = localStorage.getItem(KEY);
    } catch (e) {
      /* private mode / disabled storage — fall through to default */
    }
    return ORDER.indexOf(v) === -1 ? 'auto' : v;
  }

  function apply(mode) {
    var root = document.documentElement;
    if (mode === 'auto') {
      root.removeAttribute('data-theme');
    } else {
      root.setAttribute('data-theme', mode);
    }
  }

  // Pre-paint: apply immediately while still parsing <head>.
  apply(read());

  function buildToggle() {
    if (document.querySelector('.sho-theme-toggle')) return;

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'sho-theme-toggle';

    var dot = document.createElement('span');
    dot.className = 'sho-theme-toggle__dot';
    var label = document.createElement('span');
    label.className = 'sho-theme-toggle__label';
    btn.appendChild(dot);
    btn.appendChild(label);

    function sync() {
      var mode = read();
      label.textContent = LABELS[mode];
      btn.dataset.mode = mode;
      btn.setAttribute('aria-label', '主題：' + LABELS[mode] + '（點擊切換）');
      btn.title = '主題：' + LABELS[mode];
    }

    btn.addEventListener('click', function () {
      var next = ORDER[(ORDER.indexOf(read()) + 1) % ORDER.length];
      try {
        localStorage.setItem(KEY, next);
      } catch (e) {
        /* storage unavailable — apply for this page view only */
      }
      apply(next);
      sync();
    });

    sync();
    document.body.appendChild(btn);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', buildToggle);
  } else {
    buildToggle();
  }

  // Keep open tabs consistent when the preference changes elsewhere.
  window.addEventListener('storage', function (e) {
    if (e.key !== KEY) return;
    apply(read());
    var btn = document.querySelector('.sho-theme-toggle');
    var label = document.querySelector('.sho-theme-toggle__label');
    if (btn) btn.dataset.mode = read();
    if (label) label.textContent = LABELS[read()];
  });
})();
