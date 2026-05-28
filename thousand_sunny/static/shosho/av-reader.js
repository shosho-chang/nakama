// Robin AV Reader · YouTube IFrame Player API integration + cue follow
//
// ADR-035 §D6 — read-only viewing surface. No annotation save (PR2). The
// player is YouTube's iframe embed for ToS compliance; we never extract
// stream URLs.
//
// State machine: load YT API → instantiate Player → poll currentTime →
// highlight matching cue. Click cue → seek. Space/J/L keyboard shortcuts.

(function () {
  'use strict';

  const root = document.getElementById('avReader');
  if (!root) return;

  const videoId = root.dataset.videoId;
  const defaultSpeed = parseFloat(root.dataset.defaultSpeed || '1.5');

  const cuesNode = document.getElementById('cuesJson');
  let cues = [];
  try {
    cues = JSON.parse(cuesNode.textContent || '[]');
  } catch (e) {
    cues = [];
  }

  const cueListEl = document.getElementById('cueList');
  const cueEls = cueListEl ? Array.from(cueListEl.querySelectorAll('.cue-item')) : [];
  const speedSelect = document.getElementById('speedSelect');

  let player = null;
  let activeIdx = -1;
  let pollTimer = null;

  // Load YouTube IFrame API. The API calls onYouTubeIframeAPIReady when
  // loaded; we attach our handler globally.
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

  // Inject API script if not present.
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
    // Binary search — cues are sorted by start time.
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
    // Only "active" while currentTime is inside [start, end).
    if (cues[candidate].end !== null && t >= cues[candidate].end) {
      // Between cues — keep the last one dimmed-active so the reader's
      // eye doesn't jump back to zero. Return -1 instead if you prefer
      // strict in-range only.
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
      // Scroll into view only if outside the visible band.
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

  // Cue click → seek.
  cueEls.forEach((el, i) => {
    el.addEventListener('click', () => {
      if (player && typeof player.seekTo === 'function') {
        player.seekTo(cues[i].start, true);
        player.playVideo();
      }
    });
    el.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') {
        ev.preventDefault();
        el.click();
      }
    });
  });

  // Speed dropdown → player.setPlaybackRate
  if (speedSelect) {
    speedSelect.value = String(defaultSpeed);
    speedSelect.addEventListener('change', () => {
      const rate = parseFloat(speedSelect.value);
      if (player && typeof player.setPlaybackRate === 'function') {
        player.setPlaybackRate(rate);
      }
    });
  }

  // Keyboard shortcuts: Space play/pause, J/L ±10s.
  document.addEventListener('keydown', (ev) => {
    if (ev.target && /input|textarea|select/i.test(ev.target.tagName)) return;
    if (!player) return;
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

  // "同步到 KB" button — stubbed for PR3.
  const syncBtn = document.getElementById('syncBtn');
  if (syncBtn) {
    syncBtn.addEventListener('click', () => {
      alert('同步到 KB 將在 PR3 開放');
    });
  }
})();
