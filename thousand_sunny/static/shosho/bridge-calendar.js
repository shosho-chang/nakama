/* 任務 → 行事曆 tab (修修 2026-11-12).
 *
 * Direct manipulation over the week grid rendered by _week_calendar.html:
 *   · drag a block            → move it (another time, another day, or the 整天 row)
 *   · drag a block's bottom   → resize (snaps to 30 min = 1 🍅; that IS the 🍅 count)
 *   · drag an 整天 chip / a 還沒排時間 row onto the grid → give it a time
 *   · click an empty slot     → 新增任務 with that date + time filled in
 * Every change posts the hidden .wk-cal-form to /bridge/weekly/plan (from_date when
 * the day changes) — the same route as 排入, so conflicts, weekend reasons and the
 * Google event follow the existing rules. Pointer events: mouse drags after 4px;
 * touch needs a short press-and-hold so a swipe still scrolls the page.
 */
(function () {
  var cal = document.querySelector('.wk-cal');
  if (!cal) return;

  var HS = parseInt(cal.getAttribute('data-hour-start'), 10);
  var HE = parseInt(cal.getAttribute('data-hour-end'), 10);
  var form = cal.querySelector('.wk-cal-form');
  var axis = cal.querySelector('.wk-cal-axis');
  var cols = Array.prototype.slice.call(cal.querySelectorAll('.wk-cal-col'));
  var cells = Array.prototype.slice.call(cal.querySelectorAll('.wk-cal-adcell'));
  var heads = Array.prototype.slice.call(cal.querySelectorAll('.wk-cal-dh'));
  var dlg = cal.querySelector('.wk-cal-reason');
  var SNAP = 15;           // start times snap to a quarter hour
  var POM = 30;            // 1 🍅 block = 30 min (CALENDAR_BLOCK_MINUTES_PER_POMODORO)
  var MAX_POM = 20;
  var HOLD_MS = 320;       // touch press-and-hold before a drag starts

  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function hhmm(m) { return pad(Math.floor(m / 60)) + ':' + pad(m % 60); }
  function snap(m, step) { return Math.round(m / step) * step; }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function rectHit(el, x, y) {
    var r = el.getBoundingClientRect();
    return r.width > 0 && x >= r.left && x < r.right && y >= r.top && y < r.bottom;
  }
  function colAt(x, y) { for (var i = 0; i < cols.length; i++) if (rectHit(cols[i], x, y)) return cols[i]; return null; }
  function cellAt(x, y) { for (var i = 0; i < cells.length; i++) if (rectHit(cells[i], x, y)) return cells[i]; return null; }
  function minuteAt(col, y) {
    var r = col.getBoundingClientRect();
    return HS * 60 + (y - r.top) / r.height * (HE - HS) * 60;
  }
  function colFor(date) { for (var i = 0; i < cols.length; i++) if (cols[i].getAttribute('data-date') === date) return cols[i]; return null; }

  /* ── time marks in the axis while dragging ─────────────────────────────── */
  var marks = [document.createElement('span'), document.createElement('span')];
  marks.forEach(function (m) { m.className = 'wk-cal-mark'; m.hidden = true; axis.appendChild(m); });
  function showMarks(s, e) {
    var h = axis.getBoundingClientRect().height / (HE - HS);
    [[marks[0], s], [marks[1], e]].forEach(function (p) {
      p[0].hidden = false;
      p[0].textContent = hhmm(p[1]);
      p[0].style.top = ((p[1] / 60 - HS) * h) + 'px';
    });
  }
  function hideMarks() { marks.forEach(function (m) { m.hidden = true; }); }

  /* ── drag session ──────────────────────────────────────────────────────── */
  var drag = null;
  var swallowClick = false;

  function setBlock(el, s, e) {
    el.style.setProperty('--s', s);
    el.style.setProperty('--e', e);
    el.style.setProperty('--lane', 0);
    el.style.setProperty('--lanes', 1);
    el.classList.toggle('is-short', e - s < 45);
  }
  function clearOver() { cells.forEach(function (c) { c.classList.remove('is-over'); }); }

  function begin(e, src) {
    var isBlock = src.classList.contains('wk-cal-block');
    var kind = isBlock ? (e.target.closest('.wk-cal-grip') ? 'resize' : 'move') : 'place';
    var pom = Math.max(1, parseInt(src.getAttribute('data-pom'), 10) || 2);
    drag = {
      kind: kind, src: src, pointerId: e.pointerId, touch: e.pointerType === 'touch',
      x0: e.clientX, y0: e.clientY, active: false, timer: null,
      slug: src.getAttribute('data-slug'),
      fromDate: src.getAttribute('data-date') || '',
      pom: pom, target: null,
    };
    if (isBlock) {
      drag.s0 = parseInt(src.getAttribute('data-start'), 10);
      drag.e0 = parseInt(src.getAttribute('data-end'), 10);
      drag.dur = drag.e0 - drag.s0;
      drag.parent0 = src.parentNode;
      drag.next0 = src.nextSibling;
      drag.style0 = src.getAttribute('style') || '';
      drag.grab = minuteAt(src.parentNode, e.clientY) - drag.s0;   // where on the block it was grabbed
    } else {
      drag.dur = clamp(pom, 1, MAX_POM) * POM;
      drag.grab = POM / 2;
    }
    if (drag.touch) drag.timer = window.setTimeout(activate, HOLD_MS);
  }

  function activate() {
    if (!drag || drag.active) return;
    drag.active = true;
    cal.classList.add(drag.kind === 'resize' ? 'is-resizing' : 'is-dragging');
    if (drag.kind === 'place') {
      drag.src.classList.add('is-lifted');
      var g = document.createElement('div');
      g.className = 'wk-cal-ghost' + (drag.src.classList.contains('is-strong') ? ' is-strong' : '');
      var t = document.createElement('span');
      t.className = 'wk-cal-bt';
      t.textContent = (drag.src.textContent || '').trim();
      g.appendChild(t);
      g.hidden = true;
      drag.el = g;
    } else {
      drag.el = drag.src;
      drag.src.classList.add('is-lifted');
      showMarks(drag.s0, drag.e0);
    }
    if (drag.touch && navigator.vibrate) { try { navigator.vibrate(8); } catch (_) { /* no-op */ } }
  }

  function update(x, y) {
    var d = drag;
    if (d.kind === 'resize') {
      var col = d.parent0;
      var end = clamp(snap(minuteAt(col, y), POM), d.s0 + POM, Math.min(HE * 60, d.s0 + MAX_POM * POM));
      end = d.s0 + Math.max(POM, snap(end - d.s0, POM));
      setBlock(d.el, d.s0, end);
      showMarks(d.s0, end);
      d.target = { date: d.fromDate, start: d.s0, end: end, allDay: false, weekend: col.getAttribute('data-weekend') === '1' };
      return;
    }
    var c = colAt(x, y);
    var cell = c ? null : cellAt(x, y);
    clearOver();
    if (c) {
      var dur = d.dur;
      var s = clamp(snap(minuteAt(c, y) - d.grab, SNAP), HS * 60, HE * 60 - dur);
      if (d.el.parentNode !== c) c.appendChild(d.el);
      d.el.hidden = false;
      d.el.classList.remove('is-hidden');
      setBlock(d.el, s, s + dur);
      showMarks(s, s + dur);
      d.target = { date: c.getAttribute('data-date'), start: s, end: s + dur, allDay: false, weekend: c.getAttribute('data-weekend') === '1' };
    } else if (cell) {
      cell.classList.add('is-over');
      if (d.kind === 'place') d.el.hidden = true; else d.el.classList.add('is-hidden');
      hideMarks();
      d.target = { date: cell.getAttribute('data-date'), allDay: true, weekend: cell.getAttribute('data-weekend') === '1' };
    } else {
      if (d.kind === 'place') d.el.hidden = true; else d.el.classList.add('is-hidden');
      hideMarks();
      d.target = null;
    }
  }

  function revert() {
    var d = drag;
    if (!d) return;
    if (d.timer) window.clearTimeout(d.timer);
    clearOver();
    hideMarks();
    cal.classList.remove('is-dragging', 'is-resizing');
    if (d.kind === 'place') {
      if (d.el && d.el.parentNode) d.el.parentNode.removeChild(d.el);
    } else if (d.active) {
      d.parent0.insertBefore(d.src, d.next0);
      d.src.setAttribute('style', d.style0);
      d.src.classList.remove('is-hidden');
      d.src.classList.toggle('is-short', d.e0 - d.s0 < 45);
    }
    d.src.classList.remove('is-lifted');
    drag = null;
  }

  function unchanged(d, t) {
    if (d.kind === 'place') return false;
    if (t.allDay) return false;   // a timed block dropped on 整天 always changes
    return t.date === d.fromDate && t.start === d.s0 && t.end === d.e0;
  }

  function submit(d, t, reason) {
    var f = form.elements;
    f.task_slug.value = d.slug;
    f.entry_date.value = t.date;
    f.entry_time.value = t.allDay ? '' : hhmm(t.start);
    f.pomodoros.value = t.allDay ? String(d.pom) : String(Math.max(1, Math.round((t.end - t.start) / POM)));
    f.from_date.value = (d.kind !== 'place' || d.src.classList.contains('wk-cal-chip')) && d.fromDate && d.fromDate !== t.date ? d.fromDate : '';
    f.reason.value = reason || '';
    cal.classList.add('is-saving');
    if (form.requestSubmit) form.requestSubmit(); else form.submit();
  }

  function finish() {
    var d = drag;
    if (!d) return;
    if (d.timer) window.clearTimeout(d.timer);
    if (!d.active) { drag = null; return; }   // a plain click — let the link do its thing
    swallowClick = true;
    window.setTimeout(function () { swallowClick = false; }, 0);
    var t = d.target;
    if (!t || unchanged(d, t)) { revert(); return; }
    hideMarks();
    clearOver();
    cal.classList.remove('is-dragging', 'is-resizing');
    drag = null;
    if (t.weekend && dlg && dlg.showModal) {
      var input = dlg.querySelector('input[name="reason"]');
      input.value = '';
      dlg.returnValue = '';
      dlg.addEventListener('close', function onClose() {
        dlg.removeEventListener('close', onClose);
        if (dlg.returnValue === 'ok' && input.value.trim()) {
          submit(d, t, input.value.trim());
        } else {
          drag = d; d.active = true; revert();
        }
      });
      dlg.showModal();
      return;
    }
    submit(d, t, '');
  }

  cal.addEventListener('pointerdown', function (e) {
    if (drag || (e.pointerType === 'mouse' && e.button !== 0)) return;
    var src = e.target.closest('[data-drag]');
    if (!src || !cal.contains(src)) return;
    begin(e, src);
  });
  document.addEventListener('pointermove', function (e) {
    if (!drag || e.pointerId !== drag.pointerId) return;
    if (!drag.active) {
      var moved = Math.abs(e.clientX - drag.x0) + Math.abs(e.clientY - drag.y0);
      if (drag.touch) {
        if (moved > 8) { window.clearTimeout(drag.timer); drag = null; }   // a swipe: let it scroll
        return;
      }
      if (moved < 4) return;
      activate();
    }
    e.preventDefault();
    update(e.clientX, e.clientY);
  });
  document.addEventListener('pointerup', function (e) {
    if (drag && e.pointerId === drag.pointerId) finish();
  });
  document.addEventListener('pointercancel', function (e) {
    if (drag && e.pointerId === drag.pointerId) revert();
  });
  // once a touch drag is live, the finger moves the block, not the page
  document.addEventListener('touchmove', function (e) {
    if (drag && drag.active) e.preventDefault();
  }, { passive: false });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && drag && drag.active) { revert(); }
  });
  cal.addEventListener('dragstart', function (e) { e.preventDefault(); });   // no native link-drag image
  cal.addEventListener('contextmenu', function (e) { if (drag) e.preventDefault(); });
  cal.addEventListener('click', function (e) {
    if (swallowClick) { e.preventDefault(); e.stopPropagation(); swallowClick = false; }
  }, true);

  /* ── click an empty slot → 新增任務 with date (+ time) filled in ────────── */
  var newtask = document.querySelector('details.wk-newtask');
  function openNew(date, time) {
    if (!newtask) return;
    var nf = newtask.querySelector('.wk-newtask-form');
    if (!nf) return;
    var di = nf.querySelector('input[name="entry_date"]');
    var ti = nf.querySelector('input[name="entry_time"]');
    if (di) di.value = date;
    if (ti) ti.value = time || '';
    newtask.open = true;
    newtask.scrollIntoView({ block: 'nearest' });
  }
  cols.forEach(function (c) {
    c.addEventListener('click', function (e) {
      if (e.target !== c) return;
      var m = Math.floor(minuteAt(c, e.clientY) / POM) * POM;
      openNew(c.getAttribute('data-date'), hhmm(clamp(m, HS * 60, HE * 60 - POM)));
    });
  });
  cells.forEach(function (c) {
    c.addEventListener('click', function (e) {
      if (e.target === c) openNew(c.getAttribute('data-date'), '');
    });
  });

  /* ── phone: one day at a time; the header picks the day ──────────────────── */
  function focusDay(date) {
    [heads, cells, cols].forEach(function (list) {
      list.forEach(function (el) { el.classList.toggle('is-focus', el.getAttribute('data-date') === date); });
    });
  }
  heads.forEach(function (h) {
    h.addEventListener('click', function () { focusDay(h.getAttribute('data-date')); });
  });
  var today = cal.getAttribute('data-today');
  focusDay(today || (cols[1] && cols[1].getAttribute('data-date')) || '');

  /* ── now line on today's column ──────────────────────────────────────────── */
  var todayCol = today ? colFor(today) : null;
  if (todayCol) {
    var now = document.createElement('div');
    now.className = 'wk-cal-now';
    now.setAttribute('aria-hidden', 'true');
    todayCol.appendChild(now);
    var tick = function () {
      var d = new Date();
      var m = d.getHours() * 60 + d.getMinutes();
      now.hidden = m < HS * 60 || m > HE * 60;
      now.style.setProperty('--now', m);
    };
    tick();
    window.setInterval(tick, 60000);
  }
})();
