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

  // ── Manual +1🍅 — AJAX in-place update ─────────────────────────────────
  //
  // The +1 button posts to /tasks/{name}/manual-pomodoro. Server now content-
  // negotiates JSON when Accept: application/json. We intercept the form
  // submit, POST via fetch, and update the row cell + dock rollup in place —
  // no full page reload, so current tab + Tasks ▾ open state survive.

  function bindManualPomodoro() {
    document.querySelectorAll('.pj-dock-task-row form[data-pomodoro-op]').forEach(function (form) {
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        var op = form.getAttribute('data-pomodoro-op'); // 'add' | 'undo'
        var delta = op === 'undo' ? -1 : 1;
        var row = form.closest('.pj-dock-task-row');
        var actualCell = row ? row.querySelector('[data-task-actual]') : null;
        var dockActual = document.querySelector('[data-actual-total]');
        if (!actualCell || !dockActual) return;

        var oldRowVal = parseInt(actualCell.textContent, 10) || 0;
        var oldDockVal = parseInt(dockActual.textContent, 10) || 0;

        // Client guard — server returns 409 for undo-when-zero, but we
        // also avoid the round-trip + revert flash.
        if (op === 'undo' && oldRowVal <= 0) {
          showToast('此 task 沒有可撤銷的 🍅');
          return;
        }

        // Optimistic UI — apply delta immediately; revert on server error.
        actualCell.textContent = String(oldRowVal + delta);
        dockActual.textContent = String(oldDockVal + delta);
        var undoBtn = row.querySelector('form[data-pomodoro-op="undo"] button');
        if (undoBtn) undoBtn.disabled = (oldRowVal + delta) <= 0;

        fetch(form.action, {
          method: 'POST',
          headers: { 'Accept': 'application/json' },
          credentials: 'same-origin',
        }).then(function (r) {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        }).then(function (data) {
          if (typeof data.task_actual === 'number') {
            actualCell.textContent = String(data.task_actual);
            if (undoBtn) undoBtn.disabled = data.task_actual <= 0;
          }
          if (typeof data.project_actual_total === 'number') {
            dockActual.textContent = String(data.project_actual_total);
          }
          // Auto-flip to-do → doing reflects back in the status dropdown
          if (data.task_status) {
            var statusSel = row.querySelector('.pj-task-status-select');
            if (statusSel && statusSel.value !== data.task_status) {
              statusSel.value = data.task_status;
              applyStatusColour(statusSel);
            }
          }
        }).catch(function () {
          actualCell.textContent = String(oldRowVal);
          dockActual.textContent = String(oldDockVal);
          if (undoBtn) undoBtn.disabled = oldRowVal <= 0;
          var symbol = op === 'undo' ? '−1🍅' : '+1🍅';
          showToast('⚠ ' + symbol + ' 寫入失敗，已還原。請重試。');
        });
      });
    });
  }

  // ── HTML5 <dialog> open/close ──────────────────────────────────────────

  function bindDialogs() {
    document.querySelectorAll('[data-dialog-target]').forEach(function (btn) {
      var target = document.getElementById(btn.getAttribute('data-dialog-target'));
      if (!target || typeof target.showModal !== 'function') return;
      btn.addEventListener('click', function () { target.showModal(); });
    });
    document.querySelectorAll('[data-dialog-close]').forEach(function (closeBtn) {
      var dlg = closeBtn.closest('dialog');
      if (!dlg) return;
      closeBtn.addEventListener('click', function () { dlg.close(); });
    });
    // Click outside the dialog content closes it. Belt-and-suspenders —
    // bind on BOTH the dialog (for backdrop clicks per HTML5 spec) AND
    // the document (fallback for any browser quirk where the dialog
    // mousedown doesn't fire). Bounding-rect check is the source of truth.
    function closeIfOutside(dlg, x, y) {
      var rect = dlg.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) return; // not visible
      var inside = x >= rect.left && x <= rect.right
                && y >= rect.top  && y <= rect.bottom;
      if (!inside) dlg.close();
    }
    document.querySelectorAll('dialog.pj-dialog').forEach(function (dlg) {
      dlg.addEventListener('mousedown', function (e) {
        closeIfOutside(dlg, e.clientX, e.clientY);
      });
    });
    // Fallback: listen at document level for any mousedown while any
    // dialog is open. Useful if the spec'd backdrop-click dispatch
    // doesn't fire for some reason (browser bug, extension interference).
    document.addEventListener('mousedown', function (e) {
      document.querySelectorAll('dialog.pj-dialog[open]').forEach(function (dlg) {
        closeIfOutside(dlg, e.clientX, e.clientY);
      });
    });
  }

  // ── Research dispatch (Zoro keyword + Robin KB + synthesis + DR) — PR3 ──
  //
  // Each action either POSTs and innerHTMLs a partial into its slot, OR (DR
  // prompt) POSTs and gets JSON back, then copies to clipboard + opens an
  // external tab. Spinner shows elapsed seconds for the longer ones.

  function bindResearchActions() {
    var allActions = Array.from(document.querySelectorAll('.pj-research-actions'));
    if (!allActions.length) return;

    var SLOT_IDS = {
      keyword: 'research-zoro-result',
      kb: 'research-kb-result',
      synthesis: 'research-synthesis-result',
    };
    var LABEL_MAP = {
      keyword: 'Zoro 跑關鍵字研究中（10 個資料來源 + Claude）…',
      kb: 'Robin 搜尋 KB 中…',
      synthesis: 'Robin 摘要中（Claude Opus 編譯）…',
      'dr-prompt': '組合 DR prompt…',
    };
    var TOAST_DONE = {
      keyword: '🗝 Zoro 研究完成，已寫回 project.md',
      kb: '📚 Robin KB 命中已寫回 project.md',
      synthesis: '📝 Robin 摘要已寫回 project.md',
    };

    // Status spinners + buttons are per-step (within a tab). Scope lookups
    // to the nearest `.pj-research-step` so multi-step tabs (e.g. Research:
    // Step 1 Zoro / Step 2-4 Robin) don't share one spinner — the bug where
    // clicking Zoro showed the spinner under Robin's section.
    function panelOf(el) { return el.closest('.pj-tab-panel'); }
    function statusOfBtn(btn) {
      var scope = btn && (btn.closest('.pj-research-step') || btn.closest('.pj-tab-panel'));
      return scope ? scope.querySelector('[data-research-status]') : null;
    }

    var timerInterval = null;
    var activeStatus = null;
    function startTimer(statusEl, label) {
      if (!statusEl) return;
      activeStatus = statusEl;
      var startedAt = Date.now();
      var timerEl = statusEl.querySelector('[data-timer]');
      var statusLabel = statusEl.querySelector('.pj-research-status-label');
      statusEl.removeAttribute('hidden');
      if (statusLabel) statusLabel.textContent = label;
      if (timerEl) timerEl.textContent = '0s';
      if (timerInterval) clearInterval(timerInterval);
      timerInterval = setInterval(function () {
        if (timerEl) {
          timerEl.textContent = Math.floor((Date.now() - startedAt) / 1000) + 's';
        }
      }, 250);
    }
    function stopTimer() {
      if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
      if (activeStatus) activeStatus.setAttribute('hidden', '');
      activeStatus = null;
    }

    // Lock / restore every research button across all tabs — an in-flight
    // LLM call shouldn't race against another one.
    function lockAll() {
      allActions.forEach(function (a) {
        a.querySelectorAll('button[data-research-action]').forEach(function (b) {
          b.disabled = true;
        });
      });
    }
    function restoreAll() {
      allActions.forEach(function (a) {
        a.querySelectorAll('button[data-research-action]').forEach(function (b) {
          b.disabled = b.hasAttribute('data-original-disabled');
        });
      });
    }

    // After a research kind succeeds, unblock the next step that was waiting on
    // its cache. Server-rendered template marks buttons disabled when cache is
    // missing on page load; here we clear that without a reload.
    function enableNextStepAfter(kind) {
      var next = { keyword: 'kb', kb: 'synthesis' }[kind];
      if (!next) return;
      document
        .querySelectorAll('button[data-research-action="' + next + '"]')
        .forEach(function (b) {
          b.disabled = false;
          b.removeAttribute('data-original-disabled');
          b.removeAttribute('title');
        });
    }

    // Stick a green ✓ badge onto the just-completed button so it's obvious
    // at a glance which steps still need running. Idempotent — won't double-add
    // if the badge is already there (server-rendered at page load).
    function markActionDone(kind) {
      document
        .querySelectorAll('button[data-research-action="' + kind + '"]')
        .forEach(function (b) {
          if (!b.querySelector('.pj-action-done')) {
            var span = document.createElement('span');
            span.className = 'pj-action-done';
            span.setAttribute('aria-label', '已完成');
            span.textContent = '✓';
            b.insertBefore(span, b.firstChild);
          }
        });
    }

    function handlePartialFetch(kind, btn) {
      var slot = document.getElementById(SLOT_IDS[kind]);
      if (!slot) return;
      var slug = btn.closest('.pj-research-actions').getAttribute('data-project-slug') || '';
      lockAll();
      slot.removeAttribute('hidden');
      slot.innerHTML = '';
      startTimer(statusOfBtn(btn), LABEL_MAP[kind] || '研究中…');

      var endpoint = '/bridge/projects/' + encodeURIComponent(slug) + '/research/' + kind;
      fetch(endpoint, {
        method: 'POST',
        credentials: 'same-origin',
      }).then(function (r) {
        if (!r.ok) {
          return r.text().then(function (text) {
            throw new Error('HTTP ' + r.status + ' — ' + text.slice(0, 500));
          });
        }
        return r.text();
      }).then(function (html) {
        slot.innerHTML = html;
        enableNextStepAfter(kind);
        markActionDone(kind);
        showToast(TOAST_DONE[kind] || '✓ 完成');
      }).catch(function (err) {
        slot.innerHTML = '<div class="pj-research-error">⚠ ' + (err.message || err) + '</div>';
      }).finally(function () {
        stopTimer();
        restoreAll();
      });
    }

    function openDrTab(target) {
      var urls = {
        chatgpt: 'https://chatgpt.com/',
        claude: 'https://claude.ai/new',
      };
      var url = urls[target] || urls.chatgpt;
      window.open(url, '_blank', 'noopener');
    }

    function handleDrPrompt(btn) {
      var target = btn.getAttribute('data-dr-target') || 'chatgpt';
      var slug = btn.closest('.pj-research-actions').getAttribute('data-project-slug') || '';
      lockAll();
      startTimer(statusOfBtn(btn), LABEL_MAP['dr-prompt']);
      var endpoint = '/bridge/projects/' + encodeURIComponent(slug) + '/research/dr-prompt';
      fetch(endpoint, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Accept': 'application/json' },
      }).then(function (r) {
        if (!r.ok) {
          return r.text().then(function (text) {
            throw new Error('HTTP ' + r.status + ' — ' + text.slice(0, 500));
          });
        }
        return r.json();
      }).then(function (data) {
        var prompt = data.prompt || '';
        if (!prompt) throw new Error('伺服器回空字串');
        var copyPromise;
        if (navigator.clipboard && navigator.clipboard.writeText) {
          copyPromise = navigator.clipboard.writeText(prompt);
        } else {
          copyPromise = Promise.reject(new Error('clipboard API unavailable'));
        }
        copyPromise.then(function () {
          showToast('📋 Prompt 已複製到剪貼簿。新分頁開啟後，貼上 + 切 Deep Research + 送出。');
          openDrTab(target);
        }).catch(function () {
          var ok = window.prompt('剪貼簿不可用 — 請手動複製這段 prompt：', prompt);
          if (ok !== null) openDrTab(target);
        });
      }).catch(function (err) {
        showToast('⚠ DR prompt 失敗：' + (err.message || err));
      }).finally(function () {
        stopTimer();
        restoreAll();
      });
    }

    allActions.forEach(function (actions) {
      actions.querySelectorAll('button[data-research-action]').forEach(function (btn) {
        btn.addEventListener('click', function () {
          if (btn.disabled) return;
          var kind = btn.getAttribute('data-research-action');
          if (kind === 'dr-prompt') {
            handleDrPrompt(btn);
          } else {
            handlePartialFetch(kind, btn);
          }
        });
        // Snapshot initial disabled state so finally{} restores correctly.
        if (btn.disabled) btn.setAttribute('data-original-disabled', '');
      });
    });
  }

  // ── DR paste-back form ──────────────────────────────────────────────────
  //
  // User runs DR externally (ChatGPT / Claude.ai), pastes the markdown back
  // into the textarea, and submits. We POST as form-encoded and innerHTML
  // the rendered partial into the DR result slot.

  function bindDrPasteForm() {
    var form = document.querySelector('[data-dr-paste-form]');
    if (!form) return;
    var slug = form.getAttribute('data-project-slug') || '';
    if (!slug) return;
    var slotDr = document.getElementById('research-dr-result');

    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var textarea = form.querySelector('textarea[name="report"]');
      var sourceSel = form.querySelector('select[name="source"]');
      var submitBtn = form.querySelector('button[type="submit"]');
      var body = textarea ? textarea.value.trim() : '';
      if (!body) {
        showToast('⚠ textarea 是空的');
        return;
      }
      var source = sourceSel ? sourceSel.value : 'manual';
      if (submitBtn) submitBtn.disabled = true;

      var fd = new FormData();
      fd.append('report', body);
      fd.append('source', source);

      fetch('/bridge/projects/' + encodeURIComponent(slug) + '/research/dr-report', {
        method: 'POST',
        body: fd,
        credentials: 'same-origin',
      }).then(function (r) {
        if (!r.ok) {
          return r.text().then(function (text) {
            throw new Error('HTTP ' + r.status + ' — ' + text.slice(0, 500));
          });
        }
        return r.text();
      }).then(function (html) {
        if (slotDr) {
          slotDr.removeAttribute('hidden');
          slotDr.innerHTML = html;
        }
        if (textarea) textarea.value = '';
        // Close the <details> wrapper so it doesn't keep taking up space
        var details = form.closest('details');
        if (details) details.open = false;
        // Mark the DR action button done — cached_dr now exists. Same shape as
        // markActionDone inside bindResearchActions; duplicated here because
        // that function is closure-scoped and this form lives in a sibling
        // binding.
        document
          .querySelectorAll('button[data-research-action="dr-prompt"]')
          .forEach(function (b) {
            if (!b.querySelector('.pj-action-done')) {
              var span = document.createElement('span');
              span.className = 'pj-action-done';
              span.setAttribute('aria-label', '已完成');
              span.textContent = '✓';
              b.insertBefore(span, b.firstChild);
            }
          });
        showToast('🌐 DR 報告已寫回 project.md');
      }).catch(function (err) {
        showToast('⚠ DR 報告儲存失敗：' + (err.message || err));
      }).finally(function () {
        if (submitBtn) submitBtn.disabled = false;
      });
    });
  }

  // ── Bootstrap ──────────────────────────────────────────────────────────

  // ── Task status dropdown — AJAX + colour reflect ───────────────────────

  function applyStatusColour(sel) {
    // Swap the colour class so the chip recolours immediately on change.
    var classes = ['pj-status-to-do', 'pj-status-doing', 'pj-status-done', 'pj-status-paused'];
    classes.forEach(function (c) { sel.classList.remove(c); });
    sel.classList.add('pj-status-' + sel.value);
  }

  function bindStatusDropdown() {
    document.querySelectorAll('.pj-task-status-select').forEach(function (sel) {
      sel.addEventListener('change', function () {
        var url = sel.getAttribute('data-status-url');
        var newValue = sel.value;
        var oldValue = sel.dataset.lastValue || '';
        // Optimistic — apply colour immediately
        applyStatusColour(sel);

        var body = 'value=' + encodeURIComponent(newValue);
        fetch(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json',
          },
          body: body,
          credentials: 'same-origin',
        }).then(function (r) {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          sel.dataset.lastValue = newValue;
        }).catch(function () {
          // Revert on failure
          if (oldValue) {
            sel.value = oldValue;
            applyStatusColour(sel);
          }
          showToast('⚠ 狀態更新失敗，已還原');
        });
      });
      // Cache initial value for revert path
      sel.dataset.lastValue = sel.value;
    });
  }

  // ── Delete task — confirm + AJAX + remove row ──────────────────────────

  function bindDeleteTask() {
    document.querySelectorAll('form[data-task-delete]').forEach(function (form) {
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        var btn = form.querySelector('button');
        var taskName = btn ? btn.getAttribute('data-task-name') : '';
        if (!confirm('刪除任務「' + taskName + '」？\n會送回收桶，可從 Windows / macOS 還原。')) {
          return;
        }
        var row = form.closest('.pj-dock-task-row');
        var dockActual = document.querySelector('[data-actual-total]');
        var dockEst = document.querySelector('[data-est-total]');

        fetch(form.action, {
          method: 'POST',
          headers: { 'Accept': 'application/json' },
          credentials: 'same-origin',
        }).then(function (r) {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        }).then(function (data) {
          if (row) row.remove();
          if (dockActual && typeof data.project_actual_total === 'number') {
            dockActual.textContent = String(data.project_actual_total);
          }
          if (dockEst && typeof data.project_est_total === 'number') {
            dockEst.textContent = String(data.project_est_total);
          }
          // Also drop from the dock's top-row task selector
          var topSelect = document.querySelector('[data-task-select]');
          if (topSelect) {
            var opt = topSelect.querySelector('option[value="' + taskName + '"]');
            if (opt) opt.remove();
          }
          showToast('✓ 「' + taskName + '」已刪除（在 Windows 回收桶可還原）');
        }).catch(function (err) {
          showToast('⚠ 刪除失敗：' + err.message);
        });
      });
    });
  }

  // ── Dock Tasks ▾ panel — close on outside mousedown ────────────────────
  //
  // Native <details> stays open until you click <summary> again. The dock
  // task panel pops up over content; clicking elsewhere should dismiss it
  // (matches the Add-Task <dialog> backdrop-close pattern).

  function bindDockTasksClickOutside() {
    document.addEventListener('mousedown', function (e) {
      document.querySelectorAll('.pj-dock-tasks-expand[open]').forEach(function (details) {
        if (!details.contains(e.target)) {
          details.open = false;
        }
      });
    });
  }

  // ── HTMX progress timer (unified .pj-progress component) ──────────────
  //
  // Every HTMX request whose source has hx-indicator="#some-id" attached to
  // a .pj-progress span gets a JS-driven seconds counter. Mirrors the
  // Research tab's status panel (circle + label + timer) so all in-flight
  // LLM/render actions look the same.
  //
  // Robustness: we resolve the indicator from hx-indicator OR (fallback) the
  // closest `.pj-progress` sibling — covers cases where the attribute lives
  // on a parent form.
  function bindHtmxProgressTimers() {
    var activeIntervals = new Map();

    function resolveIndicator(elt) {
      if (!elt) return null;
      var sel = elt.getAttribute && elt.getAttribute('hx-indicator');
      if (!sel) {
        var parent = elt.closest && elt.closest('[hx-indicator]');
        if (parent) sel = parent.getAttribute('hx-indicator');
      }
      if (!sel) return null;
      try {
        return document.querySelector(sel);
      } catch (e) {
        return null;
      }
    }

    function startTimer(indicator) {
      if (!indicator) return;
      var timerEl = indicator.querySelector('[data-timer]');
      if (!timerEl) return;
      // Clear any leftover interval for this indicator (defensive: re-clicks).
      var prev = activeIntervals.get(indicator);
      if (prev) clearInterval(prev);
      var startedAt = Date.now();
      timerEl.textContent = '0s';
      var iv = setInterval(function () {
        timerEl.textContent = Math.floor((Date.now() - startedAt) / 1000) + 's';
      }, 250);
      activeIntervals.set(indicator, iv);
    }

    function stopTimer(indicator) {
      if (!indicator) return;
      var iv = activeIntervals.get(indicator);
      if (iv) {
        clearInterval(iv);
        activeIntervals.delete(indicator);
      }
    }

    document.body.addEventListener('htmx:beforeRequest', function (evt) {
      var ind = resolveIndicator(evt.detail && evt.detail.elt);
      startTimer(ind);
    });
    // htmx:afterRequest fires whether 2xx or error — stop the timer either way.
    document.body.addEventListener('htmx:afterRequest', function (evt) {
      var ind = resolveIndicator(evt.detail && evt.detail.elt);
      stopTimer(ind);
    });
    document.body.addEventListener('htmx:sendError', function (evt) {
      var ind = resolveIndicator(evt.detail && evt.detail.elt);
      stopTimer(ind);
    });
  }

  // ── HTMX error → toast ─────────────────────────────────────────────────
  //
  // HTMX silently ignores 4xx/5xx responses by default — no swap, no UI hint.
  // Surface the server's detail message via showToast so the user knows what
  // went wrong (e.g. Iterate-without-checking returns 400 with helpful text).

  function bindHtmxErrors() {
    document.body.addEventListener('htmx:responseError', function (e) {
      var xhr = e.detail && e.detail.xhr;
      if (!xhr) return;
      var status = xhr.status || 0;
      var msg = '';
      try {
        var body = JSON.parse(xhr.responseText || '{}');
        msg = body.detail || body.error || '';
      } catch (parseErr) {
        msg = (xhr.responseText || '').slice(0, 240);
      }
      if (!msg) msg = 'HTTP ' + status;
      showToast('⚠ ' + msg);
    });
    // Network failure (server down / CORS): different event.
    document.body.addEventListener('htmx:sendError', function () {
      showToast('⚠ 網路錯誤 — 連不上 server，檢查 dev server 是不是還活著。');
    });
  }

  function bindCopyTargets() {
    document.body.addEventListener('click', function (e) {
      var btn = e.target && e.target.closest && e.target.closest('[data-copy-target]');
      if (!btn) return;
      var targetId = btn.getAttribute('data-copy-target');
      var target = targetId ? document.getElementById(targetId) : null;
      if (!target) {
        showToast('⚠ Copy target not found');
        return;
      }
      var value = target.value || target.textContent || '';
      if (!value.trim()) {
        showToast('⚠ Nothing to copy');
        return;
      }
      function fallbackSelect() {
        if (typeof target.select === 'function') {
          target.focus();
          target.select();
        }
        showToast('Clipboard unavailable — selected text for manual copy');
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(value).then(function () {
          showToast('Copied');
        }).catch(fallbackSelect);
      } else {
        fallbackSelect();
      }
    });
  }

  function bootAll() {
    bindTabs();
    bindPomodoroDock();
    bindCounters();
    bindResearchActions();
    bindDrPasteForm();
    bindDialogs();
    bindManualPomodoro();
    bindStatusDropdown();
    bindDeleteTask();
    bindDockTasksClickOutside();
    bindHtmxErrors();
    bindHtmxProgressTimers();
    bindCopyTargets();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootAll);
  } else {
    bootAll();
  }
})();
