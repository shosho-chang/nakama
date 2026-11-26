// 第 5 類「解釋動畫」共用品牌積木。每一種解說型態（比例、算式……）的 HTML 只負責自己的圖，
// 底板、碎片、標題、出處、進退場、「講到哪個亮哪個」、數字跳動都從這裡拿——品牌一致靠這份，不靠每支各自小心。
// 動作規則（品牌 motion token）：像剪紙啪地貼上——快（0.14–0.38 秒）、落地帶一點回彈、退場更快；不淡入淡出、不漂。
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const ORANGE = "#f57331", INK = "#000"; // SVG 屬性與 GSAP 顏色補間不吃 CSS 變數
  const SNAP = "power3.out", LAND = "back.out(1.7)", EXIT = "power2.in";
  // 手冊 p24「影片開頭」右下 3 塊碎片（1920×1080 座標；同小標題／文件 highlighter，原樣不重畫）
  const BR = [[[1683.5,751.5],[1920.1,845.5],[1920.1,661.9],[1733.5,598.4]],[[1399.4,959.4],[1542.0,1080.1],[1832.8,1080.1],[1479.8,831.0]],[[1626.0,860.3],[1782.6,1080.1],[1920.1,1080.1],[1920.1,973.8],[1808.7,780.6]]];
  // 側欄版只用右下貼底邊那 3 塊（同重點條列）
  const SIDE = [[[1399.4,959.4],[1542.0,1080.1],[1832.8,1080.1],[1479.8,831.0]],[[1626.0,860.3],[1782.6,1080.1],[1920.1,1080.1],[1920.1,973.8],[1808.7,780.6]],[[1267.7,1080.1],[1536.7,1080.1],[1279.5,1005.1]]];

  // 沒有 HyperFrames runtime（直接用瀏覽器開檔檢查版面）時，退回 data-composition-variables 的預設值
  function vars() {
    if (window.__hyperframes) return window.__hyperframes.getVariables() || {};
    const out = {};
    try {
      JSON.parse(document.documentElement.dataset.compositionVariables || "[]").forEach((d) => (out[d.id] = d.default));
    } catch (e) { /* 預設值壞掉就當沒有 */ }
    // 瀏覽器檢查版面時可用 ?spec=<JSON> 換內容（渲染時走 --variables-file，不經過這裡）
    try { const q = new URLSearchParams(location.search).get("spec"); if (q) out.spec = q; } catch (e) { /* 沒有網址參數 */ }
    return out;
  }
  function el(tag, cls, parent, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
  }
  function poly(svg, pts) {
    const e = document.createElementNS(NS, "polygon");
    e.setAttribute("fill", ORANGE);
    e.setAttribute("points", pts.map(([x, y]) => x + "," + y).join(" "));
    svg.appendChild(e);
    return e;
  }

  // 底板＋碎片＋標題＋出處＋進退場。回傳 { tl, stage, S, OUT }：stage 是內容區（安全框）。
  function frame(id, spec) {
    const root = document.getElementById("root");
    const S = Number(spec.show_sec), OUT = S - 0.55;
    const full = spec.layout !== "overlay";
    const tl = gsap.timeline({ paused: true });
    window.__timelines = window.__timelines || {};
    window.__timelines[id] = tl;

    const base = el("div", full ? "exk-bg" : "exk-panel", root);
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("class", "exk-shards");
    svg.setAttribute("viewBox", "0 0 1920 1080");
    root.appendChild(svg);
    const stage = el("div", full ? "exk-stage" : "exk-side", root);

    if (full) {
      tl.fromTo(base, { clipPath: "polygon(130% 0%, 130% 0%, 100% 100%, 100% 100%)" },
        { clipPath: "polygon(-30% 0%, 130% 0%, 100% 100%, 0% 100%)", duration: 0.4, ease: "power2.inOut" }, 0);
      tl.to(base, { clipPath: "polygon(-30% 0%, -30% 0%, 0% 100%, 0% 100%)", duration: 0.33, ease: EXIT }, S - 0.4);
      const PUSH = [160, 109], FROM = [360, 240];
      const pieces = [];
      BR.forEach((pts) => {
        const k = 3000 - pts.reduce((a, p) => a + p[0] + p[1], 0) / pts.length;
        pieces.push({ e: poly(svg, pts.map(([x, y]) => [1080 - y, 1920 - x])), d: -1, k });
        pieces.push({ e: poly(svg, pts), d: 1, k });
      });
      pieces.sort((a, b) => a.k - b.k).forEach((p, i) => {
        const at = 0.28 + 0.03 * (i % 4);
        gsap.set(p.e, { opacity: 0 });
        tl.set(p.e, { opacity: 1 }, at);
        tl.fromTo(p.e, { x: p.d * FROM[0], y: p.d * FROM[1] },
          { x: p.d * PUSH[0], y: p.d * PUSH[1], duration: 0.3, ease: SNAP }, at);
        tl.to(p.e, { x: p.d * FROM[0], y: p.d * FROM[1], duration: 0.22, ease: EXIT }, OUT);
        tl.set(p.e, { opacity: 0 }, OUT + 0.22);
      });
    } else {
      // 側欄跟畫面搬移同一秒開始、同樣 0.5 秒；結束前 0.05 秒完全離開
      tl.fromTo(base, { xPercent: 100 }, { xPercent: 0, duration: 0.5, ease: "power2.inOut" }, 0);
      tl.to(base, { xPercent: 100, duration: 0.3, ease: EXIT }, S - 0.35);
      SIDE.forEach((pts) => {
        const e = poly(svg, pts);
        tl.fromTo(e, { y: 420 }, { y: 110, duration: 0.3, ease: SNAP }, 0.35);
        tl.to(e, { y: 420, duration: 0.22, ease: EXIT }, OUT);
      });
    }

    if (spec.title) {
      const t = el("div", "exk-title", stage);
      el("span", null, t, spec.title);
      tl.fromTo(t, { "--bar": 0 }, { "--bar": 1, duration: 0.3, ease: SNAP }, 0.42);
      tl.fromTo(t.firstChild, { y: 24, opacity: 0 }, { y: 0, opacity: 1, duration: 0.22, ease: SNAP }, 0.36);
    }
    if (spec.source) {
      const s = el("div", "exk-src", root, spec.source);
      if (full) { s.style.right = "150px"; s.style.top = "182px"; }
      else { s.style.left = "1210px"; s.style.top = "176px"; }
      tl.fromTo(s, { opacity: 0 }, { opacity: 1, duration: 0.2 }, 0.6);
    }
    return { tl, stage, S, OUT, full, src: root.querySelector(".exk-src") };
  }

  // 每支最後呼叫：內容在碎片退場同一刻往下掉出去；時間軸補到 60 秒（交件時截到停留秒數）
  function end(ctx) {
    ctx.tl.to(ctx.stage.children, { y: 40, opacity: 0, duration: 0.22, ease: EXIT, stagger: 0.02 }, ctx.OUT);
    if (ctx.src) ctx.tl.to(ctx.src, { opacity: 0, duration: 0.14 }, ctx.OUT);
    ctx.tl.to({}, { duration: Math.max(60, ctx.S + 1) }, 0);
    check(ctx);
  }

  // 版面檢查（網址有 ?check 才跑；渲染不經過這裡）：等品牌字型載入、跳到快結束（全部元素都出來了），
  // 量每一段字的位置——超出安全框、兩段字重疊都列出來，寫進 <pre id="exk-check">（tools/chart_check.py 讀）。
  // 橫向用字的實際寬度，直向用元素的行框（line-height 1，不算字體上下留白）。
  const SAFE = { full: [150, 90, 1770, 890], side: [1210, 80, 1850, 880] };
  function check(ctx) {
    let q = null;
    try { q = new URLSearchParams(location.search); } catch (e) { return; }
    if (!q.has("check")) return;
    const t = Number(q.get("check")) || ctx.S - 0.7;
    const texts = [...document.querySelectorAll("#root *")].map((e) => e.textContent).join("");
    document.fonts.load('900 100px "Noto Sans TC Black"', texts).then(() => document.fonts.ready).then(() => {
      ctx.tl.seek(t, false);
      const [x0, y0, x1, y1] = ctx.full ? SAFE.full : SAFE.side;
      const rects = [];
      const walk = document.createTreeWalker(document.getElementById("root"), NodeFilter.SHOW_TEXT);
      let n;
      while ((n = walk.nextNode())) {
        if (!n.textContent.trim()) continue;
        const host = n.parentElement;
        if (getComputedStyle(host).opacity === "0") continue;
        const r = document.createRange(); r.selectNodeContents(n);
        const b = r.getBoundingClientRect(), e = host.getBoundingClientRect();
        if (!b.width) continue;
        rects.push({ text: n.textContent.trim(), box: [b.left, e.top, b.right, e.bottom].map(Math.round) });
      }
      const outside = rects.filter(({ box: [l, tp, r, bt] }) => l < x0 || tp < y0 || r > x1 || bt > y1);
      const overlap = [];
      rects.forEach((a, i) => rects.slice(i + 1).forEach((c) => {
        const [al, at, ar, ab] = a.box, [cl, ct, cr, cb] = c.box;
        if (al < cr - 2 && cl < ar - 2 && at < cb - 2 && ct < ab - 2) overlap.push([a.text, c.text]);
      }));
      const pre = el("pre", null, document.body, JSON.stringify({ at: t, outside, overlap }));
      pre.id = "exk-check";
    });
  }

  // 啪地貼上（落地回彈）
  function land(tl, target, at, from) {
    tl.fromTo(target, Object.assign({ opacity: 0, y: 30, scale: 0.94 }, from || {}),
      { opacity: 1, x: 0, y: 0, scale: 1, duration: 0.32, ease: LAND }, at);
  }

  // 「講到哪個亮哪個」：focus 清單 [[秒數, 元素或 null]…]；正在講的染橘，講過的回黑。
  function focus(tl, steps, prop) {
    const p = prop || "color";
    let prev = null;
    steps.forEach(([t, e]) => {
      if (prev && prev !== e) tl.to(prev, { [p]: INK, duration: 0.14 }, t);
      if (e) tl.to(e, { [p]: ORANGE, duration: 0.14 }, t);
      prev = e;
    });
  }

  // 數字跳動：from → to，落在指定格式（小數位）；跟長條同一條曲線
  // 千分位：3000 → 3,000（大數字一律加，讀得快）
  function num(x, digits) {
    const [i, d] = Math.abs(x).toFixed(digits || 0).split(".");
    return (x < 0 ? "-" : "") + i.replace(/\B(?=(\d{3})+(?!\d))/g, ",") + (d ? "." + d : "");
  }
  // prefix 給「NT$」這類放在數字前面的單位
  function count(tl, node, to, at, dur, digits, suffix, prefix) {
    const o = { v: 0 };
    const fmt = (x) => (prefix || "") + num(x, digits) + (suffix || "");
    node.textContent = fmt(0);
    tl.to(o, { v: to, duration: dur, ease: SNAP, onUpdate: () => (node.textContent = fmt(o.v)) }, at);
  }

  window.EXK = { vars, el, frame, end, land, focus, count, num, ORANGE, INK, SNAP, LAND, EXIT };
})();
