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

  // ── Mobile menu (≤900px; CSS 決定何時顯示) ────────────────────────────
  // .has-menu 由這裡掛上——JS 沒跑到時 CSS 不收合，nav 維持換行版、不會消失。
  var chassis = document.querySelector(".chassis");
  var menuToggle = chassis && chassis.querySelector(".chassis-menu-toggle");
  function setMenu(open) {
    if (!menuToggle) return;
    chassis.classList.toggle("is-menu-open", open);
    menuToggle.setAttribute("aria-expanded", open ? "true" : "false");
    menuToggle.setAttribute("aria-label", open ? "關閉導覽選單" : "開啟導覽選單");
    if (!open) closeAll(dropdowns);
  }
  if (menuToggle) {
    chassis.classList.add("has-menu");
    // 鈕上寫目前所在頁（取 active 項目的英文那段），收合後才知道自己在哪。
    var current = chassis.querySelector(".chassis-nav a.active");
    var label = menuToggle.querySelector("[data-menu-current]");
    if (current && label) {
      var first = current.firstChild;
      var text = first && first.nodeType === 3 ? first.textContent.trim() : "";
      if (text) label.textContent = text;
    }
    menuToggle.addEventListener("click", function (e) {
      e.stopPropagation();
      setMenu(!chassis.classList.contains("is-menu-open"));
    });
  }

  // Outside click + Escape close every open dropdown (and the mobile menu).
  document.addEventListener("click", function (e) {
    closeAll(dropdowns);
    if (chassis && !chassis.contains(e.target)) setMenu(false);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    closeAll(dropdowns);
    if (chassis && chassis.classList.contains("is-menu-open")) {
      setMenu(false);
      menuToggle.focus();
    }
  });

  // ── Build stamp ──────────────────────────────────────────────────────────
  // 2026-09-14：#1257 與 #1261 都合併進 main 了，修修看到的卻還是舊行為——port
  // 8000 上那個 process 是三天前啟動的，而 main 前進時沒有東西會重啟它。畫面
  // 跟新版一模一樣，只有行為不對，所以看不出來。這顆晶片就是把「我現在看到的
  // 是哪一版」變成看一眼就知道。
  //
  // 取值走 fetch 而不是 template context：chassis 是 50 個 template 共用的
  // macro，而 Jinja env 有 25 個；塞 context 要改兩邊，fetch 一處就到齊。
  function describeUptime(seconds) {
    if (seconds >= 86400) return Math.floor(seconds / 86400) + " 天";
    if (seconds >= 3600) return Math.floor(seconds / 3600) + " 小時";
    return Math.max(1, Math.floor(seconds / 60)) + " 分";
  }

  var slot = document.querySelector("[data-build-slot]");
  if (!slot || !window.fetch) return;
  fetch("/bridge/build", { credentials: "same-origin" })
    .then(function (response) {
      if (!response.ok) throw new Error("build endpoint " + response.status);
      return response.json();
    })
    .then(function (build) {
      if (!build.known) {
        // 拿不到 commit（非 git 部署、git 不在 PATH）就別假裝知道版本。
        slot.textContent = "版本不明";
        slot.title = "這次部署讀不到 git commit；無法判斷程式碼是否為最新。";
        return;
      }
      var uptime = describeUptime(build.uptime_seconds);
      slot.textContent = build.commit_short + " · 已跑 " + uptime;
      slot.title =
        "執行中：" + build.commit + "（" + (build.branch || "?") + "）\n" +
        "commit 時間：" + (build.committed_at || "?") + "\n" +
        "啟動時間：" + build.started_at;
      if (build.stale) {
        slot.classList.add("is-stale");
        slot.textContent = "⚠ 需重啟 · 已跑 " + uptime;
        slot.title =
          "磁碟上的程式碼已經更新，但這個服務還跑在舊版上。\n" +
          "執行中：" + build.commit_short + "\n" +
          "磁碟上：" + build.current_commit_short + "\n" +
          "重啟 Nakama-ThousandSunny 才會套用。";
      }
    })
    .catch(function () {
      // 這是輔助診斷資訊，失敗不該在 header 留一個壞掉的東西。
      slot.textContent = "";
    });
})();
