/* Centaur 每日回顧 (N523) — CSP-safe (script-src 'self'，無 inline / onclick).
   讀 #kb-data JSON island → render 三段 + 開卡 drawer → POST /kb/api/*. */
(function () {
  "use strict";

  var DATA;
  try {
    DATA = JSON.parse(document.getElementById("kb-data").textContent || "{}");
  } catch (e) {
    DATA = { candidates: [], fleeting: [], sweep: [] };
  }

  var EDGE_META = {
    support: { label: "支持", desc: "本卡支持它" },
    refute: { label: "反駁", desc: "本卡反駁它" },
    extend: { label: "延伸", desc: "本卡把它延伸到新脈絡" },
  };
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
    t.textContent = msg;
    t.classList.add("on");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      t.classList.remove("on");
    }, 2600);
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
      var act = el("div", "cand-act");
      act.id = "act-" + fleetingId(f);
      var open = el("button", "sho-btn sho-btn--primary", "➕ 開卡");
      open.type = "button";
      open.addEventListener("click", function () {
        openDrawer(fleetingContext(f));
      });
      var trash = el("button", "sho-btn sho-btn--ghost", "丟掉 → 回收桶");
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

      var act = el("div", "cand-act");
      act.id = "act-" + c.candidate_id;
      var open = el("button", "sho-btn sho-btn--primary", "➕ 開卡");
      open.type = "button";
      open.addEventListener("click", function () {
        openDrawer(candidateContext(c));
      });
      var skip = el("button", "sho-btn sho-btn--ghost", "略過");
      skip.type = "button";
      skip.addEventListener("click", function () {
        postAction("skip", c.candidate_id, panel, act, "已略過");
      });
      var later = el("button", "sho-btn sho-btn--ghost", "之後再說");
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
    refreshOpenCount();
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
  function refreshOpenCount() {
    // reserved hook for a future nav count; no-op for now.
  }

  /* ---------------- drawer ---------------- */
  var currentCtx = null;

  function candidateContext(c) {
    var refs = (c.source_refs || []).map(function (r) {
      return { literature_path: r.literature_path || "", anchor: r.anchor || "", raw: "" };
    });
    var litSlug = "";
    if (c.primary_ref && c.primary_ref.literature_path) {
      litSlug = c.primary_ref.literature_path.split("/").pop();
    }
    return {
      kind: "cand",
      panelId: "panel-" + c.candidate_id,
      actId: "act-" + c.candidate_id,
      title: c.suggested_title,
      srcRefsHtml: refsLabel(c.source_refs),
      source_refs: refs,
      edge_groups: c.edge_groups || {},
      candidate_id: c.candidate_id,
      literature_slug: litSlug,
      fleeting_path: "",
    };
  }
  function fleetingContext(f) {
    return {
      kind: "fleet",
      panelId: "panel-" + fleetingId(f),
      actId: "act-" + fleetingId(f),
      title: "",
      srcRefsHtml: "[[" + (f.path || "") + "]] via " + esc(f.via) + " · 開卡後原檔送回收桶",
      source_refs: [{ literature_path: "", anchor: "", raw: "[[" + (f.path || "") + "]]" }],
      edge_groups: {},
      candidate_id: "",
      literature_slug: "",
      fleeting_path: f.path || "",
    };
  }
  function refsLabel(refs) {
    if (!refs || !refs.length) return "（無預填來源）";
    return refs
      .map(function (r) {
        var leaf = (r.literature_path || "").split("/").pop();
        return "[[" + (leaf || "?") + "]] " + (r.anchor ? "^" + r.anchor : "");
      })
      .join(" · ");
  }

  function buildEdgeGroup(key, suggestions) {
    var meta = EDGE_META[key];
    var group = el("div", "edgegroup");
    group.setAttribute("data-g", key);

    var gh = el("div", "gh");
    gh.appendChild(el("span", "gname " + key, meta.label + "::"));
    gh.appendChild(el("span", "gdesc", meta.desc));
    group.appendChild(gh);

    var suggRow = el("div", "sugg-row");
    suggRow.appendChild(el("span", "sl", "✦ Robin 建議"));
    if (suggestions && suggestions.length) {
      suggestions.forEach(function (s) {
        var title = s.target_title || s.target_card;
        var chip = el("button", "suggchip");
        chip.type = "button";
        chip.setAttribute("data-card", title);
        chip.appendChild(document.createTextNode("＋ " + title));
        if (s.direction === "reverse") {
          chip.appendChild(el("span", "dir", "（對方→本卡）"));
        }
        chip.addEventListener("click", function () {
          addEdgeRow(key, title, chip);
        });
        suggRow.appendChild(chip);
      });
    } else {
      suggRow.appendChild(el("span", "sho-mono", "Robin 此方向無建議"));
    }
    group.appendChild(suggRow);

    // 全量搜尋兜底（free-text add by name）
    var search = el("div", "searchall");
    var input = el("input");
    input.type = "text";
    input.placeholder = "或輸入任一張卡名加進此關係（全量兜底，Enter 加入）";
    input.setAttribute("aria-label", meta.label + " 關係：搜尋全部卡片");
    input.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") {
        ev.preventDefault();
        var v = input.value.trim();
        if (v) {
          addEdgeRow(key, v, null);
          input.value = "";
        }
      }
    });
    search.appendChild(input);
    group.appendChild(search);

    var rows = el("div", "rows");
    group.appendChild(rows);
    return group;
  }

  function addEdgeRow(key, card, chip) {
    var rows = $('.edgegroup[data-g="' + key + '"] .rows');
    if (!rows) return;
    var existing = rows.querySelectorAll(".ecard");
    for (var i = 0; i < existing.length; i++) {
      if (existing[i].textContent === "[[" + card + "]]") {
        toast("這條關係已經加過了");
        return;
      }
    }
    var row = el("div", "edgerow");
    row.appendChild(el("span", "ecard", "[[" + card + "]]"));
    var reason = el("input", "reason");
    reason.type = "text";
    reason.placeholder = "— 因為…（理由是你的判斷）";
    reason.addEventListener("input", updatePreview);
    var rm = el("button", "rm", "✕");
    rm.type = "button";
    rm.setAttribute("aria-label", "移除此關係");
    rm.addEventListener("click", function () {
      row.remove();
      if (chip) chip.removeAttribute("disabled");
      updatePreview();
    });
    row.appendChild(reason);
    row.appendChild(rm);
    rows.appendChild(row);
    if (chip) chip.setAttribute("disabled", "");
    updatePreview();
    reason.focus();
  }

  function collectEdges() {
    var out = [];
    EDGE_KEYS.forEach(function (key) {
      var rowsEls = document.querySelectorAll('.edgegroup[data-g="' + key + '"] .edgerow');
      rowsEls.forEach(function (r) {
        var card = r.querySelector(".ecard").textContent.replace(/^\[\[|\]\]$/g, "");
        var reason = r.querySelector("input.reason").value.trim();
        out.push({ edge_type: key, target: card, reason: reason });
      });
    });
    return out;
  }

  function edgesToLines(edges) {
    return edges
      .map(function (e) {
        var label = EDGE_META[e.edge_type].label;
        var line = label + ":: [[" + e.target + "]]";
        if (e.reason) line += " — " + e.reason;
        return line;
      })
      .join("\n");
  }

  function updatePreview() {
    if (!currentCtx) return;
    var title = $("#f-title").value.trim() || "（未命名）";
    var body = $("#f-body").value.trim() || "（正文待寫）";
    var refLines = (currentCtx.source_refs || [])
      .map(function (r) {
        if (r.literature_path) {
          var leaf = r.literature_path.split("/").pop();
          var anchor = r.anchor ? (r.anchor.charAt(0) === "^" ? r.anchor : "^" + r.anchor) : "";
          return '  - "[[Literature/' + leaf + "]]" + (anchor ? " " + anchor : "") + '"';
        }
        return '  - "' + r.raw + '"';
      })
      .join("\n");
    var today = (DATA.review_date || "").trim();
    var edges = collectEdges();
    var preview =
      "--- KB/Permanent/" +
      title +
      ".md ---\n" +
      "type: permanent\n" +
      "status: seedling\n" +
      "author: human\n" +
      "created: " +
      today +
      "\n" +
      "modified: " +
      today +
      "\n" +
      "source_refs:\n" +
      (refLines || "  []") +
      "\n" +
      "aliases: []\n" +
      "---\n\n" +
      body +
      (edges.length ? "\n\n" + edgesToLines(edges) : "");
    $("#f-preview").textContent = preview;
  }

  function openDrawer(ctx) {
    currentCtx = ctx;
    $("#f-title").value = ctx.title || "";
    $("#f-body").value = "";
    $("#f-body").classList.remove("invalid");
    $("#body-err").hidden = true;
    $("#f-srcrefs").textContent = ctx.srcRefsHtml;

    var edgeWrap = $("#f-edges");
    edgeWrap.innerHTML = "";
    EDGE_KEYS.forEach(function (key) {
      var suggs = (ctx.edge_groups && ctx.edge_groups[key]) || [];
      edgeWrap.appendChild(buildEdgeGroup(key, suggs));
    });

    updatePreview();
    $("#scrim").classList.add("on");
    var drawer = $("#drawer");
    drawer.classList.add("on");
    drawer.removeAttribute("hidden");
    setTimeout(function () {
      $("#f-body").focus();
    }, 300);
  }

  function closeDrawer() {
    $("#scrim").classList.remove("on");
    var drawer = $("#drawer");
    drawer.classList.remove("on");
    drawer.setAttribute("hidden", "");
    currentCtx = null;
  }

  function saveCard() {
    if (!currentCtx) return;
    var title = $("#f-title").value.trim();
    var body = $("#f-body").value.trim();
    if (!title) {
      toast("檔名不能是空的——一句宣告句");
      $("#f-title").focus();
      return;
    }
    if (!body) {
      // 空正文阻擋（紅線內側）——前端先擋，後端 422 兜底。
      $("#f-body").classList.add("invalid");
      $("#body-err").hidden = false;
      $("#f-body").focus();
      return;
    }
    var ctx = currentCtx;
    var payload = {
      title: title,
      body: body,
      edges: collectEdges(),
      source_refs: ctx.source_refs || [],
      candidate_id: ctx.candidate_id || "",
      literature_slug: ctx.literature_slug || "",
      fleeting_path: ctx.fleeting_path || "",
    };
    var saveBtn = $("#btn-save");
    saveBtn.setAttribute("disabled", "");
    fetch("/kb/api/permanent", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (r) {
        return r.json().then(function (j) {
          return { ok: r.ok, status: r.status, json: j };
        });
      })
      .then(function (res) {
        saveBtn.removeAttribute("disabled");
        if (!res.ok) {
          var detail = res.json && res.json.detail ? res.json.detail : "存檔失敗（" + res.status + "）";
          toast(typeof detail === "string" ? detail : "存檔失敗");
          return;
        }
        closeDrawer();
        var panel = document.getElementById(ctx.panelId);
        var act = document.getElementById(ctx.actId);
        if (panel && act) {
          panel.classList.add("done");
          act.innerHTML = "";
          act.appendChild(el("span", "sho-tag sho-tag--success", "已開卡"));
          act.appendChild(
            el("span", "sho-mono", res.json.path + " · author: human · Phase 5 善後完成")
          );
        }
        refreshOpenCount();
        toast("✓ 已存入 vault：" + title);
      })
      .catch(function () {
        saveBtn.removeAttribute("disabled");
        toast("存檔失敗，請重試");
      });
  }

  /* ---------------- wire static controls ---------------- */
  $("#btn-close").addEventListener("click", closeDrawer);
  $("#btn-cancel").addEventListener("click", closeDrawer);
  $("#btn-save").addEventListener("click", saveCard);
  $("#scrim").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeDrawer();
  });
  $("#f-title").addEventListener("input", updatePreview);
  $("#f-body").addEventListener("input", function () {
    $("#f-body").classList.remove("invalid");
    $("#body-err").hidden = true;
    updatePreview();
  });

  /* ---------------- boot ---------------- */
  renderFleeting();
  renderCandidates();
  renderSweep();
})();
