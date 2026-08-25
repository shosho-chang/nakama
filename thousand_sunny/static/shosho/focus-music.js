/* Focus-music companion for the pomodoro docks (修修 2026-08-25).
 *
 * ▶ 開始計時 → resume(): 抽一首 /bridge/focus-music/random 邊倒數邊播，曲名顯示在
 * dock 的 [data-music-title] bar；單曲播完自動抽下一首（25 分 > 單曲長度）。
 * ⏸ → pause()；完成/取消/自然倒數結束 → stop()。音樂永遠 best-effort：資料庫沒
 * 設定（available:false）或 fetch/play 失敗時安靜跳過，計時器完全不受影響。
 *
 * Loaded by the Task page dock AND the Project 甲板 dock — both call the same
 * three verbs, so the播放邏輯只活在這一份檔案。 */
window.ShoFocusMusic = (function () {
  var audio = null;
  var active = false; // a focus block wants music (between resume() and stop())

  function wrap() { return document.querySelector('[data-music-title]'); }
  function nameEl() { return document.querySelector('[data-music-name]'); }

  function show(title) {
    var w = wrap(), n = nameEl();
    if (n) n.textContent = title;
    if (w) w.hidden = false;
  }
  function hide() {
    var w = wrap();
    if (w) w.hidden = true;
  }

  function next() {
    fetch('/bridge/focus-music/random', { headers: { Accept: 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : { available: false }; })
      .then(function (d) {
        if (!active) return;                    // stopped while the fetch was in flight
        if (!d.available || !d.url) { hide(); return; }
        if (!audio) {
          audio = new Audio();
          audio.addEventListener('ended', function () { if (active) next(); });
        }
        audio.src = d.url;
        audio.play().then(function () { show(d.title || ''); }).catch(hide);
      })
      .catch(hide);
  }

  return {
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
