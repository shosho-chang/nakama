// Bridge · 畫面規劃表 — autosave, 核准／重排, 空白段加畫面, 改時間.
(function () {
  "use strict";

  var episode = window.VP_EPISODE;
  var base = "/bridge/video-plan/" + encodeURIComponent(episode);
  var cues = [];
  try {
    cues = JSON.parse(document.getElementById("vp-cues").textContent);
  } catch (e) {
    cues = [];
  }
  var cueIndex = {};
  cues.forEach(function (cue, i) { cueIndex[String(cue.id)] = i; });

  function request(method, url, body) {
    return fetch(url, {
      method: method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
      credentials: "same-origin",
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) {
          var message = data.error || data.detail || ("HTTP " + res.status);
          throw new Error(typeof message === "string" ? message : JSON.stringify(message));
        }
        return data;
      });
    });
  }

  function setSave(row, text, isError) {
    var el = row.querySelector("[data-save]");
    if (!el) return;
    el.textContent = text;
    el.classList.toggle("is-error", !!isError);
  }

  function updateStats(stats) {
    if (!stats) return;
    Object.keys(stats).forEach(function (key) {
      var el = document.querySelector('[data-stat="' + key + '"]');
      if (el) el.textContent = stats[key];
    });
    var re = document.querySelector('[data-stat="rearrange"]');
    if (re) re.classList.toggle("vp-warn", stats.rearrange > 0);
  }

  function applyRow(row, data) {
    if (!data) return;
    var status = data.status || "";
    row.dataset.status = status;
    row.classList.toggle("is-approved", status === "approved");
    row.classList.toggle("is-rearrange", status === "rearrange");
    row.querySelectorAll("[data-status-btn]").forEach(function (btn) {
      btn.setAttribute("aria-checked", btn.dataset.statusBtn === status ? "true" : "false");
    });
    var note = row.querySelector(".vp-note");
    if (note) note.hidden = status !== "rearrange";
    var layout = row.querySelector("[data-layout]");
    if (layout) {
      layout.hidden = !!data.layout_fixed;
      layout.querySelectorAll("input").forEach(function (input) {
        input.checked = input.value === data.layout;
      });
    }
    row.querySelectorAll(".vp-req").forEach(function (el) { el.remove(); });
    (data.missing || []).forEach(function (field) {
      var control = row.querySelector('[name="' + field + '"]');
      var label = control && control.closest(".vp-field") && control.closest(".vp-field").querySelector(".vp-label");
      if (label) {
        var em = document.createElement("em");
        em.className = "vp-req";
        em.textContent = "必填";
        label.appendChild(em);
      }
    });
    if (data.time_label) {
      var time = row.querySelector("[data-time-edit]");
      time.textContent = data.time_label;
      row.querySelector("[data-span]").textContent = data.span;
      row.querySelector("[data-quote]").textContent = data.covered_text;
    }
  }

  function save(row, changes) {
    setSave(row, "儲存中");
    return request("PATCH", base + "/rows/" + encodeURIComponent(row.dataset.rowId), changes)
      .then(function (data) {
        applyRow(row, data.row);
        updateStats(data.stats);
        setSave(row, "已儲存");
        return data;
      })
      .catch(function (err) {
        setSave(row, "儲存失敗：" + err.message, true);
        throw err;
      });
  }

  // ── autosave fields ───────────────────────────────────────────────────
  document.querySelectorAll(".vp-row").forEach(function (row) {
    row.querySelectorAll("textarea, input[type=text], select").forEach(function (control) {
      control.dataset.initial = control.value;
      var event = control.tagName === "SELECT" ? "change" : "blur";
      control.addEventListener(event, function () {
        if (control.value === control.dataset.initial) return;
        var changes = {};
        changes[control.name] = control.value;
        if (control.name === "note") {
          if (!control.value.trim()) return;
          if (row.dataset.pendingRearrange === "1" || row.dataset.status === "rearrange") {
            changes.status = "rearrange";
          }
        }
        save(row, changes).then(function () {
          control.dataset.initial = control.value;
          row.dataset.pendingRearrange = "";
        }).catch(function () {});
      });
    });

    row.querySelectorAll("[data-layout] input").forEach(function (input) {
      input.addEventListener("change", function () {
        if (input.checked) save(row, { layout: input.value }).catch(function () {});
      });
    });

    // 核准／重排：單選；再點一次已選的＝回到待審。
    row.querySelectorAll("[data-status-btn]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var target = btn.dataset.statusBtn;
        var current = row.dataset.status;
        var note = row.querySelector(".vp-note");
        var noteArea = note.querySelector("textarea");
        if (target === current) {
          save(row, { status: null }).catch(function () {});
          return;
        }
        if (target === "rearrange" && !noteArea.value.trim()) {
          row.dataset.pendingRearrange = "1";
          note.hidden = false;
          row.querySelectorAll("[data-status-btn]").forEach(function (b) {
            b.setAttribute("aria-checked", b === btn ? "true" : "false");
          });
          noteArea.focus();
          return;
        }
        var changes = { status: target };
        if (target === "rearrange") changes.note = noteArea.value;
        save(row, changes).catch(function () {});
      });
    });

    var restore = row.querySelector("[data-restore]");
    if (restore) {
      restore.addEventListener("click", function () {
        request("POST", base + "/rows/" + encodeURIComponent(row.dataset.rowId) + "/restore")
          .then(function () { reloadAt("row-" + row.dataset.rowId); })
          .catch(function (err) { setSave(row, "復原失敗：" + err.message, true); });
      });
    }

    var del = row.querySelector("[data-delete]");
    if (del) {
      del.addEventListener("click", function () {
        if (!del.classList.contains("is-armed")) {
          del.classList.add("is-armed");
          setTimeout(function () { del.classList.remove("is-armed"); }, 4000);
          return;
        }
        request("DELETE", base + "/rows/" + encodeURIComponent(row.dataset.rowId))
          .then(function () { reloadAt(null); })
          .catch(function (err) { setSave(row, "刪除失敗：" + err.message, true); });
      });
    }
  });

  // After add / delete / restore the page reloads: land on the touched row, or
  // keep the reader where they were.
  function reloadAt(anchor) {
    try {
      sessionStorage.setItem("vp-return", anchor ? "#" + anchor : String(window.scrollY));
    } catch (e) { /* storage unavailable */ }
    window.location.reload();
  }
  if ("scrollRestoration" in history) history.scrollRestoration = "manual";
  (function restorePosition() {
    var target = null;
    try {
      target = sessionStorage.getItem("vp-return");
      sessionStorage.removeItem("vp-return");
    } catch (e) { /* storage unavailable */ }
    if (target && target.charAt(0) === "#") {
      var el = document.getElementById(target.slice(1));
      if (el) el.scrollIntoView({ block: "start" });
    } else if (target !== null) {
      window.scrollTo(0, Number(target));
    } else if (window.location.hash) {
      var hashed = document.getElementById(window.location.hash.slice(1));
      if (hashed) hashed.scrollIntoView({ block: "start" });
    }
  })();

  // ── 空白段：選連續字幕 → 加畫面 ─────────────────────────────────────────
  document.querySelectorAll(".vp-gap").forEach(function (gap) {
    var boxes = Array.prototype.slice.call(gap.querySelectorAll("input[type=checkbox]"));
    var add = gap.querySelector(".vp-add");
    boxes.forEach(function (box, i) {
      box.addEventListener("change", function () {
        var picked = boxes.map(function (b, j) { return b.checked ? j : -1; }).filter(function (j) { return j >= 0; });
        if (picked.length > 1) {
          var lo = Math.min.apply(null, picked), hi = Math.max.apply(null, picked);
          boxes.forEach(function (b, j) { b.checked = j >= lo && j <= hi; });
        }
        add.disabled = !boxes.some(function (b) { return b.checked; });
      });
    });
    add.addEventListener("click", function () {
      var chosen = boxes.filter(function (b) { return b.checked; });
      if (!chosen.length) return;
      add.disabled = true;
      request("POST", base + "/rows", {
        start_cue: cueValue(chosen[0].value),
        end_cue: cueValue(chosen[chosen.length - 1].value),
      }).then(function (data) {
        reloadAt(data.row ? "row-" + data.row.id : null);
      }).catch(function (err) {
        add.disabled = false;
        add.textContent = "加畫面失敗：" + err.message;
      });
    });
  });

  function cueValue(raw) {
    var match = cues[cueIndex[String(raw)]];
    return match ? match.id : raw;
  }

  document.querySelectorAll("[data-open-gap]").forEach(function (link) {
    link.addEventListener("click", function () {
      var gap = document.getElementById(link.dataset.openGap);
      if (gap) {
        gap.open = true;
        gap.classList.add("is-target");
      }
    });
  });

  // ── 篩選分頁 ───────────────────────────────────────────────────────────
  document.querySelectorAll(".vp-tab").forEach(function (tab) {
    tab.addEventListener("click", function () {
      document.body.dataset.filter = tab.dataset.filter;
      document.querySelectorAll(".vp-tab").forEach(function (t) {
        t.setAttribute("aria-pressed", t === tab ? "true" : "false");
      });
    });
  });

  // ── 改時間：從字幕選起訖（插入類選一句）──────────────────────────────
  var dialog = document.getElementById("vp-time-dialog");
  var pick = dialog.querySelector("[data-pick]");
  var ok = dialog.querySelector("[data-pick-ok]");
  var summary = dialog.querySelector("[data-pick-summary]");
  var state = null;

  function renderPick() {
    var lo = state.lo, hi = state.hi;
    pick.querySelectorAll("button").forEach(function (b) {
      var i = Number(b.dataset.i);
      b.classList.toggle("is-in", lo !== null && i >= lo && i <= (hi === null ? lo : hi));
    });
    if (lo === null) {
      summary.textContent = "";
      ok.disabled = true;
    } else if (state.single) {
      summary.textContent = cues[lo].t + " 前";
      ok.disabled = false;
    } else {
      var last = hi === null ? lo : hi;
      summary.textContent = cues[lo].t + " 起 · " + (last - lo + 1) + " 句";
      ok.disabled = false;
    }
  }

  function openPicker(row, btn) {
    var single = row.dataset.kind === "insert";
    var lo = single ? cueIndex[btn.dataset.before] : cueIndex[btn.dataset.start];
    var hi = single ? null : cueIndex[btn.dataset.end];
    state = { row: row, single: single, lo: lo === undefined ? null : lo, hi: hi === undefined ? null : hi, anchor: null };
    pick.textContent = "";
    var from = Math.max(0, (state.lo || 0) - 15);
    var to = Math.min(cues.length - 1, (state.hi === null ? state.lo || 0 : state.hi) + 15);
    for (var i = from; i <= to; i++) {
      var li = document.createElement("li");
      var b = document.createElement("button");
      b.type = "button";
      b.dataset.i = String(i);
      var t = document.createElement("span");
      t.className = "sho-mono";
      t.textContent = cues[i].t;
      var text = document.createElement("span");
      text.textContent = cues[i].text;
      b.appendChild(t);
      b.appendChild(text);
      li.appendChild(b);
      pick.appendChild(li);
    }
    renderPick();
    dialog.showModal();
    var first = pick.querySelector("button.is-in") || pick.querySelector("button");
    if (first) first.scrollIntoView({ block: "center" });
  }

  pick.addEventListener("click", function (event) {
    var b = event.target.closest("button");
    if (!b || !state) return;
    var i = Number(b.dataset.i);
    if (state.single) {
      state.lo = i;
    } else if (state.anchor === null) {
      state.anchor = i;
      state.lo = i;
      state.hi = i;
    } else {
      state.lo = Math.min(state.anchor, i);
      state.hi = Math.max(state.anchor, i);
      state.anchor = null;
    }
    renderPick();
  });

  ok.addEventListener("click", function () {
    if (!state || state.lo === null) return;
    var changes = state.single
      ? { before_cue: cues[state.lo].id }
      : { start_cue: cues[state.lo].id, end_cue: cues[state.hi === null ? state.lo : state.hi].id };
    var row = state.row;
    dialog.close();
    // 時間一動，前後的空白段就跟著變，所以存好後重新載入、停在這一列。
    save(row, changes).then(function () {
      reloadAt(row.id);
    }).catch(function () {});
  });

  dialog.querySelector("[data-dialog-close]").addEventListener("click", function () { dialog.close(); });
  dialog.addEventListener("click", function (event) {
    if (event.target === dialog) dialog.close();
  });

  document.querySelectorAll("[data-time-edit]").forEach(function (btn) {
    btn.addEventListener("click", function () { openPicker(btn.closest(".vp-row"), btn); });
  });
})();
