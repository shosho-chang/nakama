// 多格漫畫頁的骨架（版面、運鏡、字卡、圖層、人偶放置）——每支漫畫只寫「分鏡表＋每格的動作」
//   需要：gsap、figure.js；要動態特效再載 fx.js
//
//   const P = EXK_COMIC.page(root, {
//     cells: { p1: { x, y, w, h, img: "assets/…/c1.png", iw: 1536, ih: 1024, flipX: false }, … },
//     over: 1.08,          // 背景圖比格子大多少（裁掉生圖自己畫的邊框）
//     fx: true,            // true：角色寫在 P.act（劇情時間，一拍二＋頓格），鏡頭／字卡／特效寫在 P.tl
//     fps: 30000 / 1001,   // 一拍二的格數＝成品影片的影格率（新影片 29.97；舊的 23.976 影片寫 24000 / 1001）
//   });
//   P.cam("p1", at, dur) / P.camOut(at, dur)      運鏡：推到某格／拉回整頁
//   P.cap(k, html, {left|right, top|bottom}, at)   旁白框（不跟著格子震動）
//   P.bigText(k, html, left, top, size, at)        大字（跟著格子震動）
//   P.svg(k, z) / P.el(tag, attrs, parent)         格子裡的 SVG 圖層
//   P.imgPt(k, ix, iy)                              背景圖座標 → 格子座標（量桌緣、地板用）
//   P.figure(k, opts)                               在格子裡放一隻人偶 → { wrap, f }
//   音效：直接在頁面 HTML 寫 <audio src data-start data-duration data-track-index data-volume [data-media-start]>（放在 #root 裡）。
//         渲染器只讀靜態 HTML 決定要混哪些聲音，JS 動態加的 <audio> 不會被混進去（2026-11-20 實測 audioCount 0）。
//         秒數是真實時間（頓格之後的事件要加上停格秒數）。
//   P.finish(end, { stops })                        收尾：補時間長度；fx 模式把 act 接上 tl（一拍二＋頓格）
//
//   每格三層：C[k].outer＝外框＋旁白框；cell[k]（＝C[k].stage）＝格子內容，P.svg 圖層、人偶、大字都掛這層；
//   C[k].scene＝只有背景圖。要「整格轉／整格推近／震動」動 stage；要「背景放大到不露邊」動 scene
//   （荷蘭角：stage 轉 -20°、scene 放大 1.6 倍，人和道具不會被放大——2026-11-21 高收入段格 9）。
//   同一拍又推近又震（都動 x／y 會打架）：把要震的內容搬進 scene（C[k].scene.append(svg)）再震 scene。
//   沒有背景圖的格子 scene 是空的，直接震 scene 看不到（2026-11-22 高收入段格 5）。
//   頓格（stops）會讓之後所有 act 事件在真實時間晚「停格秒數」：照字幕排的 act 事件用 A(t)=t>頓格點?t-停格:t 預先扣掉。
//
//   字的大小都用「推進後在螢幕上的大小」來定（÷ 那格的放大倍率），所以大格、小格推近後字一樣大。
(function () {
  const NS = "http://www.w3.org/2000/svg";

  function page(root, opt) {
    const cells = opt.cells, over = opt.over || 1.08;
    const pageEl = document.createElement("div"); pageEl.className = "comic-page";
    Object.assign(pageEl.style, { position: "absolute", left: 0, top: 0, width: "1920px", height: "1080px", transformOrigin: "0 0" });
    root.appendChild(pageEl);
    const tl = gsap.timeline({ paused: true });
    window.__timelines = { [root.dataset.compositionId]: tl };
    const act = opt.fx ? gsap.timeline({ paused: true }) : tl;

    const cell = {};
    for (const k in cells) {
      const c = cells[k], d = document.createElement("div"); d.className = "cell";
      Object.assign(d.style, { left: c.x + "px", top: c.y + "px", width: c.w + "px", height: c.h + "px" });
      // stage＝格子內容（震動時晃這層）；d＝外框＋旁白框（不跟著晃）
      const stage = document.createElement("div"); stage.className = "scene"; d.appendChild(stage);
      const sc = document.createElement("div"); sc.className = "scene"; stage.appendChild(sc);
      c.outer = d; c.stage = stage; c.scene = sc; c.s = 1;
      if (c.img) {
        const img = document.createElement("img"); img.className = "bg"; img.src = c.img;
        const s = Math.max(c.w * over / c.iw, c.h * over / c.ih);
        Object.assign(img.style, { position: "absolute", width: c.iw * s + "px", height: c.ih * s + "px",
          left: (c.w - c.iw * s) / 2 + "px", top: (c.h - c.ih * s) / 2 + "px" });
        if (c.flipX) img.style.transform = "scaleX(-1)";
        c.s = s; sc.appendChild(img);
      }
      c.zoom = Math.min(1920 / c.w, 1080 / c.h) * 0.94;
      pageEl.appendChild(d); cell[k] = stage;
    }
    const imgPt = (k, ix, iy) => { const c = cells[k]; return [(c.w - c.iw * c.s) / 2 + ix * c.s, (c.h - c.ih * c.s) / 2 + iy * c.s]; };

    const api = {
      tl, act, cells, cell, page: pageEl, imgPt, NS,
      cam(k, at, dur) {
        const c = cells[k], s = c.zoom;
        tl.to(pageEl, { scale: s, x: 960 - (c.x + c.w / 2) * s, y: 540 - (c.y + c.h / 2) * s, duration: dur, ease: "power3.inOut" }, at);
      },
      camOut(at, dur) { tl.to(pageEl, { scale: 1, x: 0, y: 0, duration: dur || 1.3, ease: "power3.inOut" }, at); },
      drift(k, at, dur, amt) {
        tl.fromTo(cells[k].scene, { scale: 1, transformOrigin: "50% 50%" }, { scale: 1 + (amt || 0.06), duration: dur, ease: "none" }, at);
      },
      cap(k, html, pos, at) {
        const C = cells[k], z = C.zoom, c = document.createElement("div"); c.className = "cap"; c.innerHTML = html;
        Object.assign(c.style, { fontSize: 40 / z + "px", borderWidth: 7 / z + "px" });
        C.outer.appendChild(c);
        const ww = c.offsetWidth, hh = c.offsetHeight;
        c.style.left = (pos.right != null ? C.w - ww - pos.right : pos.left) + "px";
        c.style.top = (pos.bottom != null ? C.h - hh - pos.bottom : pos.top) + "px";
        tl.fromTo(c, { opacity: 0, x: -16 / z }, { opacity: 1, x: 0, duration: 0.25, ease: "power3.out" }, at);
        return c;
      },
      bigText(k, html, left, top, size, at) {
        const z = cells[k].zoom, b = document.createElement("div"); b.className = "big"; b.innerHTML = html;
        Object.assign(b.style, { left: left + "px", top: top + "px", fontSize: size / z + "px", webkitTextStroke: 16 / z + "px #fff" });
        cell[k].appendChild(b);
        tl.fromTo(b, { opacity: 0, scale: 0.7, transformOrigin: "0 50%" }, { opacity: 1, scale: 1, duration: 0.3, ease: "back.out(1.7)" }, at);
        return b;
      },
      svg(k, z) {
        const C = cells[k], s = document.createElementNS(NS, "svg");
        s.setAttribute("viewBox", `0 0 ${C.w} ${C.h}`); s.setAttribute("class", "ink"); s.style.zIndex = z || 10;
        cell[k].appendChild(s); return s;
      },
      el(tag, attrs, parent) {
        const e = document.createElementNS(NS, tag);
        for (const a in attrs) e.setAttribute(a, attrs[a]);
        if (parent) parent.appendChild(e); return e;
      },
      figure(k, o, parent) {
        const wrap = document.createElement("div"); wrap.className = "fig";
        (parent || cell[k]).appendChild(wrap);
        return { wrap, f: EXK_FIGURE.make(wrap, o) };
      },
      finish(end, o) {
        o = o || {};
        act.to({}, { duration: 0.01 }, end);
        tl.to({}, { duration: 0.01 }, end);
        if (opt.fx) window.EXK_FX.drive(tl, act, { start: 0, end, stops: o.stops || [], twos: o.twos !== false, fps: o.fps || opt.fps });
        const q = new URLSearchParams(location.search);
        if (q.get("t")) tl.seek(+q.get("t")); else if (q.get("play") === "1") tl.play(0); else tl.seek(0);
      },
    };
    return api;
  }

  // 頁面需要的樣式（每支漫畫頁共用）
  const CSS = `
    .comic-page .cell { position: absolute; overflow: hidden; background: var(--white); outline: 7px solid var(--ink); outline-offset: -3px; }
    .comic-page .scene { position: absolute; inset: 0; }
    .comic-page .ink { position: absolute; inset: 0; pointer-events: none; overflow: visible; }
    .comic-page .fig { position: absolute; left: 0; top: 0; width: 0; height: 0; z-index: 3; }
    .comic-page .cap { position: absolute; z-index: 5002; background: var(--white); border-style: solid; border-color: var(--ink);
      padding: .45em .7em; line-height: 1.4; white-space: nowrap; }
    .comic-page .big { position: absolute; z-index: 5002; line-height: 1.1; color: var(--orange); white-space: nowrap; paint-order: stroke fill; font-weight: 900; }`;
  const st = document.createElement("style"); st.textContent = CSS; document.head.appendChild(st);

  window.EXK_COMIC = { page };
})();
