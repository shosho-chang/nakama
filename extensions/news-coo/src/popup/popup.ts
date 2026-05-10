// Popup entry point. S1 skeleton — Hello World only.
// S5 will replace this with preview UI (title / author / word count / images / save button).
export {};

const statusEl = document.getElementById("status");
if (statusEl) {
  statusEl.textContent = "News Coo skeleton loaded. Run S2 to wire extraction.";
}
