/* Bridge / Tier C — tab switching + Pomodoro timer
 *
 * Tab switching: client-side fragment toggle on .pj-tab-panel.
 * Soft gate: if user clicks past an incomplete tab, show a toast.
 * Pomodoro timer: 25-min countdown; on complete posts to /timer/complete.
 *
 * No framework — vanilla JS, CSP-safe (no inline handlers).
 */

(function () {
  'use strict';

  // ── Tab switching ──────────────────────────────────────────────────────

  function activateTab(slug) {
    document.querySelectorAll('.pj-tab').forEach(function (t) {
      var s = t.getAttribute('data-tab-slug');
      if (s === slug) {
        t.classList.add('is-active');
        t.setAttribute('aria-current', 'page');
      } else {
        t.classList.remove('is-active');
        t.setAttribute('aria-current', 'false');
      }
    });
    document.querySelectorAll('.pj-tab-panel').forEach(function (p) {
      if (p.getAttribute('data-tab') === slug) {
        p.removeAttribute('hidden');
      } else {
        p.setAttribute('hidden', '');
      }
    });
  }

  function softGateCheck(targetSlug) {
    // Cheap: read the visible tab status icons from the tab bar.
    // If targetSlug is past an incomplete previous tab, surface a toast.
    var tabs = Array.from(document.querySelectorAll('.pj-tab'));
    var targetIdx = tabs.findIndex(function (t) { return t.getAttribute('data-tab-slug') === targetSlug; });
    if (targetIdx <= 0) return null;

    var incomplete = [];
    for (var i = 0; i < targetIdx; i++) {
      var t = tabs[i];
      var icon = (t.querySelector('.pj-tab-icon') || {}).textContent || '';
      if (icon === '○') {
        var label = (t.querySelector('.pj-tab-label-en') || {}).textContent || t.getAttribute('data-tab-slug');
        incomplete.push(label);
      }
    }
    return incomplete.length ? incomplete : null;
  }

  function showToast(message) {
    var t = document.createElement('div');
    t.className = 'pj-toast';
    t.textContent = message;
    t.setAttribute('role', 'alert');
    Object.assign(t.style, {
      position: 'fixed',
      top: '24px',
      right: '24px',
      padding: '10px 16px',
      background: 'var(--sho-warning, #d4a040)',
      color: 'var(--sho-bg, #fff)',
      borderRadius: '4px',
      fontSize: '13px',
      zIndex: '100',
      boxShadow: '0 4px 12px rgba(0, 0, 0, 0.2)',
      maxWidth: '320px',
      lineHeight: '1.5',
    });
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 4500);
  }

  function bindTabs() {
    document.querySelectorAll('.pj-tab').forEach(function (a) {
      a.addEventListener('click', function (e) {
        var slug = a.getAttribute('data-tab-slug');
        if (!slug) return;
        e.preventDefault();
        var incomplete = softGateCheck(slug);
        if (incomplete) {
          showToast('⚠ ' + incomplete.join(' / ') + ' 還沒填，確定要跳到 ' + slug + ' 嗎？');
        }
        history.replaceState(null, '', '#' + slug);
        activateTab(slug);
      });
    });

    var fragment = (location.hash || '').replace('#', '');
    if (fragment) activateTab(fragment);
  }

  // ── Pomodoro timer ─────────────────────────────────────────────────────

  function bindPomodoroDock() {
    var dock = document.getElementById('pj-pomodoro-dock');
    if (!dock) return;
    var minutes = parseInt(dock.getAttribute('data-pomodoro-minutes'), 10) || 25;
    var slug = dock.getAttribute('data-project-slug') || '';

    var btn = dock.querySelector('[data-timer-btn]');
    var display = dock.querySelector('[data-timer-display]');
    var icon = dock.querySelector('[data-timer-icon]');
    var taskSelect = dock.querySelector('[data-task-select]');
    if (!btn || !display || !icon) return;

    var remainingSec = minutes * 60;
    var intervalId = null;
    var running = false;

    function fmt(sec) {
      var m = Math.floor(sec / 60);
      var s = sec % 60;
      return (m < 10 ? '0' + m : m) + ':' + (s < 10 ? '0' + s : s);
    }

    function render() {
      display.textContent = fmt(remainingSec);
      if (running) {
        icon.textContent = '⏸';
        btn.setAttribute('data-running', 'true');
      } else {
        icon.textContent = '▶';
        btn.setAttribute('data-running', 'false');
      }
    }

    function postComplete(taskName) {
      var form = document.createElement('form');
      form.method = 'POST';
      form.action = '/bridge/projects/' + encodeURIComponent(slug) + '/timer/complete';
      var input = document.createElement('input');
      input.type = 'hidden';
      input.name = 'task_name';
      input.value = taskName;
      form.appendChild(input);
      document.body.appendChild(form);
      form.submit();
    }

    function tick() {
      remainingSec -= 1;
      if (remainingSec <= 0) {
        running = false;
        if (intervalId) { clearInterval(intervalId); intervalId = null; }
        remainingSec = 0;
        render();

        var taskName = taskSelect ? taskSelect.value : '';
        if (!taskName) {
          showToast('🍅 番茄完成！未選擇 task，未寫入 timeEntry — 請選 task 後手動 +1🍅');
          remainingSec = minutes * 60;
          render();
          return;
        }
        // Audible done signal — single beep via Web Audio (CSP-safe, no asset load)
        try {
          var Ctor = window.AudioContext || window.webkitAudioContext;
          if (Ctor) {
            var ctx = new Ctor();
            var osc = ctx.createOscillator();
            var gain = ctx.createGain();
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.frequency.value = 880;
            gain.gain.setValueAtTime(0.18, ctx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.5);
            osc.start();
            osc.stop(ctx.currentTime + 0.5);
          }
        } catch (e) { /* ignore — fallback is silent */ }
        postComplete(taskName);
        return;
      }
      render();
    }

    btn.addEventListener('click', function () {
      if (running) {
        running = false;
        if (intervalId) { clearInterval(intervalId); intervalId = null; }
      } else {
        if (taskSelect && !taskSelect.value) {
          showToast('⚠ 請先選擇 active task 才能啟動番茄鐘');
          return;
        }
        running = true;
        intervalId = setInterval(tick, 1000);
      }
      render();
    });

    render();
  }

  // ── Hook character counter ──────────────────────────────────────────────

  function bindCounters() {
    document.querySelectorAll('[data-counter-target]').forEach(function (el) {
      var label = el.closest('.pj-inline-form').querySelector('.pj-counter');
      if (!label) return;
      var nowEl = label.querySelector('.pj-count-now');
      function refresh() {
        if (nowEl) nowEl.textContent = String(el.value.length);
        // Soft cap 300 — over 300 marks the counter warn-color
        if (el.value.length > 300) label.classList.add('is-over');
        else label.classList.remove('is-over');
      }
      el.addEventListener('input', refresh);
      refresh();
    });
  }

  // ── KB Research stub ────────────────────────────────────────────────────

  function bindKbResearch() {
    var btn = document.querySelector('[data-action="kb-research"]');
    if (!btn) return;
    btn.addEventListener('click', function () {
      // PR1 stub — actual KB endpoint dispatch lands in PR3+
      var results = document.getElementById('kb-research-results');
      if (results) {
        results.removeAttribute('hidden');
        results.innerHTML = '<h3 class="pj-section-subtitle">KB 命中</h3>' +
          '<p class="pj-empty-hint">PR1 stub — KB endpoint dispatch lands in PR3. ' +
          'Robin <code>/kb/research</code> endpoint 已上線，UI 整合待補。</p>';
      }
    });
  }

  // ── Bootstrap ──────────────────────────────────────────────────────────

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      bindTabs();
      bindPomodoroDock();
      bindCounters();
      bindKbResearch();
    });
  } else {
    bindTabs();
    bindPomodoroDock();
    bindCounters();
    bindKbResearch();
  }
})();
