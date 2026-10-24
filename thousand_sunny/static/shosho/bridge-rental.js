/* Bridge /bridge/rental — 標記後留在原位（修修 2026-10-23）。
 *
 * 標「有興趣／不考慮」是 form POST + 303 重新載入；被標的那一筆會移到別的區塊。
 * 沒有 JS 時，伺服器把網址錨點設成「補上它位置的那一筆」，至少不會跳到頁底。
 * 有 JS 時送出 js=1 → 伺服器不加錨點（瀏覽器的錨點捲動會在 load 之後才發生，蓋掉
 * 這裡的校正），改由這裡把那一筆對齊到剛剛被標記那一筆在螢幕上的高度，看起來就像
 * 清單往上補位、畫面沒動。
 */
(() => {
  const KEY = "rt-rental-keep-position";

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.classList.contains("rt-fb")) return;
    const row = form.closest(".rt-row");
    const after = form.querySelector('input[name="return_to"]');
    if (!row || !after || !after.value) return;
    if (!form.querySelector('input[name="js"]')) {
      const flag = document.createElement("input");
      flag.type = "hidden";
      flag.name = "js";
      flag.value = "1";
      form.appendChild(flag);
    }
    try {
      sessionStorage.setItem(
        KEY,
        JSON.stringify({ id: after.value, top: row.getBoundingClientRect().top }),
      );
    } catch (_) {
      /* private mode / storage blocked → 退回只靠網址錨點 */
    }
  });

  const restore = () => {
    let saved = null;
    try {
      saved = JSON.parse(sessionStorage.getItem(KEY) || "null");
      sessionStorage.removeItem(KEY);
    } catch (_) {
      return;
    }
    if (!saved || typeof saved.top !== "number") return;
    const target = document.getElementById(saved.id);
    if (!target) return;
    const delta = target.getBoundingClientRect().top - saved.top;
    window.scrollTo({ top: window.scrollY + delta, behavior: "instant" });
  };

  // load 之後（版面與字型都到位）再對齊。不用 requestAnimationFrame：分頁在背景時它
  // 會被暫停，重新載入時切去別的分頁就不會校正。
  window.addEventListener("load", () => setTimeout(restore, 0));
})();

/* 照片燈箱（修修 2026-10-24：不用點進原網頁就能看空間）。
 * 縮圖本身是原網頁連結；這裡攔下點擊改開 <dialog>。←／→ 換張、Esc 關閉（dialog 原生）。
 */
(() => {
  const dlg = document.getElementById("rt-gallery");
  if (!dlg || typeof dlg.showModal !== "function") return; // 不支援就照原本開原網頁
  const img = dlg.querySelector(".rt-gallery-img");
  const title = dlg.querySelector(".rt-gallery-title");
  const count = dlg.querySelector(".rt-gallery-count");
  const src = dlg.querySelector(".rt-gallery-src");
  const navs = dlg.querySelectorAll(".rt-gallery-nav");
  let photos = [];
  let i = 0;
  let opener = null;

  const show = (n) => {
    i = (n + photos.length) % photos.length;
    img.src = photos[i];
    img.alt = `${title.textContent}，第 ${i + 1} 張照片`;
    count.textContent = `${i + 1} / ${photos.length}`;
    // 預先載下一張，換張時不閃
    if (photos.length > 1) new Image().src = photos[(i + 1) % photos.length];
  };

  document.addEventListener("click", (event) => {
    const a = event.target instanceof Element && event.target.closest("a.rt-thumb");
    if (!a || event.ctrlKey || event.metaKey || event.shiftKey) return; // 修飾鍵 → 照常開新分頁
    try {
      photos = JSON.parse(a.dataset.gallery || "[]");
    } catch (_) {
      return;
    }
    if (!photos.length) return;
    event.preventDefault();
    opener = a;
    title.textContent = a.dataset.title || "";
    src.href = a.href;
    navs.forEach((b) => (b.hidden = photos.length < 2));
    show(0);
    dlg.showModal();
  });

  dlg.addEventListener("click", (event) => {
    const act = event.target instanceof Element && event.target.closest("[data-act]");
    if (act) {
      const what = act.dataset.act;
      if (what === "close") dlg.close();
      else show(i + (what === "next" ? 1 : -1));
    } else if (event.target === dlg) {
      dlg.close(); // 點背景關閉
    }
  });

  dlg.addEventListener("keydown", (event) => {
    if (photos.length < 2) return;
    if (event.key === "ArrowRight") show(i + 1);
    else if (event.key === "ArrowLeft") show(i - 1);
    else return;
    event.preventDefault();
  });

  // 手機：左右滑換張
  let touchX = null;
  img.addEventListener("touchstart", (e) => (touchX = e.touches[0].clientX), { passive: true });
  img.addEventListener("touchend", (e) => {
    if (touchX === null || photos.length < 2) return;
    const dx = e.changedTouches[0].clientX - touchX;
    touchX = null;
    if (Math.abs(dx) > 40) show(i + (dx < 0 ? 1 : -1));
  });

  dlg.addEventListener("close", () => {
    img.removeAttribute("src");
    if (opener) opener.focus();
  });
})();
