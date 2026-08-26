/* Focus-music companion for the pomodoro docks (修修 2026-08-25).
 *
 * ▶ 開始計時 → resume(): 隨機抽一首邊倒數邊播，曲名顯示在 dock 的
 * [data-music-title] bar；單曲播完自動抽下一首。⏸ → pause()；完成/取消/歸零
 * → stop()。計時器每個 tick 呼叫 sync(remainingSec)：
 *   - 剩 ≤60 秒 → 切到 uplifting 分類（振奮曲，宣告 session 要收尾了）
 *   - 剩 ≤5 秒 → 漸弱到靜音（不驟停）
 * 音樂永遠 best-effort：沒設定來源或播放失敗就安靜跳過，計時器不受影響。
 *
 * 音量：dock 的 [data-music-vol] range（0–100），localStorage 記住；漸弱以
 * 使用者音量為 100% 基準做比例衰減。
 *
 * 音源兩層（修修: 不上傳伺服器再串流、浪費頻寬）：
 *   1. 本地資料夾 — File System Access API。[data-music-setup] 按鈕選一次根資料夾
 *      （如 E:\data\music，內含 focus* / uplifting* 兩個分類子資料夾；沒有分類子
 *      資料夾就把根目錄的音檔全當 focus），handle 存 IndexedDB。零伺服器流量。
 *   2. 伺服器 fallback — /bridge/focus-music/random?kind=…（FOCUS_MUSIC_DIR 有設
 *      才活；沒設回 available:false，等於只有本地模式）。
 *
 * Task 頁與 Project 甲板 dock 都載入這一份，播放邏輯只活在這裡。 */
