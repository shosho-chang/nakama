// 解釋動畫的像素角色與小圖示（修修 2026-11-17 選定 A：程式畫的像素角色）。
// 每張圖是一組字串：一個字元＝一格，「.」是透明；顏色只用品牌色＋膚色（PAL）。
// 角色有「換色版本」（variants：例如頭髮變灰），只換調色盤，不重畫。
//
//   EXK_SPRITES.draw(name, frame, px, variant)  → 一張 <svg>（crispEdges，放大不糊）
//   EXK_SPRITES.hero(ctx, opts)                  → 角色積木（原地走＋動作），見下方
(function () {
  // H＝頭髮（換色版本用），K＝黑，O＝橘，W＝白，G＝深灰（褲子），S＝膚色
  const PAL = { H: "#000", K: "#000", O: "#f57331", W: "#fff", G: "#4d4a49", S: "#f1c7a1" };
  const VARIANTS = {
    shosho: { old: { H: "#9a9893" } }, // 頭髮變灰（「老了」）
  };
  const S = {
    // 像素版修修：黑短髮、黑 T 胸口橘色字牌、深灰褲。兩格走路。
    shosho: [
      [
        "....HHHHHHHH....",
        "...HHHHHHHHHH...",
        "..HHHHHHHHHHHH..",
        "..HHSSSSSSSSHH..",
        "..HSSSSSSSSSSH..",
        "..SSKKSSSSKKSS..",
        "..SSSSSSSSSSSS..",
        "..SSSSSSSSSSSS..",
        "...SSSSKKSSSS...",
        "....SSSSSSSS....",
        "..KKKKKKKKKKKK..",
        ".KKKKKKKKKKKKKK.",
        ".KKKOOOOOOOKKKK.",
        "SKKKOOOOOOOKKKKS",
        "SKKKOOOOOOOKKKKS",
        "SKKKKKKKKKKKKKKS",
        "..KKKKKKKKKKKK..",
        "..GGGGGGGGGGGG..",
        "..GGGGG..GGGGG..",
        "..GGGG....GGGG..",
        "..GGGG....GGGG..",
        ".KKKKK....KKKKK.",
      ],
      [
        "................",
        "....HHHHHHHH....",
        "...HHHHHHHHHH...",
        "..HHHHHHHHHHHH..",
        "..HHSSSSSSSSHH..",
        "..HSSSSSSSSSSH..",
        "..SSKKSSSSKKSS..",
        "..SSSSSSSSSSSS..",
        "..SSSSSSSSSSSS..",
        "...SSSSKKSSSS...",
        "....SSSSSSSS....",
        "..KKKKKKKKKKKK..",
        ".KKKKKKKKKKKKKK.",
        "SKKKOOOOOOOKKKKS",
        "SKKKOOOOOOOKKKKS",
        "SKKKOOOOOOOKKKKS",
        "..KKKKKKKKKKKK..",
        "...GGGGGGGGGG...",
        "....GGGGGGGG....",
        "....GGG..GGG....",
        "....GGG..GGG....",
        "...KKKK..KKKK...",
      ],
    ],
    coin: [[
      "..OOO..",
      ".OOOOO.",
      "OOOKOOO",
      "OOOKOOO",
      "OOOKOOO",
      ".OOOOO.",
      "..OOO..",
    ]],
    star: [[
      "...O...",
      "...O...",
      "OOOOOOO",
      ".OOOOO.",
      "..OOO..",
      ".OO.OO.",
      "OO...OO",
    ]],
    book: [[
      "KKKKKKK",
      "KWWKWWK",
      "KWWKWWK",
      "KWWKWWK",
      "KWWKWWK",
      "KKKKKKK",
    ]],
    dumbbell: [[
      "KK...KK",
      "KKKKKKK",
      "KKKKKKK",
      "KK...KK",
    ]],
    heart: [[
      ".OO.OO.",
      "OOOOOOO",
      "OOOOOOO",
      ".OOOOO.",
      "..OOO..",
      "...O...",
    ]],
    cart: [[
      "KK.......",
      ".K.......",
      ".KKKKKKKK",
      ".KOOOOOOK",
      ".KOOOOOK.",
      ".KKKKKKK.",
      "..K...K..",
      ".KKK.KKK.",
    ]],
    bowl: [[
      "......K..",
      ".....K...",
      "....K..K.",
      "...K..K..",
      "KKKKKKKKK",
      "KOOOOOOOK",
      ".KOOOOOK.",
      "..KKKKK..",
    ]],
    plane: [[
      "....K....",
      "....KK...",
      "K...KKK..",
      "KKKKKKKKK",
      "KK..KKK..",
      "....KK...",
      "....K....",
    ]],
    house: [[
      "....O....",
      "...OOO...",
      "..OOOOO..",
      ".OOOOOOO.",
      "OOOOOOOOO",
      ".KKKKKKK.",
      ".KWKKKKK.",
      ".KKKK.KK.",
      ".KKKK.KK.",
    ]],
    briefcase: [[
      "...KKK...",
      "...K.K...",
      "KKKKKKKKK",
      "KOOOOOOOK",
      "KKKKKKKKK",
      "KOOOOOOOK",
      "KOOOOOOOK",
      "KKKKKKKKK",
    ]],
    question: [[
      ".KKKKK.",
      "KK...KK",
      ".....KK",
      "...KKK.",
      "...KK..",
      ".......",
      "...KK..",
    ]],
  };

  function draw(name, frame, px, variant) {
    const rows = S[name][frame || 0];
    const pal = Object.assign({}, PAL, (VARIANTS[name] || {})[variant] || {});
    const w = rows[0].length, h = rows.length;
    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    svg.setAttribute("width", w * px);
    svg.setAttribute("height", h * px);
    svg.setAttribute("shape-rendering", "crispEdges");
    rows.forEach((row, y) => {
      for (let x = 0; x < row.length; x++) {
        const c = pal[row[x]];
        if (!c) continue;
        const r = document.createElementNS(NS, "rect");
        r.setAttribute("x", x); r.setAttribute("y", y);
        r.setAttribute("width", 1); r.setAttribute("height", 1);
        r.setAttribute("fill", c);
        svg.appendChild(r);
      }
    });
    return svg;
  }

  // 角色積木。opts：
  //   sprite（角色名）、left/top（在內容區裡的位置，px）、px（一格幾 px，預設 14）
  //   enter_at（從左邊走進來的秒數）、step（走路換格的間隔秒數，預設 0.28）
  // 回傳的動作（全部用秒數排在 ctx.tl 上，跟字幕對齊）：
  //   pop(icon, at)      頭上冒出小圖示＋「+」，角色小跳一下（「得到／加上了什麼」）
  //   hop(at)            小跳一下（強調）
  //   variant(name, at)  換成換色版本（例：old＝頭髮變灰）
  function hero(ctx, opts) {
    const { tl, stage } = ctx;
    const name = opts.sprite;
    const PX = opts.px || 14, STEP = opts.step || 0.28;
    const box = EXK.el("div", "exk-hero", stage);
    box.style.left = (opts.left ?? 0) + "px";
    box.style.top = (opts.top ?? 0) + "px";
    const pos = { left: opts.left ?? 0, top: opts.top ?? 0 };
    const body = EXK.el("div", "exk-hero-body", box);
    const n = S[name].length;
    const variants = [null].concat(Object.keys(VARIANTS[name] || {}));
    const sets = {}; // variant → [frame svg…]
    variants.forEach((v) => {
      sets[v] = [];
      for (let i = 0; i < n; i++) sets[v].push(body.appendChild(draw(name, i, PX, v)));
    });
    const w = S[name][0][0].length * PX, h = S[name][0].length * PX;
    body.style.width = w + "px";
    body.style.height = h + "px";
    if (opts.ground !== false) {
      const ground = EXK.el("div", "exk-hero-ground", box);
      ground.style.width = w + 2 * PX * 4 + "px";
      ground.style.left = -PX * 4 + "px";
      ground.style.top = h + PX + "px";
    }

    // 換色版本的切換點（照時間排）；走路每一步只顯示「當下版本」的那一格
    const switches = [];
    const current = (t) => switches.filter((s) => s.at <= t).reduce((v, s) => s.name, null);
    const t0 = opts.enter_at ?? 0.5;

    tl.fromTo(box, { opacity: 0 }, { opacity: 1, duration: 0.01 }, t0);
    tl.fromTo(box, { x: -(opts.left ?? 0) - w - 200 }, { x: 0, duration: 0.7, ease: "power2.out" }, t0);

    const api = {
      el: box,
      pop(icon, at) {
        const pop = EXK.el("div", "exk-hero-pop", box);
        pop.style.left = w / 2 + "px";
        pop.style.top = -PX * 9 + "px";
        pop.appendChild(draw(icon, 0, opts.pop_px || Math.round(PX * 0.75)));
        EXK.el("div", "exk-hero-plus", pop, "+");
        tl.fromTo(pop, { opacity: 0, xPercent: -50, y: 30, scale: 0.6 },
          { opacity: 1, y: 0, scale: 1, duration: 0.28, ease: EXK.LAND }, at);
        tl.to(pop, { y: -50, opacity: 0, duration: 0.3, ease: EXK.EXIT }, at + 0.85);
        api.hop(at);
      },
      hop(at) {
        tl.to(body, { y: -PX * 2.5, duration: 0.14, ease: "power2.out" }, at);
        tl.to(body, { y: 0, duration: 0.18, ease: "bounce.out" }, at + 0.14);
      },
      variant(vname, at) { switches.push({ name: vname, at }); },
      // 跳到另一個位置（left/top＝腳底那一格的左上角，同 opts）：往前＋往上一個弧線，約 0.45 秒
      jumpTo(at, left, top) {
        const from = Object.assign({}, pos);
        const peak = Math.min(from.top, top) - PX * 4;
        tl.fromTo(box, { left: from.left + "px" }, { left: left + "px", duration: 0.45, ease: "none", immediateRender: false }, at);
        tl.fromTo(box, { top: from.top + "px" }, { top: peak + "px", duration: 0.24, ease: "power2.out", immediateRender: false }, at);
        tl.fromTo(box, { top: peak + "px" }, { top: top + "px", duration: 0.21, ease: "power2.in", immediateRender: false }, at + 0.24);
        pos.left = left; pos.top = top; // 下一次 jumpTo 從這裡起跳
      },
      size: { w, h },
      // 所有動作排完再呼叫：把走路的每一格排上時間軸
      walk() {
        switches.sort((a, b) => a.at - b.at);
        const all = [].concat(...Object.values(sets));
        tl.set(all, { visibility: "hidden" }, 0);
        const marks = new Set();
        for (let t = t0; t < ctx.OUT; t += STEP) marks.add(Math.round(t * 1000) / 1000);
        switches.forEach((s) => marks.add(s.at));
        [...marks].sort((a, b) => a - b).forEach((t) => {
          const k = Math.floor((t - t0) / STEP + 1e-6) % n;
          const v = current(t);
          all.forEach((f) => tl.set(f, { visibility: "hidden" }, t));
          tl.set(sets[v][k], { visibility: "visible" }, t);
        });
      },
    };
    return api;
  }

  window.EXK_SPRITES = { draw, hero, names: Object.keys(S) };
})();
