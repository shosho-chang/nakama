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
