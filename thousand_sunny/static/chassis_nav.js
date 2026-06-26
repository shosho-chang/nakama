// Chassis header dropdown toggles (Fleet / Ops lanes).
//
// Externalised from templates/bridge/_chassis_nav.html so the shared chassis
// header carries NO inline <script>. The Reader + KB surfaces follow a
// `script-src 'self'`, no-inline convention (middleware/csp.py guards the
// /books prefixes; routers/kb_review.py extends the same rule to /kb*), so the
// chassis can only be reused there if its behaviour lives in an external file.
// Loaded with `defer` so the chassis DOM is parsed before this runs.
(function () {
  function closeAll(dropdowns) {
    dropdowns.forEach(function (dd) {
      var t = dd.querySelector(".chassis-nav-trigger");
      var m = dd.querySelector(".chassis-dropdown-menu");
      if (t) t.setAttribute("aria-expanded", "false");
      if (m) m.classList.remove("open");
    });
  }

  var dropdowns = document.querySelectorAll(".chassis-dropdown");
  dropdowns.forEach(function (dd) {
    var trigger = dd.querySelector(".chassis-nav-trigger");
    var menu = dd.querySelector(".chassis-dropdown-menu");
    if (!trigger || !menu) return;
    trigger.addEventListener("click", function (e) {
      e.stopPropagation();
      var open = trigger.getAttribute("aria-expanded") === "true";
      closeAll(dropdowns); // close others first
      if (!open) {
        trigger.setAttribute("aria-expanded", "true");
        menu.classList.add("open");
      }
    });
  });

  // Outside click + Escape close every open dropdown.
  document.addEventListener("click", function () {
    closeAll(dropdowns);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeAll(dropdowns);
  });
})();
