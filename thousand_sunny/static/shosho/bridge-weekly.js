/* Shared task-row behaviour (ADR-041 v3-H Slice 3).
 *
 * These three handlers are page-agnostic — they operate on the task_row markup
 * (.wk-box-form / form[data-confirm] / .wk-sched-form) wherever it appears, so the
 * Weekly Dashboard AND the Project Brief tab both load this file. The dashboard's
 * tab-toggle + stay-in-place sessionStorage restore stays inline in weekly.html
 * (it targets .wk-tab/.wk-tabpane that only exist there). */
(function () {
  /* Done-toggle checkbox: the box is a real <form> submit. Stop the click bubbling
     so it doesn't also toggle the parent <details> row. */
  document.querySelectorAll('.wk-box-form button').forEach(function (b) {
    b.addEventListener('click', function (e) { e.stopPropagation(); });
  });

  /* Confirm-gate destructive chip actions (a linked-entry ✕ deletes its Google
     event too — V3d). */
  document.querySelectorAll('form[data-confirm]').forEach(function (f) {
    f.addEventListener('submit', function (e) {
      if (!window.confirm(f.getAttribute('data-confirm'))) e.preventDefault();
    });
  });

  /* Merged 排入 form: reveal+require the weekend reason when the picked date is
     Sat/Sun, and show the derived block length (1🍅 = 30 min) when a time is set. */
  function isWeekend(v) {
    if (!v) return false;
    var day = new Date(v + 'T00:00:00').getDay();   // 0 Sun .. 6 Sat
    return day === 0 || day === 6;
  }
  function endLabel(timeStr, pom) {
    if (!timeStr || !pom) return '';
    var parts = timeStr.split(':');
    var raw = (+parts[0]) * 60 + (+parts[1]) + pom * 30;
    var nextDay = raw >= 1440;
    var mins = ((raw % 1440) + 1440) % 1440;
    var hh = String(Math.floor(mins / 60)).padStart(2, '0');
    var mm = String(mins % 60).padStart(2, '0');
    return '→ ' + hh + ':' + mm + (nextDay ? '（+1 天）' : '') + '（' + (pom * 30) + ' 分）';
  }
  function sync(form) {
    var date = form.querySelector('.wk-sched-date');
    var time = form.querySelector('.wk-sched-time');
    var pom = form.querySelector('input[name="pomodoros"]');
    var reason = form.querySelector('.wk-reason-field');
    var dur = form.querySelector('.wk-sched-dur');
    if (date && reason) {
      var weekend = isWeekend(date.value);
      form.classList.toggle('is-weekend', weekend);
      reason.required = weekend;
    }
    // v3-I.5: the 時長/🍅 field is only meaningful for a TIMED block — blank time = all-day,
    // where a duration would be misleading (修修). Hide the field until a time is picked.
    var pomLabel = pom && pom.closest('label');
    if (pomLabel && time) pomLabel.style.display = time.value ? '' : 'none';
    if (dur && time && pom) dur.textContent = endLabel(time.value, parseInt(pom.value, 10));
  }
  document.querySelectorAll('.wk-sched-form').forEach(function (form) {
    form.querySelectorAll('.wk-sched-date, .wk-sched-time, input[name="pomodoros"]')
      .forEach(function (el) { el.addEventListener('input', function () { sync(form); }); });
    form.addEventListener('submit', function () {
      var btn = form.querySelector('.wk-sched-go, .wk-plan-go');
      if (btn) { btn.disabled = true; btn.textContent = '排入中…'; }
    });
    sync(form);
  });

  /* v3-I: after 新增任務 the server redirects with ?focus=<slug>. Reveal that row's
     排入 chip so 修修 can add a time immediately — switch to the 全部 pane (where a
     freshly-created, unscheduled task lives) if tabs exist, open the <details> row,
     scroll it into view and focus its date field. Runs before the inline restore()
     in weekly.html; restore() leaves the tab alone when sessionStorage is empty. */
  (function () {
    var focus;
    try { focus = new URLSearchParams(window.location.search).get('focus'); } catch (_) { return; }
    if (!focus) return;
    // A ?focus redirect (after 新增任務 / 重新命名) must win over the stay-in-place
    // restore() — clear its saved tab/open/scroll so it can't switch the pane back and
    // hide the row we're about to focus.
    try { ['wk-tab', 'wk-open', 'wk-scroll'].forEach(function (k) { sessionStorage.removeItem(k); }); } catch (_) { /* no sessionStorage */ }
    var sel = '.wk-task-d[data-slug="' + (window.CSS && CSS.escape ? CSS.escape(focus) : focus) + '"]';
    var rows = document.querySelectorAll(sel);
    if (!rows.length) return;
    var allTab = document.querySelector('.wk-tab[data-tab="all"]');
    if (allTab) allTab.click();   // dashboard: 全部 shows every open task; Brief has no tabs
    rows.forEach(function (d) { d.open = true; });
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].offsetParent !== null) {
        rows[i].scrollIntoView({ block: 'center' });
        var input = rows[i].querySelector('.wk-sched-date');
        if (input) input.focus();
        break;
      }
    }
  })();

  /* v3-I: dismiss an open 新增任務 / 新增關聯任務 dropdown (<details.wk-newtask>) when
     clicking outside it or pressing Esc (修修: the modal didn't close on outside-click). */
  (function () {
    var panels = document.querySelectorAll('details.wk-newtask');
    if (!panels.length) return;
    document.addEventListener('click', function (e) {
      panels.forEach(function (d) {
        if (d.open && !d.contains(e.target)) d.open = false;
      });
    });
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape') return;
      panels.forEach(function (d) { if (d.open) d.open = false; });
    });
  })();

  /* v3-I.2: Web UI calendar-conflict modal. The server redirects to
     ?err=cal_conflict&cf_* on a timed clash (NO Slack — that path is Nami-from-Slack
     only). Pop a dialog with the clash + nearby free slots and three choices:
       · 仍排此時段 → re-POST the row's 排入 form with force=1
       · 改時間     → open the row + focus its time field, prefilled
       · 取消排程   → dismiss (the plan-only entry already stands, vault-first)
     If the firing surface has no row+form on the page (e.g. the single-task page,
     which doesn't load this script), we no-op and the no-JS banner stays. */
  (function () {
    var p;
    try { p = new URLSearchParams(window.location.search); } catch (_) { return; }
    if (p.get('err') !== 'cal_conflict' || !p.get('cf_slug')) return;
    var slug = p.get('cf_slug'),
        cdate = p.get('cf_date') || '', ctime = p.get('cf_time') || '',
        pom = p.get('cf_pom') || '', withTxt = p.get('cf_with') || '既有事件',
        reason = p.get('cf_reason') || '', slots = p.get('cf_slots') || '';
    var esc = window.CSS && CSS.escape ? CSS.escape(slug) : slug;
    var row = document.querySelector('.wk-task-d[data-slug="' + esc + '"]');
    var form = row && row.querySelector('.wk-sched-form');
    if (!form) return;   // surface has no editable row → leave the no-JS banner

    var toast = document.querySelector('.wk-toast');
    if (toast) toast.remove();   // the modal supersedes the fallback banner
    var nameEl = row.querySelector('.wk-tname');
    var taskName = (nameEl && nameEl.textContent) || '此任務';

    function setField(name, val) {
      var el = form.querySelector('[name="' + name + '"]');
      if (el) el.value = val;
    }
    function close() { ov.remove(); document.removeEventListener('keydown', onEsc); }
    function onEsc(e) { if (e.key === 'Escape') close(); }

    var ov = document.createElement('div');
    ov.className = 'wk-cmodal';
    var box = document.createElement('div');
    box.className = 'wk-cmodal-box';
    box.setAttribute('role', 'alertdialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-label', '排程衝突');

    var h = document.createElement('div');
    h.className = 'wk-cmodal-h';
    h.textContent = '⚠ 排程衝突';
    box.appendChild(h);

    var msg = document.createElement('p');
    msg.className = 'wk-cmodal-msg';
    msg.textContent = '「' + taskName + '」排在 ' + cdate + (ctime ? ' ' + ctime : '') +
      '（' + pom + '🍅）與 ' + withTxt + ' 衝突。';
    box.appendChild(msg);

    if (slots) {
      var s = document.createElement('p');
      s.className = 'wk-cmodal-slots';
      s.textContent = '附近空檔：' + slots;
      box.appendChild(s);
    }

    var acts = document.createElement('div');
    acts.className = 'wk-cmodal-acts';

    var bForce = document.createElement('button');
    bForce.type = 'button';
    bForce.className = 'sho-btn wk-cmodal-force';
    bForce.textContent = '仍排此時段';
    bForce.addEventListener('click', function () {
      var f = form.querySelector('input[name="force"]');
      if (!f) { f = document.createElement('input'); f.type = 'hidden'; f.name = 'force'; form.appendChild(f); }
      f.value = '1';
      setField('entry_date', cdate);
      setField('entry_time', ctime);
      setField('pomodoros', pom);
      if (reason) setField('reason', reason);
      // requestSubmit (not submit) so the stay-in-place save-state submit handler fires
      // — form.submit() bypasses submit-event listeners, which made the view jump (v3-I.5).
      if (form.requestSubmit) form.requestSubmit(); else form.submit();
    });

    var bEdit = document.createElement('button');
    bEdit.type = 'button';
    bEdit.className = 'sho-btn sho-btn--ghost';
    bEdit.textContent = '改時間';
    bEdit.addEventListener('click', function () {
      close();
      if (row.tagName === 'DETAILS') row.open = true;
      setField('entry_date', cdate);
      setField('entry_time', ctime);
      row.scrollIntoView({ block: 'center' });
      var t = form.querySelector('.wk-sched-time') || form.querySelector('.wk-sched-date');
      if (t) t.focus();
    });

    var bCancel = document.createElement('button');
    bCancel.type = 'button';
    bCancel.className = 'sho-btn sho-btn--ghost';
    bCancel.textContent = '取消排程';
    bCancel.addEventListener('click', close);

    acts.appendChild(bForce);
    acts.appendChild(bEdit);
    acts.appendChild(bCancel);
    box.appendChild(acts);
    ov.appendChild(box);
    document.body.appendChild(ov);
    ov.addEventListener('click', function (e) { if (e.target === ov) close(); });
    document.addEventListener('keydown', onEsc);
    bForce.focus();
  })();

  /* v3-I.4: inline title rename. ✎ swaps the title (.wk-tname) for the .wk-rename-form
     in place; ✕ / Esc reverts. Because the icon sits inside the <details><summary>, we
     stopPropagation + preventDefault so a click doesn't also toggle the row open/shut. */
  (function () {
    document.querySelectorAll('.wk-rename-icon').forEach(function (icon) {
      var scope = icon.closest('.wk-task-d') || icon.closest('.tk-titlewrap');
      if (!scope) return;
      var form = scope.querySelector('.wk-rename-form');
      var titleEl = scope.querySelector('.wk-tname, .tk-title');
      if (!form || !titleEl) return;
      var input = form.querySelector('.wk-rename-input');
      function openEditor(e) {
        if (e) { e.preventDefault(); e.stopPropagation(); }
        titleEl.hidden = true; icon.hidden = true; form.hidden = false;
        if (input) { input.focus(); input.select(); }
      }
      function closeEditor() {
        form.hidden = true; titleEl.hidden = false; icon.hidden = false;
        if (input) input.value = input.defaultValue;   // discard edits on cancel
      }
      icon.addEventListener('click', openEditor);
      form.addEventListener('click', function (e) { e.stopPropagation(); });
      form.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') { e.stopPropagation(); closeEditor(); }
      });
      var cancel = form.querySelector('.wk-rename-cancel');
      if (cancel) cancel.addEventListener('click', function (e) { e.stopPropagation(); closeEditor(); });
    });
  })();

  /* v3-I.5: hide the 🍅 預估 field on the 新增任務 / 新增關聯任務 form unless the category is
     工作 — only `work` tasks count toward the weekly 🍅, so an estimate on a health /
     growth / misc task is just noise (修修). style.display so the HTML beats any class rule. */
  (function () {
    document.querySelectorAll('.wk-newtask-form').forEach(function (form) {
      var cat = form.querySelector('select[name="category"]');
      var pom = form.querySelector('.wk-newtask-pom');
      var wrap = pom && pom.closest('label');
      if (!cat || !wrap) return;
      function sync() { wrap.style.display = cat.value === 'work' ? '' : 'none'; }
      cat.addEventListener('change', sync);
      sync();
    });
  })();

  /* v3-I follow-up (修修): the 分類/優先 dropdowns auto-submit on change (requestSubmit so
     the stay-in-place save-state fires and the row doesn't jump). */
  (function () {
    document.querySelectorAll('.wk-meta-form select').forEach(function (sel) {
      sel.addEventListener('change', function () {
        if (!sel.form) return;
        if (sel.form.requestSubmit) sel.form.requestSubmit();
        else sel.form.submit();
      });
    });
  })();
})();
