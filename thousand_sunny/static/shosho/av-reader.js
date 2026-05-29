// Robin AV Reader · YouTube IFrame Player API + cue follow + annotation write
//
// ADR-035 §D6 — player mode is read-only viewing; editor mode (PR2b) adds
// inline annotation write via ★ button / N-key / Ctrl+B with cast chip +
// note textarea. The player is YouTube's iframe embed for ToS compliance;
// we never extract stream URLs.
//
// Mode state machine (grid data-mode):
//   • "player"  — Space play/pause, J/L ±10s, cue click = seek+play,
//                 N enters editor, ★ + Ctrl+B = quick highlight (stays in
//                 player mode after save).
//   • "editor"  — Esc/Cancel exits to player, Ctrl+Enter or Save POSTs,
//                 cue click = retarget (no seek), arrow keys live in
//                 textarea normally.

(function () {
  'use strict';

  const root = document.getElementById('avReader');
  if (!root) return;

  const videoId = root.dataset.videoId;
  const defaultSpeed = parseFloat(root.dataset.defaultSpeed || '1.5');

  // ── Static payloads (cues / cast) ─────────────────────────────────
  function readJsonNode(id, fallback) {
    const node = document.getElementById(id);
    if (!node) return fallback;
    try {
      return JSON.parse(node.textContent || JSON.stringify(fallback));
    } catch (e) {
      return fallback;
    }
  }
  const cues = readJsonNode('cuesJson', []);
  const cast = readJsonNode('castJson', []);

  const cueListEl = document.getElementById('cueList');
  const cueEls = cueListEl ? Array.from(cueListEl.querySelectorAll('.cue-item')) : [];
  const speedSelect = document.getElementById('speedSelect');
  const annScroll = document.getElementById('annScroll');
  let annList = document.getElementById('annList');
  let annEmpty = document.getElementById('annEmpty');
  const annCount = document.getElementById('annCount');
  const annEditor = document.getElementById('annEditor');
  const annTextarea = document.getElementById('annTextarea');
  const annSaveBtn = document.getElementById('annSaveBtn');
  const annCancelBtn = document.getElementById('annCancelBtn');
  const annEditorTarget = document.getElementById('annEditorTarget');
  const chipEls = annEditor
    ? Array.from(annEditor.querySelectorAll('.ann-chip'))
    : [];

  let player = null;
  let activeIdx = -1;     // cue index under playhead
  let targetIdx = -1;     // cue index the editor is anchored to
  let pollTimer = null;
  let saving = false;     // guards concurrent POSTs

  // ── YouTube IFrame API bootstrap ──────────────────────────────────
  window.onYouTubeIframeAPIReady = function () {
    player = new YT.Player('ytPlayer', {
      videoId: videoId,
      playerVars: {
        playsinline: 1,
        rel: 0,
        modestbranding: 1,
      },
      events: {
        onReady: function () {
          try {
            player.setPlaybackRate(defaultSpeed);
          } catch (e) {
            // Some videos don't support the requested rate; fall back to
            // whatever YouTube picks.
          }
          startPolling();
        },
        onStateChange: function (ev) {
          if (ev.data === YT.PlayerState.PLAYING) startPolling();
          else stopPolling();
        },
      },
    });
  };

  if (!document.querySelector('script[data-yt-api]')) {
    const tag = document.createElement('script');
    tag.src = 'https://www.youtube.com/iframe_api';
    tag.dataset.ytApi = '1';
    document.head.appendChild(tag);
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(tick, 250);
  }
  function stopPolling() {
    if (!pollTimer) return;
    clearInterval(pollTimer);
    pollTimer = null;
  }

  function tick() {
    if (!player || typeof player.getCurrentTime !== 'function') return;
    const t = player.getCurrentTime();
    const idx = findActiveCue(t);
    if (idx !== activeIdx) setActive(idx);
  }

  function findActiveCue(t) {
    let lo = 0;
    let hi = cues.length - 1;
    let candidate = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (cues[mid].start <= t) {
        candidate = mid;
        lo = mid + 1;
      } else {
        hi = mid - 1;
      }
    }
    if (candidate < 0) return -1;
    if (cues[candidate].end !== null && t >= cues[candidate].end) {
      return candidate;
    }
    return candidate;
  }

  function setActive(idx) {
    if (activeIdx >= 0 && cueEls[activeIdx]) {
      cueEls[activeIdx].classList.remove('is-active');
    }
    activeIdx = idx;
    if (idx >= 0 && cueEls[idx]) {
      cueEls[idx].classList.add('is-active');
      // Editor anchor == playhead. Keep the editor target label and
      // .is-target marker in sync so there's only ever one highlight.
      if (getMode() === 'editor') {
        syncEditorTarget(idx);
      }
      const el = cueEls[idx];
      const scroll = el.closest('.cue-scroll');
      if (scroll) {
        const elRect = el.getBoundingClientRect();
        const scRect = scroll.getBoundingClientRect();
        if (elRect.top < scRect.top + 40 || elRect.bottom > scRect.bottom - 40) {
          el.scrollIntoView({ block: 'center', behavior: 'smooth' });
        }
      }
    }
  }

  // ── Mode state machine ────────────────────────────────────────────
  function getMode() {
    return root.dataset.mode || 'player';
  }
  function setMode(mode) {
    root.dataset.mode = mode;
  }

  // ── Cue interactions (click = seek in player; retarget in editor) ─
  cueEls.forEach((el, i) => {
    el.addEventListener('click', (ev) => {
      // Star button has its own handler — don't double-fire.
      if (ev.target && ev.target.classList.contains('cue-star')) return;
      if (getMode() === 'editor') {
        retargetCue(i);
      } else {
        seekTo(cues[i].start, /*play=*/true);
      }
    });
    el.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') {
        ev.preventDefault();
        el.click();
      }
    });
  });

  function seekTo(start, play) {
    if (!player || typeof player.seekTo !== 'function') return;
    player.seekTo(start, true);
    if (play && typeof player.playVideo === 'function') player.playVideo();
  }

  // ── Speed dropdown ────────────────────────────────────────────────
  if (speedSelect) {
    speedSelect.value = String(defaultSpeed);
    speedSelect.addEventListener('change', () => {
      const rate = parseFloat(speedSelect.value);
      if (player && typeof player.setPlaybackRate === 'function') {
        player.setPlaybackRate(rate);
      }
    });
  }

  // ── Annotation row click (PR2a) — seek + pause ────────────────────
  function bindAnnRow(row) {
    const startAttr = row.getAttribute('data-start');
    if (startAttr === null) return;
    const start = parseFloat(startAttr);
    if (!Number.isFinite(start)) return;
    const handler = () => {
      if (!player || typeof player.seekTo !== 'function') return;
      player.seekTo(start, true);
      if (typeof player.pauseVideo === 'function') {
        try { player.pauseVideo(); } catch (e) { /* primary effect is seek */ }
      }
    };
    row.addEventListener('click', handler);
    row.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') {
        ev.preventDefault();
        handler();
      }
    });
  }
  if (annList) {
    Array.from(annList.querySelectorAll('.ann-row')).forEach(bindAnnRow);
  }

  // ── Editor: cast chips with sticky default ────────────────────────
  const LAST_SPEAKER_KEY = 'av_reader_last_speaker';
  function readLastSpeaker() {
    try { return window.localStorage.getItem(LAST_SPEAKER_KEY) || ''; }
    catch (e) { return ''; }
  }
  function writeLastSpeaker(name) {
    try { window.localStorage.setItem(LAST_SPEAKER_KEY, name); }
    catch (e) { /* private mode / disabled storage — silently degrade */ }
  }
  function activeChipName() {
    const sel = chipEls.find((c) => c.getAttribute('aria-checked') === 'true');
    return sel ? sel.dataset.speaker : '';
  }
  function setActiveChip(name) {
    let matched = false;
    chipEls.forEach((c) => {
      const isMatch = !!name && c.dataset.speaker === name;
      c.setAttribute('aria-checked', isMatch ? 'true' : 'false');
      c.tabIndex = isMatch ? 0 : -1;
      if (isMatch) matched = true;
    });
    if (matched) writeLastSpeaker(name);
  }
  chipEls.forEach((chip) => {
    chip.addEventListener('click', () => setActiveChip(chip.dataset.speaker));
  });

  // ── Editor: open / close / retarget ───────────────────────────────
  // Editor anchor == current playhead (activeIdx). There is no independent
  // target state — the highlight follows playback. Clicking a cue in editor
  // mode seeks the player to that cue; if playback continues, the anchor
  // moves with it. Save uses whatever cue is active at submit time.
  function openEditor(cueIdx, opts) {
    if (!annEditor) return;
    opts = opts || {};
    setMode('editor');
    // Seek to the requested cue if it differs from current playhead — the
    // active follow will then carry the editor anchor.
    if (cueIdx >= 0 && cueIdx < cues.length && cueIdx !== activeIdx) {
      seekTo(cues[cueIdx].start, /*play=*/false);
      setActive(cueIdx);
    } else {
      syncEditorTarget(activeIdx);
    }
    // Restore last-used chip (sticky cross-write).
    if (chipEls.length) {
      const last = readLastSpeaker();
      const exists = cast.indexOf(last) >= 0;
      setActiveChip(exists ? last : '');
    }
    if (!opts.skipFocus && annTextarea) {
      // Focus async so the keystroke that opened the editor doesn't land
      // inside the textarea (N would otherwise type 'n').
      setTimeout(() => annTextarea.focus(), 0);
    }
  }
  function closeEditor() {
    setMode('player');
    if (annTextarea) annTextarea.value = '';
    syncEditorTarget(-1);
  }
  function syncEditorTarget(cueIdx) {
    if (targetIdx >= 0 && cueEls[targetIdx]) {
      cueEls[targetIdx].classList.remove('is-target');
    }
    targetIdx = cueIdx;
    if (cueIdx >= 0 && cueEls[cueIdx]) {
      cueEls[cueIdx].classList.add('is-target');
      const cue = cues[cueIdx];
      if (annEditorTarget && cue) {
        annEditorTarget.textContent = `${cue.label}  ·  ${truncate(cue.text, 40)}`;
      }
    } else if (annEditorTarget) {
      annEditorTarget.textContent = '--:--';
    }
  }
  function retargetCue(cueIdx) {
    // Seek + let the playhead drive the highlight. Don't pause — user wants
    // the highlight to keep following playback after the jump.
    const cue = cues[cueIdx];
    if (cue) {
      seekTo(cue.start, /*play=*/true);
      setActive(cueIdx);
    }
    // Keep keyboard focus on textarea so Ctrl+Enter still works.
    if (annTextarea) annTextarea.focus();
  }

  function truncate(text, max) {
    if (!text) return '';
    return text.length > max ? text.slice(0, max - 1) + '…' : text;
  }

  // ── Save (POST → ADR-017 store) ───────────────────────────────────
  async function postAnnotation(payload) {
    const resp = await fetch(
      `/robin/watchlist/${encodeURIComponent(videoId)}/annotation`,
      {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }
    );
    if (!resp.ok) {
      let detail = '';
      try {
        const data = await resp.json();
        detail = data && data.detail ? data.detail : '';
      } catch (e) { /* non-JSON error body */ }
      throw new Error(detail || `儲存失敗 (${resp.status})`);
    }
    return resp.json();
  }

  function buildPayload(cueIdx, note) {
    const cue = cues[cueIdx];
    if (!cue) return null;
    const end = cue.end !== null && cue.end !== undefined ? cue.end : cue.start;
    return {
      cue_start: cue.start,
      cue_end: end,
      excerpt: cue.text || '',
      speaker: activeChipName(),
      note: (note || '').trim(),
      highlight: !((note || '').trim()),
    };
  }

  async function saveFromEditor() {
    if (saving) return;
    // Anchor to current playhead — there's no separate target concept.
    if (activeIdx < 0) return;
    const note = annTextarea ? annTextarea.value : '';
    const payload = buildPayload(activeIdx, note);
    if (!payload) return;
    await commitSave(payload, /*returnToPlayer=*/true);
  }

  async function quickHighlight(cueIdx) {
    if (saving) return;
    if (cueIdx < 0 || cueIdx >= cues.length) return;
    const payload = buildPayload(cueIdx, '');
    if (!payload) return;
    await commitSave(payload, /*returnToPlayer=*/false);
  }

  async function commitSave(payload, returnToPlayer) {
    saving = true;
    if (annSaveBtn) annSaveBtn.disabled = true;
    try {
      const result = await postAnnotation(payload);
      if (result && result.annotation) {
        prependAnnotationRow(result.annotation);
        // Reflect on the cue list border-left marker.
        const idx = findCueIndexForStart(payload.cue_start);
        if (idx >= 0 && cueEls[idx]) {
          cueEls[idx].classList.add('has-annotation');
        }
      }
      if (returnToPlayer) closeEditor();
    } catch (err) {
      console.error('annotation save failed', err);
      // Soft surface — alert is the project's existing fallback (see syncBtn).
      alert(err && err.message ? err.message : '儲存失敗');
    } finally {
      saving = false;
      if (annSaveBtn) annSaveBtn.disabled = false;
    }
  }

  function findCueIndexForStart(start) {
    // Cues are sorted; tolerance 50ms (mirrors server-side _nearest_cue_index).
    for (let i = 0; i < cues.length; i += 1) {
      if (Math.abs(cues[i].start - start) <= 0.05) return i;
    }
    return -1;
  }

  // ── DOM update: insert new annotation row, swap empty state ───────
  function prependAnnotationRow(ann) {
    // Lazily create the list container if the page rendered empty (no
    // annotations yet) — the template suppresses ``#annList`` in that
    // case so the empty-state copy is the only child.
    if (!annList) {
      if (!annScroll) return;
      if (annEmpty) {
        annEmpty.remove();
        annEmpty = null;
      }
      annList = document.createElement('div');
      annList.className = 'ann-list';
      annList.id = 'annList';
      annScroll.prepend(annList);
    } else if (annEmpty) {
      annEmpty.remove();
      annEmpty = null;
    }
    const row = document.createElement('div');
    row.className = 'ann-row';
    if (ann.start === null || ann.start === undefined) {
      row.classList.add('ann-row--orphan');
    } else {
      row.setAttribute('data-start', String(ann.start));
    }
    row.tabIndex = 0;
    row.setAttribute('role', 'button');
    row.setAttribute('aria-label', `跳到 ${ann.label}`);

    const time = document.createElement('div');
    time.className = 'ann-time';
    time.textContent = ann.label || '--:--';
    row.appendChild(time);

    const body = document.createElement('div');
    body.className = 'ann-body';
    if (ann.speaker) {
      const sp = document.createElement('span');
      sp.className = 'ann-speaker';
      sp.textContent = ann.speaker;
      body.appendChild(sp);
    }
    if (ann.excerpt) {
      const ex = document.createElement('span');
      ex.className = 'ann-excerpt';
      ex.textContent = ann.excerpt;
      body.appendChild(ex);
    }
    if (ann.note) {
      const sep = document.createElement('span');
      sep.className = 'ann-sep';
      sep.textContent = '—';
      body.appendChild(sep);
      const note = document.createElement('span');
      note.className = 'ann-note';
      note.textContent = ann.note;
      body.appendChild(note);
    } else if (ann.type === 'highlight') {
      const sep = document.createElement('span');
      sep.className = 'ann-sep';
      sep.textContent = '·';
      body.appendChild(sep);
      const note = document.createElement('span');
      note.className = 'ann-note ann-note--muted';
      note.textContent = '(no note)';
      body.appendChild(note);
    }
    row.appendChild(body);

    annList.prepend(row);
    bindAnnRow(row);

    // Update count badge.
    const newCount = annList.querySelectorAll('.ann-row').length;
    if (annCount) annCount.textContent = `${newCount} 則`;
  }

  // ── ★ button: quick highlight on the cue carrying the star ───────
  if (cueListEl) {
    cueListEl.addEventListener('click', (ev) => {
      const btn = ev.target.closest('.cue-star');
      if (!btn) return;
      // Stop the click bubbling into the cue-item handler (which would seek).
      ev.stopPropagation();
      ev.preventDefault();
      const idx = parseInt(btn.dataset.cueIndex || '-1', 10);
      if (idx >= 0) quickHighlight(idx);
    });
  }

  // ── Save / Cancel buttons ─────────────────────────────────────────
  if (annSaveBtn) annSaveBtn.addEventListener('click', () => { saveFromEditor(); });
  if (annCancelBtn) annCancelBtn.addEventListener('click', () => closeEditor());

  // ── Global keyboard handler (mode-aware) ──────────────────────────
  document.addEventListener('keydown', (ev) => {
    const mode = getMode();
    const inTextField = ev.target && /^(input|textarea|select)$/i.test(ev.target.tagName);

    // Esc — always exits editor, even when focused in textarea.
    if (ev.key === 'Escape') {
      if (mode === 'editor') {
        ev.preventDefault();
        closeEditor();
      }
      return;
    }

    // Ctrl+Enter saves from editor.
    if (mode === 'editor' && (ev.key === 'Enter') && (ev.ctrlKey || ev.metaKey)) {
      ev.preventDefault();
      saveFromEditor();
      return;
    }

    // Editor mode: let the textarea own everything else.
    if (mode === 'editor') return;

    // ── Player-mode shortcuts ──
    if (inTextField) return;
    if (!player) return;

    // Ctrl+B = quick highlight on the active cue.
    if ((ev.ctrlKey || ev.metaKey) && (ev.key === 'b' || ev.key === 'B')) {
      ev.preventDefault();
      if (activeIdx >= 0) quickHighlight(activeIdx);
      return;
    }

    // N = open editor on active cue.
    if (ev.key === 'n' || ev.key === 'N') {
      // Only fire when no modifier so we don't eat Ctrl+N (browser new
      // window) or Cmd+N. Pure 'n' is the open trigger.
      if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
      ev.preventDefault();
      // No active cue → editor would open with target=-1 and Save would
      // silently no-op. Fall back to the first cue so user can retarget
      // from the cue list rather than opening an unusable editor.
      const seed = activeIdx >= 0 ? activeIdx : (cues.length > 0 ? 0 : -1);
      if (seed < 0) return;  // no cues at all → nothing to anchor to
      openEditor(seed, {});
      return;
    }

    if (ev.key === ' ') {
      ev.preventDefault();
      const state = player.getPlayerState();
      if (state === YT.PlayerState.PLAYING) player.pauseVideo();
      else player.playVideo();
    } else if (ev.key === 'j' || ev.key === 'J') {
      ev.preventDefault();
      player.seekTo(Math.max(0, player.getCurrentTime() - 10), true);
    } else if (ev.key === 'l' || ev.key === 'L') {
      ev.preventDefault();
      player.seekTo(player.getCurrentTime() + 10, true);
    }
  });

  // ── "同步到 KB" button — stubbed for PR3. ─────────────────────────
  const syncBtn = document.getElementById('syncBtn');
  if (syncBtn) {
    syncBtn.addEventListener('click', () => {
      alert('同步到 KB 將在 PR3 開放');
    });
  }
})();
