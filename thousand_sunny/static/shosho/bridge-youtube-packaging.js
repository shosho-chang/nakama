/* /bridge/youtube-packaging/<slug> 互動：圖層拖曳／滾輪縮放、素材挑選、存配方（桌機出圖）、
 * 上架標題紀錄（自動存）。版型繪製在 youtube-cover.js，跟桌機出圖共用。 */
(function () {
  "use strict";
  const BOOT = JSON.parse(document.getElementById("yt-boot").textContent);
  const SLUG = BOOT.slug, L = BOOT.lists, AB_MAX = BOOT.ab_max || 3;
  const base = `/bridge/youtube-packaging/${encodeURIComponent(SLUG)}`;
  const enc = p => encodeURIComponent(p);
  const $ = s => document.querySelector(s);
  const esc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));

  // ── 配方（沒存過就用預設版面） ──
  const S = BOOT.cover || {
    bg: {src: L.backgrounds[0] || "", brightness: 0.40, grayscale: 0.7, blur: 1.5, warm: 0.35},
    feature: {src: L.features[0] || "", cx: 975, cy: 372, h: 520, tilt: -12, frame_style: "skew"},
    person: {src: L.cutouts.find(c => c.includes("explaining")) || L.cutouts[0] || "",
             cx: 420, top: 44, h: 860, glow: true, flip: false},
    text: {x: 48, y: 486, size: 124, rot: -6, lead: "", main: "", hl: ""},
    logo: {on: false, x: 30, y: 30, h: 64}, bars: true,
  };
  const get = k => k.split(".").reduce((o, p) => o[p], S);
  const set = (k, v) => { const ps = k.split("."), last = ps.pop(); ps.reduce((o, p) => o[p], S)[last] = v; };

  const cover = YouTubeCover.mount($("#cover"), {assetUrl: p => `${base}/asset?p=${enc(p)}`});
  let sel = "person";
  const LABEL = {person: "修修", guest: "來賓", feature: "主圖", text: "大字"};
  const GUESTS = L.guests || [];
  let guestMem = S.guest || {src: GUESTS[0] || "", cx: 1040, top: 110, h: 760, glow: false, flip: false};
  function draw() {
    cover.draw(S, {selected: sel});
    const g = S.guest || guestMem;
    const m = {person: `x${Math.round(S.person.cx)} y${Math.round(S.person.top)} 高${Math.round(S.person.h)}`,
      guest: S.guest ? `x${Math.round(g.cx)} y${Math.round(g.top)} 高${Math.round(g.h)}` : "（未放）",
      feature: `x${Math.round(S.feature.cx)} y${Math.round(S.feature.cy)} 高${Math.round(S.feature.h)}`,
      text: `x${Math.round(S.text.x)} y${Math.round(S.text.y)} 字級${Math.round(S.text.size)}`};
    $("#meta").textContent = `${LABEL[sel]} ${m[sel]}`;
  }

  // ── 舞台縮放、拖曳、滾輪 ──
  const stage = $("#stage"), root = $("#cover");
  let scale = 1;
  const fit = () => { scale = stage.clientWidth / 1280; root.style.transform = `scale(${scale})`; };
  new ResizeObserver(fit).observe(stage); fit();
  const POS = {person: ["person.cx", "person.top"], guest: ["guest.cx", "guest.top"],
    feature: ["feature.cx", "feature.cy"], text: ["text.x", "text.y"]};
  const SIZE = {person: "person.h", guest: "guest.h", feature: "feature.h", text: "text.size"};
  let drag = null;
  stage.addEventListener("pointerdown", e => {
    if (sel === "guest" && !S.guest) return;   // 沒放來賓時不拖
    const [kx, ky] = POS[sel];
    drag = {x0: e.clientX, y0: e.clientY, vx: get(kx), vy: get(ky)};
    stage.setPointerCapture(e.pointerId); stage.classList.add("dragging");
  });
  stage.addEventListener("pointermove", e => {
    if (!drag) return;
    const [kx, ky] = POS[sel];
    set(kx, drag.vx + (e.clientX - drag.x0) / scale); set(ky, drag.vy + (e.clientY - drag.y0) / scale);
    draw();
  });
  const endDrag = () => { drag = null; stage.classList.remove("dragging"); };
  stage.addEventListener("pointerup", endDrag); stage.addEventListener("pointercancel", endDrag);
  stage.addEventListener("wheel", e => {
    e.preventDefault();
    if (sel === "guest" && !S.guest) return;
    const k = SIZE[sel]; set(k, get(k) * (e.deltaY < 0 ? 1.03 : 1 / 1.03)); draw();
  }, {passive: false});
  document.querySelectorAll("[data-sel]").forEach(b => b.onclick = () => {
    sel = b.dataset.sel;
    document.querySelectorAll("[data-sel]").forEach(x => x.setAttribute("aria-pressed", x === b ? "true" : "false"));
    draw();
  });

  // ── 欄位 ↔ 配方 ──
  document.querySelectorAll("[data-k]").forEach(el => {
    const k = el.dataset.k;
    if (el.type === "checkbox") { el.checked = !!get(k); el.onchange = () => { set(k, el.checked); draw(); }; }
    else { el.value = get(k); el.oninput = () => { set(k, el.type === "range" ? +el.value : el.value); draw(); bigtexts(); }; }
  });
  function thumbs(box, list, key) {
    box.innerHTML = "";
    for (const src of list) {
      const b = document.createElement("button"); b.type = "button";
      b.setAttribute("aria-pressed", get(key) === src ? "true" : "false");
      b.title = src.split("/").slice(-2).join("/");
      const im = document.createElement("img"); im.src = `${base}/thumb?p=${enc(src)}`; im.alt = b.title; im.loading = "lazy";
      b.append(im);
      b.onclick = () => { set(key, src); box.querySelectorAll("button").forEach(x => x.setAttribute("aria-pressed", x === b ? "true" : "false")); draw(); };
      box.append(b);
    }
  }
  thumbs($("#cutouts"), L.cutouts, "person.src");
  thumbs($("#features"), L.features, "feature.src");
  thumbs($("#bgs"), L.backgrounds, "bg.src");

  // ── 來賓（本集 assets/guest-*.png；沒有就整區隱藏）──
  if (GUESTS.length) {
    $("#guest-sec").hidden = false;
    document.querySelector('[data-sel="guest"]').hidden = false;
    const box = $("#guests");
    const mark = () => box.querySelectorAll("button").forEach(b =>
      b.setAttribute("aria-pressed", (S.guest ? S.guest.src : "") === b.dataset.src ? "true" : "false"));
    const none = document.createElement("button"); none.type = "button"; none.className = "ytp-none";
    none.dataset.src = ""; none.textContent = "不放";
    none.onclick = () => { if (S.guest) guestMem = S.guest; S.guest = null; mark(); draw(); };
    box.append(none);
    for (const src of GUESTS) {
      const b = document.createElement("button"); b.type = "button"; b.dataset.src = src;
      b.title = src.split("/").pop();
      const im = document.createElement("img"); im.src = `${base}/thumb?p=${enc(src)}`; im.alt = b.title; im.loading = "lazy";
      b.append(im);
      b.onclick = () => { S.guest = {...(S.guest || guestMem), src}; mark(); draw(); };
      box.append(b);
    }
    for (const [id, k] of [["#g-glow", "glow"], ["#g-flip", "flip"]]) {
      const cb = $(id); cb.checked = !!(S.guest || guestMem)[k];
      cb.onchange = () => { (S.guest || guestMem)[k] = cb.checked; draw(); };
    }
    mark();
  }

  const frames = $("#frames");
  for (const [key, label] of YouTubeCover.FRAME_LABELS) {
    const b = document.createElement("button"); b.className = "ytp-tab"; b.type = "button"; b.textContent = label;
    b.dataset.frame = key;
    b.onclick = () => { S.feature.frame_style = key; frameTabs(); draw(); };
    frames.append(b);
  }
  function frameTabs() {
    frames.querySelectorAll("[data-frame]").forEach(b => b.setAttribute("aria-pressed", b.dataset.frame === S.feature.frame_style ? "true" : "false"));
  }
  frameTabs();

  // ── 大字候選（只改封面，不碰標題） ──
  function bigtexts() {
    const box = $("#bigtexts"); box.innerHTML = "";
    for (const o of BOOT.bigtexts) {
      const b = document.createElement("button"); b.className = "ytp-opt"; b.type = "button";
      const on = S.text.lead === o.lead && S.text.main === o.main && S.text.hl === o.hl;
      b.setAttribute("aria-pressed", on ? "true" : "false");
      const i = o.hl ? o.main.indexOf(o.hl) : -1;
      const main = i >= 0 ? `${esc(o.main.slice(0, i))}<b>${esc(o.hl)}</b>${esc(o.main.slice(i + o.hl.length))}` : esc(o.main);
      b.innerHTML = (o.lead ? esc(o.lead) + "／" : "") + main;
      b.onclick = () => {
        Object.assign(S.text, {lead: o.lead, main: o.main, hl: o.hl});
        for (const k of ["text.lead", "text.main", "text.hl"]) document.querySelector(`[data-k="${k}"]`).value = get(k);
        bigtexts(); draw();
      };
      box.append(b);
    }
  }
  bigtexts();

  // ── 存配方 → 桌機出圖 → 輪詢狀態 ──
  let polling = null;
  function showStatus(st) {
    const el = $("#render-state");
    el.classList.remove("err");
    if (!st || !st.has_recipe) { el.textContent = ""; return; }
    if (st.render_error && !st.needs_render) { el.textContent = "出圖失敗：" + st.render_error; el.classList.add("err"); }
    else if (st.needs_render) el.textContent = "等桌機出圖…";
    else if (st.rendered_at) {
      const d = new Date(st.rendered_at);
      el.textContent = `已出圖 ${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    }
    if (st.png_version && !st.needs_render) {
      $("#result").hidden = false;
      $("#result-full").src = `${base}/cover.png?v=${st.png_version}`;
      $("#result-small").src = `${base}/cover.png?small=1&v=${st.png_version}`;
    }
  }
  function poll() {
    clearTimeout(polling);
    polling = setTimeout(async () => {
      try {
        const st = await (await fetch(`${base}/status`)).json();
        showStatus(st);
        if (st.needs_render) poll();
      } catch (e) { poll(); }
    }, 3000);
  }
  $("#save").onclick = async () => {
    const btn = $("#save"); btn.disabled = true;
    try {
      const r = await fetch(`${base}/cover`, {method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({recipe: S})});
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || j.detail || r.status);
      showStatus(j); poll();
    } catch (err) { $("#render-state").textContent = "存配方失敗：" + err.message; $("#render-state").classList.add("err"); }
    btn.disabled = false;
  };
  showStatus(BOOT.status);
  if (BOOT.status && BOOT.status.needs_render) poll();

  // ── 上架標題紀錄：微調文字、已上頻道（＝A/B，最多 3）、影片網址；自動存 ──
  const T = BOOT.tracking;
  let trkTimer = null;
  function trkSave(delay) {
    clearTimeout(trkTimer);
    $("#trk-status").textContent = "…"; $("#trk-status").classList.remove("err");
    trkTimer = setTimeout(async () => {
      try {
        const r = await fetch(`${base}/titles`, {method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({...T, base_updated_at: T.updated_at || null})});
        const j = await r.json();
        if (r.status === 409) {   // 別的頁面先存過：用最新版重畫，不覆蓋
          Object.assign(T, j.current); trkRender();
          throw new Error(j.error);
        }
        if (!r.ok) throw new Error(j.error || j.detail || r.status);
        Object.assign(T, j);
        const d = new Date(j.updated_at);
        $("#trk-status").textContent = `已記錄 ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
        trkRender(false);
      } catch (err) { $("#trk-status").textContent = err.message; $("#trk-status").classList.add("err"); }
    }, delay == null ? 600 : delay);
  }
  const fitArea = el => { el.style.height = "auto"; el.style.height = el.scrollHeight + "px"; };
  const md = iso => { if (!iso) return ""; const d = new Date(iso); return `${d.getMonth() + 1}/${d.getDate()} 上頻道`; };
  function trkRender(full) {
    const box = $("#titles"), n = T.titles.filter(t => t.published).length;
    if (full === false) {
      box.querySelectorAll(".ytp-trk").forEach(row => {
        const t = T.titles.find(x => x.id === row.dataset.id); if (!t) return;
        row.classList.toggle("on", !!t.published);
        row.querySelector("input[type=checkbox]").disabled = !t.published && n >= AB_MAX;
        row.querySelector(".ytp-trk-date").textContent = md(t.published_at);
      });
      return;
    }
    box.innerHTML = "";
    for (const t of T.titles) {
      const row = document.createElement("div"); row.className = "ytp-trk" + (t.published ? " on" : ""); row.dataset.id = t.id;
      const ta = document.createElement("textarea"); ta.className = "ytp-trk-text"; ta.rows = 1; ta.value = t.text;
      ta.setAttribute("aria-label", "標題");
      ta.oninput = () => { t.text = ta.value; fitArea(ta); trkSave(900); };
      const meta = document.createElement("div"); meta.className = "ytp-trk-meta";
      meta.innerHTML = `<label><input type="checkbox"><span>已上頻道</span></label><span class="ytp-trk-date"></span>`;
      const cb = meta.querySelector("input");
      cb.checked = !!t.published; cb.disabled = !t.published && n >= AB_MAX;
      meta.querySelector(".ytp-trk-date").textContent = md(t.published_at);
      cb.onchange = () => { t.published = cb.checked; if (!cb.checked) t.published_at = null; trkRender(false); trkSave(0); };
      if (t.source === "custom") {
        const del = document.createElement("button"); del.className = "ytp-trk-del"; del.type = "button";
        del.textContent = "×"; del.setAttribute("aria-label", "刪除這條標題");
        del.onclick = () => { T.titles.splice(T.titles.indexOf(t), 1); trkRender(); trkSave(0); };
        meta.append(del);
      }
      row.append(ta, meta);
      if (t.original_text && t.text !== t.original_text) {
        const o = document.createElement("div"); o.className = "ytp-trk-orig"; o.textContent = "原：" + t.original_text;
        row.append(o);
      }
      box.append(row);
      requestAnimationFrame(() => fitArea(ta));
    }
  }
  $("#v-url").value = T.video_url || "";
  $("#v-url").oninput = () => { T.video_url = $("#v-url").value.trim(); trkSave(900); };
  $("#add-title").onclick = () => {
    const id = "c" + Date.now().toString(36);
    T.titles.push({id, text: "", original_text: "", source: "custom", published: false, published_at: null, edited_at: null});
    trkRender(); document.querySelector(`.ytp-trk[data-id="${id}"] textarea`).focus();
  };
  trkRender();

  document.fonts.ready.then(draw);
  draw();
})();
