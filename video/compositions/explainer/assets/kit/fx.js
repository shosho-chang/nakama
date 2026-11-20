// 漫畫／動畫的動態特效積木（GSAP＋SVG，給 HyperFrames 合成頁用）
//   手法與數值參考 buildwithhanif/claude-animation-skill（MIT）的 lib/fx.mjs、references/motion.md，改寫成 GSAP 時間軸版。
//
// 兩條時間軸：
//   act＝「劇情時間」：角色、道具、會動的東西（paused timeline，人偶的 pose/tween 都寫在這條）
//   tl ＝「真實時間」：鏡頭、字卡、特效
//   EXK_FX.drive(tl, act, …) 把真實時間換成劇情時間推進 act：
//     一拍二（twos）＝角色每 2 格才換一張，手繪動畫的節奏；鏡頭與特效留在 tl，照樣每格動（鏡頭一拍二會一頓一頓）
//     頓格（stops）＝撞到的瞬間劇情時間停 0.08–0.3 秒，特效照跑——衝擊感主要從這裡來
//
// 用法限制（youtube-explainer skill「漫畫」節）：特效只放在分鏡表標「強調」的那一拍；每頁一個強、最多兩個中。
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const svgEl = (tag, attrs, parent) => {
    const e = document.createElementNS(NS, tag);
    for (const a in attrs) e.setAttribute(a, attrs[a]);
    if (parent) parent.appendChild(e);
    return e;
  };
  const INK = "#000";

  // ── 劇情時間：頓格＋一拍二 ──
  // stops：[[劇情秒, 停多久], …]；fps：輸出影格率；twos：true＝角色一拍二
  function warp(stops, fps, twos) {
    const S = [...(stops || [])].sort((a, b) => a[0] - b[0]);
    return (t) => {
      let acc = 0, g = null;
      for (const [s, d] of S) { if (t < s + acc) break; if (t < s + acc + d) { g = s; break; } acc += d; }
      if (g === null) g = t - acc;
      if (twos) { const step = 2 / fps; g = Math.floor(g / step + 1e-6) * step; }
      return g;
    };
  }
  // 注意：渲染器是逐格 seek，GSAP 的 onUpdate／call 在 seek 時不會觸發——所以這裡全用「屬性補間＋自訂 ease」或逐格 set，不用回呼
  function drive(tl, act, o) {
    o = o || {};
    const start = o.start || 0, end = o.end, D = end - start, map = warp(o.stops, o.fps || 30, o.twos !== false);
    const g0 = map(start), g1 = map(end);
    act.time(g0);
    tl.fromTo(act, { time: g0 }, { time: g1, duration: D, immediateRender: false,
      ease: (p) => (map(start + p * D) - g0) / ((g1 - g0) || 1) }, start);
    const S = [...(o.stops || [])].sort((x, y) => x[0] - y[0]);
    return { real: (g) => { let acc = 0; for (const [s0, d] of S) { if (s0 >= g) break; acc += d; } return g + acc; } };
  }

  // ── 震動：衰減、固定亂數（每次渲染都一樣）。el＝要晃的那一層（格子內容，不是整頁、不含字卡）──
  function shake(tl, el, at, amp, dur) {
    amp = amp || 14; dur = dur || 0.45;
    for (let d = 0; d <= dur; d += 1 / 30) {                 // 每格 set 一次（seek 安全）
      const k = Math.exp(-d * 9) * amp;
      tl.set(el, { x: Math.sin(d * 97 + at) * k, y: Math.cos(d * 83 + at * 3) * k * 0.8 }, at + d);
    }
    tl.set(el, { x: 0, y: 0 }, at + dur + 1 / 30);
  }

  // ── 白閃：撞到那一格整格變白再退（上限 0.6，不然整格變一個白洞）──
  function flash(tl, container, at, o) {
    o = o || {};
    const f = document.createElement("div");
    Object.assign(f.style, { position: "absolute", inset: 0, background: o.color || "#fff", opacity: 0, zIndex: o.z || 4500, pointerEvents: "none" });
    container.appendChild(f);
    tl.set(f, { opacity: o.cap || 0.6 }, at);
    tl.to(f, { opacity: 0, duration: o.dur || 0.12, ease: "power1.out" }, at + 1 / 30);
    return f;
  }

  // ── 集中線：從格子外緣往中心的放射線，中間留空；boil＝兩組線交替（每 2 格換），畫面活著 ──
  function focusLines(tl, svg, w, h, cx, cy, at, o) {
    o = o || {};
    const n = o.n || 64, r0 = o.r0 || Math.min(w, h) * 0.32, R = Math.hypot(w, h), dur = o.dur || 0.9;
    const sets = [0, 1].map((k) => {
      const g = svgEl("g", { opacity: 0 }, svg);
      for (let i = 0; i < n; i++) {
        const a = (i / n) * Math.PI * 2 + k * 0.031 + (((i * 7 + k * 3) % 5) - 2) * 0.012;
        const rin = r0 * (1 + (((i * 13 + k * 5) % 7) / 7) * 0.35), wd = 2 + ((i * 11 + k) % 4) * 1.6;
        // 楔形（外寬內尖），比細線有力
        const a1 = a - wd / R, a2 = a + wd / R;
        svgEl("path", { d: `M${cx + Math.cos(a1) * R},${cy + Math.sin(a1) * R} L${cx + Math.cos(a) * rin},${cy + Math.sin(a) * rin} L${cx + Math.cos(a2) * R},${cy + Math.sin(a2) * R} Z`, fill: o.color || INK }, g);
      }
      return g;
    });
    const step = 2 / 30;
    for (let t = at, i = 0; t < at + dur; t += step, i++) {
      tl.set(sets[i % 2], { opacity: 1 }, t); tl.set(sets[(i + 1) % 2], { opacity: 0 }, t);
    }
    tl.set(sets, { opacity: 0 }, at + dur);
    return sets;
  }

  // ── 衝擊星（字後面的爆炸形）：一格內彈出、稍微過頭再回來 ──
  function impactStar(tl, svg, x, y, r, at, o) {
    o = o || {};
    const n = o.n || 12, P = [];
    for (let i = 0; i < n * 2; i++) {
      const a = (o.rot || -0.2) + (i / (n * 2)) * Math.PI * 2, rr = i % 2 ? r * 0.58 : r * (1 + 0.14 * (((i * 37) % 10) / 10));
      P.push(`${(x + Math.cos(a) * rr).toFixed(1)},${(y + Math.sin(a) * rr).toFixed(1)}`);
    }
    const s = svgEl("polygon", { points: P.join(" "), fill: o.fill || "#fff", stroke: INK, "stroke-width": o.lw || 6, "stroke-linejoin": "round" }, svg);
    tl.fromTo(s, { scale: 0, svgOrigin: `${x} ${y}` }, { scale: 1, svgOrigin: `${x} ${y}`, duration: 0.16, ease: "back.out(2.6)", immediateRender: true }, at);
    return s;
  }

  // ── 衝擊環：從撞點擴散、變細、消失 ──
  function ring(tl, svg, x, y, r1, at, o) {
    o = o || {};
    const c = svgEl("circle", { cx: x, cy: y, r: 1, fill: "none", stroke: o.color || INK, "stroke-width": o.w || 10, opacity: 0 }, svg);
    tl.set(c, { opacity: 1 }, at);
    tl.to(c, { attr: { r: r1, "stroke-width": (o.w || 10) * 0.3 }, opacity: 0, duration: o.dur || 0.35, ease: "power2.out" }, at);
    return c;
  }

  // ── 撞擊短線：撞點周圍一圈短放射線，撞到的下一格出現、0.3 秒內消失 ──
  function impactLines(tl, svg, x, y, r0, r1, at, o) {
    o = o || {};
    const n = o.n || 10, g = svgEl("g", { opacity: 0 }, svg), a0 = o.a0 || 0, span = o.span || Math.PI * 2;
    for (let i = 0; i < n; i++) {
      const a = a0 + (span >= Math.PI * 2 ? i / n : i / Math.max(1, n - 1)) * span;
      svgEl("line", { x1: x + Math.cos(a) * r0, y1: y + Math.sin(a) * r0, x2: x + Math.cos(a) * r1, y2: y + Math.sin(a) * r1,
        stroke: INK, "stroke-width": o.w || 7, "stroke-linecap": "round" }, g);
    }
    tl.set(g, { opacity: 1 }, at + 1 / 30);
    tl.to(g, { opacity: 0, duration: 0.12 }, at + 0.25);
    return g;
  }

  // ── 速度線：移動的東西後面拖幾條線（dir＝前進方向，弧度；x,y＝線的起點附近）。放進會跟著移動的那一層，就會跟著走 ──
  function speedLines(tl, svg, x, y, dir, at, dur, o) {
    o = o || {};
    const n = o.n || 3, len = o.len || 60, gap = o.gap || 16, back = o.back || 40;
    const bx = -Math.cos(dir), by = -Math.sin(dir), nx = -by, ny = bx;
    const sets = [1, 0.8].map((sc) => {
      const g = svgEl("g", { opacity: 0 }, svg);
      for (let i = 0; i < n; i++) {
        const o2 = (i - (n - 1) / 2) * gap, L = len * (1 - i * 0.15) * sc, sx = x + bx * back + nx * o2, sy = y + by * back + ny * o2;
        svgEl("line", { x1: sx, y1: sy, x2: sx + bx * L, y2: sy + by * L, stroke: INK, "stroke-width": o.w || 5, "stroke-linecap": "round" }, g);
      }
      return g;
    });
    for (let t = at, i = 0; t < at + dur; t += 2 / 30, i++) { tl.set(sets[i % 2], { opacity: 1 }, t); tl.set(sets[(i + 1) % 2], { opacity: 0 }, t); }
    tl.set(sets, { opacity: 0 }, at + dur);
    return sets;
  }

  // ── 壓扁拉伸（寫在 act 上；origin＝腳底在 el 裡的座標）──
  //   預備（蹲）：0.9／1.12 兩格 → 起跳拉長 1.15／0.88 → 落地壓扁 0.78／1.2 → 0.18 秒回 1
  function squashJump(act, el, at, origin, o) {
    o = o || {};
    const O = { transformOrigin: origin };
    act.set(el, Object.assign({ scaleY: 0.86, scaleX: 1.12 }, O), at);                       // 預備
    act.set(el, Object.assign({ scaleY: 1.16, scaleX: 0.88 }, O), at + (o.antic || 0.1));    // 起跳拉長
    const land = at + (o.antic || 0.1) + (o.air || 0.42);
    act.set(el, Object.assign({ scaleY: 0.78, scaleX: 1.2 }, O), land);                      // 落地壓扁
    act.to(el, Object.assign({ scaleY: 1, scaleX: 1, duration: 0.18, ease: "power2.out" }, O), land + 1 / 30);
    return land;
  }

  window.EXK_FX = { drive, warp, shake, flash, focusLines, impactStar, ring, impactLines, speedLines, squashJump };
})();
