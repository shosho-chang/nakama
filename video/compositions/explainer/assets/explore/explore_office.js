// NEAT 書房段：A／B 畫法比較（分鏡表 storyboards/neat-office.md）
//   頁面在 #root 設 data-explore-mode="a" | "b"（渲染器會先跑外部 script，不能靠頁面裡的 inline 變數）
//   A：背景＝每格各生一張（不含人）；C＝A 的做法，但背景用 B 的畫面當風格參考生（assets/explore/c/），人偶／腳／手／筆／熱量計＝程式畫、會動
//   B：整格連人一起生，動畫只有運鏡、緩推、文字
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const root = document.getElementById("root");
  const MODE = root.dataset.exploreMode;
  // 圖片路徑寫完整字串（渲染器靠掃字串找素材）
  const IMGS = {
    a: { p1: "assets/explore/a/a1.png", p2: "assets/explore/a/a2.png", p3: "assets/explore/a/a3.png", p4: "assets/explore/a/a4.png", p5: null },
    c: { p1: "assets/explore/c/c1.png", p2: "assets/explore/c/c2.png", p3: "assets/explore/c/c3.png", p4: "assets/explore/c/c4.png", p5: "assets/explore/c/c5.png" },
    b: { p1: "assets/explore/b/b1_hi.png", p2: "assets/explore/b/b2_hi.png", p3: "assets/explore/b/b3_hi.png", p4: "assets/explore/b/b4_hi.png", p5: "assets/explore/b/b5_hi.png" },
  };
  const page = document.createElement("div"); page.id = "page"; root.appendChild(page);
  const tl = gsap.timeline({ paused: true });
  // C 版加動態特效（assets/kit/fx.js）：角色寫在 act（劇情時間，一拍二＋頓格），鏡頭／字卡／特效寫在 tl
  const FX = root.dataset.exploreMode === "c" && window.EXK_FX;
  const act = FX ? gsap.timeline({ paused: true }) : tl;
  window.__timelines = { [root.dataset.compositionId]: tl };
  const LW = 11;                         // 特寫格裡的四肢線寬：四肢永遠是單一條線，放大也不變粗
  const FIG_H = 604;

  // 版面：上排 中景＋特寫；下排 大特寫、遠景、概念格
  const CELLS = {
    p1: { x: 24,   y: 24,  w: 1100, h: 560, img: "a1.png", iw: 1536, ih: 1024 },
    p2: { x: 1144, y: 24,  w: 752,  h: 560, img: "a2.png", iw: 1536, ih: 1024 },
    p3: { x: 24,   y: 604, w: 452,  h: 452, img: "a3.png", iw: 1024, ih: 1024 },
    p4: { x: 496,  y: 604, w: 880,  h: 452, img: "a4.png", iw: 1536, ih: 1024 },
    p5: { x: 1396, y: 604, w: 500,  h: 452, iw: 1024, ih: 1024 },
  };
  // 每組背景量出來的位置（圖座標）：p1 書桌後緣 y、p2 鞋底 y、p4 地板／書桌面／腳底 y、走路起點 x
  const GEO = {
    a: { desk1: 664, shoe2: 860, floor4: 748, deskTop4: 557, feet4: 768, x4: [1180, 330, 820] },
    c: { desk1: 718, shoe2: 860, floor4: 734, deskTop4: 525, feet4: 762, x4: [940, 380, 780] },
  }[MODE];
  const OVER = MODE === "b" ? 1.12 : 1.08;      // B 的圖自己畫了框線，多放大一點把框裁掉
  const cell = {};
  for (const k in CELLS) {
    const c = CELLS[k], d = document.createElement("div"); d.className = "cell";
    Object.assign(d.style, { left: c.x + "px", top: c.y + "px", width: c.w + "px", height: c.h + "px" });
    // stage＝格子內容（震動時晃這層）；d＝外框＋字卡（字卡不跟著晃）
    const stage = document.createElement("div"); stage.className = "scene"; d.appendChild(stage); c.outer = d; c.stage = stage;
    const sc = document.createElement("div"); sc.className = "scene"; stage.appendChild(sc); c.scene = sc;
    const src = IMGS[MODE][k];
    if (src) {
      const img = document.createElement("img"); img.className = "bg"; img.src = src;
      const s = Math.max(c.w * OVER / c.iw, c.h * OVER / c.ih);
      Object.assign(img.style, { width: c.iw * s + "px", height: c.ih * s + "px",
        left: (c.w - c.iw * s) / 2 + "px", top: (c.h - c.ih * s) / 2 + "px" });
      c.s = s; sc.appendChild(img);
      if (MODE === "c" && k === "p5") img.style.transform = "scaleX(-1)";   // 窗戶翻到右邊，左上留給大字
    }
    c.zoom = Math.min(1920 / c.w, 1080 / c.h) * 0.94;
    page.appendChild(d); cell[k] = stage;
  }
  // 圖座標 → 格子座標
  const imgPt = (k, ix, iy) => { const c = CELLS[k]; return [(c.w - c.iw * c.s) / 2 + ix * c.s, (c.h - c.ih * c.s) / 2 + iy * c.s]; };

  function camTo(k, at, dur) {
    const c = CELLS[k], s = c.zoom;
    tl.to(page, { scale: s, x: 960 - (c.x + c.w / 2) * s, y: 540 - (c.y + c.h / 2) * s, duration: dur, ease: "power3.inOut" }, at);
  }
  function drift(k, at, dur) {
    tl.fromTo(CELLS[k].scene, { scale: 1, transformOrigin: "50% 50%" }, { scale: 1.06, duration: dur, ease: "none" }, at);
  }
  function cap(k, html, pos, at) {
    const z = CELLS[k].zoom, c = document.createElement("div"); c.className = "cap"; c.innerHTML = html;
    Object.assign(c.style, { fontSize: 40 / z + "px", borderWidth: 7 / z + "px" });
    CELLS[k].outer.appendChild(c);
    const ww = c.offsetWidth, hh = c.offsetHeight, C = CELLS[k];
    c.style.left = (pos.right != null ? C.w - ww - pos.right : pos.left) + "px";
    c.style.top = (pos.bottom != null ? C.h - hh - pos.bottom : pos.top) + "px";
    tl.fromTo(c, { opacity: 0, x: -16 / z }, { opacity: 1, x: 0, duration: 0.25, ease: "power3.out" }, at);
    return c;
  }
  function bigText(k, html, left, top, size, at) {
    const z = CELLS[k].zoom, b = document.createElement("div"); b.className = "big"; b.innerHTML = html;
    Object.assign(b.style, { left: left + "px", top: top + "px", fontSize: size / z + "px", webkitTextStroke: 16 / z + "px #fff" });
    cell[k].appendChild(b);
    tl.fromTo(b, { opacity: 0, scale: 0.7, transformOrigin: "0 50%" }, { opacity: 1, scale: 1, duration: 0.3, ease: "back.out(1.7)" }, at);
    return b;
  }
  function svgLayer(k, z) {
    const C = CELLS[k], s = document.createElementNS(NS, "svg");
    s.setAttribute("viewBox", `0 0 ${C.w} ${C.h}`); s.setAttribute("class", "ink"); s.style.zIndex = z || 10;
    cell[k].appendChild(s); return s;
  }
  function el(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const a in attrs) e.setAttribute(a, attrs[a]);
    if (parent) parent.appendChild(e); return e;
  }
  const ink = { fill: "none", stroke: "#000", "stroke-width": LW, "stroke-linecap": "round", "stroke-linejoin": "round" };
  // 動作線（短直線，出現後消失）
  function burst(parent, lines, at, every, times) {
    lines.forEach((l) => {
      const e = el("line", Object.assign({ x1: l[0], y1: l[1], x2: l[2], y2: l[3] }, ink, { "stroke-width": LW * 0.7 }), parent);
      gsap.set(e, { opacity: 0 });
      for (let i = 0; i < times; i++) {
        tl.set(e, { opacity: 1 }, at + i * every); tl.set(e, { opacity: 0 }, at + i * every + every * 0.5);
      }
    });
  }

  // ── 時間軸 ──
  const T = { p1: 0, p2: 2.6, p3: 4.6, p4: 6.4, p5: 9.0, out: 11.9, end: 13.6 };
  camTo("p1", 0, 0.001);
  camTo("p2", T.p2, 0.6); camTo("p3", T.p3, 0.6); camTo("p4", T.p4, 0.6); camTo("p5", T.p5, 0.6);
  tl.to(page, { scale: 1, x: 0, y: 0, duration: 1.3, ease: "power3.inOut" }, T.out);
  tl.to({}, { duration: 0.01 }, T.end);

  cap("p1", "有些人整天沒運動，", { left: 18, top: 18 }, 0.3);
  cap("p2", "光是抖腳、", { left: 16, top: 16 }, T.p2 + 0.4);
  cap("p3", "轉筆、", { left: 14, top: 14 }, T.p3 + 0.4);
  cap("p4", "走來走去——", { left: 14, top: 14 }, T.p4 + 0.4);

  if (MODE === "b") {
    drift("p1", 0, T.p2 + 0.6); drift("p2", T.p2, 2.6); drift("p3", T.p3, 2.4); drift("p4", T.p4, 3.2); drift("p5", T.p5, 3.5);
    bigText("p5", "一天 800 大卡", 12, 6, 64, T.p5 + 0.9);
    bigText("p5", "最高 2000！", 12, 52, 84, T.p5 + 1.8);
  } else {
    // 格 1：坐在書桌後，只露上半身（書桌後緣以下裁掉），疲累臉，呼吸起伏
    {
      const C = CELLS.p1, desk = imgPt("p1", 0, GEO.desk1)[1];
      const clip = document.createElement("div");
      Object.assign(clip.style, { position: "absolute", left: 0, top: 0, width: C.w + "px", height: desk + "px", overflow: "hidden", zIndex: 3 });
      cell.p1.appendChild(clip);
      // make 的 y 是腳底（站姿下巴到腳底約 364）；坐著看不到腳，只要讓下巴落在書桌後緣上方 150
      const s = 1.0, chin = desk - 150 * s;
      const f = EXK_FIGURE.make(clip, { scale: s, x: C.w * 0.5, y: chin + 364 * s, face: "tired" });
      act.fromTo(f.svg, { y: 0 }, { y: -5, duration: 0.9, ease: "sine.inOut", yoyo: true, repeat: 2 }, 0.2);
    }
    // 格 2：桌下特寫。特寫＝把真的人偶放大、對準腳（線條等比放大，跟定稿圖一樣粗）；抖腳＝近鏡頭那條腿的關節小幅補間
    {
      const C = CELLS.p2, gy = imgPt("p2", 0, GEO.shoe2)[1], Z = 5.2;
      const wrap = document.createElement("div"); wrap.className = "fig"; cell.p2.appendChild(wrap);
      const f = EXK_FIGURE.make(wrap, { scale: Z, x: 0, y: 0, view: "side", dir: -1, lineWidth: 20 });
      f.set({ vals: { lh: 7, rh: 7 } });                   // 兩腳一前一後，特寫才不會兩隻鞋疊成一塊
      f.aim("fL", C.w * 0.5, gy - 26 * Z);
      for (let i = 0; i < 16; i++) {
        const t = T.p2 + 0.45 + i * 0.11;
        f.tween(act, { lh: 25, lk: -45 }, t, 0.055, "power1.out");
        f.tween(act, { lh: 7, lk: 0 }, t + 0.055, 0.055, "power1.in");
      }
      const s = svgLayer("p2", 4), j = f.joint("fL"), hx = j[0] - 30 * Z - 24, y = gy - 50;   // 前腳鞋尖外側
      burst(s, [[hx, y - 70, hx - 46, y - 92], [hx - 8, y - 30, hx - 60, y - 30], [hx, y + 4, hx - 44, y + 18]], T.p2 + 0.5, 0.22, 8);
      if (FX) EXK_FX.shake(tl, CELLS.p2.stage, T.p2 + 0.45, 7, 0.4);          // 強調（中）：抖腳一開始晃一下
    }
    // 格 3：手的特寫，同樣是放大的人偶對準右手；筆繞手套前端轉
    {
      const C = CELLS.p3, Z = 4.2;
      const wrap = document.createElement("div"); wrap.className = "fig"; cell.p3.appendChild(wrap);
      const f = EXK_FIGURE.make(wrap, { scale: Z, x: 0, y: 0, lineWidth: 20 });
      f.set({ vals: { rs: 62, re: -48, aR: 1 } });
      f.aim("hR", C.w * 0.5, C.h * 0.58);
      const tip = f.joint("hRtip"), s = svgLayer("p3", 4);
      const pg = el("g", { transform: `translate(${tip[0]} ${tip[1]})` }, s), pr = el("g", {}, pg);
      el("rect", Object.assign({}, ink, { fill: "#fff", "stroke-width": 9, x: -110, y: -11, width: 220, height: 22, rx: 11 }), pr);
      el("path", Object.assign({}, ink, { fill: "#000", "stroke-width": 9, d: "M110,-8 L140,0 L110,8 Z" }), pr);
      el("rect", { x: -110, y: -7, width: 26, height: 14, fill: "var(--orange)" }, pr);
      act.fromTo(pr, { rotation: -20, svgOrigin: "0 0" }, { rotation: 700, svgOrigin: "0 0", duration: 1.5, ease: "power1.inOut" }, T.p3 + 0.4);
      const pv = tip, arc = (r, a0, a1) => `M${pv[0] + r * Math.cos(a0)},${pv[1] + r * Math.sin(a0)} A${r},${r} 0 0 1 ${pv[0] + r * Math.cos(a1)},${pv[1] + r * Math.sin(a1)}`;
      [arc(160, -2.6, -1.6), arc(160, 0.4, 1.4)].forEach((d, i) => {
        const a = el("path", Object.assign({}, ink, { d, "stroke-width": LW * 0.7 }), s);
        gsap.set(a, { opacity: 0 });
        tl.to(a, { opacity: 1, duration: 0.15 }, T.p3 + 0.6 + i * 0.1); tl.to(a, { opacity: 0, duration: 0.3 }, T.p3 + 1.8);
      });
    }
    // 格 4：側面走路，往左走 → 轉身 → 往右走
    {
      const C = CELLS.p4, feetY = imgPt("p4", 0, GEO.feet4)[1];
      const sImg = (GEO.floor4 - GEO.deskTop4) * 170 / 75 / FIG_H;          // 書桌高 75cm、人 170cm → 圖上身高
      const sc = sImg * C.s;
      const wrap = document.createElement("div"); wrap.className = "fig"; cell.p4.appendChild(wrap);
      const f = EXK_FIGURE.make(wrap, { scale: sc, x: 0, y: feetY, view: "side", dir: -1 });
      const xs = GEO.x4.map((x) => imgPt("p4", x, 0)[0]);
      const walk = (at, dur, x0, x1) => {
        act.fromTo(wrap, { x: x0 }, { x: x1, duration: dur, ease: "none", immediateRender: false }, at);
        for (let t = at, i = 0; t < at + dur - 0.05; t += 0.28, i++) { f.pose(act, i % 2 ? "sideWalkB" : "sideWalkA", t, 0.14); f.jump(act, t, 8); }
        f.pose(act, "stand", at + dur, 0.15);
      };
      gsap.set(wrap, { x: xs[0] });
      walk(T.p4 + 0.4, 1.4, xs[0], xs[1]);
      f.view(act, "front", T.p4 + 1.9); f.view(act, "side", T.p4 + 2.05, { dir: 1 });
      walk(T.p4 + 2.1, 0.9, xs[1], xs[2]);
    }
    // 格 5：熱量計 0 → 800 → 2000；人偶驚訝
    {
      const C = CELLS.p5, s = svgLayer("p5", 2);
      const tx = 400, top = 130, bot = 350, tw = 50, br = 46;
      el("rect", Object.assign({}, ink, { fill: "#fff", x: tx - tw / 2, y: top, width: tw, height: bot - top + 30, rx: tw / 2 }), s);
      el("circle", Object.assign({}, ink, { fill: "#fff", cx: tx, cy: bot + br * 0.9, r: br }), s);
      el("circle", { cx: tx, cy: bot + br * 0.9, r: br - LW * 0.9, fill: "var(--orange)" }, s);
      const fill = el("rect", { x: tx - tw / 2 + LW * 0.9, width: tw - LW * 1.8, y: bot + 20, height: 0, fill: "var(--orange)" }, s);
      for (let i = 0; i <= 10; i++) { const y = bot - (bot - top - 20) * i / 10; el("line", Object.assign({}, ink, { x1: tx + tw / 2 + 6, y1: y, x2: tx + tw / 2 + (i % 5 ? 22 : 40), y2: y, "stroke-width": LW * 0.6 }), s); }
      const lvl = (p) => ({ y: bot + 20 - (bot - top) * p, height: (bot - top) * p });
      act.to(fill, { attr: lvl(0.4), duration: 0.6, ease: "power2.out" }, T.p5 + 0.6);
      const wrap = document.createElement("div"); wrap.className = "fig"; cell.p5.appendChild(wrap);
      const f = EXK_FIGURE.make(wrap, { scale: 0.55, x: 170, y: C.h + 30 });
      bigText("p5", "一天 800 大卡", 16, 14, 64, T.p5 + 0.9);
      const HIT = T.p5 + 1.95;                                       // 熱量計衝到頂的那一格（強調：強）
      if (!FX) {
        tl.to(fill, { attr: lvl(1.0), duration: 0.45, ease: "power3.in" }, T.p5 + 1.6);
        f.face(tl, "surprised", T.p5 + 1.75); f.pose(tl, "armsOut", T.p5 + 1.75, 0.15); f.jump(tl, T.p5 + 1.75, 40);
        bigText("p5", "最高 2000！", 16, 62, 84, T.p5 + 1.85);
      } else {
        act.to(fill, { attr: lvl(1.0), duration: 0.35, ease: "power3.in" }, HIT - 0.35);
        // 人偶：預備（蹲）→ 跳起（驚訝、張手）→ 落地壓扁
        f.face(act, "surprised", HIT - 0.1); f.pose(act, "armsOut", HIT - 0.1, 0.07);   // 撞到那格（頓格期間）就要是驚訝臉
        EXK_FX.squashJump(act, wrap, HIT - 0.1, "170px " + (C.h + 30) + "px", { antic: 0.1, air: 0.42 });
        f.jump(act, HIT, 40);
        // 撞到頂的那一格：五件事同一格——白閃、震動、集中線、衝擊環＋撞擊線、字彈出（後面墊衝擊星）
        const fxl = svgLayer("p5", 4500);
        EXK_FX.flash(tl, cell.p5, HIT, { cap: 0.55 });
        EXK_FX.shake(tl, CELLS.p5.stage, HIT, 16, 0.45);
        EXK_FX.focusLines(tl, svgLayer("p5", 1), C.w, C.h, 300, 210, HIT, { r0: 190, dur: 1.2 });
        EXK_FX.ring(tl, fxl, tx, top, 110, HIT, { w: 9 });
        EXK_FX.impactLines(tl, fxl, tx, top, 40, 95, HIT, { n: 7, a0: -Math.PI * 0.95, span: Math.PI * 0.9, w: 6 });
        EXK_FX.impactStar(tl, svgLayer("p5", 2), 126, 84, 92, HIT, { lw: 5 });   // 墊在人偶後面、大字前面
        bigText("p5", "最高 2000！", 16, 62, 84, HIT);
        act.to({}, { duration: 0.01 }, T.end);
        EXK_FX.drive(tl, act, { start: 0, end: T.end, stops: [[HIT, 0.2]], twos: true });
      }
    }
  }

  const q = new URLSearchParams(location.search);
  if (q.get("t")) tl.seek(+q.get("t")); else if (q.get("play") === "1") tl.play(0); else tl.seek(0);
})();
