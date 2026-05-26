// Foundry storyboard polling — Tier 2 UI (ADR-032 §7).
// Every 2.5s, fetch /foundry/<eid>/status and patch chip cells in-place.
// No SSE; no inline <video>; mp4 link replaces a placeholder when render done.
(function () {
  "use strict";
  const shell = document.querySelector("[data-episode-id]");
  if (!shell) return;
  const episodeId = shell.dataset.episodeId;
  const POLL_MS = 2500;

  const RENDER_STATE_CLASSES = {
    pending: "sho-chip--offline",
    rendering: "sho-chip--hold",
    done: "sho-chip--online",
    failed: "sho-chip--failed",
  };

  function setChip(row, kind, state, label) {
    const chip = row.querySelector(`[data-chip="${kind}"]`);
    if (!chip) return;
    if (kind === "render") {
      Object.values(RENDER_STATE_CLASSES).forEach((cls) => chip.classList.remove(cls));
      chip.classList.add(RENDER_STATE_CLASSES[state] || "sho-chip--offline");
      chip.textContent = label;
    } else {
      // bool chips: text / visual
      chip.classList.toggle("sho-chip--active", kind === "text" && state);
      chip.classList.toggle("sho-chip--online", kind === "visual" && state);
      chip.classList.toggle("sho-chip--offline", !state);
      chip.textContent = state ? "✓" : "·";
    }
  }

  function updateMp4Cell(row, mp4Uri) {
    const cell = row.querySelector(".col-mp4");
    if (!cell) return;
    if (mp4Uri) {
      cell.innerHTML = `<a class="sho-lk" href="${mp4Uri}">mp4</a>`;
    } else {
      cell.innerHTML = '<span class="sho-muted">—</span>';
    }
  }

  async function poll() {
    let res;
    try {
      res = await fetch(`/foundry/${episodeId}/status`, { credentials: "same-origin" });
    } catch (e) {
      return;
    }
    if (!res.ok) return;
    let data;
    try {
      data = await res.json();
    } catch (e) {
      return;
    }
    (data.beats || []).forEach((beat) => {
      const row = shell.querySelector(`tr[data-beat-id="${beat.beat_id}"]`);
      if (!row) return;
      setChip(row, "text", beat.text_approved, beat.text_approved ? "✓" : "·");
      setChip(row, "render", beat.render_status, beat.render_status);
      setChip(row, "visual", beat.visual_approved, beat.visual_approved ? "✓" : "·");
      updateMp4Cell(row, beat.mp4_uri);
    });
  }

  setInterval(poll, POLL_MS);
})();
