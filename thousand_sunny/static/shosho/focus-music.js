/* Focus-music companion for the pomodoro docks (修修 2026-08-25).
 *
 * ▶ 開始計時 → resume(): 隨機抽一首邊倒數邊播，曲名顯示在 dock 的
 * [data-music-title] bar；單曲播完自動抽下一首。⏸ → pause()；完成/取消/歸零
 * → stop()。音樂永遠 best-effort：沒設定來源或播放失敗就安靜跳過，計時器不受影響。
 *
 * 音源兩層（修修: 不要上傳伺服器再串流、浪費頻寬）：
 *   1. 本地資料夾 — File System Access API。dock 的 [data-music-setup] 按鈕選一次
 *      資料夾（如 E:\data\focus music），directory handle 存 IndexedDB；之後直接
 *      從本地磁碟讀檔（objectURL），零伺服器流量。Chrome 可記住授權（「每次造訪
 *      時允許」）；還在 prompt 狀態時，▶ 的 user gesture 內 requestPermission 補問。
 *   2. 伺服器 fallback — /bridge/focus-music/random（FOCUS_MUSIC_DIR 有設才活；
 *      沒設回 available:false，等於只有本地模式）。
 *
 * Task 頁與 Project 甲板 dock 都載入這一份，播放邏輯只活在這裡。 */
window.ShoFocusMusic = (function () {
  var audio = null;
  var active = false;    // a focus block wants music (between resume() and stop())
  var dirHandle = null;  // File System Access directory handle (null = not loaded)
  var handleLoaded = false;
  var objectUrl = null;  // current local track's blob URL (revoked on switch)

  var AUDIO_RE = /\.(mp3|m4a|aac|ogg|opus|flac|wav)$/i;

  function wrap() { return document.querySelector('[data-music-title]'); }
  function nameEl() { return document.querySelector('[data-music-name]'); }
  function setupBtn() { return document.querySelector('[data-music-setup]'); }

  function show(title) {
    var w = wrap(), n = nameEl();
    if (n) n.textContent = title;
    if (w) w.hidden = false;
  }
  function hide() {
    var w = wrap();
    if (w) w.hidden = true;
  }

  /* Envato-style download names carry a provenance tail — cut it and de-kebab,
     same cleanup as the server route (routers/focus_music.py). */
  function displayTitle(stem) {
    var t = stem.replace(/-(main|full)-version.*$/i, '').replace(/[-\s]*\(?\d[\d()-]*\)?\s*$/, '');
    t = t.replace(/[-_]/g, ' ').trim();
    return t || stem;
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
    }).catch(function () { handleLoaded = true; return null; });
  }

  /* The ♪ setup button shows only when local mode is supported but unconfigured. */
  function syncSetupButton() {
    var b = setupBtn();
    if (!b) return;
    b.hidden = !('showDirectoryPicker' in window) || !!dirHandle;
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

  function listLocal(handle) {
    var out = [];
    var it = handle.values();
    function step() {
      return it.next().then(function (r) {
        if (r.done) return out;
        var e = r.value;
        if (e.kind === 'file' && AUDIO_RE.test(e.name)) out.push(e);
        return step();
      });
    }
    return step();
  }

  function playUrl(url, title) {
    if (!audio) {
      audio = new Audio();
      audio.addEventListener('ended', function () { if (active) next(); });
    }
    audio.src = url;
    return audio.play().then(function () { show(title); }).catch(hide);
  }

  function nextLocal(handle) {
    return ensurePermission(handle).then(function (ok) {
      if (!ok) throw new Error('permission');
      return listLocal(handle);
    }).then(function (files) {
      if (!files.length) throw new Error('empty');
      var pick = files[Math.floor(Math.random() * files.length)];
      return pick.getFile();
    }).then(function (file) {
      if (!active) return;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = URL.createObjectURL(file);
      return playUrl(objectUrl, displayTitle(file.name.replace(/\.[^.]+$/, '')));
    });
  }

  function nextServer() {
    return fetch('/bridge/focus-music/random', { headers: { Accept: 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : { available: false }; })
      .then(function (d) {
        if (!active) return;
        if (!d.available || !d.url) { hide(); return; }
        return playUrl(d.url, d.title || '');
      })
      .catch(hide);
  }

  function next() {
    loadHandle().then(function (h) {
      if (h) return nextLocal(h).catch(function () { return nextServer(); });
      return nextServer();
    });
  }

  // Reveal the setup affordance once the DOM + stored-handle check both settle
  // (this file loads in <head> on the Task page — defer the DOM queries).
  function init() { if (window.indexedDB) loadHandle(); else syncSetupButton(); }
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
      active = false;
      if (audio) { audio.pause(); audio.removeAttribute('src'); }
      hide();
    },
  };
})();

/* Wire the setup button wherever a dock rendered one (deferred — see above). */
(function () {
  function bind() {
    var b = document.querySelector('[data-music-setup]');
    if (b) b.addEventListener('click', function () { window.ShoFocusMusic.chooseFolder(); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
  else bind();
})();
