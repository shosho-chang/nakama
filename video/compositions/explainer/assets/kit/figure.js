// 解釋動畫的主角：修修人偶（2026-11-19 依定稿角色設計重做）
//   設計來源：assets/character/turnaround_v1.png（五面）、expressions_v1.png（表情）、README.md（每項決定）
//   頭：正面預設＝品牌 logo 原檔（assets/brand/shosho_face_filled.png，不重畫）；
//       其他表情與側面／背面＝從定稿圖切出的頭部素材（assets/heads/，tools/make_head_sprites.py 產生）
//   身體：程式畫（關節可轉）。A3 T 恤（圓肩、袖子跟手臂走）、雙線管狀手臂、單線腿、H3 手套、圓頭鞋、下擺品牌碎片
//
//   const f = EXK_FIGURE.make(parent, { scale: 1, x, y })   // x,y＝兩腳中間落地點；全身高約 600×scale
//   f.pose(tl, "armsUp", at, dur)      換姿勢（補間）；f.jump(tl, at, height)
//   f.view(tl, "side", at, { dir })    換角度：front / q3 / side / q3back / back；dir：-1 面向左、1 面向右
//   f.face(tl, "laugh", at)            換表情（正面才有）：default laugh surprised think confused tired angry nervous determined
//   f.set({ view, dir, face, pose })   立即設定（不經時間軸）
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const C = { K: "#000", W: "#fff" };
  const SW = 10;                          // 線寬
  const HEAD_H = 240;                      // 頭頂到下巴
  const CHIN_Y = 4;                        // 下巴落點（稍微壓住領口）
  // 身體尺寸（scale 1，px；原點＝身體中線、下巴高度）——量自 turnaround_v1.png 正面 ×0.827
  const B = { shX: 50, shY: 30, ua: 60, fa: 58, hipX: 29, hipY: 168, th: 86, sn: 84, shoe: 26,
              sleeve: 40 };
  const BASE = "assets/";
  // 頭部素材：w,h＝圖寬高；chinX/chinY＝下巴最低點；top＝頭頂；dir＝這張圖面向哪邊（-1 左、1 右、0 正面）
  const LOGO = { src: BASE + "brand/shosho_face_filled.png", w: 978, h: 1377, top: 0, chinX: 632.3, chinY: 1376, dir: 0 };
  const HEADS = {
    q3:     { src: "head_q3.png",     w: 225, h: 292, top: 0, chinX: 129.1, chinY: 291, dir: 1 },
    side:   { src: "head_side.png",   w: 238, h: 301, top: 0, chinX: 86.0,  chinY: 300, dir: -1 },
    q3back: { src: "head_q3back.png", w: 223, h: 297, top: 0, chinX: 101.7, chinY: 296, dir: -1 },
    back:   { src: "head_back.png",   w: 253, h: 298, top: 0, chinX: 127.8, chinY: 297, dir: 0 },
  };
  const FACES = {
    laugh:      { src: "head_x_laugh.png",      w: 195, h: 273, top: 0, chinX: 114.0, chinY: 268 },
    surprised:  { src: "head_x_surprised.png",  w: 195, h: 273, top: 0, chinX: 113.0, chinY: 268 },
    think:      { src: "head_x_think.png",      w: 196, h: 314, top: 0, chinX: 127.3, chinY: 254 },
    confused:   { src: "head_x_confused.png",   w: 196, h: 272, top: 0, chinX: 113.0, chinY: 267 },
    tired:      { src: "head_x_tired.png",      w: 196, h: 272, top: 0, chinX: 114.0, chinY: 267 },
    angry:      { src: "head_x_angry.png",      w: 196, h: 271, top: 0, chinX: 114.7, chinY: 266 },
    nervous:    { src: "head_x_nervous.png",    w: 256, h: 271, top: 0, chinX: 113.3, chinY: 266 },
    determined: { src: "head_x_determined.png", w: 196, h: 271, top: 0, chinX: 113.7, chinY: 266 },
  };
  const SHARDS = { src: BASE + "character/shards_orange.png", w: 1200, h: 506, scale: 0.2 };
  // 角度（度）：0＝朝下；兩邊都是「往外張為正」；肘、膝是相對上一節。aL/aR＝那隻手臂畫在身體前面（1）或後面（0）
  const POSES = {
    stand:  { ls: 10,  le: 0,  rs: 10,  re: 0,  lh: 2,  lk: 0,   rh: 2,  rk: 0,   aL: 0, aR: 0 },
    armsUp: { ls: 160, le: 4,  rs: 160, re: 4,  lh: 2,  lk: 0,   rh: 2,  rk: 0,   aL: 0, aR: 0 },
    armsOut:{ ls: 90,  le: 0,  rs: 90,  re: 0,  lh: 2,  lk: 0,   rh: 2,  rk: 0,   aL: 0, aR: 0 },
    squat:  { ls: 80,  le: 0,  rs: 80,  re: 0,  lh: 62, lk: -62, rh: 62, rk: -62, aL: 0, aR: 0 },
    star:   { ls: 135, le: 0,  rs: 135, re: 0,  lh: 26, lk: 0,   rh: 26, rk: 0,   aL: 0, aR: 0 },
    wave:   { ls: 10,  le: 0,  rs: 125, re: 45, lh: 2,  lk: 0,   rh: 2,  rk: 0,   aL: 0, aR: 0 },
    // 跑步（兩拍交替）：屈膝那條腿的對側手往前擺——正面看是手肘彎、手套抬到胸前、畫在身體前面；
    // 另一隻手往後擺——幾乎伸直、略往外
    jogA:   { ls: 34,  le: -114, rs: 18, re: -6,   lh: 2,  lk: 0,   rh: 22, rk: -48, aL: 1, aR: 0 },
    jogB:   { ls: 18,  le: -6,   rs: 34, re: -114, lh: 22, lk: -48, rh: 2,  rk: 0,  aL: 0, aR: 1 },
    // 托下巴想事情（配 face "think"）：畫面左邊那隻手（修修的右手）彎到下巴
    think:  { ls: -12, le: -158, rs: 10, re: 0,  lh: 2,  lk: 0,   rh: 2,  rk: 0,   aL: 1, aR: 0 },
  };
  // 各角度的身體參數：w＝軀幹寬倍率、legX＝兩腿間距倍率、arms＝看得到幾隻手臂、shoe＝鞋型、off＝軀幹往後偏
  const VIEWS = {
    front:  { w: 1,    legX: 1,    arms: 2, shoe: "front", off: 0 },
    back:   { w: 1,    legX: 1,    arms: 2, shoe: "back",  off: 0 },
    q3:     { w: 0.86, legX: 0.75, arms: 2, shoe: "q3",    off: 0 },
    q3back: { w: 0.86, legX: 0.75, arms: 2, shoe: "q3",    off: 0 },
    side:   { w: 0.62, legX: 0.12, arms: 1, shoe: "side",  off: 6 },
  };
  const rad = (d) => (d * Math.PI) / 180;
  const vec = (deg, len) => [Math.sin(rad(deg)) * len, Math.cos(rad(deg)) * len];
  const add = (p, v) => [p[0] + v[0], p[1] + v[1]];
  const f1 = (n) => n.toFixed(1);
  let uid = 0;

  function el(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  const pts = (arr) => arr.map((p) => f1(p[0]) + "," + f1(p[1])).join(" ");

  function solve(st, V) {
    const sx = B.shX * V.w, hx = B.hipX * V.legX;
    const shL = [-sx, B.shY], shR = [sx, B.shY];
    const eL = add(shL, vec(-st.ls, B.ua)), hL = add(eL, vec(-(st.ls + st.le), B.fa));
    const eR = add(shR, vec(st.rs, B.ua)), hR = add(eR, vec(st.rs + st.re, B.fa));
    const hipL = [-hx, B.hipY], hipR = [hx, B.hipY];
    const kL = add(hipL, vec(-st.lh, B.th)), fL = add(kL, vec(-(st.lh + st.lk), B.sn));
    const kR = add(hipR, vec(st.rh, B.th)), fR = add(kR, vec(st.rh + st.rk, B.sn));
    const ground = Math.max(fL[1], fR[1]) + B.shoe;
    return { shL, shR, eL, hL, eR, hR, hipL, hipR, kL, fL, kR, fR, ground };
  }

  // T 恤（不含袖子）：圓肩、側邊微微外擴、下擺弧
  function torsoPath(V) {
    const w = V.w, o = V.off;
    const P = (x, y) => f1(x * w + o) + "," + f1(y);
    return `M${P(-34, 6)} C${P(-48, 6)} ${P(-57, 14)} ${P(-57, 30)} L${P(-58, 70)} L${P(-61, 154)}` +
      ` C${P(-40, 172)} ${P(40, 172)} ${P(61, 154)} L${P(58, 70)} L${P(57, 30)}` +
      ` C${P(57, 14)} ${P(48, 6)} ${P(34, 6)} C${P(16, 12)} ${P(-16, 12)} ${P(-34, 6)} Z`;
  }

  // 一節手臂：跟腿一樣是單一條黑線（定稿：四肢單線，不管什麼姿勢都不變成雙線）
  function tube(g, a, b, c) {
    el("polyline", { points: pts([a, b, c]), fill: "none", stroke: C.K, "stroke-width": SW, "stroke-linejoin": "round", "stroke-linecap": "round" }, g);
  }
  // 袖子：從肩關節沿上臂方向；頂端是圓肩（半圓），袖口平切。白底黑框
  function sleeve(g, sh, el2) {
    const dx = el2[0] - sh[0], dy = el2[1] - sh[1], L = Math.hypot(dx, dy) || 1;
    const ux = dx / L, uy = dy / L, nx = -uy, ny = ux;
    const w = 22, top = [sh[0] - ux * 2, sh[1] - uy * 2], hem = [sh[0] + ux * B.sleeve, sh[1] + uy * B.sleeve];
    const a = [top[0] + nx * w, top[1] + ny * w], b = [top[0] - nx * w, top[1] - ny * w];
    const c = [hem[0] - nx * (w - 1), hem[1] - ny * (w - 1)], d = [hem[0] + nx * (w - 1), hem[1] + ny * (w - 1)];
    const path = `M${f1(a[0])},${f1(a[1])} A${w},${w} 0 0 ${1} ${f1(b[0])},${f1(b[1])} L${f1(c[0])},${f1(c[1])} L${f1(d[0])},${f1(d[1])} Z`;
    el("path", { d: path, fill: C.W, stroke: C.K, "stroke-width": SW, "stroke-linejoin": "round" }, g);
  }
  // H3 手套：封閉圓弧外框＋短圓拇指；inward＝拇指朝身體中線那側
  function mitten(g, e, h, inward) {
    const dx = h[0] - e[0], dy = h[1] - e[1], L = Math.hypot(dx, dy) || 1;
    const ux = dx / L, uy = dy / L;
    const ang = (Math.atan2(uy, ux) * 180) / Math.PI - 90;     // 讓 ellipse 的 y 軸沿著前臂
    const nx = -uy * inward, ny = ux * inward;
    const body = [h[0] + ux * 15, h[1] + uy * 15];
    const th = [h[0] + ux * 8 + nx * 13, h[1] + uy * 8 + ny * 13];
    const st = { fill: C.W, stroke: C.K, "stroke-width": SW };
    el("ellipse", Object.assign({ cx: f1(th[0]), cy: f1(th[1]), rx: 6.5, ry: 9, transform: `rotate(${f1(ang + 25 * inward)} ${f1(th[0])} ${f1(th[1])})` }, st), g);
    el("ellipse", Object.assign({ cx: f1(body[0]), cy: f1(body[1]), rx: 14, ry: 17.5, transform: `rotate(${f1(ang)} ${f1(body[0])} ${f1(body[1])})` }, st), g);
  }
  // 鞋：正面／背面＝圓頂；側面＝鞋尖朝前（-x）；q3＝圓頂往前偏
  function shoe(g, f, kind, out) {
    const x = f[0], y = f[1], gy = y + B.shoe;
    let d;
    if (kind === "side") {
      d = `M${f1(x + 12)},${f1(gy)} L${f1(x + 12)},${f1(gy - 18)} C${f1(x + 2)},${f1(gy - 28)} ${f1(x - 22)},${f1(gy - 24)} ${f1(x - 30)},${f1(gy - 8)} L${f1(x - 30)},${f1(gy)} Z`;
    } else {
      const cx = kind === "q3" ? x + 7 : x + out * 7, hw = kind === "q3" ? 21 : 22;
      d = `M${f1(cx - hw)},${f1(gy)} C${f1(cx - hw)},${f1(gy - 32)} ${f1(cx + hw)},${f1(gy - 32)} ${f1(cx + hw)},${f1(gy)} Z`;
    }
    el("path", { d, fill: C.W, stroke: C.K, "stroke-width": SW, "stroke-linejoin": "round" }, g);
  }

  function make(parent, opts) {
    const sc = opts.scale || 1;
    const svg = el("svg", { width: 700 * sc, height: 900 * sc, viewBox: "-350 -620 700 900", overflow: "visible" });
    svg.style.position = "absolute";
    svg.style.left = (opts.x - 350 * sc) + "px";
    svg.style.top = (opts.y - 620 * sc) + "px";
    parent.appendChild(svg);
    const id = "exkfig" + (++uid);
    const defs = el("defs", {}, svg);
    const clip = el("clipPath", { id: id + "c" }, defs);
    const clipPathEl = el("path", {}, clip);
    const root = el("g", {}, svg);
    const flipG = el("g", {}, root);
    const gBackArm = el("g", {}, flipG), gLegs = el("g", {}, flipG), gSleeveB = el("g", {}, flipG), gTorso = el("g", {}, flipG),
      gFrontArm = el("g", {}, flipG), gHead = el("g", {}, flipG);
    const head = el("image", {}, gHead);

    const vals = Object.assign({ y: 0 }, POSES.stand);
    const st = {};
    const disc = { view: opts.view || "front", dir: opts.dir || -1, face: opts.face || "default" };
    let ready = false;
    Object.keys(vals).forEach((k) => Object.defineProperty(st, k, {
      get: () => vals[k], set: (v) => { vals[k] = v; if (ready) draw(); }, enumerable: true,
    }));
    Object.keys(disc).forEach((k) => Object.defineProperty(st, k, {
      get: () => disc[k], set: (v) => { disc[k] = v; if (ready) draw(); }, enumerable: true,
    }));

    function headSpec() {
      if (disc.view === "front") {
        if (disc.face === "default" || !FACES[disc.face]) return LOGO;
        return Object.assign({ dir: 0 }, FACES[disc.face], { src: BASE + "heads/" + FACES[disc.face].src });
      }
      const h = HEADS[disc.view];
      return Object.assign({}, h, { src: BASE + "heads/" + h.src });
    }

    function draw() {
      const V = VIEWS[disc.view] || VIEWS.front;
      const P = solve(st, V);
      const oy = -P.ground + st.y;            // 腳落在 y=0（往上跳時 st.y 為負）
      root.setAttribute("transform", `translate(0 ${f1(oy)})`);
      // 側面類的圖本來就有方向，要的方向不同就整個鏡像（正面／背面不鏡像：logo 不能翻）
      const H = headSpec();
      const flip = H.dir !== 0 && H.dir !== disc.dir ? -1 : 1;
      flipG.setAttribute("transform", flip < 0 ? "scale(-1 1)" : "");
      [gBackArm, gLegs, gSleeveB, gTorso, gFrontArm].forEach((g) => (g.innerHTML = ""));
      // 腿＋鞋
      const leg = { fill: "none", stroke: C.K, "stroke-width": SW, "stroke-linecap": "round", "stroke-linejoin": "round" };
      const legsArr = V.shoe === "side" ? [[P.hipR, P.kR, P.fR, 1], [P.hipL, P.kL, P.fL, -1]] : [[P.hipL, P.kL, P.fL, -1], [P.hipR, P.kR, P.fR, 1]];
      legsArr.forEach(([h, k, f, out]) => {
        el("polyline", Object.assign({ points: pts([h, k, f]) }, leg), gLegs);
        shoe(gLegs, f, V.shoe, out);
      });
      // 手臂：前或後由姿勢決定；側面只畫靠近鏡頭那隻（另一隻在身體後面）
      const arms = [
        { s: P.shL, e: P.eL, h: P.hL, inward: -1, front: st.aL > 0.5 },
        { s: P.shR, e: P.eR, h: P.hR, inward: 1, front: st.aR > 0.5 },
      ];
      if (V.arms === 1) { arms[0].s = arms[1].s = [V.off, B.shY]; }
      arms.forEach((a, i) => {
        const g = a.front ? gFrontArm : gBackArm;
        if (V.arms === 1 && i === 0 && !a.front) {    // 側面：遠的那隻手藏在身體後
          tube(gBackArm, a.s, a.e, a.h); mitten(gBackArm, a.e, a.h, a.inward); return;
        }
        const fr = a.front || V.arms === 1;             // 側面：靠近鏡頭那隻手整隻在身體前面
        const gA = fr ? gFrontArm : g, gS = fr ? gFrontArm : gSleeveB;
        // 順序：上臂 → 袖子 → 前臂＋手套（手肘彎回來時，前臂要壓在袖子上面）
        tube(gA, a.s, a.e, a.e);
        sleeve(gS, a.s, a.e);
        tube(gS, a.e, a.e, a.h);
        mitten(gS, a.e, a.h, a.inward);
      });
      // T 恤＋下擺碎片（品牌 Motif A/B 原圖裁切，只在下擺）
      const tp = torsoPath(V);
      clipPathEl.setAttribute("d", tp);
      el("path", { d: tp, fill: C.W }, gTorso);
      const sw = SHARDS.w * SHARDS.scale, sh = SHARDS.h * SHARDS.scale;
      el("image", { href: SHARDS.src, x: f1(-sw / 2 + 20), y: 92, width: f1(sw), height: f1(sh), "clip-path": `url(#${id}c)` }, gTorso);
      el("path", { d: tp, fill: "none", stroke: C.K, "stroke-width": SW, "stroke-linejoin": "round" }, gTorso);
      // 前面的手臂畫在 T 恤之後（上面）
      // 頭：下巴對到身體中線、CHIN_Y；頭頂到下巴縮成 HEAD_H
      const k = HEAD_H / (H.chinY - H.top);
      head.setAttribute("href", H.src);
      head.setAttribute("width", f1(H.w * k)); head.setAttribute("height", f1(H.h * k));
      head.setAttribute("x", f1(-H.chinX * k + (V.arms === 1 ? -V.off : 0)));
      head.setAttribute("y", f1(CHIN_Y - H.chinY * k));
    }
    draw();
    ready = true;

    const api = {
      svg,
      pose(tl, name, at, dur) {
        tl.to(st, Object.assign({}, POSES[name], { duration: dur || 0.35, ease: "power2.inOut", lazy: false }), at);
      },
      jump(tl, at, h) {
        tl.to(st, { y: -(h || 120), duration: 0.22, ease: "power2.out", lazy: false }, at);
        tl.to(st, { y: 0, duration: 0.2, ease: "power2.in", lazy: false }, at + 0.22);
      },
      view(tl, name, at, o) {
        tl.set(st, Object.assign({ view: name }, o && o.dir ? { dir: o.dir } : {}), at);
      },
      face(tl, name, at) { tl.set(st, { face: name }, at); },
      set(o) {
        if (o.pose) Object.assign(st, POSES[o.pose]);
        ["view", "dir", "face"].forEach((k) => { if (o[k] !== undefined) st[k] = o[k]; });
      },
    };
    if (opts.pose) api.set({ pose: opts.pose });
    return api;
  }

  window.EXK_FIGURE = { make, POSES: Object.keys(POSES), VIEWS: Object.keys(VIEWS), FACES: ["default"].concat(Object.keys(FACES)) };
})();
