import { loadHandle, saveHandle, verifyHandle } from "../vault/handle.js";
import { t } from "../i18n/locale.js";

const statusEl = document.getElementById("vault-status") as HTMLParagraphElement;
const pickBtn = document.getElementById("btn-pick") as HTMLButtonElement;
const repickBtn = document.getElementById("btn-repick") as HTMLButtonElement;

function showValid(name: string): void {
  statusEl.textContent = t("vaultStatus", name);
  pickBtn.hidden = true;
  repickBtn.hidden = false;
}

function showInvalid(): void {
  statusEl.textContent = t("noVaultStatus");
  pickBtn.hidden = false;
  repickBtn.hidden = true;
}

async function pickVault(): Promise<void> {
  try {
    const handle = await window.showDirectoryPicker({ mode: "readwrite" });
    await saveHandle(handle);
    showValid(handle.name);
  } catch {
    // User cancelled or permission denied — leave current state unchanged.
  }
}

async function init(): Promise<void> {
  const handle = await loadHandle();
  if (!handle) {
    showInvalid();
    return;
  }
  const ok = await verifyHandle(handle);
  if (ok) {
    showValid(handle.name);
  } else {
    showInvalid();
  }
}

pickBtn.addEventListener("click", () => {
  void pickVault();
});
repickBtn.addEventListener("click", () => {
  void pickVault();
});

void init();
