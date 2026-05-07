// /projects/{slug} review-mode client.
// Vanilla module, no framework. Mirrors the React behaviour in the design
// handoff (N458-brook-review-mode/project/brook/screen.jsx) but without
// hydration — the server already rendered the full state.

(function () {
  "use strict";

  const shell = document.getElementById("review-shell");
  if (!shell) return;

  const slug = shell.dataset.projectSlug;
  const themeKey = "proj_theme";

  // ── Theme ────────────────────────────────────────────────────────────────
  function applyTheme(theme) {
    shell.classList.toggle("brk-dark", theme === "dark");
    shell.classList.toggle("brk-light", theme !== "dark");
  }
  function initialTheme() {
    const stored = localStorage.getItem(themeKey);
    if (stored === "light" || stored === "dark") return stored;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }
  let theme = initialTheme();
  applyTheme(theme);

  const themeBtn = document.getElementById("review-theme-toggle");
  if (themeBtn) {
    themeBtn.addEventListener("click", () => {
      theme = theme === "dark" ? "light" : "dark";
      localStorage.setItem(themeKey, theme);
      applyTheme(theme);
    });
  }

  // ── Highlight excerpts ───────────────────────────────────────────────────
  // Mirrors BrkHighlightedExcerpt regex pattern from the design but with
  // project-customizable terms via data attribute on the shell.
  function highlightExcerpts() {
    const raw = shell.dataset.highlightTerms || "";
    if (!raw.trim()) return;
    const terms = raw
      .split("|")
      .map((t) => t.trim())
      .filter(Boolean)
      .map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    if (!terms.length) return;
    const re = new RegExp("(" + terms.join("|") + ")", "gi");
    document.querySelectorAll('[data-excerpt="true"]').forEach((node) => {
      const text = node.textContent;
      if (!text) return;
      const frag = document.createDocumentFragment();
      let last = 0;
      text.replace(re, (match, _g, offset) => {
        if (offset > last) frag.appendChild(document.createTextNode(text.slice(last, offset)));
        const m = document.createElement("mark");
        m.textContent = match;
        frag.appendChild(m);
        last = offset + match.length;
        return match;
      });
      if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
      node.textContent = "";
      node.appendChild(frag);
    });
  }
  highlightExcerpts();

  // ── Outline selection + evidence filter ──────────────────────────────────
  const outlineItems = Array.from(document.querySelectorAll(".review-outline-item"));
  const evidenceCards = Array.from(document.querySelectorAll(".review-evidence-card"));
  const evidenceCount = document.getElementById("evidence-count");
  const sectionNum = document.getElementById("evidence-section-num");
  const sectionHeading = document.getElementById("evidence-heading");

  function applySectionFilter(sectionId, evidenceRefs) {
    const refs = (evidenceRefs || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const refSet = new Set(refs);
    let visible = 0;
    evidenceCards.forEach((card) => {
      const slug = card.dataset.evidenceSlug;
      // Empty refs == show all (haven't constrained yet); non-empty filters.
      const show = refSet.size === 0 || refSet.has(slug);
      card.hidden = !show;
      if (show) visible++;
    });
    if (evidenceCount) evidenceCount.textContent = String(visible);
    if (sectionNum && sectionId !== undefined) sectionNum.textContent = sectionId;
  }

  function selectOutline(button) {
    if (!button || button.getAttribute("aria-disabled") === "true") return;
    outlineItems.forEach((b) => {
      b.classList.remove("is-active");
      b.setAttribute("aria-pressed", "false");
    });
    button.classList.add("is-active");
    button.setAttribute("aria-pressed", "true");
    const refs = button.dataset.evidenceRefs;
    const sid = button.dataset.sectionId;
    const heading = button.querySelector(".review-outline-item__heading");
    if (heading && sectionHeading) sectionHeading.textContent = heading.textContent;
    applySectionFilter(sid, refs);
  }

  outlineItems.forEach((btn, idx) => {
    btn.addEventListener("click", () => selectOutline(btn));
    btn.addEventListener("keydown", (ev) => {
      if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
        ev.preventDefault();
        const next = ev.key === "ArrowDown" ? idx + 1 : idx - 1;
        const target = outlineItems[next];
        if (target) {
          target.focus();
          selectOutline(target);
        }
      }
    });
  });

  // Initial filter run for the server-rendered active section.
  const activeBtn = outlineItems.find((b) => b.classList.contains("is-active"));
  if (activeBtn) selectOutline(activeBtn);

  // ── Reject confirm dialog ────────────────────────────────────────────────
  const dialog = document.getElementById("reject-dialog");
  const dialogKind = document.getElementById("reject-dialog-kind");
  const dialogTitle = document.getElementById("reject-dialog-title");
  const dialogMeta = document.getElementById("reject-dialog-meta");
  const dialogConfirm = document.getElementById("reject-confirm");
  const dialogCancel = document.getElementById("reject-cancel");
  let pending = null;

  function openRejectDialog(payload) {
    pending = payload;
    if (!dialog) return;
    const isGlobal = payload.kind === "global";
    if (dialogKind)
      dialogKind.textContent = isGlobal
        ? "整條不要 · global down-rank"
        : "從這段拿掉 · remove from this section";
    if (dialogTitle)
      dialogTitle.textContent = isGlobal
        ? "確定要把這條證據從整個專案下架嗎？"
        : "確定只把這條證據從這個段落拿掉嗎？";
    if (dialogMeta) dialogMeta.textContent = payload.evidenceSlug;
    if (typeof dialog.showModal === "function") {
      dialog.showModal();
    } else {
      dialog.setAttribute("open", "");
    }
    if (dialogConfirm) dialogConfirm.focus();
  }
  function closeRejectDialog() {
    pending = null;
    if (dialog && dialog.open) dialog.close();
  }

  document.querySelectorAll(".reject-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.disabled) return;
      const card = btn.closest(".review-evidence-card");
      const evidenceSlug = btn.dataset.evidenceSlug;
      const kind = btn.dataset.reject;
      const activeSectionBtn = document.querySelector(".review-outline-item.is-active");
      const sectionId = activeSectionBtn ? activeSectionBtn.dataset.sectionId : null;
      openRejectDialog({ evidenceSlug, kind, sectionId, card });
    });
  });

  if (dialogCancel) dialogCancel.addEventListener("click", closeRejectDialog);
  if (dialog) {
    dialog.addEventListener("close", () => {
      pending = null;
    });
    dialog.addEventListener("click", (ev) => {
      // Click on backdrop (target is the dialog itself, not a child) → cancel.
      if (ev.target === dialog) closeRejectDialog();
    });
  }

  if (dialogConfirm) {
    dialogConfirm.addEventListener("click", async (ev) => {
      ev.preventDefault();
      if (!pending) return closeRejectDialog();
      const action =
        pending.kind === "global" ? "reject_evidence_entirely" : "reject_from_section";
      const body = {
        op: "append_user_action",
        action: {
          timestamp: new Date().toISOString(),
          action,
          evidence_slug: pending.evidenceSlug,
          section: pending.sectionId ? Number(pending.sectionId) : null,
        },
      };
      // Optimistic: dim the card.
      if (pending.card) pending.card.classList.add("is-rejected");
      const cardRef = pending.card;
      closeRejectDialog();
      try {
        const res = await fetch(`/api/projects/${slug}/synthesize`, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error("reject failed: " + res.status);
      } catch (err) {
        // Roll back optimistic dim on failure.
        if (cardRef) cardRef.classList.remove("is-rejected");
        console.error(err);
        alert("拒絕證據失敗，請重試。");
      }
    });
  }

  // ── Save / finalize stubs (server side ops not in scope here) ────────────
  const saveBtn = document.getElementById("btn-save-draft");
  if (saveBtn) {
    saveBtn.addEventListener("click", () => {
      // Placeholder — outline_final POST hook can land later.
      saveBtn.textContent = "已暫存";
      setTimeout(() => (saveBtn.textContent = "暫存草稿"), 1200);
    });
  }
})();
