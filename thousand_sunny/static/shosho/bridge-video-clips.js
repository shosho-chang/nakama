// Bridge · 挑片段（他人影片引用）— 直接播 YouTube（不下載），按鍵把起點／終點設成播放器目前位置，
// 試播這段，標要／不要。改了就存（visual_sources/<row_id>.json），之後 DP 只下載標「要」的那幾段。
(function () {
  "use strict";

  var base = window.VC_BASE;
  var state = JSON.parse(document.getElementById("vc-picks").textContent);
  var list = document.querySelector("[data-list]");
  var tpl = document.getElementById("vc-row");
  var players = {};
  var apiReady = false;
  var pending = [];

  function fmt(sec) {
    sec = Math.max(0, Number(sec) || 0);
    var m = Math.floor(sec / 60);
    var s = (sec - m * 60).toFixed(2);
    return m + ":" + (s.length < 5 ? "0" + s : s);
  }

  // 影片長度：1:04:06（不帶小數）
  function fmtLen(sec) {
    sec = Math.round(Number(sec) || 0);
    var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    var mm = h ? String(m).padStart(2, "0") : String(m);
    return (h ? h + ":" : "") + mm + ":" + String(s).padStart(2, "0");
  }

  function request(method, url, body) {
    return fetch(url, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      credentials: "same-origin",
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) throw new Error(data.error || data.detail || "HTTP " + res.status);
        return data;
      });
    });
  }

  function summary() {
    document.querySelector("[data-need]").textContent = state.need;
    document.querySelector("[data-approved]").textContent = state.approved;
    document.querySelector("[data-approved-sec]").textContent = Number(state.approved_sec).toFixed(1);
    document.querySelector("[data-empty]").hidden = state.candidates.length > 0;
  }

  function paint(li, c) {
    li.dataset.status = c.status;
    li.querySelector("[data-title]").textContent = c.title || c.video_id;
    li.querySelector("[data-link]").href =
      "https://www.youtube.com/watch?v=" + c.video_id + "&t=" + Math.floor(c.start) + "s";
    li.querySelector("[data-channel]").textContent = c.channel;
    li.querySelector("[data-duration]").textContent = c.duration ? fmtLen(c.duration) : "";
    li.querySelector("[data-note]").textContent = c.note || "";
    li.querySelector("[data-start]").textContent = fmt(c.start);
    li.querySelector("[data-end]").textContent = fmt(c.end);
    li.querySelector("[data-len]").textContent = (c.end - c.start).toFixed(1) + " 秒";
    var input = li.querySelector('[name="source_label"]');
    if (document.activeElement !== input) input.value = c.source_label;
    li.querySelectorAll("[data-status]").forEach(function (b) {
      b.setAttribute("aria-checked", b.dataset.status === c.status ? "true" : "false");
    });
  }

  function save(li, c, changes) {
    var el = li.querySelector("[data-save]");
    el.textContent = "儲存中…";
    el.classList.remove("is-error");
    return request("PATCH", base + "/" + encodeURIComponent(c.id), changes)
      .then(function (data) {
        Object.assign(c, data.candidate);
        state.approved = data.approved;
        state.approved_sec = data.approved_sec;
        paint(li, c);
        summary();
        el.textContent = "已儲存";
      })
      .catch(function (err) {
        paint(li, c);
        el.textContent = err.message;
        el.classList.add("is-error");
      });
  }

  function makePlayer(li, c) {
    var host = li.querySelector("[data-player]");
    host.id = "vc-player-" + c.id;
    players[c.id] = new YT.Player(host.id, {
      videoId: c.video_id,
      playerVars: { start: Math.floor(c.start), rel: 0, modestbranding: 1, playsinline: 1 },
    });
  }

  var stopTimer = null;
  function preview(c) {
    var p = players[c.id];
    if (!p || !p.seekTo) return;
    Object.keys(players).forEach(function (k) {
      if (k !== c.id && players[k].pauseVideo) players[k].pauseVideo();
    });
    p.seekTo(c.start, true);
    p.playVideo();
    clearInterval(stopTimer);
    stopTimer = setInterval(function () {
      if (p.getCurrentTime() >= c.end) {
        p.pauseVideo();
        clearInterval(stopTimer);
      }
    }, 50);
  }

  function addRow(c) {
    var li = tpl.content.firstElementChild.cloneNode(true);
    li.id = "clip-" + c.id;
    paint(li, c);
    li.querySelectorAll("[data-set]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var p = players[c.id];
        if (!p || !p.getCurrentTime) return;
        var t = Math.round(p.getCurrentTime() * 100) / 100;
        var change = {};
        change[btn.dataset.set] = t;
        save(li, c, change);
      });
    });
    li.querySelector("[data-preview]").addEventListener("click", function () { preview(c); });
    li.querySelectorAll("[data-status]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var next = c.status === btn.dataset.status ? "proposed" : btn.dataset.status;
        save(li, c, { status: next });
      });
    });
    var input = li.querySelector('[name="source_label"]');
    var timer = null;
    input.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        if (input.value.trim()) save(li, c, { source_label: input.value.trim() });
      }, 600);
    });
    list.appendChild(li);
    if (apiReady) makePlayer(li, c);
    else pending.push([li, c]);
  }

  state.candidates.forEach(addRow);
  summary();

  document.querySelector("[data-add]").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var form = ev.currentTarget;
    var el = form.querySelector("[data-add-save]");
    var btn = form.querySelector("button");
    el.textContent = "讀取影片資訊…";
    el.classList.remove("is-error");
    btn.disabled = true;
    request("POST", base, { url: form.url.value })
      .then(function (data) {
        state.candidates.push(data.candidate);
        state.approved = data.approved;
        state.approved_sec = data.approved_sec;
        addRow(data.candidate);
        summary();
        form.reset();
        el.textContent = "";
      })
      .catch(function (err) {
        el.textContent = err.message;
        el.classList.add("is-error");
      })
      .finally(function () { btn.disabled = false; });
  });

  window.onYouTubeIframeAPIReady = function () {
    apiReady = true;
    pending.forEach(function (pair) { makePlayer(pair[0], pair[1]); });
    pending = [];
  };
  var tag = document.createElement("script");
  tag.src = "https://www.youtube.com/iframe_api";
  document.head.appendChild(tag);
})();
