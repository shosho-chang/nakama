/* YouTube 影片封面版型 — 繪製邏輯（搭配 youtube-cover.css）。
 *
 *   const cover = YouTubeCover.mount(rootEl, { assetUrl: p => url, logoUrl: url });
 *   cover.draw(recipe, { selected: "person" });   // recipe = YouTubeCoverRecipeV1（JSON）
 *   await cover.ready();                          // 圖片與字型都載入後 resolve（出圖用）
 *
 * recipe 欄位與 shared/schemas/youtube_packaging.py 一一對應；素材是 vault-relative 路徑，
 * 由 assetUrl 轉成實際網址（Bridge 走 asset 路由、桌機出圖走 file://）。
 */
(function (global) {
  "use strict";

  // 主圖外框：全部是品牌橘 #F37425 的純幾何（品牌書 p15 斜切四邊形、p22 框＋碎片）。
  // 每片：[left, top, width, height, rotate°, class, borderWidth]；class ""＝實心、"ol"＝外框線、"sk"＝斜切
  const FRAMES = {
    skew: (w, h, u) => [
      [-u * 1.5, -u * .9, w + u * 2.2, h + u * 1.9, 0, "ol sk", u * .17],
      [-u * 2.4, h * .06, h * .05, h * .15, -14], [-u * 2.6, h * .27, h * .035, h * .09, 9],
      [w + u * .9, h * .72, h * .05, h * .14, 12], [w + u * 1.3, h * .92, h * .03, h * .08, -8]],
    offset: (w, h, u) => [[u * .9, u * .9, w + u * .4, h + u * .3, 0, "sk"]],
    double: (w, h, u) => [
      [-u * .7, -u * .7, w + u * 1.4, h + u * 1.4, 0, "ol sk", u * .09],
      [-u * 1.6, -u * 1.6, w + u * 3.2, h + u * 3.2, 0, "ol sk", u * .09]],
    steps: (w, h, u) => {   // 三階各佔一欄、互不重疊，越往右越高
      const s = u * .95;
      return [[w * .6, h * .58, w * .4 + s, h * .42 + u, 0, ""],
              [w + s, h * .36, s, h * .64 + u, 0, ""],
              [w + s * 2, h * .14, s, h * .86 + u, 0, ""]];
    },
    brackets: (w, h, u) => {
      const t = u * .28, a = w * .32;
      return [[-u, -u, a, t, 0, ""], [-u, -u, t, a, 0, ""],
              [w + u - a, h + u - t, a, t, 0, ""], [w + u - t, h + u - a, t, a, 0, ""]];
    },
    burst: (w, h, u) => [
      [-u * 1.2, -u * 1.2, w + u * 2.4, h + u * 2.4, 0, "ol sk", u * .2],
      [-u * 2.6, h * .05, h * .05, h * .16, -16], [-u * 3.2, h * .24, h * .03, h * .10, 11],
      [-u * 2.4, h * .40, h * .04, h * .07, -6], [-u * 3.0, h * .78, h * .035, h * .12, 14],
      [w + u * 1.6, h * .10, h * .04, h * .13, 13], [w + u * 2.3, h * .30, h * .03, h * .08, -10],
      [w + u * 1.5, h * .66, h * .05, h * .15, 9], [w + u * 2.4, h * .88, h * .03, h * .07, -12]],
    none: () => [],
  };
  const FRAME_LABELS = [["skew", "斜切框"], ["burst", "爆裂碎片"], ["offset", "錯位色塊"],
    ["double", "雙層框"], ["steps", "階梯"], ["brackets", "角框"], ["none", "無"]];

  function el(tag, cls, parent) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (parent) parent.append(e);
    return e;
  }

  function loaded(img) {
    if (!img.getAttribute("src")) return Promise.resolve();
    if (img.complete && img.naturalWidth) return Promise.resolve();
    return new Promise(r => { img.addEventListener("load", r, {once: true}); img.addEventListener("error", r, {once: true}); });
  }

  function mount(root, opts) {
    opts = opts || {};
    const assetUrl = opts.assetUrl || (p => p);
    root.classList.add("ytc");
    root.innerHTML = "";
    const bg = el("div", "ytc-bg", root); el("div", "ytc-warm", root); el("div", "ytc-shade", root);
    const feat = el("div", "ytc-layer ytc-feature", root); feat.dataset.role = "feature";
    const deco = el("div", "ytc-deco", feat);
    const b = el("div", "ytc-b", feat);
    const fimg = el("img", "", b); fimg.alt = ""; fimg.draggable = false;
    el("div", "ytc-spine", b);
    const guest = el("img", "ytc-layer ytc-person", root); guest.dataset.role = "guest";
    guest.alt = ""; guest.draggable = false;
    const person = el("img", "ytc-layer ytc-person", root); person.dataset.role = "person";
    person.alt = ""; person.draggable = false;
    const text = el("div", "ytc-layer ytc-text", root); text.dataset.role = "text";
    const lead = el("span", "ytc-lead", text), main = el("span", "ytc-main", text);
    const logo = el("img", "ytc-layer ytc-logo", root); logo.dataset.role = "logo"; logo.alt = "";
    logo.draggable = false;
    el("div", "ytc-bar t", root); el("div", "ytc-bar b", root);

    let last = null, lastOpts = {};
    const setSrc = (img, url) => { if (img.dataset.src !== url) { img.src = url; img.dataset.src = url; } };
    fimg.addEventListener("load", () => last && draw(last, lastOpts));
    person.addEventListener("load", () => last && draw(last, lastOpts));
    guest.addEventListener("load", () => last && draw(last, lastOpts));

    function draw(r, o) {
      last = r; lastOpts = o || {};
      // 背景
      bg.style.backgroundImage = r.bg.src ? `url("${assetUrl(r.bg.src)}")` : "none";
      bg.style.filter = `grayscale(${r.bg.grayscale}) brightness(${r.bg.brightness}) contrast(1.12) blur(${r.bg.blur}px)`;
      root.querySelector(".ytc-warm").style.opacity = r.bg.warm;
      // 主圖（中心點＋高；寬依圖片比例）
      const f = r.feature;
      if (f.src) setSrc(fimg, assetUrl(f.src));
      const aspect = fimg.naturalWidth ? fimg.naturalWidth / fimg.naturalHeight : 0.773;
      const fh = f.h, fw = fh * aspect;
      Object.assign(feat.style, {left: (f.cx - fw / 2) + "px", top: (f.cy - fh / 2) + "px",
        width: fw + "px", height: fh + "px", display: f.src ? "" : "none"});
      b.style.transform = `rotateY(${f.tilt}deg)`;
      deco.innerHTML = "";
      for (const [x, y, w, h, rot, cls, bw] of (FRAMES[f.frame_style] || FRAMES.skew)(fw, fh, fh * .07)) {
        const i = el("i", cls || "", deco);
        Object.assign(i.style, {left: x + "px", top: y + "px", width: w + "px", height: h + "px",
          transform: rot ? `rotate(${rot}deg)` : "", borderWidth: bw ? bw + "px" : ""});
      }
      // 人物（中心 x、頂 y、高）：修修＋選用的來賓（來賓疊在修修後面）
      placePerson(person, r.person);
      placePerson(guest, r.guest);
      // 大字（上行／主標，主標裡第一個橘框詞上橘底）
      const t = r.text, sz = t.size;
      Object.assign(text.style, {left: t.x + "px", top: t.y + "px", transform: `rotate(${t.rot || 0}deg)`});
      lead.textContent = t.lead || ""; lead.style.fontSize = sz * 0.46 + "px"; lead.style.display = t.lead ? "" : "none";
      main.style.fontSize = sz + "px"; main.textContent = "";
      const i = t.hl ? (t.main || "").indexOf(t.hl) : -1;
      if (i >= 0) {
        main.append(t.main.slice(0, i));
        el("b", "ytc-hl", main).textContent = t.hl;
        main.append(t.main.slice(i + t.hl.length));
      } else main.textContent = t.main || "";
      // logo、上下橘條
      const lg = r.logo || {};
      if (lg.on && opts.logoUrl) setSrc(logo, opts.logoUrl);
      Object.assign(logo.style, {left: (lg.x || 0) + "px", top: (lg.y || 0) + "px", height: (lg.h || 64) + "px",
        display: lg.on && opts.logoUrl ? "" : "none"});
      root.querySelectorAll(".ytc-bar").forEach(e => e.style.display = r.bars === false ? "none" : "");
      // 編輯選取框
      root.classList.toggle("editing", !!lastOpts.selected);
      root.querySelectorAll(".ytc-layer").forEach(l => l.classList.toggle("sel", l.dataset.role === lastOpts.selected));
    }

    function placePerson(img, p) {
      if (!p || !p.src) { img.style.display = "none"; return; }
      setSrc(img, assetUrl(p.src));
      const pw = img.naturalWidth ? p.h * img.naturalWidth / img.naturalHeight : 0;
      Object.assign(img.style, {height: p.h + "px", left: (p.cx - pw / 2) + "px", top: p.top + "px",
        transform: p.flip ? "scaleX(-1)" : "", display: ""});
      img.classList.toggle("glow", !!p.glow);
    }

    async function ready() {
      await Promise.all([loaded(fimg), loaded(person), loaded(guest), loaded(logo), document.fonts.ready]);
      if (r_bgUrl()) await new Promise(res => { const im = new Image(); im.onload = im.onerror = res; im.src = r_bgUrl(); });
      if (last) draw(last, lastOpts);
    }
    function r_bgUrl() { return last && last.bg.src ? assetUrl(last.bg.src) : ""; }

    return {draw, ready, root};
  }

  global.YouTubeCover = {mount, FRAMES, FRAME_LABELS};
})(window);