window.ShoFocusMusic = (function () {
  var audio = null;
  var active = false;     // a focus block wants music (between resume() and stop())
  var phase = 'focus';    // 'focus' | 'finale' (last-minute uplifting switch done)
  var fade = null;        // {t0, from, to, ms, stopAfter} — the running volume ramp
  var dirHandle = null;   // File System Access directory handle (null = not loaded)
  var handleLoaded = false;
  var objectUrl = null;   // current local track's blob URL (revoked on switch)
  var masterVol = 1;      // 0..1 — the user's slider setting (fades scale within it)

  var AUDIO_RE = /\.(mp3|m4a|aac|ogg|opus|flac|wav)$/i;
  var FINALE_AT = 60;     // s remaining → switch to uplifting
  var FADE_LAST = 5;      // s remaining → start the fade-to-silence

  try { masterVol = Math.min(1, Math.max(0, parseFloat(localStorage.getItem('sho-music-vol') || '1'))); }
  catch (_) { /* storage unavailable — keep 1 */ }
  if (isNaN(masterVol)) masterVol = 1;

  function wrap() { return document.querySelector('[data-music-title]'); }
  function nameEl() { return document.querySelector('[data-music-name]'); }
  function setupBtn() { return document.querySelector('[data-music-setup]'); }
  function volEl() { return document.querySelector('[data-music-vol]'); }

  function show(title) {
    var w = wrap(), n = nameEl();
    if (n) n.textContent = title;
    if (w) w.hidden = false;
    var v = volEl();
    if (v) v.hidden = false;
  }
  /* a status line (not a track) — no volume slider alongside it */
  function note(msg) {
    var w = wrap(), n = nameEl(), v = volEl();
    if (n) n.textContent = msg;
    if (w) w.hidden = false;
    if (v) v.hidden = true;
  }
  function hide() {
    var w = wrap(), v = volEl();
    if (w) w.hidden = true;
    if (v) v.hidden = true;
  }

  /* Envato-style download names carry a provenance tail — cut it and de-kebab,
     same cleanup as the server route (routers/focus_music.py). */
  function displayTitle(stem) {
    var t = stem.replace(/-(main|full)-version.*$/i, '').replace(/[-\s]*\(?\d[\d()-]*\)?\s*$/, '');
    t = t.replace(/[-_]/g, ' ').trim();
    return t || stem;
  }

  /* ── volume + fades ──
     setInterval-driven, NOT requestAnimationFrame — rAF suspends in background
     tabs, and a block routinely ends while 修修 works in another window; the
     wall-clock k keeps the ramp correct even under background timer throttling. */
  var fadeTimer = null;
  function stepFade() {
    if (!audio || !fade) { clearFade(); return; }
    var k = Math.min(1, (Date.now() - fade.t0) / fade.ms);
    var scale = fade.from + (fade.to - fade.from) * k;
    audio.volume = Math.min(1, Math.max(0, masterVol * scale));
    if (k >= 1) {
      var stopAfter = fade.stopAfter;
      clearFade();
      if (stopAfter) audio.pause();
    }
  }
  function clearFade() {
    fade = null;
    if (fadeTimer) { clearInterval(fadeTimer); fadeTimer = null; }
  }
  function startFade(from, to, ms, stopAfter) {
    clearFade();
    fade = { t0: Date.now(), from: from, to: to, ms: Math.max(120, ms), stopAfter: !!stopAfter };
    fadeTimer = setInterval(stepFade, 100);
    stepFade();
  }

  /* ── IndexedDB — persist the directory handle across visits ── */
  function idb() {
    return new Promise(function (resolve, reject) {
      var req = indexedDB.open('sho-focus-music', 1);
      req.onupgradeneeded = function () { req.result.createObjectStore('handles'); };
      req.onsuccess = function () { resolve(req.result); };
      req.onerror = function () { reject(req.error); };
    });
  }
  function idbGet(key) {
    return idb().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx = db.transaction('handles', 'readonly').objectStore('handles').get(key);
        tx.onsuccess = function () { resolve(tx.result || null); };
        tx.onerror = function () { reject(tx.error); };
      });
    });
  }
  function idbSet(key, val) {
    return idb().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx = db.transaction('handles', 'readwrite').objectStore('handles').put(val, key);
        tx.onsuccess = function () { resolve(); };
        tx.onerror = function () { reject(tx.error); };
      });
    });
  }

  function loadHandle() {
    if (handleLoaded) return Promise.resolve(dirHandle);
    return idbGet('dir').then(function (h) {
      handleLoaded = true; dirHandle = h || null;
      syncSetupButton();
      return dirHandle;
    }).catch(function () {
      handleLoaded = true; dirHandle = null;
      syncSetupButton();   // IndexedDB unavailable ⇒ still offer the picker
      return null;
    });
  }

  /* The ♪ folder control is ALWAYS available wherever local mode is supported —
     it is the only way to point the player somewhere else. 修修 2026-08-26 血淚:
     hiding it once a handle existed left NO way to re-pick after the library moved
     (E:\data\focus music → E:\data\music), and the stale handle then failed with
     NotFoundError while the UI stayed silent — 按 ▶ 完全沒反應、也沒得改。 */
  function syncSetupButton() {
    var b = setupBtn();
    if (!b) return;
    b.hidden = !('showDirectoryPicker' in window);
    b.textContent = dirHandle ? '♪ ' + dirHandle.name : '♪ 音樂資料夾';
    b.title = dirHandle
      ? '目前音樂資料夾：' + dirHandle.name + '（點擊可換一個）'
      : '選一個本地音樂資料夾（內含 focus / uplifting 分類子資料夾）——之後按 ▶ 就會隨機播放，不經過伺服器';
  }

  /* A stored handle that no longer resolves (folder moved/renamed/deleted, or read
     permission revoked) is worse than none: it silently swallows every ▶. Drop it,
     put the picker back in its unconfigured state, and SAY so in the bar. */
  function forgetHandle(msg) {
    dirHandle = null;
    idbSet('dir', null).catch(function () { /* best-effort */ });
    syncSetupButton();
    if (msg) note(msg);
  }

  function ensurePermission(handle) {
    if (!handle.queryPermission) return Promise.resolve(true);
    return handle.queryPermission({ mode: 'read' }).then(function (st) {
      if (st === 'granted') return true;
      // still inside the ▶ click's transient activation → the prompt is allowed
      return handle.requestPermission({ mode: 'read' }).then(function (st2) {
        return st2 === 'granted';
      });
    }).catch(function () { return false; });
  }

  function iterate(handle, onEntry) {
    var it = handle.values();
    function step() {
      return it.next().then(function (r) {
        if (r.done) return null;
        onEntry(r.value);
        return step();
      });
    }
    return step();
  }

  /* Category pools: kind-prefixed subdirectories (focus* / uplifting*) win; audio
     files sitting at the root count as focus (flat-folder backwards compat). */
  function listLocal(handle, kind) {
    var subdirs = [], rootFiles = [];
    return iterate(handle, function (e) {
      if (e.kind === 'directory') subdirs.push(e);
      else if (e.kind === 'file' && AUDIO_RE.test(e.name)) rootFiles.push(e);
    }).then(function () {
      var match = subdirs.filter(function (d) {
        return d.name.toLowerCase().indexOf(kind) === 0;
      })[0];
      if (!match) return kind === 'focus' ? rootFiles : [];
      var files = [];
      return iterate(match, function (e) {
        if (e.kind === 'file' && AUDIO_RE.test(e.name)) files.push(e);
      }).then(function () { return files; });
    });
  }

  function playUrl(url, title) {
    if (!audio) {
      audio = new Audio();
      audio.addEventListener('ended', function () { if (active) next(); });
    }
    clearFade();                    // a fresh track always starts at slider volume
    audio.src = url;
    audio.volume = masterVol;
    return audio.play().then(function () { show(title); }).catch(hide);
  }

  function nextLocal(handle, kind) {
    return ensurePermission(handle).then(function (ok) {
      if (!ok) { var pe = new Error('permission'); pe.gone = true; throw pe; }
      return listLocal(handle, kind).catch(function (e) {
        // folder moved / renamed / deleted since the handle was stored
        if (e && e.name === 'NotFoundError') { e.gone = true; }
        throw e;
      });
    }).then(function (files) {
      if (!files.length) throw new Error('empty:' + kind);
      var pick = files[Math.floor(Math.random() * files.length)];
      return pick.getFile();
    }).then(function (file) {
      if (!active) return;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = URL.createObjectURL(file);
      return playUrl(objectUrl, displayTitle(file.name.replace(/\.[^.]+$/, '')));
    });
  }

  function nextServer(kind) {
    return fetch('/bridge/focus-music/random?kind=' + kind, { headers: { Accept: 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : { available: false }; })
      .then(function (d) {
        if (!active) return true;               // stopped meanwhile — nothing left to do
        if (!d.available || !d.url) return false;  // pool empty — let the caller fall back
        return playUrl(d.url, d.title || '').then(function () { return true; });
      })
      .catch(function () { return false; });
  }

  function next() {
    var kind = phase === 'finale' ? 'uplifting' : 'focus';
    /* server fallback, then give up — but never in silence: an unreachable library
       explains itself in the bar so ▶ is never a dead button. */
    function viaServer(k) {
      return nextServer(k).then(function (ok) {
        if (ok) return true;
        if (k === 'uplifting') return nextServer('focus');
        return false;
      }).then(function (ok) {
        if (!ok && !dirHandle) note('選一個音樂資料夾就會開始播放 →');
        else if (!ok) hide();
        return ok;
      });
    }
    loadHandle().then(function (h) {
      if (!h) return viaServer(kind);
      return nextLocal(h, kind).catch(function (err) {
        if (err && err.gone) {                       // handle dead → drop it, tell 修修
          forgetHandle('♪ 找不到音樂資料夾 — 請重新選擇 →');
          return viaServer(kind);
        }
        // pool merely empty: finale falls back to the focus pool, else try the server
        if (kind === 'uplifting') {
          return nextLocal(h, 'focus').catch(function () { return viaServer('focus'); });
        }
        return viaServer(kind);
      });
    });
  }

  // Reveal the setup affordance once the DOM + stored-handle check both settle
  // (this file loads in <head> on the Task page — defer the DOM queries).
  function init() {
    if (window.indexedDB) loadHandle(); else syncSetupButton();
    var v = volEl();
    if (v) v.value = String(Math.round(masterVol * 100));
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

  return {
    /* ♪ 選擇音樂資料夾 — one-time picker; the handle persists in IndexedDB. */
    chooseFolder: function () {
      if (!('showDirectoryPicker' in window)) return;
      window.showDirectoryPicker({ id: 'sho-focus-music', mode: 'read' })
        .then(function (h) {
          dirHandle = h; handleLoaded = true;
          syncSetupButton();
          idbSet('dir', h).catch(function () { /* handle lives for this visit anyway */ });
          if (active) next();  // mid-block pick → start the music right away
        })
        .catch(function () { /* picker dismissed — keep current state */ });
    },
    /* ▶ — resume the paused track, or pick a fresh one when none is loaded. */
    resume: function () {
      active = true;
      if (audio && audio.src) audio.play().catch(function () { /* best-effort */ });
      else next();
    },
    /* ⏸ — hold the track (title stays visible; the block is only paused). */
    pause: function () { if (audio) audio.pause(); },
    /* block over (logged or aborted) — silence and clear the bar. */
    stop: function () {
      active = false; phase = 'focus'; clearFade();
      if (audio) { audio.pause(); audio.removeAttribute('src'); }
      hide();
    },
    /* the timer's tick — drives the last-minute uplifting switch + final fade.
       修修: 最後 1 分鐘換振奮的歌宣告收尾；最後 5 秒漸弱到停,不要驟停。 */
    sync: function (remainingSec) {
      if (!active) return;
      if (phase !== 'finale' && remainingSec <= FINALE_AT && remainingSec > 0) {
        phase = 'finale';
        next();  // switch to the uplifting pool (falls back to focus when empty)
      }
      if (remainingSec <= FADE_LAST && remainingSec > 0 && !fade && audio && !audio.paused) {
        startFade(1, 0, remainingSec * 1000, true);
      }
    },
    /* dock volume slider (0–100) — remembered across visits. */
    setVolume: function (pct) {
      masterVol = Math.min(1, Math.max(0, pct / 100));
      try { localStorage.setItem('sho-music-vol', String(masterVol)); } catch (_) { /* ignore */ }
      if (audio && !fade) audio.volume = masterVol;
    },
  };
})();

/* Wire the dock controls wherever a dock rendered them (deferred — see above). */
(function () {
  function bind() {
    var b = document.querySelector('[data-music-setup]');
    if (b) b.addEventListener('click', function () { window.ShoFocusMusic.chooseFolder(); });
    var v = document.querySelector('[data-music-vol]');
    if (v) v.addEventListener('input', function () { window.ShoFocusMusic.setVolume(parseInt(v.value, 10) || 0); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
  else bind();
})();
