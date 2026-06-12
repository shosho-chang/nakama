/* Centaur 每日回顧 (N523 → N528) — CSP-safe (script-src 'self'，無 inline / onclick).
   讀 #kb-data JSON island → render 三段候選清單（Fleeting / 候選 / 清掃）→
   「開卡」一律開卡片畫布（kb_canvas.js）。線性 drawer 已移除（修修 N528 回饋⑥）。
   skip / later / POST 寫入仍走原有 endpoint。 */
(function () {
  "use strict";

  var DATA;
  try {
    DATA = JSON.parse(document.getElementById("kb-data").textContent || "{}");
  } catch (e) {
    DATA = { candidates: [], fleeting: [], sweep: [] };
  }

  var EDGE_KEYS = ["support", "refute", "extend"];

  function $(s, root) {
    return (root || document).querySelector(s);
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function esc(s) {
    return s == null ? "" : String(s);
  }

  /* ---------------- toast ---------------- */
  var toastTimer;
  function toast(msg) {
    var t = $("#toast");
    if (!t) return;
    t.textContent = msg;
    t.classList.add("on");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      t.classList.remove("on");
    }, 2600);
  }

  /* ---------------- 開卡：交給畫布（kb_canvas.js 掛 window.__kbCanvasOpen） ---------------- */
  function openCanvasFor(ctx) {
    if (typeof window.__kbCanvasOpen === "function") {
      window.__kbCanvasOpen(ctx);
    } else {
      toast("畫布尚未載入，請重新整理頁面");
    }
  }

  /* ---------------- render fleeting ---------------- */
  function fleetingId(f) {
    return "fl-" + (f.path || "").replace(/[^a-zA-Z0-9]/g, "");
  }
  function renderFleeting() {
    var list = $("#fleet-list");
    (DATA.fleeting || []).forEach(function (f) {
      var panel = el("div", "panel");
      panel.id = "panel-" + fleetingId(f);
      var meta = el("div", "cand-meta", "fleeting · via " + esc(f.via) + " · " + esc(f.created));
      var body = el("p", "fleet-text");
      body.id = "body-" + fleetingId(f);
      body.textContent = f.text || "";
      var act = el("div", "cand-seg");
      act.id = "act-" + fleetingId(f);
      var open = el("button", "seg seg-go", "開卡");
      open.type = "button";
      open.addEventListener("click", function () {
        openCanvasFor(fleetingContext(f));
      });
      var trash = el("button", "seg", "丟掉");
      trash.type = "button";
      trash.addEventListener("click", function () {
        // pilot：fleeting 丟掉只在 UI 標記（無 server fleeting-discard endpoint，
        // 善後在開卡時連動）。標記後本回合不再出現。
        markDone(panel, act, "已送回收桶");
        body.classList.add("struck");
      });
      var pin = el("div", "panel-in");
      pin.appendChild(meta);
      pin.appendChild(body);
      act.appendChild(open);
      act.appendChild(trash);
      pin.appendChild(act);
      panel.appendChild(pin);
      list.appendChild(panel);
    });
  }

  /* ---------------- render candidates ---------------- */
  function renderCandidates() {
    var list = $("#cand-list");
    (DATA.candidates || []).forEach(function (c) {
      var panel = el("div", "panel");
      panel.id = "panel-" + c.candidate_id;

      var head = el("div", "cand-head");
      head.appendChild(el("span", "tt", c.suggested_title));
      if (c.why) head.appendChild(el("span", "why", "｜ " + c.why));
      if (c.strong_signal) {
        head.appendChild(el("span", "sho-tag sho-tag--accent", "★ 強訊號"));
      }

      var ref = c.primary_ref;
      var metaText = "AI 建議卡名（可改）";
      if (ref) {
        var litLeaf = (ref.literature_path || "").split("/").pop();
        metaText += " · " + (litLeaf || "literature") + " · ^" + (ref.anchor || "");
      }
      var meta = el("div", "cand-meta", metaText);

      var pin = el("div", "panel-in");
      pin.appendChild(head);
      pin.appendChild(meta);

      // 引文 + note（v2 視覺語言）
      if (ref && (ref.quote || ref.note)) {
        var ex = el("div", "ex");
        if (ref.quote) {
          var q = el("div", "ex-q");
          q.appendChild(el("span", "qm", "“"));
          q.appendChild(el("span", null, ref.quote));
          ex.appendChild(q);
        }
        if (ref.note) {
          var n = el("div", "ex-n");
          n.appendChild(el("span", "who", "你 NOTE"));
          n.appendChild(document.createTextNode(ref.note));
          ex.appendChild(n);
        }
        pin.appendChild(ex);
      }

      // 相關既有卡（Robin judged，分方向合併顯示）
      var rel = el("div", "cand-rel");
      rel.appendChild(el("span", "sl", "相關既有卡"));
      var allEdges = collectEdgeTargets(c.edge_groups);
      if (allEdges.length) {
        allEdges.forEach(function (t) {
          rel.appendChild(el("span", "wikilink", "[[" + t.title + "]]"));
        });
      } else {
        rel.appendChild(el("span", "sho-mono", "KB 尚無相關卡（冷啟動）"));
      }
      pin.appendChild(rel);

      // 三動作併成一個分段式控制（修修回饋：做成一個按鈕）。開卡為主段、略過/之後再說為次段。
      var act = el("div", "cand-seg");
      act.id = "act-" + c.candidate_id;
      var open = el("button", "seg seg-go", "開卡");
      open.type = "button";
      open.addEventListener("click", function () {
        openCanvasFor({ kind: "cand", candidate_id: c.candidate_id });
      });
      var skip = el("button", "seg", "略過");
      skip.type = "button";
      skip.addEventListener("click", function () {
        postAction("skip", c.candidate_id, panel, act, "已略過");
      });
      var later = el("button", "seg", "之後再說");
      later.type = "button";
      later.addEventListener("click", function () {
        postAction("later", c.candidate_id, panel, act, "之後再說 — 14 天後過期歸檔");
      });
      act.appendChild(open);
      act.appendChild(skip);
      act.appendChild(later);
      pin.appendChild(act);

      panel.appendChild(pin);
      list.appendChild(panel);
    });
  }

  function collectEdgeTargets(groups) {
    var seen = {};
    var out = [];
    EDGE_KEYS.forEach(function (k) {
      (groups && groups[k] ? groups[k] : []).forEach(function (e) {
        var title = e.target_title || e.target_card;
        if (!seen[title]) {
          seen[title] = 1;
          out.push({ title: title, card: e.target_card });
        }
      });
    });
    return out;
  }

  /* ---------------- render sweep ---------------- */
  function renderSweep() {
    var list = $("#sweep-list");
    if (!list) return;
    (DATA.sweep || []).forEach(function (s) {
      var panel = el("div", "panel");
      var head = el("div", "cand-head");
      head.appendChild(el("span", "tt", s.title || s.path));
      var kindTag = {
        stale_seedling: "🌱 久未升級",
        orphan_card: "孤兒 · 無連結",
        expired_defer: "之後再說已過期",
      }[s.kind] || s.kind;
      head.appendChild(el("span", "sho-tag sho-tag--warning", kindTag));
      var reason = el("p", "sweep-reason", s.reason || "");
      var pin = el("div", "panel-in");
      pin.appendChild(head);
      pin.appendChild(reason);
      panel.appendChild(pin);
      list.appendChild(panel);
    });
  }

  /* ---------------- action POST (skip / later) ---------------- */
  function markDone(panel, act, tagText) {
    panel.classList.add("done");
    act.innerHTML = "";
    act.appendChild(el("span", "sho-tag", tagText));
  }
  function postAction(action, candidateId, panel, act, tagText) {
    fetch("/kb/api/review/" + action, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ candidate_id: candidateId }),
    })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function () {
        markDone(panel, act, tagText);
      })
      .catch(function () {
        toast("動作沒存成功，請重試");
      });
  }

  /* ---------------- fleeting → 畫布 context ---------------- */
  function fleetingContext(f) {
    return {
      kind: "fleet",
      panelId: "panel-" + fleetingId(f),
      actId: "act-" + fleetingId(f),
      fleeting_path: f.path || "",
      // fleeting 開卡：把捕捉的原話直接帶進正文當起點（修修 feedback — 少一個動作）。
      body_prefill: f.text || "",
      source_refs: [{ literature_path: "", anchor: "", raw: "[[" + (f.path || "") + "]]" }],
    };
  }

  /* ---------------- boot ---------------- */
  renderFleeting();
  renderCandidates();
  renderSweep();
})();
