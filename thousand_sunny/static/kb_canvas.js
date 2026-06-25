/* Centaur 卡片畫布 (N528) — CSP-safe (script-src 'self'，無 inline / onclick).
   讀 #kb-data JSON island（與 kb_review.js 共用）→ 畫布工作桌 + 三帶卡片場 →
   拖拉建 typed edge → POST /kb/api/permanent（沿用唯一寫入口）。

   N528 回饋⑥：畫布是「開卡」的唯一介面（線性 drawer 已移除）。kb_review.js 的
   「開卡」按鈕呼叫本檔掛出的 window.__kbCanvasOpen(ctx) 開啟畫布；esc 關閉回候選清單。
   照 prototype v4 落地——互動、版面、動畫直接搬，token 換成 tokens.css 的 --sho-*。 */
(function () {
  "use strict";

  var DATA;
  try {
    DATA = JSON.parse(document.getElementById("kb-data").textContent || "{}");
  } catch (e) {
    DATA = { candidates: [] };
  }

  // typed edge label（後端契約：support/refute/extend；src 是 source_ref 非 edge）。
  var TLBL = { support: "支持", refute: "反駁", extend: "延伸", src: "來源" };

  function $(s, root) {
    return (root || document).querySelector(s);
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  var reduced = false;
  try {
    reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch (e) {
    reduced = false;
  }
  function rand(a, b) {
    return a + Math.random() * (b - a);
  }

  /* ---------------- toast（沿用線性版 #toast 元素） ---------------- */
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

  /* ================= state ================= */
  // 候選佇列：複製 bundle，每張帶 done 旗標。畫布存卡後在此標 done 並推進。
  var CANDS = (DATA.candidates || []).map(function (c) {
    return c;
  });
  var doneIds = {}; // candidate_id -> true（已存/已收）
  var centerIdx = 0;
  var edges = []; // [{target, type, reason}]；type ∈ support/refute/extend
  var srcRefs = []; // 拖入「來源」格的卡 → 額外 source_ref（raw wikilink）
  var doneCount = 0;
  var binCount = 0;
  var binned = {}; // 場上單卡 / 疊卡的 session-scoped dismissal（key=title 或 "stack:"+name）
  var editIdx = -1;
  var zCounter = 12;
  var overlayEl = null;
  var memberCache = {}; // moc_path -> members[]（lazy load 結果快取）

  function curCand() {
    while (
      centerIdx < CANDS.length &&
      (CANDS[centerIdx].__done || (CANDS[centerIdx].candidate_id && doneIds[CANDS[centerIdx].candidate_id]))
    )
      centerIdx++;
    return CANDS[centerIdx] || null;
  }

  /* ================= field render ================= */
  function render() {
    var cand = curCand();
    var field = $("#kbc-field");
    var emptyAll = $("#kbc-empty-all");
    if (!cand) {
      field.innerHTML = "";
      emptyAll.hidden = false;
      $("#kbc-title").value = "";
      $("#kbc-body").value = "";
      $("#kbc-edges-box").hidden = true;
      $("#kbc-i").textContent = CANDS.length;
      $("#kbc-n").textContent = CANDS.length;
      $("#kbc-done").textContent = doneCount;
      $("#kbc-mocbox-n").textContent = "";
      return;
    }
    emptyAll.hidden = true;
    if (!$("#kbc-title").dataset.dirty) $("#kbc-title").value = cand.suggested_title || "";
    $("#kbc-i").textContent = centerIdx + 1;
    $("#kbc-n").textContent = CANDS.length;
    $("#kbc-done").textContent = doneCount;

    var FW = field.offsetWidth;
    var FH = field.offsetHeight;
    var b1 = FW * 0.34;
    var b2 = FW * 0.66;

    field.innerHTML = "";
    // 帶標籤 + 分隔線
    addBandLabel(field, "高關聯 · Robin 判定", b1 * 0.5 - 58);
    addBandLabel(field, "中關聯 · 字面相關", b1 + (b2 - b1) * 0.5 - 58);
    addBandLabel(field, "MOC 疊卡 · Robin 篩選", b2 + (FW - b2) * 0.5 - 70);
    addBandSep(field, b1);
    addBandSep(field, b2);

    // 高圈：edges（含建議方向 chip）。中圈：related_pool（字面）。
    var hiCards = (cand.edges || []).filter(function (e) {
      var t = e.target_title || e.target_card;
      return !linked(t) && !binned[t];
    });
    var midCards = (cand.related_pool || []).filter(function (r) {
      var t = r.title || r.card_path;
      return !linked(t) && !binned[t];
    });
    placeCards(field, hiCards, 0, b1, FH, "hi");
    placeCards(field, midCards, b1, b2, FH, "mid");

    // MOC 疊卡：只放 Robin 判定相關的（related_mocs）。其餘收進 MOC 盒（C8）。
    var mocs = (cand.related_mocs || []).filter(function (m) {
      return !binned["stack:" + (m.name || m.moc_path)];
    });
    mocs.forEach(function (m, i) {
      var name = m.name || m.moc_path.split("/").pop();
      var x = b2 + 24 + rand(-4, 4);
      var y = Math.min(FH - 110, 58 + i * 112 + rand(-4, 4));
      var stack = el("div", "kbc-stack rel");
      stack.dataset.stack = name;
      stack.dataset.mocPath = m.moc_path;
      stack.style.cssText = "left:" + x + "px;top:" + y + "px;z-index:5";
      var layers = el("div", "layers");
      layers.appendChild(el("div", "ly"));
      layers.appendChild(el("div", "ly"));
      var top = el("div", "ly");
      top.appendChild(el("span", "t", name));
      top.appendChild(el("span", "n", (m.card_count || 0) + " 張"));
      layers.appendChild(top);
      stack.appendChild(layers);
      field.appendChild(stack);
    });

    // MOC 盒計數：related_mocs 是已上桌的子集，盒內是全部 related_mocs（含已收回的兜底入口）。
    var totalMocs = (cand.related_mocs || []).length;
    $("#kbc-mocbox-n").textContent = totalMocs ? totalMocs + " 個 MOC" : "無相關 MOC";

    renderEdges();
    bindCards();
    bindStacks();
  }

  function addBandLabel(field, text, left) {
    var lbl = el("span", "kbc-bandlbl", text);
    lbl.style.left = left + "px";
    field.appendChild(lbl);
  }
  function addBandSep(field, left) {
    var s = el("div", "kbc-bandsep");
    s.style.left = left + "px";
    field.appendChild(s);
  }
  function placeCards(field, cards, x0, x1, FH, tier) {
    cards.forEach(function (d, i) {
      var title = tier === "hi" ? d.target_title || d.target_card : d.title || d.card_path;
      var x = Math.max(10, Math.min(x1 - 214, x0 + 18 + rand(0, Math.max(20, x1 - x0 - 240))));
      var y = 56 + i * ((FH - 140) / Math.max(cards.length, 1)) + rand(-10, 10);
      var rot = reduced ? 0 : rand(-2.2, 2.2);
      var card = el("div", "kbc-card");
      card.dataset.title = title;
      card.dataset.path = (tier === "hi" ? d.target_card : d.card_path) || "";
      card.style.cssText =
        "left:" + x + "px;top:" + Math.min(FH - 120, y) + "px;transform:rotate(" + rot + "deg);z-index:" + zCounter++;
      card.appendChild(el("div", "t", title));
      // 展開圖示：看這張永久卡的內容（修修回饋 #3）。點卡身仍是換角度/拖拉，互不干擾。
      var peek = el("button", "kbc-peek");
      peek.type = "button";
      peek.title = "看內容";
      peek.setAttribute("aria-label", "看「" + title + "」的內容");
      peek.innerHTML =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z"/><circle cx="12" cy="12" r="2.6"/></svg>';
      peek.addEventListener("pointerdown", function (ev) {
        ev.stopPropagation();
      });
      peek.addEventListener("click", function (ev) {
        ev.stopPropagation();
        openPeek(card.dataset.path, title);
      });
      card.appendChild(peek);
      var m = el("div", "m");
      if (tier === "hi") {
        // 高關聯（Robin 判定）：無 ✦Robin 文字，改用加粗邊框（.robin）標示區別。
        card.classList.add("robin");
        var sugg = d.edge_type;
        if (sugg && TLBL[sugg]) {
          m.appendChild(el("span", "kbc-minitag " + sugg, "建議" + TLBL[sugg]));
        }
        if (d.direction === "reverse") m.appendChild(el("span", "kbc-minitag", "對方→本卡"));
        if (d.status) m.appendChild(el("span", "kbc-minitag st-" + d.status, d.status));
      } else {
        m.appendChild(el("span", "kbc-minitag", "字面相關"));
        if (d.status) m.appendChild(el("span", "kbc-minitag st-" + d.status, d.status));
      }
      card.appendChild(m);
      field.appendChild(card);
    });
  }

  /* 看候選永久卡內容（修修回饋 #3）：fetch 正文 → 彈出預覽面板。esc / 點外關閉。 */
  function closePeek() {
    var p = $("#kbc-peekpop");
    if (p) p.remove();
  }
  function openPeek(path, title) {
    if (!path) return;
    closePeek();
    var pop = el("div", "kbc-peekpop");
    pop.id = "kbc-peekpop";
    var h = el("div", "pk-h");
    h.appendChild(el("b", null, title));
    var x = el("button", "pk-x", "✕");
    x.type = "button";
    x.addEventListener("click", closePeek);
    h.appendChild(x);
    pop.appendChild(h);
    var body = el("div", "pk-body", "載入中…");
    pop.appendChild(body);
    $("#kbc-root").appendChild(pop);
    fetch("/kb/api/permanent/peek?path=" + encodeURIComponent(path))
      .then(function (r) {
        return r.ok ? r.json() : null;
      })
      .then(function (j) {
        body.textContent = j && j.body ? j.body : "（這張卡還沒有內容）";
      })
      .catch(function () {
        body.textContent = "讀取失敗";
      });
  }

  function linked(title) {
    return edges.some(function (e) {
      return e.target === title;
    }) || srcRefs.indexOf(title) >= 0;
  }

  function renderKeepText() {
    var t = $("#kbc-title").value;
    var b = $("#kbc-body").value;
    var dirty = $("#kbc-title").dataset.dirty;
    render();
    if (curCand()) {
      if (dirty) $("#kbc-title").value = t;
      $("#kbc-body").value = b;
    }
  }

  /* ================= edges 清單 ================= */
  function renderEdges() {
    var box = $("#kbc-edges-box");
    var hasAny = edges.length || srcRefs.length;
    box.hidden = !hasAny;
    $("#kbc-e-n").textContent = "";

    var list = $("#kbc-edges");
    list.innerHTML = "";

    edges.forEach(function (e, i) {
      var item = el("div", "kbc-edgeitem");
      var badge = el("button", "kbc-ebadge " + e.type);
      badge.type = "button";
      badge.dataset.eb = i;
      badge.textContent = TLBL[e.type];
      badge.addEventListener("click", function (ev) {
        openPopover(i, badge);
      });
      item.appendChild(badge);
      item.appendChild(el("span", "tg", e.target));
      var reason = el("input", "reason-in");
      reason.type = "text";
      reason.dataset.ri = i;
      reason.value = e.reason || "";
      reason.placeholder = "";
      reason.setAttribute("aria-label", "「" + e.target + "」關係理由");
      reason.addEventListener("input", function () {
        edges[i].reason = reason.value;
        updateFootHint();
      });
      item.appendChild(reason);
      var rm = el("button", "rm", "✕");
      rm.type = "button";
      rm.dataset.rm = i;
      rm.title = "解除連結";
      rm.setAttribute("aria-label", "解除「" + e.target + "」連結");
      rm.addEventListener("click", function () {
        var removed = edges.splice(i, 1)[0];
        toast("已解除「" + removed.target + "」— 卡片回到場上");
        renderKeepText();
      });
      item.appendChild(rm);
      list.appendChild(item);
    });

    // 來源格收進來的卡（不是 typed edge，存檔時併入 source_refs）。
    srcRefs.forEach(function (target, i) {
      var item = el("div", "kbc-edgeitem");
      var badge = el("span", "kbc-ebadge src", "來源");
      item.appendChild(badge);
      item.appendChild(el("span", "tg", target));
      item.appendChild(el("span", "reason-in", "來源"));
      var rm = el("button", "rm", "✕");
      rm.type = "button";
      rm.title = "解除來源";
      rm.setAttribute("aria-label", "解除來源「" + target + "」");
      rm.addEventListener("click", function () {
        srcRefs.splice(i, 1);
        toast("已解除來源「" + target + "」");
        renderKeepText();
      });
      item.appendChild(rm);
      list.appendChild(item);
    });

    updateFootHint();
  }

  function updateFootHint() {
    // 好 UI 不需解釋：無連結時底欄留白；有連結才顯示「已寫 n / 缺幾條理由」狀態（③）。
    var missing = edges.filter(function (e) {
      return !e.reason.trim();
    }).length;
    if (edges.length) {
      $("#kbc-fn").textContent = missing
        ? "已寫 " + edges.length + " 條 · 缺 " + missing + " 條理由"
        : "已寫 " + edges.length + " 條 · 理由都填好了 ✓";
    } else if (srcRefs.length) {
      $("#kbc-fn").textContent = "已加 " + srcRefs.length + " 條來源";
    } else {
      $("#kbc-fn").textContent = "";
    }
  }

  /* ---------------- popover（改類型） ---------------- */
  function openPopover(idx, badge) {
    editIdx = idx;
    var e = edges[idx];
    $("#kbc-pop-name").textContent = "修改關係：" + e.target;
    document.querySelectorAll("#kbc-popover .ptype").forEach(function (p) {
      p.classList.toggle("sel", p.dataset.t === e.type);
    });
    var po = $("#kbc-popover");
    var r = badge.getBoundingClientRect();
    po.classList.add("on");
    po.style.left = Math.min(window.innerWidth - 292, r.left) + "px";
    po.style.top = r.bottom + 6 + "px";
  }
  document.querySelectorAll("#kbc-popover .ptype").forEach(function (p) {
    p.addEventListener("click", function () {
      if (editIdx >= 0) {
        var t = p.dataset.t;
        if (t === "src") {
          // 改成「來源」= 從 typed edge 降級為 source_ref。
          var moved = edges.splice(editIdx, 1)[0];
          if (srcRefs.indexOf(moved.target) < 0) srcRefs.push(moved.target);
        } else {
          edges[editIdx].type = t;
        }
        renderEdges();
      }
      $("#kbc-popover").classList.remove("on");
      editIdx = -1;
    });
  });

  /* ================= drop targets ================= */
  function overTarget(ev) {
    var ib = $("#kbc-inbox").getBoundingClientRect();
    if (ev.clientX > ib.left - 8 && ev.clientX < ib.right + 8 && ev.clientY > ib.top - 8 && ev.clientY < ib.bottom + 8)
      return "bin";
    var desk = $("#kbc-desk");
    var r = desk.getBoundingClientRect();
    if (ev.clientX < r.right + 30) {
      var zones = desk.querySelectorAll(".kbc-dz");
      for (var i = 0; i < zones.length; i++) {
        var zr = zones[i].getBoundingClientRect();
        if (zr.width && ev.clientX > zr.left && ev.clientX < zr.right && ev.clientY > zr.top && ev.clientY < zr.bottom)
          return zones[i].dataset.z;
      }
      return "near";
    }
    return null;
  }
  function dragFeedback(o) {
    document.querySelectorAll(".kbc-dz").forEach(function (z) {
      z.classList.toggle("hot", z.dataset.z === o);
    });
    $("#kbc-inbox").classList.toggle("hot", o === "bin");
  }
  function setDragging(on) {
    $("#kbc-desk").classList.toggle("dz-on", on);
  }

  /* ================= card drag =================
     拖曳期間把卡片移到 #kbc-draglayer（.kbc-root 直屬、z-index 高於寫作桌四格落點），
     座標改用 viewport（draglayer = position:absolute; inset:0）。如此正在拖的卡永遠
     渲染在 drop zone 之上、跟手清楚（修修回饋②：原本卡在 field 被 desk 的 dz 擋住）。 */
  function liftToDragLayer(elx, clientX, clientY, grabX, grabY) {
    var layer = $("#kbc-draglayer");
    layer.appendChild(elx);
    elx.classList.add("dragging");
    elx.style.left = clientX - grabX + "px";
    elx.style.top = clientY - grabY + "px";
    elx.style.transform = "none";
    elx.style.zIndex = ++zCounter;
  }
  function startCardDrag(elx, e) {
    if (elx.setPointerCapture) {
      try {
        elx.setPointerCapture(e.pointerId);
      } catch (er) {
        /* ignore */
      }
    }
    setDragging(true);
    // 拖前的原位（field 內、無旋轉的 style 值）——純點擊時精確還原，不漂移。
    var origLeft = elx.style.left;
    var origTop = elx.style.top;
    var moved = false;
    // 抓取點：相對卡片左上角的位移（用卡片目前在 viewport 的位置算）。
    var r = elx.getBoundingClientRect();
    var grabX = e.clientX - r.left;
    var grabY = e.clientY - r.top;
    liftToDragLayer(elx, e.clientX, e.clientY, grabX, grabY);
    function mv(ev) {
      moved = true;
      elx.style.left = ev.clientX - grabX + "px";
      elx.style.top = ev.clientY - grabY + "px";
      dragFeedback(overTarget(ev));
    }
    function up(ev) {
      elx.classList.remove("dragging");
      document.removeEventListener("pointermove", mv);
      document.removeEventListener("pointerup", up);
      var o = overTarget(ev);
      dragFeedback(null);
      setDragging(false);
      if (o === "bin") {
        binned[elx.dataset.title] = true;
        binCount++;
        $("#kbc-bin-n").textContent = binCount ? binCount + " 張" : "";
        elx.remove();
        toast("「" + elx.dataset.title + "」已收回卡片盒");
        return;
      }
      if (o && o !== "near") {
        commitLink(elx.dataset.title, elx, o);
        return;
      }
      // 沒落在有效落點：卡片留在場上 + 換新角度，不重排其他卡（修修回饋）。
      var field = $("#kbc-field");
      if (moved) {
        // 真拖動：放在放手的位置（自由擺放）。
        // 座標必須在 appendChild **之前**讀——一旦搬進 field，drag-layer 的 viewport
        // style.left 會被當成 field 相對值，卡片瞬間跳到最右（修修回饋的 X 軸 bug）。
        var er = elx.getBoundingClientRect();
        var fr = field.getBoundingClientRect();
        field.appendChild(elx);
        elx.style.left = Math.max(4, Math.min(fr.width - 170, er.left - fr.left)) + "px";
        elx.style.top = Math.max(4, Math.min(fr.height - 96, er.top - fr.top)) + "px";
      } else {
        // 純點擊：精確還原原位，只換角度（不漂移）。
        field.appendChild(elx);
        elx.style.left = origLeft;
        elx.style.top = origTop;
      }
      elx.style.transform = "rotate(" + (reduced ? 0 : rand(-2.2, 2.2)) + "deg)";
      elx.style.zIndex = ++zCounter;
    }
    document.addEventListener("pointermove", mv);
    document.addEventListener("pointerup", up);
  }
  function bindCards() {
    document.querySelectorAll("#kbc-field .kbc-card").forEach(function (elx) {
      elx.addEventListener("pointerdown", function (e) {
        startCardDrag(elx, e);
      });
    });
  }

  function commitLink(target, cardEl, type) {
    if (type === "src") {
      if (srcRefs.indexOf(target) >= 0) {
        toast("這張卡已經是來源了");
        return;
      }
      srcRefs.push(target);
      if (cardEl && cardEl.parentElement) cardEl.remove();
      renderEdges();
      toast("已加為來源 source_ref");
      return;
    }
    if (linked(target)) {
      toast("這張卡已經連結過了");
      return;
    }
    edges.push({ target: target, type: type, reason: "" });
    if (cardEl && cardEl.parentElement) cardEl.remove();
    renderEdges();
    var inps = document.querySelectorAll("#kbc-edges .reason-in");
    if (inps.length && inps[inps.length - 1].tagName === "INPUT") inps[inps.length - 1].focus();
    toast("已連結「" + TLBL[type] + "」");
  }

  /* ================= stacks（點＝攤平；拖＝可收回） ================= */
  function bindStacks() {
    document.querySelectorAll("#kbc-field .kbc-stack").forEach(function (elx) {
      elx.addEventListener("pointerdown", function (e) {
        var moved = false;
        var r = elx.getBoundingClientRect();
        var grabX = e.clientX - r.left;
        var grabY = e.clientY - r.top;
        var ox = e.clientX;
        var oy = e.clientY;
        function mv(ev) {
          if (!moved && Math.abs(ev.clientX - ox) + Math.abs(ev.clientY - oy) > 8) {
            moved = true;
            // 拖起整疊：抬到拖曳頂層（同單卡），跟手且不被落點擋住。
            liftToDragLayer(elx, ev.clientX, ev.clientY, grabX, grabY);
          }
          if (moved) {
            elx.style.left = ev.clientX - grabX + "px";
            elx.style.top = ev.clientY - grabY + "px";
            dragFeedback(overTarget(ev) === "bin" ? "bin" : null);
          }
        }
        function up(ev) {
          document.removeEventListener("pointermove", mv);
          document.removeEventListener("pointerup", up);
          elx.classList.remove("dragging");
          if (!moved) {
            openOverlay(elx.dataset.stack, elx.dataset.mocPath);
            return;
          }
          var o = overTarget(ev);
          dragFeedback(null);
          setDragging(false);
          if (o === "bin") {
            binned["stack:" + elx.dataset.stack] = true;
            binCount++;
            $("#kbc-bin-n").textContent = binCount ? binCount + " 張" : "";
            elx.remove();
            toast("整疊「" + elx.dataset.stack + "」已收回 — 桌面清爽了");
          } else {
            // 沒丟進回收盒：整疊留在放手的位置（自由擺放），不重排其他卡（修修回饋）。
            // 同單卡：座標必須在 appendChild 之前讀，否則 viewport 值被當 field 相對值跳掉。
            var field = $("#kbc-field");
            var er = elx.getBoundingClientRect();
            var fr = field.getBoundingClientRect();
            field.appendChild(elx);
            elx.style.left = Math.max(4, Math.min(fr.width - 180, er.left - fr.left)) + "px";
            elx.style.top = Math.max(4, Math.min(fr.height - 120, er.top - fr.top)) + "px";
            elx.style.zIndex = ++zCounter;
          }
        }
        document.addEventListener("pointermove", mv);
        document.addEventListener("pointerup", up);
      });
    });
  }

  /* ================= overlay（疊卡攤平，lazy load MOC 成員） ================= */
  function fetchMembers(mocPath, cb) {
    if (memberCache[mocPath]) {
      cb(memberCache[mocPath]);
      return;
    }
    fetch("/kb/api/moc/members?moc_path=" + encodeURIComponent(mocPath))
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (j) {
        var members = (j && j.members) || [];
        memberCache[mocPath] = members;
        cb(members);
      })
      .catch(function () {
        cb(null);
      });
  }

  function openOverlay(name, mocPath) {
    closeOverlay();
    overlayEl = el("div", "kbc-overlay");
    var head = el("div", "kbc-ov-head");
    head.appendChild(el("b", null, name));
    head.appendChild(el("span", null, "載入中…"));
    var close = el("button", null, "關閉");
    close.type = "button";
    close.addEventListener("click", closeOverlay);
    head.appendChild(close);
    var grid = el("div", "kbc-ov-grid");
    overlayEl.appendChild(head);
    overlayEl.appendChild(grid);
    document.body.appendChild(overlayEl);
    overlayEl.addEventListener("pointerdown", function (e) {
      if (e.target === overlayEl || e.target.classList.contains("kbc-ov-grid")) closeOverlay();
    });

    fetchMembers(mocPath, function (members) {
      if (!overlayEl) return;
      if (members === null) {
        head.querySelector("span").textContent = "成員載入失敗";
        grid.appendChild(el("div", "kbc-ov-empty", "無法載入這個 MOC 的成員，稍後再試。"));
        return;
      }
      head.querySelector("span").textContent = members.length + " 張";
      if (!members.length) {
        grid.appendChild(el("div", "kbc-ov-empty", "這個 MOC 還沒有歸位的成員卡。"));
        return;
      }
      members.forEach(function (mem) {
        var title = mem.title || (mem.card_path || "").split("/").pop();
        var g = el("div", "kbc-gcard");
        g.dataset.title = title;
        g.appendChild(el("div", "t", title));
        var m = el("div", "m");
        m.appendChild(el("span", "kbc-minitag", name));
        if (mem.status) m.appendChild(el("span", "kbc-minitag st-" + mem.status, mem.status));
        g.appendChild(m);
        bindGcardPull(g, title, name);
        grid.appendChild(g);
      });
    });
  }

  function bindGcardPull(g, title, name) {
    g.addEventListener("pointerdown", function (e) {
      var pulled = false;
      var cardEl = null;
      var ox = e.clientX;
      var oy = e.clientY;
      function mv(ev) {
        if (!pulled && Math.abs(ev.clientX - ox) + Math.abs(ev.clientY - oy) > 8) {
          pulled = true;
          closeOverlay();
          setDragging(true);
          cardEl = el("div", "kbc-card dragging");
          cardEl.dataset.title = title;
          cardEl.appendChild(el("div", "t", title));
          var m = el("div", "m");
          m.appendChild(el("span", "kbc-minitag", name));
          cardEl.appendChild(m);
          // 拖出的卡直接放拖曳頂層（viewport 座標），跟手且不被落點擋住。
          $("#kbc-draglayer").appendChild(cardEl);
          cardEl.style.zIndex = ++zCounter;
          toast("從「" + name + "」挑出一張 — 其餘已收回");
        }
        if (pulled && cardEl) {
          cardEl.style.left = ev.clientX - 100 + "px";
          cardEl.style.top = ev.clientY - 26 + "px";
          dragFeedback(overTarget(ev));
        }
      }
      function up(ev) {
        document.removeEventListener("pointermove", mv);
        document.removeEventListener("pointerup", up);
        if (!pulled) return;
        cardEl.classList.remove("dragging");
        var o = overTarget(ev);
        dragFeedback(null);
        setDragging(false);
        if (o === "bin") {
          cardEl.remove();
        } else if (o && o !== "near") {
          commitLink(title, cardEl, o);
        } else {
          // 落在空白：把拖出的卡放回卡片場原地，並可再次拖曳。
          var f = $("#kbc-field").getBoundingClientRect();
          var cr = cardEl.getBoundingClientRect();
          $("#kbc-field").appendChild(cardEl);
          cardEl.style.left = cr.left - f.left + "px";
          cardEl.style.top = cr.top - f.top + "px";
          cardEl.style.zIndex = ++zCounter;
          cardEl.addEventListener("pointerdown", function (e2) {
            startCardDrag(cardEl, e2);
          });
        }
      }
      document.addEventListener("pointermove", mv);
      document.addEventListener("pointerup", up);
    });
  }

  function closeOverlay() {
    if (overlayEl) {
      overlayEl.remove();
      overlayEl = null;
    }
  }

  /* 兜底：全部相關 MOC 索引（手動入口；點一疊攤平） */
  function openMocIndex() {
    closeOverlay();
    var cand = curCand();
    var base = cand ? (cand.related_mocs || []).filter(function (m) {
      return !binned["stack:" + (m.name || m.moc_path)];
    }) : [];
    overlayEl = el("div", "kbc-overlay");
    var head = el("div", "kbc-ov-head");
    head.appendChild(el("b", null, "相關 MOC"));
    var close = el("button", null, "關閉");
    close.type = "button";
    close.addEventListener("click", closeOverlay);
    head.appendChild(close);
    var grid = el("div", "kbc-ov-grid");
    if (!base.length) {
      grid.appendChild(el("div", "kbc-ov-empty", "這張候選沒有 Robin 判定相關的 MOC。"));
    }
    base.forEach(function (m) {
      var name = m.name || m.moc_path.split("/").pop();
      var g = el("div", "kbc-gcard");
      g.style.cursor = "pointer";
      g.appendChild(el("div", "t", name));
      var mm = el("div", "m");
      mm.appendChild(el("span", "kbc-minitag", (m.card_count || 0) + " 張"));
      mm.appendChild(el("span", "kbc-minitag hi", "✦ 相關"));
      g.appendChild(mm);
      g.addEventListener("click", function () {
        openOverlay(name, m.moc_path);
      });
      grid.appendChild(g);
    });
    overlayEl.appendChild(head);
    overlayEl.appendChild(grid);
    document.body.appendChild(overlayEl);
    overlayEl.addEventListener("pointerdown", function (e) {
      if (e.target === overlayEl || e.target.classList.contains("kbc-ov-grid")) closeOverlay();
    });
  }

  /* ================= save（沿用 POST /kb/api/permanent） ================= */
  function buildSourceRefs(cand) {
    var refs = ((cand && cand.source_refs) || []).map(function (r) {
      return { literature_path: r.literature_path || "", anchor: r.anchor || "", raw: "" };
    });
    // 拖入「來源」格的既有卡 → 額外 raw wikilink source_ref。
    srcRefs.forEach(function (t) {
      refs.push({ literature_path: "", anchor: "", raw: "[[" + t + "]]" });
    });
    return refs;
  }

  function saveCard() {
    var cand = curCand();
    if (!cand) return;
    var title = $("#kbc-title").value.trim();
    var body = $("#kbc-body").value.trim();
    if (!title) {
      toast("檔名不能是空的——一句宣告句");
      $("#kbc-title").focus();
      return;
    }
    if (!body) {
      toast("正文是紅線內側——這段要你自己寫");
      $("#kbc-body").focus();
      return;
    }
    var missing = edges.filter(function (e) {
      return !e.reason.trim();
    }).length;
    if (missing) {
      toast("還有 " + missing + " 條連結沒寫理由");
      return;
    }

    var payload = {
      title: title,
      body: body,
      edges: edges.map(function (e) {
        return { edge_type: e.type, target: e.target, reason: e.reason.trim() };
      }),
      source_refs: buildSourceRefs(cand),
      candidate_id: cand.candidate_id || "",
      literature_slug:
        cand.__literature_slug ||
        (cand.primary_ref && cand.primary_ref.literature_path
          ? cand.primary_ref.literature_path.split("/").pop()
          : ""),
      fleeting_path: (activeCtx && activeCtx.fleeting_path) || "",
    };

    var saveBtn = $("#kbc-save");
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
        markSavedInList(activeCtx, res.json.path);
        cand.__done = true;
        if (cand.candidate_id) doneIds[cand.candidate_id] = true;
        // fleeting 開卡後不再循環候選（一次性）；候選開卡則自動接下一張。
        activeCtx = null;
        doneCount++;
        advance(title, cand.__fleeting || cand.__adhoc);
      })
      .catch(function () {
        saveBtn.removeAttribute("disabled");
        toast("存檔失敗，請重試");
      });
  }

  function advance(savedTitle, wasFleeting) {
    function finish() {
      $("#kbc-desk").classList.remove("flyout");
      edges = [];
      srcRefs = [];
      $("#kbc-body").value = "";
      $("#kbc-title").dataset.dirty = "";
      // fleeting 開卡是一次性：存完關畫布回候選清單。
      if (wasFleeting) {
        closeCanvas();
        toast("✓ 已存入：" + savedTitle);
        return;
      }
      render();
      var next = curCand();
      toast(
        next
          ? "✓ 已存入：" + savedTitle + " — 下一張：「" + next.suggested_title + "」"
          : "✓ 已存入：" + savedTitle + " — 今日候選清空 🎉"
      );
    }
    if (reduced) {
      finish();
      return;
    }
    $("#kbc-desk").classList.add("flyout");
    setTimeout(finish, 300);
  }

  // 存卡後在候選清單把對應 panel 標 done（畫布關閉後清單一致）。
  function markSavedInList(ctx, path) {
    var panelId = ctx && ctx.panelId;
    var actId = ctx && ctx.actId;
    if (!panelId && ctx && ctx.candidate_id) {
      panelId = "panel-" + ctx.candidate_id;
      actId = "act-" + ctx.candidate_id;
    }
    if (!panelId) return;
    var panel = document.getElementById(panelId);
    var act = document.getElementById(actId);
    if (panel) panel.classList.add("done");
    if (act) {
      act.innerHTML = "";
      var tag = el("span", "sho-tag sho-tag--success", "已開卡");
      act.appendChild(tag);
      if (path) act.appendChild(el("span", "sho-mono", path + " · author: human"));
    }
  }

  /* ================= 開 / 關畫布（N528⑥：開卡的唯一介面） =================
     kb_review.js 的「開卡」按鈕 → window.__kbCanvasOpen(ctx)。
     ctx.kind === "cand"：把佇列定位到該候選（candidate_id）。
     ctx.kind === "fleet"：注入一張一次性合成候選（帶 fleeting 正文預填 + raw source_ref）。 */
  var activeCtx = null;

  function openCanvas(ctx) {
    ctx = ctx || {};
    activeCtx = ctx;
    // 每次開卡都從乾淨草稿開始。
    edges = [];
    srcRefs = [];
    if (ctx.kind === "fleet") {
      // 合成一次性候選並插到目前位置，畫布走同一套渲染 / 存卡流程。
      var synth = {
        candidate_id: "",
        suggested_title: "",
        edges: [],
        related_pool: [],
        related_mocs: [],
        source_refs: ctx.source_refs || [],
        __fleeting: true,
        __body_prefill: ctx.body_prefill || "",
      };
      CANDS.splice(centerIdx, 0, synth);
    } else if (ctx.kind === "adhoc") {
      // 隨手 / ingest 完馬上開卡（缺口 A）：合成一次性空白候選，可帶來源 source_ref。
      // 與 fleet 同走一次性流程；差別只在預填來源（非 fleeting 正文），存完即關。
      CANDS.splice(centerIdx, 0, {
        candidate_id: "",
        suggested_title: "",
        edges: [],
        related_pool: [],
        related_mocs: [],
        source_refs: ctx.source_refs || [],
        __adhoc: true,
        __body_prefill: "",
        __literature_slug: ctx.literature_slug || "",
      });
    } else if (ctx.candidate_id) {
      // 定位到指定候選；找不到就維持目前游標。
      for (var i = 0; i < CANDS.length; i++) {
        if (CANDS[i].candidate_id === ctx.candidate_id) {
          centerIdx = i;
          break;
        }
      }
    }
    showCanvas();
  }

  function showCanvas() {
    var root = $("#kbc-root");
    root.hidden = false;
    document.body.classList.add("kbc-active");
    $("#kbc-title").dataset.dirty = "";
    $("#kbc-body").value = "";
    render();
    // fleeting 正文預填（render 之後寫，避免被清空）。
    var cur = curCand();
    if (cur && cur.__fleeting && cur.__body_prefill) {
      $("#kbc-body").value = cur.__body_prefill;
    }
    setTimeout(function () {
      $("#kbc-body").focus();
    }, 50);
  }

  function closeCanvas() {
    closeOverlay();
    $("#kbc-popover").classList.remove("on");
    $("#kbc-root").hidden = true;
    document.body.classList.remove("kbc-active");
    activeCtx = null;
  }
  // 對外開卡入口（kb_review.js 呼叫）。
  window.__kbCanvasOpen = openCanvas;

  /* ================= wire ================= */
  function wire() {
    $("#kbc-save").addEventListener("click", saveCard);
    $("#kbc-mocbox").addEventListener("click", openMocIndex);
    $("#kbc-back").addEventListener("click", closeCanvas);
    $("#kbc-title").addEventListener("input", function () {
      $("#kbc-title").dataset.dirty = "1";
    });

    // ＋開新卡 / ingest 完直達開卡（缺口 A）：空白 adhoc 畫布；
    // ?open=adhoc&slug=<source> → 從剛 ingest 的來源預填 source_ref 開卡。
    var newBtn = $("#kb-newcard");
    if (newBtn) {
      newBtn.addEventListener("click", function () {
        openCanvas({ kind: "adhoc" });
      });
    }
    try {
      var qp = new URLSearchParams(location.search);
      if (qp.get("open") === "adhoc") {
        var sgl = qp.get("slug") || "";
        var ax = { kind: "adhoc" };
        if (sgl) {
          ax.literature_slug = sgl;
          ax.source_refs = [{ literature_path: "KB/Wiki/Sources/" + sgl, anchor: "", raw: "" }];
        }
        openCanvas(ax);
        // 清掉 query，重新整理不再自動彈卡。
        try {
          history.replaceState(null, "", location.pathname);
        } catch (e2) {
          /* ignore */
        }
      }
    } catch (e) {
      /* 無 URLSearchParams → 略過自動開卡 */
    }

    // 點空白關 popover / peek 預覽
    document.addEventListener("pointerdown", function (e) {
      if (!e.target.closest("#kbc-popover") && !e.target.closest(".kbc-ebadge")) {
        $("#kbc-popover").classList.remove("on");
        editIdx = -1;
      }
      if (!e.target.closest("#kbc-peekpop") && !e.target.closest(".kbc-peek")) {
        closePeek();
      }
    });
    // esc：先關 peek/overlay/popover，否則關畫布回候選清單。
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      if ($("#kbc-root").hidden) return;
      if ($("#kbc-peekpop")) {
        closePeek();
        return;
      }
      if (overlayEl) {
        closeOverlay();
        return;
      }
      if ($("#kbc-popover").classList.contains("on")) {
        $("#kbc-popover").classList.remove("on");
        editIdx = -1;
        return;
      }
      closeCanvas();
    });
    var resizeTimer;
    window.addEventListener("resize", function () {
      if ($("#kbc-root").hidden) return;
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function () {
        closeOverlay();
        renderKeepText();
      }, 150);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }
})();
